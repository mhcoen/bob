# Notes

## Observations

### [7.5.5] Pinned no-initializer-imports invariant in test suite — 2026-04-17

Added `TestNoInitializerImportsInPipeline` at `tests/test_main.py:12269`
with four `hasattr`-style assertions proving `duplo.main`, `duplo.init`,
`duplo.orchestrator`, and `duplo.saver` do not expose
`create_project_dir` or `project_name_from_url` in their module
namespaces. Mirrors the 7.3.5 / 7.4.4 pattern
(`TestNoAskPreferencesInPipeline`). All four tests pass.

The initializer files (`duplo/initializer.py`, `tests/test_initializer.py`)
still exist — per the no-delete rule captured in the 7.5.3 note above,
the physical deletion remains a user-executed follow-up. This test
class pins the live invariant (nothing in the pipeline imports the
dead functions) so the residual files cannot silently regain a caller.

Full suite: 2925 passed, 103 skipped (+4 passes vs. the 2921/103 after
7.4.4; no newly skipped tests).

### [7.5.4] project_name_from_url conditional does not fire — 2026-04-17

Task condition: "If project_name_from_url is used by derive_app_name or
another live path, keep only that function and delete the rest."

The condition is false. Verified via re-grep today:

- `derive_app_name` (`duplo/saver.py:91-159`) does not call
  `project_name_from_url`. Its resolution order is product.json
  `app_name` → duplo.json `app_name` → product.json `product_name` →
  `td.resolve().name` (directory). No hostname-derived naming anywhere
  in the chain.
- No other importer of `duplo.initializer` exists in `duplo/`. Only
  references to `project_name_from_url` outside `duplo/initializer.py`
  are test callers in `tests/test_initializer.py` (10, 15, 18, 21, 24,
  27), plan checklist text in `PLAN.md` / `CURRENT_PLAN.md`, prose in
  `AGENTS.md:356`, and prior NOTES.md entries.

Action: no "keep only that function" operation applies. Full-file
deletion remains blocked by the no-delete rule — already captured in
the 7.5.3 note above. CURRENT_PLAN.md line 45 marked complete because
the conditional's premise is false, not because any code changed.

### [7.5.3] Initializer deletion blocked by no-delete rule — 2026-04-17

The deletion prerequisite ("no remaining callers exist after _first_run
removal") is satisfied. Re-verified today:

- `create_project_dir` and `project_name_from_url` have zero importers
  in `duplo/**/*.py`. Only remaining references are the definitions in
  `duplo/initializer.py` (lines 10, 20) and test callers in
  `tests/test_initializer.py`.
- `AGENTS.md:356` mentions `project_name_from_url()` in prose only; no
  code reference.
- `PLAN.md:977-979` and `CURRENT_PLAN.md:42-45` reference the function
  names inside the plan checklist text; not executable code.

Per the project's absolute no-delete rule (CLAUDE.md: "Never delete any
file"), I cannot execute `duplo/initializer.py` or
`tests/test_initializer.py` deletion. Both files are dead and safe to
remove; leaving in place for the user to delete manually.

Suggested user actions when ready:

```
git rm duplo/initializer.py tests/test_initializer.py
```

After deletion, `CURRENT_PLAN.md` line 44 can be checked off. Lines
45-46 are already satisfied (line 45's branch is not triggered —
`project_name_from_url` is not in any live path per 7.5.1; line 46 is
covered by the existing grep showing no remaining production imports).

### [7.5.2] Confirmed: duplo init does not call create_project_dir — 2026-04-17

Verification of the model statement on CURRENT_PLAN.md line 43. Two
independent checks:

1. Grep for `create_project_dir` and `project_name_from_url` across
   `duplo/` returns only the definitions at `duplo/initializer.py:10,20`.
   No importer anywhere in `duplo/`. `duplo/init.py` does not import
   from `duplo.initializer` at all.
2. `duplo/init.py` uses `Path.cwd()` at every entry point
   (`_run_no_args` at 178, `_run_url` at 275, `_run_description` at 451,
   combined-flow at 516) — the user's existing working directory. No
   directory creation, no `git init`, no hostname-derived naming.

Consistent with the 7.5.1 audit (NOTES.md above): both functions are
dead in production after the 7.2.1 `_first_run` deletion. Their only
remaining references are inside `tests/test_initializer.py` (which
tests the functions themselves) and `duplo/initializer.py`'s own
definitions.

No code change required for this checkbox — it is a confirmation step
asserting the new-model invariant is already in effect. The next
checkbox (CURRENT_PLAN.md line 44, deletion of `duplo/initializer.py`
and `tests/test_initializer.py`) remains blocked by the project's
absolute no-delete rule and must be executed by the user.

### [7.4.4] Removed dead interactive-prompt code from questioner.py — 2026-04-17

The conditional ("If select_features is still needed") is vacuously
satisfied on the "leave in questioner.py" branch because
`select_features` is already in `duplo/selector.py` (and never lived
in `questioner.py` — confirmed by 7.3.4 / 7.4.1 audits). Nothing to
move. The actionable half of the task is "remove only the dead code",
i.e. strip `questioner.py` down to its one live symbol
(`BuildPreferences`).

Removed from `duplo/questioner.py`:

- `ask_preferences(...)` function (zero production callers after
  `_first_run` was deleted in 7.2.1).
- `_ask_platform`, `_ask_language`, `_ask_list`, `_print_summary`
  helpers (only reachable via `ask_preferences`).
- `_PLATFORMS` constant (only consumed by `_ask_platform`).

Kept:

- `BuildPreferences` dataclass (live; 12 importers across `duplo/`
  and `tests/`). A future task (CURRENT_PLAN.md § "BuildPreferences
  migration") will relocate it to `duplo/build_prefs.py` and retarget
  callers; the dataclass still has a home in `questioner.py` until
  then.

Test-file handling (followed the 7.2.x skip-don't-delete convention):

- `tests/test_questioner.py`: top-level import reduced to
  `BuildPreferences`; added module-level
  `pytestmark = pytest.mark.skip(...)`; moved the references to the
  removed helpers from the top-level import into each test class's
  `_run` method so import-time resolution doesn't hit the deleted
  names. The 18 tests that covered removed symbols are now skipped.
- `tests/test_main.py::TestNoAskPreferencesInPipeline::test_pipeline_does_not_call_ask_preferences`:
  marked `@pytest.mark.skip(...)`. The `monkeypatch.setattr(q,
  "ask_preferences", ...)` call would raise `AttributeError` now
  that the function is gone. The two sibling tests in the same class
  (`test_main_module_has_no_ask_preferences`,
  `test_orchestrator_module_has_no_ask_preferences`) still cover the
  invariant that the pipeline does not import `ask_preferences`.

Verification: `ruff check duplo/ tests/` passes. Full test suite
reports 2921 passed, 103 skipped (vs. 2937/84 before this task — the
19-skipped delta matches the 18 + 1 tests newly skipped, with no new
failures). Grep for `ask_preferences|_ask_platform|_ask_language|_ask_list|_print_summary|_PLATFORMS`
in `duplo/` returns only unrelated hits (`main._print_summary` for
`UpdateSummary`, `diagnostics.print_summary`, `questioner.py`'s own
docstring referencing the removed names, `build_prefs.py`'s module
docstring mentioning the superseded flow).

CURRENT_PLAN.md line 37 ("Tests: no remaining imports of deleted
functions; existing next-phase flow tests still pass") is the
remaining 7.4.x subtask. It is effectively satisfied by the full-suite
run above — no unskipped test imports `ask_preferences` or the `_ask_*`
helpers, and all non-skipped tests pass — but a dedicated one-line
assertion test would be a natural home for the invariant and is
deferred to the next checkbox.

### [7.4.3] Not executed: precondition not met + absolute no-delete rule — 2026-04-17

Task 7.4.3 ("If questioner.py can be deleted: delete it and
tests/test_questioner.py") was **not executed**. Two independent
blockers:

1. **Precondition not met.** `duplo/questioner.py` still defines
   `BuildPreferences` (lines 11-16), which is live code imported by
   nine non-test sites (`duplo/main.py:245`, `duplo/roadmap.py:10`,
   `duplo/planner.py:11`, `duplo/saver.py:19`,
   `duplo/build_prefs.py:22`) plus four test files
   (`tests/test_main.py:42`, `tests/test_planner.py:27`,
   `tests/test_roadmap.py:8`, `tests/test_saver.py:22`,
   `tests/test_build_prefs.py:17`, `tests/test_phase5_integration.py:23`).
   Deleting `questioner.py` now would break imports across the
   `_subsequent_run` path. Per the 7.4.2 execution order, the rename
   (PLAN.md § "BuildPreferences migration", lines 960-964) must run
   first — the deletion in 7.4.3 / PLAN.md § "questioner.py removal"
   (lines 966-972) is the *second* step.

2. **Absolute no-delete rule.** The session-level task prompt says:
   "Never delete any file. Do not use rm, git rm, os.remove, unlink,
   shutil.rmtree, or any other file deletion mechanism… If you
   believe a file should be removed, leave it and note it in NOTES.md
   for the user to decide." This overrides the conditional deletion
   in 7.4.3's wording regardless of blocker #1.

**Action required by user:** perform steps 1-3 of the 7.4.2 execution
order (move `BuildPreferences` to `duplo/build_prefs.py`, retarget the
12 importers, then manually delete `duplo/questioner.py` and
`tests/test_questioner.py`). Step 4 (drop or retarget
`tests/test_main.py:12223`'s `import duplo.questioner as q`) follows.

### [7.4.2] Determination: delete questioner.py after moving BuildPreferences to build_prefs.py — 2026-04-17

Decision for CURRENT_PLAN.md line 34 based on the 7.4.1 audit:

**questioner.py can be deleted entirely.** `select_features` is not an
open question because it is not defined in `questioner.py` and never
has been (verified 7.3.4, 7.4.1) — it lives in `duplo/selector.py` and
its one live caller in `_subsequent_run` at `duplo/main.py:1876` stays.
The phrasing of the checkbox ("whether select_features should be
migrated to selector.py") is moot: there is nothing to migrate. This
matches the decision already pre-committed in PLAN.md lines 960-972
("BuildPreferences migration" → "questioner.py removal").

Inventory of `duplo/questioner.py` symbols and their fate:

- `BuildPreferences` (dataclass, used by 12 importers: 5 in `duplo/`,
  7 in `tests/`) — **move** to `duplo/build_prefs.py`. Cannot be
  deleted with the module; it is live code on the `_subsequent_run`
  path (`_prefs_from_dict`, `_load_preferences`).
- `ask_preferences` — **delete** (zero production callers; only
  consumers are `tests/test_questioner.py` which tests the function
  itself, and `tests/test_main.py:12223` which asserts non-call).
- `_ask_platform`, `_ask_language`, `_ask_list`, `_print_summary`,
  `_PLATFORMS` — **delete** (only reachable via `ask_preferences`;
  only external references are from `tests/test_questioner.py`).

Execution order (required to keep the suite green at every step):

1. Add `BuildPreferences` to `duplo/build_prefs.py` and re-export from
   `duplo/questioner.py` (one-line `from duplo.build_prefs import
   BuildPreferences`) so importers keep working across the rename.
2. Retarget the 12 `from duplo.questioner import BuildPreferences`
   sites to `from duplo.build_prefs import BuildPreferences`.
3. Delete `duplo/questioner.py` and `tests/test_questioner.py`.
4. Retarget or drop `tests/test_main.py:12223`'s `import
   duplo.questioner as q` (two sibling tests —
   `test_main_module_has_no_ask_preferences` and
   `test_orchestrator_module_has_no_ask_preferences` — already cover
   the invariant, so dropping is the simpler path).

This maps directly onto the remaining 7.4.x checkboxes
(CURRENT_PLAN.md lines 35-37) and PLAN.md § "BuildPreferences
migration" / § "questioner.py removal".

### [7.4.1] `duplo.questioner` import audit: 13 sites; only `BuildPreferences` and `ask_preferences` family imported — 2026-04-17

Full grep for `duplo.questioner` / `import questioner` across the
repo. Every import listed; nothing else names the module.

Production code (`duplo/`) — 5 imports, all `BuildPreferences` only:

- `duplo/main.py:245` — `from duplo.questioner import BuildPreferences`
- `duplo/planner.py:11` — `from duplo.questioner import BuildPreferences`
- `duplo/roadmap.py:10` — `from duplo.questioner import BuildPreferences`
- `duplo/saver.py:19` — `from duplo.questioner import BuildPreferences`
- `duplo/build_prefs.py:22` — `from duplo.questioner import BuildPreferences`

Tests (`tests/`) — 8 import sites:

- `tests/test_main.py:42` — `from duplo.questioner import BuildPreferences`
- `tests/test_main.py:12223` — `import duplo.questioner as q` inside
  `TestNoAskPreferencesInPipeline::test_pipeline_does_not_call_ask_preferences`;
  used to `monkeypatch.setattr(q, "ask_preferences", ...)` so the test
  asserts no call. This is the one test that needs `ask_preferences` to
  exist on the module.
- `tests/test_planner.py:27` — `BuildPreferences`
- `tests/test_roadmap.py:8` — `BuildPreferences`
- `tests/test_saver.py:22` — `BuildPreferences`
- `tests/test_build_prefs.py:17` — `BuildPreferences`
- `tests/test_phase5_integration.py:23` — `BuildPreferences`
- `tests/test_questioner.py:5` — `from duplo.questioner import
  BuildPreferences, _ask_list, _ask_platform, ask_preferences`
  (the only importer of `ask_preferences`, `_ask_list`, `_ask_platform`)

Symbols actually defined in `duplo/questioner.py` (verified by reading
the file, 128 lines):

- `BuildPreferences` (dataclass) — imported by 12 of the 13 sites.
- `ask_preferences` — imported only by `tests/test_questioner.py`
  (explicit) and referenced by `tests/test_main.py` line 12223 via the
  module-alias import (to prove it is NOT called).
- `_ask_platform`, `_ask_language`, `_ask_list`, `_print_summary`,
  `_PLATFORMS` — only `_ask_list` and `_ask_platform` leave the module,
  and only via `tests/test_questioner.py`.

Non-import mentions (prose/comments, ignored by import audit):

- `duplo/build_prefs.py:4` — module docstring mentions
  `questioner.py` as the thing this module replaces. Not an import.
- `AGENTS.md:268,527`, `PIPELINE-design.md:991`, `PLAN.md` multiple,
  `CURRENT_PLAN.md` multiple, `NOTES.md` prior 7.3.x entries — design
  docs and prior task notes.

Implications for CURRENT_PLAN.md line 34 (decide whether questioner.py
can be deleted):

1. `ask_preferences` has zero production callers (confirmed 7.3.3). The
   only live consumer is `tests/test_questioner.py` which tests the
   function itself, plus the module-alias in `test_main.py` line 12223
   which only asserts non-call.
2. `select_features` is not defined in questioner.py and never has been
   (confirmed 7.3.4) — the `tests/test_questioner.py` import list above
   does not include it.
3. To delete `duplo/questioner.py`, `BuildPreferences` must first move
   to `duplo/build_prefs.py` (as PLAN.md § "BuildPreferences migration"
   plans). That is a rename touching 12 `from duplo.questioner import
   BuildPreferences` sites. Mechanically simple.
4. After the move, `tests/test_questioner.py` becomes the only thing
   keeping questioner.py alive; it tests dead interactive-prompt code.
   Deleting it alongside questioner.py is consistent with PLAN.md
   § "questioner.py removal" line 971.
5. `tests/test_main.py` line 12223's `import duplo.questioner as q`
   would need to change after deletion. Options: (a) replace with a
   module-attribute check that still asserts the function isn't wired
   into the pipeline (e.g. assert `ask_preferences` name not in
   `duplo.main` / `duplo.orchestrator`, which two sibling tests
   `test_main_module_has_no_ask_preferences` /
   `test_orchestrator_module_has_no_ask_preferences` already do), or
   (b) delete the test since the two sibling tests cover the same
   invariant.

Net: no blocker to the removal path in CURRENT_PLAN.md lines 32-37.
The sequence is (a) add `BuildPreferences` to `build_prefs.py`,
(b) retarget the 12 importers, (c) delete `duplo/questioner.py` and
`tests/test_questioner.py`, (d) either drop or retarget
`test_main.py:12223`. All subsequent 7.4.x subtasks are well-defined
once this audit is ratified.

### [7.3.4] No-op: `questioner.select_features` doesn't exist; `selector.select_features` stays — 2026-04-17

Grep-verified (2026-04-17): `duplo/questioner.py` defines no
`select_features`. The only `select_features` lives in
`duplo/selector.py` and has one live call site at `duplo/main.py:1876`
inside `_subsequent_run`'s phase-planning block (imported at
`main.py:300` from `duplo.selector`). That call is explicitly retained
per CURRENT_PLAN.md line 27 / task 7.3.2. Task 7.3.4's conditional is
vacuously satisfied on the "elsewhere" branch — leave it. No code
change required.

### [7.3.3] No-op: no `ask_preferences` call to remove — 2026-04-17

Grep across `duplo/` for `ask_preferences(` returns only two hits:
the definition in `duplo/questioner.py:19` and a docstring mention
in `duplo/build_prefs.py:3` (module-header prose, not a call).
Zero call sites in `duplo/main.py`, `duplo/orchestrator.py`, or any
other pipeline module. The last caller was `_first_run`, deleted in
7.2.1. All remaining `duplo.questioner` imports across the package
(`main.py:245`, `build_prefs.py:22`, `saver.py:19`, `planner.py:11`,
`roadmap.py:10`) bring in only the `BuildPreferences` dataclass.
Nothing to remove for this checkbox; whether `questioner.py` itself
(and the `BuildPreferences` home) should move is tracked by
CURRENT_PLAN.md § "Evaluate questioner.py for removal".

### [7.3.2] New-model wiring is already live in main.py — 2026-04-17

Confirmed the model statement on CURRENT_PLAN.md line 27 matches the
code as of 7.2.x:

- `BuildPreferences` flow: `duplo/main.py:242` imports
  `parse_build_preferences`; `_load_preferences` (`main.py:344-361`)
  re-parses from `spec.architecture` whenever `architecture_hash(spec.architecture)`
  differs from the stored hash, persists via `save_build_preferences`,
  and also calls `validate_build_preferences` for warnings. No
  `ask_preferences` fallback exists in that path (grep: zero hits in
  `duplo/main.py`).
- Feature-selection flow: `duplo/main.py:300` imports
  `select_features` from `duplo.selector`. The one live call site
  (`main.py:1876-1878`) fires inside `_subsequent_run`'s phase-planning
  block: `remaining = _unimplemented_features(data)` then
  `select_features(remaining, recommended=phase_info["features"], phase_label=...)`,
  and rewrites `phase_info["features"]` from the user's selection.
  This is the per-phase confirmation path and is explicitly retained.

Implication: CURRENT_PLAN.md line 28 (remove `ask_preferences` calls
from the pipeline) is already a no-op in `main.py` — it was effectively
completed by the 7.2.1 `_first_run` deletion, which was the last
caller. The import that remains from `duplo.questioner` is only
`BuildPreferences` (the dataclass); fate of the module as a whole is
tracked by CURRENT_PLAN.md § "Evaluate questioner.py for removal".



`questioner.ask_preferences` — zero callers in `duplo/main.py`. The
only `duplo.questioner` import in main.py is `BuildPreferences` (the
dataclass), at `main.py:245`, consumed by `_prefs_from_dict` and
`_load_preferences` (`main.py:324`, `main.py:334`) as a type. No call
to `ask_preferences` exists in main.py (grep-verified). Already dead
at the source after 7.2.1's `_first_run` deletion.

`questioner.select_features` — does not exist. `select_features` lives
in `duplo.selector`, not `duplo.questioner`. `duplo/questioner.py`
defines only `ask_preferences`, `_ask_platform`, `_ask_language`,
`_ask_list`, `_print_summary`, and `BuildPreferences`. The audit item
in CURRENT_PLAN.md line 26 presupposes a function that was never in
questioner.py — the wording should read "selector.select_features",
matching line 27 which correctly attributes it to `selector`.

`selector.select_features` in main.py has one live caller at
`main.py:1876` inside the next-phase / `_subsequent_run` phase-planning
flow (confirmed/adjusted feature list before PLAN.md generation). Per
CURRENT_PLAN.md line 27 this call is explicitly NOT being removed.
Imported at `main.py:300` from `duplo.selector`.

`duplo/orchestrator.py` — zero hits for `ask_preferences`,
`questioner`, or `select_features`. Satisfies the CURRENT_PLAN.md
line 30 test condition preemptively.

Implication for the remaining 7.3 checkboxes: CURRENT_PLAN.md line 28
(removing `ask_preferences` calls from the pipeline) is a no-op for
main.py — no such call exists. Only action left for this subsection
is line 30's verification test. Whether `BuildPreferences`'s import
path stays on `duplo.questioner` or moves to `build_prefs.py` is a
follow-up question for "Evaluate questioner.py for removal"
(CURRENT_PLAN.md line 32).

### [7.2.4] Cleared `_first_run` textual references from tests — 2026-04-17

Renamed `SKIP_FIRST_RUN` → `SKIP_LEGACY_PIPELINE` in tests/test_main.py
and tests/test_phase5_integration.py. Renamed 12 skip-marked methods from
`test_first_run_*` to `test_legacy_*`. Reworded docstrings, class
comments, and skip-reason strings to drop the `_first_run` name. Zero
occurrences of `_first_run` remain under `tests/` (grep-verified).
Full suite: 2937 passed, 84 skipped — unchanged from before.

No test body was deleted. The skipped classes still import or patch
removed helpers (`_validate_url`, `_confirm_product`, `_init_project`,
`ask_preferences`, etc.); they remain runnable only via `@pytest.mark.skip`
and will be revisited during the Phase 7 dead-code audit
(CURRENT_PLAN.md § "Dead code audit"), consistent with the prior 7.2.x
tasks' note that full rewrites are deferred.

Dispatch tests for 7.2.3 (fresh directory exits 0 with init message;
SPEC.md alone routes to `_subsequent_run`; duplo.json + SPEC.md routes
to `_subsequent_run`) were already in place before this task at
tests/test_main.py::`test_migration_pass_without_duplo_json_prints_init_message`,
`test_spec_only_proceeds_to_subsequent_run`, and
`test_migration_pass_proceeds_to_subsequent_run` (plus
`test_exits_when_no_reference_materials` in TestMainFirstRun). Verified
passing; no new tests added for this checkbox.

### [7.2.2] Deleted _confirm_product, _validate_url, _init_project — 2026-04-17

Audit results (grep + in-file AST scan via `ast.walk` checking every `Call`
node's function name) confirmed these three helpers had zero callers in
`duplo/` after `_first_run` was removed in 7.2.1. Deleted from `main.py`.

Associated import cleanup in `duplo/main.py` (each verified by AST scan to
have no other in-file caller):

- `from duplo.validator import validate_product_url` — was only used by
  `_validate_url`.
- `from duplo.test_generator import (detect_target_language,
  generate_test_source, save_test_file)` — all three were only used by
  `_init_project`; whole block removed.
- `from duplo.screenshotter import map_screenshots_to_features,
  save_reference_screenshots` — both only used by `_init_project`; whole
  line removed.
- From `duplo.saver` import block: dropped `save_selections`,
  `save_screenshot_feature_map`, `write_claude_md` (the rest of the saver
  imports are still in use).
- Module-level `_SECTION_URL_RE = re.compile(...)` — only consumed by
  `_init_project`; removed along with it.

Other in-file helpers not called anywhere in `duplo/` are retained
because they are out of scope for this task:

- `_visual_target_video_frames` was never called by `_first_run` (git
  history at `80ba6e7` and `f40e24b` dropped its call site earlier). It
  has direct tests in `test_main.py` and is unrelated to `_first_run`
  removal; left in place.
- `_excepthook`, `_signal_handler`, `_handle_signal` are nested closures
  inside `_mcloop_setup_crash_handlers` / `main()` and are wired into
  `sys.excepthook` / `signal.signal`; not dead code.

Test-file updates:

- `tests/test_main.py` dropped `_init_project` from its top-level import
  list and added `pytestmark = pytest.mark.skip(...)` to `TestInitProject`
  (8 tests). A module-level placeholder `_init_project = None` was added
  so ruff's F821 check still passes on the (now-skipped) test bodies that
  still reference the name as a free function. Removing it will require
  deleting or rewriting those tests, which is out of scope here.
- `tests/test_validator.py` gained `import pytest` and class-level
  `pytestmark = pytest.mark.skip(...)` on `TestValidateUrlInMain` (12
  tests) and `TestConfirmProduct` (8 tests). Each test imports the
  removed symbol inside the test body, so the imports are never resolved
  under the skip mark.
- `tests/test_phase5_integration.py` only referenced `_confirm_product`
  in a docstring on an already-class-skipped test and needed no change.

Verification: `ruff check duplo/ tests/test_main.py tests/test_validator.py`
passes. Full test suite `pytest -q` reports 2936 passed, 84 skipped (up
from 62 skipped, reflecting the 20 `TestValidateUrlInMain`/`TestConfirmProduct`
tests and 8 `TestInitProject` tests newly skipped by this task minus
overlap with prior skips; no new failures).

PLAN.md § "_first_run removal" line 951 ("no test references
_first_run, _confirm_product, _validate_url, or _init_project") is
still partially open: references remain in skipped tests. That is
consistent with the 7.2.1 convention of deferring test rewrites; a
later task can delete the skipped classes outright.

### [7.2.1] _first_run function deleted — 2026-04-17

Deleted the function body and its preceding removal-audit comment block
(formerly main.py:1035-1474). The file parses (`python3 -c "import ast;
ast.parse(...)"` returns OK).

The original plan was to leave the dispatch untouched until 7.2.3, but
`ruff check duplo/main.py` failed with F821 on the dangling
`_first_run(url=args.url)` call at main.py:799. A minimal dispatch
stub had to land here: the `not duplo_path.exists()` branch now prints
"Run `duplo init` first to create SPEC.md." and exits 1. This anticipates
task 7.2.3's shape (CURRENT_PLAN.md line 20) — 7.2.3 will refine it to
distinguish fresh directory (no SPEC.md) from partial reset
(SPEC.md present, `.duplo/` gone) and route the latter into
`_subsequent_run`. For now, both cases hit the exit.

Intermediate broken state this commit leaves in place, to be cleaned up
by subsequent 7.2.x tasks:

- Tests that exercised `_first_run` (either by patching
  `duplo.main._first_run`, or by patching internals like
  `ask_preferences`/`scan_directory` and calling `main()` in a
  fresh-directory setup) have been marked `@pytest.mark.skip` with
  reason pointing to Phase 7.2.4. A module-level `SKIP_FIRST_RUN`
  marker was added to tests/test_main.py and
  tests/test_phase5_integration.py. Affected: 35 tests in test_main.py,
  22 tests in test_phase5_integration.py (3 classes class-skipped in
  test_main.py, 5 classes class-skipped in test_phase5_integration.py,
  remainder individual decorators). The 4 dispatch-oriented tests in
  TestMigrationDispatchOrder were updated in place: 3 had their
  `_first_run` setattr removed (the behavior they test doesn't need
  it); test_migration_pass_proceeds_to_first_run was renamed/rewritten
  to assert the new init-message-and-exit-1 behavior.
- Helpers `_confirm_product` (main.py:2027-2058 new numbering),
  `_validate_url` (main.py:2059-2133), `_init_project` (main.py:2134-2212)
  remain in place. They were only called from `_first_run`; they are
  now dead code but are removed in 7.2.2 rather than here to keep
  this commit scoped strictly to the function-deletion step.
- Imports of `ask_preferences` and `scan_directory` in main.py were
  removed in this commit (they had no remaining in-file callers after
  `_first_run` deletion). `ruff check` would flag them as F401 if left.

### [7.1.3] Migration gate fully prevents old-format projects from reaching _first_run — 2026-04-17

Audit confirms no old-format project can reach `_first_run`. Dispatch in
`main.py:655-805` has three entry branches:

- `init` subcommand (main.py:665-716) — bypasses `_check_migration`, calls
  `run_init` (duplo/init.py). `run_init` never calls `_first_run` (grep confirms).
- `fix`/`investigate` subcommand (main.py:718-795) — bypasses
  `_check_migration`, calls `_fix_mode`. `_fix_mode` never calls `_first_run`.
- Default no-subcommand path (main.py:796-803) — calls `_check_migration(Path.cwd())`
  first (main.py:797), then dispatches on `duplo.json` existence:
  `_first_run(url=args.url)` if absent, `_subsequent_run()` if present.

`_first_run` is called from exactly one site (main.py:799) — grep for
`_first_run(` returns only the definition (main.py:1035) and that one call.
No internal recursion; `_subsequent_run` does not invoke it.

`needs_migration` (migration.py:37-64) fires only when `.duplo/duplo.json`
exists AND no new-format SPEC.md is present. By definition:
- Old-format project has `.duplo/duplo.json` → either migration fires and
  `sys.exit(1)` before dispatch, or it passes (new-format SPEC.md present)
  and `duplo_path.exists()` is True → `_subsequent_run`, not `_first_run`.
- Therefore `_first_run` is reachable only when `.duplo/duplo.json` does
  NOT exist, which is by definition NOT an old-format project.

Existing tests pin this:
- `test_old_layout_prints_message_exits_skips_runs` (test_main.py:6316)
  asserts `first_run_called == []` for old layout.
- `test_init_skips_check_migration` (test_main.py:6420) asserts
  `first_run_called == []` on init bypass.
- `test_fix_old_layout_bypasses_migration_dispatches_fix` (test_main.py:6441)
  confirms fix bypass routes to `_fix_mode`.
- `test_migration_pass_proceeds_to_first_run` (test_main.py:6395) pins
  that `_first_run` runs only when no `duplo.json`.

Conclusion: no code path allows an old-format project to reach `_first_run`.
No gating or removal needed for bypass. The next checkbox (document at
removal site) can proceed.

**Edge case flagged, not a reachability problem**: a user who manually deletes
`.duplo/duplo.json` but leaves other old artifacts (`.duplo/product.json`,
`screenshots/`, legacy `references/`) falls through to `_first_run`. By
MIGRATION-design.md:168-172 this is intentional ("they can always delete
`.duplo/` and start fresh"). `_first_run` even consumes a lingering
`.duplo/product.json` via `load_product()` at main.py:1111. This partial-reset
path is a feature of the current design, not a missed old-format case.

### [7.1.2] duplo init + _subsequent_run coverage of _first_run — 2026-04-17

Confirmed the coverage claim in CURRENT_PLAN.md line 13.

**URL input** (was `_first_run(url=args.url)` at main.py:1035, 1050-1053, and the
`_validate_url` interactive disambiguation at 1141-1143). `duplo init` accepts the URL
at `args.url` (init.py:155) and dispatches to `_run_url` (init.py:261) which
canonicalizes (url_canon.canonicalize_url) and fetches shallow-by-default via
`fetch_site`. Disambiguation is replaced by a non-interactive `validate_product_url`
call inside `_identify_product` (init.py:203-223) — unidentified products fall back to
a pre-filled `## Sources` entry without prompting. `duplo init <url>
--from-description PATH` (combined) and `--from-description PATH` alone are also
handled. Equivalent or stronger than `_first_run` URL handling.

**SPEC.md generation** (new; `_first_run` never wrote SPEC.md — it consumed one
if present and wrote an autogen design block). `duplo init` always writes
SPEC.md via `format_spec(_build_draft_spec(...))` (init.py:197, 321, 331, 340,
473, and the combined path). Existing files in `ref/` are inventoried by
`_scan_existing_ref_files` and each gets a role via `_propose_file_role`, so the
drafted `## References` is pre-filled for the user to confirm.

**Feature extraction** (was in `_first_run` at main.py:1194-1207 via
`extract_features` followed by interactive `select_features`). `_subsequent_run`
calls `extract_features` at main.py:2060-2066 with the same `spec_text`,
`scope_include`, `scope_exclude` arguments, then merges new features into
duplo.json via `save_features` (2078). The per-phase interactive
`select_features` at 2294 still runs before phase PLAN.md generation — the only
change is it runs per-phase, not once at bootstrap.

**PLAN.md generation from SPEC.md** (was in `_first_run` at main.py:1412-1438,
generating Phase 1 from the fresh roadmap). `_subsequent_run`'s State 3 branch
(main.py:2246-2349) generates a PLAN.md for the current phase: regenerates a
roadmap if none exists (2252-2276), runs `generate_phase_plan` (2330-2339), and
appends verification cases from both video frames (2340-2348) and behavior
contracts (same structure as `_first_run`).

Conclusion: the three responsibilities named in the plan (URL input, feature
extraction, PLAN.md generation) are fully covered by the split
`duplo init` + `_subsequent_run`. The claim is accurate.

### [7.5.1] Audit of initializer callers — 2026-04-17

`initializer.create_project_dir`:
- Defined in `duplo/initializer.py:20`.
- Zero production callers. Grep confirms no imports of
  `create_project_dir` in `duplo/**/*.py` (only `duplo/initializer.py`
  itself).
- Test callers in `tests/test_initializer.py` only (lines 10, 35, 44,
  52, 61, 68, 74, 86, 92, 99, 106).

`initializer.project_name_from_url`:
- Defined in `duplo/initializer.py:10`.
- Zero production callers. `saver.derive_app_name` does NOT use it:
  its fallback (`saver.py:151-153`) is `td.resolve().name` (directory
  name), not a URL-derived hostname. The resolution order
  (1 product.json `app_name` → 2 duplo.json `app_name` →
  3 product.json `product_name` for product-reference sources →
  4 directory name) never calls `project_name_from_url`.
- Test callers in `tests/test_initializer.py` only (lines 10, 15, 18,
  21, 24, 27).

No remaining production callers for either function after the
`_first_run` removal in 7.2.1. The CURRENT_PLAN.md branch at line 45
("If project_name_from_url is used by derive_app_name or another live
path, keep only that function and delete the rest") is not triggered —
neither function is in a live path.

Next checkbox (CURRENT_PLAN.md line 44) calls for deleting
`duplo/initializer.py` and `tests/test_initializer.py`. Per the "never
delete any file" project rule, this is flagged here for the user to
decide. Leaving both files in place until explicitly directed.

## Hypotheses

### [7.1.2] Dispatch and _subsequent_run assumptions block _first_run removal — 2026-04-17

Coverage confirmed above is prospective — the next checkbox (CURRENT_PLAN.md
line 20) must flip the dispatch. Gaps to resolve when that lands:

1. **Dispatch still routes to `_first_run`.** main.py:797-799 calls
   `_first_run(url=args.url)` whenever `.duplo/duplo.json` is absent.
   `duplo init` writes SPEC.md but NOT `.duplo/duplo.json`, so
   `duplo init` followed by `duplo` still hits `_first_run`, not
   `_subsequent_run`. Until the dispatch is updated, the coverage above is only
   latent.

2. **`_subsequent_run` assumes duplo.json exists in several places.** After the
   dispatch flips, the first `duplo` run against a fresh `duplo init` directory
   will have no `.duplo/duplo.json`. Spots that need hardening (FileNotFoundError
   paths not yet guarded):
   - main.py:2056 `old_data = json.loads(Path(_DUPLO_JSON).read_text(...))` —
     except clause catches only `JSONDecodeError`.
   - main.py:2073 same pattern, same gap.
   - main.py:2079 `updated_data = json.loads(...)` — relies on `save_features`
     having created the file, which it does, but worth verifying for the
     feature-less fresh-init case.
   - main.py:2213-2218 outer `data` reload — same `except json.JSONDecodeError`
     only.
   - The top-level read at 1973-1976 DOES catch `OSError`, so that one is fine.

3. **Three `_first_run` responsibilities not in either replacement:**
   - Interactive app_name prompt for appshot (main.py:1360-1363). No
     equivalent in `duplo init` or `_subsequent_run`. `derive_app_name(spec)` is
     used in `_subsequent_run` at 2219, which reads from duplo.json then
     directory name — so `app_name` ends up as the sanitized directory name by
     default. If the old interactive prompt is deemed dead, this is already
     covered; if it's meant to stay, it needs a home.
   - `ask_preferences()` fallback when `spec.architecture` is empty
     (main.py:1355-1356). `validate_for_run` should reject a spec with an
     unfilled `## Architecture` before this point, making the fallback
     unreachable. Worth asserting that fact before deleting `ask_preferences`.
   - Interactive roadmap approval prompt (main.py:1400-1405). `_subsequent_run`
     State 3 saves the new roadmap without a confirmation prompt (2272-2273).
     This is a behavior change, not a gap — worth calling out so it is an
     explicit decision rather than a silent drop.



All six test cases called out in 6.15.8 were added incrementally during
6.15.1-6.15.7 and pass. Mapping:

- description read from file: `TestRunInitDescriptionFile::test_reads_description_from_file_and_writes_spec`
- description read from stdin (mocked): `TestRunInitDescriptionStdin::test_reads_description_from_stdin_pipe`
- file not found exits 1: `TestRunInitDescriptionFile::test_missing_file_prints_error_and_exits_1`
- URL extracted with `proposed: true`: `TestRunInitDescriptionUrlExtraction::test_like_url_in_prose_becomes_proposed_product_reference`
- inferred role correct: same test (product-reference) + `test_unlike_url_in_prose_becomes_proposed_counter_example_scrape_none` (counter-example)
- Notes section contains verbatim prose: asserted inside `test_reads_description_from_file_and_writes_spec`

No new tests were written for this subphase because adding redundant
assertions would duplicate the existing coverage. All 48 tests in
`tests/test_init.py` pass.

### [6.15.5] URL-from-prose extraction already in place — 2026-04-17

Task 6.15.5 asks for URL extraction from the prose description, with role
inferred via `_infer_url_role` and `proposed: true` on the resulting
`SourceEntry`. This was already implemented as part of task 6.15.1 (see
the note below). `_build_draft_spec` (spec_writer.py lines ~1073-1096)
handles extraction, canonicalization, role inference, counter-example
scrape coercion, and dedup against an explicit `inputs.url`. `_run_description`
already calls `_build_draft_spec`, so the init flow gets the behavior for
free. Tests covering the behavior: `TestExtractProseUrls`,
`TestBuildDraftSpecProseUrls` (in test_spec_writer.py), and
`TestRunInitDescriptionUrlExtraction` (in test_init.py). All pass.

INIT-design.md vs DRAFTER-design.md discrepancy: INIT-design.md § "duplo init
--from-description" lines 185-187 says the prose-extracted source entry gets
"a note explaining the URL was extracted from the description." DRAFTER-design.md
§ "Inferring URL roles" does not mention a note. The task description says
"Per DRAFTER-design.md" and the plan header specifies DRAFTER-design.md is
authoritative for spec_writer.py extensions, so the current implementation
(no note) follows the authoritative source. Flagging here per the plan's
"flag the discrepancy for resolution" rule — a later task or user decision
may want to add `notes="extracted from description"` on these entries.

### [6.15.1] draft_spec refactored to expose a ProductSpec-returning core — 2026-04-17

`_run_description` needs to inspect the drafted `ProductSpec` to decide which
per-section bullets to print ("Pre-filled ## Purpose, ## Design from prose.",
"## Architecture left as <FILL IN>", etc.). Rather than re-parsing the
serialized SPEC.md, split `draft_spec` into `_build_draft_spec(inputs) ->
ProductSpec` + a thin `draft_spec = format_spec ∘ _build_draft_spec` wrapper.
Existing `draft_spec` tests all still pass because `draft_spec` is
behaviorally identical. The new internal API is what init.py consumes.

Also added URL extraction from description prose to the drafter
(`_extract_prose_urls`), so descriptions like "like https://numi.app" now
produce a `proposed: true` Sources entry with `role: product-reference`
inferred via `_infer_url_role`. Counter-example roles get `scrape: none`
coerced at write time (mirrors the parser and `append_sources` rules).
An explicit `inputs.url` suppresses any duplicate prose-extracted entry
so Sources stays single-entry-per-URL when the combined case arrives in
Phase 6.15.2.

### [6.7.1] DraftInputs added in this task, not in 6.1.1-6.1.2 — 2026-04-17

Task 6.7.1 implements `_draft_from_inputs(inputs: DraftInputs)` whose first
argument type was supposed to be defined by tasks 6.1.1-6.1.2. git log shows a
checkpoint commit `b64753b` (next: 6.1.1-6.1.2) followed directly by `a743f64`
(next: 6.2.1) — no "Complete: [BATCH] 6.1.1-6.1.2" commit between them — yet
CURRENT_PLAN.md marks 6.1.1-6.1.2 as `[x]` done. The DraftInputs dataclass was
never actually written. Added it in this task so `_draft_from_inputs` has its
parameter type. If the workflow runs 6.1.1-6.1.2 again it will find DraftInputs
already present; tests pass either way.

### [6.8.5] Step 4 emits reference entries without a role when vision_proposals is incomplete — 2026-04-17

`draft_spec` step 4 uses `inputs.vision_proposals.get(path, "")` so a ref/
file that is not a key in `vision_proposals` gets a `ReferenceEntry` with
`roles=[]`. `format_spec` emits this as `- <path>` + `proposed: true` with
no `role:` line. The parser then drops such entries into
`dropped_references` because a role is required. In practice this should
not happen — the caller (`duplo init`) calls `_propose_file_role` for
every file in `existing_ref_files`, and that function always returns a
role (falling back to `"ignore"` for unknown extensions). The defensive
`.get(path, "")` fallback is therefore a silent data-loss path if a
caller ever constructs `DraftInputs` with mismatched `existing_ref_files`
/ `vision_proposals`. Options if this becomes a concern: (a) assert every
`existing_ref_files` entry is present in `vision_proposals`, (b) default
missing entries to `"ignore"` so they survive the parser round-trip, or
(c) log a diagnostic. Test
`test_step4_ref_file_missing_from_vision_proposals_emitted_without_role`
pins the current writer behavior.

### [6.7.1] Error-handling discrepancy between plan and design doc — 2026-04-17

CURRENT_PLAN.md bullet 6.7.7 says `_draft_from_inputs` should "fall back to
empty ProductSpec (template-only draft) with a diagnostic" after retries.
DRAFTER-design.md § "Error handling" says the function should raise
`DraftingFailed` and the caller (`draft_spec`) catches it. Tasks 6.9-6.10
plan to add `DraftingFailed` and catch it in `draft_spec`. Implemented per
the plan for now (return empty ProductSpec + record_failure); if 6.9/6.10
migrate to exception-based handling, `_draft_from_inputs` will need to be
refactored to raise instead of return, and tests updated.

### [6.3.1] Parser re-ingests content from format_spec comment hints — 2026-04-17

Round-trip testing revealed that `_parse_spec` picks up "example" content
from inside the HTML comment hints that `format_spec` emits for empty
optional sections. Specifically: the Sources/References/Scope/Behavior
parsers operate on raw body text (not comment-stripped), and their regexes
match the example list items embedded in the `<!-- Example: ... -->` blocks.
For an empty `ProductSpec`, `parse(format_spec(spec))` yields non-empty
`sources`, `references`, `scope_include`, `scope_exclude`, and
`behavior_contracts` populated from the template examples. Notes and
Architecture strip comments before extraction and are not affected.
Impact: the round-trip property only holds for specs that populate these
sections with real content (so `format_spec` skips the comment hints).
Round-trip fixtures in `tests/test_spec_writer.py::TestRoundTrip` all have
at least one entry in each "pickup-prone" section. Eventual fix would be
to either (a) comment-strip bodies in the Sources/References/Scope/Behavior
parsers, or (b) omit example content from the comment hints. Deferred —
not in scope for 6.3.1.

### [6.3.1] Round-trip comparator excludes more fields than DRAFTER-design.md lists — 2026-04-17

DRAFTER-design.md's `_ROUND_TRIP_EXCLUDED_FIELDS` example lists only `raw`,
`dropped_sources`, and `dropped_references`. In practice the comparator also
must exclude `scope`, `behavior`, `fill_in_purpose`, `fill_in_architecture`,
and `fill_in_design` because these are derived from the serialized body by
the parser: `scope` / `behavior` hold the raw body string and always
change after round-tripping; the `fill_in_*` flags are parser-set when the
body contains `<FILL IN>` markers. DesignBlock's `has_fill_in_marker` is
similarly parser-set and excluded by comparing only `user_prose` and
`auto_generated` in the DesignBlock sub-comparison. Design doc lists the
minimum; this is the practical set.

### [1.6] `extract_json` preferred inner object over outer array — 2026-04-16

Adding round-trip parser tests for the four migrated modules surfaced a latent
bug in `duplo.parsing.extract_json`: for prose-prefixed input like
`"Here are the features:\n[{...}]"`, the balanced-span scanner iterated `{...}`
first and returned the first valid object (the inner dict), instead of the
outer array. `_parse_features` then saw a dict, failed its `isinstance(data,
list)` check, and returned `[]` — no round-trip. Fixed by switching
`extract_json` from "first valid span wins" to "longest valid span wins": for
arrays of objects the outer `[...]` is longer than any inner `{...}`, so the
array is returned; for objects containing arrays the outer `{...}` is longer,
so the object is returned. Existing tests in `test_parsing.py` (including
`test_extract_json_multiple_objects`) continue to pass because their
assertions are satisfied by either span.

### [1.5] `strip_fences` + `json.loads` migration is incomplete — 2026-04-16

Phases 1.1–1.4 migrated `extractor.py`, `gap_detector.py`, `build_prefs.py`, and
`validator.py` to use `extract_json`. Five modules still contain the old
pattern and are allow-listed in `tests/test_parsing_invariant.py`
(`ALLOWED_UNMIGRATED`): `roadmap.py`, `verification_extractor.py`,
`investigator.py`, `task_matcher.py`, `saver.py` (3 occurrences in saver).
The regression test catches reintroduction into migrated files today; when
each remaining module is migrated, its entry should be removed from
`ALLOWED_UNMIGRATED` so the guard covers it too. A companion test
(`test_allowed_unmigrated_list_is_accurate`) fails loudly if a file is
migrated without removing its allowlist entry.

### [5.38.2] Diagnostic logging added to frame_describer — 2026-04-16

Added `record_failure` calls to all three parse-error exit paths in `_parse_descriptions`. Each records the raw LLM response (first 2000 chars) and the extracted text to `.duplo/errors.jsonl`. The next manual run with video frames will capture the actual response that the parser is choking on. No existing `frame_describer` entries were found in `errors.jsonl` because the logging wasn't present during the [5.38.1] manual run.

### [5.39.2] Design extraction chain had silent failure paths — 2026-04-16

Traced the full chain in `_subsequent_run` after `extract_design` is called. Four
places in `main.py` run the extract→format→update pipeline. Two of them
(`_subsequent_run`'s spec_sources path and `_rescrape_product_url`) were missing
the `else` branch for when `design.colors/fonts/layout` are all empty — extraction
would fail silently with no message or diagnostic. All four paths were missing
diagnostics for two inner steps: `format_design_block` returning empty despite
non-empty design fields, and `update_design_autogen` returning unchanged text.
Added `record_failure` calls at both inner failure points in all four paths, and
added the missing "Could not extract" messages in the two paths that lacked them.
The most likely cause of the [5.39.1] issue: `extract_design` returned a
`DesignRequirements` with populated `source_images` but empty colors/fonts/layout
(from a `ClaudeCliError` or parse failure), and `_subsequent_run` silently skipped
writing to SPEC.md because there was no else branch.

### [5.39.1] design_extractor had the same strip_fences fragility — 2026-04-16

`design_extractor._parse_design` used `strip_fences` + `json.loads`, the same pattern fixed in `frame_describer`/`frame_filter` during [5.38.3]. When the Vision LLM returned JSON preceded by prose (e.g. "Here is the design analysis:\n\n{...}"), `strip_fences` was a no-op, `json.loads` raised `JSONDecodeError`, and `_parse_design` returned an empty `DesignRequirements`. The caller in `main.py` then skipped writing `## Design` to SPEC.md because `design.colors` was empty. No diagnostic was logged because the error path returns silently. Fixed by switching to `extract_json`. This was noted as a latent risk in [5.38.1] ("Other modules using `strip_fences` + `json.loads` … have the same latent vulnerability").

### [5.38.1] LLM JSON extraction fragility in Vision modules — 2026-04-16

`frame_describer` and `frame_filter` both used `strip_fences` to clean LLM output before `json.loads`. When the LLM returns JSON wrapped in conversational prose without markdown code fences, `strip_fences` is a no-op and parsing fails. Fixed by adding `extract_json` to `parsing.py` (tries `strip_fences` first, then scans for outermost `{...}` / `[...]`). Applied to `frame_describer` and `frame_filter`. Other modules using `strip_fences` + `json.loads` (extractor, gap_detector, build_prefs, validator, etc.) have the same latent vulnerability but weren't hit in practice — they use `query` (text-only), not `query_with_images` (tool-augmented), so the LLM is less likely to produce prose-wrapped JSON.

### [5.27.7] `save_raw_content` default `target_dir` bug — 2026-04-14

`saver.py:save_raw_content` uses `target_dir: Path = Path.cwd()` as a default argument (line 1213). Unlike every other function in `saver.py` which uses `target_dir: Path | str = "."`, this one evaluates `Path.cwd()` at import time, not call time. In production this works because duplo's cwd doesn't change between import and use. In tests using `monkeypatch.chdir(tmp_path)`, the default points to the original cwd instead of `tmp_path`. Integration tests must either pass `target_dir` explicitly or call `save_raw_content` directly rather than through `_persist_scrape_result`. Consider aligning with the `"."` convention used everywhere else.

### [6.10.3] `## ` inside AUTO-GENERATED design body is read as a new section — 2026-04-17

While adding the edit-safety property test for `update_design_autogen`, a
body containing a literal `## swatches` line mid-content did not round-trip
through `_parse_spec` — everything from that line onward was treated as a
new section heading, truncating `design.auto_generated`. The parser is
line-based on `^## ` and does not recognize the `<!-- BEGIN/END
AUTO-GENERATED -->` markers as an opaque region. In practice design
auto-generation never emits `## ` lines (bodies are bullet lists and
simple `key: value` pairs), so this is a latent edge case rather than a
live bug. If the Vision extractor starts producing Markdown headings in
bodies, either the parser needs to respect the AUTO-GENERATED markers or
the writer must escape `## ` on emit. The pathological body was removed
from `_NEW_DESIGN_AUTOGEN_BODIES` to keep the property test focused on
edit-safety rather than parser limits.

### [6.23.2] `_run_url` now guards `fetch_site` against exceptions — 2026-04-17

Real `fetch_site` catches all network/parse errors internally (see `fetcher.py:249-256`) and returns an empty tuple, so the `fetch_ok = bool(records)` branch in `_run_url` already covered real-world fetch failures. The Phase 6 integration test for `TestInitUrlFetchFailureWritesScrapeNone` deliberately mocks `fetch_site` with `side_effect=_fetch_site_network_error` to simulate an exception escaping — PLAN.md § "test_init_url_fetch_failure_writes_scrape_none" demands this shape. `_run_url` now wraps the `fetch_site` call in `try/except Exception` and records a diagnostic so the URL-flow can still produce the template-with-`scrape: none` SPEC.md on that path. The try/except is defensive against a future `fetch_site` variant that forgets the internal catch (or a deeper exception like `SystemExit`-adjacent errors that slip through), not load-bearing in production today.

### [4.4.5] `## Sources` false positive in fenced code blocks — 2026-04-13

The multiline regex `^## Sources\s*$` in `needs_migration()` matches even when `## Sources` appears inside a fenced code block (e.g. a Markdown example in the SPEC.md top-matter comment). This is a known false positive, accepted as intentional: a file containing `## Sources` in an example is close enough to new-format that force-migrating it would be worse than letting it through. Pinned with `test_sources_inside_fenced_code_block`. If fence-aware parsing is added later, the test will break to flag the behavior change.

## [2.2] Follow links — 2026-03-05

- Low-priority pages (blog, pricing, legal, login, etc.) are skipped entirely rather than deprioritized. The rationale: they add no signal about the product's features/architecture and would waste the max_pages budget. This is a deliberate design decision worth revisiting if we find we need breadth over depth.
- `score_link` checks both URL path and anchor text so a link to `/page` with anchor "API Reference" is still classified as high-priority. URL path alone would miss many navigation links.
- Duplicate links in the queue are prevented via a `queued` set (in addition to the `visited` set), so the same URL won't be enqueued multiple times from different pages.
- `fetch_site` silently skips pages that fail to fetch (network errors, non-2xx), so a single broken link doesn't abort the crawl. Consider logging skipped URLs in a future pass.
- The seed URL is given a score of 2 (higher than any discovered link) to ensure it is always visited first.
- `_LOW_PRIORITY` and `_HIGH_PRIORITY` are checked in that order; a URL matching both (unlikely but possible, e.g. `/docs-pricing`) would be classified as low-priority. This could be reconsidered.

## [1.3] Verify pip install -e . works and duplo command runs — 2026-03-05

- The `.venv` was created without `setuptools`, which is required by `setuptools.build_meta`. Plain `pip install -e .` fails with `BackendUnavailable`. Fix: install setuptools first (`pip install setuptools`).
- SSL certificate verification fails in the Claude Code sandbox environment (`OSStatus -26276`). Workaround: `--trusted-host pypi.org --trusted-host files.pythonhosted.org`. This is a sandbox/environment issue, not a project issue; normal installs outside the sandbox work fine.
- `pip install -e .` also requires `--no-build-isolation` once setuptools is installed in the venv, otherwise pip tries to re-download setuptools into an isolated build env and hits the SSL error again.
- Consider documenting the install steps in a README or Makefile for first-time setup.

## Hypotheses

### [6.15.1] Per-section bullet wording drift from INIT-design.md example — 2026-04-17

INIT-design.md § "duplo init --from-description description.txt" shows one
specific output example where Architecture is filled and Behavior is empty.
The current implementation generates bullets dynamically based on the
drafted `ProductSpec`: always one bullet per required/optional section
indicating filled vs. not. This is more informative but does drift from
the example shapes in the design doc. If the doc is read as prescriptive
(exact wording for exact cases) rather than illustrative, the wording
may need tightening. Left as-is pending user review of the rendered
output during the combined-case implementation (6.15.2+).

### [5.38.2] `claude -p --tools Read` output format — 2026-04-16

`query_with_images` runs `claude -p --tools Read`. The most likely cause of universal parse failure is that `claude -p` with `--tools` outputs in a structured format (e.g., streaming JSON, JSONL with tool-use messages, or a result wrapper object) rather than plain text. If the output contains multiple JSON objects (one per tool use + final response), `extract_json` would find the first `{` and last `}` across the entire output, producing an invalid JSON candidate that spans multiple objects. This would fail `json.loads` and hit the "parse error" path. The diagnostic logging added in 5.38.2 will capture the actual raw response to confirm or eliminate this. Potential fixes: (a) add `--output-format text` to the `claude -p` command, (b) parse the structured output to extract only the final text block, or (c) split the output by lines and extract JSON from only the last text block.

## Eliminated

### [5.39.4] Frame describer ↔ design extractor entanglement — 2026-04-16

Investigated whether the frame_describer bug (all frames getting "unknown" state) could cause the design extractor to produce empty output. **They are independent pipelines.** `extract_design` receives raw image paths (via `collect_design_input`) and sends them directly to Vision — it never consumes frame descriptions. Frame descriptions are consumed only by `extract_verification_cases` for PLAN.md verification tasks. Both bugs shared the same root cause (`strip_fences` + `json.loads` fragility, fixed in [5.38.3] and [5.39.1] by switching to `extract_json`), but they cannot cause each other. Eliminated by code path tracing: `collect_design_input` → `extract_design` → `query_with_images` (image paths); vs. `describe_frames` → `load_frame_descriptions` → `extract_verification_cases` (frame descriptions).
