<!-- bob-plan-format: 1 -->

## Bugs

- [x] T-000001: mcloop task verdict misses work landed in earlier attempts during session-limit cycles.

  mcloop's task-level verdict ("session produced no file changes and no
  task-specific acceptance evidence") inspects only the final attempt's
  diff, not the cumulative committed state across all attempts.

  When a task runs multiple attempts — common during rate-limit /
  session-limit cycles — the pattern is: attempt 1 produces real work
  and mcloop's checkpoint hook commits it; ~N session-limit retry stubs
  execute while the session is capped (each producing zero changes);
  eventually a fresh attempt resumes, correctly diagnoses "work
  already done," and exits without producing diffs. mcloop's verdict
  then flags the task as having produced no file changes, even though
  the task's full execution window did produce committed work that
  passes tests.

  Observed twice on 2026-05-26/27:
  - T-000004 (bob_tools.planfile.resolve_global): attempt 1
    committed; later attempts saw work present; verdict was wrong.
  - T-000017 (duplo.design.run_iterative_design): attempt 1
    committed (commit d3738626); 17 session-limit retry stubs
    between attempts; final attempt at 01:57 explicitly said
    "design.py already exists with the implementation ... task is
    complete; nothing further to change" — verdict still failed.

  Suggested fix: mcloop should consider a task successful if EITHER
  (a) the final attempt produced passing file changes, OR (b) the
  git log entries created during the task's full execution window
  contain commits whose cumulative changes pass the gate. Option (b)
  handles the rate-limit-then-verify pattern correctly.

  Until fixed, the workaround is manual: after each mcloop run, audit
  any task marked [!] by checking git log for unpushed commits in the
  task's time window; if present and gate-green, push and flip
  [!] -> [x] by hand.
