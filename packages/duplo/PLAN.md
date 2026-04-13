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

## Bugs





# Duplo - Phase 1: Bootstrapping

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

## Duplo — Phase 2: Phase Completion and Next-Phase Generation

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

---

## Duplo — Phase 3: SPEC.md parser and prompt-injection-safe formatters

The SPEC.md / `ref/` redesign restructures duplo's input contract so user intent lives in a typed, reviewable spec rather than in interactive prompts and ambient directory scanning. This phase implements the data layer only: parser, dataclasses, validation, role-filtered formatters, and the rewrite of `format_spec_for_prompt` that closes the prompt-injection leak. No pipeline behavior changes; existing callers continue to work via a compatibility layer.

Design reference: `PARSER-design.md` (authoritative), with `SPEC-template.md` and `SPEC-guide.md` defining the on-disk schema and `REDESIGN-overview.md` providing context.

Critical safety invariant introduced in this phase: **no LLM call ever sees raw SPEC.md text.** `format_spec_for_prompt` is rewritten to serialize from parsed dataclasses with role/flag filtering. Without this, `proposed:`, `discovered:`, and `counter-example` entries leak into every LLM prompt despite the role-filter helpers. The invariant has its own dedicated test (item below) that pins the property.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all code generation. Ruff for linting, pytest for tests. All AI calls go through `claude -p` (no direct API calls).

---

- [x] [BATCH] Add new dataclasses and the comment-stripping helper to `spec_reader.py`
  - [x] Add `SourceEntry` dataclass with fields `url`, `role`, `scrape`, `notes`, `proposed`, `discovered`. Per PARSER-design.md § SourceEntry.
  - [x] Add `ReferenceEntry` dataclass with fields `path`, `roles` (list[str]), `notes`, `proposed`. Per PARSER-design.md § ReferenceEntry. Note `roles` is plural to support multiple-roles-per-entry.
  - [x] Add `DesignBlock` dataclass with fields `user_prose`, `auto_generated`, `has_fill_in_marker`. Per PARSER-design.md § DesignBlock.
  - [x] Add `_HTML_COMMENT_RE` and `_strip_comments(body)` helper. Per PARSER-design.md § `<FILL IN>` detection.
  - [x] Tests: dataclass field defaults, `_strip_comments` removes single-line and multi-line HTML comment blocks, comment-stripping leaves non-comment content intact.

- [x] [BATCH] Add `<FILL IN>` detection for required sections
  - [x] Add the `_FILL_IN_RE` regex per PARSER-design.md (matches `<FILL IN>` permissively on whitespace and trailing hint text).
  - [x] Apply `_strip_comments` to a section body before regex matching, so commented-out template hints don't trigger detection.
  - [x] Wire detection into `_parse_spec` to set `spec.fill_in_purpose` after parsing `## Purpose`.
  - [x] Wire detection into `_parse_spec` to set `spec.fill_in_architecture` after parsing `## Architecture`.
  - [x] Wire detection into `_parse_spec` to set `spec.fill_in_design` per the rule: true ONLY when `design.has_fill_in_marker` AND no reference entries have `visual-target` in `roles`.
  - [x] Tests: marker present in body sets flag; marker present only in an HTML comment does NOT set flag; absent marker keeps flag false; `fill_in_design` rule covers both required conditions.

- [x] Add `## Sources` parser
  - [x] Add `_SOURCE_ENTRY_START` and `_FIELD_LINE` regexes per PARSER-design.md § `## Sources` parser. Entry start matches a list-item line containing an http(s) URL; field lines match indented `key: value` pairs.
  - [x] Implement entry-block parser: scan section line-by-line, accumulate field lines until next entry or section end, support multi-line `notes:` continuations indented further than the field name.
  - [x] Validation per `SourceEntry`: drop entries with invalid URL; DROP entries with unknown role (do NOT default — typo `role: doc` must not silently widen authority); default unknown `scrape` to `none` (not `deep`); accept both `proposed` and `discovered` set without diagnostic.
  - [x] Diagnostic emission via existing `duplo.diagnostics.record_failure`.
  - [x] Add `sources` to `_KNOWN_SECTIONS`.
  - [x] Tests: single entry, multiple entries, all field combinations, invalid URLs dropped, invalid roles dropped (entry removed entirely), invalid scrape defaulting to `none`, comment-stripped examples not parsed as real entries, multi-line `notes:` parsed correctly.

- [x] Add `## Notes` parser
  - [x] Trivial: store comment-stripped body as `spec.notes`. No structured parsing.
  - [x] Add `notes` to `_KNOWN_SECTIONS`.
  - [x] Tests: present section captured verbatim; absent section yields empty string; comment blocks stripped before storage.

- [ ] Convert `## References` parser from prose to structured entries
  - [ ] Add bare and quoted entry-start regexes per PARSER-design.md § `## References` parser. Bare form matches list-item lines starting with `ref/` followed by a path with non-greedy whitespace handling (paths with spaces are common; macOS screenshots default to names like `Screen Shot 2025-10-12 at 14.30.png`). Quoted form matches `- "ref/..."` and strips the quotes after match (for paths with unusual characters).
  - [ ] Implement entry parser sharing `_FIELD_LINE` with the Sources parser.
  - [ ] Parse `role:` as comma-separated list into `roles: list[str]`. Support multiple roles per entry (the dual-use case for behavioral-and-visual videos).
  - [ ] Validation per `ReferenceEntry`: drop entries with paths not under `ref/` (after quote-stripping); drop unknown roles from the comma-separated list with diagnostic; if all roles unknown, default to `["ignore"]`.
  - [ ] Reject `discovered:` flag with diagnostic (only Sources can be discovered).
  - [ ] Tests: single entry, multiple entries, paths with spaces (bare form), paths with unusual characters (quoted form), paths outside `ref/` dropped, multiple roles parsed correctly, unknown roles dropped while valid ones kept, all-unknown-roles defaults to `ignore`, `discovered:` rejected.
  - [ ] Migration test: old prose-form `## References` parses to empty `references` list, prose preserved in `spec.raw`, diagnostic emitted suggesting migration.

- [ ] Add AUTO-GENERATED block parsing in `## Design`
  - [ ] Add the `_AUTOGEN_RE` regex per PARSER-design.md § `## Design` parser (matches the BEGIN/END comment markers with DOTALL).
  - [ ] If block present: split body into `user_prose` (text before block) and `auto_generated` (block contents, markers stripped).
  - [ ] If block absent: entire comment-stripped body becomes `user_prose`; `auto_generated` is empty.
  - [ ] Set `has_fill_in_marker` by checking `user_prose` (after comment stripping) against `_FILL_IN_RE`.
  - [ ] Tests: block present (correct split); block absent (all to user_prose); malformed BEGIN-only or END-only markers treated as no block; nested or repeated markers handled deterministically.

- [ ] Update `ProductSpec` and audit existing callers
  - [ ] Change `design` field from `str` to `DesignBlock`.
  - [ ] Change `references` field from `str` to `list[ReferenceEntry]`.
  - [ ] Add new fields: `sources: list[SourceEntry]`, `notes: str`, `fill_in_purpose: bool`, `fill_in_architecture: bool`, `fill_in_design: bool`.
  - [ ] Grep the codebase for callers of `spec.references` and `spec.design` accessing them as strings. Update each call site to use `spec.design.user_prose` (or the new `format_design_for_prompt` helper, item below) and to treat `spec.references` as a list.
  - [ ] Tests: existing `test_spec_reader.py` continues to pass for fields that didn't change type (purpose, architecture, scope, behavior); new fields populate correctly on a fully-filled SPEC.md fixture.

- [ ] [BATCH] Add per-stage role-filtering formatters
  - [ ] `format_visual_references(spec) -> list[ReferenceEntry]`: entries where `visual-target` is in `roles`, excluding `proposed: true`.
  - [ ] `format_behavioral_references(spec) -> list[ReferenceEntry]`: entries where `behavioral-target` is in `roles`, excluding `proposed: true`. Dual-role entries appear in both this and `format_visual_references` so the caller can detect dual-use via membership check on `entry.roles`.
  - [ ] `format_doc_references(spec) -> list[ReferenceEntry]`: entries where `docs` is in `roles`, excluding `proposed: true`.
  - [ ] `format_counter_examples(spec) -> list[ReferenceEntry]`: entries where `counter-example` is in `roles`, excluding `proposed: true`.
  - [ ] All four return `list[ReferenceEntry]` (not `list[Path]`) so callers can inspect roles, notes, and flags. Path extraction is `[e.path for e in ...]` at the call site.
  - [ ] Tests: each formatter returns the right filtered list; each excludes `proposed: true`; entries with multiple roles appear in every matching formatter; each handles empty input gracefully.

- [ ] Add `format_scrapeable_sources(spec) -> list[SourceEntry]`
  - [ ] Returns source entries where `scrape` is `deep` or `shallow`, AND `discovered: false`, AND `proposed: false`, AND `role` is NOT `counter-example`.
  - [ ] Counter-example entries with `scrape: deep` or `scrape: shallow` get a diagnostic (the user almost certainly meant `scrape: none`) and are treated as `none` regardless of declared value.
  - [ ] Tests: each filter condition exercised independently; counter-example with non-`none` scrape diagnostic emitted; counter-example with `scrape: none` silent.

- [ ] Add `format_design_for_prompt(spec) -> str`
  - [ ] If both `user_prose` and `auto_generated` are present, format them in that order with a separator.
  - [ ] If only one is present, return that one.
  - [ ] If neither, return empty string.
  - [ ] Tests: each combination produces expected output; user_prose comes first when both present.

- [ ] Rewrite `format_spec_for_prompt` to serialize from dataclasses (prompt-injection safety invariant)
  - [ ] Replace the existing implementation that returns `spec.raw`. The new implementation serializes from parsed `ProductSpec` fields, NOT from raw text.
  - [ ] Include user-authored sections verbatim: `## Purpose`, `## Architecture`, `## Design.user_prose`, `## Scope`, `## Behavior`, `## Notes`.
  - [ ] For `## Sources`: include only entries where `proposed: false` AND `discovered: false` AND `role` is NOT `counter-example`.
  - [ ] For `## References`: include only entries where `proposed: false` AND no role is `counter-example` AND no role is `ignore`.
  - [ ] For `## Design`: include `auto_generated` content alongside `user_prose` (autogen is derived from non-proposed visual targets only and has already been filtered upstream).
  - [ ] Wrap output in the existing labelled prefix ("PRODUCT SPECIFICATION (authored by the user...") so existing consumers see equivalent framing.
  - [ ] Update existing tests for `format_spec_for_prompt` (output format will differ) so they pin the new behavior.
  - [ ] **Prompt-injection invariant test (highest-stakes test in the phase)**: construct a spec containing `proposed: true` source, `discovered: true` source, `counter-example` source, `proposed: true` reference, and `counter-example` reference, all with distinctive recognizable content; assert that `format_spec_for_prompt(spec)` output does NOT contain any of those entries' content. This test pins the safety property for all downstream LLM call sites.

- [ ] Add `validate_for_run(spec) -> list[str]` and wire into `main.py`
  - [ ] Returns list of human-readable error messages; empty list means OK to run.
  - [ ] Errors: purpose-fill-in, architecture-fill-in, and the no-source-and-no-ref-and-sparse-purpose condition (no scrapeable sources AND no non-ignore references AND `## Purpose` shorter than 50 characters).
  - [ ] `fill_in_design` produces a WARNING (not an error) per PARSER-design.md § Validation API. The "URL alone" common pattern is valid even when `## Design` has no user prose and no visual-target references — duplo can still proceed by inferring design from scraped product-reference pages. Warnings print but do not block execution.
  - [ ] Warnings for unreviewed entries: count `proposed: true` references and `discovered: true` sources, emit one warning each summarizing counts and what to do.
  - [ ] Wire `validate_for_run` into `main.py` so it runs after `read_spec` and before any pipeline work. If errors returned, print them to stderr and exit 1. Warnings print to stdout but do not block.
  - [ ] Tests: each error condition produces the expected message; valid spec returns empty list; `fill_in_design` produces warning not error; warnings include correct counts.
  - [ ] Backward compatibility: old-format SPEC.md files (no fill-in markers anywhere because they predate the convention) keep `fill_in_purpose` and `fill_in_architecture` false and pass validation. Test this explicitly.

---

## Manual verification (user must test)

- [ ] Write a fully-populated SPEC.md in the new format (every section filled, including `## Sources`, structured `## References`, `## Notes`) in a scratch directory and confirm `read_spec()` parses every section into the expected dataclass fields. Drop into a Python REPL or write a small script.
- [ ] Write a SPEC.md with deliberate `proposed: true`, `discovered: true`, and `counter-example` entries. Call `format_spec_for_prompt(spec)` and visually confirm the output contains none of those entries' content. This is the safety invariant.
- [ ] Write a SPEC.md with a fill-in marker left in `## Purpose`. Run `duplo` and confirm it exits 1 with a clear error message and does NOT proceed to scraping or extraction.
- [ ] Run `duplo` against an existing pre-redesign project (one with no SPEC.md or an old-format SPEC.md). Confirm it still runs end-to-end without errors. The new validation should not block legacy projects until they migrate.
- [ ] Write a SPEC.md with a reference path containing spaces (e.g. a list-item entry naming `ref/Screen Shot 2025-10-12 at 14.30.png`) and confirm it parses without dropping the entry. Same for a quoted path with unusual characters.
