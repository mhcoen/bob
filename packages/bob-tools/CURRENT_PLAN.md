## Stage 1: Scaffolding and types

- [ ] Create the `bob_tools/planfile/` package
   - [ ] Create `bob_tools/planfile/__init__.py` with a package docstring and an explicit `__all__` listing the public exports the library will eventually expose: parse_plan, render_plan, validate_plan, canonicalize, migrate, next_tasks, complete_task, fail_task, reset_task, add_task, replace_phase, resolve_task_context, check_consistency, load, save, update, Plan, Phase, Task, Settlement, TaskContext, RuledOut, TaskStatus, PlanSyntaxError, PlanValidationError, PlanInconsistencyError. Names that don't exist yet can be commented out; they get uncommented as stages add them.
   - [ ] Create empty modules `model.py`, `parser.py`, `renderer.py`, `operations.py`, `fileio.py`, `cli.py` with one-line docstrings naming what each will own. Source: design doc section 3.1.
   - [ ] Create `bob_tools/planfile/tests/__init__.py` (empty) and `bob_tools/planfile/tests/conftest.py` with a fixtures directory pointer.
   - [ ] Update `pyproject.toml`: add `"bob_tools/planfile/tests"` to `[tool.pytest.ini_options].testpaths` so pytest discovers planfile tests alongside ledger tests.

- [ ] [BATCH] Define core dataclasses in `model.py`
   - [ ] Define `TaskStatus` as an `enum.Enum` with members `TODO`, `DONE`, `FAILED`. Map the checkbox markers: space character to TODO, lowercase x and uppercase X both to DONE, exclamation mark to FAILED. Per design doc section 2.1.
   - [ ] Define `RuledOut` dataclass with fields `text: str` and `line_number: int`. Per design doc section 4.2 and section 11 question 3.
   - [ ] Define `Task` dataclass (frozen) with fields: `task_id: str | None` (None in compat mode pre-migration), `text: str`, `status: TaskStatus`, `flag_tags: tuple[str, ...]` (members are bare names "USER" and "BATCH", no brackets), `action_tag: tuple[str, str] | None` (the pair is action name and args string), `annotations: tuple[tuple[str, str], ...]` (key-value pairs for feat and fix annotations), `deps: tuple[str, ...]` (task IDs this task depends on; empty when none declared), `children: tuple[Task, ...]`, `ruled_out: tuple[RuledOut, ...]`, `indent_level: int`, `line_number: int`.
   - [ ] Define `Phase` dataclass (frozen) with fields: `phase_id: str | None`, `phase_id_source: str` (one of "explicit_comment", "explicit_header", "ordinal", "none"), `ordinal: int`, `keyword: str` (either "Stage" or "Phase"), `title: str`, `prose: str`, `subsections: tuple[Subsection, ...]`, `tasks: tuple[Task, ...]`, `line_number: int`. Per design doc section 2.5 and section 7.1.
   - [ ] Define `Subsection` dataclass (frozen) with fields: `title: str`, `prose: str`, `tasks: tuple[Task, ...]`, `line_number: int`. Per design doc section 11 question 5.
   - [ ] Define `BugsSection` dataclass (frozen) with fields: `tasks: tuple[Task, ...]`, `line_number: int`. Per design doc section 6.
   - [ ] Define `Plan` dataclass (frozen) with fields: `magic_version: int | None` (from the bob-plan-format comment), `project_title: str`, `preamble: str`, `phases: tuple[Phase, ...]`, `bugs: BugsSection | None`, `source_path: Path | None` (for error messages).
   - [ ] Define exceptions: `PlanSyntaxError(message, line, column, path)` with a `__str__` matching the format in design doc section 9 ("PLAN.md invalid at line N, column M: ..."), `PlanValidationError(messages: list[str])`, `PlanInconsistencyError(messages: list[str])`.
   - [ ] Write tests in `bob_tools/planfile/tests/test_model.py` that exercise dataclass construction, frozen behavior (mutation raises), and exception `__str__` formatting.

- [ ] Verify Stage 1 leaves the repo green: ruff check, pytest, and mypy strict all pass.
