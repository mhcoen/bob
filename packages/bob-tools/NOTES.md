# Planfile build notes

## Observations

- 2026-05-15 [1.1.2] Task 1.1.1 (`bob_tools/planfile/__init__.py`) is marked
  `[x]` in CURRENT_PLAN.md, but the file does not exist on disk. Verified
  by `git show --stat dd60b01` and `git show --stat 54e117a`: the only
  files touched between the "next: 1.1.1" and "next: 1.1.2" checkpoints
  were CURRENT_PLAN.md, BUGS.md, and orchestra-run logs. The payload at
  `logs/orchestra-runs/4750dfe7db10/payloads/4750dfe7db10__edit__1.json`
  shows the agent returned "I'll wait for your direction before
  starting" and was nonetheless verdict-marked `complete`. Because 1.1.2
  creates sibling module files (not `__init__.py`), this session
  proceeded with the sibling files only; the package currently has no
  `__init__.py` despite the checkbox claiming otherwise. The user
  should decide whether to re-run 1.1.1 or accept a namespace package.
- 2026-05-15 [2.2.1-2.2.6] Same failure mode appears to have recurred at
  task 2.1.1-2.1.5 (heading parsers `_parse_heading`, `_parse_bugs_heading`,
  `_parse_h1`, `_parse_subsection`). The checkpoint commit
  `b608c08` is marked "next: 2.1.1-2.1.5" but no completion commit follows
  and `parser.py` was empty when this session (2.2.1-2.2.6) started. The
  orchestra log at `logs/orchestra-runs/f1af613fa1f2/log.jsonl` shows the
  edit state exited in 9 seconds with `output_chars: 38` and the editor
  said "Ready. What would you like to work on?" — the orchestrator
  still marked the state `complete` and advanced. Heading parsers will
  need to be retroactively implemented before the parser can be wired
  together; flagging for the user so the gap is not papered over.
- 2026-05-15 [2.4.2] `_attach_deps` reads "immediately preceding task line
  at strictly lesser indent" (from the task description) as: walk the
  open-ancestor stack from innermost to outermost and return the first
  task at lesser-or-equal indent — strict on `<`, lenient on `==`. The
  alternative reading — "look only at the literally-immediately-preceding
  task and accept only if its indent is strictly less" — would reject
  the case where @deps sits at indent 0 after a deeper child task in
  source order. Treating that as lenient attachment to the outer task
  matches what hand-written PLAN.md files seem to expect, but the design
  doc grammar only specifies position in the production (`DepsLine?`
  after the parent Item's `NL`); it does not state indent rules. Flagging
  in case the strict reading is preferred. No root-task fallback was
  added (unlike `_attach_ruledout`) because the task description does
  not mention one.
- 2026-05-15 [2.2.1-2.2.6] `_extract_annotations` distinguishes annotations
  from action tags by the mandatory whitespace after the colon: `[feat: x]`
  matches (whitespace after `:`), `[AUTO:run]` does not. This is the
  cleanest separator available given that both share the bracketed
  `key:value` shape and both could be observed as a trailing token. Per
  design doc grammar `Annot ← WS "[" Key ":" WS Value "]"` the post-colon
  WS is required, so this is faithful to spec, not a workaround.
- 2026-05-15 [2.5.2] The state machine in `parse_plan` resolves a handful
  of ambiguities the design doc leaves open; flagging here so 2.5.4
  (syntax-error reporting) and strict mode in Stage 3 can revisit:
  1. Orphan tasks/`@deps`/`[RULEDOUT]` before any phase or Bugs heading
     are silently dropped. The grammar `Plan ← Magic Preamble? PhaseOrBugs+`
     forbids tasks outside a section, but mcloop's parser accepts them
     (with `stage=""`). Strict mode should raise `PlanSyntaxError` here.
  2. A `###` subsection heading appearing inside a Bugs section is
     silently ignored (no scope change). The grammar
     `BugsSection ← "##" WS "Bugs" NL Item*` excludes subsections;
     strict mode could error.
  3. The phase `keyword` field is normalized to title case
     (`"Stage"` / `"Phase"`) regardless of how the heading was
     written. Design doc Q4 ("How are Stage and Phase reconciled?")
     recommends the canonicalizer not rewrite the keyword; if the
     canonical form must round-trip the original case, the parser
     needs to preserve it and a separate `keyword_original_case`
     field (or similar) becomes necessary. Left as title case for now
     because that's what the typed model needs and Q4 only constrains
     the rendered output.
  4. Stack is `clear()`-ed at every phase/Bugs/subsection boundary,
     matching mcloop. Consequence: an indented task immediately after
     a section heading becomes a root of the new section, not a child
     of any task in the previous section. Verified intentional via the
     `test_new_phase_resets_indent_stack` test.

## Hypotheses

## Eliminated

4026da1: Created six empty planfile modules (model.py, parser.py, renderer.py, operations.py, fileio.py, cli.py) with one-line docstrings as specified in the design. Discovered that task 1.1.1 was marked complete but never created the __init__.py file, documented this in NOTES.md. All four check commands (ruff check, ruff format, pytest, mypy) passed cleanly.

91bc7df: Added test infrastructure for the planfile module. Created an empty __init__.py and a conftest.py with a fixtures directory pointer for future test fixtures. All code quality checks (ruff, pytest, mypy) passed successfully.

ee309dc: Added typed dataclasses for PLAN.md parsing model including TaskStatus enum, Task, Phase, Subsection, BugsSection, and Plan classes with frozen immutability. Created comprehensive test suite covering construction, frozen behavior, and exception formatting. All code quality checks (ruff, pytest, mypy) pass. The package currently functions as a namespace package without __init__.py as noted in existing documentation.

3183a1b: Implemented the Stage 2 task-line recognizers and tag extractors in parser.py: the checkbox regex, a raw task-line record, and three extraction functions that strip leading flag tags (USER/BATCH), leading action tags (AUTO:<word>), and trailing key-value annotations from task text. Annotation disambiguation from action tags relies on the mandatory post-colon whitespace specified in the grammar. Added a test file with 251 lines covering each tag family in isolation, in combination, and in edge cases including nested brackets in annotation values and tag-like substrings that must remain prose. NOTES.md records that the heading-parser subtasks (2.1.1-2.1.5) were never executed due to a recurring orchestrator failure mode and will need to be implemented before the parser can be assembled into a full document parser.

286741e: Added support for parsing RULEDOUT lines in the planfile parser. The implementation includes a regex pattern to match lines starting with [RULEDOUT] and a function that returns a structured record with indent, text, and line number. This matches mcloop's existing parse behavior where RULEDOUT lines are sibling lines attached to tasks by indentation. Tests verify proper handling of indented and top-level RULEDOUT lines, empty bodies, trailing whitespace stripping, and that non-leading occurrences are treated as prose.

7b31ee0: Added RULEDOUT line attachment logic to match mcloop's behavior. The new function finds the nearest ancestor task with strictly less indent, falling back to the most recent root task when no such ancestor exists. Includes comprehensive tests covering edge cases like equal indents, empty stacks, and orphaned RULEDOUT lines. All linting, formatting, and type checks pass.

2961b81: Added a regex constant `_DEPS_RE` to the planfile parser to recognize `@deps` lines containing whitespace-separated task IDs, following the design doc grammar. A new log file was created to record the implementation session.

0941fdf: Added `_attach_deps` to the planfile parser, implementing the attachment logic for `@deps` sibling lines. The function walks the open-ancestor stack innermost to outermost, returning the parent task and a boolean indicating whether the attachment is lenient (same-indent) versus strict (lesser-indent); callers are expected to emit a validation warning for the lenient form. No root-task fallback is provided, unlike the existing RULEDOUT attachment. Seven unit tests cover the strict, lenient, innermost-wins, and outdented-walk cases, plus empty-stack and no-match edge cases. NOTES.md records the interpretation chosen for the ambiguous grammar rule, flagging it for review.

e346b0e: Implemented `validate_plan` in `operations.py`, which checks that every task ID listed in any `@deps` line resolves to a real task in the plan. The function walks all task containers (phases, subsections, and the bugs section) recursively, collects known IDs, then reports every missing reference in a single `PlanValidationError` rather than stopping at the first. Tasks without an explicit ID fall back to a source-line reference in the error message. A new test file covers the full range of cases: valid plans, unknown deps at root and nested levels, cross-section references, multi-error aggregation, and compat-mode tasks.

704fc32: Added the public `parse_plan(text, *, strict=False, source_path=None) -> Plan` entry point to the planfile parser, establishing the API contract callers will use in subsequent stages. This stage wires only the signature: `strict` is accepted but unused, and the function returns an empty `Plan` that carries `source_path` through for future error reporting. The state machine that actually walks document text into phases, tasks, and bugs is deferred to the next increment. All four quality checks (ruff, pytest, mypy) pass with no regressions.
