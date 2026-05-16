## Stage 2: Compat-mode parser

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

- [x] [BATCH] Parse stage and phase headings
   - [x] In `parser.py`, implement `_parse_heading(line, line_number)` that recognizes the pattern `^#+\s+.*?\b(?:stage|phase)\s+(\d+)\b` (matches mcloop's `STAGE_RE`). Return (ordinal, keyword, title) or None.
   - [x] Implement `_parse_bugs_heading(line)` matching `^#+\s+Bugs\s*$` (mcloop's `BUGS_RE`). Return True or False.
   - [x] Implement `_parse_h1(line)` matching `^#\s+(.+)$` for the project title.
   - [x] Implement `_parse_subsection(line)` matching `^###\s+(.+)$` for sub-grouping headings such as Manual verification headings.
   - [x] Tests in `tests/test_parser.py`: each heading type matches; case-insensitive on stage and phase; bare digits required after the stage or phase keyword. A heading like `## Phase phase_001:` does not match this regex — that strict-mode form is handled in Stage 3.

- [x] [BATCH] Parse task lines (compat mode, leading-position tag rule)
   - [x] Implement `_CHECKBOX_RE = re.compile(r"^(\s*)- \[([ xX!])\] (.+)$")` matching mcloop's `CHECKBOX_RE`.
   - [x] Implement `_parse_task_line(line, line_number)` returning a raw record with indent, status_char, text, line_number — or None.
   - [x] Implement `_extract_flag_tags(text)` returning a pair of (flag_tags tuple, remaining_text). Flag tags are recognized only at the leading position of the text, immediately after a stable ID if present. Specifically, scan from the start: if the next token is the bracketed form for USER or for BATCH, consume it and continue scanning; stop at the first non-flag-tag token. Flag tags appearing later in the text are prose, not tags. Per design doc section 4.3.
   - [x] Implement `_extract_action_tag(text)` returning a pair of (action_tag or None, remaining_text). The action-tag pattern is the bracketed form starting with "AUTO:" followed by a word character sequence. Recognized only at the leading position after any flag tags. Argument string is the text from the closing bracket to end of line. Non-leading occurrences are prose.
   - [x] Implement `_extract_annotations(text)` returning a pair of (annotations tuple, remaining_text). Annotations are bracketed key-colon-value patterns at the end of the line. Keys observed today: `feat`, `fix`. Per design doc section 4.3.
   - [x] Tests covering each tag family in isolation, in combination, and absent. Edge cases: nested brackets in annotation values; tag-like substrings in task description text are treated as prose, never as tags.

- [x] Parse RULEDOUT sibling lines
   - [x] Implement `_parse_ruledout_line(line, line_number)` returning a raw RuledOut record. A line is a RULEDOUT line when its stripped form starts with the literal RULEDOUT bracket token. Per mcloop's `parse` function.
   - [x] Implement attachment logic: a RULEDOUT line attaches to the nearest task with strictly less indent. If no such task exists in the current phase, attach to the most recent root task (matches mcloop's fallback in `parse`).
   - [x] Tests: a RULEDOUT line attaches to a parent task by indent; a top-level RULEDOUT line attaches to the most recent root task; multiple RULEDOUT lines on one task collected in order.

- [x] Parse @deps lines
   - [x] Implement `_DEPS_RE = re.compile(r"^(\s*)@deps\s+(.+)$")`. The captured tail is whitespace-separated task IDs of the form T-NNNNNN (no trailing colon — bare IDs).
   - [x] A `@deps` line attaches to the immediately preceding task line at strictly lesser indent. A `@deps` line at the same indent as its task is also accepted (lenient) and emits a validation warning.
   - [x] Validation: every referenced ID must exist in the plan; otherwise raise `PlanValidationError` from `validate_plan` (not at parse time — parse only structures, validate checks references).
   - [x] Tests: single-line deps with one or more IDs; deps attached to nested subtasks; missing target ID surfaces in `validate_plan`. Per design doc section 6 and Phase A scope in section 8.

- [ ] Assemble the parse tree
   - [x] Implement `parse_plan(text: str, *, strict: bool = False, source_path: Path | None = None) -> Plan`. The `strict` parameter is wired but defaults to False (compat mode); strict-mode behavior is added in Stage 3.
   - [x] State machine: walk lines once, tracking the current phase (or bugs section), the current subsection within a phase, and a stack of open tasks (by indent). Each task line opens or closes scopes by indent comparison, matching mcloop's logic in `parse`.
   - [x] Project title: the first H1 heading seen. Preamble: prose between the H1 and the first phase or bugs heading. Phase prose: prose between a phase heading and its first task or subsection. Subsection prose: prose between a sub-heading and its first task.
   - [ ] On a syntax violation in compat mode, raise `PlanSyntaxError(message, line, column, path)` with a message that quotes the offending line.
   - [ ] Tests: a hand-crafted minimal valid plan parses correctly; a missing H1 raises; tasks before any phase land in an implicit phase zero (mcloop tolerates this — see `parse` function and PLAN.EXAMPLE.md fixtures in mcloop); a Bugs section after phases is recognized; subsections inside a phase preserve their tasks.

- [ ] Structural sanity check
   - [ ] Implement `_check_structural_sanity(parsed_plan)` raising `PlanSyntaxError` on duplicate H1 titles, multiple Bugs sections (any heading level), or duplicate phase/stage ordinals. Per mcloop's `_check_structural_sanity` function; the rationale (no auto-fix) is preserved.
   - [ ] Tests: each corruption pattern detected with the offending line numbers in the error message.

- [ ] [BATCH] Malformed-input rejection coverage
   - [ ] Add a parameterized test class `tests/test_parser_rejections.py` exercising each rejection condition with a minimal failing fixture: duplicate H1, multiple Bugs sections, duplicate phase ordinals, malformed annotations (unclosed bracket, missing colon, empty value), action tag without colon, action tag with empty action name. Per Codex's pile-5 acceptance test gap.
   - [ ] Each test asserts on the specific error message and the line number where the error was detected.

- [ ] [USER] Verify compat-mode parser reads existing PLAN.md files without error. From a shell, run `python -c "from bob_tools.planfile import parse_plan; from pathlib import Path; p = parse_plan(Path('/Users/mhcoen/proj/duplo/PLAN.md').read_text()); print(f'phases={len(p.phases)}, bugs={p.bugs is not None}')"`. Then do the same for `/Users/mhcoen/proj/mcloop/PLAN.md` and `/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md`. Report the phase counts. No exceptions should be raised. Expected counts: duplo has 8 phases, mcloop has at least 7 stages plus a Bugs section, PLAN.EXAMPLE has 2 stages.

- [ ] Verify Stage 2 leaves the repo green.
