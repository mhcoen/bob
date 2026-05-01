# Slice A second-round fix list

Codex re-audit of the seven Slice A fix commits found four new
issues, three P1 blockers and one P2. Slice B does not start until
these are fixed. Land each as its own commit on top of the current
`slice-a-complete` (`3000d6b`). Update Slice A's test suite
alongside each fix so the regressions are gated.

## BLOCKER 1: `resume_fan_out` snapshot leaks completed sibling artifacts

**Location.** `orchestra/executor/executor.py:1008-1024`. The
`resume_fan_out` entrypoint rebuilds its snapshot by reading every
currently visible artifact. After `cli.cmd_resume` calls
`VisibilityIndex.replace_from(replay.visibility_statuses)`,
completed fan-out siblings' invocation_ids are marked `success` in
the index, which makes their committed artifacts visible to
`read_latest` per the visibility rule. The reconstructed snapshot
therefore contains completed siblings' outputs. A pending child
that is re-entered after the crash sees its completed siblings'
artifacts, violating the sibling visibility rule that the snapshot
machinery exists to enforce.

The fan-out invariant is that no child sees any sibling's output,
regardless of whether the sibling completed before or after the
current child. The original `_run_fan_out_group` enforces this by
capturing the snapshot before any child runs; resume must
reconstruct that same pre-fan-out boundary, not the post-completion
visible state.

**Fix.** Filter completed fan-out children's artifacts out of the
reconstructed snapshot. Two viable approaches:

1. **Filter by invocation_id.** The fan-out group knows its
   children's invocation_ids (from `replay.completed_children` and
   `replay.open_fan_out`). When reconstructing the snapshot, walk
   visible artifacts and exclude any whose `producer_invocation_id`
   matches a completed fan-out child of THIS group. Other states
   completed before the fan-out remain visible.

2. **Persist the original snapshot at `fan_out_start`.** Write the
   snapshot's contents into the `fan_out_start` log record (or a
   sidecar file referenced by it) so resume can reload the exact
   pre-fan-out boundary instead of reconstructing it. This is a
   larger change but more robust to future complications (nested
   fan-out, dynamic-N, or any case where reconstruction-from-index
   becomes ambiguous).

Pick approach 1 for this fix. It is smaller, fully solves the
current case, and does not preclude approach 2 later if a stronger
invariant is needed.

**Test.** `test_resume_pending_child_does_not_see_completed_sibling_output`.
Synthesize a log with: parent state `frame` writes `framed_question`;
`fan_out_start` for `[a, b, c]`; child `a` completes with
`a_output` (durable success `state_exit`); child `b` has
`state_enter` but no exit; child `c` has nothing; no `fan_out_end`.
Resume. The fixture's child `b` declares `reads framed_question,
a_output`. Assert: when `b` is re-entered, its prepared invocation
request has `reads["framed_question"]["value"]` populated (frame
ran before the fan-out, so it is visible) AND
`reads["a_output"]["value"] is None` (a is a completed fan-out
sibling, hidden). Also assert: `read_latest("a_output")` returns
None during the resume flow even though the index shows
invocation_id `a-success`.

## BLOCKER 2: Replay re-runs pending children when a completed child has already errored

**Location.** `orchestra/executor/executor.py:1075-1097`. In replay
case 5, if `completed_children` already contains an error outcome,
the group has already failed and the cancellation race rule says
the routing decision is fixed at error. The controller should
immediately route to the error target, run cleanup, and write
`fan_out_end` without launching the pending children. The current
code submits `pending_children` futures unconditionally, then
detects the prior error during the drain loop, then requests
cancellation. New child invocations are created during replay of
an already-failed group, which is the opposite of the contract.

**Fix.** Before submitting any future, scan `completed_children`
for any error outcome. If at least one exists:
- Set `aggregate = "error"` immediately.
- Skip future submission for `pending_children`. Mark them as
  unstarted in the per-child outcome map (e.g.,
  `outcome="not_launched"` or omit them from the map; pick a
  convention and document it in the replay-rules subsection of
  the plan).
- Run the cleanup pass.
- Write `fan_out_end` with the aggregate error outcome and
  pending children flagged as not launched.
- Return the error target.

If `completed_children` has no errors, proceed with the existing
launch-and-drain logic for `pending_children`.

**Test.** `test_resume_open_fan_out_with_errored_completed_child_does_not_launch_pending`.
Synthesize a log with: `fan_out_start` for `[a, b, c]`; child `a`
completes with success; child `b` completes with error; child `c`
has nothing; no `fan_out_end`. Resume. Assert: child `c` does NOT
get a `state_enter` record (no future submitted). `fan_out_end` is
written with `aggregate="error"`, `per_child_outcome` showing `a`
success, `b` error, `c` not_launched (or whatever convention is
chosen). Routing target is the fan-out error target.

## BLOCKER 3: Pending cancellation can be missed between worker check and invoke

**Location.** `orchestra/executor/executor.py:1197-1219`. The
worker checks `cancel_requested` at the top of
`_fan_out_child_worker` before calling `_execute_state_body`. But
the controller's `request_cancel_all` can set `cancel_requested`
WHILE the worker is in `prepare()` or about to enter
`actor_invoke_start`. The `on_prepared` callback marks the entry
`registered` and stores the handle; `_execute_state_body` then
proceeds to write `actor_invoke_start` and call `adapter.invoke()`
without re-checking the flag. Pre-handle cancellation can still
fire the adapter call.

**Fix.** The cancellation check needs to happen at the boundary
between registration and invocation, not just at worker entry.
Specifically:

- Inside `_execute_state_body`, AFTER the `on_prepared` callback
  runs (which transitions the registry entry from `pending` to
  `registered` and stores the handle), AND BEFORE the
  `actor_invoke_start` log write, re-check `cancel_requested` for
  this child. If set, write a cancelled `state_exit` and return
  without invoking. The cancelled-`state_exit` path already exists
  in `_write_cancelled_state_exit`; reuse it or factor a shared
  helper.
- The check must be done while holding the registry lock briefly,
  to avoid a race where the controller is mid-`request_cancel_all`
  and has not yet flipped the flag for this child. Acquire the
  lock, read `cancel_requested`, release the lock. If true, take
  the cancelled path; if false, proceed to invoke.

Note: the controller's `request_cancel_all` for a `registered`
entry calls `adapter.cancel(handle)`. The adapter may or may not
honor the cancel; that is the adapter's contract. The fix here is
about the pending-to-registered transition window, not about
already-running invocations. The B6 fix correctly handles
`registered` entries via `adapter.cancel`. This blocker is about
the small window where the registry says `registered` but the
adapter has not yet been invoked.

**Test.** `test_pending_cancellation_caught_between_register_and_invoke`.
Use a barrier inside the mock adapter's `prepare()` so child A
returns from `prepare()` and is registered, but is not yet inside
`invoke()`. From the controller thread, simulate child B's error
by triggering `request_cancel_all`. Assert: child A's helper
detects `cancel_requested=True` AFTER the registry transition and
writes a cancelled `state_exit` WITHOUT calling `adapter.invoke`.
The mock adapter's `invoke` method records its calls; the test
asserts A's adapter never received an `invoke` call.

## P2: `fan_out_end.child_invocation_ids` records the initial retry's invocation_id

**Location.** `orchestra/executor/executor.py:938-941`. The
controller computes `child_invocation_ids` from `per_child_attempt`,
which captures attempt 1. After B2's retry loop, a child whose
final successful attempt is attempt 2 or 3 has an envelope and
`state_exit` keyed to that final attempt's invocation_id. But
`fan_out_end` records attempt 1's invocation_id. The aggregate log
record therefore lies about which invocation produced the success.
The actual `state_exit` and artifact commits are correctly keyed;
only the aggregate is wrong.

**Fix.** Read the final invocation_id from the returned envelope
or from a value the worker returns alongside the envelope, not
from `per_child_attempt`. Either:

- Have `_fan_out_child_worker` return `(envelope, final_invocation_id)`
  and have the controller use the returned id.
- Or compute the final invocation_id from
  `(run_id, child_name, envelope.attempt)` since `Envelope.attempt`
  reflects the final successful attempt after retry. Verify that
  `Envelope.attempt` is genuinely set to the final attempt by the
  retry loop (B2's commit should have done this; confirm).

Pick whichever is easier in the current code. Both produce the
same correctness result.

**Test.** `test_fan_out_end_records_final_retry_invocation_id`.
Fixture: child has `on error retry max 2 then stop`. Mock adapter
fails twice then succeeds. After the fan-out completes, parse the
`fan_out_end` log record and assert
`child_invocation_ids[child_name]` corresponds to attempt 3 (the
final retry's invocation_id), not attempt 1.

## TEST GAP 1: B1's snapshot test does not prove `read_latest` is unused

**Location.** `tests/test_fan_out_executor.py` for the B1 fix.
The test asserts the prepared invocation request reflects the
snapshot, but does not wrap `read_latest` to verify it was never
called from the worker's read paths. Without the wrap, the
assertion that "no live store consult happens" is never verified
at the call site; the test could pass even if the read path
fell through to `read_latest` in some branch.

**Fix.** Add a `read_latest` call counter or a wrapper that
records every call site. During `slow`'s execution, assert the
counter for `fast_out` and `frame_out` is zero. The original B1
fix-list specified this assertion ("use a wrapper or counter to
detect the call"); the implementer's test omitted it. Land the
test reinforcement as part of the BLOCKER 1 / BLOCKER 2 / BLOCKER
3 commits, in whichever commit is most natural, or as its own
small commit before any of them.

Additionally: the test's barrier occurs after `slow`'s reads are
already resolved. Move the barrier to AFTER `fast` has durable
`state_exit` and BEFORE `slow` begins reading. This ensures the
race the test claims to exercise actually happens.

## TEST GAP 2: B4+B5's resume tests do not include the cross-fix cases

**Location.** `tests/test_fan_out_executor.py` for the B4+B5 fix.
The existing test
`test_resume_open_fan_out_relaunches_only_incomplete_children`
covers the happy path (one completed success, two pending). The
test that would have exposed the BLOCKER 1 leak (pending child
reads completed sibling's artifact) is not in the suite. The test
that would have exposed the BLOCKER 2 launch-after-error case
(completed child errored, pending children still launched) is not
in the suite either.

**Fix.** Both tests are specified above (under BLOCKER 1 and
BLOCKER 2). Land them as part of those blockers' commits.

## Order of work

1. BLOCKER 3 (cancellation between register and invoke) is small
   and isolated. Land first.
2. BLOCKER 2 (replay launch-after-error) is medium. Land second.
3. BLOCKER 1 (resume snapshot leak) is the largest. Land third.
4. P2 (fan_out_end final invocation_id) is small. Land fourth.
5. TEST GAP 1 (read_latest counter) lands inside BLOCKER 1's
   commit since it strengthens the snapshot test that BLOCKER 1's
   fix relies on.

Each commit must keep all 211 existing tests green plus add the
test specified in this message. `pytest`, `ruff check .`, `mypy
orchestra` strict all pass on every commit.

When all four commits are pushed, retag `slice-a-complete` to
point at the final commit. Stop and report. Do NOT begin Slice B.
