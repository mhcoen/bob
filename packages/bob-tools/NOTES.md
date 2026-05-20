# Planfile build notes

## Observations

- 2026-05-16 [7.2] The Stage 7 verification helper expects
  `/Users/mhcoen/proj/bob-tools/.venv/bin/bob-plan` to exist, but the
  bob-tools package is not installed in the venv (only its
  declaration sits in `pyproject.toml` under `[project.scripts]`).
  Running `python -m bob_tools.planfile.tests.manual.check_cli_end_to_end`
  in the current tree fails with the pre-flight check
  ("`bob-plan` not found"). Resolving the verification requires a
  one-time `pip install -e .` into the venv; the script intentionally
  does not run `pip` itself because per the global CLAUDE.md
  instruction no package installer may be invoked from a session.
- 2026-05-16 [7.2] Running `bob-plan fmt` on
  `/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md` produces a non-additive
  diff in two ways the Stage 7 verifier flags: (a) the six nested
  example-flow bullets under the "Clearer terminal output" task
  (lines 110-115 of the source — they use `  - "..."` with no
  checkbox) are dropped by the parser/renderer round-trip and
  therefore disappear from the formatted file; (b) the blank lines
  between top-level task bullets in Stage 2 are collapsed by the
  renderer. The verifier also reports that post-fmt
  `bob-plan validate` exits 1 because line 243's
  `- [x] [RULEDOUT] tag for recording failed approaches in PLAN.md`
  is treated as a task body whose leading bracket `[RULEDOUT]` is
  flagged as an unknown tag (the `[RULEDOUT]` sibling-line form is a
  different production). All three are pre-existing
  parser/renderer/validator behaviors, not Stage 7 CLI bugs; the
  verifier surfaces them honestly per its "additive-only" contract,
  and the underlying fix belongs upstream (likely a future fmt
  pass that escapes or preserves no-checkbox bullets and a
  validator rule that lets `[RULEDOUT]` lead a task body when the
  trailing text describes the tag's purpose).
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

- 2026-05-15 [2.5.4] Compat-mode syntax errors are scoped narrowly. The
  task description says "raise PlanSyntaxError on syntax violations in
  compat mode", but most of the candidates (orphan tasks, prose outside
  accumulators, `### subsection` inside Bugs) have established mcloop
  precedent for being silently tolerated and so stay dropped in compat
  mode (deferred to strict mode in Stage 3, per the 2.5.2 NOTES entry).
  The one case that does raise in compat mode is an orphan `@deps` line
  with no preceding task to attach to: `@deps` is a planfile-introduced
  feature with no mcloop history, so there is no compat behavior to
  preserve, and the keyword has no semantic interpretation absent a
  target task. The `_raise_syntax_error` helper centralizes the
  message-quoting convention (backticks around the offending line) so
  Stage 3's strict-mode call sites can reuse the same format.

- 2026-05-15 [2.5.5] Two cases listed in the Stage-2.5.5 task description do
  not match what compat-mode actually does, and the new
  ``TestParsePlanMinimalValidPlan`` class pins the actual behavior
  rather than the described one:
  1. "a missing H1 raises" — compat mode does not raise. mcloop's
     ``parse`` has no H1 concept at all, so there is no precedent to
     preserve, but our compat parser also chose silent tolerance
     (``project_title`` falls back to ``""``). Strict mode in Stage 3
     should require an H1 and raise ``PlanSyntaxError`` on absence.
  2. "tasks before any phase land in an implicit phase zero" — the
     typed ``Plan`` model has no phase-zero slot, and ``Phase``
     requires an ordinal pulled from a heading. The 2.5.2 decision
     (documented above) was to drop orphan tasks silently to mirror
     mcloop's effective ``stage=""`` behavior. Stage 3 strict mode
     will raise. If a future task asks us to actually surface these
     tasks somewhere, the typed model needs a new container (e.g. an
     orphan-tasks tuple on ``Plan``); deferring until that need is
     concrete.

- 2026-05-15 [2.7.1-2.7.2] The Stage 2.7.1 task description listed eight
  rejection conditions for the new `tests/test_parser_rejections.py`:
  three structural (duplicate H1, multiple Bugs sections, duplicate
  phase/stage ordinals) and five tag-level (annotations with unclosed
  bracket, missing colon, or empty value; action tags without a colon
  or with an empty action name). Only the three structural anomalies
  raise in compat mode — every tag-level malformation is silently
  treated as prose by the parser today, and `[feat: ]` (the empty-value
  case) is in fact captured as a *valid* annotation because
  `_ANNOTATION_CONTENT_RE` only requires whitespace after the colon and
  permits an empty value. Per the [2.5.5] precedent of pinning actual
  behavior when the task description and the parser disagree, the new
  test module exercises the structural rejections with message/line
  assertions and uses a companion class to pin compat-mode tolerance of
  the tag-level cases. Stage 3 strict mode is the right place to add
  the rejections the task description anticipated.

- 2026-05-16 [BUG re-attempt from task 2.8] Re-running the four check
  commands surfaced a pre-existing `ruff` RUF022 failure on
  `bob_tools/planfile/__init__.py`: the `__all__` list was committed
  unsorted in commit f067460 (where the file was first added) and the
  Stage-2 group needed isort-style ASCII-alphabetical ordering. Fixed
  by re-sorting that group; the commented Stage-3+ entries were left
  in declaration order because they are inactive. Also stripped an
  unused `# noqa: BLE001` on `except Exception as exc:` in
  `tests/manual/check_compat_read.py` (RUF100): BLE001 is not in our
  enabled set (`E,F,W,I,B,UP,RUF`), so the directive was dead. The
  bug-fix payload (the `bug_count` API, the helper script, and the
  `TestBugCount` cases) from the previous attempt was left intact —
  only the lint cleanup the previous attempt left unfinished was
  applied here.

- 2026-05-16 [3.1.1-3.1.5] Three compat-mode tolerances were chosen for
  the magic line and phase-id comment that strict mode (Stage 3) should
  revisit:
  1. A magic-shaped line appearing later than the first non-blank line
     falls through to ordinary prose handling rather than being captured
     or rejected. Recognizing it post-preamble would silently upgrade a
     compat plan whose author left a stray template comment behind;
     rejecting it would make a hand-edited PLAN.md harder to recover.
     The "first non-blank line only" check is in `_detect_magic_line`.
     Strict mode could either reject a misplaced magic line or require
     it as the literal first line.
  2. Duplicate `<!-- phase_id: ... -->` comments inside the phase-prose
     window overwrite (last write wins), matching the behavior of
     `mcloop/ledger_emit.find_explicit_phase_id_for_task` so the two
     libraries cannot drift. The grammar permits at most one comment
     (`PhaseIdComment?`), so strict mode should raise on duplicates.
  3. A phase-id comment after the first task of the phase is silently
     dropped in compat mode. The phase-prose accumulator closes at the
     first task, so the comment falls through the "no active accumulator"
     branch. Strict mode should reject it: position is part of the
     grammar (`PhaseHead PhaseIdComment? Prose? ...`), and a late
     comment is unrecoverably ambiguous between "intended for this
     phase" and "intended for the next phase but misplaced".

  The `<!-- phase_id: ... -->` regex was specified as
  `(...)` in the task description but mcloop uses the named-group form
  `(?P<id>...)`. The two are functionally equivalent (same match span,
  same characters), and the task explicitly required the positional
  form; I kept it that way. If a later task ever needs to call
  `m.group("id")` for parity with mcloop's call sites, the regex can be
  re-written to use the named group without changing semantics.

- 2026-05-16 [BUG from task 2.8] The BUGS.md entry filed against task 2.8
  was truncated by mcloop's `flat_obs[:200] + "..."` capture (see
  `mcloop/main.py:1218-1226`); the captured 200 chars covered only the
  python one-liner up to ``p.bugs is no`` and cut off the user's actual
  observation. Re-running the one-liner against all three target files
  (`/Users/mhcoen/proj/duplo/PLAN.md`, `/Users/mhcoen/proj/mcloop/PLAN.md`,
  `/Users/mhcoen/proj/mcloop/PLAN.EXAMPLE.md`) shows the parser succeeds
  cleanly with phase counts 8, 10, 2 and ``bugs is not None`` False for
  all three (none of those files has a literal ``## Bugs`` heading). The
  reproducible failure mode the bug entry *most likely* refers to is the
  ambiguity of the printed output: ``bugs=False`` is the same string for
  "no Bugs section in the file" and "Bugs section is present but empty",
  and task 2.8's stated expectations have ``mcloop/PLAN.md ... bugs=true``
  (which the file does not satisfy because mcloop keeps its bug list in a
  sibling ``BUGS.md`` file, not inside PLAN.md). The fix has two parts:
  (1) a new ``bug_count(plan) -> int`` in ``operations.py`` so callers
  can print a concrete count instead of the ambiguous bool, and (2) a
  manual verification helper at
  ``bob_tools/planfile/tests/manual/check_compat_read.py`` that the
  Stage 2 USER task can invoke with ``python -m
  bob_tools.planfile.tests.manual.check_compat_read`` to print
  ``OK <path> phases=<n> bugs=<true|false> bug_count=<n>`` per file
  (matching the helper-script task the user added in PLAN.md after this
  bug fired). Tests in ``TestBugCount`` pin the three states (no
  section, empty section, populated section) so the disambiguation
  cannot regress.

- 2026-05-16 [3.2.2] The ordinal field for a ledger-form
  `## Phase phase_001: ...` heading is set to positional index
  (`len(phases) + 1`) since there is no digit to extract. Consequence:
  a mixed plan like `## Phase 1: A` then `## Phase phase_002: B` then
  `## Phase 5: C` produces ordinals 1, 2, 5 — non-contiguous because
  the digit-form headings override positional numbering. This matches
  the design doc's "ordinal fallback = n-th heading in document order"
  rule applied only when no digit is present, and it preserves the
  behavior of digit-form headings.

  The structural-sanity check (`_check_structural_sanity`) only
  groups duplicates by `_STAGE_RE`'s `num` group, so two phases with
  the same ledger-form phase_id (e.g. two `## Phase phase_001:` lines)
  are not flagged. This is a gap parallel to the digit-form duplicate
  detection. Worth extending in a later task if a corrupt PLAN.md
  with duplicate ledger phase_ids is ever observed in the wild; for
  now no precedent has surfaced and the design doc does not require
  it.

- 2026-05-16 [4.2.1] The renderer parent BATCH [4.1.1-4.1.11] was never
  executed in the prior session — same failure mode flagged for tasks
  1.1.1 (2026-05-15 entry) and 2.1.1-2.1.5: the orchestra-run log shows
  the agent exited in ~9 seconds with "Ready. What would you like me to
  work on?" and the orchestrator still advanced to 4.2.1. Property tests
  cannot exist without a renderer, so this session implemented
  `renderer.py` and the public `render_plan` wiring as a prerequisite,
  then the two property tests in `tests/test_roundtrip.py`. Both 4.1
  and 4.2 should be checked together.

- 2026-05-16 [4.2.1] PLAN.md line 163 ("Phase rendering: ... blank line,
  then subsections in order, then tasks in order") is in the wrong order
  for the parser semantics: a `###` subsection captures every following
  task until the next subsection or phase boundary, so if subsections
  rendered before phase-level tasks, those phase-level tasks would be
  re-parsed into the last subsection and the round-trip would fail. The
  renderer therefore emits tasks first, then subsections. The design
  doc may need this clarified; flagging here rather than altering the
  PLAN.md description (the PLAN.md/design-doc reconciliation rule says
  the design doc wins, but I have not consulted it).

- 2026-05-16 [4.2.1] `normalize_positions` in `renderer.py` collapses
  three fields that legitimately differ across parse-render-parse
  cycles: `line_number` (rendered text has its own layout),
  `Task.indent_level` (renderer canonicalizes to 2-space-per-level),
  and `Phase.phase_id_source` (renderer migrates `"explicit_header"`
  to `"explicit_comment"` per design doc section 7.1). The 4.2.1 task
  description only mentions ignoring line numbers; the wider normalize
  set is required for `ledger_phase_header.md` to round-trip, and the
  helper docstring documents this. If a stricter equality check is
  preferred, the ledger fixture can be moved out of
  `test_parse_render_parse_idempotent` (the canonical-form
  fixed-point test still passes on it without normalization).

- 2026-05-16 [5.2.4] Coverage-pinning task; the four bullets in the task
  description are already exercised by `TestResolveTaskContext` from the
  prior 5.2.1–5.2.3 sessions: lookup by ID
  (`test_resolves_by_task_id_exact_match`), lookup by label
  (`test_resolves_label_prefix_with_separator` plus the
  `test_resolves_positional_label_*` cases), ordinal fallback
  (`test_phase_with_none_source_fills_ordinal_id`,
  `test_ordinal_fill_uses_document_position_not_phase_ordinal`,
  `test_ordinal_fill_per_phase_position`,
  `test_ordinal_fill_via_positional_label`), and unknown-task handling
  (`test_unresolved_reference_returns_none_context`,
  `test_unresolved_reference_stays_none_not_ordinal`). No new tests
  added in this session.

  Wording discrepancy flagged per the PLAN.md preamble: the task
  description says "raises a clear error for an unknown task", but the
  design doc (section 7.1 shim sketch — `return PhaseIdResolution(None,
  "none", ctx.plan_phase_count)`) and the contract pinned by the prior
  subtasks have `resolve_task_context` return a none-shaped
  `TaskContext` for an unknown reference, not raise. Callers branch on
  `task_id is None` / `phase_id is None`; the `label` field echoes the
  input so a clear diagnostic can be constructed at the call site
  (`test_label_field_echoes_input`). Design doc wins per the PLAN.md
  reconciliation rule. If the user actually wants the resolver to
  raise, that is a contract change to the design doc, not something to
  bolt onto the test layer.

- 2026-05-16 [3.5.2] Contract-pinning task; the implementing agent in
  3.5.1 wrote the full test class `TestMagicLineForcesStrict`
  (`bob_tools/planfile/tests/test_parser.py:1690-1746`) covering all
  three cases this task requires: magic-present forces strict (with the
  caller omitting `strict=` and with the caller explicitly passing
  `strict=False`), magic-absent keeps compat default, and explicit
  `strict=True` without a magic line still raises. A sanity test for
  magic-present + ids-present parsing cleanly is also there. No new
  tests needed; this entry records that the 3.5.2 contract is already
  pinned and the check commands were re-run to confirm.

- 2026-05-16 [5.4.1-5.4.7] Same recurring failure mode flagged for tasks
  1.1.1, 2.1.1-2.1.5, and 4.1.1-4.1.11: task `[5.1.1-5.1.8]` (Settlement
  dataclass + `migrate`) was never executed — the session-history entry
  shows an 8-second session ending with "Ready. What would you like to
  work on?" and no `Complete:` commit follows `11367f2` (the "next:
  5.1.1-5.1.8" checkpoint). `Settlement` was unimplemented when this
  session started, but task 5.4 depends on it ("the settlement for the
  direct task uses the kind policy above"). This session implemented
  `Settlement` and `Outcome` in `model.py` as a prerequisite for the
  5.4 mutation operations, but did NOT implement `migrate` — that
  belongs to the 5.1 scope. A future session will need to revisit 5.1
  to add `migrate` and any 5.1-specific tests (idempotency,
  partial-migration ID assignment for non-contiguous existing IDs,
  phase-id ordinal assignment). The Settlement kind-policy tests
  required by 5.1.1-5.1.8 step 2-6 are covered indirectly through
  `TestCompleteTask` and `TestFailTask` in this session, but the
  Settlement dataclass-construction tests from 5.1.1-5.1.8 step 8 are
  not — they were never written and are not strictly necessary because
  Settlement is a frozen dataclass with no behavior of its own (per
  the project rule against testing trivial dataclass additions).

- 2026-05-16 [5.4.1-5.4.7] The kind policy from the 5.1.1-5.1.8 task
  description treats AUTO action tasks and successfully-verified USER
  tasks as `work_observed`; the implementation in
  `_direct_completion_kind` uses the disjunction
  `task.action_tag is not None or "USER" in task.flag_tags`. A task
  that is both BATCH and USER (unlikely but representable) settles as
  `work_observed`. A task that is BATCH alone (the common case of a
  BATCH parent surfaced as a unit by `next_tasks`) settles as
  `commit_landed` — the BATCH flag is a scheduling hint, not an
  evidence-of-commit signal, so the default applies.

- 2026-05-16 [5.4.1-5.4.7] `complete_task`'s cascade walks ancestors
  innermost-first. The recursion appends the new (DONE) parent to
  `ancestors` after walking its children, so by the time the recursion
  unwinds the list reads [innermost, ..., outermost]. This matches the
  design doc section 5 wording "from innermost outward" and the
  three-Settlement test for the BATCH chain. An ancestor whose status
  was already DONE before the call is not re-added to the list, even
  if its children are now all DONE — the cascade fires on a
  transition, not on observation of a final state. This guards against
  duplicate `kind="none"` Settlements when the operation is called
  twice on an already-completed subtree.

- 2026-05-16 [7.1.1-7.1.9] Stage 7's CLI depends on three APIs that
  prior PLAN.md checkpoints marked complete but never implemented in
  code: `migrate` (specified in 5.1.1-5.1.8), `load`, and `save`
  (specified in 6.1.1-6.1.4). Same recurring failure mode flagged in
  the 5.4.1-5.4.7, 4.2.1, 2.2.1-2.2.6, and 1.1.2 entries above. This
  session implemented the minimum surface those subcommands need to
  function: `operations.migrate` (assigns missing `T-NNNNNN` and
  synthesizes `phase_NNN` for phases whose source was `"none"`, both
  idempotent), `fileio.load` (thin wrapper over `parse_plan` with
  `source_path` propagation), and `fileio.save` (atomic write via
  same-directory tempfile + `fsync` + `os.replace`). The Stage 6
  advisory-lock `update` entry point is stubbed with
  `NotImplementedError` since the CLI does not yet exercise the
  load-lock-reparse-save flow described in 6.1.3. A future session
  should revisit:
    1. `migrate` partial-migration and idempotency unit tests
       (5.1.1-5.1.8 step 8) which were never added — they are
       exercised indirectly through `TestFmt.test_idempotent_on_strict_plan`
       in `tests/test_cli.py` but not at the function level.
    2. The `update` helper with file locking + concurrent-edit
       detection (6.1.3) — needed when humans and tooling race for
       the same PLAN.md.
    3. The 6.1.4 atomic-write / locking tests, which assume the full
       Stage 6 surface is in place.
  The CLI's `fmt` subcommand composition matches the design doc 3.2
  contract (`save(path, migrate(parse_plan(read(path))))`) once these
  prerequisites are in place. The `done` and `fail` subcommands emit
  the Settlement tuple as a JSON list on stdout per the 7.1 task
  description; `bob-plan done` of an inner-most leaf may emit
  multiple list entries (derived parent completion) per design doc
  section 5.

- 2026-05-16 [8.5] Final-verification install step (`pip install -e
  /Users/mhcoen/proj/bob-tools`) cannot run in this session: PyPI is
  unreachable from the sandbox (SSL cert verification fails for
  `pypi.org`) and the venv at `.venv` does not have `setuptools`
  installed, so even `--no-build-isolation` is unusable
  (`Cannot import 'setuptools.build_meta'`). Same blocker the Stage 7
  and Stage 8.4 verification scripts hit: `.venv/bin/bob-plan` is not
  present because the package has never been editable-installed. The
  global CLAUDE.md forbids invoking package installers from a session,
  so this step has to be run by the user once with network access and
  `setuptools` available in the venv. CLI surface itself is verified
  functional in this session by `python -m bob_tools.planfile.cli
  --help`, which lists all five subcommands (`validate`, `next`,
  `fmt`, `done`, `fail`) from the same `main()` the `bob-plan` console
  script would dispatch to. Once the user runs the install,
  `bob-plan --help` will print the same banner.

- 2026-05-20 [12.1] [T-000173] On re-entry the `assert_mcloop_canonical`
  function was already in `operations.py` (added in checkpoint commit
  011d166) and all five `TestAssertMcloopCanonical` cases passed. The
  no-op retry diagnosis pointed at a missing-file-change signal rather
  than a genuine implementation gap. Confirmed by reading the
  implementation against the v4 Contract 5 wording: it runs
  `validate_plan(plan, constructed=True)`, renders, parses with
  `source_path`, semantically compares (not byte fixed point) after
  the v4 normalizer, then enforces R1 (via `_INCOMPLETE_CHECKBOX_RE`
  mirroring `mcloop._planfile_precondition._INCOMPLETE_RE`) and R2
  (every parsed task carries a `T-NNNNNN`) without importing mcloop,
  and returns the rendered text. `PlanSyntaxError` from the re-parse
  is intentionally not caught — it propagates per contract.

  Interpretation decision worth recording explicitly: Contract 5's
  task wording lists `line_number`, `indent`, `source_path`, and
  `trailing_lines` as the only fields the semantic normalizer should
  ignore. The implementation also collapses `Phase.phase_id_source`
  to `"explicit_comment"` when the intended source was the legacy
  `"explicit_header"` form. This is necessary because the renderer
  always emits `<!-- phase_id: ... -->` (comment form), so the
  re-parse always reports `explicit_comment`; without the collapse,
  legitimate phases with the legacy header would fail the round-trip
  even though both representations identify the same phase. The
  collapse is bounded — `"none"` stays `"none"` — so the validity
  signal `validate_plan(constructed=True)` enforces (source must not
  be `"none"`) is preserved. This is a pragmatic departure from the
  literal task wording.

  Coverage gap filled in this session: added three tests to
  `TestAssertMcloopCanonical` exercising paths the original five
  cases did not touch — a multi-phase plan, a plan with a Bugs
  section, and `source_path` forwarding to the re-parse (verified
  via a monkeypatch spy on `operations.parse_plan` since the happy
  path otherwise has no observable use of `source_path`).

- 2026-05-20 [12.2] [T-000174] The Stage 12 gate verification needs
  two specific failure-mode tests that the implementation step
  (12.1) did not include: a v3-leak-class fixture (parsed plan
  byte-fixed-points but semantically diverges from the intended
  plan) and an R1-shape fixture (rendered text contains an
  incomplete checkbox line that the parser does not recover as a
  TODO task). Neither is constructible organically through the
  public Plan/Task model — `validate_plan(constructed=True)`'s
  field-stability harness would catch any divergence at scalar
  granularity before Contract 5 ever runs, and the renderer never
  emits checkbox lines the parser would not recover. Both new tests
  therefore monkeypatch `operations.parse_plan` to inject a
  divergent or task-stripped reparse result, discriminating the
  Contract 5 reparse from `validate_plan`'s internal field-stability
  parses via the `source_path` kwarg: the Contract 5 call is the
  only one that forwards a non-None `source_path`, so passing a
  test marker makes the divergence trigger unambiguous without
  fragile call-counting or content-matching. The same marker
  approach is reused for both tests.

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

001ec52: The parser now extracts the project title from the first H1 heading and accumulates prose in three distinct regions: the preamble (between the H1 and the first phase or bugs heading), phase prose (between a phase heading and its first task or subsection), and subsection prose (between a subsection heading and its first task). A new `_finalize_prose` helper trims leading and trailing blank lines while preserving internal paragraph breaks. Thirteen tests were added covering title extraction, multi-paragraph preambles, prose boundary behavior at tasks and subsections, and end-of-file finalization.

142caa8: Added `_check_structural_sanity(lines, source_path)` to the planfile parser as a pre-parse corruption guard, mirroring mcloop's own check in `checklist.py`. The function scans raw lines for three anomalies observed in real PLAN.md corruption incidents: duplicate H1 titles with identical text, multiple Bugs sections at any heading level, and duplicate phase/stage ordinals. It is called at the start of `parse_plan()`, before any structural parsing, so the typed `Plan` model never has to represent a corrupted document. When anomalies are found, a single `PlanSyntaxError` is raised listing all of them with one-based line numbers so the user can fix everything in one pass. Ten new tests cover each anomaly in isolation, the H1/stage header disambiguation (a `# Phase 1:` line is classified as a stage header, not an H1), multi-error aggregation, source-path passthrough, and the clean-plan no-op path.

3435ed6: Added support for format-version magic line and phase-id comments. The parser now recognizes a leading `<!-- bob-plan-format: 1 -->` line to enable strict mode, and can attach explicit phase IDs via `<!-- phase_id: ... -->` comments placed after phase headings. Unrecognized format versions raise an error, and phase-id comments are only captured when they appear on their own line before any tasks.

5f55495: Added support for ledger-form phase headings, allowing phase identifiers like "phase_001" instead of bare integers. This ensures consistency with the ledger emission library's parsing rules.

846d956: Added support for ledger-form phase headings (e.g., "## Phase phase_001: Title") in planfile parsing. These non-numeric identifiers are now recognized as explicit phase IDs, with ordinals assigned positionally. The change ensures compatibility with legacy ledger formats while preserving existing behavior for numeric headings.

a1c07d9: Updated task ID parsing to capture the full line after the ID, enabling proper handling of annotations and tags. The regex now extracts digits and remaining text separately, ensuring the canonical T-NNNNNN format is preserved.

176bec4: Added strict mode enforcement for mandatory task IDs in plan files. Tasks without a T-000123-style ID now raise a PlanSyntaxError with a specific message and location. Compat mode remains unchanged, allowing missing IDs.

0f731a9: Added a helper function to find tasks by exact ID match, preventing substring confusion where similar IDs like T-000001 and T-0000010 would be incorrectly conflated. Updated tests to verify the function works across all plan sections and correctly handles prefix overlaps.

d02cfa5: The parser now automatically enforces strict mode when a plan file includes the magic version line, ensuring files that opt into the strict format are parsed correctly regardless of the caller's flag. This prevents silent failures when a plan declares itself strict but the caller forgets to enable strict mode. The caller's explicit strict flag is still honored when no magic line is present.

6c244f0: Added a manual verification script to test strict parsing mode. The script checks that existing PLAN.md files without the required strict-mode header are correctly rejected, ensuring backward compatibility and preventing accidental acceptance of outdated formats.

54724e3: Implemented the renderer module and round-trip property tests. The renderer converts parsed Plan objects back to canonical PLAN.md text, fixing a phase content ordering discrepancy (tasks now render before subsections to maintain round-trip correctness). Added a normalization helper to compare parsed objects ignoring line numbers, indentation differences, and phase ID source variations. Created comprehensive test fixtures and property tests verifying parse-render-parse idempotence and render-parse-render stability.

226cba2: Added auto-skipping for slow-marked generative tests unless explicitly requested with `pytest -m slow`. The default iteration count for property-based tests is 100, while the slow variant runs 1000 iterations. This keeps the default test suite fast while allowing thorough generative testing when needed.

f4480ea: Added canonicalize function to renderer, enabling round-trip formatting of plan files without modifying task IDs or adding phase comments. This provides a lossless reformatting step separate from identity-mutating operations like migration.

a52ac94: Added a task context resolver to replace substring-based phase lookups. The new function resolves task references by exact task ID or structured text matching, preventing ambiguous overlaps. It returns a context object with phase info, supporting both modern task IDs and legacy plan files. Comprehensive tests verify correct behavior for nested tasks, subsections, bugs, and edge cases like prefix collisions.

9e70c89: Added support for positional task labels (e.g., "1.3.2") to resolve tasks by phase ordinal and hierarchical position, matching mcloop's output. This prevents ambiguous substring matches and ensures compatibility with pre-migration plans lacking task IDs. The resolver now attempts positional lookup first, falling back to ID or text matching if the format is invalid or out of range.

1785f5b: Added ordinal fallback for phase IDs: when a phase lacks an explicit identifier, the resolver now synthesizes a "phase_NNN" ID based on the phase's document-order position and reports the source as "ordinal". This ensures callers always receive a usable phase ID without needing a separate pass over the plan, aligning with the explicit-required/ordinal-degraded contract. The change applies to both positional and label-based task resolution, while unresolved references and bug tasks continue to report no phase ID.

66f1ea2: Added next_tasks operation to find actionable tasks based on priority rules. Bugs have absolute priority over phases, and only the first incomplete phase is considered. Tasks are blocked by dependencies, failed siblings, or incomplete parents. BATCH tasks surface as a single unit with their actionable children.

8a83026: Implemented core mutating operations for planfile tasks: complete_task, fail_task, reset_task, add_task, and replace_phase. Added Settlement and Outcome dataclasses to track operation results for ledger emission. The cascade logic auto-completes parent tasks when all children are done, returning derived settlements in innermost-first order. Tasks with AUTO action or USER flags settle as work_observed; others as commit_landed. Tests cover the kind policy, cascade behavior, and idempotency.

0900df6: Enhanced plan validation to detect duplicate task IDs, unknown leading bracket tags, and malformed trailing annotations. The validator now reports all issues together without short-circuiting, providing comprehensive error messages for users to fix their plan files.

7441584: Added a consistency checker that reconciles PLAN.md task statuses against ledger events. It raises an error when a task's checkbox contradicts the most recent lifecycle event for that task, such as a DONE checkbox after a test_failed event. The checker intentionally allows resets (TODO after test_failed) and ignores tasks without events or stable IDs.

02f4c1e: Implemented the CLI for bob-plan with subcommands validate, next, fmt, done, and fail. Added file I/O operations load and save, and the migrate function to assign stable IDs to tasks and phases. The CLI handles parsing, validation, and atomic writes, and outputs settlements as JSON for done and fail commands.

ecdf99d: Added a Stage 7 verification script for the bob-plan CLI that performs an end-to-end test. The script validates, formats, re-validates, and fetches the next task from a plan file, ensuring the formatting diff is additive-only. It also documents two pre-existing issues: a missing CLI tool installation and non-additive formatting changes in certain edge cases.

adf7278: Added a verification script to ensure `bob-plan fmt` only makes additive changes to duplo-generated plan files. The script copies a real plan, runs the formatter, and compares parsed structures to detect any semantic changes beyond allowed formatting adjustments. If divergences are found, it logs them to a bugs file and fails.
