## Stage 3: Strict-mode parser

Strict mode requires the format additions in design doc section 4.1
and 4.2: the magic line, stable task IDs, and the phase-id comment.
The parser still accepts compat-mode input when strict is false;
strict mode is opt-in or triggered by the presence of the magic
line.

- [ ] [BATCH] Recognize the format magic line and phase-id comment
   - [ ] Implement `_MAGIC_RE = re.compile(r"^<!--\s*bob-plan-format:\s*(\d+)\s*-->\s*$")`. Capture the version integer.
   - [ ] Implement `_PHASE_ID_COMMENT_RE = re.compile(r"<!--\s*phase_id\s*:\s*([A-Za-z0-9_]+)\s*-->")` matching `mcloop/ledger_emit.py`'s regex of the same name. The two libraries must use identical regexes so they cannot disagree.
   - [ ] Update `parse_plan` to capture the magic line when it appears as the first non-blank line and store the version in `Plan.magic_version`. Absence is not an error (compat mode); presence with an unrecognized version raises `PlanSyntaxError`.
   - [ ] Update phase parsing: when a phase-id comment line follows a phase heading before any task, set `Phase.phase_id` to that value and `Phase.phase_id_source` to "explicit_comment".
   - [ ] Tests: magic line captured; phase-id comment attaches to the immediately preceding phase heading; a phase-id comment not on its own line does not attach to a task's phase (it is a different mechanism — task IDs, not phase IDs).

- [ ] Recognize the legacy `## Phase phase_NNN: Title` heading form
   - [ ] Implement `_LEDGER_PHASE_HEADER_RE = re.compile(r"^##\s+Phase\s+(?P<id>[A-Za-z0-9_]+):\s+(?P<title>.+?)\s*$")` matching `mcloop/ledger_emit.py`'s `_PHASE_HEADER_RE`. Identical for the same reason.
   - [ ] In both strict and compat mode: when a heading matches this regex but not the stage-or-phase ordinal regex (because the id is non-numeric, e.g. phase_001), accept it, set `Phase.phase_id` to the captured id, and `Phase.phase_id_source` to "explicit_header". Per design doc section 7.1.
   - [ ] Tests: a heading with a non-numeric phase id parses with that id and source "explicit_header"; a heading with a numeric ordinal parses with that ordinal and phase_id None unless a comment follows (then "explicit_comment"); the canonicalizer eventually rewrites explicit_header to explicit_comment, but the parser preserves both forms as input.

- [ ] Stable task IDs
   - [ ] Implement `_TASK_ID_RE = re.compile(r"^T-(\d+):\s+(.*)$")`. Apply to the task text after stripping the checkbox but before extracting tags.
   - [ ] In compat mode: presence of a task ID is recorded on `Task.task_id` but absence is accepted.
   - [ ] In strict mode: absence of a task ID raises `PlanSyntaxError` with the exact message format from design doc section 9: "expected task id like T-000123 after checkbox marker".
   - [ ] Tokenization: the library MUST NOT use substring matching to find tasks by ID. Implement `_find_task_by_id(plan, task_id)` that walks the parsed tree. Per design doc section 7.2 caveat about substring matching.
   - [ ] Tests: a task line with a stable ID parses with that ID; a task line without an ID parses with task_id None in compat mode and raises in strict mode; `_find_task_by_id` distinguishes T-000001 from T-0000010.

- [ ] Ordinal fallback for unattributed phases
   - [ ] When neither a phase-id comment nor the legacy header form provides an id, leave `Phase.phase_id` as None and `Phase.phase_id_source` as "none". The Stage 5 `resolve_task_context` function is what maps None to an ordinal fallback at resolve time.
   - [ ] Tests: a phase with no id source has phase_id None and source "none".

- [ ] Magic line gates strict mode by default
   - [ ] When the magic line is present, default `strict` to True even if the caller passed `strict=False`. When absent, default to compat. Explicit caller-supplied `strict=True` overrides.
   - [ ] Tests: magic present implies strict; magic absent implies compat; explicit strict=True with no magic still strict.

- [ ] Write the Stage 3 verification helper script. Create `bob_tools/planfile/tests/manual/check_strict_reject.py`. The script imports `parse_plan` and `PlanSyntaxError`, then for each of `/Users/mhcoen/proj/duplo/PLAN.md` and `/Users/mhcoen/proj/mcloop/PLAN.md` calls `parse_plan(text, strict=True)`. Expected outcome is rejection: for each file it prints either `REJECTED <path> at line=<n> col=<m>` (the correct result) or `PARSED <path> (UNEXPECTED - strict mode should have rejected this)` and exits non-zero. The script takes no arguments and hardcodes the two paths.

- [ ] [USER] Run the strict-mode rejection check and confirm both files are rejected.

   What to do: run this single command in a shell.

   python -m bob_tools.planfile.tests.manual.check_strict_reject

   What to expect: two lines, both starting with REJECTED. Each line names a line and column where the parser expected a stable task ID and did not find one. Both existing files lack `T-NNNNNN:` IDs, so strict mode must reject them.

   What to report back: paste the two output lines. If either line starts with PARSED, strict mode is incorrectly accepting un-migrated files. That is a parser bug to fix before Stage 3 is complete.

- [ ] Verify Stage 3 leaves the repo green.
