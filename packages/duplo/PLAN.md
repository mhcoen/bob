# Duplo

Create or clone from whatever you've got — screenshots, a demo video,
doc pages, a website, a one-line description — no source code required.
Drives Claude Code or Codex through phased builds via mcloop, turning
references into working software.

The user creates a project directory and drops in whatever reference
material they have. Running `duplo` from that directory analyzes the
materials, identifies the product to build or clone, extracts features
and visual design details, generates a build plan, and uses mcloop to
build it. Running `duplo` again detects new files the user has added,
re-scrapes any product docs, and appends new tasks for anything that
was missed. The cycle is: add reference material, run duplo, let
mcloop build, test, add more reference material if needed, run duplo
again.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all
code generation. Ruff for linting, pytest for tests. Keep modules
short and focused. This is a thin orchestration layer, not a framework.

**ARCHITECTURE NOTE**: The old subcommand model (duplo init, duplo
run, duplo next) has been replaced. The new model is a single `duplo`
command with no required arguments. It runs from the current directory
and auto-detects whether this is a first run or an update based on
whether `.duplo/` exists. The redesign in progress (Phases 3-7)
restructures the input contract so user intent lives in a typed,
reviewable `SPEC.md` rather than in interactive prompts and ambient
directory scanning.

## Bugs

<!-- Bugs here have absolute priority and run before any other phase
     work. mcloop's bug-only mode picks these up first. -->


---

# Phase 1: Bootstrapping (complete)

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

# Phase 2: Phase Completion and Next-Phase Generation (complete)

Duplo currently handles first-run (scrape, extract features, select, generate plan) and incremental updates (detect new files, re-scrape, append gap tasks). What is missing is the phase-completion loop: when all tasks in PLAN.md are done, duplo should track what was implemented, present the remaining work, and generate a scoped next-phase plan.

This phase added feature annotations in generated plans, deterministic status tracking in duplo.json, a next-phase flow with interactive feature selection and issue injection, and fixes to the state machine bugs that prevented any of this from working on existing projects.

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

- [x] Automatic BATCH tag support in generated plans
  - [x] Update `_PHASE_SYSTEM` prompt in `planner.py` to instruct Claude to mark parent tasks with `[BATCH]` when all subtasks are specific enough to execute without design decisions (file paths, function names, explicit conditionals, concrete values). Include an example showing the `[BATCH]` syntax with concrete subtasks. Do NOT use `[BATCH]` on tasks whose subtasks require significant design decisions or architectural exploration.
  - [x] Update `_NEXT_PHASE_SYSTEM` prompt with the same `[BATCH]` instruction for next-phase plan generation.
  - [x] Update the example plan in `_PHASE_SYSTEM` to show a `[BATCH]` parent with concrete subtasks instead of the generic "Subtask if needed" placeholder.

### Manual verification (all complete)

- [x] Run duplo in the mcwhisper directory. Confirm it detects Phase 1 as complete, runs the unannotated-task matching via Claude, marks implemented features, prompts for issues, generates a roadmap from remaining features, presents feature selection with a recommendation, and generates a Phase 2 PLAN.md with proper annotations.
- [x] Run duplo again immediately (Phase 2 not started). Confirm it prints the status summary and tells you to run mcloop.
- [x] After mcloop completes Phase 2, run duplo again. Confirm annotated tasks are tracked deterministically (no Claude call needed), issues prompt appears, roadmap is regenerated if consumed, and Phase 3 is ready.

---

# Phase 3: SPEC.md parser and prompt-injection-safe formatters (complete)

The SPEC.md / `ref/` redesign restructures duplo's input contract so user intent lives in a typed, reviewable spec rather than in interactive prompts and ambient directory scanning. This phase implemented the data layer only: parser, dataclasses, validation, role-filtered formatters, and the rewrite of `format_spec_for_prompt` that closes the prompt-injection leak. No pipeline behavior changes; existing callers continued to work via a compatibility layer.

Design reference: `PARSER-design.md` (authoritative), with `SPEC-template.md` and `SPEC-guide.md` defining the on-disk schema and `REDESIGN-overview.md` providing context.

Critical safety invariant introduced in this phase: **no LLM call ever sees raw SPEC.md text.** `format_spec_for_prompt` was rewritten to serialize from parsed dataclasses with role/flag filtering. Without this, `proposed:`, `discovered:`, and `counter-example` entries would leak into every LLM prompt despite the role-filter helpers. The invariant has its own dedicated test that pins the property.

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

- [x] Convert `## References` parser from prose to structured entries
  - [x] Add bare and quoted entry-start regexes per PARSER-design.md § `## References` parser. Bare form matches list-item lines starting with `ref/` followed by a path with non-greedy whitespace handling (paths with spaces are common; macOS screenshots default to names like `Screen Shot 2025-10-12 at 14.30.png`). Quoted form matches `- "ref/..."` and strips the quotes after match (for paths with unusual characters).
  - [x] Implement entry parser sharing `_FIELD_LINE` with the Sources parser.
  - [x] Parse `role:` as comma-separated list into `roles: list[str]`. Support multiple roles per entry (the dual-use case for behavioral-and-visual videos).
  - [x] Validation per `ReferenceEntry`: drop entries with paths not under `ref/` (after quote-stripping); drop unknown roles from the comma-separated list with diagnostic; if all roles unknown, default to `["ignore"]`.
  - [x] Reject `discovered:` flag with diagnostic (only Sources can be discovered).
  - [x] Tests: single entry, multiple entries, paths with spaces (bare form), paths with unusual characters (quoted form), paths outside `ref/` dropped, multiple roles parsed correctly, unknown roles dropped while valid ones kept, all-unknown-roles defaults to `ignore`, `discovered:` rejected.
  - [x] Migration test: old prose-form `## References` parses to empty `references` list, prose preserved in `spec.raw`, diagnostic emitted suggesting migration.

- [x] Add AUTO-GENERATED block parsing in `## Design`
  - [x] Add the `_AUTOGEN_RE` regex per PARSER-design.md § `## Design` parser (matches the BEGIN/END comment markers with DOTALL).
  - [x] If block present: split body into `user_prose` (text before block) and `auto_generated` (block contents, markers stripped).
  - [x] If block absent: entire comment-stripped body becomes `user_prose`; `auto_generated` is empty.
  - [x] Set `has_fill_in_marker` by checking `user_prose` (after comment stripping) against `_FILL_IN_RE`.
  - [x] Tests: block present (correct split); block absent (all to user_prose); malformed BEGIN-only or END-only markers treated as no block; nested or repeated markers handled deterministically.

- [x] Update `ProductSpec` and audit existing callers
  - [x] Change `design` field from `str` to `DesignBlock`.
  - [x] Change `references` field from `str` to `list[ReferenceEntry]`.
  - [x] Add new fields: `sources: list[SourceEntry]`, `notes: str`, `fill_in_purpose: bool`, `fill_in_architecture: bool`, `fill_in_design: bool`.
  - [x] Grep the codebase for callers of `spec.references` and `spec.design` accessing them as strings. Update each call site to use `spec.design.user_prose` (or the new `format_design_for_prompt` helper, item below) and to treat `spec.references` as a list.
  - [x] Tests: existing `test_spec_reader.py` continues to pass for fields that didn't change type (purpose, architecture, scope, behavior); new fields populate correctly on a fully-filled SPEC.md fixture.

- [x] [BATCH] Add per-stage role-filtering formatters
  - [x] `format_visual_references(spec) -> list[ReferenceEntry]`: entries where `visual-target` is in `roles`, excluding `proposed: true`.
  - [x] `format_behavioral_references(spec) -> list[ReferenceEntry]`: entries where `behavioral-target` is in `roles`, excluding `proposed: true`. Dual-role entries appear in both this and `format_visual_references` so the caller can detect dual-use via membership check on `entry.roles`.
  - [x] `format_doc_references(spec) -> list[ReferenceEntry]`: entries where `docs` is in `roles`, excluding `proposed: true`.
  - [x] `format_counter_examples(spec) -> list[ReferenceEntry]`: entries where `counter-example` is in `roles`, excluding `proposed: true`.
  - [x] All four return `list[ReferenceEntry]` (not `list[Path]`) so callers can inspect roles, notes, and flags. Path extraction is `[e.path for e in ...]` at the call site.
  - [x] Tests: each formatter returns the right filtered list; each excludes `proposed: true`; entries with multiple roles appear in every matching formatter; each handles empty input gracefully.

- [x] Add `format_scrapeable_sources(spec) -> list[SourceEntry]`
  - [x] Returns source entries where `scrape` is `deep` or `shallow`, AND `discovered: false`, AND `proposed: false`, AND `role` is NOT `counter-example`.
  - [x] Counter-example entries with `scrape: deep` or `scrape: shallow` get a diagnostic (the user almost certainly meant `scrape: none`) and are treated as `none` regardless of declared value.
  - [x] Tests: each filter condition exercised independently; counter-example with non-`none` scrape diagnostic emitted; counter-example with `scrape: none` silent.

- [x] Add `format_design_for_prompt(spec) -> str`
  - [x] If both `user_prose` and `auto_generated` are present, format them in that order with a separator.
  - [x] If only one is present, return that one.
  - [x] If neither, return empty string.
  - [x] Tests: each combination produces expected output; user_prose comes first when both present.

- [x] Rewrite `format_spec_for_prompt` to serialize from dataclasses (prompt-injection safety invariant)
  - [x] Replace the existing implementation that returns `spec.raw`. The new implementation serializes from parsed `ProductSpec` fields, NOT from raw text.
  - [x] Include user-authored sections verbatim: `## Purpose`, `## Architecture`, `## Design.user_prose`, `## Scope`, `## Behavior`, `## Notes`.
  - [x] For `## Sources`: include only entries where `proposed: false` AND `discovered: false` AND `role` is NOT `counter-example`.
  - [x] For `## References`: include only entries where `proposed: false` AND no role is `counter-example` AND no role is `ignore`.
  - [x] For `## Design`: include `auto_generated` content alongside `user_prose` (autogen is derived from non-proposed visual targets only and has already been filtered upstream).
  - [x] Wrap output in the existing labelled prefix ("PRODUCT SPECIFICATION (authored by the user...") so existing consumers see equivalent framing.
  - [x] Update existing tests for `format_spec_for_prompt` (output format will differ) so they pin the new behavior.
  - [x] **Prompt-injection invariant test (highest-stakes test in the phase)**: construct a spec containing `proposed: true` source, `discovered: true` source, `counter-example` source, `proposed: true` reference, and `counter-example` reference, all with distinctive recognizable content; assert that `format_spec_for_prompt(spec)` output does NOT contain any of those entries' content. This test pins the safety property for all downstream LLM call sites.

- [x] Add `validate_for_run(spec) -> list[str]` and wire into `main.py`
  - [x] Returns list of human-readable error messages; empty list means OK to run.
  - [x] Errors: purpose-fill-in, architecture-fill-in, and the no-source-and-no-ref-and-sparse-purpose condition (no scrapeable sources AND no non-ignore references AND `## Purpose` shorter than 50 characters).
  - [x] `fill_in_design` produces a WARNING (not an error) per PARSER-design.md § Validation API. The "URL alone" common pattern is valid even when `## Design` has no user prose and no visual-target references — duplo can still proceed by inferring design from scraped product-reference pages. Warnings print but do not block execution.
  - [x] Warnings for unreviewed entries: count `proposed: true` references and `discovered: true` sources, emit one warning each summarizing counts and what to do.
  - [x] Wire `validate_for_run` into `main.py` so it runs after `read_spec` and before any pipeline work. If errors returned, print them to stderr and exit 1. Warnings print to stdout but do not block.
  - [x] Tests: each error condition produces the expected message; valid spec returns empty list; `fill_in_design` produces warning not error; warnings include correct counts.
  - [x] Backward compatibility: old-format SPEC.md files (no fill-in markers anywhere because they predate the convention) keep `fill_in_purpose` and `fill_in_architecture` false and pass validation. Test this explicitly.

### Manual verification (all complete)

- [x] Write a fully-populated SPEC.md in the new format (every section filled, including `## Sources`, structured `## References`, `## Notes`) in a scratch directory and confirm `read_spec()` parses every section into the expected dataclass fields. Drop into a Python REPL or write a small script.
- [x] Write a SPEC.md with deliberate `proposed: true`, `discovered: true`, and `counter-example` entries. Call `format_spec_for_prompt(spec)` and visually confirm the output contains none of those entries' content. This is the safety invariant.
- [x] Write a SPEC.md with a fill-in marker left in `## Purpose`. Run `duplo` and confirm it exits 1 with a clear error message and does NOT proceed to scraping or extraction.
- [x] Run `duplo` against an existing pre-redesign project (one with no SPEC.md or an old-format SPEC.md). Confirm it still runs end-to-end without errors. The new validation should not block legacy projects until they migrate.
- [x] Write a SPEC.md with a reference path containing spaces (e.g. a list-item entry naming `ref/Screen Shot 2025-10-12 at 14.30.png`) and confirm it parses without dropping the entry. Same for a quoted path with unusual characters.

---

# Phase 4: Migration detection gate (complete)

This phase added a single gate at the start of `duplo` (no-subcommand path) that detects pre-redesign projects and prints manual-migration instructions instead of running the pipeline against them. Intentionally small: a detection function, a wrapper that prints and exits, dispatch wiring in `main()`, and tests. The pipeline refactor itself is Phase 5 and is NOT part of this phase.

Design reference: `MIGRATION-design.md` (authoritative).

This phase shipped the Phase-2-message-text version ("author a SPEC.md by hand" — `duplo init` does not exist yet). Phase 6 will replace it with the `duplo init` version as a one-line change.

- [x] Add `needs_migration(target_dir: Path) -> bool` to `duplo/migration.py` (new module)
  - [x] Create `duplo/migration.py`. Import `re` and `Path`. Export `needs_migration`.
  - [x] Signal 1 (marker-string match, fast path): SPEC.md contains the literal substring `"How the pieces fit together:"`. This string appears in the top-matter comment of SPEC-template.md and will be present in any SPEC.md created by `duplo init` (once it ships) or by a user copying the template.
  - [x] Signal 2 (schema-structural match, fallback): SPEC.md contains an `## Sources` heading (matched via `re.search(r"^## Sources\s*$", spec_text, re.MULTILINE)`). Either signal is sufficient to classify as new-format.
  - [x] Returns False when `.duplo/duplo.json` does not exist (not a duplo project).
  - [x] Returns True when `.duplo/duplo.json` exists AND SPEC.md is absent OR SPEC.md has neither signal.
  - [x] Why two signals: Phase 4 instructs users to author SPEC.md by hand using the template as a starting point. A user who writes a valid minimal new-format SPEC.md without copying the top-matter comment would otherwise stay stuck in migration forever. The `## Sources` structural signal is the lowest-ceremony marker of new-format intent.
  - [x] Tests:
    - [x] returns True for old layout (has `.duplo/duplo.json`, no SPEC.md)
    - [x] returns True for old layout with an old-format SPEC.md (has `.duplo/duplo.json`, SPEC.md exists but has neither marker nor `## Sources`)
    - [x] returns False for new-format with marker string (has `.duplo/duplo.json`, SPEC.md contains `"How the pieces fit together:"`)
    - [x] returns False for new-format with `## Sources` heading but no marker string (structural fallback)
    - [x] returns False when `.duplo/duplo.json` does not exist (not a duplo project at all)
    - [x] returns False when both signals present (belt-and-braces)
    - [x] `## Sources` check uses multiline anchor so an `## Sources` line mid-document matches, but a line like `My sources` or `### Sources` does not

- [x] Add the migration message constant and `_check_migration` wrapper
  - [x] Define `_MIGRATION_MESSAGE` as a module-level constant in `duplo/migration.py` containing the migration message text verbatim per MIGRATION-design.md § Behavior (the "Phase 2 message" block — the version that says "Author a SPEC.md by hand using SPEC-template.md"). Do NOT use the Phase 4 version (which references `duplo init`); `duplo init` does not exist yet.
  - [x] Message lists the five steps: create `ref/`, move reference files, hand-author SPEC.md using SPEC-template.md with minimum fields (Purpose, Architecture, Sources, References), run `duplo` again. Mentions that PLAN.md, `.duplo/duplo.json`, and source code are unchanged.
  - [x] Implement `_check_migration(target_dir: Path) -> None` per MIGRATION-design.md § Implementation. If `needs_migration(target_dir)` returns True, print `_MIGRATION_MESSAGE` and `sys.exit(1)`. Otherwise return without doing anything.
  - [x] Tests:
    - [x] `_check_migration` on an old-layout directory: patches `sys.exit` and `print` (or captures via `capsys`), confirms the message is printed and exit is called with code 1
    - [x] `_check_migration` on a new-format directory: no output, no exit, function returns None
    - [x] `_check_migration` on a non-duplo directory (no `.duplo/duplo.json`): no output, no exit
    - [x] Message text test: pin the exact message content by snapshot comparison to a fixture file. This protects against accidental wording drift.

- [x] Wire `_check_migration` into `main.py` dispatch
  - [x] Per MIGRATION-design.md § Implementation "Phase 2 dispatch order": at the top of `main()`, after argv parsing but before any other work, branch on subcommand. If subcommand is `fix` or `investigate`, dispatch to the existing handlers WITHOUT calling `_check_migration` (those subcommands work on already-initialized projects regardless of layout and should not be blocked by migration).
  - [x] If there is no subcommand (the default `duplo` invocation), call `_check_migration(Path.cwd())` FIRST, before any other work. If `_check_migration` exits, nothing else in `main()` runs.
  - [x] If `_check_migration` returns, proceed with the existing no-subcommand code path unchanged. `_first_run` and `_subsequent_run` are NOT touched in this phase.
  - [x] Do NOT add an `init` branch — `duplo init` does not exist yet; that lands in Phase 6. If the user types `duplo init` today, argparse should reject it with an unknown-subcommand error as it does now.
  - [x] Tests (these are integration-style tests against `main`, using `capsys` and monkeypatching `sys.argv` / `Path.cwd`):
    - [x] `duplo` (no args) in an old-layout temp directory: prints migration message, exits 1, does not call `_subsequent_run` or `_first_run`
    - [x] `duplo` (no args) in a new-format temp directory: migration check passes silently, proceeds to the existing dispatch (may exit for other reasons like missing purpose, but NOT the migration message)
    - [x] `duplo fix` in an old-layout directory: bypasses migration check, dispatches to existing `fix` handler. Confirm by patching the fix handler and asserting it was called.
    - [x] `duplo investigate` in an old-layout directory: same as above for the investigate handler.

- [x] Add tests for edge cases specific to migration detection
  - [x] Case: `.duplo/duplo.json` is corrupted JSON. `needs_migration` should still return True (the presence of the file, not its contents, is what matters for migration detection). The check must NOT try to parse it.
  - [x] Case: `SPEC.md` is zero bytes. Same classification as "SPEC.md absent" — neither signal matches, so migration needed.
  - [x] Case: `SPEC.md` contains only the marker string inside an HTML comment (`<!-- How the pieces fit together: ... -->`). The substring match still hits; classifies as new-format. This is intentional — the marker exists in the template as part of a comment, and that's where it will appear in real specs. No special comment-handling needed.
  - [x] Case: `SPEC.md` is a BOM-prefixed UTF-8 file. The read must handle BOM correctly (use `Path.read_text(encoding="utf-8")` which strips BOM automatically, or equivalent). Test with a fixture that has a UTF-8 BOM and a new-format signal.
  - [x] Case: `SPEC.md` contains `## Sources` inside a fenced code block (e.g. an example in the top-matter comment). The multiline regex will match this as a false positive — document this as acceptable behavior (better to let through a near-new-format file than to force-migrate it) but add a test pinning the current behavior so any future fix is intentional.

- [x] Update project documentation to reflect Phase 4 shipping
  - [x] In `README.md` (if it exists at the project root): add a short section or update the existing "Getting started" to mention that existing duplo projects will be prompted to migrate on their next run, and that migration is manual (author SPEC.md by hand; `duplo init` is not available yet).
  - [x] In `CLAUDE.md` (if it exists): if it currently mentions the old subcommand model or describes duplo's behavior in a way that's now stale, update to reference the current state (Phase 4 shipped: migration gate is in place; pipeline still uses `_subsequent_run` / `_first_run` as before).
  - [x] Do NOT update SPEC-template.md, SPEC-guide.md, or any design doc in `/Users/mhcoen/proj/duplo/*.md` — those are the forward-looking design specifications and are already current.

### Manual verification (all complete)

- [x] Create a scratch directory that looks like a pre-redesign duplo project (`mkdir -p scratch/.duplo && echo '{}' > scratch/.duplo/duplo.json`). Do NOT create a SPEC.md. Run `duplo` from that directory. Confirm the migration message prints and the command exits with status 1. Confirm the message contents match MIGRATION-design.md's Phase 2 message exactly.
- [x] In the same scratch directory, author a minimal new-format SPEC.md by hand: `## Purpose`, `## Architecture`, and an empty `## Sources` section (just the heading). Run `duplo` again. Confirm it does NOT print the migration message and instead proceeds into the existing pipeline (which will likely error on other grounds like missing purpose content — that's expected; the point is that the migration gate no longer fires).
- [x] In a completely empty directory (no `.duplo/` at all), run `duplo`. Confirm `needs_migration` returns False and `duplo` proceeds to its existing no-duplo-project behavior. No migration message should appear.
- [x] In a pre-redesign scratch directory, run `duplo fix` and `duplo investigate`. Confirm neither prints the migration message — they dispatch to their existing handlers unchanged.
- [x] Run the full test suite: `pytest -x`. Confirm no pre-existing tests broke. Confirm the new migration tests pass.

---

# Phase 5: Pipeline integration

Wires the new SPEC-driven inputs through the actual orchestration code in `main.py`. The largest phase in the redesign.

Design reference: `PIPELINE-design.md` (authoritative). All tasks in this phase reference design sections by name; the design doc is the source of truth for contracts and edge cases. When a task description and the design doc disagree, the design doc wins; flag the discrepancy for resolution rather than silently picking one interpretation.

The principle: every pipeline stage takes role-filtered input from the parser instead of running heuristics on raw directory contents. Implementation order respects dependencies — foundation (URL canonicalization, fetcher, helper) before pipeline-stage updates, helpers before orchestration.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all code generation. Ruff for linting, pytest for tests. All AI calls go through `claude -p` (no direct API calls).

## Pre-work: missing per-stage formatter

- [x] [BATCH] Add `format_counter_example_sources(spec) -> list[SourceEntry]` to `duplo/spec_reader.py`
  - [x] Returns source entries where `role` is `counter-example`, excluding `proposed: true` AND `discovered: true`. Per PIPELINE-design.md § `format_counter_example_sources`.
  - [x] This is the missing per-stage formatter from Phase 3 — Phase 3 added the four reference formatters and `format_scrapeable_sources` but not this one. Required by the investigator changes in 5.11.
  - [x] Tests: returns counter-example sources only; excludes proposed/discovered; empty input handled; counter-example sources with other flags (e.g. `scrape: deep`, which the user almost certainly didn't mean) still returned by this filter (separate concern from the scrape-depth diagnostic emitted by `format_scrapeable_sources`).

## Foundation: URL canonicalization

- [x] [BATCH] Create new module `duplo/url_utils.py` with `canonicalize_url(url: str) -> str`
  - [x] New module per design (the existing files are too long). Imports stdlib only (`urllib.parse`).
  - [x] Implement the four canonicalization rules per PIPELINE-design.md § "URL canonicalization": (1) lowercase scheme and host; (2) strip default ports (80 on http, 443 on https); (3) strip fragment (#section); (4) strip trailing slash from ALL paths INCLUDING the root path `/`. Preserve query strings.
  - [x] The trailing-slash rule MUST apply to the root path: `https://a.com/` → `https://a.com`. Do not special-case the root. Per PIPELINE-design.md § "Why strip all trailing slashes, including root" — root-path slash treatment is what makes user-authored host-only URLs and fetcher post-redirect URLs compare equal.
  - [x] Tests: each rule exercised individually; combined rules; root-path slash stripped (`https://a.com/` → `https://a.com`); non-root path slash stripped (`https://a.com/docs/` → `https://a.com/docs`); already-canonical URL unchanged; query string preserved (`https://a.com/?q=1` → `https://a.com?q=1` — root slash gone, query kept); fragment stripped; uppercase scheme/host lowercased; default port stripped on http (80) and https (443); non-default ports preserved (`https://a.com:8443/` → `https://a.com:8443`).

## Foundation: fetch_site signature changes

- [x] Add `scrape_depth` parameter and 5-tuple return to `duplo/fetcher.py:fetch_site`
  - [x] Per PIPELINE-design.md § `fetcher.py`. New signature: `fetch_site(url, *, scrape_depth: Literal["deep", "shallow", "none"] = "deep") -> tuple[str, list[CodeExample], DocStructures, list[PageRecord], dict[str, str]]`.
  - [x] `scrape_depth="deep"` follows links but ONLY same-origin (same scheme + host + port). Cross-origin links are NOT fetched in the same run — they are extracted later by `_collect_cross_origin_links` for SPEC.md `discovered:` write-back.
  - [x] `scrape_depth="shallow"` fetches only the entry URL, no link-following.
  - [x] `scrape_depth="none"` does no fetch, returns empty content tuple plus empty `raw_pages` dict.
  - [x] The fifth return value `raw_pages: dict[str, str]` maps EVERY successfully fetched canonical URL to its raw HTML. For `deep`, includes entry URL plus same-origin pages followed and successfully fetched. For `shallow`, exactly one entry on success, empty dict on failure. For `none`, empty dict.
  - [x] All URL keys in `raw_pages` and all `PageRecord.url` values MUST be canonicalized via `url_utils.canonicalize_url`. Apply post-redirect (after the HTTP response, on the final URL the fetcher landed on).
  - [x] Failed fetches (404, timeout, non-HTML content-type, decode failure) are NOT included in `raw_pages` and NOT included in `page_records`. Both structures stay in sync by construction. Failure surfaces via `record_failure("fetch_site", "fetch", ...)`.
  - [x] HTML decode: UTF-8 with `errors="replace"` per the design.
  - [x] Update existing callers of `fetch_site` in `duplo/main.py` to handle the new 5-tuple. Existing call sites that don't yet need `raw_pages` should still unpack it (assign to `_` if unused) so they don't crash on the tuple-length change.
  - [x] Tests: 5-tuple return shape; `scrape_depth="shallow"` fetches only entry URL and returns one `raw_pages` entry; `scrape_depth="deep"` follows same-origin links and returns multiple entries; `scrape_depth="deep"` does NOT fetch cross-origin links (cross-origin URL not in `raw_pages`, no PageRecord for it); `scrape_depth="none"` does no HTTP and returns empty `raw_pages`; failed fetch (mock 404) omits the URL from BOTH `raw_pages` AND `page_records`; canonical URL keys (post-redirect URL canonicalized); decode error doesn't crash, omits the URL with diagnostic.

## Foundation: cross-origin link collection helper

- [x] [BATCH] Implement `_collect_cross_origin_links(raw_pages, source_url) -> list[str]` in `duplo/orchestrator.py` (new module)
  - [x] Per PIPELINE-design.md § `_collect_cross_origin_links`. Place in a new `duplo/orchestrator.py` module since `main.py` is already long; helper functions used by orchestration go here.
  - [x] Parse each HTML page in `raw_pages.values()`, extract every `<a href="...">` target, resolve to absolute URL, canonicalize via `url_utils.canonicalize_url`.
  - [x] Compare canonical form's (scheme, host, port) against the canonical `source_url`'s. Different = cross-origin = include in result.
  - [x] Only `<a href>` is considered. NOT `<link>`, `<script src>`, `<img src>`, `<video src>`, `<source src>`, etc. Per design § "Decisions".
  - [x] Return deduplicated list of canonical URLs. Per design: dedup happens here (per-run) and again in `append_sources` (against existing SPEC.md). Belt and braces; both use `canonicalize_url` so divergence is impossible by construction.
  - [x] Tests: same-origin links excluded; cross-origin links included; subdomain treated as cross-origin (`https://numi.app` vs `https://docs.numi.app` are different hosts); only `<a href>` collected (`<img src>` to cross-origin CDN is NOT collected); duplicates within a single page collapsed; duplicates across pages collapsed; canonicalization applied (uppercase or trailing-slash variants of the same URL collapse to one); empty `raw_pages` returns `[]`; relative href resolved against the page URL it appeared on (not against `source_url`) — a relative `href="docs"` on `https://example.com/foo/page.html` resolves to `https://example.com/foo/docs`, not `https://example.com/docs`.

## Pipeline stage updates

- [x] Refactor `duplo/scanner.py:scan_directory` to point at `ref/` and drop relevance heuristics
  - [x] Per PIPELINE-design.md § `scanner.py`. `scan_directory(target_dir)` becomes `scan_directory(ref_dir)`; callers that pass `"."` change to pass `target_dir / "ref"`.
  - [x] Drop the relevance scoring (image dimensions, file size). Roles are declared in `## References`, not inferred.
  - [x] Add a diagnostic for files in `ref/` that are not listed in `## References`: `record_failure("scanner", "io", f"file in ref/ has no entry in ## References; will be ignored: {path}")`. Diagnostic only — does not error.
  - [x] `scan_files(paths)` (used for analyzing specific changed files in subsequent runs) keeps working but gets a parallel role lookup: each file's path is checked against the parsed `## References` to determine its role.
  - [x] Update existing callers in `duplo/main.py` to pass `ref/` instead of project root.
  - [x] Tests: `scan_directory` only enumerates files under `ref/`, ignoring everything else in project root; file in `ref/` listed in `## References` is included with its declared role; file in `ref/` NOT listed in `## References` produces diagnostic and is excluded from the result; relevance heuristics removed (a tiny image is included if declared, a huge irrelevant one is excluded if not declared); `scan_files` role-lookup matches paths against `## References` correctly.

- [x] Refactor `duplo/extract_design` callers to use `format_visual_references` and the four-source design input set
  - [x] Per PIPELINE-design.md § `design_extractor.py`. The design input is the union of: (1) `format_visual_references(spec)` paths; (2) accepted frames from videos with `visual-target` in their roles; (3) accepted frames from scraped product-reference videos; (4) images downloaded from product-reference sources via `_download_site_media`.
  - [x] All four sources MUST exclude `proposed: true` references and frames derived from them. Filter via the existing per-stage formatters which already enforce this.
  - [x] Implement frame-content-hash dedup per design § "Compose the design extraction input set from FOUR sources". A user with both a ref/-declared local copy of a demo video AND the same video appearing on a scraped product page should not have its frames counted twice. Use `hashlib.sha256(frame.read_bytes()).hexdigest()` as the dedup key. ref-declared frames win on collision (added to seen set first).
  - [x] Update `extract_design`'s call site in `duplo/main.py` to pass `design_input` composed per the rules above instead of the current project-root scan.
  - [x] Implementation lives in the orchestrator (composition of input set), not in `design_extractor.py` itself. `extract_design` continues to take `list[Path]`.
  - [x] Tests: visual-target ref files included; non-visual ref files excluded; `proposed: true` visual ref excluded; dual-role behavioral+visual video contributes its accepted frames; behavioral-only video does NOT contribute frames to design; scraped product-reference video frames included; non-product-reference scraped videos do NOT contribute; site media images included; frame-content-hash dedup: same-content frame from ref/ and scraped path counted once; ref-declared frame wins on hash collision.

- [x] Refactor video pipeline to use `format_behavioral_references`
  - [x] Per PIPELINE-design.md § `video_extractor.py and friends`. Callers of `extract_all_videos` pass paths from `format_behavioral_references(spec)` instead of all videos.
  - [x] EXTEND the behavioral input set with `site_videos` (the second element of `_download_site_media`'s return tuple) per the orchestration sketch. Scraped demo videos from product-reference pages are first-class behavioral input.
  - [x] Pin the source-path-preservation contract for `extract_all_videos` per PIPELINE-design.md § `_accepted_frames_by_source` "Source-path preservation contract": `ExtractionResult.source` MUST equal the input path byte-for-byte — no `Path.resolve()`, no symlink following, no normalization. Enforce in code (no transformation in `extract_all_videos`) and pin with a test that passes a relative path and asserts `result.source` equals that same relative path.
  - [x] Add an assertion at the orchestrator's behavioral-paths construction point per the orchestration sketch: `assert len(behavioral_paths) == len(set(behavioral_paths))`. ref/ and `.duplo/site_media/` live under different roots so collisions require user error; the assert surfaces that error visibly.
  - [x] Tests: `format_behavioral_references` paths are passed to `extract_all_videos`; site_videos are added; ref/ video and scraped video both present in input; source-path-preservation: relative input path round-trips through `ExtractionResult.source` unchanged; collision assertion fires when same path appears in both lists.

- [x] [BATCH] Implement `_accepted_frames_by_source(filtered_results) -> dict[Path, list[Path]]` helper in `duplo/orchestrator.py`
  - [x] Per PIPELINE-design.md § `_accepted_frames_by_source`. One-line implementation (`{r.source: r.frames for r in filtered_results}`) but must live as a named helper so the post-filter contract has a named place in tests.
  - [x] **Critical**: the input must be POST-FILTER. Callers MUST run `frame_filter.apply_filter` on `ExtractionResult.frames` before passing to this helper. The orchestration sketch uses `dataclasses.replace(r, frames=apply_filter(filter_frames(r.frames)))` to preserve `source` and `error` while replacing `frames`.
  - [x] Tests: (a) lookup returns correct frames per source; (b) if called with unfiltered results (rejected frames present), rejected frames appear in output — demonstrating the contract violation is detectable; (c) source-path preservation: keys equal the input `ExtractionResult.source` values byte-for-byte (no path transformation).

- [x] Refactor PDF/text/markdown doc extraction with `docs_text_extractor`
  - [x] Per PIPELINE-design.md § `pdf_extractor.py and text/markdown docs`. New function `docs_text_extractor` that takes references with `docs` in `roles` and produces a single text blob per file, routed by extension.
  - [x] Routing: `.pdf` → existing `extract_pdf_text` path; `.txt` → read directly; `.md` → read directly (markdown is text; the LLM handles formatting).
  - [x] Place `docs_text_extractor` in `duplo/pdf_extractor.py` (rename file later if it becomes misleading) OR in a new `duplo/docs_extractor.py` module. The new module is preferred per the "new module over extending long files" preference.
  - [x] Combined text feeds into feature extraction the same way today's PDF text does. Update the `extract_features` call site to include doc-references-derived text.
  - [x] Tests: PDF input routes to `extract_pdf_text`; `.txt` input read directly; `.md` input read directly; unknown extension produces diagnostic and is skipped; multiple docs combined into one blob.

- [x] Refactor `extract_features` callers and add `_matches_excluded` post-extraction filter
  - [x] Per PIPELINE-design.md § `extractor.py (feature extraction)`. `scraped_text` becomes the concatenation of text from all scrapeable sources. `spec_text` continues to use `format_spec_for_prompt(spec)` (which already excludes unreviewed entries per Phase 3). `scope_include`/`scope_exclude` come from `spec.scope_include`/`spec.scope_exclude`.
  - [x] Implement `_matches_excluded(feature, scope_exclude) -> bool` per design § `_matches_excluded`. Place in `duplo/orchestrator.py` (new module from earlier task) or `duplo/extractor.py` if it fits naturally there.
  - [x] Matching rule: case-insensitive WORD-BOUNDARY regex (`\b...\b`), NOT substring. Multi-word excluded terms must match as contiguous word sequence. Per design: `"plugin API"` matches `"Plugin API"` and `"plugin API."` but not `"non-plugin-API"` or a description that mentions `"plugin API"` only as contrast.
  - [x] Compare against BOTH `feature.name` and `feature.description`.
  - [x] When a feature is dropped, emit diagnostic via `record_failure("extractor:scope_exclude", "...", f"scope_exclude '<term>' matched feature '<n>'; dropped")`. Use whichever of the existing diagnostics categories fits best (likely `"io"` since `extractor` doesn't have a dedicated category); flag in code review if a new category is warranted.
  - [x] Apply the post-extraction filter at the orchestrator level: `features = [f for f in features if not _matches_excluded(f, spec.scope_exclude)]` after `extract_features` returns, before `save_features`.
  - [x] Tests: word-boundary match (positive cases for exact phrase, with trailing punctuation, with leading whitespace); word-boundary non-match (negative cases for substring-only matches like `"non-plugin-API"`, `"plugins-API"`); case-insensitive; multi-word excluded term must match as contiguous sequence (`"plugin API"` excluded does NOT match a description that mentions `"plugin"` and `"API"` separately); feature dropped produces diagnostic naming term and feature; empty `scope_exclude` is no-op; multiple matches emit one diagnostic per (term, feature) pair.

- [x] Refactor `_download_site_media` per the new signature
  - [x] Per PIPELINE-design.md § `_download_site_media signature under the new model`. New signature: `_download_site_media(raw_pages: dict[str, str]) -> tuple[list[Path], list[Path]]` returning `(image_paths, video_paths)`.
  - [x] Parameter is the dict of product-reference raw pages (NOT all raw pages). Caller passes `product_ref_raw_pages` per the orchestration sketch.
  - [x] Returns paths for EVERY embedded media resource that exists locally — BOTH newly-downloaded files AND files already present in cache. Per design § "Cached-vs-new rule": a URL-only project's second run finds all media cached; if the function returned only new paths, design extraction would silently get zero inputs on regeneration.
  - [x] Storage: `.duplo/site_media/<url-hash>/<filename>`. URL hash is the hash of the page URL the media was embedded in; filename is derived from the resource URL.
  - [x] Embedded-media origin handling per design § "Same-origin and embedded media": media is downloaded REGARDLESS of origin. The user authorized loading the page; the page's content includes its embedded media. This differs from cross-origin link behavior (which is recorded as discovered, not fetched).
  - [x] Parse `<img src>`, `<video src>`, AND `<source src>` tags. Resolve to absolute URLs against the embedding page URL (not against any `source_url`).
  - [x] Tests: image URL embedded in a page is downloaded and path returned; video URL same; cross-origin CDN image is downloaded (not skipped); cached file returns its existing path without re-downloading; mix of cached and new files all returned; zero embedded media returns `([], [])`; multiple pages each contributing media yields combined lists; HTTP failure on a single embed records diagnostic and skips that file but doesn't abort the function.

- [x] Refactor `gap_detector` callers to pre-filter through `scope_exclude`
  - [x] Per PIPELINE-design.md § `gap_detector.py`. No change to `detect_gaps` itself. The features list passed in is filtered through `scope_exclude` at the orchestrator level (handled by the previous `_matches_excluded` task) before `detect_gaps` is called.
  - [x] Verify no existing call site of `detect_gaps` bypasses the filter. If any do, route them through the same filter.
  - [x] `detect_design_gaps` operates on the AUTO-GENERATED block in SPEC.md's `## Design` section AS WELL AS on `duplo.json`'s `design_requirements` (redundant during transition; can simplify in Phase 7).
  - [x] Tests: feature list passed to `detect_gaps` excludes scope_exclude'd entries; `detect_design_gaps` reads from both AUTO-GENERATED block and `duplo.json` (verify both code paths exist).

## Investigator

- [x] Update investigator to include counter-examples, counter-example sources, docs references, and behavior contracts
  - [x] Per PIPELINE-design.md § `investigator.py`. `investigate(bugs, ...)` gains role-filtered context inputs.
  - [x] Counter-example references via `format_counter_examples(spec)` get included in the prompt with an explicit "AVOID this pattern" label.
  - [x] Counter-example SOURCES via `format_counter_example_sources(spec)` (the new formatter from the pre-work task) get included as URL+notes context with the same "AVOID" framing. **The URL is NOT fetched** — declarative context only. Pin this with a test.
  - [x] Docs references via `format_doc_references(spec)` get included as supplementary context (PDF text via `extract_pdf_text`, .txt/.md via direct read — reuse the `docs_text_extractor` from the earlier task).
  - [x] `## Behavior` contracts via `spec.behavior_contracts` get included as ground-truth expectations.
  - [x] Update the investigator's structured-output prompt so that diagnoses can reference these new context types: e.g. `Diagnosis(... contradicts: "behavior contract X")` or `Diagnosis(... avoids_pattern: "counter-example Y")`. The exact prompt rewording is at Claude Code's discretion as long as the structure supports referencing the new context.
  - [x] Tests: counter-example refs included with AVOID label; counter-example source URLs included with AVOID label and NOT fetched (mock the fetcher and assert it was not called for counter-example URLs); docs refs included as supplementary; behavior contracts included as ground-truth; investigator output structure supports referencing all new context types.

## Drafter write helpers (minimal subset)

- [x] [BATCH] Create `duplo/spec_drafter.py` with `append_sources(spec_text, new_entries) -> str`
  - [x] New module per the design (text-layer module independent of pipeline stages — must NOT import from `duplo/extractor.py`, `duplo/design_extractor.py`, etc.).
  - [x] `append_sources(existing_spec_text: str, new_entries: list[SourceEntry]) -> str` returns modified spec text with new entries appended to `## Sources`.
  - [x] Dedup-by-canonical: skip entries whose canonical URL already exists in the spec's `## Sources` (regardless of whether the existing entry has `proposed:` or `discovered:` flags). Use `url_utils.canonicalize_url` for comparison; the parser stores canonical URLs in `SourceEntry.url` already (per Phase 3) so existing entries are already canonical.
  - [x] Idempotent: calling `append_sources(s, [])` returns `s` unchanged. Calling `append_sources(append_sources(s, [e]), [e])` returns the same string as `append_sources(s, [e])` (the second call's `e` is dedup'd).
  - [x] Format new entries with their flags: `discovered: true` and/or `proposed: true` lines appear as field lines under the entry per PARSER-design.md § `## Sources` parser format.
  - [x] If `## Sources` section does not exist in `existing_spec_text`, create it (heading + entries) appended to the spec. Place it after `## Architecture` if present, else at end of file. Maintain the same blank-line conventions as the rest of SPEC.md.
  - [x] Tests: append single new entry; append multiple; dedup against existing canonical URL (entry not added); dedup against existing URL with different trailing slash (canonicalization in action); dedup is case-insensitive on host; idempotent (double-call returns same result); empty new_entries returns input unchanged; missing `## Sources` section is created; flags `discovered: true` and `proposed: true` written correctly.

- [x] [BATCH] Add `update_design_autogen(spec_text, body) -> str` to `duplo/spec_drafter.py`
  - [x] `update_design_autogen(existing_spec_text: str, body: str) -> str` returns modified spec text with the AUTO-GENERATED block in `## Design` populated.
  - [x] Write-once-never-replace semantics per PIPELINE-design.md § "Note on the autogen-cache divergence": if a well-formed AUTO-GENERATED block already exists with non-empty body, return `existing_spec_text` unchanged. The orchestrator is responsible for checking and skipping the Vision call when an autogen block already exists; this function is a defense-in-depth no-op in that case rather than an overwrite.
  - [x] If `## Design` section exists with no AUTO-GENERATED block: append the block (with BEGIN/END comment markers per PARSER-design.md § `## Design` parser) at the end of the section, after any existing user prose.
  - [x] If `## Design` section does not exist: create it with the AUTO-GENERATED block. Place after `## Architecture` (or after `## Sources` if both present). Maintain blank-line conventions.
  - [x] BEGIN/END markers: use the EXACT same comment-marker form that the parser's `_AUTOGEN_RE` matches (per PARSER-design.md). Pin with a test that round-trips through the parser.
  - [x] Tests: empty `## Design` gets autogen block appended; existing user prose in `## Design` preserved with autogen appended after it; existing autogen block with non-empty body NOT replaced (write-once); existing autogen block with empty body is replaced (allows regeneration after user clears the block); missing `## Design` section is created; round-trip: `update_design_autogen` output parses back to a spec where `spec.design.auto_generated` equals the body.

## Save_raw_content update

- [x] [BATCH] Update `duplo/saver.py:save_raw_content` per the new signature
  - [x] Per PIPELINE-design.md § `save_raw_content` signature. New signature: `save_raw_content(raw_pages: dict[str, str], page_records: list[PageRecord], *, target_dir: Path = Path.cwd()) -> None`.
  - [x] For each `PageRecord`, look up `raw_pages[record.url]` and write the HTML to `.duplo/raw_pages/<sha256(record.url)>.html`.
  - [x] Cache filename is the SHA-256 of the canonical URL. NOT the content hash. `PageRecord.content_hash` continues to be stored inside the record for change detection but is NOT used for the cache filename.
  - [x] **Behavior on missing key**: if `record.url` has no entry in `raw_pages`, this indicates a construction-invariant violation. Log via `record_failure("save_raw_content", "io", f"no raw_pages entry for {record.url}; record skipped")` and SKIP that record. Do NOT raise. Per design § "Behavior on missing keys: log and skip, do not raise."
  - [x] Tests: each record's HTML written to URL-hashed filename; URL-hash filename matches `sha256(record.url).hexdigest()`; existing file at the same hash overwritten (one file per URL); missing key for a record skipped with diagnostic; remaining records still persisted when one is skipped; empty `raw_pages` and empty `page_records` no-op without error.

## BuildPreferences and app_name

- [x] Implement `parse_build_preferences(architecture_prose) -> BuildPreferences` in `duplo/build_prefs.py` (new module)
  - [x] Per PIPELINE-design.md § BuildPreferences. New module per the "new module over extending long files" preference. NOT in `spec_reader.py` (PARSER-design.md forbids LLM calls there) and NOT in `questioner.py` (which is being replaced).
  - [x] Calls `claude -p` with structured-output prompt asking for `{platform, language, framework, dependencies: list[str], other_constraints: list[str]}` extracted from the prose. Returns `BuildPreferences` with whatever fields the LLM populated; missing fields stay at default.
  - [x] Section-scoped hash invalidation per design: the bytes hashed are `spec.architecture` (the parsed, comment-stripped content of `## Architecture`), NOT the whole SPEC.md file. Stored in `.duplo/duplo.json` under `architecture_hash`. Re-parse only when the hash changes.
  - [x] When the LLM returns no usable fields, return `BuildPreferences()` (all defaults). Surface as a WARNING via `validate_for_run`, not an error — plan generation handles all-defaults gracefully.
  - [x] Tests: parse with a typical architecture prose (Swift macOS app etc.); fields populated correctly; missing fields default; hash invalidation works (changing architecture re-triggers parse); commented-out content in `## Architecture` does NOT change hash (per PARSER-design.md `_strip_comments` runs before storage); cache hit avoids the LLM call; all-defaults BuildPreferences emits warning via `validate_for_run`.

- [x] [BATCH] Implement app_name derivation logic in `duplo/orchestrator.py`
  - [x] Per PIPELINE-design.md § app_name. New function `derive_app_name(spec, target_dir) -> str`.
  - [x] If `## Sources` includes a product-reference URL, derive a candidate app_name from the scraped product identity using existing `validator.validate_product_url` behavior (or whatever produces the product name today).
  - [x] If no URL, derive from project directory name as fallback (`numi-clone/` → `numi-clone`).
  - [x] Stored in `.duplo/product.json` under `app_name`. The user can edit this file directly if the auto-derived name is wrong.
  - [x] Tests: URL-based derivation produces expected name; no-URL fallback uses directory name; `product.json` written; user-edited `product.json` is NOT overwritten on subsequent runs (load and preserve existing `app_name` if present).

## Orchestration: source iteration with first-source-wins dedup

- [x] Implement multi-source iteration loop in `_subsequent_run`
  - [x] Per PIPELINE-design.md § `main.py orchestration` orchestration sketch. Iterate `format_scrapeable_sources(spec)` and call `fetch_site` for each.
  - [x] Maintain `seen_canonical_urls: set[str]` for first-source-wins dedup of `PageRecord` entries.
  - [x] Maintain `all_raw_pages: dict[str, str]` and `product_ref_raw_pages: dict[str, str]` using `setdefault` (NOT `update` — dict.update would silently let later sources overwrite earlier; setdefault preserves first-source-wins).
  - [x] Accumulate `combined_text`, `all_code_examples`, `merged_doc_structures` across sources.
  - [x] `discovered_urls` collected from `_collect_cross_origin_links(source_raw_pages, source.url)` ONLY when `source.scrape == "deep"`. Per design: shallow sources fetched only the entry URL; collecting cross-origin links and recording them as `discovered: true` would silently append URLs the user never asked duplo to explore. Pin with a test.
  - [x] After the loop, if `all_code_examples`, call `save_examples(all_code_examples)`. If `all_page_records`, call `save_reference_urls(all_page_records)` and `save_raw_content(all_raw_pages, all_page_records)`. If `merged_doc_structures`, call `save_doc_structures(merged_doc_structures)`.
  - [x] Tests: multi-source iteration calls `fetch_site` once per scrapeable source; first-source-wins dedup of page_records (URL appearing in source A and source B is recorded once with source A's record); first-source-wins for raw_pages (HTML from source A preserved over source B for the same canonical URL); discovered_urls collected only from `deep` sources, NOT from `shallow`; non-product-reference sources do not contribute to product_ref_raw_pages; doc_structures merged across sources.

- [x] [BATCH] Wire SPEC.md write-back for discovered URLs in `_subsequent_run`
  - [x] After the source iteration loop, if `discovered_urls` is non-empty: read SPEC.md from disk, call `spec_drafter.append_sources(existing, [SourceEntry(url=u, role="docs", scrape="deep", discovered=True) for u in discovered_urls])`, and write back ONLY if the result differs from the input. Per PIPELINE-design.md orchestration sketch.
  - [x] Default `role="docs"` and `scrape="deep"` for discovered entries per the design.
  - [x] Tests: discovered URLs trigger SPEC.md write; SPEC.md unchanged when all discovered URLs are already in `## Sources` (idempotency through `append_sources` dedup); `discovered: true` flag and `role: docs` written; SPEC.md NOT modified when `discovered_urls` is empty.

## Orchestration: design extraction with autogen-skip

- [x] Compose design input set from four sources in `_subsequent_run`
  - [x] Per PIPELINE-design.md orchestration sketch "Compose the design extraction input set from FOUR sources".
  - [x] Sources: (1) `format_visual_references(spec)` paths; (2) accepted frames from videos with `visual-target` in roles via `accepted_frames_by_path.get(entry.path, [])`; (3) accepted frames from scraped `site_videos`; (4) `site_images` from `_download_site_media`.
  - [x] Apply frame-content-hash dedup per the design sketch: ref-declared frames (source 2) added to `seen_frame_hashes` first; scraped frames (source 3) added only if their content-hash is not already seen.
  - [x] `accepted_frames_by_path = _accepted_frames_by_source(filtered_results)` where `filtered_results` is the post-`apply_filter` list (use `dataclasses.replace(r, frames=apply_filter(filter_frames(r.frames)))` per the sketch).
  - [x] Tests: design_input contains all four sources when present; missing source gracefully omitted; frame-content-hash dedup verified with two videos containing identical frames at different paths; behavioral-only video does NOT contribute frames; `proposed: true` visual ref does NOT contribute.

- [x] Wire SPEC.md write-back for autogen design with skip-when-present in `_subsequent_run`
  - [x] Per PIPELINE-design.md orchestration sketch "Check autogen block FIRST via the in-memory dataclass".
  - [x] Check `autogen_present = bool(spec.design.auto_generated.strip())` from the in-memory `spec` (NOT a re-read of SPEC.md, NOT a second regex pass). Per the design § "in-memory spec is source of truth within a single run" invariant.
  - [x] If `design_input` AND NOT `autogen_present`: call `extract_design(design_input)`, then read SPEC.md from disk, call `update_design_autogen(existing, format_design_block(design))`, write back if changed, then `save_design_requirements(dataclasses.asdict(design))` for the cache.
  - [x] If `design_input` AND `autogen_present`: skip extraction. Emit diagnostic via `record_failure("orchestrator:design_extraction", "io", f"Autogen design block exists in SPEC.md; skipped Vision extraction. Delete the BEGIN/END AUTO-GENERATED block to regenerate from {len(design_input)} input image(s).")`.
  - [x] Cache invariant per design § "Note on the autogen-cache divergence": when autogen is skipped, `save_design_requirements` is ALSO skipped — the cache stays consistent with SPEC.md autogen.
  - [x] Tests: autogen-absent triggers Vision call and write-back; autogen-present skips Vision call AND skips cache write; skip emits diagnostic naming the input count; SPEC.md write only happens when content differs (idempotency); in-memory `spec.design.auto_generated` consulted, not a re-read of SPEC.md from disk.

- [x] [BATCH] Implement `format_design_block(design) -> str` in `duplo/design_extractor.py`
  - [x] Per PIPELINE-design.md § `format_design_block`. Wraps the existing `format_design_section(design)` in the same module, MINUS the section heading.
  - [x] **Lives in `design_extractor.py`, NOT `spec_drafter.py`** per the layering rationale (drafter must not depend on pipeline stages).
  - [x] The orchestrator imports `format_design_block` from `design_extractor` and passes the resulting string into `spec_drafter.update_design_autogen`. The drafter sees only a string.
  - [x] Tests: output equals `format_design_section(design)` minus the heading line; round-trip: `update_design_autogen(spec, format_design_block(design))` produces a spec where the parsed `spec.design.auto_generated` content reflects `design`'s fields.

## Orchestration: full _subsequent_run restructure

- [x] Restructure `_subsequent_run` to consume role-filtered inputs end-to-end
  - [x] This is the integration task that wires together everything from previous Phase 5 tasks. Follow the orchestration sketch in PIPELINE-design.md § `main.py orchestration` step by step.
  - [x] Order within the function: (1) `read_spec()`; (2) `validate_for_run(spec)` and exit on errors; (3) file-change detection (unchanged from today); (4) multi-source iteration loop with first-source-wins dedup; (5) save_examples / save_reference_urls / save_raw_content / save_doc_structures; (6) discovered-URLs SPEC.md write-back; (7) `extract_features` with merged scraped text and `_matches_excluded` post-filter; (8) `save_features`; (9) `_download_site_media(product_ref_raw_pages)` for site_images and site_videos; (10) behavioral-paths construction with collision assert; (11) `extract_all_videos` + filter + `_accepted_frames_by_source`; (12) compose design_input from four sources with frame-content-hash dedup; (13) check `autogen_present`, run Vision and write-back OR skip with diagnostic; (14) phase planning (unchanged from today).
  - [x] The in-memory `spec` from step 1 is the source of truth for ALL decisions in steps 2–13. SPEC.md is re-read ONLY in step 6 and step 13 (to stage writes), NOT to drive extraction. Per design § "in-memory spec is source of truth within a single run".
  - [x] `_first_run` is NOT touched in this phase per design § "`_first_run` removal is NOT part of Phase 3." That's Phase 7.
  - [x] Tests (integration-style, per design § "Test plan"): URL-only spec produces correct PLAN.md without consulting `ref/`; ref/-only spec produces correct PLAN.md without making any HTTP requests; both contribute to the plan; subsequent run with new files added to `ref/` produces `proposed: true` entries in SPEC.md and pipeline does NOT act on them; after user removes `proposed: true`, next run includes the files in pipeline stages.

## Orchestration: _fix_mode update

- [x] [BATCH] Update `_fix_mode` to use the new investigator with counter-examples and behavior contracts
  - [x] Per PIPELINE-design.md § `_fix_mode integration with new model`: "No structural change. The new investigator includes counter-examples and behavior contracts; existing `_fix_mode` tests should continue to pass with those added sources."
  - [x] Verify that `_fix_mode`'s call to `investigate(...)` passes the spec (or whatever context the investigator now needs to access counter-examples and behavior contracts via the formatters).
  - [x] Tests: existing `_fix_mode` tests still pass; new test that confirms counter-example references reach the investigator prompt when called from `_fix_mode`; new test for behavior contracts in `_fix_mode` context.

## Multi-source persistence in duplo.json

- [x] Add `sources` field to `.duplo/duplo.json` and update saver functions
  - [x] Per PIPELINE-design.md § "Multi-source persistence". `.duplo/duplo.json` gains a `sources` field: list of `{url, last_scraped, content_hash, scrape_depth_used}` entries, one per scrapeable source.
  - [x] Add `save_sources(sources_metadata)` and `load_sources()` functions to `duplo/saver.py`. Sources metadata accumulated during the iteration loop and persisted after.
  - [x] Backward compatibility per design: `.duplo/product.json` keeps the single `source_url` field, populated from the FIRST product-reference entry in `## Sources`. New code reads from the spec, not from `product.json`. The field is preserved only so old tooling and migration detection keep working.
  - [x] When user removes a URL from `## Sources`, the entry STAYS in `duplo.json` (idempotent state) but the pipeline doesn't re-scrape and doesn't include cached content in subsequent extractions. Per design.
  - [x] Tests: sources field populated correctly; existing `product.json:source_url` populated from first product-reference entry; removed source stays in `duplo.json` but is not re-scraped; `save_sources` is idempotent; multiple sources tracked independently.

## Automated integration tests

All Phase 5 end-to-end behaviors are verified by automated pytest integration tests, not by manual user runs. Each test constructs a fixture project in a tmpdir, runs duplo's pipeline programmatically, and asserts on the output state. Tests must NOT make real HTTP requests — use `unittest.mock.patch` on `duplo.fetcher.fetch_site` (or a local HTTP fixture if mocking is awkward) so the suite is hermetic and fast. Vision/LLM calls must also be mocked so tests don't depend on `claude -p` availability or network. All tests live in `tests/test_phase5_integration.py` (new file).

The earlier USER verification block was authored incorrectly: every scenario in it is automatable and should never have been a manual task. The standing rule is: never ask the user to do what the system can do itself. USER tasks are reserved for genuine human-judgment cases (e.g. "does this look visually correct"). None of these scenarios meet that bar. They are rewritten below as automated integration tests that mcloop will execute.

- [x] Add `tests/test_phase5_integration.py` with `test_url_only_spec_runs_end_to_end`
  - [x] Construct a tmpdir with a SPEC.md containing the marker comment, a `## Purpose` of >50 chars, a `## Architecture` block, and a `## Sources` block listing one entry with `role: product-reference` and `scrape: deep`. No `ref/` directory.
  - [x] Mock `duplo.fetcher.fetch_site` to return a fixture 5-tuple: a small scraped_text, empty code_examples, empty doc_structures, one PageRecord with the canonical URL, and a `raw_pages` dict mapping that URL to a small HTML fixture containing one `<a href>` to a same-origin path and one `<a href>` to a cross-origin path.
  - [x] Mock `duplo.design_extractor.extract_design` to return a deterministic DesignRequirements fixture.
  - [x] Mock `duplo.extractor.extract_features` to return a deterministic two-feature fixture.
  - [x] Mock `duplo.questioner.select_features` (or whatever interactive selector exists) to auto-select all features without prompting.
  - [x] Run duplo's `_subsequent_run` (or the top-level entry function) against the tmpdir.
  - [x] Assert: PLAN.md exists in tmpdir; `.duplo/raw_pages/` contains at least one `.html` file whose name is `sha256(canonical_url).hex` form; `.duplo/duplo.json` has the `sources` field populated with the URL; `.duplo/product.json` exists with `source_url` populated from the first product-reference; no `FileNotFoundError`, no diagnostic about missing `ref/` was recorded.

- [x] Add `test_ref_only_spec_runs_without_http`
  - [x] Construct a tmpdir with a SPEC.md containing marker, Purpose, Architecture, NO `## Sources` (or empty `## Sources`), and a `## References` block listing two entries: one with `role: visual-target` and one with `role: docs`. Create `ref/` directory and place small fixture image and text files at the declared paths.
  - [x] Patch `duplo.fetcher.fetch_site` with a mock that raises if called — the test asserts no HTTP work happened.
  - [x] Mock `extract_design` to return deterministic output and assert it was called with the visual-target ref/ file paths in its `design_input`.
  - [x] Mock the docs-text path (`docs_text_extractor`) to return deterministic output and assert it was called with the docs ref/ file path.
  - [x] Mock `extract_features` and the interactive selectors as in the previous test.
  - [x] Run `_subsequent_run` against the tmpdir.
  - [x] Assert: PLAN.md produced; `fetch_site` mock recorded zero calls; `extract_design` was called with expected paths; no diagnostic about missing source URL.

- [x] Add `test_url_and_ref_with_scope_exclude_drops_features`
  - [x] Construct a tmpdir with SPEC.md containing marker, Purpose, Architecture, one product-reference URL, one ref/ entry with `role: visual-target`, AND a `## Scope` block with `exclude: plugin API`.
  - [x] Mock `fetch_site` and `extract_design` deterministically.
  - [x] Mock `extract_features` to return three features whose names/descriptions are: (a) a clear match for `"plugin API"` as a whole phrase; (b) a non-match that contains the substring `"non-plugin-API"`; (c) an unrelated feature.
  - [x] Run `_subsequent_run` against the tmpdir.
  - [x] Assert: feature (a) was dropped — it does NOT appear in `.duplo/duplo.json` features list. Features (b) and (c) WERE kept (substring match must NOT trigger word-boundary regex).
  - [x] Assert: `duplo.diagnostics` recorded a `scope_exclude` diagnostic for feature (a) and only feature (a).

- [x] Add `test_discovered_urls_appended_to_spec_and_not_rescraped_on_second_run`
  - [x] Construct a tmpdir with SPEC.md containing one product-reference URL with `scrape: deep`. Mock `fetch_site` to return a `raw_pages` dict whose HTML contains one cross-origin `<a href>` to a different host.
  - [x] Run `_subsequent_run` once. Assert: SPEC.md was modified; `## Sources` now has a new entry for the cross-origin URL with `discovered: true` flag; the cross-origin URL was NOT fetched.
  - [x] Run `_subsequent_run` a second time on the same tmpdir without modifying SPEC.md. Assert: the discovered entry is still present with the flag intact; the cross-origin URL was STILL not fetched.
  - [x] Programmatically edit SPEC.md to remove the `discovered: true` line from the discovered entry. Run `_subsequent_run` a third time. Assert: this time the previously-discovered URL WAS fetched.

- [ ] Add `test_autogen_design_block_present_skips_vision`
  - [x] Construct two tmpdirs sharing the same SPEC.md skeleton (URL-only, product-reference). Variant A: SPEC.md has `## Design` containing a populated AUTO-GENERATED block. Variant B: SPEC.md has `## Design` with no autogen block.
  - [ ] Mock `extract_design` and assert call counts.
  - [ ] Run `_subsequent_run` against Variant A. Assert: `extract_design` was NOT called; `duplo.diagnostics` recorded the autogen-skip message; SPEC.md was NOT modified by the run; `.duplo/duplo.json` has NO new `design_requirements`.
  - [ ] Run `_subsequent_run` against Variant B. Assert: `extract_design` WAS called; SPEC.md was modified to add a populated AUTO-GENERATED block; `.duplo/duplo.json` `design_requirements` was populated.
  - [ ] Modify Variant A's SPEC.md to delete the autogen block contents (leave block markers but empty body). Run `_subsequent_run` again. Assert: `extract_design` IS called this time; SPEC.md autogen block is now populated.

- [ ] Add `test_proposed_true_references_excluded_from_pipeline`
  - [ ] Construct a tmpdir with SPEC.md containing two ref/ entries with the same `role: visual-target`: one with `proposed: true`, one without. Drop fixture image files for both.
  - [ ] Mock `extract_design` and capture its `design_input` argument.
  - [ ] Run `_subsequent_run` against the tmpdir.
  - [ ] Assert: `extract_design` was called; its `design_input` contains the path of the non-proposed reference; its `design_input` does NOT contain the path of the proposed reference.
  - [ ] Programmatically edit SPEC.md to remove `proposed: true` from the previously-proposed entry. Run `_subsequent_run` again. Assert: this time `extract_design` was called with both reference paths in `design_input`.
  - [ ] Repeat the same pattern for a `behavioral-target` reference: assert that with `proposed: true`, `extract_all_videos` is NOT called for that path; without the flag, it IS called.

- [ ] Add `test_counter_example_reference_excluded_from_extraction_and_appears_in_investigator`
  - [ ] Construct a tmpdir with SPEC.md containing one ref/ entry with `role: counter-example` and one ref/ entry with `role: visual-target`.
  - [ ] Mock `extract_design` and `extract_features` and capture their inputs.
  - [ ] Run `_subsequent_run`. Assert: `extract_design`'s `design_input` contains the visual-target path but NOT the counter-example path. The features list does NOT mention counter-example content.
  - [ ] Programmatically invoke `duplo fix "sample bug"`. Mock the investigator LLM call and capture the prompt.
  - [ ] Assert: the captured investigator prompt contains the counter-example reference's path or notes content, framed under an explicit "AVOID" label.
  - [ ] If the spec has counter-example SOURCES (URLs with `role: counter-example`), assert: the URL appears in the investigator prompt under the same AVOID framing, AND `fetch_site` was NOT called against that URL.

- [ ] Run the full test suite and confirm Phase 5 closes cleanly
  - [ ] Execute `pytest -x` against the duplo repo.
  - [ ] Assert: all pre-existing tests still pass; all seven new Phase 5 integration tests pass.
  - [ ] If any test fails, the task fails and mcloop will retry. The retry should investigate the failure (read pytest output, identify the failing assertion, locate the responsible code, fix it). Phase 5 is not complete until this task passes.

## Followup: bugs surfaced during the manual run of the URL-only scenario

The manual run of the URL-only scenario (against numi.app) before this rewrite surfaced real bugs. Queued here so they don't get lost.

- [ ] Fix planner output wrapped in markdown code fences
  - [ ] When duplo's planner generates a PLAN.md via `claude -p` and the LLM returns the markdown wrapped in triple-backtick fences, duplo writes the wrapped text verbatim. The resulting file is unparseable by mcloop because the H1 heading is buried inside a code fence.
  - [ ] Fix: in `duplo/planner.py` (or wherever the planner output is written), strip leading/trailing fences before writing. Use the existing `strip_fences` helper from `duplo/parsing.py` if it covers this case; if not, extend it.
  - [ ] Tests: planner output containing a fenced markdown block is written without the fences. Output without fences is written unchanged. Output with `~~~` fences is also handled.

- [ ] Fix planner placing feature tasks under `## Bugs` heading
  - [ ] The Phase 1 PLAN.md generated during the manual run had its feature implementation tasks placed UNDER the `## Bugs` heading instead of as the plan body. `## Bugs` should be empty initially. Feature tasks should be at the top level under the phase H1 heading.
  - [ ] Investigate `duplo/planner.py` (or saver.py's `save_plan`) to find where the structure is being assembled wrong.
  - [ ] Fix: ensure the planner's output has the correct structure: H1 phase heading, then feature tasks at top level, then `## Bugs` heading at the end with no tasks below it.
  - [ ] Tests: a generated PLAN.md has feature tasks at top level under the H1 heading. The `## Bugs` heading is present but contains no tasks. Mcloop's parser correctly identifies the feature tasks as Phase 1 work, not as bugs.

- [ ] Fix `derive_app_name` not writing `product_name` to product.json
  - [ ] During the manual run, the PLAN.md heading correctly read `# numi — Phase 1: Scaffold` (so the app name was derived as "numi" somewhere), but `.duplo/product.json` had `product_name: ""` (empty string).
  - [ ] Investigate `duplo/orchestrator.py:derive_app_name` and its callers. Either the function isn't writing `product_name` (only writing `app_name`), or `product.json` is initialized with empty `product_name` and never updated.
  - [ ] Fix: ensure `product.json` ends up with `product_name` populated to the same value used in the PLAN.md heading. Backward-compat: if `product.json` already has a non-empty `product_name` (user-edited), do not overwrite.
  - [ ] Tests: after `_subsequent_run`, `product.json:product_name` matches the value used in PLAN.md's H1 heading. User-edited `product_name` survives a subsequent run.

- [ ] Fix `frame_describer` parse-error on every video frame
  - [ ] During the manual run, all 17 video frames extracted from a demo video were stored with `state: "unknown"`, `detail: "parse error"` in `.duplo/duplo.json` `frame_descriptions`. The frame describer's LLM call is returning output that the parser cannot handle. Pre-existing behavior, not caused by Phase 5, but surfaced clearly during Phase 5 verification.
  - [ ] Investigate `duplo/frame_describer.py`. Capture a real LLM response sample and inspect what the parser is choking on.
  - [ ] Fix: make the parser tolerant of common LLM output variations: strip code fences, strip leading/trailing prose, parse the first valid JSON object found. Alternatively, tighten the prompt to demand strict JSON output.
  - [ ] Tests: parser handles JSON wrapped in fences; parser handles JSON preceded by prose; parser handles JSON with trailing whitespace; parser returns a useful error message when the LLM truly returned something unparseable.

- [ ] Investigate why AUTO-GENERATED design block was not written to SPEC.md during the manual URL-only run
  - [ ] During the manual run, design extraction appears to have completed (no error logged), but SPEC.md ended the run with no `## Design` section and no AUTO-GENERATED block.
  - [ ] Investigate: in `_subsequent_run`, after `extract_design` is called, what happens with the result? Is `format_design_block(design)` producing a non-empty string? Is `update_design_autogen` being invoked? Is its result being written back? Add a diagnostic at each step if the chain breaks.
  - [ ] Fix: ensure the write-back happens reliably whenever `extract_design` returns a non-empty result. The `test_autogen_design_block_present_skips_vision` test will catch this regression once the bug is fixed.
  - [ ] Note: this bug may be entangled with the frame_describer bug — if the design extractor receives only "unknown" frame descriptions, it may produce empty or trivial output that legitimately doesn't merit a write-back. Investigate the relationship.

---

# Phase 6: Drafter and `duplo init` (planned)

Adds `duplo init` and the spec-drafter that creates SPEC.md entries from URL scrapes, prose descriptions, and existing reference files (via Vision).

Design references: `DRAFTER-design.md`, `INIT-design.md`.

PLAN.md tasks for this phase have not yet been written.

---

# Phase 7: Cleanup (planned)

Removes the legacy code paths that the new SPEC-driven flow has superseded. Updates documentation. No new functionality.

PLAN.md tasks for this phase have not yet been written.
