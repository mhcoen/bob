# Real council with parallel state execution

## Background

Orchestra's existing `propose_critique_synthesize` workflow is a linear
four-stage pipeline (proposer -> critic -> synthesizer -> responder).
It produces good output but it is not a council. The user wants a
Karpathy-style council: N independent advisors running in parallel,
each from a distinct thinking lens, then N peer reviewers running in
parallel against anonymized advisor outputs, then a chairman
synthesizing.

Reference implementation:
`/Users/mhcoen/.claude/skills/llm-council/SKILL.md`. The skill does
this through Claude Code sub-agents. Orchestra needs to do it through
its own executor.

Orchestra slice 1 walks one state at a time. There is no fan-out
primitive. The user has confirmed parallel execution was always
required and considers its absence a design mistake to fix now.

Keep `propose_critique_synthesize` as-is. It is a useful linear
pipeline, just not a council. Do not delete or rename in code; the
user will rename the verb mapping in their config.

## High-level design

### 1. Parallel state execution primitive

Add a fan_out transition shape to the grammar. Failure routing is
part of the surface form so it does not have to be retrofitted later:

```
state frame
  ...
  on complete fan_out [contrarian_advise, first_principles_advise, expansionist_advise, outsider_advise, executor_lens_advise] join anonymize on error abandon
```

The fan_out spawns the listed states concurrently. Each runs through
the per-state sequence individually, but **a fan-out child does NOT
follow its own outgoing transitions.** Each child executes exactly
its named state and stops at durable `state_exit`. Only the fan-out
controller decides whether the group as a whole proceeds to `join`
or to the `on error` target. The reentrant per-state helper takes a
"fan-out child" mode flag that suppresses outgoing transition
selection after `state_exit` is durable. **Child-local retry policy
declared on the state itself (e.g., `on error retry max N then ...`)
still applies inside the helper.** A transient adapter error in a
fan-out child triggers the local retry path normally; the child
state produces an error `state_exit` only after its retry budget is
exhausted. Only outgoing-transition selection is suppressed in
fan-out child mode, not retry handling.

The executor waits for each child to reach a durable `state_exit`
log record before evaluating the group outcome. Artifact commit
alone is NOT the completion criterion: a child can commit artifact
writes and crash before its `state_exit` is durable, and replay
must not treat that child as complete.

**Per-child commit protocol.** The per-state sequence orders artifact
commit BEFORE writing `state_exit`. After commit succeeds, the
helper writes `state_exit` through the LogWriter (which fsyncs under
lock). `state_exit` durability is the completion point.

**Invocation identity.** Every per-state execution attempt has a
unique `invocation_id` minted at `state_enter` time. The
invocation_id is the (run_id, state_name, attempt_seq) tuple, where
attempt_seq is a monotonic counter incremented on every entry and
re-entry of the state within the run. The invocation_id is recorded
in `state_enter`, in every artifact commit performed by that
invocation, and in `state_exit`. Every artifact version row in the
store carries the producing invocation_id, not just a state name.
This is the visibility key.

Producer kinds for visibility: each artifact version row carries a
`producer_kind` field with one of the values:

- `state_invocation`: produced by a state's per-state execution.
  Visibility depends on that invocation's `state_exit` outcome.
- `external`: external_input or initial-input artifact supplied at
  workflow entry. Always visible. No producing invocation.
- `initial`: workflow-level initial artifact declared in the spec.
  Always visible. No producing invocation.

External and initial versions are visible roots; the visibility
rule never hides them. A workflow without external or initial
inputs has only state_invocation versions, all of which are
governed by their producing invocation's outcome.

**Artifact visibility rule.** A committed artifact version is
visible to downstream consumers (other states, transforms, replay,
snapshot construction) if and only if either:

- `producer_kind` is `external` or `initial`, OR
- `producer_kind` is `state_invocation` AND the producing invocation
  (identified by invocation_id) has a durable success `state_exit`.

The artifact store enforces this at read time: a `read_latest(name)`
call returns the most recent version that satisfies the rule, not
simply the most recent committed write. Versions whose producing
invocation has no `state_exit` (incomplete) or has an error
`state_exit` are hidden.

**Visibility status source.** The store does not derive durability
or outcome on its own. The executor and replay layer maintain a
`VisibilityIndex`: a mapping `invocation_id -> Literal["pending",
"success", "error"]`, kept in memory and persisted into the run
directory's recovery state. The index is updated when:

- a `state_enter` record is committed (insert as `pending`),
- a `state_exit` record is committed (update to `success` or
  `error`),
- replay rewalks the log and reconstructs the index from
  `state_enter` and `state_exit` pairs.

The store reads, including `read_latest` and the snapshot
constructor, consult the VisibilityIndex via a thread-safe
interface that the store is given at construction time. The store
itself does not parse the log; it asks the index. The index is
the single source of truth for visibility status across both the
store and the executor.

**Schema migration.** Existing artifact-store rows from runs
created before this change have no `invocation_id` or
`producer_kind`. The migration policy: existing rows are tagged
`producer_kind = legacy` with no invocation_id, and `legacy` rows
are always visible (treated like `external`). This is conservative:
old runs continue to work, and new runs use the strict invocation
keying. Replay of old runs is not affected. Slice A includes a
forward-only schema migration that adds the two columns and tags
existing rows. No down-migration is supported.

**Post-fan-out artifact cleanup.** After `fan_out_end` is durable,
the controller initiates a cleanup pass that purges committed
artifact versions whose producing invocation lacks a durable
`state_exit` OR has an error `state_exit`. This applies to the
success path (no leftover incomplete children to clean) and to
the error path (where some children may have committed without
ever reaching `state_exit` due to crash, and where post-error
drained successes still write `state_exit` and their artifacts
remain visible per the visibility rule). The cleanup is
replay-safe: re-running cleanup against an already-clean store is
a no-op.

If cleanup is interrupted mid-purge by a crash, the visibility
rule continues to hide the orphaned versions correctly because the
producing invocations' visibility status is still `pending` or
`error`. The next replay re-runs cleanup after seeing durable
`fan_out_end`. Orphaned dead-weight rows in the store are
acceptable if they persist across a cleanup interruption; they
are invisible to readers and will be purged by the next
successful cleanup pass.

If a child crashes after artifact commit but before `state_exit`,
replay sees committed artifacts but no `state_exit` and treats the
child as incomplete. Per the visibility rule, those committed
artifacts are not visible to readers (the invocation's status is
`pending` in the index). On re-entry, the child re-runs with a
new invocation_id (new attempt_seq) and writes new versions; the
new versions are gated by the new invocation's `state_exit`, and
the prior versions remain hidden until they are purged by
cleanup.

The existing `_discard_stale_tentatives` path is retained for the
case where the child crashed before artifact commit; commit is a
single atomic step within the per-state sequence. Replay rule:
any child without a durable `state_exit` is re-entered with a
fresh invocation_id, regardless of whether its artifact commits
exist on disk, AND those artifacts are not visible to readers per
the visibility rule until re-entry produces a durable success
`state_exit` for the new invocation.

**Fan-out group lifecycle and its own log records.** The fan-out
group is its own logical entity, separate from the parent state
that triggered it. The parent state has already produced its
`state_exit` before the fan-out is entered. The group has its own
records:

- `fan_out_start`: written before submitting child futures. Names
  the parent state, the ordered child state list, the join target,
  and the error target. This is the durable "this fan-out group
  exists" anchor for replay.
- `fan_out_end`: the durable record of the fan-out controller's
  routing decision. Written **only after every running future has
  drained to its own `state_exit`** (success or error). Never
  written speculatively. Never updated after being written.
  Carries the group's aggregate outcome (`success` or `error`),
  the per-child outcome map (every child invocation_id that
  produced a durable `state_exit` before `fan_out_end` was
  written, including drained successes that landed after the
  first error), and the next state the controller is routing to.

`fan_out_end` is the commit point for the routing decision, not a
redundant summary. Without it, replay would have to re-derive the
decision from `fan_out_start` plus child `state_exit` records on
every restart; with it, the decision is durable and the controller
can transition without re-deriving. For failed groups with drained
siblings, `fan_out_end` is the only place the drained outcomes are
canonically attributed to the group.

Routing decisions read from `fan_out_start` plus child `state_exit`
records (and `fan_out_end` if present), NOT from the parent state's
envelope. The parent's envelope is immutable once its `state_exit`
is durable.

**Per-child cancellation handles.** The fan-out controller submits
one future per child. The cancellation-handle registry is a single
data structure shared between the controller thread and worker
threads, protected by a `threading.Lock` that guards every
register, lookup, unregister, and cancel-flag mutation. The
registry maps child state name to a `ChildHandleEntry` with three
fields: `cancel_requested: bool`, `invocation_handle: Optional[...]`,
`state: Literal["pending", "registered", "done"]`.

- On future submission: controller inserts an entry with
  `state="pending"`, `cancel_requested=False`,
  `invocation_handle=None`.
- When the worker enters its adapter call: worker sets
  `state="registered"` and `invocation_handle=<handle>` (a
  reference to the prepared adapter request, the running
  subprocess, or a session-scoped cancel token, depending on the
  adapter). Sibling invocations have their own independent
  handles.
- When the worker finishes its adapter call: worker sets
  `state="done"` and clears `invocation_handle`.
- All registry operations are performed under the lock. The
  controller never reads `invocation_handle` while the worker is
  mid-mutation.

**Cancellation request handling (race-safe).** When the controller
decides to cancel child X:

- If X's future has not started (`future.running() == False` and
  `future.done() == False`): call `future.cancel()`. Set
  `cancel_requested=True` defensively. No `state_enter` will be
  written.
- If X's future is running and registry shows `state="pending"`
  (helper has started but is pre-adapter, no handle yet): set
  `cancel_requested=True`. The helper checks `cancel_requested`
  immediately before invoking the adapter and after registering
  its handle; if set, the helper writes an error `state_exit`
  with reason "cancelled" and returns without invoking.
- If X's future is running and registry shows `state="registered"`
  (handle exists): call `adapter.cancel(invocation_handle)`. The
  worker drains: the adapter call returns (with an error,
  truncated success, or natural completion depending on adapter
  cancellation semantics), and the worker writes `state_exit`
  reflecting the actual outcome.
- If X's future is `state="done"`: cancel is a no-op.

The text-role and edit-agent adapters' existing per-call
SessionState lives per-invocation, not shared across siblings;
the cancellation mechanism respects this isolation.

**Failure handling.**

- *Pending futures* (submitted but not started): cancelled with
  `future.cancel()` per the cancellation request handling above.
  No `state_enter` is written.
- *Running futures* (per-state helper has begun, in any registry
  state): the controller follows the cancellation request handling
  above, then drains the future. "Drain" means the running child
  completes its current invocation (interrupted by `adapter.cancel`
  if supported, or finished naturally) and writes its `state_exit`
  with whatever outcome it produced.
- The group's aggregate outcome is set the moment any child
  produces a durable error `state_exit`. Subsequent successful
  `state_exit` records from sibling children do not change the
  routing decision. Drained successes that land before
  `fan_out_end` is written are recorded in `fan_out_end`'s
  per-child outcome map, attributed to the group, but the group
  still routes to the error target.
- The controller waits for all running children to drain to a
  durable `state_exit` before writing `fan_out_end`. This is
  required for `fan_out_end`'s per-child outcome map to be complete
  and authoritative.

**Cancellation race rule.** A fan-out group with ANY durable child
error routes to the fan-out error target, regardless of whether
other children succeeded. This is the rule for both first-pass
execution and replay.

**Sibling visibility rule.** Workers see only state that existed
BEFORE the fan-out was entered. A child cannot read a sibling's
envelope or artifacts during the same fan-out group, even if the
sibling completes first. Each child's prompt resolution and
reference guards operate against an immutable snapshot of
pre-fan-out traversal state plus the child's own local invocation
outputs. The snapshot is the worker's only artifact-read source
during fan-out execution; workers MUST NOT call `read_latest`
against the live store from within a fan-out child.

**Lock ordering for snapshot capture.** Snapshot construction
reads artifacts from the store and therefore needs the store
lock; the `fan_out_start` record append needs the LogWriter lock.
The fixed lock-acquisition order, used everywhere both locks are
held together, is: **acquire LogWriter lock first, then acquire
store lock.** Snapshot capture proceeds as:

1. Acquire LogWriter lock.
2. Acquire store lock.
3. Construct snapshot by reading visible artifact versions from
   the store (visibility rule applied).
4. Append `fan_out_start` record and fsync.
5. Release store lock.
6. Release LogWriter lock.

This single critical section pairs snapshot capture and
`fan_out_start` durability so no state can be observed in two
different snapshots from one fan-out entry. Worker threads that
need both locks for any operation MUST follow the same order
(LogWriter then store) to prevent deadlock. Worker threads that
need only one of the two locks may acquire that one alone.

**Replay rules for fan-out groups.**

1. No `fan_out_start` in the log: parent state is re-entered.
2. `fan_out_start` present, no child `state_exit` records: launch
   all children fresh with new invocation_ids.
3. `fan_out_start` present, some children have `state_exit`, some
   do not, all completed children succeeded: re-enter the children
   without `state_exit` with new invocation_ids. Tentative writes
   from incomplete children are discarded by the existing
   `_discard_stale_tentatives` path. Committed artifacts from
   prior runs of incomplete children are hidden from reads by the
   visibility rule (their producing invocations are still
   `pending` in the VisibilityIndex) and remain hidden until
   purged by cleanup; they are NOT overwritten because the
   re-entry uses a new invocation_id.
4. `fan_out_start` present, all children have `state_exit`, no
   `fan_out_end`: evaluate the aggregate outcome (any error -> error
   target, all success -> join), run the post-fan-out cleanup
   pass, write `fan_out_end`, transition.
5. `fan_out_start` present, ANY child `state_exit` has error
   outcome: route to the fan-out error target. Do NOT re-run
   successful siblings looking for a join. Do NOT re-run the
   errored child. Run the cleanup pass to purge committed artifacts
   from invocations that are `pending` or `error` in the index.
   Write `fan_out_end` if missing, naming the durable error
   outcome and including all completed children in the per-child
   outcome map. The cancellation race rule applies on replay
   identically.
6. `fan_out_end` present: routing already decided, transition
   accordingly.

**Re-entry retry budget.** A re-entered child gets a FRESH retry
budget. Retry-attempt log records from the prior partial run are
not authoritative because no durable success `state_exit` was
produced. The retry counter passed to a re-entered child's helper
starts at zero, regardless of how many attempts the partial run
logged. This rule prevents pathological cases where a child crashed
mid-retry and replay would otherwise inherit a depleted budget.
Note: the workflow's `retry max N` clause is therefore a
per-entry budget, not a per-run budget. Multiple re-entries of
the same child across crashes can produce up to N retries each.

**Re-entered children and downstream consumers.** When replay
re-enters incomplete children (case 3), those children re-run
their adapter calls and may produce different output text than they
would have on the prior incomplete run. Downstream states (the
anonymize transform, the reviewer fan-out, the chairman) consume
the LATEST visible artifacts from the artifact store as filtered
by the visibility rule. Determinism guarantees apply to the
anonymize transform's seed (which is keyed on `(run_id,
state_name, sorted_input_keys)` and so produces the same
A->advisor-key mapping across replays), but NOT to the underlying
text content of any advisor's output. Reviewers see the same
anon-key structure across replays of the same run, but the text
behind a given key may differ if the underlying child was
re-entered. This is acceptable because the run as a whole had not
completed before the crash, so there is no first-pass behavior to
preserve.

Crash-atomicity case analysis (the executor must handle each
correctly):

1. Crash before `fan_out_start`: replay re-enters the parent state.
2. Crash after `fan_out_start` but before any child `state_enter`:
   replay launches all children fresh.
3. Crash with N of M children completed (durable `state_exit`),
   M-N still tentative or never started, all completed children
   succeeded: replay leaves the completed N alone, re-enters the
   M-N with new invocation_ids. Tentative writes from incomplete
   children are discarded. Committed artifacts from prior partial
   runs of incomplete children are hidden by the visibility rule
   and purged by cleanup at `fan_out_end`.
4. Crash with all M completed, all success, but `fan_out_end` not
   yet written: replay runs the cleanup pass, writes `fan_out_end`,
   and transitions to join.
5. Crash with at least one child `state_exit` carrying error,
   regardless of other children's outcomes: replay routes to the
   error target (per the cancellation race rule), runs the cleanup
   pass, writes `fan_out_end` carrying the group error outcome
   (with the full per-child outcome map including drained
   successes), and discards tentative writes from any unfinished
   child.
6. Reader-thread crash on one child while siblings finish cleanly:
   the affected child has tentative writes but no `state_exit`; it
   is re-entered on replay (case 3 if all other children succeeded;
   case 5 if any other child errored).
7. Two siblings declared as writing the same artifact: this is a
   workflow-level error caught by validation. Slice A's validator
   must reject sibling writes to the same artifact name within a
   fan-out group.

**Reentrant per-state execution requirements.**

The current Executor walks one state at a time using instance
fields (`current_state`, attempt counters, envelopes, logger
handle, store handle). Slice A must factor the per-state sequence
into a reentrant helper that runs for one state name plus an
invocation_id plus shared services (registry, store, log,
VisibilityIndex). The Executor instance keeps the workflow-level
traversal state; child workers receive only the per-state context
they need.

Workers receive an immutable read-only snapshot, captured atomically
with the `fan_out_start` write per the lock-ordering rule, of:

- prior completed envelopes from states that ran BEFORE the fan-out
  (for prompt resolution and reference guards).
- artifact contents (filtered by the visibility rule) from those
  prior states. Workers consume the snapshot exclusively for
  artifact reads during fan-out execution; live `read_latest` calls
  against the store from within a fan-out child are forbidden.
- workflow declarations (immutable by design).

A re-entered child's snapshot is reconstructed at the moment of
re-entry from the same pre-fan-out boundary; the snapshot does not
inherit sibling writes that landed during the partial run, because
those writes are filtered by the visibility rule (incomplete or
errored producers' artifacts are hidden).

The snapshot is immutable per the sibling visibility rule above:
sibling envelopes and sibling-written artifacts do not appear in
any worker's view, even after a sibling commits. Only the fan-out
controller mutates workflow traversal state. Workers do NOT modify
the Executor's instance fields directly.

The retry counter delivered to a worker is set to zero on first
entry and on every re-entry. The fan-out controller does not
inherit retry attempts across re-entries.

**Single-writer log discipline.** All log records, regardless of
which thread produced them, must funnel through one `LogWriter`
with a lock guarding the append-and-fsync pair. Workers do not
open their own log file. Records appear in append order; the
order between siblings is meaningful for replay only insofar as
each child's records are individually atomic and parseable. The
LogWriter lock also serves as the outer lock in the fixed
lock-ordering rule (LogWriter first, then store) for any operation
that pairs a record write with a snapshot read or a store update.

**Artifact store concurrency.** The SQLite-backed `ArtifactStore`
is not implicitly thread-safe. Slice A uses a single SQLite
connection opened with `check_same_thread=False`, protected by a
single store-level Python lock that guards every read, write,
tentative stage, and commit. The store-level lock is the inner
lock in the fixed lock-ordering rule (LogWriter first, then
store). The lock is held only during artifact operations, NOT
during adapter `invoke`. Concretely: a worker acquires the store
lock to read prior artifacts (or, during fan-out, consults its
snapshot which was captured under both locks), releases it, runs
the adapter call, then re-acquires the store lock to stage
tentatives and commit. The shared connection plus single Python
lock is sufficient because all SQLite operations are serialized
at the Python level; SQLite's own locking is irrelevant under
this discipline. Per-thread connections are explicitly NOT used
in Slice A; that is a future optimization if lock contention
becomes a problem. Sibling write collisions are rejected at
validation time before launch, so the lock never has to mediate
conflicting writes.

The visibility rule is implemented in the store's `read_latest`
and in the snapshot constructor. Both consult the VisibilityIndex
through a thread-safe interface. Both also short-circuit `external`
and `initial` versions as always visible without index lookup.

### 2. Transform state primitive

Add `actor transform` for pure-function data transforms (no LLM
call). Transforms are looked up by name in the registry. Slice 1
ships exactly one built-in: `anonymize_outputs`. The `.orc` file
references the transform by registered name; arbitrary Python in
workflow files is NOT permitted. This matches the closed-core
direction the codebase already follows for actor backings.

```
anonymize_outputs(named_outputs: dict[str, str]) -> {
    anon_map: dict[str, str],     # {"A": "<output>", "B": "<output>", ...}
}
```

The transform writes ONLY `anon_map`. No `deanon_map` is produced
by the transform, written to the artifact store, or referenced by
any state in the workflow. The chairman state reads named advisor
outputs directly via the artifact store; no de-anonymization step
is needed in the council workflow. This is the workflow contract
for slice C and the validator's expected write set.

**Transform registry contract.** A transform is a registered
callable with the following surface:

- **Registration call shape**:
  ```
  register_transform(
      name: str,
      callable: Callable[[dict, TransformContext], dict],
      input_schema: dict[str, type],   # artifact_name -> Python type
      output_schema: dict[str, type],
  )
  ```
  The registry stores `(callable, input_schema, output_schema)`
  per name. The validator and the executor read from the same
  registry record; there is no parallel declaration of schemas
  in the `.orc` file beyond `reads`/`writes` clauses.
- **Inputs**: a dict `{artifact_name -> artifact_value}` covering
  exactly the artifacts the transform's `reads` clause declared.
  The transform never reads beyond its declared inputs. The
  validator checks that the workflow's `reads` clause keys match
  `input_schema` keys exactly.
- **Outputs**: a dict `{artifact_name -> artifact_value}` whose
  keys are exactly the artifacts the transform's `writes` clause
  declared. Missing keys are an error. Extra keys are an error.
  The validator checks that the workflow's `writes` clause keys
  match `output_schema` keys exactly.
- **Type checking scope (Slice B).** Schemas use a fixed, narrow
  set of types. Slice B supports:
  - primitive types: `str`, `int`, `float`, `bool`, `bytes`.
  - the parameterized type `dict[str, str]` (because
    `anonymize_outputs` needs it).
  Other parameterized generics (`list[T]`, `tuple[T, ...]`,
  arbitrary nested dicts) are NOT supported in Slice B. Adding
  more types is a future slice; the registry rejects schema
  declarations using unsupported types at registration time.

  The validator checks both key sets and value types statically
  against the workflow's artifact type declarations. Runtime
  type checking on actual values uses `isinstance` for primitive
  types and a small custom recursive checker for `dict[str, str]`
  (asserts the value is a `dict`, every key is a `str`, every
  value is a `str`). No third-party type-checking library is
  pulled in.
- **Determinism context**: a `TransformContext` object with
  attributes `run_id: str`, `state_name: str`,
  `sorted_input_keys: list[str]`. No other context is provided.
- **Error behavior**: a transform that raises Python exceptions
  produces an error `state_exit` for its state. The exception
  message is captured in the envelope. There is no transform-level
  retry; transforms are pure functions and retrying them would
  produce identical results.
- **Tentative write path**: transform output goes through the same
  tentative/commit path as adapter output. The per-state sequence
  stages tentative writes before `state_exit` and commits at the
  same point an adapter-backed state would commit. Replay treats
  a completed transform state identically to a completed
  adapter-backed state, including the artifact visibility rule.

**Determinism requirements:**

- The RNG seed is derived from `(run_id, state_name, sorted_input_keys)`,
  not from `run_id` alone. This ensures two transform states in the
  same run produce different mappings (because state_name differs)
  and that the same transform with the same inputs produces the same
  mapping (because sorted_input_keys is stable).
- Input keys are sorted lexicographically before shuffling. Dict
  insertion order from the artifact store cannot be relied on across
  runs.
- Replay treats a completed transform state the same as any
  completed actor state: if `state_exit` is durable, replay reuses
  the logged result and committed artifacts. The transform is NOT
  re-executed on replay.

### 3. Configurable advisor lenses, fixed canonical set

The five canonical Karpathy lenses are part of the workflow contract:

- contrarian
- first_principles
- expansionist
- outsider
- executor_lens (named with `_lens` suffix to disambiguate from the
  code-edit `editor` role family; the lens is a thinking style, not
  the workspace-mutating code editor)

The user controls only the model bindings in their config. Lens
prompts live in template files (one per lens), so a user who wants
to swap a lens later edits the template.

Reviewer: a single `reviewer` role binding. Five reviewer states
are spawned in parallel, all using the same role. **Each reviewer
invocation is a fresh, stateless text-role call. No persistent
session history is shared between reviewer invocations.** This is
guaranteed by the text-role adapter's stateless invocation model:
each `invoke` call builds a fresh subprocess from the constructed
prompt with no continuation token or session id. If a future adapter
introduces session continuity, parallel reviewer states must opt
into stateless mode explicitly.

Chairman: a single `chairman` role binding.

Required role bindings for `ask_council` (eight total):

- `framer` (used by the `frame` state)
- `contrarian`, `first_principles`, `expansionist`, `outsider`,
  `executor_lens` (the five advisor lens roles)
- `reviewer` (used by all five reviewer states)
- `chairman` (used by the chairman state)

The user must bind all eight roles in their global or project
config before `ask_council` runs. Slice C's validator must reject
a config that does not bind all eight. Slice E's README example
must show all eight bindings explicitly.

### 4. Workflow file: `ask_council.orc`

States:

1. `frame`: takes query + history, produces `framed_question` artifact.
   Single text-role state, single model. Role: `framer`.
2. Fan-out from `frame` into five parallel advisor states:
   `contrarian_advise`, `first_principles_advise`, `expansionist_advise`,
   `outsider_advise`, `executor_lens_advise`. Each reads
   `framed_question` and writes its own `<lens>_output` artifact. Each
   uses its corresponding lens role.
3. Join into `anonymize`: transform state. Reads all five advisor
   outputs, writes `anon_map` artifact (only).
4. Fan-out from `anonymize` into five parallel reviewer states:
   `reviewer_1` through `reviewer_5`, all using the `reviewer` role.
   Each reads `anon_map` and writes its own `review_N_output`.
5. Join into `chairman`: text-role state using the `chairman` role.
   Reads `framed_question`, all five advisor outputs by their named
   artifacts (`contrarian_output`, `first_principles_output`, etc.),
   and all five reviewer outputs. Writes `chairman_output`: the
   structured verdict (Where Council Agrees / Where Council Clashes
   / Blind Spots / Recommendation / One Thing First).

The chairman reads named advisor outputs directly from the artifact
store. Anonymization affects only what the reviewers see. There is
no de-anonymization step. There is no `deanon_map` artifact in this
workflow.

### 5. Templates

- `templates/ask_council_framer.md`: takes query + history, produces
  a clear neutral framed question.
- `templates/ask_council_contrarian.md`,
  `_first_principles.md`, `_expansionist.md`, `_outsider.md`,
  `_executor_lens.md`: each contains the lens description, the
  framed question, and instructs the model to lean fully into its
  angle. 150-300 words target.
- `templates/ask_council_reviewer.md`: takes `anon_map` A-E, asks
  the three review questions:
    1. Which response is the strongest? Why?
    2. Which has the biggest blind spot? What is it missing?
    3. What did ALL responses miss?
  Under 200 words. The reviewer prompt MUST NOT contain lens
  identifiers; it sees only A-E.
- `templates/ask_council_chairman.md`: takes framed question,
  named advisor outputs, all five reviews. Produces the structured
  verdict. Headers: Where Council Agrees / Where Council Clashes /
  Blind Spots Caught / Recommendation / One Thing to Do First.

### 6. REPL: surface each state's output

The REPL currently prints only the final answer. For real council,
print each state's output as it completes, labeled by role. Advisor
outputs print first (as they finish, possibly out of order, with
labels like `[Contrarian]:`), then reviewer outputs (`[Reviewer 1]:`),
then the chairman verdict.

Implementation: `orchestra.api.run_verb` gains a callback parameter
`on_state_complete(state_name, role_name, output_text)`. The
callback fires from worker threads as each state's commit completes,
**but it must not write to stdout directly.** The REPL implementation
of the callback enqueues completed outputs onto a thread-safe queue
that the main thread drains, printing each completed state's labeled
block atomically.

**Atomic-print contract (single rule).** Each enqueued item is
`(state_name, role_name, output_text)`. The main-thread drainer
holds a single explicit `threading.Lock` around each block. The
sequence is exactly:

1. Acquire the stdout lock.
2. `sys.stdout.write` the header line `[<role>]:\n`.
3. `sys.stdout.write` the full output text.
4. `sys.stdout.write` a trailing `\n`.
5. `sys.stdout.flush()`.
6. Release the lock.

No other thread writes to stdout while the lock is held. This is
the only acceptable implementation. There is no fallback "single
print call without lock"; the lock is mandatory because one
worker's enqueue can complete during another's drain. Items are
printed in dequeue order, which matches completion order.

For `ask_single` the existing behavior is preserved: the callback
fires once and the output looks the same.

For `ask_propose_critique_synthesize` the user now also sees each
stage labeled. This is a behavior change for that verb but a
positive one.

### 7. Verb mapping

The user updates `~/.orchestra/config.json` themselves. Code does
not edit it. Suggested in the README:

- `ask` -> `ask_single` (unchanged)
- `pair` -> `ask_draft_then_adjudicate` (unchanged)
- `refine` -> `ask_propose_critique_synthesize` (renamed from
  "council")
- `council` -> `ask_council` (new, real council)

The Slice E README example must show all eight required role
bindings for `ask_council` (`framer`, `contrarian`, `first_principles`,
`expansionist`, `outsider`, `executor_lens`, `reviewer`, `chairman`)
in addition to the existing role bindings used by other verbs.

## Implementation order: five slices

Do not do all of this in one push. Stop after each slice and report.

### Slice A: parallel executor primitive

- Extend the spine grammar to parse
  `on complete fan_out [...] join <state> on error <target>`
  transitions. Failure target is required, not optional.
- Validator checks that BOTH `join <state>` and `on error <target>`
  reference declared states reachable from the current workflow.
  A typo in either target is a parse-time error.
- Refactor the per-state sequence into a reentrant helper that
  takes one state name, an invocation_id, and shared services
  (registry, store, log, VisibilityIndex). No shared mutable
  traversal state across worker threads. Workers receive an
  immutable read-only snapshot (captured under both locks per the
  lock-ordering rule) for prior envelopes and global attempt
  counters. The snapshot excludes sibling envelopes and
  sibling-written artifacts. Workers consume the snapshot for
  artifact reads during fan-out execution; live `read_latest`
  calls from within a fan-out child are forbidden.
- Introduce `invocation_id = (run_id, state_name, attempt_seq)`
  minted at `state_enter`. Record invocation_id in `state_enter`,
  every artifact commit by that invocation, and `state_exit`.
- Introduce `producer_kind` field on every artifact version:
  `state_invocation`, `external`, `initial`, or `legacy` for
  pre-migration rows. External and initial are always visible.
- Add the VisibilityIndex: `invocation_id -> Literal["pending",
  "success", "error"]`. Updated by the executor on `state_enter`
  (insert as `pending`), on `state_exit` (update to `success` or
  `error`), and reconstructed by replay from the log. Persisted
  alongside the run's recovery state. Thread-safe interface,
  shared with the artifact store at construction.
- Implement the artifact visibility rule: `read_latest(name)` and
  the snapshot constructor return only versions where either
  (a) `producer_kind` is `external`, `initial`, or `legacy`, or
  (b) `producer_kind` is `state_invocation` AND the producing
  invocation_id has status `success` in the VisibilityIndex.
- Implement the post-fan-out cleanup pass: after `fan_out_end` is
  durable, purge committed artifact versions whose producing
  invocation has status `pending` or `error` in the index.
  Cleanup is replay-safe (idempotent). If interrupted mid-purge,
  orphaned versions remain hidden by the visibility rule and are
  purged by the next replay's cleanup pass.
- Schema migration: forward-only. Add `invocation_id` and
  `producer_kind` columns to the artifact-store schema. Tag
  existing rows as `legacy` with no invocation_id. Slice A's
  startup runs the migration once.
- Helper accepts a "fan-out child" mode flag. When set, the helper
  stops after writing `state_exit`; it does NOT select or follow
  the state's outgoing transitions. Child-local retry policy
  declared on the state IS honored even in fan-out child mode.
  Re-entered children get a fresh retry budget (counter starts at
  zero), regardless of prior partial-run retry-attempt records.
  Each re-entry mints a new invocation_id (new attempt_seq).
- Per-state sequence orders artifact commit BEFORE `state_exit`
  write. `state_exit` durability is the completion point. Replay
  rule: any child without durable `state_exit` is re-entered
  with a new invocation_id.
- Extend Executor to run fan_out transitions via
  `concurrent.futures.ThreadPoolExecutor`. Submit one future per
  child state. Wait on `as_completed`.
- Per-child cancellation handle registry: a `dict[str, ChildHandleEntry]`
  protected by a single `threading.Lock`. All register, lookup,
  unregister, and cancel-flag mutation happens under the lock.
  Each entry has `cancel_requested: bool`,
  `invocation_handle: Optional[...]`, `state: Literal["pending",
  "registered", "done"]`. Pre-registration cancellation sets
  `cancel_requested=True`; the helper checks the flag immediately
  before invoking the adapter and writes an error `state_exit`
  with reason "cancelled" if set, without invoking. Post-registration
  cancellation calls `adapter.cancel()` against the entry's
  invocation_handle.
- Cancellation policy on first child failure (per the request
  handling rules in Section 1):
  - Pending (not yet started) futures: `future.cancel()` plus
    `cancel_requested=True` defensively. No `state_enter` written.
  - Running futures pre-handle: `cancel_requested=True`. Helper
    cancels itself before invoking the adapter; writes error
    `state_exit` with reason "cancelled".
  - Running futures with handle: `adapter.cancel(invocation_handle)`,
    drain to `state_exit`.
  - Group aggregate outcome is fixed at the first durable child
    error. Subsequent successful child outcomes do not change
    routing.
- Controller waits for ALL running children to drain to a durable
  `state_exit` before writing `fan_out_end`. `fan_out_end` is
  never written speculatively and never updated.
- Add a single store-level Python lock around all `ArtifactStore`
  operations. SQLite connection opened with `check_same_thread=False`
  and shared across threads under the Python lock. Hold the lock
  during artifact ops only, not during adapter `invoke`.
- Add a `LogWriter` lock guarding append-and-fsync. All threads
  write through the same writer. The LogWriter lock is the OUTER
  lock and the store lock is the INNER lock per the fixed
  lock-ordering rule. Snapshot capture acquires LogWriter then
  store, releases store then LogWriter.
- Write a `fan_out_start` log record (under both locks per the
  ordering rule, paired with snapshot capture inside the same
  critical section) before submitting futures. Write a
  `fan_out_end` log record after the group's outcome is settled
  (all running children drained), naming the group's aggregate
  outcome, the per-child outcome map (including drained successes
  that landed before `fan_out_end`), and the routing decision.
  `fan_out_end` is the durable commit point for the routing
  decision.
- Routing decisions for fan-out groups read from `fan_out_start`
  plus child `state_exit` records (and `fan_out_end` if present),
  NOT from the parent state's envelope.
- Replay rules implemented per Section 1's Replay rules subsection.
- Validator rejects sibling writes to the same artifact name within
  a fan-out group.
- Tests:
  - Fixture workflow with three parallel mock text states and a
    join. Assert all three commit before join runs. Assert
    chairman's reads see all three artifacts (visibility rule
    satisfied for all-success case).
  - External and initial artifact visibility: fixture has an
    external_input plus an initial-declared artifact; both are
    visible to a state's reads even before any state runs.
    Visibility rule does not hide them.
  - Crash-and-replay tests covering each of the **seven**
    crash-atomicity cases listed in section 1.
  - Reader-thread failure on one child while siblings finish:
    affected child re-entered on replay with a new invocation_id,
    siblings preserved, and incomplete child's prior-run committed
    artifacts are NOT visible to the join state during the failure
    window.
  - Visibility key by invocation, not state: fixture runs the same
    state twice in sequence (e.g., state A succeeds with
    invocation_id_1 producing version V1, replay re-enters A with
    invocation_id_2 producing version V2). Test asserts that V1's
    visibility is independent of invocation_id_2's outcome; if V2
    fails, V1 remains visible (because invocation_id_1 has success
    in the index), demonstrating the producing-invocation key is
    correct.
  - Sibling writes to the same artifact: rejected at validation.
  - Fan-out with invalid `on error` target reference: rejected at
    parse time.
  - Fan-out with invalid `join` target reference: rejected at
    parse time.
  - Failure-path test: one child returns error; pending siblings
    cancelled with `future.cancel()`, running siblings receive
    `adapter.cancel()` against their per-child handles and drained.
    The test must assert that a sibling success recorded AFTER the
    first error does NOT change routing away from the error
    target. Both first-pass execution and replay of the same log
    route to the error target.
  - Cancellation race test: child A errors and child B succeeds at
    nearly the same instant. Group routes to error target. Replay
    of the same log routes to error target. `fan_out_end`
    per-child outcome map includes B's success.
  - Pre-handle cancellation test: controller cancels child A
    while A is between `state_enter` and adapter invocation;
    A's helper observes `cancel_requested=True` and writes error
    `state_exit` with reason "cancelled" without invoking the
    adapter.
  - Per-child cancellation isolation: fixture has two siblings,
    controller cancels child A while B is mid-invocation; B
    completes normally without observing A's cancellation. The
    handle registry's threading discipline is exercised.
  - Fan-out child does not follow its own outgoing transitions:
    fixture child has an `on complete => other_state` declared;
    helper in fan-out child mode stops after `state_exit` and
    does NOT enter `other_state`.
  - Child-local retry in fan-out child mode: fixture child declares
    `on error retry max 2 then fail`; transient adapter error
    triggers two retries inside the helper before producing the
    final error `state_exit`.
  - Re-entry retry budget: fixture child crashes mid-retry on first
    attempt with one logged retry-attempt record; replay re-enters
    the child with retry counter at zero, not one, and a new
    invocation_id.
  - Sibling visibility: fixture has child A that writes its output
    quickly and child B that runs longer; B's prompt resolution
    must NOT include A's envelope or artifacts. Test asserts B's
    snapshot excludes A's writes. Test also confirms B does NOT
    call `read_latest` from within fan-out execution (snapshot
    is the only source).
  - Snapshot atomic-capture lock-order test: instrument the
    LogWriter and store locks to record acquisition order; assert
    the implementation acquires LogWriter first then store, and
    that snapshot construction occurs while both locks are held.
    Direct lock-path instrumentation; no negative-control bypass
    code is used.
  - Lock-order deadlock prevention: a worker thread that needs
    both locks (e.g., a future code path) must follow the same
    LogWriter-then-store order. Test exercises a second code path
    that acquires both locks and asserts no deadlock occurs under
    concurrent pressure with the snapshot path.
  - Commit-vs-state_exit ordering: simulate crash between artifact
    commit and `state_exit` write; replay re-enters the child and
    writes new versions under a new invocation_id. Confirm the
    prior-committed artifact is NOT visible to a hypothetical
    reader during the crash window (visibility rule), and that
    the new invocation's writes become visible only after the
    new `state_exit` is durable.
  - Visibility rule on errored producer: child writes artifact,
    produces error `state_exit`; downstream `read_latest` returns
    the prior visible version (or "no visible version" if there
    was none), not the errored child's commit.
  - Cleanup pass after error: fan-out group with one errored child
    and one successful drained sibling; after cleanup, the
    incomplete-child artifacts are purged, the drained sibling's
    artifacts remain (its invocation has durable success
    `state_exit`).
  - Cleanup-interrupted-mid-purge: simulate crash during cleanup
    on the error path; assert orphaned versions remain hidden by
    the visibility rule, and the next replay's cleanup pass
    completes the purge.
  - Schema migration: starting against a store from a pre-Slice-A
    run, migration adds the columns and tags rows `legacy`;
    `legacy` rows remain visible to a fresh run's reads.
- Does NOT touch the council workflow yet.
- One commit.

### Slice B: transform state primitive and anonymization

- Add `actor transform` to the spine grammar. Transforms register
  in the registry by name. Workflows reference transforms by
  registered name only. Arbitrary Python in `.orc` files is not
  permitted.
- Implement the registration call shape from Section 2:
  `register_transform(name, callable, input_schema, output_schema)`.
  The registry stores `(callable, input_schema, output_schema)`
  per name. Validator and executor read from the same registry
  record.
- Implement the transform registry contract: typed inputs/outputs
  declared by the workflow's `reads`/`writes` clauses, validator
  checks input/output shape statically (both key sets and value
  types against the workflow's artifact type declarations).
- Restrict schema types to the supported set (Slice B): `str`,
  `int`, `float`, `bool`, `bytes`, plus the parameterized type
  `dict[str, str]`. Other types are rejected at registration
  time. No third-party type-checking libraries are pulled in.
- Runtime type checking: `isinstance` for primitive types, a small
  custom recursive checker for `dict[str, str]` (asserts value
  is a `dict`, every key is a `str`, every value is a `str`).
- Built-in `anonymize_outputs` transform. Writes ONLY `anon_map`
  (typed `dict[str, str]`). Seed derived from `(run_id,
  state_name, sorted_input_keys)`. Input keys sorted before
  shuffling.
- Replay treats a completed transform state as any other completed
  state: reuse logged result, do not re-execute.
- Tests:
  - Fixture workflow with a transform state asserts inputs map to
    outputs deterministically across runs (same seed inputs ->
    same mapping).
  - Two transform states in the same run produce different mappings
    (state_name differentiates them).
  - Replay of a completed transform state does not re-execute.
  - Workflow that tries to declare arbitrary Python is rejected at
    parse time.
  - Validator rejects a transform state whose `writes` clause does
    not match the registered transform's `output_schema` keys.
  - Validator rejects a transform state whose `reads` clause does
    not match the registered transform's `input_schema` keys.
  - Validator rejects a transform whose declared input/output
    types disagree with the workflow's artifact type declarations
    (key-shape match but type mismatch).
  - Registration rejects an unsupported type (e.g., `list[str]`)
    at register-time.
  - Transform that raises a Python exception produces an error
    `state_exit` whose envelope captures the exception message;
    the state is NOT retried.
  - Transform whose runtime output values violate `output_schema`
    types produces an error `state_exit` (runtime type check),
    including a `dict[str, str]` whose values include a non-string.
- One commit.

### Slice C: ask_council.orc plus templates plus role naming

- Workflow file with the eleven states (frame, five advisors,
  anonymize, five reviewers, chairman).
- All eleven templates per Section 5.
- Role names per Section 3: framer, the five lens roles,
  reviewer, chairman (eight required bindings total).
- Tests:
  - Workflow loads and validates.
  - End-to-end run with mock adapters: chairman_output prompt
    contains all five named advisor outputs and all five reviewer
    outputs.
  - Reviewer prompts contain `anon_map` A-E and DO NOT contain
    any lens identifier strings (contrarian, first_principles,
    etc.). This catches anonymization regressions.
  - Reviewer states are stateless: a fresh subprocess per
    invocation, no shared session id.
  - Validator rejects a config that does not bind all eight
    required roles for `ask_council`. The error message names
    each missing binding.
- One commit.

### Slice D: REPL state-completion callback

- `on_state_complete` callback parameter in `run_verb`.
- REPL passes a callback that enqueues completed outputs onto a
  thread-safe queue. The main thread drains and prints each block
  atomically using the explicit stdout-lock contract from Section 6.
  No fallback path; the lock is mandatory.
- Tests:
  - REPL with a stubbed multi-state workflow asserts each state's
    labeled output reaches stdout in completion order.
  - Concurrent completion test: two stubbed states finish nearly
    simultaneously; output blocks remain atomic (no interleaving
    of one state's text with another's label or text). Test
    validates that header, body, and trailing newline of each
    block all appear contiguously.
- One commit.

### Slice E: README and verb mapping documentation

- Document the new ask_council workflow.
- Document the role names and lens descriptions.
- Document the recommended verb mapping (refine for the linear
  pipeline, council for ask_council).
- README config example MUST show all eight required role bindings
  for `ask_council` (`framer`, the five lens roles, `reviewer`,
  `chairman`) in addition to existing role bindings.
- One commit.

## Conventions

- No em-dashes, no en-dashes, no semicolons in any prose.
- Never mention Claude, Claude Code, or Anthropic in commit messages.
- Author dislikes verbosity; just work and do.
- Run pytest, ruff check ., and mypy orchestra (strict) after each
  slice.
- Commit and push after each slice. Do not batch slices into single
  commits.

**Bug-fix discipline across slices.**

- Bugs surfaced during work on slice X that trace to slice X's own
  code: fixed before slice X's commit lands. Standing rule, no
  deferral.
- Bugs surfaced during work on slice X (X = B, C, D, E) that trace
  to an earlier slice's already-committed code: stop slice X work,
  ship the fix as a separate commit on top of HEAD before
  continuing slice X. Do NOT batch the fix into slice X's feature
  commit. Do NOT defer to slice X's commit. Do NOT retroactively
  fold into the earlier slice's original commit history.
- The fix commit must keep all earlier slices' tests green; if the
  fix requires changing earlier slices' tests, those test changes
  go in the same fix commit. Update test expectations only when the
  prior expectations were incorrect (the bug being fixed); do not
  weaken tests to accommodate the fix.
- Resume the current slice's work only after the fix commit lands.
- Operationally: `stop -> ship fix commit -> verify all tests
  green -> resume current slice`.

**Test discipline.**

- Tests must check user-visible output, not just structural shape.
  The recent council pipeline bug (empty proposer output flowing
  through correctly but invisibly) was missed because tests asserted
  shape, not content. Every fix must include an end-to-end test
  that asserts on what the user would see.

## Stopping points

Stop after each slice and report. The user will run end-to-end tests
after slice E.

Begin with slice A.
