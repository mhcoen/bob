# Duplo

Duplo duplicates apps. The user creates a project directory and drops
in whatever reference material they have: screenshots, PDFs, text files,
URLs. Running `duplo` from that directory analyzes the materials,
identifies the product to duplicate, extracts features and visual
design details, generates a build plan, and uses McLoop to build it.
Running `duplo` again detects new files the user has added, re-scrapes
the product docs, and appends new tasks for anything that was missed.
The cycle is: add reference material, run duplo, let McLoop build,
test, add more reference material if needed, run duplo again.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all
code generation. Ruff for linting, pytest for tests. Keep modules
short and focused. This is a thin orchestration layer, not a framework.

**ARCHITECTURE CHANGE**: The old subcommand model (duplo init, duplo
run, duplo next) is being replaced. The new model is a single `duplo`
command with no required arguments. It runs from the current directory
and auto-detects whether this is a first run or an update based on
whether .duplo/ exists. The old main.py with its _cmd_init, _cmd_run,
_cmd_next functions should be rewritten. The old modules (selector.py,
questioner.py, collector.py, initializer.py, runner.py, planner.py,
roadmap.py, notifier.py, comparator.py, issuer.py, appshot.py) can
be reused where they still apply, but the orchestration in main.py
must change to match the new directory-based workflow. Do not preserve
the old subcommand parsing or the old init/run/next flow.

- [x] Project scaffolding
  - [x] Create duplo package with __init__.py and main.py entry point
  - [x] Add CLI argument parser: duplo <url>, duplo run, duplo next
  - [x] Verify pip install -e . works and duplo command runs
- [x] Product scraping
  - [x] Fetch the product URL and extract text content
  - [x] Follow links, prioritizing documentation, features, guides, changelogs, and API references over marketing, blog, pricing, legal, and login pages
  - [x] Save reference screenshots from the product website
  - [x] Extract a structured feature list from the scraped content
- [x] Interactive feature selection
  - [x] Present features to the user and ask which to include
  - [x] Ask about platform, language, constraints, and preferences
  - [x] Save selections to duplo.json in the target project
- [x] Plan generation
  - [x] Generate Phase 1 PLAN.md (smallest end-to-end working thing)
  - [x] Create target project directory with git init
  - [x] Write PLAN.md, README.md, and mcloop.json
  - [x] Include CLAUDE.md with appshot instructions
- [x] Phase execution
  - [x] Run McLoop on the target project
  - [x] Wait for completion, capture screenshots with appshot
  - [x] Compare screenshots against reference images via Claude API
  - [x] Generate visual issue list
  - [x] Notify user that phase is complete and ready for testing
- [x] Feedback and iteration
  - [x] Collect user feedback (text input or from a file)
  - [x] Generate next phase PLAN.md incorporating feedback and visual issues
  - [x] Append completed phases to duplo.json history
  - [x] Run McLoop for the next phase
- [x] State management
  - [x] Store all state in duplo.json: source URL, features, phases, feedback
  - [x] Support resuming after interruption (duplo run picks up where it left off)
  - [x] Track which reference screenshots map to which features
- [x] Deep documentation extraction
  - [x] When scraping a product site, identify links to documentation pages by reading the page content and link text, not by matching a hardcoded list of platforms
  - [x] Follow documentation links even if they leave the main domain (docs are often hosted separately)
  - [x] Increase the page limit for documentation sites since doc pages are individually small but collectively important
  - [x] Extract code examples from documentation pages as input/expected_output pairs
  - [x] Extract feature tables, operation lists, unit lists, and function references
  - [x] Store all extracted examples in duplo.json so they persist across runs
- [x] Test case generation from documentation
  - [x] Every input/output example extracted from documentation becomes a unit test case
  - [x] Tests should call the app's core logic directly without requiring GUI interaction
  - [x] Include test generation tasks in the PLAN.md that Duplo generates for the target project
  - [x] Group tests by category so failures are easy to diagnose
- [x] Persistent state in .duplo/ directory
  - [x] Create a .duplo/ directory in the target project for Duplo's working state between runs
  - [x] Save all reference URLs consulted during scraping, with timestamps and content hashes
  - [x] Save raw scraped content so re-runs can diff against what changed on the product site
  - [x] Save extracted examples separately from duplo.json so they can be reviewed and edited
  - [x] Add .duplo/ to the target project's .gitignore
- [x] Directory-based workflow redesign
  - [x] Duplo runs from the current directory with no required arguments. The user creates the project directory, puts whatever reference material they want inside (images, PDFs, text files, URLs in a file), and runs duplo.
  - [x] On first run, scan the directory for reference materials: images (png, jpg, gif, webp), PDFs, text/markdown files, and any file containing URLs. Analyze each to determine relevance.
  - [x] If a URL is found, validate it points to a single clear product, not a company portfolio or homepage with multiple products. Ask the user to clarify if ambiguous.
  - [x] Clearly state what product Duplo thinks it is duplicating and get confirmation before proceeding. No ambiguity.
  - [x] Send images to Claude Vision to extract visual design details: colors, fonts, spacing, layout, component styles. These become design requirements in PLAN.md.
  - [x] Extract text content from PDFs and include in feature analysis.
  - [x] Move processed reference materials to .duplo/references/ to keep the project directory clean.
  - [x] Keep a hash manifest of all files in the project directory in .duplo/file_hashes.json
- [x] Incremental update mode
  - [x] On subsequent runs, detect new or changed files in the project directory by comparing against .duplo/file_hashes.json
  - [x] Analyze any new files the same way as first run (images to Vision, PDFs to text, URLs to scraper)
  - [x] Re-scrape the product URL with the improved deep extractor if the URL was already known
  - [x] Compare newly extracted features and examples against existing PLAN.md
  - [x] Append new unchecked tasks for missing features, uncovered examples, and design refinements
  - [x] Never modify or remove existing tasks (checked or unchecked)
  - [x] Print a summary of what was found and what was added
- [x] Video reference extraction
  - [x] Detect video files in the project directory (mp4, mov, webm, avi)
  - [x] Use ffmpeg scene change detection to extract frames at visual transition points
  - [x] Deduplicate similar frames using perceptual image hashing
  - [x] Send candidate frames to Claude Vision to filter: keep only clear, stable screenshots of the application showing a distinct UI state. Discard transitions, blur, marketing overlays, loading screens.
  - [x] For each accepted frame, ask Claude Vision to describe what UI state it shows (main view, settings panel, dialog, menu, etc.)
  - [x] Store accepted frames in .duplo/references/ with their UI state descriptions
  - [x] Include extracted frames in the same analysis pipeline as user-provided screenshots
  - [x] Requires ffmpeg on PATH (document in README)
- [x] Product disambiguation
  - [x] When a URL points to a company with multiple products, present the products found and ask which one to duplicate
  - [x] When a URL is a landing page with unclear product boundaries, ask the user to describe what specific product they want
  - [x] Store the confirmed product identity in .duplo/product.json so subsequent runs don't re-ask
- [x] Non-destructive plan updates
  - [x] save_plan() must never overwrite an existing PLAN.md. If PLAN.md already exists, append new tasks to the end of the file instead of replacing it. Existing checked and unchecked items must be preserved exactly as they are.
  - [x] All other files duplo writes (CLAUDE.md, mcloop.json, README.md) must also be non-destructive on subsequent runs. Merge or append, never replace.
  - [x] Update README.md to document that duplo's update cycle is non-destructive: existing code, plans, and configuration are never removed or overwritten.
- [x] Route all AI calls through claude -p instead of direct Anthropic API calls. Every module that creates an anthropic.Anthropic() client (extractor.py, design_extractor.py, validator.py, roadmap.py, planner.py, comparator.py, frame_filter.py, frame_describer.py, gap_detector.py) must be changed to use claude -p so the Max subscription is used instead of API credits. No direct API calls.
- [x] Re-extract features on subsequent runs: _subsequent_run currently re-scrapes the product URL and updates page records, but never re-runs feature extraction on the new content. The gap detector compares the same features stored in duplo.json against the plan, so it always finds no gaps. On subsequent runs, after re-scraping, re-extract features from the updated scraped content using extract_features(), merge new features into duplo.json (without removing existing ones), then pass the combined feature list to the gap detector.

---

# Duplo — Phase 2: Phase Completion and Next-Phase Generation

Duplo currently handles first-run (scrape, extract features, select, generate plan) and incremental updates (detect new files, re-scrape, append gap tasks). What is missing is the phase-completion loop: when all tasks in PLAN.md are done, duplo should track what was implemented, present the remaining work, and generate a scoped next-phase plan.

This phase adds feature annotations in generated plans, deterministic status tracking in duplo.json, a next-phase flow with interactive feature selection and issue injection, and fixes to the state machine bugs that prevent any of this from working on existing projects.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all code generation. Ruff for linting, pytest for tests. All AI calls go through `claude -p` (no direct API calls).

---

- [x] Fix phase-title regex to handle app-name prefixed headings
  - [x] `append_phase_to_history` uses `r"^#\s*(Phase\s+\d+[^\n]*)"` which fails on headings like `# McWhisper — Phase 1: Core`. The same regex pattern appears in `_complete_phase`, `_advance_to_next`, `_detect_next_phase_number`, and `_subsequent_run`. All instances must be relaxed to find a phase number anywhere in the first `#` heading line, e.g. `r"^#\s*.*?(Phase\s+\d+[^\n]*)"` or extract the phase number with `r"Phase\s+(\d+)"`.
  - [x] Add tests covering headings in both formats: `# Phase 1: Core` and `# McWhisper — Phase 1: Core`

- [x] Add feature annotations to generated plans
  - [x] Modify the planner system prompt in `generate_phase_plan` so that every generated task line includes a `[feat: "Feature Name"]` annotation listing which features from the input list it addresses. Tasks addressing multiple features list them comma-separated: `[feat: "Push-to-talk recording", "Global keyboard shortcuts"]`. Tasks for bug fixes or issues use `[fix: "description"]`. Scaffolding or structural tasks that do not map to any feature use no annotation.
  - [x] Modify `generate_next_phase_plan` with the same annotation requirement
  - [x] Add a test that verifies generated plans contain `[feat: ...]` or `[fix: ...]` annotations on task lines

- [x] Add `status`, `implemented_in`, and `issues` fields to feature tracking
  - [x] Each feature dict in `duplo.json` gets two optional fields: `status` (one of `pending`, `implemented`, `partial`) and `implemented_in` (phase label string). New features default to `status: "pending"`.
  - [x] Add a `save_feature_status(name, status, implemented_in)` function to saver.py that updates a feature by name
  - [x] Add a top-level `issues` list to `duplo.json` for implementation problems not tied to a specific feature
  - [x] Add `save_issue(description, source, phase)` and `resolve_issue(description)` functions to saver.py
  - [x] Existing features in duplo.json files that lack a `status` field should be treated as `pending` by all code that reads them

- [x] Implement deterministic phase-completion tracking
  - [x] At phase completion (all checkboxes checked), parse PLAN.md for checked task lines
  - [x] For each checked line with a `[feat: ...]` annotation, mark the referenced features as `implemented` with the current phase label
  - [x] For each checked line with a `[fix: ...]` annotation, mark the corresponding issue as resolved
  - [x] For checked lines without annotations (user-added tasks or pre-annotation plans), batch them into a single `claude -p` call with the full feature list. Claude matches each task to an existing feature or confirms it is genuinely new. Mark matched features as implemented. Add genuinely new items as new feature entries with `status: "implemented"` and `implemented_in` set to the current phase.
  - [x] Add tests for the annotation parser covering annotated lines, unannotated lines, and mixed plans

- [x] Prompt for issues at phase completion
  - [x] After status tracking, before advancing to the next phase, prompt the user for known issues with the completed phase
  - [x] Multi-line input, blank line to finish, skippable
  - [x] Each line becomes an entry in the `issues` list in `duplo.json` with `source: "user"` and the current phase label
  - [x] This is where the user reports bugs (e.g. "waveform shows static bars during recording") or incomplete wiring (e.g. "qwen3-asr-swift dependency is unused")

- [x] Generate roadmap from remaining features when missing or consumed
  - [x] At the start of the next-phase flow, if `duplo.json` has no `roadmap` or the existing roadmap has been fully consumed (current_phase is past the last entry), generate a new one using `generate_roadmap` with only the remaining unimplemented features
  - [x] Pass the completion history (list of phase labels and what they implemented) as context so the roadmap builds on what exists rather than starting from scratch
  - [x] Save the new roadmap to `duplo.json`, resetting `current_phase` to 0 relative to the new roadmap
  - [x] Add a test that verifies roadmap generation excludes implemented features

- [x] Redesign `_subsequent_run` state machine
  - [x] Replace the current branching (in_progress checks, roadmap lookups, history-based detection) with a clean flow:
    - If PLAN.md exists and has unchecked items: print status summary and "Run mcloop to continue building." Exit.
    - If PLAN.md exists and all items are checked: run phase-completion flow (annotation parsing, status tracking, issue prompt). Delete PLAN.md. Fall through to next-phase flow.
    - If no PLAN.md: run next-phase flow.
  - [x] Remove the separate `_advance_to_next` code path. The single next-phase flow handles everything.
  - [x] Remove the dependency on `in_progress` for flow control. The `in_progress` key can be removed entirely or repurposed for crash recovery only.

- [x] Implement next-phase flow with feature selection
  - [x] Re-scrape the product site, re-extract features, merge new ones into `duplo.json` (already works)
  - [x] Partition features into implemented and remaining based on `status` field
  - [x] If roadmap is missing or consumed, generate a new one from remaining features (previous item)
  - [x] Use the next phase entry from the roadmap as the default recommendation during feature selection
  - [x] Present remaining features to the user using `select_features` (numbered, grouped by category), with the roadmap recommendation labeled (e.g. "Recommended for Phase 2: 3, 7, 12, 15")
  - [x] Show open issues from `duplo.json` and ask which should be addressed in this phase (same numbered selection pattern)
  - [x] Update `generate_phase_plan` to accept issues alongside features. The system prompt should instruct Claude to include fix tasks for issues alongside feature-implementation tasks, ordering fixes before new feature work when there are dependencies.
  - [x] Generate the next PLAN.md scoped to selected features + selected issues. Heading format: `# <AppName> — Phase N: <Title>`. All task lines include `[feat: ...]` or `[fix: ...]` annotations. Phase number derived from `phases` history length + 1.

- [x] Print status summary on every run
  - [x] Before doing any work, print: current phase number, features implemented vs. remaining, open issues count
  - [x] Example output: `McWhisper: Phase 1 complete. 14/52 features implemented, 3 open issues.`
  - [x] If no phases have been completed yet, print feature count and "Phase 1 in progress" or "Ready to generate Phase 1"

---

## Manual verification (user must test)

- [x] Run duplo in the mcwhisper directory. Confirm it detects Phase 1 as complete, runs the unannotated-task matching via Claude, marks implemented features, prompts for issues, generates a roadmap from remaining features, presents feature selection with a recommendation, and generates a Phase 2 PLAN.md with proper annotations.
- [x] Run duplo again immediately (Phase 2 not started). Confirm it prints the status summary and tells you to run mcloop.
- [x] After mcloop completes Phase 2, run duplo again. Confirm annotated tasks are tracked deterministically (no Claude call needed), issues prompt appears, roadmap is regenerated if consumed, and Phase 3 is ready.

---

- [x] Automatic BATCH tag support in generated plans
  - [x] Update `_PHASE_SYSTEM` prompt in `planner.py` to instruct Claude to mark parent tasks with `[BATCH]` when all subtasks are specific enough to execute without design decisions (file paths, function names, explicit conditionals, concrete values). Include an example showing the `[BATCH]` syntax with concrete subtasks. Do NOT use `[BATCH]` on tasks whose subtasks require significant design decisions or architectural exploration.
  - [x] Update `_NEXT_PHASE_SYSTEM` prompt with the same `[BATCH]` instruction for next-phase plan generation.
  - [x] Update the example plan in `_PHASE_SYSTEM` to show a `[BATCH]` parent with concrete subtasks instead of the generic "Subtask if needed" placeholder.


## Bugs

- [x] Scope `_plan_is_complete` and `_complete_phase` to the current phase only
  - [x] `_plan_is_complete()` in `main.py` iterates ALL lines in PLAN.md. Now that PLAN.md accumulates phases (no longer deleted between phases), it must only consider lines between the current phase heading (`# ... Phase N: ...`) and the next phase heading (or end of file). Extract a helper `_current_phase_content(content: str) -> str` that returns only the current phase section, using the phase number from `get_current_phase()`. Use this in `_plan_is_complete`.
  - [x] `_complete_phase` passes the full `plan_content` to `parse_completed_tasks`. It must pass only the current phase section (from the same helper). Without this fix, completing Phase 2 re-processes all Phase 1 tasks (re-marks features, re-matches unannotated tasks).
  - [x] `_plan_has_unchecked_tasks()` has the same bug — scope it to the current phase section.
  - [x] Add tests: multi-phase PLAN.md where Phase 1 is all `[x]` and Phase 2 has `[ ]` tasks. Verify `_plan_is_complete` returns False, `_plan_has_unchecked_tasks` returns True, and `_complete_phase` only processes Phase 2 tasks.

- [x] Add timeout to `capture_appshot` in `appshot.py`
  - [x] `capture_appshot` calls `subprocess.run(cmd)` with no `timeout` parameter. If the app is a menu bar app with no main window, or `run.sh` triggers a long build, this hangs indefinitely and blocks phase completion. Add a `timeout` keyword argument (default 60 seconds). Pass it to `subprocess.run(cmd, timeout=timeout)`. Catch `subprocess.TimeoutExpired`, print a warning, and return a distinct exit code (e.g. -2).
  - [x] Update `_complete_phase` in `main.py` to handle the timeout exit code gracefully (print "Screenshot capture timed out (skipping)" and continue).
  - [x] Add tests: mock `subprocess.run` to raise `TimeoutExpired`, verify `capture_appshot` returns -2.

- [x] Remove `generate_plan_test_tasks` from plan generation
  - [x] The mcloop task prompt already instructs every session to write unit tests. Standalone "add tests" / "wire up doc-example tests" tasks appended by `generate_plan_test_tasks` are redundant — they fail as no-ops because the tests already exist by the time mcloop reaches them. Remove the call to `generate_plan_test_tasks` and `append_test_tasks` from `_first_run` in `main.py`. Remove the import of `generate_plan_test_tasks` and `append_test_tasks`. Keep `generate_test_source`, `load_code_examples`, and `save_test_file` (they generate the initial test file from doc examples, which is separate).
  - [x] Verify no other call sites use `generate_plan_test_tasks`. If none remain, mark it deprecated or remove it from `test_generator.py`.
  - [x] Update `test_main.py` and `test_test_generator.py` to remove tests that assert `generate_plan_test_tasks` output appears in plans.

- [x] Skip re-scrape when site content is unchanged
  - [x] `_rescrape_product_url()` in `main.py` unconditionally fetches 60+ pages on every `_subsequent_run`, even when content has not changed since the last scrape. The page content hashes are already stored in `duplo.json` (via `save_raw_content`). After fetching, compare new content hashes against stored hashes. If no pages changed, skip feature re-extraction (`extract_features` call) and print "Site content unchanged, skipping feature re-extraction."
  - [x] When entering the next-phase flow (no PLAN.md exists) immediately after phase completion, the re-scrape from minutes ago is still current. Add a `last_scrape_timestamp` to `duplo.json` (set by `_rescrape_product_url`). If the timestamp is less than 10 minutes old, skip the entire re-scrape and print "Using recent scrape data (N minutes ago)."
  - [x] Add tests: mock `fetch_site` to return identical content, verify `extract_features` is not called. Mock timestamp within 10 minutes, verify `fetch_site` is not called.

- [x] Semantic feature deduplication in `save_features`
  - [x] `save_features` in `saver.py` only deduplicates by exact name match. Near-duplicates like "Custom vocabulary / glossary" vs "Custom vocabulary", or "Bring-your-own API keys" vs "Bring your own API keys" accumulate with every re-scrape. After merging new features by exact name, collect all feature names (including existing ones) and run a single `claude -p` call to identify groups of semantically identical features. For each group, keep the most descriptive name and merge the others (preserving `status: implemented` if any member has it). Print "Merged N duplicate feature(s)."
  - [x] Also deduplicate against already-implemented features: if a remaining feature is semantically identical to an implemented one (e.g. "Local offline transcription" remaining when "Local on-device transcription" is implemented), mark it implemented.
  - [x] Add tests: provide a feature list with known near-duplicates, mock the Claude call to return expected groupings, verify the merged list has no duplicates and statuses are preserved.

- [x] Language-aware test generation
  - [x] `test_generator.py` generates Python test files (`generate_test_source`) and `generate_plan_test_tasks` appends Python-specific "wire up doc-example tests" tasks regardless of the target project's language. For non-Python projects (Swift, Rust, Go, etc.) these tasks are invalid. Detect the target language from the project's build system (pyproject.toml → Python, Package.swift → Swift, Cargo.toml → Rust, go.mod → Go, package.json → JS/TS). If not Python, skip `generate_test_source` and `save_test_file` entirely. Print "Test generation skipped (target language: <lang>, only Python supported)."
  - [x] Add tests: mock a project directory with Package.swift, verify no test file is generated.

- [ ] Verify source-file URL scanning is suppressed on subsequent runs
  - [ ] `_analyze_new_files` calls `scan_files` which calls `_classify_file`. Verify that `_SOURCE_EXTS` and `_SOURCE_NAMES` filtering in `scanner.py` excludes source code files (`.py`, `.swift`, `.rs`, etc.) from being classified as analyzable reference materials. Add an explicit test: pass a list of source files to `scan_files`, verify none appear in `scan.urls`, `scan.images`, `scan.pdfs`, or `scan.text_files`.
