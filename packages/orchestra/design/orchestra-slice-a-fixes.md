# Slice A fix list

Codex review of Slice A (tag `slice-a-complete`, commit `5e35d35`)
found six real issues, five of which are blockers. Slice B does NOT
start until these are fixed. Land each as its own commit on top of
`slice-a-complete`. Update Slice A's test suite alongside each fix
so the regressions are gated.

## BLOCKER 1: Fan-out workers ignore the snapshot

**Location.** `orchestra/executor/executor.py` around 920-960.
`_run_fan_out_group` builds `snapshot_envelopes` and
`snapshot_artifacts`, passes them to `_fan_out_child_worker`, which
passes nothing further to `_execute_state_body`. `_execute_state_body`
calls `_resolve_prompt` and `_read_artifacts`, both of which call
`self._store.read_latest(...)` directly. A fan-out child can
therefore observe a sibling's envelope or artifact that landed
mid-fan-out. The plan's sibling-visibility rule is violated.

**Fix.** Thread the snapshot all the way through. `_execute_state_body`
gains an optional `snapshot: FanOutSnapshot | None = None` parameter
(typed dataclass with `envelopes: dict[str, dict[str, Any]]` and
`artifacts: dict[str, Any]`). When `snapshot` is non-None:

- `_read_artifacts` reads from `snapshot.artifacts`, NOT from
  `self._store.read_latest`. For artifacts present in the snapshot,
  return their value with `__version_id="snapshot"`. For
  external_inputs, behavior unchanged. Artifacts NOT in the
  snapshot are NOT visible to the child; reading them returns
  `{"value": None, "__version_id": ""}`, same as today's
  "no version available" path.
- `_resolve_prompt` (specifically the template-substitution branch)
  reads from `snapshot.artifacts` when substituting `{var}`, NOT
  from `self._store.read_latest`.
- `_select_transition_decl` is irrelevant inside `_execute_state_body`
  because the body returns the envelope and the controller picks
  the transition; but the body's other read sites must respect the
  snapshot.

The linear path (`_run_one_state`) calls `_execute_state_body` with
`snapshot=None`, preserving today's behavior. The fan-out worker
calls `_execute_state_body` with `snapshot=<the captured snapshot>`.

**Test.** `test_fan_out_sibling_reads_use_snapshot_not_live_store`.
Fixture: parent state `frame` writes an artifact `frame_out` before
the fan-out. Two fan-out children `fast` and `slow`. `slow`'s state
declares `reads frame_out, fast_out`. `fast` writes `fast_out`
quickly. `slow` sleeps until `fast` has durable `state_exit`, then
runs. Assert: slow's prepared invocation request has
`reads["frame_out"]` populated from the snapshot and
`reads["fast_out"]["value"] is None` (not visible because fast is a
sibling). Also assert: during slow's execution,
`self._store.read_latest` is not called for `fast_out` or
`frame_out` (use a wrapper or counter to detect the call).

## BLOCKER 2: Fan-out children don't retry

**Location.** `orchestra/executor/executor.py:953`. The worker calls
`_execute_state_body` exactly once and returns. The linear path
retries via the transition-selection block in `_run_one_state`.

**Fix.** Child-local retry is honored inside the fan-out child
worker. After `_execute_state_body` returns an error envelope,
evaluate the state's transition declarations for the matched
outcome. If a retry transition matches AND
`retries[child_name] < retry_max`, increment `retries[child_name]`
under `self._attempt_lock`, mint a NEW attempt and a NEW
invocation_id, call `_execute_state_body` again. Repeat until
retry budget exhausted or success. Only then return the final
envelope to the controller.

Note: re-entered children get a fresh retry budget per the plan,
but THIS is the per-entry budget on first execution. The
fresh-budget rule applies after a CRASH and replay re-entry, not
to within-child retries on the same entry.

**Test.** `test_fan_out_child_retry_budget_is_per_entry`. Fixture:
child has `on error retry max 2 then stop`. Mock adapter that
fails twice then succeeds. Assert child's final envelope is
success, the worker called `_execute_state_body` three times
(initial + 2 retries), each invocation has a distinct
invocation_id, and the group's aggregate is success.

## BLOCKER 3: Visibility status set before `state_exit` is durable

**Locations.** `executor.py:391` (`_run_one_state`) and
`executor.py:1203` (`_execute_state_body`). Both call
`self._visibility_index.mark_success/mark_error` before
`self._log.write("state_exit", ...)`. The plan says `state_exit`
durability is the completion point, and visibility must reflect
that.

**Fix.** Reorder. Write `state_exit` FIRST. Only after
`self._log.write("state_exit", ...)` returns (LogWriter has
fsynced), call `mark_success` or `mark_error` on the index. A
crash between the two leaves the index at "pending" plus a
durable `state_exit` on disk; replay's `rebuild_from_records`
reconstructs the correct index from the log.

Apply the same reorder in `_write_cancelled_state_exit`.

**Test.** `test_visibility_not_success_until_state_exit_durable`.
Use a LogWriter wrapper that pauses inside `write("state_exit")`
via a `threading.Event` after the file write but before fsync (or
after fsync; either works for the test, the point is that
`mark_success` has not been called yet). While paused, call
`store.read_latest` from a separate thread and assert the
artifact is NOT visible. Then release the event, allow the write
to complete, allow `mark_success` to run, and re-read; now
visible.

## BLOCKER 4: Resume doesn't dispatch the open fan-out group back to the executor

**Location.** `orchestra/resume/resume.py:97` (`replay_log` records
`open_fan_out`) and `orchestra/cli.py:222` (resume passes only
`current_state` into Executor). Cases 2-5 of the plan's replay
rules need an executor entrypoint that takes "this fan-out group
is mid-flight, here are the children with durable `state_exit`,
here are the children without, here is the visibility-index state
from `rebuild_from_records`, resume the group."

**Fix.**

In Executor: add a new entrypoint
`resume_fan_out(parent_state, transition, completed_children: dict[str, Envelope], pending_children: list[str])`
(signature subject to refinement). Behavior:

- Capture a fresh snapshot using the SAME pre-fan-out boundary
  that the original `fan_out_start` used. Since the snapshot is
  reconstructed from artifacts visible per the visibility rule
  (which already excludes incomplete/errored producers),
  reconstructing it is equivalent to re-reading visible
  artifacts under the locks now.
- Submit futures only for `pending_children`. Each gets a NEW
  invocation_id (new attempt_seq incremented under
  `_attempt_lock`). Re-entered children use the fresh-retry-budget
  rule: their `retries[child_name]` reset to 0.
- Drain via `as_completed`, same as first-pass execution.
- Determine aggregate outcome combining `completed_children`'s
  outcomes and the freshly-completed children's outcomes.
- Run cleanup pass.
- Write `fan_out_end`.
- Return target.

In `resume.py`: `ReplayState` gains the data the executor needs
(already has `open_fan_out`, completed children outcomes,
`visibility_statuses`). Apply `visibility_statuses` to the index
via `VisibilityIndex.replace_from()` BEFORE handing control to
the executor (this also closes blocker 5).

In `cli.py`: when `ReplayState.open_fan_out` is set, dispatch to
`executor.resume_fan_out` instead of stepping the linear loop.
After `resume_fan_out` returns, the executor's normal step loop
takes over.

**Test.** `test_resume_open_fan_out_relaunches_only_incomplete_children`.
Synthesize a log with: state_enter+state_exit for parent `frame`;
`fan_out_start` for `[a, b, c]`; state_enter+state_exit for child
`a` with success; state_enter for child `b` with no exit;
nothing for child `c`; no `fan_out_end`. Run resume. Assert:
child `a` is NOT re-run (its existing committed artifacts remain
visible). Children `b` and `c` both get fresh invocation_ids and
run to completion. `fan_out_end` is written. Aggregate is
success (assuming b and c succeed). Routing is the join target.

## BLOCKER 5: Rebuilt visibility state isn't applied on resume

**Location.** `replay_log` rebuilds `visibility_statuses`
(`resume.py:92`), but resume never calls
`VisibilityIndex.replace_from()`. The persisted `visibility.json`
wins by default via `_load()`. The plan says the log is the
source of truth.

**Fix.** In `cli.py`'s resume path, after `replay_log` returns,
call `self._visibility_index.replace_from(replay_state.visibility_statuses)`
BEFORE constructing the Executor. The persisted `visibility.json`
is a best-effort cache only and must be overwritten by replay's
reconstruction.

**Test.** `test_resume_visibility_log_wins_over_persisted_json`.
Synthesize a run where `visibility.json` has stale entries
(e.g., success for an `invocation_id` that the log records as
error). Resume. Assert the index after resume reflects the log's
view, not the json's.

## BLOCKER 6: Cancellation registry has no `invocation_handle` field

**Location.** `executor.py:1381` (`_ChildEntry` has only
`cancel_requested`, `state`, `invocation_id`; no
`invocation_handle`). `executor.py:1420` (`mark_started` doesn't
accept a handle). `executor.py:1435`
(`request_cancel_all_pending` only flips `cancel_requested` for
pending entries; never calls `adapter.cancel` for registered
entries).

**Fix.**

`_ChildEntry` gains `invocation_handle: Any = None`. `mark_started`
signature becomes
`mark_started(child_name, invocation_id, invocation_handle)`. The
fan-out worker, when it begins the adapter call, calls
`registry.mark_started(child_name, invocation_id, prepared)` (or
whatever handle the adapter expects to receive in `cancel()`).

`request_cancel_all_pending` becomes `request_cancel_all`. For
each entry:

- If `state == "pending"`: `cancel_requested = True`;
  `future.cancel()` if pending future exists.
- If `state == "registered"`: call
  `adapter.cancel(invocation_handle)` on the appropriate adapter.
  Do NOT change state; the worker drains naturally.
- If `state == "done"`: no-op.

Slice A's adapter implementations are non-cooperative for
in-flight cancellation, which is acceptable. The fix here is that
the registry now CALLS `adapter.cancel`; whether the adapter
actually cooperates is the adapter's contract, separately
documented.

**Test.** `test_cancellation_registered_child_calls_adapter_cancel`.
Two fan-out children A and B. A errors immediately. B is in
`adapter.invoke` (use a barrier). Mock adapter records a list of
`cancel()` calls. Assert: A errors -> `request_cancel_all` is
called -> B's adapter receives `cancel(B's invocation_handle)`.
B's worker completes naturally (drains) and writes a `state_exit`;
the test asserts the `state_exit` is durable. `fan_out_end`
records B's outcome (whatever it ended up being) in the per-child
outcome map.

## FOLLOW-UP 1: `_discard_stale_tentatives` deletes FK parent before child

**Location.** `executor.py:708`. The code does
`DELETE FROM versions WHERE seq = ?` then
`DELETE FROM tentative_handles WHERE seq = ?`. `tentative_handles.seq`
references `versions.seq`. Under `PRAGMA foreign_keys=ON`, this
fails. Reverse the order: delete `tentative_handles` first, then
`versions`. The store's `discard_tentative` does it the right way
(`store.py:595`); use that as the reference.

**Test.** `test_discard_stale_tentatives_respects_fk`. Enable
`foreign_keys=ON` pragma. Create a tentative write. Restart the
run (simulate by opening a new Executor with the same store).
Assert `_discard_stale_tentatives` runs without IntegrityError.

## FOLLOW-UP 2: `tentative_write` is multi-statement but not in `BEGIN IMMEDIATE`

**Location.** `store.py:200` (`isolation_level=None`) and
`store.py:502` (`tentative_write`). The two inserts in
`tentative_write` are not wrapped in a single SQLite transaction.
Under concurrent workers, one write's failure could leave the
other half committed.

**Fix.** Wrap `tentative_write`'s inserts in
`BEGIN IMMEDIATE` / commit / rollback under `self.lock`,
mirroring the discipline already in `commit_tentative`,
`discard_tentative`, `purge`, and `_discard_stale_tentatives`.

**Test.** `test_tentative_write_atomic_under_concurrent_pressure`.
Two threads call `tentative_write` concurrently for different
artifact names. Assert each completes atomically; corrupting the
second insert (mock the second `cursor.execute` to raise) leaves
no partial row from that thread visible.

## FOLLOW-UP 3 (informational, do NOT fix): `fan_out_start` write outside store lock

Codex flagged that the snapshot capture and `fan_out_start` append
are NOT in a single critical section: the code releases the store
lock before writing `fan_out_start` (still under the LogWriter
lock). The plan's invariant e/k says they should be in one
section. Codex marks this as deadlock-safe but plan-divergent; it
does not block correctness because no other code path can mutate
the store between snapshot capture and `fan_out_start` while the
LogWriter lock is held (the LogWriter lock guards all log writes;
`fan_out_start` cannot be observed by another thread until it is
written). Decide later. For now, leave it.

## Order of work

1. Blocker 3 (visibility-after-state_exit reorder) is small and
   isolated. Land first.
2. Blocker 1 (snapshot threading) is the single largest change.
   Land second.
3. Blocker 2 (child retry) sits on top of blocker 1's snapshot.
   Land third.
4. Blocker 6 (cancellation handle) is independent of the others.
   Land fourth.
5. Blockers 4 and 5 (resume) are paired; land them together as a
   fifth commit.
6. Follow-up 1 (FK ordering) is a one-line fix. Land sixth.
7. Follow-up 2 (`BEGIN IMMEDIATE` in `tentative_write`) is a small
   wrap. Land seventh.

Each commit must keep all 203 existing tests green plus add the
test specified in this message. `pytest`, `ruff check .`, `mypy orchestra`
strict all pass on every commit.

When all seven commits are pushed, retag `slice-a-complete` to
point at the final commit. Stop and report. Do NOT begin Slice B.
