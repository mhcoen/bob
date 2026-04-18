# Phase 8: Platform Knowledge Library

Duplo generates platform-naive tasks and scaffold. Claude Code in -p mode
takes the most literal path without reasoning about platform context,
causing failures like running SwiftUI binaries directly (no window),
waiting for GUI apps to exit (infinite hang), and missing .gitignore
entries. This phase adds a platform knowledge library that duplo selects
automatically from build preferences and injects into the planner prompt,
CLAUDE.md, and scaffold artifacts.

The duplo/platforms/ package (schema.py, resolver.py, formatter.py,
scaffold.py, macos/swiftui_spm.py, macos/python_cli.py) is already on
disk. This phase wires it into the pipeline and adds structured platform
declarations to SPEC.md.

- [x] Add structured platform entry syntax to the spec parser. In spec_reader.py, parse list-item entries under the Architecture section with fields: platform, language, build. Each entry becomes one element in a new list field on ProductSpec. Free-form prose after the structured entries is still captured in spec.architecture as before. Write tests in test_spec_reader.py covering: single entry, multiple entries, mixed entries plus prose, no entries (backward compatible prose-only).

- [x] Update SPEC-template.md and SPEC-guide.md. Add an example showing structured platform entries under Architecture. The template should show one entry with platform/language/build fields. The guide should explain that multiple entries are supported for multi-stack projects and that free-form prose can follow.

- [x] Extend BuildPreferences to support multiple stacks. In build_prefs.py, change parse_build_preferences to accept the structured entries from the spec parser when available, falling back to LLM extraction from prose when no structured entries exist. Return a list of BuildPreferences instead of a single instance. Update architecture_hash and validation. Update all callers in main.py to handle the list. Write tests in test_build_prefs.py.

- [x] Wire the resolver into the pipeline. In main.py, after loading preferences, call resolve_profiles() for each BuildPreferences in the list. Collect the union of matched profiles. Pass them downstream to the planner and CLAUDE.md writer. Write a test confirming resolve_profiles is called and its output is threaded through.

- [x] Wire platform rules into the planner system prompt. In planner.py, add a platform_addendum parameter to generate_phase_plan and generate_next_phase_plan. When provided, append it to the system prompt string before calling query(). The caller in main.py passes format_planner_system_addendum(profiles). Write tests in test_planner.py: mock query(), verify system prompt contains platform rules when addendum is provided, verify it does not when addendum is empty.

- [x] Wire scaffold generation into the pipeline. In main.py, before the first call to generate_phase_plan for a new project, call write_scaffold(profiles, project_name, target_dir). Pass format_scaffold_notice(written) into the planner as part of the platform addendum. Write tests in test_scaffold confirming: run.sh is created with correct content and executable bit, existing files are not overwritten, gitignore entries are appended without duplication.

- [x] Build the CLAUDE.md writer for target projects. Create a new function in saver.py that assembles and writes CLAUDE.md to the target project directory. Content includes: project name and stack from BuildPreferences, platform rules section from format_claude_md_section(profiles), and local overrides section from local.md if present. This function is called from main.py during project setup and on subsequent runs when profiles change. Write tests in test_saver.py.

- [x] Add local.md support. In main.py, check for local.md in the target project root. If present, read its content and pass it through format_local_overrides() into both the planner addendum and the CLAUDE.md writer. Add local.md to the gitignore entries written by initializer.py create_project_dir(). Write tests confirming local overrides appear in planner prompt and CLAUDE.md when local.md exists and are absent when it does not.

- [ ] Add integration test for the full platform knowledge flow. Create test_platform_integration.py. Given a SPEC.md with a SwiftUI architecture entry, mock the LLM calls and verify that: resolve_profiles returns the swiftui_spm profile, the planner system prompt contains platform rules, a run.sh file exists on disk, CLAUDE.md contains the platform rules section, and gitignore contains .build/ and *.app/ entries.

- [ ] Run the full test suite and confirm all tests pass. Execute pytest -x against the duplo repo. If any test fails, the task fails and mcloop will retry.
