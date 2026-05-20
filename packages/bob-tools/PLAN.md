# bob_tools.planfile

A deterministic library for reading, writing, and operating on PLAN.md
files. Replaces ad-hoc Markdown parsing in mcloop, duplo, and any other
consumer of PLAN.md. PLAN.md becomes machine-owned structurally while
remaining hand-editable; LLMs propose task text but never own plan
structure.

Authoritative design reference:
`/Users/mhcoen/proj/bob/design/planfile.md`. When this PLAN.md and the
design doc disagree, the design doc wins. Flag the discrepancy for
resolution rather than silently picking one interpretation.

Deferred cross-project design backlog: see `/Users/mhcoen/proj/bob/design/BACKLOG.md`.

Python 3.12+, ruff for linting, pytest for tests, mypy strict for
typing. Sits in `bob_tools/planfile/` as a peer of `bob_tools/ledger/`.
No new top-level dependencies; stdlib only for parser, renderer, and
operations. Each task should leave the repo with ruff check, pytest,
and mypy all passing.

Throughout this PLAN.md, when referring to the operational tag
families defined in the design doc, prose uses unbracketed names
(USER tag, BATCH parent, AUTO action tag) rather than the bracket
form. McLoop's current checklist parser does substring matching on
the bracket form, so writing the bracket literals in task descriptions
would cause mcloop to misclassify those tasks during construction.
The library being built fixes this; the construction plan must work
around it.

## Stage 1: Scaffolding and types
<!-- phase_id: phase_001 -->

- [x] T-000005: Create the `bob_tools/planfile/` package
  - [x] T-000001: Create `bob_tools/planfile/__init__.py` with a package docstring and an explicit `__all__` listing the public exports the library will eventually expose: parse_plan, render_plan, validate_plan, canonicalize, migrate, next_tasks, complete_task, fail_task, reset_task, add_task, replace_phase, resolve_task_context, check_consistency, load, save, update, Plan, Phase, Task, Settlement, TaskContext, RuledOut, TaskStatus, PlanSyntaxError, PlanValidationError, PlanInconsistencyError. Names that don't exist yet can be commented out; they get uncommented as stages add them.
  - [x] T-000002: Create empty modules `model.py`, `parser.py`, `renderer.py`, `operations.py`, `fileio.py`, `cli.py` with one-line docstrings naming what each will own. Source: design doc section 3.1.
  - [x] T-000003: Create `bob_tools/planfile/tests/__init__.py` (empty) and `bob_tools/planfile/tests/conftest.py` with a fixtures directory pointer.
  - [x] T-000004: Update `pyproject.toml`: add `"bob_tools/planfile/tests"` to `[tool.pytest.ini_options].testpaths` so pytest discovers planfile tests alongside ledger tests.

- [x] T-000015: [BATCH] Define core dataclasses in `model.py`
  - [x] T-000006: Define `TaskStatus` as an `enum.Enum` with members `TODO`, `DONE`, `FAILED`. Map the checkbox markers: space character to TODO, lowercase x and uppercase X both to DONE, exclamation mark to FAILED. Per design doc section 2.1.
  - [x] T-000007: Define `RuledOut` dataclass with fields `text: str` and `line_number: int`. Per design doc section 4.2 and section 11 question 3.
  - [x] T-000008: Define `Task` dataclass (frozen) with fields: `task_id: str | None` (None in compat mode pre-migration), `text: str`, `status: TaskStatus`, `flag_tags: tuple[str, ...]` (members are bare names "USER" and "BATCH", no brackets), `action_tag: tuple[str, str] | None` (the pair is action name and args string), `annotations: tuple[tuple[str, str], ...]` (key-value pairs for feat and fix annotations), `deps: tuple[str, ...]` (task IDs this task depends on; empty when none declared), `children: tuple[Task, ...]`, `ruled_out: tuple[RuledOut, ...]`, `indent_level: int`, `line_number: int`.
  - [x] T-000009: Define `Phase` dataclass (frozen) with fields: `phase_id: str | None`, `phase_id_source: str` (one of "explicit_comment", "explicit_header", "ordinal", "none"), `ordinal: int`, `keyword: str` (either "Stage" or "Phase"), `title: str`, `prose: str`, `subsections: tuple[Subsection, ...]`, `tasks: tuple[Task, ...]`, `line_number: int`. Per design doc section 2.5 and section 7.1.
  - [x] T-000010: Define `Subsection` dataclass (frozen) with fields: `title: str`, `prose: str`, `tasks: tuple[Task, ...]`, `line_number: int`. Per design doc section 11 question 5.
  - [x] T-000011: Define `BugsSection` dataclass (frozen) with fields: `tasks: tuple[Task, ...]`, `line_number: int`. Per design doc section 6.
  - [x] T-000012: Define `Plan` dataclass (frozen) with fields: `magic_version: int | None` (from the bob-plan-format comment), `project_title: str`, `preamble: str`, `phases: tuple[Phase, ...]`, `bugs: BugsSection | None`, `source_path: Path | None` (for error messages).
  - [x] T-000013: Define exceptions: `PlanSyntaxError(message, line, column, path)` with a `__str__` matching the format in design doc section 9 ("PLAN.md invalid at line N, column M: ..."), `PlanValidationError(messages: list[str])`, `PlanInconsistencyError(messages: list[str])`.
  - [x] T-000014: Write tests in `bob_tools/planfile/tests/test_model.py` that exercise dataclass construction, frozen behavior (mutation raises), and exception `__str__` formatting.

- [x] T-000016: Verify Stage 1 leaves the repo green: ruff check, pytest, and mypy strict all pass.

## Stage 2: Compat-mode parser
<!-- phase_id: phase_002 -->

The compat-mode parser reads PLAN.md files in the format mcloop's
`checklist.py` accepts today: no stable task IDs, no phase-id
comments, no magic-line. This is what every existing PLAN.md uses.
Strict-mode additions come in Stage 3.

Source of truth for compat-mode acceptance:
`/Users/mhcoen/proj/mcloop/mcloop/checklist.py`. The parser entry
point is `parse` and the structural-sanity check is
`_check_structural_sanity`. Verified citations are in design doc
section 2.1 and section 2.2; refer to those by function name rather
than line number since line numbers drift across edits.

Important policy difference from mcloop, per design doc section 4.3:
operational tags are recognized only in the leading position of a
task line, not anywhere in the task text. This is stricter than
mcloop's substring matching.

- [x] T-000022: [BATCH] Parse stage and phase headings
  - [x] T-000017: In `parser.py`, implement `_parse_heading(line, line_number)` that recognizes the pattern `^#+\s+.*?\b(?:stage|phase)\s+(\d+)\b` (matches mcloop's `STAGE_RE`). Return (ordinal, keyword, title) or None.
  - [x] T-000018: Implement `_parse_bugs_heading(line)` matching `^#+\s+Bugs\s*$` (mcloop's `BUGS_RE`). Return True or False.
  - [x] T-000019: Implement `_parse_h1(line)` matching `^#\s+(.+)$` for the project title.
  - [x] T-000020: Implement `_parse_subsection(line)` matching `^###\s+(.+)$` for sub-grouping headings such as Manual verification headings.
  - [x] T-000021: Tests in `tests/test_parser.py`: each heading type matches; case-insensitive on stage and phase; bare digits required after the stage or phase keyword. A heading like `## Phase phase_001:` does not match this regex — that strict-mode form is handled in Stage 3.

- [x] T-000029: [BATCH] Parse task lines (compat mode, leading-position tag rule)
  - [x] T-000023: Implement `_CHECKBOX_RE = re.compile(r"^(\s*)- \[([ xX!])\] (.+)$")` matching mcloop's `CHECKBOX_RE`.
  - [x] T-000024: Implement `_parse_task_line(line, line_number)` returning a raw record with indent, status_char, text, line_number — or None.
  - [x] T-000025: Implement `_extract_flag_tags(text)` returning a pair of (flag_tags tuple, remaining_text). Flag tags are recognized only at the leading position of the text, immediately after a stable ID if present. Specifically, scan from the start: if the next token is the bracketed form for USER or for BATCH, consume it and continue scanning; stop at the first non-flag-tag token. Flag tags appearing later in the text are prose, not tags. Per design doc section 4.3.
  - [x] T-000026: Implement `_extract_action_tag(text)` returning a pair of (action_tag or None, remaining_text). The action-tag pattern is the bracketed form starting with "AUTO:" followed by a word character sequence. Recognized only at the leading position after any flag tags. Argument string is the text from the closing bracket to end of line. Non-leading occurrences are prose.
  - [x] T-000027: Implement `_extract_annotations(text)` returning a pair of (annotations tuple, remaining_text). Annotations are bracketed key-colon-value patterns at the end of the line. Keys observed today: `feat`, `fix`. Per design doc section 4.3.
  - [x] T-000028: Tests covering each tag family in isolation, in combination, and absent. Edge cases: nested brackets in annotation values; tag-like substrings in task description text are treated as prose, never as tags.

- [x] T-000033: Parse RULEDOUT sibling lines
  - [x] T-000030: Implement `_parse_ruledout_line(line, line_number)` returning a raw RuledOut record. A line is a RULEDOUT line when its stripped form starts with the literal RULEDOUT bracket token. Per mcloop's `parse` function.
  - [x] T-000031: Implement attachment logic: a RULEDOUT line attaches to the nearest task with strictly less indent. If no such task exists in the current phase, attach to the most recent root task (matches mcloop's fallback in `parse`).
  - [x] T-000032: Tests: a RULEDOUT line attaches to a parent task by indent; a top-level RULEDOUT line attaches to the most recent root task; multiple RULEDOUT lines on one task collected in order.

- [x] T-000038: Parse @deps lines
  - [x] T-000034: Implement `_DEPS_RE = re.compile(r"^(\s*)@deps\s+(.+)$")`. The captured tail is whitespace-separated task IDs of the form T-NNNNNN (no trailing colon — bare IDs).
  - [x] T-000035: A `@deps` line attaches to the immediately preceding task line at strictly lesser indent. A `@deps` line at the same indent as its task is also accepted (lenient) and emits a validation warning.
  - [x] T-000036: Validation: every referenced ID must exist in the plan; otherwise raise `PlanValidationError` from `validate_plan` (not at parse time — parse only structures, validate checks references).
  - [x] T-000037: Tests: single-line deps with one or more IDs; deps attached to nested subtasks; missing target ID surfaces in `validate_plan`. Per design doc section 6 and Phase A scope in section 8.

- [x] T-000044: Assemble the parse tree
  - [x] T-000039: Implement `parse_plan(text: str, *, strict: bool = False, source_path: Path | None = None) -> Plan`. The `strict` parameter is wired but defaults to False (compat mode); strict-mode behavior is added in Stage 3.
  - [x] T-000040: State machine: walk lines once, tracking the current phase (or bugs section), the current subsection within a phase, and a stack of open tasks (by indent). Each task line opens or closes scopes by indent comparison, matching mcloop's logic in `parse`.
  - [x] T-000041: Project title: the first H1 heading seen. Preamble: prose between the H1 and the first phase or bugs heading. Phase prose: prose between a phase heading and its first task or subsection. Subsection prose: prose between a sub-heading and its first task.
  - [x] T-000042: On a syntax violation in compat mode, raise `PlanSyntaxError(message, line, column, path)` with a message that quotes the offending line.
  - [x] T-000043: Tests: a hand-crafted minimal valid plan parses correctly; a missing H1 raises; tasks before any phase land in an implicit phase zero (mcloop tolerates this — see `parse` function and PLAN.EXAMPLE.md fixtures in mcloop); a Bugs section after phases is recognized; subsections inside a phase preserve their tasks.

- [x] T-000047: Structural sanity check
  - [x] T-000045: Implement `_check_structural_sanity(parsed_plan)` raising `PlanSyntaxError` on duplicate H1 titles, multiple Bugs sections (any heading level), or duplicate phase/stage ordinals. Per mcloop's `_check_structural_sanity` function; the rationale (no auto-fix) is preserved.
  - [x] T-000046: Tests: each corruption pattern detected with the offending line numbers in the error message.

- [x] T-000050: [BATCH] Malformed-input rejection coverage
  - [x] T-000048: Add a parameterized test class `tests/test_parser_rejections.py` exercising each rejection condition with a minimal failing fixture: duplicate H1, multiple Bugs sections, duplicate phase ordinals, malformed annotations (unclosed bracket, missing colon, empty value), action tag without colon, action tag with empty action name. Per Codex's pile-5 acceptance test gap.
  - [x] T-000049: Each test asserts on the specific error message and the line number where the error was detected.

- [x] T-000051: Write the Stage 2 verification helper script. Create `bob_tools/planfile/tests/manual/check_compat_read.py` (create the `manual` directory). The script imports `parse_plan`, parses each of the three existing PLAN.md files (`/Users/mhcoen/proj/duplo/PLAN.md`, `/Users/mhcoen/proj/mcloop/PLAN.md`, `/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md`), and for each prints one line: `OK <path> phases=<n> bugs=<true|false>`. If parsing raises, it prints `FAIL <path> <exception>` and exits non-zero. The script takes no arguments and hardcodes the three paths. This task makes the USER step a single command instead of a pasted one-liner.

- [x] T-000052: [AUTO:run_cli] /Users/mhcoen/proj/bob-tools/.venv/bin/python -m bob_tools.planfile.tests.manual.check_compat_read

- [x] T-000053: Verify Stage 2 leaves the repo green.

## Stage 3: Strict-mode parser
<!-- phase_id: phase_003 -->

Strict mode requires the format additions in design doc section 4.1
and 4.2: the magic line, stable task IDs, and the phase-id comment.
The parser still accepts compat-mode input when strict is false;
strict mode is opt-in or triggered by the presence of the magic
line.

- [x] T-000059: [BATCH] Recognize the format magic line and phase-id comment
  - [x] T-000054: Implement `_MAGIC_RE = re.compile(r"^<!--\s*bob-plan-format:\s*(\d+)\s*-->\s*$")`. Capture the version integer.
  - [x] T-000055: Implement `_PHASE_ID_COMMENT_RE = re.compile(r"<!--\s*phase_id\s*:\s*([A-Za-z0-9_]+)\s*-->")` matching `mcloop/ledger_emit.py`'s regex of the same name. The two libraries must use identical regexes so they cannot disagree.
  - [x] T-000056: Update `parse_plan` to capture the magic line when it appears as the first non-blank line and store the version in `Plan.magic_version`. Absence is not an error (compat mode); presence with an unrecognized version raises `PlanSyntaxError`.
  - [x] T-000057: Update phase parsing: when a phase-id comment line follows a phase heading before any task, set `Phase.phase_id` to that value and `Phase.phase_id_source` to "explicit_comment".
  - [x] T-000058: Tests: magic line captured; phase-id comment attaches to the immediately preceding phase heading; a phase-id comment not on its own line does not attach to a task's phase (it is a different mechanism — task IDs, not phase IDs).

- [x] T-000063: Recognize the legacy `## Phase phase_NNN: Title` heading form
  - [x] T-000060: Implement `_LEDGER_PHASE_HEADER_RE = re.compile(r"^##\s+Phase\s+(?P<id>[A-Za-z0-9_]+):\s+(?P<title>.+?)\s*$")` matching `mcloop/ledger_emit.py`'s `_PHASE_HEADER_RE`. Identical for the same reason.
  - [x] T-000061: In both strict and compat mode: when a heading matches this regex but not the stage-or-phase ordinal regex (because the id is non-numeric, e.g. phase_001), accept it, set `Phase.phase_id` to the captured id, and `Phase.phase_id_source` to "explicit_header". Per design doc section 7.1.
  - [x] T-000062: Tests: a heading with a non-numeric phase id parses with that id and source "explicit_header"; a heading with a numeric ordinal parses with that ordinal and phase_id None unless a comment follows (then "explicit_comment"); the canonicalizer eventually rewrites explicit_header to explicit_comment, but the parser preserves both forms as input.

- [x] T-000069: Stable task IDs
  - [x] T-000064: Implement `_TASK_ID_RE = re.compile(r"^T-(\d+):\s+(.*)$")`. Apply to the task text after stripping the checkbox but before extracting tags.
  - [x] T-000065: In compat mode: presence of a task ID is recorded on `Task.task_id` but absence is accepted.
  - [x] T-000066: In strict mode: absence of a task ID raises `PlanSyntaxError` with the exact message format from design doc section 9: "expected task id like T-000123 after checkbox marker".
  - [x] T-000067: Tokenization: the library MUST NOT use substring matching to find tasks by ID. Implement `_find_task_by_id(plan, task_id)` that walks the parsed tree. Per design doc section 7.2 caveat about substring matching.
  - [x] T-000068: Tests: a task line with a stable ID parses with that ID; a task line without an ID parses with task_id None in compat mode and raises in strict mode; `_find_task_by_id` distinguishes T-000001 from T-0000010.

- [x] T-000072: Ordinal fallback for unattributed phases
  - [x] T-000070: When neither a phase-id comment nor the legacy header form provides an id, leave `Phase.phase_id` as None and `Phase.phase_id_source` as "none". The Stage 5 `resolve_task_context` function is what maps None to an ordinal fallback at resolve time.
  - [x] T-000071: Tests: a phase with no id source has phase_id None and source "none".

- [x] T-000075: Magic line gates strict mode by default
  - [x] T-000073: When the magic line is present, default `strict` to True even if the caller passed `strict=False`. When absent, default to compat. Explicit caller-supplied `strict=True` overrides.
  - [x] T-000074: Tests: magic present implies strict; magic absent implies compat; explicit strict=True with no magic still strict.

- [x] T-000076: Write the Stage 3 verification helper script. Create `bob_tools/planfile/tests/manual/check_strict_reject.py`. The script imports `parse_plan` and `PlanSyntaxError`, then for each of `/Users/mhcoen/proj/duplo/PLAN.md` and `/Users/mhcoen/proj/mcloop/PLAN.md` calls `parse_plan(text, strict=True)`. Expected outcome is rejection: for each file it prints either `REJECTED <path> at line=<n> col=<m>` (the correct result) or `PARSED <path> (UNEXPECTED - strict mode should have rejected this)` and exits non-zero. The script takes no arguments and hardcodes the two paths.

- [x] T-000077: [AUTO:run_cli] /Users/mhcoen/proj/bob-tools/.venv/bin/python -m bob_tools.planfile.tests.manual.check_strict_reject

- [x] T-000078: Verify Stage 3 leaves the repo green.

## Stage 4: Renderer
<!-- phase_id: phase_004 -->

- [x] T-000090: [BATCH] Implement `render_plan(plan: Plan) -> str`
  - [x] T-000079: Render order: magic line (if present in the input or required by strict output mode), blank line, project H1, blank line, preamble (if any), blank line, each phase in order, then bugs section if present.
  - [x] T-000080: Phase rendering: heading line `## {keyword} {ordinal}: {title}`, then on the next line the phase-id comment if `phase_id_source != "none"`, then blank line, then prose (if any), then blank line, then subsections in order, then tasks in order.
  - [x] T-000081: Canonical phase-id position is always the comment form, even when input used the legacy header form. The renderer is what migrates legacy header to comment per design doc section 7.1.
  - [x] T-000082: Task rendering: `{indent}- [{status_char}] {task_id_prefix}{flag_tag_block}{action_tag_block}{text} {annotations}`. Status char: TODO renders as space, DONE as x, FAILED as exclamation mark. Flag tags ordered by source position; action tag immediately after flag tags. Annotations at end of line, separated by spaces.
  - [x] T-000083: When `task_id` is None (compat-mode plan being rendered without identity migration), omit the task-id prefix. This is the same plan a compat-mode parse produced, rendered back unchanged.
  - [x] T-000084: @deps line rendering: when a task has non-empty `deps`, render `{child_indent}@deps {id} {id} ...` on the line immediately after the task line.
  - [x] T-000085: Subsection rendering: blank line, sub-heading, blank line, prose (if any), blank line, tasks in order.
  - [x] T-000086: Bugs section rendering: blank line, the Bugs H2 heading, blank line, tasks in order.
  - [x] T-000087: Indentation: 2 spaces per nesting level. Canonical, per design doc section 4.2 Notes.
  - [x] T-000088: Trailing newline at end of file. Always exactly one.
  - [x] T-000089: Tests: render output matches a hand-written fixture byte-for-byte for a small Plan; output ends with exactly one newline; indentation always 2 spaces regardless of input indentation.

- [x] T-000095: Round-trip property tests
  - [x] T-000091: Implement two property tests in `tests/test_roundtrip.py`:
  - [x] T-000092: `test_parse_render_parse_idempotent`: for each fixture text, `parse(render(parse(text)))` equals `parse(text)` on the Plan value (ignoring line_number fields which differ between iterations). Fixtures are hand-crafted small plans covering each tag, each heading form, each status, the bugs section, subsections, RULEDOUT lines, and @deps lines.
  - [x] T-000093: `test_render_parse_render_stable`: for each fixture plan, `render(parse(render(plan)))` equals `render(plan)`. This is the canonical-form fixed-point property.
  - [x] T-000094: Fixtures live in `bob_tools/planfile/tests/fixtures/` as markdown files; the test loads them at runtime.

- [x] T-000099: Generative property tests
  - [x] T-000096: Add `tests/test_generative.py`. Implement a small Plan generator using stdlib (no Hypothesis dependency): random small valid trees with random phase counts, random task counts per phase, random tag combinations, random deps among declared IDs. Per Codex's pile-5 acceptance test gap.
  - [x] T-000097: Properties: `parse(render(plan))` equals `plan` modulo line numbers; task IDs in the rendered plan are unique; `next_tasks` returns tasks in the expected canonical order (defer this property to after Stage 5 lands `next_tasks`).
  - [x] T-000098: Run 100 random plans per property by default; bump to 1000 in a slow-mode pytest marker.

- [x] T-000102: Canonicalization function
  - [x] T-000100: Implement `canonicalize(text: str) -> str` as `render_plan(parse_plan(text))`. Lossless formatting only. Does not assign IDs or add phase-id comments — that is the `migrate` operation in Stage 5. Per design doc section 3.2.
  - [x] T-000101: Test: `canonicalize(canonicalize(text))` equals `canonicalize(text)` for every fixture. Test: tasks without IDs in the input have no IDs in the output (canonicalize does not migrate).

- [x] T-000103: Verify Stage 4 leaves the repo green.

## Stage 5: Operations
<!-- phase_id: phase_005 -->

Operations are pure functions on typed Plan objects. Per design doc
sections 3.2 and 5: mutation operations return a tuple of Settlements
so derived parent completion is explicit.

- [x] T-000112: [BATCH] Define the Settlement descriptor and migrate operation
  - [x] T-000104: In `model.py`, define `Settlement` dataclass (frozen) with fields: `kind: Literal["commit_landed", "test_failed", "work_observed", "none"]`, `task_id: str | None`, `phase_id: str | None`, `summary: str`, `failure_kind: str | None`, `ledger_event_required: bool`. Per design doc section 5 target contract.
  - [x] T-000105: Settlement kind policy by source operation:
  - [x] T-000106: Direct success with a commit-producing task settles as `commit_landed` with `ledger_event_required=True`.
  - [x] T-000107: Direct success without a commit (AUTO action tasks and successfully verified USER tasks) settles as `work_observed` with `ledger_event_required=True`. This commits to `work_observed` per Codex's pile-1 confirmation.
  - [x] T-000108: Direct terminal task failure settles as `test_failed` with `ledger_event_required=True`.
  - [x] T-000109: Derived parent completion (all children done, parent auto-checked by `complete_task`) settles as kind `none` with `ledger_event_required=False`. Per design doc section 5.
  - [x] T-000110: In `operations.py`, implement `migrate(plan: Plan) -> Plan`. Returns a new Plan with task_id assigned to every task that had none, and a phase-id comment added for every phase whose `phase_id_source` is "none". ID assignment rule: preserve every existing T-NNNNNN unchanged; scan the plan for the maximum existing numeric ID; assign missing IDs sequentially starting at max+1 (or T-000001 if no existing IDs). This handles partially migrated plans, plans with non-contiguous existing IDs, and plans with no IDs at all. Phase-id assignment uses the same rule on phase_NNN values. Idempotent: a plan that already has IDs and phase-ids is returned unchanged.
  - [x] T-000111: Tests: Settlement construction; the four kind values; `migrate` assigns missing IDs on a fully unmigrated plan; `migrate(migrate(plan))` equals `migrate(plan)`; `migrate` does not change tasks or phases that already have identifiers; partially-migrated input (some tasks have T-000003 and T-000007, others have none) correctly assigns T-000008, T-000009, ... to the unmigrated tasks without touching T-000003 or T-000007; the same rule for non-contiguous phase IDs.

- [x] T-000117: resolve_task_context
  - [x] T-000113: Implement `resolve_task_context(plan: Plan, task_label_or_id: str) -> TaskContext` where TaskContext is a dataclass with fields `task_id: str | None`, `phase_id: str | None`, `phase_id_source: str`, `label: str`, `plan_phase_count: int`.
  - [x] T-000114: Accepts either a stable task ID or a positional label such as "1.3.2" (as mcloop's `task_label` function produces today via `checklist.py`). Tokenizes properly — does not do substring search. Per design doc section 7.2 caveat.
  - [x] T-000115: When the task's containing phase has `phase_id_source` equal to "none", fill in the ordinal-derived id (the n-th phase in document order) and set source to "ordinal". Per design doc section 2.4 and 7.1.
  - [x] T-000116: Tests: lookup by ID; lookup by label; ordinal fallback when no explicit phase_id; raises a clear error for an unknown task.

- [x] T-000124: [BATCH] Implement next_tasks preserving mcloop's find_next semantics
  - [x] T-000118: Implement `next_tasks(plan: Plan, *, limit: int = 1) -> list[Task]` per design doc section 6.
  - [x] T-000119: Priority: tasks in the Bugs section first (absolute), then first-incomplete-phase scope.
  - [x] T-000120: Actionability: status is TODO; every dep listed in the task's @deps is DONE; no failed ancestor; if children, return first actionable child before parent. Per `_search_tasks` in mcloop's checklist.
  - [x] T-000121: Failed sibling blocking: in the depth-first walk, a failed subtask blocks all later siblings under the same parent. Root-level failed tasks are skipped, not blocking. Per `_search_tasks` exactly.
  - [x] T-000122: BATCH parent surfacing: when the next actionable leaf is a child of a BATCH parent, return the parent as a single Task with its actionable children attached (caller iterates). Match the `get_batch_children` semantics in mcloop's checklist: consecutive unchecked children until a USER child or AUTO child stops collection.
  - [x] T-000123: Tests: each priority rule exercised in isolation; failed-sibling blocking; leaf-before-parent; BATCH returns parent unit; later phases invisible until current phase done; bug priority; @deps blocking exercised with at least one test where a task is unblocked only after its dep is completed.

- [x] T-000132: [BATCH] Mutation operations returning tuples of Settlements
  - [x] T-000125: Implement `complete_task(plan, task_id, outcome=None) -> tuple[Plan, tuple[Settlement, ...]]`. Flips status to DONE. The settlement for the direct task uses the kind policy above. If the parent (and grandparent, transitively) becomes complete because all children are now DONE, add a derived `kind="none"` Settlement for each newly-completed ancestor. Order in the returned tuple: direct settlement first, then derived ancestors from innermost outward.
  - [x] T-000126: Implement `fail_task(plan, task_id, reason: str, outcome=None) -> tuple[Plan, tuple[Settlement, ...]]`. Flips status to FAILED. The settlement kind is `test_failed` with the supplied reason and the outcome's failure_kind (default "max_retries_exceeded" if outcome is None). Failing a task does not auto-complete ancestors; the tuple has exactly one Settlement.
  - [x] T-000127: Implement `reset_task(plan, task_id) -> tuple[Plan, tuple[Settlement, ...]]`. Flips FAILED back to TODO (matches mcloop's `clear_failed_markers`). Settlement kind is `none`, `ledger_event_required=False`. Per design doc section 5: reset is an operator decision to retry existing work, not evidence about implementation.
  - [x] T-000128: Implement `add_task(plan, phase_id, text, *, deps=(), parent_id=None) -> Plan`. Appends to the named phase. If `parent_id` is given, nests under it. The new task gets the next sequential globally-unique stable ID. Per design doc section 11 question 1 (global default).
  - [x] T-000129: Implement `replace_phase(plan, phase_id, new_phase) -> Plan`. Wholesale phase replacement, used by Duplo on phase reauthor.
  - [x] T-000130: All operations are pure: input Plan is not mutated; a new Plan is constructed.
  - [x] T-000131: Tests for each operation: status transitions, settlement kinds, derived parent completion produces multiple Settlements in the right order, ID assignment in `add_task`, replacement preserves other phases. Specifically test: completing the last unchecked child of a chain of two BATCH parents returns three Settlements (direct + two derived).

- [x] T-000136: validate_plan and check_consistency
  - [x] T-000133: Implement `validate_plan(plan) -> None` raising `PlanValidationError(messages)` on: unknown bracket tags anywhere, malformed annotations, duplicate task IDs, references in @deps to non-existent task IDs. Per design doc section 4.2 Notes (unknown bracket tags are rejected by validation).
  - [x] T-000134: Implement `check_consistency(plan, events) -> None` raising `PlanInconsistencyError(messages)` per design doc section 5: flag contradictions between checkbox state and the most recent lifecycle event for each task; do NOT flag intentional ledger gaps such as derived parent completion or settlements where ledger_event_required is false.
  - [x] T-000135: Tests for each violation category.

- [x] T-000137: Verify Stage 5 leaves the repo green.

## Stage 6: File I/O
<!-- phase_id: phase_006 -->

- [x] T-000142: [BATCH] Implement load, save, and update
  - [x] T-000138: `load(path: Path) -> Plan`: read file, call `parse_plan(text, source_path=path)`. Errors propagate.
  - [x] T-000139: `save(path: Path, plan: Plan) -> None`: render to text, write atomically (write to a tempfile in the same directory, fsync, rename). Acquire an advisory file lock (`fcntl.flock` with LOCK_EX) for the duration of the write. Release after rename.
  - [x] T-000140: `update(path: Path, operation: Callable[[Plan], Plan]) -> Plan`: load, lock, re-parse to detect concurrent edits, apply operation, save, release lock. Returns the new Plan. This is the safe-mutation entry point for tools that race with humans.
  - [x] T-000141: Tests: atomic write does not leave half-written files on simulated crash (use a tempdir and a side-channel that simulates failure between write and rename); locking serializes two concurrent `update` calls; `update` detects mid-flight external edits and raises.

- [x] T-000143: Verify Stage 6 leaves the repo green.

## Stage 7: CLI
<!-- phase_id: phase_007 -->

The `bob-plan` console script is the human entry point. Per design
doc section 9: validate, fmt, next, done, fail.

- [x] T-000153: [BATCH] Implement the bob-plan CLI
  - [x] T-000144: Update `pyproject.toml`: add a `[project.scripts]` section with `bob-plan = "bob_tools.planfile.cli:main"`.
  - [x] T-000145: In `cli.py`, implement subcommands with argparse:
  - [x] T-000146: `bob-plan validate PATH` — parse the file (strict mode when the magic line is present, compat mode otherwise) and call `validate_plan`. Print success or an error with line and column. Exit code 0 on success, 1 on any parse or validation error. This is the standalone validation entry point; other subcommands invoke validation internally before scheduling.
  - [x] T-000147: `bob-plan next PATH` — call `validate_plan` first; on validation failure print the errors and exit with code 1. Otherwise call `next_tasks` and print the next actionable task as a single line in the form `T-NNNNNN: <text>`. Per design doc section 6 contract: `next_tasks` assumes a validated Plan.
  - [x] T-000148: `bob-plan fmt PATH` — load, call `migrate`, save. Equivalent to `save(path, migrate(parse_plan(read(path))))`. Per design doc section 3.2 fmt composition.
  - [x] T-000149: `bob-plan done PATH TASK_ID` — call `validate_plan` first; on validation failure exit code 1. Otherwise call `complete_task` and save. Prints the resulting Settlements as JSON on stdout for the caller to optionally feed to the ledger. The JSON is a list, since the tuple may have more than one entry on derived parent completion.
  - [x] T-000150: `bob-plan fail PATH TASK_ID --reason TEXT` — call `validate_plan` first; on validation failure exit code 1. Otherwise call `fail_task` and save. Prints the Settlement(s) as JSON.
  - [x] T-000151: Exit codes: 0 success; 1 invalid plan; 2 task not found; 3 other error.
  - [x] T-000152: Tests: each subcommand with a fixture file; exit codes; output formats.

- [x] T-000154: Write the Stage 7 verification helper script. Create `bob_tools/planfile/tests/manual/check_cli_end_to_end.py`. The script copies `/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md` to `/tmp`, runs `/Users/mhcoen/proj/bob-tools/.venv/bin/bob-plan validate` expecting failure before formatting, then runs `fmt`, `validate` expecting success, and `next`. It asserts exit codes and asserts the diff is additive-only: task IDs, phase-id comments, indentation normalization, and the format magic line. It hardcodes all paths, takes no arguments, exits non-zero on any failure, prints progress to stdout at least every few seconds, and gives every subprocess an explicit short timeout.

- [x] T-000155: [AUTO:run_cli] /Users/mhcoen/proj/bob-tools/.venv/bin/python -m bob_tools.planfile.tests.manual.check_cli_end_to_end

- [x] T-000156: Verify Stage 7 leaves the repo green.

## Stage 8: Round-trip and parity validation
<!-- phase_id: phase_008 -->

This stage is the empirical acceptance test for the library. It does
not write new logic — it verifies that fmt produces clean,
semantics-preserving output on every existing PLAN.md and that the
new parser agrees with mcloop on every existing fixture.

- [x] T-000161: Round-trip every existing PLAN.md through fmt
  - [x] T-000157: In `tests/test_existing_plans.py`, add a parameterized test that loads each of `/Users/mhcoen/proj/duplo/PLAN.md`, `/Users/mhcoen/proj/mcloop/PLAN.md`, `/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md`, runs the fmt composition (parse, migrate, render) on each, then re-parses the result in strict mode (since the migrated form has IDs and phase-id comments), then renders again, and asserts the second render equals the first render. This is the fixed-point property on real files.
  - [x] T-000158: The test does NOT modify the source files. It reads them and operates in memory.
  - [x] T-000159: Skip with a clear pytest.skip message if any source file is missing (so the suite is hermetic when running outside the dev environment).
  - [x] T-000160: Tests: each fixture round-trips; any deviation is reported with a unified diff in the assertion message.

- [x] T-000165: Mcloop parity tests
  - [x] T-000162: In `tests/test_mcloop_parity.py`, for each existing PLAN.md fixture, parse it both with `bob_tools.planfile.parse_plan` (compat mode) and with `mcloop.checklist.parse`. Per Codex's pile-5 acceptance test gap.
  - [x] T-000163: Assert structural agreement on: stage and phase ordinals; bugs section presence; task counts per phase; flag-tag presence on each task (USER and BATCH); action-tag presence; RULEDOUT attachments; checkbox status for each task. Cross the two trees by position (since stable IDs are present in one but not the other).
  - [x] T-000164: Document one known divergence: mcloop's substring matcher classifies prose-mention tasks as USER, BATCH, or AUTO tasks (mcloop substring-matches BATCH the same way it does USER, in `is_batch_task`); bob_tools.planfile does not. The parity test allows this specific divergence and asserts nothing else differs.

- [x] T-000166: Write the Stage 8 verification helper script. Create `bob_tools/planfile/tests/manual/check_duplo_generated_fmt.py`. The script globs `/Users/mhcoen/proj/*/.duplo`, picks the first parent directory that also has a `PLAN.md`, copies that plan to `/tmp`, runs `/Users/mhcoen/proj/bob-tools/.venv/bin/bob-plan fmt` on the copy, and diffs source against copy. It asserts only additive changes: task IDs, phase-id comments, indentation normalization, and the format magic line; task structure, tag set, and task order must be unchanged. On semantic divergence it appends a precise entry to `/Users/mhcoen/proj/bob-tools/BUGS.md` and exits non-zero. It hardcodes all paths, takes no arguments, prints progress to stdout at least every few seconds, and gives every subprocess an explicit short timeout.

- [x] T-000167: [AUTO:run_cli] /Users/mhcoen/proj/bob-tools/.venv/bin/python -m bob_tools.planfile.tests.manual.check_duplo_generated_fmt

- [x] T-000168: Final verification: run the full pytest suite with mypy strict and ruff check. All green. Then run `pip install -e /Users/mhcoen/proj/bob-tools` and verify `bob-plan --help` lists all subcommands.

## Stage 9: DEFERRED - deterministic bugfile layer (DO-NOT-EXECUTE)
<!-- phase_id: phase_009 -->

DEFERRED / DO-NOT-EXECUTE: McLoop must skip this section because it contains no checkbox tasks. Deterministic bugfile layer - a parser/renderer/operations/CLI layer for BUGS.md analogous to bob_tools.planfile, with a defined schema that includes temporal and provenance fields (opened-at, resolved-at, and a build/run or commit identifier so a resolved bug is locatable in the build process, not just in wall-clock time). Rationale: BUGS.md has repeatedly been LLM-corrupted (same failure mode that motivated planfile); adding structured temporal/provenance metadata to freeform markdown is the forcing function for giving the bug file the same anti-corruption deterministic-access treatment as the plan file. Sibling to the planfile stages. Not to be started until the planfile work is complete and the user explicitly schedules it.

## Stage 10: Phase C Increment 1 - make_task and semantic field-stability
<!-- phase_id: phase_010 -->

Authoritative spec for Stages 10-23: `/Users/mhcoen/proj/bob-tools/.scratch/phase-c-path1-API-SPEC-v4.md` (FROZEN). When this PLAN.md and the v4 spec disagree, the v4 spec wins; flag the discrepancy rather than silently reinterpreting. Each stage implements exactly one v4 increment with its acceptance gate. No stage may begin the next increment's work. No markdown/structure workaround is permitted anywhere: if the planfile API is insufficient for a task, stop and append a precise blocking entry to BUGS.md rather than hand-writing PLAN.md structure. Verify every claim against source; cite file and line in commit messages.

This stage is already complete (commit f429b21d1087574bbeca89017d0359d424ebc91d, source-verified). It is recorded here so the plan is the full Phase C record.

- [x] T-000169: Implement make_task plus the task-level semantic field-stability harness in bob_tools.planfile per v4 Contract 1: exact signature, the pre-filters, the field-stability harness (render candidate in a minimal canonical plan, parse back, compare fields after the canonical normalizer, raise PlanValidationError naming the failing field), the id-less round-trip sentinel rule (collect explicit ids, allocate non-colliding sentinels, record node-to-sentinel mapping, restore to None by that mapping), and the canonical semantic normalizer (reject nonempty trailing_lines first, then ignore only line_number, indent, source_path, and explicit-header vs explicit-comment equivalence). Export make_task from the package.
- [x] T-000170: Verify Stage 10 gate: every v4 D1 exploit raises with the field named; legitimate non-colliding prose passes and round-trips; id-less and mixed explicit/None sibling cases handled; ruff, ruff format, mypy strict, full pytest all green.

## Stage 11: Phase C Increment 2 - validate_plan constructed extension
<!-- phase_id: phase_011 -->

- [x] T-000171: Extend validate_plan to the signature validate_plan(plan, *, constructed=False) per v4 Contract 4. constructed=False preserves today's task-centric behavior exactly (existing callers unchanged). constructed=True additionally enforces: magic_version == 1; phase ordinals unique and contiguous 1..N; keyword in {Phase, Stage}; every phase has phase_id and phase_id_source != none; every task has a T-NNNNNN id; no duplicate phase ids; no trailing_lines on construction-API tasks; and semantic field-stability over every task plus the non-task scalars defined in the v4 non-task oracle section. Reuse the Stage 10 normalizer and round-trip harness; do not duplicate them.
- [x] T-000172: Verify Stage 11 gate: existing validate_plan tests pass unchanged at the default; new constructed-mode tests cover each added check including a failing fixture per check; ruff, ruff format, mypy strict, full pytest all green.

## Stage 12: Phase C Increment 3 - assert_mcloop_canonical with semantic round-trip
<!-- phase_id: phase_012 -->

- [x] T-000173: Implement assert_mcloop_canonical(plan, *, source_path=None) per v4 Contract 5: run validate_plan constructed=True, render, parse, require SEMANTIC equality of parsed-vs-intended after normalizing only line_number, indent, source_path, trailing_lines (not byte fixed point), then enforce the R1/R2 equivalent without importing mcloop. Return the validated rendered text so the caller persists exactly what was checked. Raise PlanValidationError; let PlanSyntaxError from the re-parse propagate.
- [x] T-000174: Verify Stage 12 gate: a plan that byte-fixed-points but is semantically different (the v3 leak class) is rejected; valid plan returns text; missing ids and R1-shape fixtures rejected; ruff, ruff format, mypy strict, full pytest all green.

## Stage 13: Phase C Increment 4 - add_bug_task
<!-- phase_id: phase_013 -->

- [ ] T-000175: Implement add_bug_task(plan, task, *, dedup_keys=()) -> tuple[Plan, str] per v4 Contract 2: returns one of appended, reopened, unchanged; creates the Bugs section if absent; root bug tasks only; force TODO and assign next global T-NNNNNN on append; dedup keys = explicit keys then fix annotation values then normalized text; TODO match returns unchanged; DONE or FAILED match reopens earliest in place preserving id, children, annotations, deps, ruled_out, position; task must pass Stage 10 field-stability. PlanValidationError only.
- [ ] T-000176: Verify Stage 13 gate: absent section, append, unchanged-TODO, reopen-DONE, reopen-FAILED, fix-key dedup, text-key dedup, id assignment, children preserved, field-stability rejection; ruff, ruff format, mypy strict, full pytest all green.

## Stage 14: Phase C Increment 5 - replace_phase_validated
<!-- phase_id: phase_014 -->

- [ ] T-000177: Implement replace_phase_validated(plan, phase_id, new_phase, *, assign_missing_ids=True, preserve_position=True) per v4 Contract 3: exactly one phase matched and replaced in place; missing phase id gets fresh phase_NNN above all existing suffixes; missing task ids assigned global-sequential; ordinal normalized to the replaced phase ordinal when preserve_position; whole result must pass validate_plan constructed=True including field-stability. Structural validity only; lineage policy stays in duplo. PlanValidationError on no or multi match, dup ids, malformed ids, invalid scalars or deps or tags, non-contiguous ordinals, canonical or round-trip failure, missing ids when assign_missing_ids is False.
- [ ] T-000178: Verify Stage 14 gate: in-place replace, id assignment, ordinal normalization, dup and unknown phase rejection, invalid deps, assert_mcloop_canonical success; ruff, ruff format, mypy strict, full pytest all green.

## Stage 15: Phase C Increment 6 - add_phase_task
<!-- phase_id: phase_015 -->

- [ ] T-000179: Implement add_phase_task(plan, phase_id, task, *, parent_id=None, subsection_title=None) -> tuple[Plan, str] per v4 Contract 6: append a Stage-10 field-stable task into an existing phase at root, under parent_id, or under a named subsection; share make_task validation and global-sequential id assignment; return new plan and the assigned T-NNNNNN; result must pass validate_plan constructed=True. PlanValidationError on unknown phase_id, parent_id, or subsection, invalid task, dup id, unknown deps.
- [ ] T-000180: Verify Stage 15 gate: root, parent, and subsection append; id assignment; plan-and-id return; validation; ruff, ruff format, mypy strict, full pytest all green.

## Stage 16: Phase C Increment 7 - save and update default canonical validation
<!-- phase_id: phase_016 -->

- [ ] T-000181: Change save and update to the signatures save(path, plan, *, validation="canonical") and update(path, operation, *, validation="canonical") where validation is the enum literal canonical or unchecked, per v4 Decision 4. canonical runs assert_mcloop_canonical and writes exactly the validated rendered text. unchecked renders and writes as today. _save_unlocked must not remain a validation bypass: it either takes the same mode or is replaced by a write-only helper fed already-validated text. update's in-lock save must honor the same mode. Update the existing fileio tests: make ordinary save and update fixtures canonical; use validation=unchecked only in tests whose purpose is atomic-write, lock, or crash behavior.
- [ ] T-000182: Verify Stage 16 gate: a non-canonical plan saved with the default raises; unchecked still writes; _save_unlocked has no bypass; update honors the mode; the CI grep gate (rg for validation=unchecked) finds occurrences only in named low-level tests or an approved legacy ledger, each carrying a deprecation reference; ruff, ruff format, mypy strict, full pytest all green.
- [ ] T-000183: [AUTO:run_cli] /Users/mhcoen/proj/bob-tools/.venv/bin/python -c "import subprocess,sys; r=subprocess.run(['rg','-n','validation=.?unchecked','--glob','!*/tests/*','bob_tools'],cwd='/Users/mhcoen/proj/bob-tools',capture_output=True,text=True); sys.exit(0 if r.returncode==1 else (print(r.stdout) or 1))"

## Stage 17: Phase C Increment 8 - mcloop and bob-tools R1/R2 cross-repo parity
<!-- phase_id: phase_017 -->

mcloop delegation is explicitly out of scope for Phase C and recorded as a tracked follow-on; the mandatory parity test below is the Path 1 mitigation for R1/R2 drift.

- [ ] T-000184: Add a required cross-repo parity test: a shared fixture corpus run through bob_tools.planfile assert_mcloop_canonical and through mcloop's real mcloop._planfile_precondition.enforce_canonical, asserting identical accept or reject on every fixture. The test must fail loudly if the two predicates disagree. Place the corpus and test in bob-tools; the test imports mcloop's precondition module by path.
- [ ] T-000185: Verify Stage 17 gate: parity test green across the corpus including canonical-pass, R1-drop, and R2-idless fixtures; ruff, ruff format, mypy strict, full pytest all green.

## Stage 18: Phase C Increment 9 - migrate duplo fresh and initial phase generation, and council
<!-- phase_id: phase_018 -->

Stages 18-22 modify the duplo repo, not bob-tools. Each stage commits and pushes in /Users/mhcoen/proj/duplo with scoped commits. The bob-tools repo must remain green throughout.

- [ ] T-000186: In duplo, replace fresh and initial PLAN.md generation so the model returns structured task data, not PLAN.md markdown. Build a typed bob_tools.planfile Plan, validate with validate_plan constructed=True, persist only via bob_tools.planfile save. Migrate council.author_phase_plan to return structured data and delete council._validate_canonical_plan_markdown (replaced wholesale by constructed validation and assert_mcloop_canonical). Remove the model-instruction prompt text that tells the model to emit PLAN.md markdown. No markdown write_text of plan content may remain on this path.
- [ ] T-000187: Verify Stage 18 gate: a representative duplo fresh-generation run produces a PLAN.md equal to assert_mcloop_canonical output that passes mcloop's real enforce_canonical in an integration test; duplo tests that asserted old markdown behavior rewritten to assert typed behavior, not deleted; duplo ruff and pytest green; bob-tools still green.

## Stage 19: Phase C Increment 10 - migrate duplo bug and fix append
<!-- phase_id: phase_019 -->

- [ ] T-000188: In duplo, replace saver.append_to_bugs_section and the pipeline fix-mode fallback and investigator.investigation_to_fix_tasks with make_task plus add_bug_task. No raw PLAN.md or BUGS.md markdown write of bug content may remain. Tag escaping helpers become unnecessary because tags are typed fields; remove them on this path.
- [ ] T-000189: Verify Stage 19 gate: duplo fix and investigate paths cover append, skip duplicate, reopen DONE and FAILED; no raw plan-markdown write in saver; duplo ruff and pytest green; bob-tools still green.

## Stage 20: Phase C Increment 11 - migrate duplo gap, verification, and contract appends
<!-- phase_id: phase_020 -->

- [ ] T-000190: In duplo, change gap_detector.format_gap_tasks, verification_extractor.format_verification_tasks, and spec_reader.format_contracts_as_verification to return typed Task values via make_task, appended through add_phase_task. The pipeline gap and verification append sites stop writing markdown. No raw checklist markdown returned by these helpers.
- [ ] T-000191: Verify Stage 20 gate: typed tasks only; the three helpers return no markdown; output passes the canonical gate; duplo ruff and pytest green; bob-tools still green.

## Stage 21: Phase C Increment 12 - migrate duplo reauthor
<!-- phase_id: phase_021 -->

- [ ] T-000192: In duplo, migrate the reauthor path: parse and construct via bob_tools.planfile, substitute changed phases with replace_phase_validated, keep lineage and ledger policy in duplo, persist only via bob_tools.planfile save, output must pass assert_mcloop_canonical. Reauthor preserves unchanged phases and validates lineage as before.
- [ ] T-000193: Verify Stage 21 gate: reauthor preserves unchanged phases, substitutes changed, lineage validated, lifecycle events emitted, save only via planfile, canonical helper passes; duplo ruff and pytest green; bob-tools still green.

## Stage 22: Phase C Increment 13 - make plan_document.py callerless
<!-- phase_id: phase_022 -->

- [ ] T-000194: In duplo, remove the last production imports of duplo/plan_document.py by moving remaining callers (reauthor.py, reauthor_assemble.py) onto bob_tools.planfile. Do not delete the module yet. Run the behavior-preservation checks from the v4 ledger and prove every retained guarantee still holds against bob-tools paths.
- [ ] T-000195: Verify Stage 22 gate: rg plan_document across duplo and tests shows no production callers; behavior-preservation tests pass; duplo ruff and pytest green; bob-tools still green.

## Stage 23: Phase C Increment 14 - delete plan_document.py and global no-migrate gate
<!-- phase_id: phase_023 -->

- [ ] T-000196: Delete duplo/plan_document.py. Then run the single all-path end-to-end test: a full duplo run exercising initial generation, gap append, verification, contracts, bug append, and reauthor produces output that passes mcloop's real enforce_canonical with zero bob-plan migrate. Only after this test passes may the global claim duplo to mcloop runs with no migrate step be made.
- [ ] T-000197: Verify Stage 23 gate: plan_document.py deleted, no production imports, no behavior regressions, the all-path end-to-end no-migrate test green, no raw PLAN.md write sites remain in duplo except via bob_tools.planfile; duplo and bob-tools both green and pushed.
