## Stage 7: CLI

The `bob-plan` console script is the human entry point. Per design
doc section 9: validate, fmt, next, done, fail.

- [x] [BATCH] Implement the bob-plan CLI
   - [x] Update `pyproject.toml`: add a `[project.scripts]` section with `bob-plan = "bob_tools.planfile.cli:main"`.
   - [x] In `cli.py`, implement subcommands with argparse:
   - [x] `bob-plan validate PATH` — parse the file (strict mode when the magic line is present, compat mode otherwise) and call `validate_plan`. Print success or an error with line and column. Exit code 0 on success, 1 on any parse or validation error. This is the standalone validation entry point; other subcommands invoke validation internally before scheduling.
   - [x] `bob-plan next PATH` — call `validate_plan` first; on validation failure print the errors and exit with code 1. Otherwise call `next_tasks` and print the next actionable task as a single line in the form `T-NNNNNN: <text>`. Per design doc section 6 contract: `next_tasks` assumes a validated Plan.
   - [x] `bob-plan fmt PATH` — load, call `migrate`, save. Equivalent to `save(path, migrate(parse_plan(read(path))))`. Per design doc section 3.2 fmt composition.
   - [x] `bob-plan done PATH TASK_ID` — call `validate_plan` first; on validation failure exit code 1. Otherwise call `complete_task` and save. Prints the resulting Settlements as JSON on stdout for the caller to optionally feed to the ledger. The JSON is a list, since the tuple may have more than one entry on derived parent completion.
   - [x] `bob-plan fail PATH TASK_ID --reason TEXT` — call `validate_plan` first; on validation failure exit code 1. Otherwise call `fail_task` and save. Prints the Settlement(s) as JSON.
   - [x] Exit codes: 0 success; 1 invalid plan; 2 task not found; 3 other error.
   - [x] Tests: each subcommand with a fixture file; exit codes; output formats.

- [ ] Write the Stage 7 verification helper script. Create `bob_tools/planfile/tests/manual/check_cli_end_to_end.py`. The script copies `/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md` to `/tmp`, runs `/Users/mhcoen/proj/bob-tools/.venv/bin/bob-plan validate` expecting failure before formatting, then runs `fmt`, `validate` expecting success, and `next`. It asserts exit codes and asserts the diff is additive-only: task IDs, phase-id comments, indentation normalization, and the format magic line. It hardcodes all paths, takes no arguments, exits non-zero on any failure, prints progress to stdout at least every few seconds, and gives every subprocess an explicit short timeout.

- [ ] [AUTO:run_cli] /Users/mhcoen/proj/bob-tools/.venv/bin/python -m bob_tools.planfile.tests.manual.check_cli_end_to_end

- [ ] Verify Stage 7 leaves the repo green.
