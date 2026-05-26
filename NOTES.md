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
- 2026-05-26 [1.2] [T-000002]: Re-applied the backfill after the prior
  session's edits were never committed (the script output from the
  earlier attempt sat in `git stash` and the checkpoint commits only
  toggled PLAN.md's checkbox between `[ ]` and `[!]`). The current run
  re-executes `/tmp/backfill_created_at.py` against the same four
  files and reaches the same fill counts (1384/1384, 0 skipped). All
  files re-parse cleanly through `bob_tools.planfile.parser.parse_plan`
  with `with_created_at == total` per file.
- 2026-05-26 [1.2] [T-000002]: The randomized-order pytest flakiness
  from the prior note is reproduced exactly: run 1 surfaced 31 failures
  in `packages/duplo/tests/test_phase5_integration.py`; run 2 surfaced
  22 errors in `packages/duplo/tests/test_pipeline.py` instead. The
  failing tests pass in isolation (e.g. `test_cross_origin_url_not_fetched`
  green when run alone). The diff between the runs touches only the
  four PLAN.md files plus this NOTES.md, so the flakiness is genuinely
  pre-existing and not introduced by the backfill.

## Hypotheses

## Eliminated
