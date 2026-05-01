# Slice A cleanup pass

Codex's third audit of Slice A (current `slice-a-complete` at
`82cd8d3`) returned zero blockers and explicitly recommended Slice
B greenlight. The cross-cut audit confirmed every Section 1
invariant. This cleanup pass closes the two remaining pieces of
acknowledged spec drift and adds the three plan-listed tests that
were partial or missing across all three audit rounds. None of
these block Slice B; landing them now is hardening before the
foundation gets built on.

Two cleanup commits, then three test commits, then retag
`slice-a-complete` to the final commit. Slice B follows
immediately after.

## CLEANUP 1: Mint `attempt_seq` at `state_enter`, not at submission time

**Location.** `orchestra/executor/executor.py:883-890` (per Codex's
P2 finding from the third audit). `resume_fan_out` derives the
next child attempt from workflow-level `_attempts` that were
pre-seeded during the original fan-out submission. A never-entered
pending child can resume at `attempt_seq 2`. Invocation identity
is still fresh (the invocation_id includes attempt_seq, so a
different number is by definition a different invocation), but the
counter is misleading: the child has been entered exactly once,
yet its attempt number says it has been entered twice.

The original spec's "Invocation identity" subsection says
"`invocation_id = (run_id, state_name, attempt_seq)` minted at
`state_enter` time. The invocation_id is the (run_id, state_name,
attempt_seq) tuple, where attempt_seq is a monotonic counter
incremented on every entry and re-entry of the state within the
run." The current implementation increments at submission, which
diverges.

**Fix.** Mint `attempt_seq` at `state_enter` rather than at
submission. Concretely:

- The fan-out controller submits futures without pre-seeding
  `_attempts[child_name]`.
- The reentrant per-state helper increments `_attempts[child_name]`
  under `_attempt_lock` immediately before writing the
  `state_enter` log record. The increment and the
  `state_enter` write happen in that order; if the helper crashes
  between the increment and the log write, replay will see no
  `state_enter` for this attempt and will re-enter, incrementing
  again. This is correct behavior because `_attempts` is in-memory
  state and is reconstructed by replay from log records, not from
  a persistent counter.
- Replay's `rebuild_from_records` reconstructs `_attempts[child_name]`
  by counting `state_enter` records for each child. After replay,
  `_attempts[child_name]` reflects the number of entries actually
  logged.
- `resume_fan_out` no longer pre-seeds `_attempts` for pending
  children. It passes the (post-replay-reconstruction) counter
  through to the helper, which increments it at the next
  `state_enter`.

**Test.** `test_attempt_seq_minted_at_state_enter`. Fixture: a
fan-out with three children. Crash before any child enters
(simulate by mocking `submit` to raise after the first child
enters but before the second). Resume. Assert: child A has
`attempt_seq=1` (it was entered exactly once on the first run);
child B has `attempt_seq=1` (it was never entered on the first
run, so resume's first entry is its first attempt); child C has
`attempt_seq=1` (same as B). The current implementation would
have B and C at `attempt_seq=2` because of pre-seeding.

This test will fail before the fix and pass after.

## CLEANUP 2: `fan_out_start` written under both locks

**Location.** `orchestra/executor/executor.py:855-876` (per Codex's
P3 finding from the third audit, also flagged in the first audit
round). The current code holds the LogWriter lock while taking the
store snapshot, then releases the store lock BEFORE writing
`fan_out_start`. The original Section 1 wording requires snapshot
capture and `fan_out_start` append in one LogWriter-then-store
critical section.

Codex flagged this twice across audits as deadlock-safe but
plan-divergent. No concrete failure mode has been identified. But
the spec wording is what it is, and closing the drift is mechanical.

**Fix.** Reorder so `fan_out_start` is appended while both locks
are held. The locked sequence becomes:

1. Acquire LogWriter lock.
2. Acquire store lock.
3. Construct snapshot from visible artifact versions.
4. Append `fan_out_start` record and fsync.
5. Release store lock.
6. Release LogWriter lock.

Worker threads that need both locks for any operation MUST follow
the same order (LogWriter then store) to prevent deadlock. Confirm
that no worker code path acquires the store lock first then tries
to acquire the LogWriter lock.

**Test.** `test_lock_order_deadlock_prevention`. Wrap both locks
to record acquisition order. Run a fan-out group. Assert: every
acquisition of both locks goes LogWriter-first, then store. Spawn
a second concurrent code path (a worker thread that needs both
locks for an artifact write) and assert no deadlock occurs under
concurrent pressure. Use a timeout assertion (e.g., the test
fails if it does not complete within 5 seconds) to catch a
deadlock if the lock order is violated.

This is the missing test from Codex's "lock-order deadlock
prevention with direct instrumentation" gap.

## TEST 1: Cancellation-race timing test

**Location.** New test in `tests/test_fan_out_executor.py`. Closes
the partial gating Codex flagged for "the exact 'A errors while B
succeeds nearly simultaneously, replay preserves B in outcome map'
race."

**Fixture.** Two fan-out children, A and B. A's mock adapter
errors immediately. B's mock adapter completes successfully (with
a small synthetic delay or barrier so the timing race is
exercised). Run the fan-out.

**Assertions.**

1. First-pass execution: group routes to error target. `fan_out_end`
   per-child outcome map shows A=error AND B=success (B's
   successful outcome lands before `fan_out_end` is written and
   is recorded in the map).
2. Replay of the resulting log: route to error target identically.
   The replay of an already-completed group with a mix of error
   and success outcomes should hit case 6 (`fan_out_end` present,
   transition accordingly). Verify the log already contains
   `fan_out_end`; replay does not re-run any child.
3. Replay of a TRUNCATED log where `fan_out_end` is missing but
   both child `state_exit` records are present (one error, one
   success): hit replay case 5; route to error; `fan_out_end`
   gets written now with both children in the per-child outcome
   map.

This is the test Codex flagged as not gating the cancellation
race rule's "subsequent successful child outcomes do not change
routing" invariant.

## TEST 2: Crash-mid-retry-then-replay-with-fresh-budget

**Location.** New test in `tests/test_fan_out_executor.py`. Closes
the partial gating Codex flagged for "re-entry retry budget after
crash mid-retry."

**Fixture.** A fan-out with one child that has `on error retry max
2 then stop`. Mock adapter: first attempt raises a transient
error; second attempt blocks on a barrier. Crash the run while
attempt 2 is blocked (simulate by mocking the executor to raise
after attempt 1's `state_exit` is durable but during attempt 2's
execution).

**Assertions.**

1. Pre-crash log contains: `state_enter` (attempt 1), error
   `state_exit` (attempt 1), `state_enter` (attempt 2). No
   `state_exit` for attempt 2.
2. Resume the run. The child's retry counter resets to 0 (per the
   Re-entry retry budget rule). The replay re-enters with a new
   invocation_id and a fresh budget.
3. The mock adapter's third call (which is attempt 1 of the
   re-entered invocation, because the budget is fresh) succeeds.
4. The final `fan_out_end` records the child's success with the
   final invocation_id (whose attempt_seq is 1 of the re-entered
   invocation, NOT 3 of the pre-crash sequence; this confirms
   the fresh budget rule).

This is the test Codex flagged as not gating the fresh-budget
behavior across crash-and-replay.

## TEST 3: Per-child cancellation isolation under concurrent pressure

**Location.** New test in `tests/test_fan_out_executor.py`. Closes
the partial gating Codex flagged for "broader stress-style
concurrency."

**Fixture.** Five fan-out children, A through E. A's mock adapter
errors after a short delay. B, C, D, E's mock adapters block on
individual barriers and then return success. Each adapter records
when its `cancel()` method was called (with which handle) and
when its `invoke()` returned.

**Assertions.**

1. After A errors, the controller calls `request_cancel_all`.
2. B, C, D, E's adapters each receive `cancel(handle)` calls;
   the handles are distinct and match each child's
   `invocation_handle` from the registry.
3. No child receives a `cancel(handle)` call with a different
   child's handle (cross-child handle leakage would indicate a
   registry threading bug).
4. After `cancel`, B, C, D, E's mock adapters complete naturally
   (drain). Each writes a `state_exit` with whatever outcome it
   produced.
5. The group's aggregate outcome is error. The per-child outcome
   map includes A's error and B/C/D/E's drained outcomes
   (whatever they ended up being).
6. The registry lock's discipline is exercised: assert no
   `RuntimeError` from concurrent registry mutation, no
   `KeyError` from cancellation hitting a deregistered entry,
   no `AttributeError` from a stale handle reference.

This is the test Codex flagged as not gating cancellation
isolation under stress.

## Order of work

1. CLEANUP 1 (attempt_seq at state_enter) is medium-sized and
   touches the executor's per-state sequence in the linear and
   fan-out paths. Land first.
2. CLEANUP 2 (`fan_out_start` lock ordering) is small and
   isolated to the snapshot-capture critical section. Land
   second.
3. TEST 1 (cancellation race timing) is a new test; no
   implementation change. Land third.
4. TEST 2 (crash-mid-retry-replay) is a new test plus possibly
   small fixture infrastructure for crash simulation. Land
   fourth.
5. TEST 3 (cancellation isolation under pressure) is a new test
   plus mock adapter infrastructure. Land fifth.

Each commit must keep all 215 existing tests green. CLEANUP 1
adds 1 test (216). CLEANUP 2 adds 1 test (217). TEST 1, 2, 3 add
3 tests (220).

`pytest`, `ruff check .`, `mypy orchestra` strict all pass on
every commit.

When all five commits are pushed, retag `slice-a-complete` to
point at the final commit. Stop and report. The user will then
greenlight Slice B as a separate instruction.
