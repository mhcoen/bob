# .mcloop/ ignore audit — findings

## Status

Complete (Codex audit 2026-05-24).

## Repos audited

- `/Users/mhcoen/proj/mcloop`
- `/Users/mhcoen/proj/duplo`
- `/Users/mhcoen/proj/orchestra`
- `/Users/mhcoen/proj/bob`

## Per-repo results

### mcloop

- `.gitignore` patterns relevant to runtime state: `.mcloop/`,
  `.mcloop-last-audit`, `logs/`, `.venv/`, `__pycache__/`, `*.pyc`,
  `*.pyo`, `build/`, `*.egg-info/`, `pytest-of-*/`, `.ruff_cache/`,
  `.pytest_cache/`, `.scratch/`, and `*.lock`.
- Tracked `.mcloop/` content: no. `git ls-files .mcloop` returned no
  output.
- Tracked other runtime state matching the broader pattern list: no.
- On-disk `.mcloop/` content observed: `config.json`,
  `maintain-log.json`, `pending/`, `reviews/`, and `runs/`. All are
  ignored.
- Action taken: no action required.

### duplo

- `.gitignore` patterns relevant to runtime state before the fix:
  `.duplo/`, `.mcloop/`, `.mcloop-last-audit`, `logs/`, `.venv/`,
  `__pycache__/`, `*.pyc`, `*.pyo`, `build/`, `*.egg-info/`,
  `.ruff_cache/`, `.pytest_cache/`, `.scratch/`, and `*.lock`. The
  pytest temp pattern was user-specific: `pytest-of-mhcoen/`.
- Tracked `.mcloop/` content: no. `git ls-files .mcloop` returned no
  output.
- Tracked other runtime state matching the broader pattern list: no.
- On-disk `.mcloop/` content observed: `last_error.log`, `pending/`,
  `runs/`, and `wrap/`. All are ignored.
- Action taken: commit `9b51d46` broadened `pytest-of-mhcoen/` to
  `pytest-of-*/`. No tracked runtime-state files needed removal.
- Verification: `.venv/bin/python -m pytest -x` passed with
  3176 passed, 60 skipped.

### orchestra

- `.gitignore` patterns relevant to runtime state before the fix:
  `.duplo/`, `.mcloop/`, `.mcloop-last-audit`, `.orchestra/`,
  `outputs/`, `logs/`, `.venv/`, `__pycache__/`, `*.pyc`, `*.pyo`,
  `build/`, `*.egg-info/`, `.ruff_cache/`, `.pytest_cache/`,
  `.scratch/`, and `*.lock`. The pytest temp pattern was
  user-specific: `pytest-of-mhcoen/`.
- Tracked `.mcloop/` content: no. `git ls-files .mcloop` returned no
  output.
- Tracked other runtime state matching the broader pattern list: no.
- On-disk `.mcloop/` content observed: `logs/`. It is ignored.
- Action taken: commit `63ab08a` broadened `pytest-of-mhcoen/` to
  `pytest-of-*/`. No tracked runtime-state files needed removal.
- Verification: `.venv/bin/python -m pytest -x` passed with
  654 passed, 2 skipped.

### bob

- `.gitignore` patterns relevant to runtime state before the fix:
  `.venv/`, `__pycache__/`, `local/`, `.scratch/`, and `*.lock`.
  Cache directories were ignored only via non-repo/global ignore
  configuration, and McLoop/Duplo/runtime/build patterns were absent.
- Tracked `.mcloop/` content: no. No `.mcloop/` directory exists on
  disk.
- Tracked other runtime state matching the broader pattern list: no.
- On-disk related content observed: `.mypy_cache/`, `.pytest_cache/`,
  `.ruff_cache/`, and `local/`.
- Action taken: commit `a0bb8ab` added local ignores for `*.pyc`,
  `*.pyo`, `.mypy_cache/`, `.pytest_cache/`, `.ruff_cache/`,
  `pytest-of-*/`, `build/`, `*.egg-info/`, `logs/`,
  `.mcloop-last-audit`, `.mcloop/`, and `.duplo/`. No tracked
  runtime-state files needed removal.
- Verification: no local test suite was found (`pyproject.toml`,
  `pytest.ini`, `setup.cfg`, and `tox.ini` absent).

## Summary

| Repo | .mcloop/ state | Other runtime state | Action |
| --- | --- | --- | --- |
| mcloop | Broadly ignored; no tracked content | Related patterns already covered | No action |
| duplo | Broadly ignored; no tracked content | `pytest-of-mhcoen/` was too specific | Commit `9b51d46` |
| orchestra | Broadly ignored; no tracked content | `pytest-of-mhcoen/` was too specific | Commit `63ab08a` |
| bob | No `.mcloop/` on disk; pattern absent before fix | Local cache/runtime/build patterns incomplete | Commit `a0bb8ab` |

## Notes for future passes

- No repo had tracked `.mcloop/` content, so no `git rm -r --cached`
  was needed in this pass.
- Duplo still has a pre-existing unstaged `README.md` change.
- Orchestra still has a pre-existing unstaged `README.md` change and
  untracked design files/figures. This audit did not expand into an
  Orchestra cruft hunt.
- Bob still has pre-existing unstaged README/design work, including
  the repository-consolidation checklist and per-project state-file
  architecture docs. This audit touched only `.gitignore`.
