# NOTES

## Observations

- 2026-05-26 [1.2] [T-000002]: After backfilling `created_at` across the
  four workspace PLAN.md files, `pytest` passes deterministically with
  `-p no:randomly` (6137 passed, 118 skipped). Run with the default
  randomized order it surfaces 30-42 pre-existing failures and 6-117
  errors that vary run-to-run — purely test-isolation flakiness in
  packages/duplo/tests (e.g. `AttributeError: module 'duplo' has no
  attribute 'spec_writer'` from `monkeypatch.setattr` reaching for a
  submodule the test order has not yet imported). The check command as
  listed (`/Users/mhcoen/proj/bob/.venv/bin/pytest`) honors the
  randomized order set by `pyproject.toml`, so the orchestrator's
  verification will hit the same flakiness regardless of this task.
- 2026-05-26 [1.2] [T-000002]: Backfill was implemented as a surgical
  line-rewrite (script in `/tmp/backfill_created_at.py`, run once and
  discarded) rather than a parse + render round-trip, to avoid
  introducing canonical-form normalization changes on PLAN.md files
  that predate strict mode (notably `packages/duplo/PLAN.md`, which is
  compat-mode with no task IDs).
- 2026-05-26 [1.2] [T-000002]: For compat-mode tasks (no `T-NNNNNN`
  prefix) the pickaxe signature is the entire stripped task line; for
  strict-mode tasks the signature is just the `T-NNNNNN` token. Both
  resolved every task: 19/19, 383/383, 197/197, 785/785 across the
  four PLAN.md files. No task fell through to a null `created_at`. The
  earliest dates land in March 2026 (duplo bootstrapping); workspace
  PLAN.md tasks share the recent commit date because the file itself
  was first added today.

## Hypotheses

## Eliminated
