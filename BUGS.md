## Bugs

- [ ] Workspace-root pytest has non-deterministic test-state pollution.
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

- [ ] duplo per-package pytest has rare intermittent failure.
  Running `cd packages/duplo && .venv/bin/pytest` showed 1 failed
  in 1 of 4 sequential runs (3 of 4 were clean 3176/0). The specific
  failing test was masked by output truncation when this was first
  observed; the production gate command does not truncate, so the
  next occurrence will surface the test name in mcloop's failure
  log. Lower priority than the workspace pollution bug — mcloop's
  retry-on-failure handles single transient failures correctly — but
  worth identifying to determine whether it's the same pollution
  pattern leaking into per-package runs, or a distinct flake.

