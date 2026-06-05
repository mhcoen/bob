<!-- bob-plan-format: 1 -->

## Bugs

- [x] T-000001: In `mcloop/coverage_verify.py` (using an AST check, consistent with `change_class.py`), add detection of a coverage-exempt Python file class: a `.py` file whose classes are all `typing.Protocol` subclasses or ABCs with only abstract/`...`/`pass`/docstring-only method bodies, and/or whose module body is solely imports and re-exports with no executable statements. When a changed `.py` file is in this class, the coverage gate passes it without requiring an executed-line/mapped-test proof and without forcing a waiver. Files containing any executable logic remain gated as today. [fix: "coverage gate exempts Protocol/ABC/re-export-only Python files"] <!-- completed_at: 2026-06-05T04:39:05Z -->
- [x] T-000002: Add tests: a Protocol-only module, an ABC-only module, and an import-and-re-export-only `__init__.py` each pass the gate with no mapped test and no waiver; a module with real executable logic and no exercising test still fails the gate; a module mixing a Protocol with one real executable function is NOT exempt (still gated). [fix: "regression: un-coverable Python exempt, real logic still gated"] <!-- completed_at: 2026-06-05T04:40:30Z -->

### `parse_auto_task` now over-rejects: a bare-path/bare-command run_cli task with no backticks errors instead of running

**Symptom**: building writer, the phase-1 verify task `[AUTO:run_cli] /Users/mhcoen/proj/writer/scripts/verify_phase1.sh` fails with `ERROR: run_cli task has no backtick-delimited command`. The task text is a bare absolute path with no surrounding prose and no backticks. This is a regression from the earlier fix that made `parse_auto_task` extract the backtick-delimited command from prose (which fixed the "Run `<cmd>` to confirm ..." case): that fix made backticks mandatory, so a legitimate bare-path/bare-command AUTO task now errors. Every phase's verify task phrased as a bare path hits this.

**Root cause**: the corrected `parse_auto_task` requires a backtick-delimited span and raises when none is present. But an AUTO run_cli task's args can legitimately be (a) prose containing a backtick-quoted command ("Run `<cmd>` to confirm ..."), or (b) a bare command/path that is itself the whole args with no prose and no backticks. The fix handled (a) but broke (b).

**Chosen fix**: `parse_auto_task` for run_cli should: if the text contains a backtick-delimited command, run exactly that (case a); else if the entire args is a single bare command/path with no surrounding prose (no spaces beyond the command, or it resolves to an existing script path), run it as-is (case b); else (prose with no extractable command) error as it does now. Bare path/command and backtick-quoted command are both valid; only genuinely unextractable prose errors.

### Tasks

- [x] T-000003: Fix `mcloop`'s `parse_auto_task` so a run_cli task whose args is a bare command/path with no backticks and no surrounding prose runs as-is, while still extracting the backtick-delimited command when the args is prose containing one, and still erroring only when the args is prose with no extractable command. [fix: "run_cli accepts bare command as well as backtick-quoted command in prose"] <!-- completed_at: 2026-06-05T04:42:58Z -->
- [ ] T-000004: Add tests covering all three run_cli arg shapes: (1) prose with a backtick-quoted command runs exactly that command; (2) a bare path/command with no backticks runs as-is; (3) prose with no extractable command errors with a clear message. [fix: "regression: run_cli handles bare, backtick-quoted, and unextractable args"]
