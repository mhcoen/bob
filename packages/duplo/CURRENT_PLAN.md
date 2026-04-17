# Phase 7: Cleanup

Removes the legacy code paths that the new SPEC-driven flow has superseded. Updates documentation. No new functionality. Per REDESIGN-overview.md: "only the new model is supported; code is clean."

Design reference: REDESIGN-overview.md section "Implementation phasing" Phase 5 (cleanup). Also informed by PIPELINE-design.md (which identifies legacy paths to remove) and the "What stays the same" section of REDESIGN-overview.md (which identifies what must NOT be touched).

This phase is entirely about deletion, simplification, and documentation. No new features. Every removal must be preceded by a caller audit to confirm no live code path depends on the removed code.

## Legacy _first_run removal

- [x] Audit all callers of _first_run in duplo/main.py
  - [x] Grep the codebase for references to _first_run. Identify every call site. Per PIPELINE-design.md: _first_run removal is deferred to cleanup because the new code needs time to prove itself.
  - [x] Confirm that duplo init fully replaces _first_run for new projects: _first_run handled URL input, interactive feature selection, and first PLAN.md generation. duplo init handles URL input and SPEC.md generation; _subsequent_run handles feature extraction and PLAN.md generation from SPEC.md.
  - [x] Confirm that the migration gate (Phase 4) prevents any old-format project from reaching _first_run. If a code path can still reach _first_run, document it and decide whether to gate or remove it.
  - [x] Document findings in a comment block at the removal site.

- [x] Remove _first_run and its direct dependencies from duplo/main.py
  - [x] Delete the _first_run function.
  - [x] Delete any helper functions in main.py that are ONLY called by _first_run (audit each helper for other callers before removing).
  - [x] Update the no-subcommand dispatch in main() to remove the _first_run branch. After migration check passes and SPEC.md exists, always go to _subsequent_run. If SPEC.md does not exist and .duplo/duplo.json does not exist (fresh directory, not an old project), print a message directing the user to run duplo init and exit 0.
  - [x] Tests: duplo in a fresh directory (no .duplo/, no SPEC.md) prints "run duplo init" message and exits 0; duplo in a directory with SPEC.md proceeds to _subsequent_run; no test references _first_run.

## Legacy interactive prompts removal

- [x] Remove interactive feature selection from the pipeline entry path
  - [x] Audit callers of questioner.ask_preferences and questioner.select_features in main.py.
  - [x] The new model: BuildPreferences come from spec.architecture via build_prefs.parse_build_preferences (Phase 5). Feature selection for phase planning uses the existing selector.select_features in the next-phase flow, which is NOT being removed.
  - [x] Remove any call to questioner.ask_preferences from the pipeline. BuildPreferences are now derived from SPEC.md, not from interactive prompts.
  - [x] If questioner.select_features is still called during _first_run only, its removal is covered by the _first_run removal above. If it is called elsewhere, leave it.
  - [x] Tests: no import of ask_preferences remains in main.py or orchestrator.py; pipeline runs without interactive prompts when given a valid SPEC.md.

- [x] Evaluate questioner.py for removal
  - [x] Audit all imports of questioner across the codebase.
  - [x] If ask_preferences has no remaining callers after _first_run removal, and select_features is only used via selector.py (or the next-phase flow), determine whether questioner.py can be deleted entirely or whether select_features should be migrated to selector.py.
  - [x] If questioner.py can be deleted: delete it and tests/test_questioner.py.
  - [x] If select_features is still needed: keep the function, move it to selector.py (or leave in questioner.py), remove only the dead code.
  - [x] Tests: no remaining imports of deleted functions; existing next-phase flow tests still pass.

## Legacy initializer removal

- [ ] Evaluate duplo/initializer.py for removal
  - [x] Audit all callers of initializer.create_project_dir and initializer.project_name_from_url.
  - [x] _first_run used initializer to create a target project directory and git init it. Under the new model, the user creates their own project directory and runs duplo init from it. duplo init does NOT call create_project_dir.
  - [ ] If no remaining callers exist after _first_run removal: delete duplo/initializer.py and tests/test_initializer.py.
  - [ ] If project_name_from_url is used by derive_app_name or another live path, keep only that function and delete the rest.
  - [ ] Tests: no remaining imports of deleted functions.

## Legacy scanner heuristics removal

- [ ] Remove file-relevance scoring from duplo/scanner.py
  - [ ] Phase 5 already changed scan_directory to point at ref/ and drop relevance heuristics. Confirm that no legacy scoring code remains (image dimension checks, file size thresholds, etc.).
  - [ ] If any legacy scoring functions or constants remain in scanner.py that are no longer called, delete them.
  - [ ] Tests: no reference to removed scoring functions; scan_directory works purely on ref/ file inventory.

## Legacy URL-in-text-file scanning removal

- [ ] Remove URL extraction from arbitrary text files in the project directory
  - [ ] Under the old model, duplo scanned the project root for text files containing URLs and used them as scrape targets. Under the new model, URLs live exclusively in SPEC.md Sources section.
  - [ ] Audit main.py and any other module for code that scans the project root for text files containing URLs. Remove that code.
  - [ ] Confirm that _subsequent_run reads URLs only from format_scrapeable_sources(spec), not from file scanning.
  - [ ] Tests: placing a text file containing a URL in the project root does NOT cause duplo to scrape that URL; URLs come only from SPEC.md.

## Compatibility layer removal

- [ ] Remove Phase 3 compatibility shims for spec.references and spec.design string access
  - [ ] Phase 3 changed spec.references from str to list[ReferenceEntry] and spec.design from str to DesignBlock. If any compatibility properties or helper methods were added to ProductSpec to support old-style string access, remove them now.
  - [ ] Grep for any call site that accesses spec.references as a string or spec.design as a string. If found, update to use the structured types.
  - [ ] Tests: no string-access patterns remain; all callers use list[ReferenceEntry] and DesignBlock.

## Dead code audit

- [ ] [BATCH] Run a dead-code audit across the duplo package
  - [ ] Use ruff or manual grep to identify functions, classes, and module-level constants that are never imported or called from any live code path.
  - [ ] For each candidate: confirm it is truly dead (not used by tests that test the function itself, not used by external scripts). If dead, delete it.
  - [ ] Pay special attention to: functions in saver.py that were only used by _first_run; functions in fetcher.py that were only used by the old URL-in-text-file scanning; functions in extractor.py that were only used by the old first-run feature selection flow.
  - [ ] Tests: pytest -x passes after all deletions.

## Documentation updates

- [ ] Update README.md to reflect the new project setup flow
  - [ ] Remove any references to the old implicit first-run behavior (duplo auto-detecting a fresh directory and running interactively).
  - [ ] Document the new flow: duplo init to create SPEC.md, edit SPEC.md, run duplo.
  - [ ] Document the three input channels: URL in Sources, files in ref/, prose in Purpose/Architecture/Design/Behavior/Notes.
  - [ ] Document duplo init command surface and flags.
  - [ ] Keep existing documentation for duplo fix and duplo investigate unchanged.

- [ ] Update CLAUDE.md to reflect the current architecture
  - [ ] Remove any references to _first_run, interactive prompts, or URL-in-text-file scanning.
  - [ ] Document that SPEC.md is the input contract and all pipeline stages consume role-filtered input from the parser.
  - [ ] Document the module inventory: spec_reader.py (parser), spec_writer.py (drafter), init.py (duplo init), orchestrator.py (pipeline helpers), migration.py (migration gate).
  - [ ] Document the safety invariant: no raw SPEC.md text in LLM prompts.

- [ ] [BATCH] Remove or archive stale design documents
  - [ ] The design documents (PARSER-design.md, DRAFTER-design.md, INIT-design.md, PIPELINE-design.md, MIGRATION-design.md, REDESIGN-overview.md) were authoritative during implementation. Now that all phases are shipped, decide whether to keep them as historical reference or move them to a docs/ subdirectory.
  - [ ] Do NOT delete them without confirming with the user. Propose the archival strategy and wait for confirmation.
  - [ ] If archiving: create docs/design/ directory and move all design docs there. Update any remaining cross-references.

## Automated integration tests

- [ ] Add tests/test_phase7_integration.py with test_fresh_directory_without_init_prints_message
  - [ ] Run duplo (no subcommand) in a completely empty tmpdir (no .duplo/, no SPEC.md).
  - [ ] Assert: prints a message directing user to run duplo init; exits 0 (not 1); does NOT attempt _first_run behavior (no interactive prompts, no directory creation).

- [ ] Add test_old_project_still_blocked_by_migration
  - [ ] Create a tmpdir with .duplo/duplo.json but no SPEC.md.
  - [ ] Run duplo (no subcommand).
  - [ ] Assert: migration message printed (now referencing duplo init); exits 1.

- [ ] Add test_no_dead_imports_remain
  - [ ] Programmatically import every module in the duplo package.
  - [ ] Assert: no ImportError from deleted modules; no AttributeError from deleted functions.
  - [ ] This is a smoke test to catch stale imports that the individual deletion tests might miss.

- [ ] Run the full test suite and confirm Phase 7 closes cleanly
  - [ ] Execute pytest -x against the duplo repo.
  - [ ] Assert: all tests pass; no test file references deleted modules or functions.
  - [ ] If any test fails, the task fails and mcloop will retry.
