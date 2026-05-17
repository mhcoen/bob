# McLoop de-split: parity audit and behavior-preserving integration plan

Status: design/planning document; completed implementation steps are
recorded inline with commit hashes. Every claim
below was verified by reading source on this machine; citations are
`file:symbol` (and explicit line constants where the file numbers them).
The governing constraint is restated verbatim from the source design
doc and is the acceptance bar for the whole effort.

Audit trail (2026-05-17): two independent adversarial reviewers
(Kimi K2.6, Claude Opus 4.7) re-derived every load-bearing claim
from current source and converged on zero behavioral defects. Their
findings reports are at `bob/design/desplit-independent-validation-kimi.md`
and `bob/design/desplit-independent-validation.md`. Three mechanical
doc corrections from those reports — extending the §2 freeze
invariant to scan BUGS.md as well as PLAN.md (B-1), adding
`tests/test_args.py` to the §2(h) test deletion surface (B-2), and
replacing the brittle B1 pre-flight SHA fingerprint with a shape
invariant the human re-verifies just before cutover (B-3) — are
folded in below.

Authoritative architecture reference: `bob/design/planfile.md` §8
"Phase B: McLoop migration" and its "Consumer contract" paragraph
(quoted in §0.2 below). This document derives strictly from that
section and from the parity findings in §2.

---

## 0. State verification (confirmed from source, not assumed)

0.1 **planfile surface is live and complete as claimed.**
`bob-tools/bob_tools/planfile/__init__.py:19-50` imports, and
`:52-88` exports, the runtime surface used by this migration
(`parse_plan`,
`render_plan`/`canonicalize`, `migrate`, `next_tasks`,
`complete_task`, `fail_task`, `reset_task`, `clear_failed`,
`add_task`, `replace_phase`, `resolve_task_context`,
`check_consistency`, `validate_plan`, `load`/`save`/`update`, model
types, `Settlement`, `Outcome`). `clear_failed` is implemented in
`operations.py:1058-1097`; `validate_plan` remains implemented at
`operations.py:202` and is now exported at the package root.
`bob-tools/PLAN.md` Stages 1–8 are all `[x]`; Stage 9 is the
DEFERRED bugfile layer with no checkbox tasks by design
(`PLAN.md:293-295`). Stage 6 fileio is implemented with
`fcntl.flock` `LOCK_EX` on a sidecar lock
(`fileio.py:89-106`), atomic `tempfile`+`fsync`+`os.replace`
(`:109-135`), and `update()` mid-flight byte-comparison raising
`ConcurrentUpdateError` (`:156-188`).

0.2 **planfile is editable-installed into mcloop's venv (confirmed).**
`mcloop/.venv/lib/python3.13/site-packages/__editable__.bob_tools-0.1.0.pth`
loads `__editable___bob_tools_0_1_0_finder.py`, whose
`MAPPING = {'bob_tools': '/Users/mhcoen/proj/bob-tools/bob_tools'}`
at finder line 9. `import bob_tools.planfile` from mcloop's runtime
resolves to the live source tree. (`bob_tools-0.1.0.dist-info` also
present.)

0.3 **Empirical parser parity already exists and passes.**
`bob-tools/bob_tools/planfile/tests/test_mcloop_parity.py` imports the
live `mcloop.checklist` and asserts, on the real
`/Users/mhcoen/proj/{duplo,mcloop}/PLAN.md` and
`mcloop/PLAN.EXAMPLE.md`, structural equality of: task positions,
indent levels, checkbox status, bugs-section presence/count, phase
ordinals, per-phase task counts, per-task `[RULEDOUT]` counts
(`test_mcloop_parity.py:343-520`). It encodes the one-sided
`bob ⊆ mcloop` operational-tag allowance (`:454-497`): current
mcloop USER is anchored, so live divergence is only possible for
BATCH/AUTO prose mentions (§2(d)), while the test still guards all
three tags against bob-side over-recognition. It asserts nothing else
differs. It currently passes in this dev environment:
`PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider
bob_tools/planfile/tests/test_mcloop_parity.py` → `3 passed in
0.73s`. This test is the parser half of the acceptance bar and is
reused, not re-derived, below.

0.4 **Phase B contract (quoted from `bob/design/planfile.md` §8).**
"McLoop's settle hook calls `planfile.resolve_task_context` instead
of `ledger_emit.find_explicit_phase_id_for_task`. `resolve_phase_id`
becomes a shim. … `mcloop.checklist.parse` delegates to
`planfile.parse_plan`. `check_off`, `mark_failed`, `find_next`
delegate to `planfile.complete_task`, `fail_task`, `next_tasks`. …
The split-plan runner is the proven, mostly-working status quo, but
`planfile` is its principled successor. The migration does not use an
interim direct-PLAN.md checklist hack. The ordered path is: first
finish the planfile operations, mutation/checkoff, and file-I/O
surface; then migrate McLoop to consume that deterministic API with
PLAN.md as the sole authoritative build document; then eliminate
CURRENT_PLAN.md, `mcloop/plan_split.py`, and the split-specific
tests."

0.5 **Not independently audited here** (stated as "believed true" in
the brief; outside the read-only source surface): the specific mcloop
defect-fix commit SHAs (18206df, 7b8a8d0, 3ac67dd, 3dd7e06, 5b5b03d)
and the bob-tools HEAD 0767951. The *behaviors* those commits claim
to produce were verified from current source where the parity audit
depends on them (notably the Defect-C anchored `is_user_task`, §2(d)).

---

## 1. Governing risk (restated; this is the acceptance bar)

The swap from `mcloop/checklist.py` + `mcloop/plan_split.py` to
`bob_tools.planfile` must be **behavior-preserving**: mcloop's runtime
semantics must not silently change. The prior failure mode this entire
effort corrected was "looks complete, isn't". Therefore every
behavioral divergence between the two implementations is made explicit
and tested below, never assumed equivalent. Where planfile is strictly
*better* (atomic writes, locking, leading-anchored tag detection,
tokenized phase resolution), that improvement is itself a divergence
from current runtime behavior and is staged and tested as such, not
folded silently into the parser swap.

---

## 2. Parity audit (read-only; per item; MATCH / DIVERGENCE)

Classification key: **MATCH** = byte-or-semantically equivalent on the
inputs mcloop actually runs against; **DIVERGENCE** = a real
behavioral difference, tagged with remediation: `[planfile-API]`,
`[mcloop-adapt]`, or `[accepted-doc]`.

### Pre-cutover freeze invariants

From this point until the atomic B1+B3 cutover completes, **both**
`mcloop/PLAN.md` **and** `mcloop/BUGS.md` must preserve the
state-dependent assumptions that make the accepted-doc divergences
inert. BUGS.md is in scope because `planfile.next_tasks` walks
`plan.bugs.tasks` ahead of phase tasks
(`bob_tools/planfile/operations.py:703-711`) through the same
`_walk_actionable`/`_deps_satisfied` paths used for phase tasks, and
mcloop already reads BUGS.md as a separate plan input on every
iteration (`mcloop/main.py:981`, `:1012`, `:1057`, `:1807`, `:1862`,
`:1930`, `:1959`). So an `@deps` line or a divergence-relevant
prose tag added to BUGS.md would trigger the §2(a)/§2(d) divergence
identically to one added to PLAN.md.

1. No `@deps` line is introduced (§2(a)). mcloop's checklist ignores
   dependencies, while planfile scheduling honors them.
2. No incomplete task line contains a non-leading `[BATCH]` or
   `[AUTO...]` prose mention (§2(d)). USER is intentionally excluded
   from the guard: §2(d) records USER classification as a confirmed
   **MATCH** between post-Defect-C `checklist.is_user_task` (anchored
   at `checklist.py:633-642`) and planfile's leading-only `flag_tags`
   (`bob_tools/planfile/parser.py:849-865`), so a USER prose mention
   is classification-inert on both sides and needs no divergence
   guard. Current source verification, run from
   `/Users/mhcoen/proj/mcloop`:
   `rg -n "@deps" PLAN.md BUGS.md` returns no matches.
   `rg -n "^[[:space:]]*- \[[ !]\].*\[(BATCH|AUTO[^\]]*)\]" PLAN.md BUGS.md`
   returns no matches. The existing prose-mention `[BATCH]` lines in
   PLAN.md are DONE, so they are not selected by the scheduler.

### (a) Next-task selection

mcloop: `checklist.find_next` → `_search_in_stage(tasks,"Bugs")` first;
else `current_stage(tasks)` (`checklist.current_stage` at
`checklist.py:321-333` → `_stage_complete` at `:302-318`, where any
non-checked task including `[!]` makes the stage incomplete) then
`_search_tasks(required_stage=active_stage, skip_stages={"Bugs"})`
(`find_next` at `:440-466`, `_search_in_stage` at `:469-475`).
DFS leaf-before-parent is in `checklist._search_tasks`
(`:362-411`); no `@deps` concept exists in mcloop.
planfile: `operations.next_tasks` → `_walk_actionable(plan.bugs.tasks)`
first; else first phase failing `_phase_complete` (FAILED ≠ complete,
mirrors `_stage_complete`), then `_walk_actionable` over `phase.tasks`
then each `subsection.tasks`. DFS leaf-before-parent in
`_walk_actionable` (`operations.py:591-666`); `@deps` via
`_deps_satisfied` (`:515-525`); phase completion via
`_phase_complete` (`:538-547`); top-level selection in
`next_tasks` (`:669-720`).

- DFS order, first-incomplete-phase scoping, leaf-before-parent:
  **MATCH**. `_walk_actionable` is a line-for-line re-expression of
  `_search_tasks`; `_phase_complete` mirrors `_stage_complete`
  (FAILED blocks completion in both). Empirically confirmed for task
  positions/ordinals/per-phase counts by §0.3.
- `@deps` gating: **MATCH (vacuous)** on existing mcloop plans —
  `mcloop/PLAN.md` has no `@deps` lines, and `_deps_satisfied` returns
  `all(()) == True`. **DIVERGENCE [accepted-doc]** if a `@deps` line
  is ever added to mcloop's PLAN.md before the swap: it changes
  scheduling under checklist (which ignores it) vs planfile (which
  gates on it). Mitigation: enforce the pre-cutover freeze invariant
  above; do not introduce `@deps` into the authoritative PLAN.md until
  after Stage B3.
- Subsection ordering: **DIVERGENCE [accepted-doc]**. mcloop parses
  linearly; a `### Manual verification` heading is *not* a
  `STAGE_RE`/`BUGS_RE` match (`checklist.parse`), so its tasks stay in
  document order in the single flat stack. planfile separates
  `phase.tasks` from `subsection.tasks` and `next_tasks` walks
  `(phase.tasks, *sub.tasks)` — phase-level tasks first, *then*
  subsection tasks. If a subsection physically precedes phase-level
  tasks in the file, the chosen next task differs. This cannot arise
  on canonical output: `renderer._render_phase_into` emits phase tasks
  then subsections, and the parser does not auto-close a subsection
  (renderer.py docstring + NOTES 2026-05-16 task 4.2.1), so post-`###`
  tasks are subsection tasks by construction. Resolution: the one-time
  canonicalization in Stage B1 makes the authoritative PLAN.md
  canonical, after which the divergence is structurally impossible.
  Pin a parity test on the pre-migration file.
- BATCH return shape: **DIVERGENCE [mcloop-adapt]** — see (c).

### (b) Failed-sibling blocking

mcloop `checklist._search_tasks` (`checklist.py:362-411`):
`if task.failed: if is_subtask: return None; continue` at `:375-382`
(root-level failed skipped, not blocking later roots; subtask failed
blocks later siblings under the same parent); parent-with-failed-child:
`if any(c.failed for c in task.children): if is_subtask: return None;
continue` at `:402-407`. Top-level `find_next` calls
`_search_tasks(..., is_subtask=False)` at `:462-466`.
planfile `operations._walk_actionable` (`operations.py:591-666`):
`if task.status==FAILED: if is_subtask: return; continue` at
`:622-626`; and the no-actionable-descendant branch `if any(c.status
== FAILED for c in task.children): if is_subtask: return; continue`
at `:657-665`. `next_tasks` passes `is_subtask=False` for
`plan.bugs.tasks` at `:703-706`, and for `phase.tasks` / each
`sub.tasks` at `:713-719`.

**MATCH.** Identical control flow including the root-skip /
subtask-block asymmetry and the failed-child-blocks-parent rule.

### (c) BATCH parent surfacing

mcloop: `find_next` returns the first actionable *leaf*; `main.run_loop`
then computes `parent = find_parent(tasks, task)` and, if
`is_batch_task(parent)`, calls `checklist.get_batch_children(parent)`
to assemble the batch. Selection logic in
`checklist.get_batch_children`: skip DONE (set `seen_non_failed`),
FAILED → break if `batch or seen_non_failed` else continue,
`is_user_task`/`is_auto_task` child → break, else append
(`checklist.py:695-719`; `is_batch_task` at `:686-692`).
planfile: `next_tasks` surfaces the **parent** itself —
`_walk_actionable` does `dataclasses.replace(task,
children=_get_batch_children(task))` when `"BATCH" in task.flag_tags`
and drains the child iterator (`operations.py:636-649`).
`operations._get_batch_children` (`:550-575`) mirrors
`checklist.get_batch_children`.

- Batch child *selection*: **MATCH** (`_get_batch_children` ==
  `get_batch_children`).
- Scheduler *return shape*: **DIVERGENCE [mcloop-adapt]**.
  `find_next` → leaf; `next_tasks` → synthetic BATCH parent
  (`replace(parent, children=batch)`). A drop-in `find_next →
  next_tasks` swap changes what `run_loop` receives. Remediation:
  the Stage B3 scheduler shim normalizes this to mcloop's current
  shape — when `next_tasks` yields a node with `"BATCH" in
  flag_tags`, the shim returns the surfaced parent and `run_loop`'s
  existing batch block consumes `node.children` directly instead of
  recomputing `get_batch_children(parent)`. Net control flow
  (batch retries, `batch_exhausted`, `mark_failed` of parent+children
  on exhaustion) is preserved unchanged.
- `is_batch_task` substring vs `flag_tags` token: see (d).

### (d) USER/AUTO/BATCH classification

mcloop: `checklist.is_user_task` = `text == "[USER]" or
text.startswith("[USER] ")` (`checklist.py:633-642`; anchored; this
is the post-Defect-C form, confirmed in current source).
`checklist.is_auto_task` = `_AUTO_TAG_RE.search(task.text)`
(`:722-729`, substring, anywhere). `checklist.is_batch_task` =
`"[BATCH]" in task.text` (`:686-692`, substring, anywhere).
`[USER]`/`[BATCH]`/`[AUTO]` constants/regex at `checklist.py:25-27`.
planfile: parser extracts `flag_tags`/`action_tag` **leading-only**
after stripping a leading `T-NNNNNN:` (`parser._extract_flag_tags`,
`_extract_action_tag`, `_build_task`). USER = `"USER" in
task.flag_tags`; AUTO = `task.action_tag is not None`; BATCH =
`"BATCH" in task.flag_tags`.

- USER: **MATCH on compat (no-ID) plans.** Both are leading-anchored.
- AUTO, BATCH: **DIVERGENCE [accepted-doc]**, one-directional `bob ⊆
  mcloop`. mcloop's substring matchers classify prose-mention tasks
  (a task whose body merely contains `[BATCH]`/`[AUTO:x]`) as
  batch/auto; planfile does not. This is the live divergence guarded
  by `test_mcloop_parity.py`: the test's module docstring still
  describes USER as substring-matched, but its executable assertions
  call the current anchored `checklist.is_user_task` and the substring
  `is_batch_task`/`is_auto_task` (`test_mcloop_parity.py:454-497`).
  Current `mcloop/PLAN.md` contains three `[BATCH]` prose-mention
  tasks (`PLAN.md:341`, `:359`, `:439`) and no `[AUTO...]` lines;
  all three prose-mention tasks are `[x]`, so the substring-vs-leading
  divergence is observationally inert in the current scheduler state
  because completed tasks are never selected. There are no incomplete
  tasks whose classification differs. planfile is the more-correct
  behavior. Acceptance: keep the parity test as the regression guard
  and assert the true invariant on live fixtures: every
  prose-mention tag-bearing task is DONE, and no incomplete task
  differs in classification. This is the §2 pre-cutover freeze
  invariant; the swap intentionally adopts the corrected
  classification. Note: this premise was corrected during
  B0.2 (`117f3ac`); the earlier audit wording incorrectly implied
  prose-mention `[BATCH]` tasks were absent rather than present-but-DONE.
- **HIGH-SEVERITY interaction with task IDs.** `is_user_task` checks
  raw `task.text`. After `migrate()` prepends `T-000NNN:`, the line
  body is `T-000NNN: [USER] …`, which does **not** satisfy
  `text == "[USER]"` nor `startswith("[USER] ")` → mcloop's
  `is_user_task` returns **False** for every USER task
  (`checklist.py:633-642`); the `_collect_body` `[USER]` body capture
  in `checklist.parse` also breaks because parse collects the body
  only when `text == _USER_TAG or text.startswith(f"{_USER_TAG} ")`
  (`checklist.py:210-228`). planfile strips a leading task ID before
  tag extraction (`parser.py:668-688`) and then recognizes only
  leading flag/action tags (`parser.py:849-885`). `is_auto_task`/
  `is_batch_task` survive the ID prefix in current mcloop because
  they `search`/`in` anywhere. `STAGE_RE` survives (bare digits
  unaffected). Net:
  running `bob-plan fmt` (= `parse;migrate;save`) on the authoritative
  PLAN.md while mcloop still uses `checklist` **silently breaks USER
  task handling**. This is the central ordering constraint and the
  reason the swap must be atomic (§3, Stage B1/B3 coupling). It also
  qualifies `bob/design/planfile.md` §11 open-question 2: `bob-plan
  fmt` is **not** safe to run on `mcloop/PLAN.md` until mcloop no
  longer uses `checklist`.

### (e) Checkoff / derived parent completion / Settlement vs ledger

mcloop: `checklist.check_off` writes one `[x]` line via
`_find_task_line` (stale-line-tolerant: line number, then text+indent
+stage fallback, prefer unchecked), then `_auto_check_parents`
re-parses (`check_structure=False`) and silently `[x]`-marks any
parent whose children are all checked — **no ledger event**
(`checklist.py:478-558`, `_auto_check_parents` at `:771-797`).
`main.run_loop` standard success: `check_off(active_file, task)` then
`_ledger_settle(label, TaskOutcome(success=True, …))` →
`resolve_phase_id` + `emit_task_lifecycle_events`, which emits
`commit_landed` **only if `_git_head_sha` is non-None**
(`main.py:1681-1722`; `ledger_emit.emit_task_lifecycle_events` at
`ledger_emit.py:376-475`, git gate at `:461-465`). `[AUTO]` and successful
`[USER]` tasks are checked off with **no `_ledger_settle` call**
(verified: the `is_auto_task`/`is_user_task` branches in `run_loop`
call `check_off`/`completed.append`/`ctx.add`/`notify` only:
`main.py:1201-1224` and `:1226-1255`).
Commit-failure path: `_ledger_settle(failure_kind="commit_failed")`
but **no `mark_failed`** before the terminal break
(`main.py:1660-1673`). Retry exhaustion: `mark_failed` then
`_ledger_settle(success=False, abandoned=True,
failure_kind="max_retries_exceeded")` (`main.py:1769-1786`).
planfile: `complete_task` → `(plan, (direct, *derived))`. `direct`
kind via `_direct_completion_kind`: `action_tag is not None or
"USER" in flag_tags → "work_observed"` else `"commit_landed"`,
`ledger_event_required=True`. Derived parents → `kind="none"`,
`ledger_event_required=False`. `fail_task` → single `"test_failed"`,
`failure_kind` from `Outcome` or `"max_retries_exceeded"`, `cascade=
False`. `reset_task` → `"none"`, no event. All three resolve by
`task_id` via `_find_task_by_id` (`operations.py:107-122`), and
missing IDs or missing matches raise `ValueError` in
`complete_task` (`:933-935`), `fail_task` (`:983-985`), and
`reset_task` (`:1016-1018`). Status application and derived parent
cascade are in `_flip_in_tree`/`_apply_status_to_plan`
(`:727-875`); Settlement shapes are in `_direct_completion_kind` and
the three public ops (`:879-1030`).

- Derived parent completion (no event): **MATCH**. mcloop's silent
  `_auto_check_parents` ≡ planfile's `ledger_event_required=False`
  derived Settlement; equivalent end state.
- `commit_landed` git-gating: **MATCH preserved by construction.**
  planfile only emits the *descriptor*; the wire event is built by
  the caller (design §5). As long as the Stage B2 shim keeps
  `emit_task_lifecycle_events` as the event constructor, the
  "no sha ⇒ no commit_landed" gate is unchanged.
- **DIVERGENCE [mcloop-adapt], CONSEQUENTIAL: AUTO/USER now settle.**
  planfile's model says AUTO/USER success → `work_observed`
  (`ledger_event_required=True`); mcloop today emits *nothing* for
  these. Mapping every `complete_task` Settlement to a ledger event
  would start writing `work_observed` into the audit stream, which
  feeds `ledger_pause.evaluate_and_maybe_pause` and can trigger
  reauthor / `HardStop`. Per design §5 this is the *intended target*
  contract, but it is **not behavior-preserving**. Resolution
  (Decision D1, §4): the Stage B3 scheduler/mutation swap keeps the
  current emission profile — the settle hook ignores
  `kind=="work_observed"` Settlements — and a separate, later,
  independently-tested stage (Stage B6) opts into emitting them. This
  keeps the swap behavior-preserving and isolates the audit-stream
  change.
- **DIVERGENCE [mcloop-adapt]: commit-failure must not mark `[!]`.**
  mcloop currently does not write a checkbox on commit failure
  (design §5 records this as a current inconsistency). If the shim
  routes commit failure through `fail_task`, a new `[!]` appears that
  mcloop never wrote. Remediation: the shim calls `fail_task` only on
  the retry-exhaustion path (which today calls `mark_failed`);
  commit-failure keeps today's "no checkbox change, terminal break,
  emit commit_failed event" exactly.
- **DIVERGENCE [hard constraint]: `complete_task` needs task IDs.**
  `complete_task`/`fail_task`/`reset_task` resolve by
  `task_id` (`_find_task_by_id`); a compat plan with no IDs makes
  `_find_task_by_id` return `None` → `ValueError`. mcloop's
  `check_off` identifies the task by `Task` object (line+text), no
  ID. Therefore the authoritative PLAN.md **must be migrated (IDs
  assigned)** before planfile mutation can be used — but migration
  breaks `checklist.is_user_task` (§2(d)). The only consistent
  resolution is an **atomic** Stage B1+B3: migrate the file and switch
  classification+scheduling+mutation to planfile in one cutover, never
  an intermediate state where checklist sees an ID-bearing file or
  planfile mutation sees an ID-less one. This is the linchpin of the
  whole plan.

### (f) Failure marking & reset

mcloop: `checklist.mark_failed` rewrites `[ ]→[!]` or `[x]/[X]→[!]`
(handles Claude pre-checking), no parent cascade
(`checklist.py:565-596`).
`checklist.clear_failed_markers` (on `--retry`) regex-rewrites every
`^(\s*)- \[!\] ` → `- [ ] ` across the *active files*
(`CURRENT_PLAN.md` + `BUGS.md`), returns a count, anchored so prose
containing `- [!]` is not corrupted (`checklist.py:599-624`).
planfile: `fail_task` → FAILED regardless of prior status,
`cascade=False`. `reset_task` → single task FAILED→TODO by ID,
`kind="none"` (`operations.py:964-1030`). There is no bulk
`clear_failed` export in `bob_tools.planfile.__all__`
(`__init__.py:50-84`) and no `clear_failed` symbol in
`operations.py`, so B0.1 is required.

- `mark_failed` semantics (mark even if `[x]`, no cascade):
  **MATCH** (`fail_task` flips to FAILED unconditionally,
  `cascade=False`).
- `--retry` bulk clear: **DIVERGENCE [planfile-API or
  mcloop-adapt]**. mcloop clears *all* `[!]` across two files in one
  pass without IDs; `reset_task` is per-ID. Post-migration (IDs
  present) the equivalent is: parse PLAN.md (+BUGS.md), iterate every
  FAILED task, `reset_task` each, save. Remediation options: (i)
  mcloop-side loop using `_iter`-style traversal already available via
  `next_tasks`/model, or (ii) add `planfile.clear_failed(plan)->Plan`
  bulk op. Recommendation: option (ii), trivial and keeps the bulk
  semantics atomic under one `update()`; small `[planfile-API]`
  addition, gated to Stage B-pre work so it does not block the
  cutover.

### (g) Plan mutation safety

mcloop: `checklist.check_off`/`mark_failed`/`clear_failed_markers`/
`_auto_check_parents` do `read_text → mutate → write_text`
(`checklist.py:547-558`, `:572-596`, `:608-623`, `:781-797`) —
non-atomic, unlocked, single-line edits. `plan_split` uses the same
plain file rewrite model for `CURRENT_PLAN.md`/PLAN.md extraction and
transition: it imports checklist at `plan_split.py:21-28`, defines
the split filenames at `:31-33`, writes the active file in
`ensure_current_plan` (`:182-205`), and replaces it during
`transition_phase` (`:214-240`).
Concurrency contract is a printed warning ("Do not edit
CURRENT_PLAN.md or BUGS.md while mcloop is running"); resilience to
external edits comes only from `_find_task_line`'s stale-line
fallback (`checklist.py:478-537`).
planfile: `fileio.save` = atomic tempfile+fsync+`os.replace` under
`fcntl.flock` `LOCK_EX` on a sidecar `.lock`; `fileio.update` =
load → lock → re-read → byte-compare → `ConcurrentUpdateError` on
external change → apply → save (`fileio.py:89-188`).

- Atomicity/locking: **DIVERGENCE [mcloop-adapt], strictly safer.**
  Adopting `update()` adds crash-safety mcloop never had. The only
  behavior change to *guard* is that `ConcurrentUpdateError` would
  abort a mutation where mcloop today silently last-write-wins (with
  stale-line recovery). To preserve "the run keeps going", the Stage
  B3 shim wraps `update()` in a bounded retry: on
  `ConcurrentUpdateError`, re-`load` and re-apply the same operation
  (the file is the writable surface; re-deriving the mutation against
  current bytes reproduces mcloop's current resilience). Net survival
  behavior preserved; durability improved.
- **DIVERGENCE [hard constraint], HIGHEST behavior-preservation
  risk: whole-file canonical rewrite.** `fileio.save` writes
  `render_plan(plan)` — canonical form: 2-space indent, `T-NNNNNN:`
  IDs, `<!-- phase_id: … -->` comments, magic line if set, prose
  blank-line normalization (`parser._finalize_prose`), phase-tasks-
  then-subsections ordering. mcloop's `check_off` today changes
  **exactly one line**. `mcloop/PLAN.md` is human-edited,
  git-tracked, uses **3-space** subtask indent and `## Stage N:`
  headings with no IDs/comments (verified: `mcloop/PLAN.md:14-21`;
  grep finds `## Stage` and 3-space task indents, and no `T-NNNNNN:`
  or `phase_id` markers anywhere in the file).
  The "lossless canonicalize" invariant
  (`render(parse(x))==x` fixed point, `bob/design/planfile.md`
  §3.2; round-trip Stage 8 tests) guarantees the fixed point only on
  *already-canonical* input. `render(parse(human_text)) !=
  human_text` for mcloop's current non-canonical file. Consequence:
  the *first* planfile write reflows the entire PLAN.md and every
  subsequent commit's PLAN.md diff changes character. Resolution:
  Stage B1 performs the canonicalization+migration **once**, as a
  dedicated human-reviewed commit, before the cutover. After B1 the
  file is at the renderer fixed point, so every later mutation diff
  is again single-line (only `[ ]→[x]`/`[!]`), which Stage B3's
  acceptance test asserts.

### (h) Deletion-surface inventory (CURRENT_PLAN.md / plan_split.py / checklist.py couplings)

Verified by reading every importer. "Coupled" = imports
`mcloop.checklist` and/or `mcloop.plan_split`, or hard-codes the
split-file model.

| Module | Coupling | Disposition |
|---|---|---|
| `mcloop/plan_split.py` | imports `checklist.{BUGS_RE,CHECKBOX_RE,STAGE_RE,_stage_complete,get_stages,parse}` at `plan_split.py:21-28`; defines `MASTER_PLAN/CURRENT_PLAN/BUGS_FILE` at `:31-33`; owns `extract_next_phase` (`:36-59`), `_extract_stage_content` (`:62-84`), `_extract_flat_tasks` (`:87-123`), `get_current_phase_name` (`:126-135`), `mark_phase_complete` (`:138-179`), `ensure_current_plan` (`:182-205`), `ensure_bugs_file` (`:208-211`), `transition_phase` (`:214-240`) | **DELETE in full** (terminal stage) |
| `mcloop/main.py` | imports checklist names at `main.py:17-40` and `plan_split.{BUGS_FILE,CURRENT_PLAN,ensure_bugs_file,ensure_current_plan,get_current_phase_name,transition_phase}` at `:107-114`; `run_loop` binds `master_path/current_plan_path/bugs_path` at `:689-693`, clears failed markers in active files at `:695-703`, passes interrupt `active_paths=[bugs,current,master]` at `:713-724`, and prints the split warning at `:1042-1048` | **Primary migration site** (Stages B2–B5) |
| `mcloop/lifecycle.py` | imports `checklist.{CHECKBOX_RE,Task,mark_failed,parse}` at `lifecycle.py:14-19`; `_check_interrupted` documents `active_paths` including `BUGS.md, CURRENT_PLAN.md, PLAN.md` at `:76-87` and uses them at `:103`; `_all_tasks` is `:240-245`; `_write_ruledout_to_plan` uses `CHECKBOX_RE` to insert `[RULEDOUT]` at `:248-261` | **Migration site** (Stage B4): re-point to planfile types/ops; collapse `active_paths` to `[BUGS.md, PLAN.md]` |
| `mcloop/output.py` | imports `checklist.{Task,count_unchecked,current_stage,find_next,get_stages}` at `output.py:10-16`; `_dry_run` uses `get_stages/current_stage/find_next` at `:21-46`; `_print_summary` counts remaining via `count_unchecked` at `:114-150` | **Migration site** (Stage B3/B5): `find_next→next_tasks`, `count_unchecked`→model walk; `_dry_run` reimplemented against `Plan` (no direct `current_stage`/`get_stages` analog) |
| `mcloop/investigate_cmd.py` | imports `checklist.{Task,parse}` at `investigate_cmd.py:19-20` for *generated worktree investigation plans*: appends verification tasks to `PLAN.md` at `:286-290`, parses generated `PLAN.md` for status at `:378-386`, and writes generated `PLAN.md` at `:634-637`; not a CURRENT_PLAN split coupling | **Separate, lower-risk site** (Stage B5): re-point `parse→parse_plan`; verify the generated-plan format parses (its `## Bug Description` etc. headings are non-Stage → orphan/preamble in planfile; may require `generate_plan` to emit a Stage heading or a planfile compat allowance — pin a test) |
| `mcloop/maintain.py` | imports `checklist.CHECKBOX_RE` at `maintain.py:13`; `parse_invariants` uses it for `MAINTAIN.md` unchecked invariant lines at `:66-82` — **not** PLAN.md, **not** the split | Decoupled from de-split. Keep a local regex or move `CHECKBOX_RE` to a shared constant when `checklist` is deleted. Not a CURRENT_PLAN coupling. |
| `mcloop/ledger_emit.py` | does **not** import `checklist`; owns `parse_plan_phase_ids` (`ledger_emit.py:72-89`), `find_explicit_phase_id_for_task` (`:92-119`), and `resolve_phase_id` (`:122-145` and following) using `_PHASE_HEADER_RE`/`_PHASE_ID_COMMENT_RE` | **Shim site** (Stage B2): `resolve_phase_id` becomes a `resolve_task_context` shim (design §7.1) |
| `mcloop/{run_summary,checks,runner}.py` | **No checklist/plan_split import** (verified). `runner` consumes a precomputed `eliminated` list (`main` computes `get_eliminated`); RULEDOUT reaches prompts through `_build_*_prompt`'s "RULED OUT APPROACHES" blocks at `runner.py:376-385`, `:422-431`, `:482-488`, and `run_task(... eliminated=...)` at `:523-536` | No change |
| `mcloop/{errors,sync_cmd,audit,claude_md_sync,dep_validator,investigator,review_integration,code_edit}.py` | reference `"PLAN.md"`/`"BUGS.md"` *string literals* only where relevant (`errors.py:138-139` legacy PLAN insertion and `:218-230` BUGS insertion, `sync_cmd.py:1-73` PLAN sync text/diff, `audit.py:39-63/:103-134/:339` audit-report/BUGS handling, `review_integration.py:94/:137` BUGS insertion); no `checklist`/`plan_split` import | No change (string literals; not API-coupled) |

Test deletion/migration surface (verified by
`rg -n "from mcloop\.(checklist|plan_split)" tests`):
`tests/test_plan_split.py` imports `mcloop.plan_split` at `:14-20`
and exercises `CURRENT_PLAN.md` behavior at `:210-230` (delete with
`plan_split.py`); `tests/test_checklist.py` imports checklist at
`:3` (module-level) plus 10 in-function imports of `Task` /
`_find_task_line` at `:788, :1144, :1162, :1170, :1171, :1194,
:1195, :1219, :1220, :1433` (parser-behavior tests — retarget to
planfile or delete once `checklist` is gone);
`tests/integration/test_checklist_integration.py` imports checklist
ops at `:5`; `tests/integration/test_subtask_ordering.py` drives
`run_loop` against PLAN fixtures at `:10-30` (scheduler
integration — retain as planfile scheduler-parity tests);
`tests/test_output.py` imports `checklist.parse` in `_dry_run`
fixtures at `:99` and `:114`; `tests/test_lifecycle.py` imports
`Task` at `:27` (in-function) and asserts split-file interrupt
behavior at `:804+`; `tests/test_args.py` imports
`from mcloop.checklist import Task` at `:13` (module-level) plus 9
in-function imports of `parse as parse_checklist` / `parse as
cl_parse` / `Task` at `:4309, :4340, :5980, :6264, :6276, :6600,
:9893, :9942, :9988` — **Stage D1 deletion-prerequisite**
(retarget `Task`/`parse` to `_planfile_compat` or planfile types
before `mcloop.checklist` is reduced/deleted); **explicitly NOT a
B1+B3 cutover blocker** since `test_args.py` continues to pass
against the unchanged `mcloop.checklist` during the cutover.
Empirical guard already in bob-tools:
`tests/test_mcloop_parity.py`,
`tests/manual/check_duplo_generated_fmt.py`,
`tests/manual/check_cli_end_to_end.py`.

### 2.x Parity summary

| Item | Verdict |
|---|---|
| (a) DFS/scope/leaf-before-parent | MATCH; subsection-order & `@deps` accepted-doc (neutralized by B1) |
| (a/c) BATCH return shape | DIVERGENCE [mcloop-adapt] (shim normalizes) |
| (b) Failed-sibling blocking | MATCH |
| (c) Batch child selection | MATCH |
| (d) USER (compat) | MATCH |
| (d) AUTO/BATCH substring vs leading | DIVERGENCE [accepted-doc] (existing parity test) |
| (d) IDs break `is_user_task` | DIVERGENCE [hard constraint] → atomic B1+B3 |
| (e) Derived parent / commit gating | MATCH |
| (e) AUTO/USER now settle | DIVERGENCE [mcloop-adapt], CONSEQUENTIAL → Decision D1 |
| (e) commit-failure `[!]` | DIVERGENCE [mcloop-adapt] (shim restricts `fail_task` to retry-exhaustion) |
| (e) mutation needs IDs | DIVERGENCE [hard constraint] → atomic B1+B3 |
| (f) `mark_failed` semantics | MATCH |
| (f) `--retry` bulk clear | DIVERGENCE [planfile-API] (add `clear_failed`) |
| (g) atomic/lock | DIVERGENCE [mcloop-adapt], safer (retry wrapper) |
| (g) whole-file canonical rewrite | DIVERGENCE [hard constraint] → one-time B1 |
| (e) ledger phase-id ordinal path | DIVERGENCE [mcloop-adapt], CONSEQUENTIAL → Decision D2 |

---

## 3. Ordered, staged, behavior-preserving integration plan

Each stage is independently verifiable that mcloop runtime behavior is
unchanged (or that the change is exactly the one the stage names and
tests). Stages are strictly ordered. Phase A (planfile parser/ops/IO)
is already complete (§0.1); this plan is Phase B + Phase D cleanup
from `bob/design/planfile.md` §8.

### Stage B0 — Pre-cutover, no mcloop behavior change

B0.1 **DONE in bob-tools commit `f2acceb`**: added
`bob_tools.planfile.clear_failed(plan: Plan) -> Plan`
(bulk FAILED→TODO, mirrors `checklist.clear_failed_markers`
semantics: no event, idempotent). Pure planfile change; covered by
planfile's own tests. Resolves §2(f). Verification on commit
`f2acceb`: `ruff check .` clean; `ruff format --check .` → `39 files
already formatted`; `mypy --strict bob_tools` → `Success: no issues
found in 39 source files`; `pytest -q` → `567 passed, 2 skipped in
1.12s`; `check_cli_end_to_end` exit 0; `check_duplo_generated_fmt`
exit 0; `test_mcloop_parity.py` → `3 passed in 0.76s`. *(Decision:
deterministic API addition — Codex/Claude, not routed.)*
B0.2 **DONE in mcloop commit `117f3ac`**: built the mcloop
scheduler/mutation shim module `mcloop/_planfile_compat.py` but did
not wire it into `run_loop`. It exposes checklist-shaped functions
backed by planfile: `parse`, `find_next`, `check_off`, `mark_failed`,
`clear_failed_markers`, `is_user_task`/`is_auto_task`/`is_batch_task`,
`get_batch_children`, `count_unchecked`, `find_parent`,
`get_eliminated`, `task_label`, `has_unchecked_bugs`,
`user_task_instructions`, `parse_auto_task`, `purge_completed_bugs`.
Shim obligations from §2 are implemented: BATCH return-shape
normalization (2c); `ConcurrentUpdateError` bounded-retry wrapper
(2g; two retries, three total attempts); retry-exhaustion-only
`fail_task` boundary (2e); USER/AUTO/BATCH classified via
`flag_tags`/`action_tag` (2d).
B0.3 **DONE for the B0.2 shim in mcloop commit `117f3ac`**:
`tests/test_planfile_compat.py` adds operation-level parity tests on
copies of `mcloop/PLAN.md` and `mcloop/PLAN.EXAMPLE.md` for
`find_next`, `check_off`, `mark_failed`, `clear_failed_markers`,
classification, `get_batch_children`, `count_unchecked`,
`find_parent`, ID-required mutation, `purge_completed_bugs`, and the
additive-only import proof. Verification on commit `117f3ac`: `ruff
check .` clean; `ruff format --check .` → `99 files already
formatted`; `mypy --config-file pyproject.toml mcloop` → `Success:
no issues found in 45 source files`; `pytest -q` → `1745 passed, 38
skipped in 12.39s`; shim-only tests → `11 passed in 0.83s`. mcloop
runtime unchanged: `rg -n "_planfile_compat" mcloop --glob
'!_planfile_compat.py'` returns no matches.

### Stage B1 — One-time authoritative-PLAN.md canonicalization+migration (atomic with B3; reviewed)

This stage and B3 form **one cutover commit** and must not be split
in time (the §2(d)/§2(e) hard constraints: a migrated file is
unreadable by `checklist`, an unmigrated file is unwritable by
planfile mutation).

B1.1 Run the `bob-plan fmt` composition (`parse_plan; migrate; save`)
on a copy of `/Users/mhcoen/proj/mcloop/PLAN.md`.
B1.2 Produce the unified diff and **route it to the user for
review** — this is the one genuinely consequential, irreversible
artifact (rewrites the human-edited build document: 3→2-space indent,
adds `T-NNNNNN:` IDs and `<!-- phase_id: phase_NNN -->` comments,
normalizes prose blank lines, orders phase-tasks-before-subsections).
*(Per the decision rule: deterministic ordering is ours; the actual
content rewrite of the user's authoritative build document is
consequential and surfaced.)*
B1.3 Verification before cutover: `bob-plan validate` on the
migrated file exits 0; re-parse(strict)→render is a fixed point
(reuse Stage 8 round-trip property); `test_mcloop_parity.py`-style
structural compare between `checklist.parse(original)` and
`parse_plan(migrated)` shows only the accepted additive deltas
(IDs/comments/indent/magic) and the accepted §2(d) substring set —
nothing else.

### Stage B2 — ledger phase-id resolver shim (independent, pre-cutover-safe)

B2.1 **DONE in mcloop commit `7bf086e`**: replaced
`ledger_emit.resolve_phase_id`'s body with a
`planfile.resolve_task_context` shim (design §7.1): maps
`phase_id_source` `explicit_comment`/`explicit_header → "explicit"`,
`ordinal → "ordinal"` only when the existing `ordinal_index` argument
is supplied, and `none → "none"`; carries `plan_phase_count`.
B2.2 **DONE in mcloop commit `7bf086e`**: Decision D2 preserved.
`main._ledger_settle` still calls `resolve_phase_id` without
`ordinal_index` (`main.py:903-906`), so a no-explicit-id plan still
returns `source="none"`, `phase_id=None`, and
`record_phase_id_fallback` does not fire. The existing
`ordinal_index` parameter is the opt-in switch for ordinal attribution;
enabling that path remains deferred to Stage B6.
B2.3 **DONE in mcloop commit `7bf086e`**:
`tests/test_ledger_emit.py` now covers explicit header resolution,
explicit `<!-- phase_id -->` comment resolution, no-explicit-id
collapse to `("none", None)`, explicit ordinal opt-in, exact task-ID
matching (`T-000001` does not match `T-0000010`), and a no-fallback
event-stream replay that emits only the expected `test_failed` event.
Verification on commit `7bf086e`: `tests/test_ledger_emit.py` → `27
passed in 0.99s`; `tests/test_ledger_emit.py
tests/test_integration_slice_d.py tests/test_ledger_pause.py` → `55
passed in 1.03s`; full gate: `ruff check .` clean; `ruff format
--check .` → `99 files already formatted`; `mypy --config-file
pyproject.toml mcloop` → `Success: no issues found in 45 source
files`; `pytest -q` → `1750 passed, 38 skipped in 17.98s`. The
design-§7.2 `T-000001`/`T-0000010` substring hazard is fixed by the
planfile side: `operations._task_matches_label` uses exact `task_id`
match or exact/structurally-delimited text match, never raw substring
matching.

### Stage B3 — Cutover: scheduler + mutation + classification (atomic with B1)

B3.1 Land the B1-migrated PLAN.md and, in the same commit, switch
`main.py` and `output.py` from `mcloop.checklist` to the B0.2 shim
for: `parse`, `find_next`, `check_off`, `mark_failed`,
`is_user_task`/`is_auto_task`/`is_batch_task`, `get_batch_children`,
`count_unchecked`, `find_parent`, `task_label`, `has_unchecked_bugs`,
`user_task_instructions`, `parse_auto_task`. PLAN.md is now the sole
authoritative build document for *scheduling and mutation*;
`CURRENT_PLAN.md`/`plan_split` are **still present and still own
phase windowing** (removed only at B5) — so `run_loop`'s active-file
still reads PLAN.md sections via `plan_split` for now, but the
parse/select/mutate calls go through planfile. (This keeps the
diff minimal and the phase-window change isolated to B5.)
B3.2 Settlement wiring: `complete_task`/`fail_task` Settlements are
mapped to ledger events by the existing `emit_task_lifecycle_events`
constructor (preserving the git-sha gate, §2(e)). Per Decision D1 the
hook **drops `kind=="work_observed"`** (AUTO/USER) and routes only
`commit_landed`/`test_failed` exactly as today; `fail_task` is invoked
only on retry-exhaustion (not commit-failure).
B3.3 Verification (the behavior-preservation gate): a recorded-replay
harness drives `run_loop` over a deterministic stub backend
(`tests/integration/test_stub_run.py`, `test_minimal_run.py`,
`test_subtask_ordering.py`, `test_failing_task.py`,
`test_resume_after_kill.py`) on the B1-migrated PLAN.md and asserts,
against a pre-cutover baseline captured on the *original* PLAN.md:
(i) identical task execution order; (ii) identical final checkbox
states (modulo the canonical-form bytes already accepted at B1, and
the accepted §2(d) classification of prose-mention tag-bearing tasks:
assert every such task in the fixture is DONE and no incomplete task
differs between checklist substring classification and planfile
leading-tag classification); (iii) **single-line mutation diffs** —
each `check_off`/`mark_failed` changes exactly the one task line, no
reflow (proves the §2(g) whole-file-rewrite risk is neutralized
post-B1); (iv) byte-identical emitted ledger event stream. Gate:
full mcloop suite + bob-tools `test_mcloop_parity.py` +
`check_cli_end_to_end.py` + `check_duplo_generated_fmt.py` green.

### B1+B3 pre-flight result (2026-05-17; scratch copy only)

Freeze invariants rechecked on current `/Users/mhcoen/proj/mcloop/PLAN.md`
and `BUGS.md`: `rg -n "@deps" PLAN.md BUGS.md` and
`rg -n "^[[:space:]]*- \[[ !]\].*\[(BATCH|AUTO[^\]]*)\]" PLAN.md BUGS.md`
both return no matches (USER is excluded from the alternation per
the freeze-invariant subsection above — confirmed MATCH per §2(d)).

**B1 migration artifact: regenerate immediately before cutover
review.** The artifact lives under
`/Users/mhcoen/proj/bob-tools/.scratch/mcloop-b1b3-preflight/`
(`/tmp` is forbidden by the workspace scratch policy):
`PLAN.original.md`, `PLAN.migrated.md`, `PLAN.migration.diff`. The
diff SHA-256 is **not** the cutover gate — it churns whenever
`mcloop/PLAN.md` is touched. The gate is the
transformation-shape invariant below, which must hold on the
freshly-regenerated artifact at the moment of the cutover commit.
If any one of these drifts, canonicalization shape itself has
changed and the cutover must not proceed without re-audit:

- Transformation counts: 376 checkbox task IDs added; 10
  `<!-- phase_id: phase_NNN -->` comments added; 0 magic-format
  lines added; 286 checkbox indentation changes (3-space nested
  tasks to 2-space canonical nesting); blank-line normalization
  net −1 line; task order unchanged (no
  phase-tasks-before-subsections reordering on current file).
- `validate_plan(parse_plan(migrated))` exits clean (no
  `PlanValidationError`).
- `render_plan(parse_plan(migrated)) == migrated` (renderer fixed
  point on migrated bytes).
- Structural compare `checklist.parse(original)` vs
  `_planfile_compat.parse(migrated)` reports 376 tasks on both
  sides, no text/status/child-count differences, and only the four
  accepted additive delta classes: IDs, phase_id comments, indent
  normalization, and the three DONE prose-`[BATCH]` lines at
  `PLAN.md:341, :359, :439` (legacy checklist classifies them as
  BATCH by substring; planfile does not; all are `[x]` and not
  scheduler-selectable).

Latest known-good fingerprint (2026-05-17, informational only — do
**not** treat as a gate; regenerate immediately before cutover):
`PLAN.original.md` SHA-256
`56daf92f5e3895048d8d4f970fbcc95cd921363941d48f58c0c632cfd8ca645e`
(matches current `mcloop/PLAN.md`); `PLAN.migrated.md` SHA-256
`6e057520b454c4343a043cc072d7710027395530ea691e3ef9206619867dd0e6`;
`PLAN.migration.diff` SHA-256
`a3ceecf4a05c58c62b0a7c5c434a4c05df06e1efa1e72d484d1bb253e3860fd6`
(840 lines, +387/−378, net +9). All four shape invariants above
re-verified against this artifact on 2026-05-17.

B1 verification: `validate_plan(parse_plan(migrated))` passed;
`render_plan(parse_plan(migrated)) == migrated` was true. Structural
compare `checklist.parse(original)` vs `_planfile_compat.parse(migrated)`
reported 376 tasks on both sides, no text/status/child-count
differences, and 286 accepted indentation-only differences. The only
classification differences were the accepted §2(d) DONE prose
`[BATCH]` mentions at current `PLAN.md:341`, `:359`, and `:439`
(legacy checklist classifies them as BATCH by substring; planfile
does not); all are `[x]`.

B3 behavior-preservation pre-flight at the shim API boundary passed:
representative next-task selection matched; representative nested
task labels matched (`1.2.1`); BATCH child selection for "Add
reviewer module" matched; `check_off` and `mark_failed` each changed
exactly one migrated checkbox line; and a representative ledger
event-stream replay for identical inputs produced byte-identical
`test_failed` payloads.

Recorded-replay harness repair (commit `3cd8165`): the harness is now
hermetic without production/runtime changes. Root cause was test-side:
`runner.run_task` dispatches Claude/default calls through
`code_edit.invoke_code_edit`, whose `_select_backend(project_dir)` may
choose orchestra before the direct `_build_command` seam; the old
stub tests patched only `mcloop.runner._build_command`, so orchestra
could still reach `claude_code_agent:opus`. The repaired harness
forces the direct backend in the tests and patches `_build_command`
there; it also asserts checkbox mutations against `CURRENT_PLAN.md`
when split mode is active, because current `run_loop` mutates
`CURRENT_PLAN.md` rather than master `PLAN.md` until phase transition.
`MCLOOP_INTEGRATION=1 .venv/bin/pytest -q
tests/integration/test_stub_run.py tests/integration/test_minimal_run.py
tests/integration/test_subtask_ordering.py
tests/integration/test_failing_task.py
tests/integration/test_resume_after_kill.py` was run twice after the
repair: `21 passed in 1.97s` and `21 passed in 2.07s`.

Failing-task/resume diagnosis: the previously observed missing
checkbox mutation was not a `_planfile_compat` vs `checklist`
failure-marking or interrupt/resume divergence. It was harness
contamination/staleness: non-hermetic stub tests could reach the real
agent path, and failing/resume tests read master `PLAN.md` even though
split-mode mutation happens in `CURRENT_PLAN.md`. A scratch comparison
of the failing-task pattern over an original checklist plan and a
migrated shim plan selected `Impossible task` on both sides and
produced `[!]` on both sides. A scratch comparison of the resume
pattern selected `Create alpha.txt` first on both sides, produced
`Create alpha.txt=[x]`, `Create beta.txt=[ ]`, `Create gamma.txt=[ ]`
after the first/interrupted step on both sides, and produced all
three `[x]` after restart-style completion on both sides.

Recommendation: **GO for the B1+B3 cutover preconditions covered by
this pre-flight.** The B1 canonicalization artifact is internally
consistent, shim API-boundary parity checks pass, and the named B3
recorded-replay harness is now hermetic and deterministic. The
irreversible cutover itself is still a separate scoped execution step.

### Stage B4 — Interrupt/lifecycle path onto planfile

B4.1 `lifecycle._check_interrupted`/`_write_ruledout_to_plan`/
`_all_tasks` re-pointed to shim/planfile types; `mark_failed` via
shim. `active_paths` still `[BUGS.md, CURRENT_PLAN.md, PLAN.md]` at
this stage (collapsed at B5).
B4.2 Verification: `tests/test_lifecycle.py`,
`tests/integration/test_resume_after_kill.py` green; skip/describe on
an interrupted task marks `[!]` / appends `[RULEDOUT]` on the same
line/indent as before (assert against pre-B4 baseline).

### Stage B5 — Eliminate CURRENT_PLAN.md and the split

B5.1 `run_loop` reads/writes a single authoritative `PLAN.md` for
scheduling **and** phase windowing: replace `ensure_current_plan`/
`transition_phase`/`get_current_phase_name`/`mark_phase_complete`
with planfile-native first-incomplete-phase scheduling
(`next_tasks` already scopes to the first incomplete phase — §2(a)
MATCH — so the explicit `transition_phase` extract/rewrite of
`CURRENT_PLAN.md` becomes unnecessary; phase-boundary full-suite/build
checks and `--stop-after-stage`/`completed_stage` accounting are
rewired to detect "first incomplete phase advanced" from successive
`next_tasks`/`Plan` reads instead of from `CURRENT_PLAN.md`
rewrites). `BUGS.md` remains a separate file (bugs section priority
is `next_tasks`-native via `plan.bugs`; current mcloop keeps bugs in
a standalone `BUGS.md`, so retain `BUGS.md` as a second plan input
parsed by `parse_plan` — bug priority semantics unchanged, §2(a)
MATCH). `--retry` uses `planfile.clear_failed` (B0.1) over PLAN.md
and BUGS.md. Collapse `_interrupt_active_paths` to `[BUGS.md,
PLAN.md]`. Remove the "Do not edit CURRENT_PLAN.md" startup string;
replace with the PLAN.md/BUGS.md wording.
B5.2 Delete `mcloop/plan_split.py` and `tests/test_plan_split.py`.
Remove `plan_split` imports from `main.py`.
B5.3 Verification: a phased fixture (≥2 stages, bugs section,
USER/AUTO/BATCH, RULEDOUT, a failed task) driven end-to-end on the
stub backend reproduces, against the pre-B5 baseline: identical task
order across phase boundaries; identical phase-boundary full-suite/
build invocation points; identical `RunStatus`, `run-summary.json`
`terminal_status`/`stuck`/`completed_stage`; identical bug-only-mode
behavior; identical `--retry`, `--stop-after-stage`,
`--stop-after-one` outcomes. Confirm no remaining `CURRENT_PLAN`
string or `plan_split` import anywhere in `mcloop/`
(grep gate). Full suite green.

### Stage B6 — (Deferred, opt-in, separately tested) adopt the target ledger contract

Not part of the behavior-preserving cutover. Enables Decision D1
(`work_observed` for AUTO/USER) and/or Decision D2 (ordinal phase-id
attribution) deliberately, each behind its own test that asserts the
*new* event stream against an explicitly updated baseline and that
re-runs `ledger_pause`/threshold evaluation to confirm no unintended
reauthor/HardStop. Strictly after B5, only on explicit scheduling.

### Stage D (Phase D cleanup)

D1 Once no `mcloop` module imports `mcloop.checklist`, reduce it to a
≤5-line shim or delete it (design §8 Phase D.1). `maintain.py`'s
`CHECKBOX_RE`-only use is moved to a local constant or a shared
`mcloop` constant first so deletion is unblocked.
D2 Retarget/delete `tests/test_checklist.py`; keep
`tests/integration/test_checklist_integration.py` and
`test_subtask_ordering.py` as planfile scheduler-parity tests
(renamed). `investigate_cmd` (`parse→parse_plan`) handled here with
its own generated-plan-format test (§2(h)); low risk, isolated.
D3 **Re-home `purge_completed_bugs` (behavior-preserving; major
modification, not a tweak).** `checklist.purge_completed_bugs`
(`mcloop/checklist.py:748`, delete loop `:761-768`) is called
once, in `main.run_loop`'s bug-only success path
(`mcloop/main.py:1838`; the only call site under `mcloop/`, apart
from the import at `main.py:35` and tests). It is
`checklist`-coupled: it uses `CHECKBOX_RE` (`checklist.py:9`) to
identify checkbox lines (`checklist.py:763`), so
reducing/deleting `checklist` at D1 forces it to be re-homed
regardless — it is part of the deletion surface. Its current
observable behavior is: every checked-off (`[x]` or `[X]`) line in
`BUGS.md` is **deleted** and the whole file is rewritten
(`checklist.py:763-768`). Two tests directly encode deletion:
`tests/test_checklist.py::test_purge_completed_bugs_basic`
(`:1642`) and `::test_purge_completed_bugs_all_done` (`:1656`).
Three additional purge-behavior tests also need to survive the
re-home: `::test_purge_completed_bugs_no_checked` (`:1668`),
`::test_purge_completed_bugs_keeps_prose` (`:1682`), and
`::test_purge_completed_bugs_with_subtasks` (`:1695`).
  - **Scope (Option A, decided):** the de-split re-homes
    `purge_completed_bugs` onto the deterministic writer with its
    **current delete semantics unchanged**. Post-B5, `BUGS.md` is
    already a `planfile`-parsed second plan input, so the
    behavior-preserving re-home is: `fileio.update(BUGS.md,
    purge_done_bug_tasks)` where the new `planfile` operation filters
    DONE bug tasks and returns a new `Plan`; `save`/render then
    rewrites the file. Current `bob_tools.planfile` has the primitives
    (`TaskStatus.DONE`, `BugsSection`, `render_plan`, `save`,
    `update`) but no existing operation that drops DONE bug tasks, so
    add the small `planfile` op rather than embedding structural
    filter logic in `mcloop`. Atomicity/locking comes from
    `fileio.update`; the reason to put the filter in `planfile` is to
    keep BUGS.md structural mutation in the deterministic API. The §2(g)
    whole-file-canonical-rewrite caveat is *materially milder here*
    than for `PLAN.md`: purge already rewrites the entire `BUGS.md`
    every call (`checklist.py:768`, `p.write_text("\n".join(new_lines)
    + "\n")`), so a canonical
    render on purge is not a new "every commit reflows" problem; a
    one-time `BUGS.md` canonicalization at re-home is sufficient and
    cheap.
  - **Explicitly out of scope:** the delete-vs-retain decision
    (whether resolved bugs are erased, checked-off-in-place, or
    moved to an append-only history). That is a *separate major
    modification* already filed as an mcloop defect (commit
    `5c7c714`, fix direction: move checked-off entries to a
    git-tracked append-only `BUGS-resolved.md`) and is owned by the
    deferred deterministic-bugfile layer (`bob-tools/PLAN.md`
    Stage 9 DEFERRED; `bob/design/BACKLOG.md` 2026-05-16, schema incl.
    opened-at / resolved-at / build-or-commit provenance). The
    de-split must **not** decide it by the back door; it preserves
    delete behavior and updates the five purge tests only to the
    extent the deterministic-writer re-home changes their fixtures or
    module target, not to change semantics.
D4 `commit_landed.attributed_task_id` (design §7.2/§10) is a ledger
schema bump, explicitly **out of scope** and not part of this plan.

---

## 4. Decisions register

- **D1 — AUTO/USER `work_observed` emission.** CONSEQUENTIAL (alters
  ledger stream → threshold eval). Recommended default: **preserve
  current behavior** (drop `work_observed` at the settle hook through
  B5); adopt at opt-in B6. Rationale: behavior-preserving cutover is
  the acceptance bar; the design-§5 target is a deliberate, separable
  change.
- **D2 — Ordinal phase-id attribution via `resolve_task_context`.**
  CONSEQUENTIAL (alters ledger stream + `finding_observed` degraded
  events). Recommended default: shim collapses `ordinal → ("none",
  None)` to reproduce today's `resolve_phase_id(no ordinal_index)`
  emission; adopt at opt-in B6.
- **D3 — `--retry` bulk clear.** Procedural. Add
  `planfile.clear_failed` (B0.1). Not routed.
- **D4 — One-time PLAN.md canonicalization diff (B1.2).**
  CONSEQUENTIAL/irreversible (rewrites the human authoritative build
  document). The diff is routed to the user for review before the B1+B3
  cutover commit. Everything else in B1 is deterministic and not
  routed.
- **D5 — `purge_completed_bugs` re-home (Stage D3).** Option A
  decided: behavior-preserving re-home only; **delete-vs-retain is
  out of scope**, owned by the already-filed mcloop defect `5c7c714`
  and the deferred deterministic-bugfile layer. This is a *major
  modification* (touches `checklist.py`, the `main.py:1838` call
  site, and the purge tests at
  `test_checklist.py:1642/:1656/:1668/:1682/:1695`) and
  must not be conflated with, or sequenced before, the parser swap;
  it lands at Stage D, after the split is gone. The procedural choice
  is not routed: current `bob_tools.planfile` has save/update/render
  primitives but no purge operation, so add a small `planfile` purge
  op and call it through `fileio.update` rather than embedding the
  filter in `mcloop`.
  Changing purge *semantics* would be routed — but it is explicitly
  deferred, so it does not arise in this plan.

All other choices in this plan are deterministic/procedural and are
made by Codex/Claude per the standing decision rule.

---

## 5. Why this ordering is forced (not arbitrary)

The two hard constraints — (i) `migrate()` IDs break
`checklist.is_user_task` (§2(d)); (ii) planfile mutation requires IDs
(§2(e)) — make B1 (migrate file) and B3 (switch consumers) a single
indivisible cutover: no point in time may have `checklist` reading an
ID-bearing file or planfile mutation seeing an ID-less file. B2
(ledger shim) is genuinely independent of file identity and is
sequenced first to shrink the cutover. B5 (delete the split) is
deferred until after B3 proves scheduling/mutation parity, so the
phase-windowing rewrite is isolated from the parser swap. B6 quarantines
the only two *intended* (non-preserving) behavioral changes so the
cutover itself remains strictly behavior-preserving — directly
satisfying the governing risk and the "looks complete, isn't"
failure-mode mandate.
