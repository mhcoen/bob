## Resolved Bugs

- [x] T-000001: Live-activity surfacing missing in orchestra-routed agent sessions.
  T-000095 (Stage 1) implemented status-line surfacing for mcloop's
  direct `claude -p` subprocess wrapping, but the orchestra-routed
  `claude_code_agent` code path emits only the minimal ticker
  (`[1/1] editor (claude_code_agent:opus) ... still running, Xs elapsed`)
  with no indication of what the agent is currently doing. The active
  session's stream-json log contains every tool_use and tool_result
  event; the fix is for whichever component prints the 30s ticker to
  tail that log and surface the most recent tool_use as a second line
  beneath the elapsed-time line. Two-line format required: the
  existing elapsed-time line is already at the terminal-width ceiling
  on common laptop setups, so the activity summary must not be
  appended to it. Example:

      [1/1] editor (claude_code_agent:opus) ... still running, 360.0s elapsed
          running: Read /Users/mhcoen/proj/bob/packages/bob-tools/PLAN.md

  Indent the activity line so it visually attaches to the ticker
  without competing with it. Truncate the activity content with an
  ellipsis if it would still exceed terminal width
  (`shutil.get_terminal_size().columns`) — do not wrap. Confirm
  whether the ticker is emitted by mcloop, orchestra's executor, or
  the claude_code_agent adapter, and fix at the lowest layer that
  has the active log path. Cost is trivial: read last few KB of the
  log, find most recent tool_use block, format.

- [x] T-000002: Workspace-root pytest has non-deterministic test-state pollution.
  Running `.venv/bin/pytest` from the workspace root produces 0-270
  failures across runs (order-dependent), with the signature
  `AttributeError: module 'duplo' has no attribute 'X'` (or
  'orchestra', or 'mcloop') inside `_pytest/monkeypatch.py:94
  annotated_getattr` after a `monkeypatch.setattr("duplo.X.Y", ...)`
  call. The failure persists regardless of `--import-mode` (importlib
  vs prepend) and regardless of pytest-xdist (`-n auto` on or off);
  eager `from . import X` in each package's __init__.py reduces but
  does not eliminate it. Per-package pytest is reliable (0-1 failures
  vs. 0-270). The cause is almost certainly a test or fixture
  somewhere that mutates `sys.modules` or rebinds a package's
  submodule attribute without restoring on teardown. mcloop's gate
  has been switched to per-package serial runs via mcloop.json as a
  workaround. Investigation needed: find the polluting test or
  fixture; once fixed, the gate can switch back to workspace-root
  pytest for tighter coupling. Suggested approach: use `pytest
  --collect-only` to enumerate tests, then bisect by running pairs
  to find the smallest set that reproduces the pollution.
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
