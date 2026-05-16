## Stage 8: Round-trip and parity validation

This stage is the empirical acceptance test for the library. It does
not write new logic — it verifies that fmt produces clean,
semantics-preserving output on every existing PLAN.md and that the
new parser agrees with mcloop on every existing fixture.

- [x] Round-trip every existing PLAN.md through fmt
   - [x] In `tests/test_existing_plans.py`, add a parameterized test that loads each of `/Users/mhcoen/proj/duplo/PLAN.md`, `/Users/mhcoen/proj/mcloop/PLAN.md`, `/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md`, runs the fmt composition (parse, migrate, render) on each, then re-parses the result in strict mode (since the migrated form has IDs and phase-id comments), then renders again, and asserts the second render equals the first render. This is the fixed-point property on real files.
   - [x] The test does NOT modify the source files. It reads them and operates in memory.
   - [x] Skip with a clear pytest.skip message if any source file is missing (so the suite is hermetic when running outside the dev environment).
   - [x] Tests: each fixture round-trips; any deviation is reported with a unified diff in the assertion message.

- [ ] Mcloop parity tests
   - [x] In `tests/test_mcloop_parity.py`, for each existing PLAN.md fixture, parse it both with `bob_tools.planfile.parse_plan` (compat mode) and with `mcloop.checklist.parse`. Per Codex's pile-5 acceptance test gap.
   - [ ] Assert structural agreement on: stage and phase ordinals; bugs section presence; task counts per phase; flag-tag presence on each task (USER and BATCH); action-tag presence; RULEDOUT attachments; checkbox status for each task. Cross the two trees by position (since stable IDs are present in one but not the other).
   - [ ] Document one known divergence: mcloop's substring matcher classifies prose-mention tasks as USER, BATCH, or AUTO tasks (mcloop substring-matches BATCH the same way it does USER, in `is_batch_task`); bob_tools.planfile does not. The parity test allows this specific divergence and asserts nothing else differs.

- [ ] Write the Stage 8 verification helper script. Create `bob_tools/planfile/tests/manual/check_duplo_generated_fmt.py`. The script globs `/Users/mhcoen/proj/*/.duplo`, picks the first parent directory that also has a `PLAN.md`, copies that plan to `/tmp`, runs `/Users/mhcoen/proj/bob-tools/.venv/bin/bob-plan fmt` on the copy, and diffs source against copy. It asserts only additive changes: task IDs, phase-id comments, indentation normalization, and the format magic line; task structure, tag set, and task order must be unchanged. On semantic divergence it appends a precise entry to `/Users/mhcoen/proj/bob-tools/BUGS.md` and exits non-zero. It hardcodes all paths, takes no arguments, prints progress to stdout at least every few seconds, and gives every subprocess an explicit short timeout.

- [ ] [AUTO:run_cli] /Users/mhcoen/proj/bob-tools/.venv/bin/python -m bob_tools.planfile.tests.manual.check_duplo_generated_fmt

- [ ] Final verification: run the full pytest suite with mypy strict and ruff check. All green. Then run `pip install -e /Users/mhcoen/proj/bob-tools` and verify `bob-plan --help` lists all subcommands.
