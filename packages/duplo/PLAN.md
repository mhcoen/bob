<!-- bob-plan-format: 1 -->

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

## Phase 1: Bootstrapping (complete)
<!-- phase_id: phase_001 -->

- [x] T-000004: Project scaffolding <!-- created_at: 2026-03-06T03:13:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000001: Create duplo package with __init__.py and main.py entry point <!-- created_at: 2026-03-06T03:09:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000002: Add CLI argument parser: duplo <url>, duplo run, duplo next <!-- created_at: 2026-03-06T03:10:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000003: Verify pip install -e . works and duplo command runs <!-- created_at: 2026-03-06T03:13:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000009: Product scraping <!-- created_at: 2026-03-06T03:41:44Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000005: Fetch the product URL and extract text content <!-- created_at: 2026-03-06T03:23:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000006: Follow links, prioritizing documentation, features, guides, changelogs, and API references over marketing, blog, pricing, legal, and login pages <!-- created_at: 2026-03-06T03:34:11Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000007: Save reference screenshots from the product website <!-- created_at: 2026-03-06T03:38:33Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000008: Extract a structured feature list from the scraped content <!-- created_at: 2026-03-06T03:41:43Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000013: Interactive feature selection <!-- created_at: 2026-03-06T03:51:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000010: Present features to the user and ask which to include <!-- created_at: 2026-03-06T03:46:42Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000011: Ask about platform, language, constraints, and preferences <!-- created_at: 2026-03-06T03:49:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000012: Save selections to duplo.json in the target project <!-- created_at: 2026-03-06T03:51:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000018: Plan generation <!-- created_at: 2026-03-06T04:13:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000014: Generate Phase 1 PLAN.md (smallest end-to-end working thing) <!-- created_at: 2026-03-06T03:54:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000015: Create target project directory with git init <!-- created_at: 2026-03-06T04:07:40Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000016: Write PLAN.md, README.md, and mcloop.json <!-- created_at: 2026-03-06T04:10:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000017: Include CLAUDE.md with appshot instructions <!-- created_at: 2026-03-06T04:13:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000024: Phase execution <!-- created_at: 2026-03-06T04:28:33Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000019: Run McLoop on the target project <!-- created_at: 2026-03-06T04:16:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000020: Wait for completion, capture screenshots with appshot <!-- created_at: 2026-03-06T04:21:16Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000021: Compare screenshots against reference images via Claude API <!-- created_at: 2026-03-06T04:24:16Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000022: Generate visual issue list <!-- created_at: 2026-03-06T04:26:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000023: Notify user that phase is complete and ready for testing <!-- created_at: 2026-03-06T04:28:33Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000029: Feedback and iteration <!-- created_at: 2026-03-06T04:45:45Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000025: Collect user feedback (text input or from a file) <!-- created_at: 2026-03-06T04:31:42Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000026: Generate next phase PLAN.md incorporating feedback and visual issues <!-- created_at: 2026-03-06T04:40:34Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000027: Append completed phases to duplo.json history <!-- created_at: 2026-03-06T04:43:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000028: Run McLoop for the next phase <!-- created_at: 2026-03-06T04:45:45Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000033: State management <!-- created_at: 2026-03-06T05:04:54Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000030: Store all state in duplo.json: source URL, features, phases, feedback <!-- created_at: 2026-03-06T04:46:01Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000031: Support resuming after interruption (duplo run picks up where it left off) <!-- created_at: 2026-03-06T04:52:05Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000032: Track which reference screenshots map to which features <!-- created_at: 2026-03-06T05:04:54Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000040: Deep documentation extraction <!-- created_at: 2026-03-06T16:16:03Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000034: When scraping a product site, identify links to documentation pages by reading the page content and link text, not by matching a hardcoded list of platforms <!-- created_at: 2026-03-06T15:55:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000035: Follow documentation links even if they leave the main domain (docs are often hosted separately) <!-- created_at: 2026-03-06T15:57:50Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000036: Increase the page limit for documentation sites since doc pages are individually small but collectively important <!-- created_at: 2026-03-06T15:59:47Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000037: Extract code examples from documentation pages as input/expected_output pairs <!-- created_at: 2026-03-06T16:05:28Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000038: Extract feature tables, operation lists, unit lists, and function references <!-- created_at: 2026-03-06T16:12:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000039: Store all extracted examples in duplo.json so they persist across runs <!-- created_at: 2026-03-06T16:16:03Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000045: Test case generation from documentation <!-- created_at: 2026-03-06T16:30:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000041: Every input/output example extracted from documentation becomes a unit test case <!-- created_at: 2026-03-06T16:19:42Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000042: Tests should call the app's core logic directly without requiring GUI interaction <!-- created_at: 2026-03-06T16:24:13Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000043: Include test generation tasks in the PLAN.md that Duplo generates for the target project <!-- created_at: 2026-03-06T16:26:24Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000044: Group tests by category so failures are easy to diagnose <!-- created_at: 2026-03-06T16:30:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000051: Persistent state in .duplo/ directory <!-- created_at: 2026-03-06T16:55:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000046: Create a .duplo/ directory in the target project for Duplo's working state between runs <!-- created_at: 2026-03-06T16:38:05Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000047: Save all reference URLs consulted during scraping, with timestamps and content hashes <!-- created_at: 2026-03-06T16:42:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000048: Save raw scraped content so re-runs can diff against what changed on the product site <!-- created_at: 2026-03-06T16:49:33Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000049: Save extracted examples separately from duplo.json so they can be reviewed and edited <!-- created_at: 2026-03-06T16:53:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000050: Add .duplo/ to the target project's .gitignore <!-- created_at: 2026-03-06T16:55:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000060: Directory-based workflow redesign <!-- created_at: 2026-03-06T19:57:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000052: Duplo runs from the current directory with no required arguments. The user creates the project directory, puts whatever reference material they want inside (images, PDFs, text files, URLs in a file), and runs duplo. <!-- created_at: 2026-03-06T17:14:51Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000053: On first run, scan the directory for reference materials: images (png, jpg, gif, webp), PDFs, text/markdown files, and any file containing URLs. Analyze each to determine relevance. <!-- created_at: 2026-03-06T17:18:24Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000054: If a URL is found, validate it points to a single clear product, not a company portfolio or homepage with multiple products. Ask the user to clarify if ambiguous. <!-- created_at: 2026-03-06T17:21:33Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000055: Clearly state what product Duplo thinks it is duplicating and get confirmation before proceeding. No ambiguity. <!-- created_at: 2026-03-06T19:43:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000056: Send images to Claude Vision to extract visual design details: colors, fonts, spacing, layout, component styles. These become design requirements in PLAN.md. <!-- created_at: 2026-03-06T19:48:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000057: Extract text content from PDFs and include in feature analysis. <!-- created_at: 2026-03-06T19:51:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000058: Move processed reference materials to .duplo/references/ to keep the project directory clean. <!-- created_at: 2026-03-06T19:54:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000059: Keep a hash manifest of all files in the project directory in .duplo/file_hashes.json <!-- created_at: 2026-03-06T19:57:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000068: Incremental update mode <!-- created_at: 2026-03-06T20:22:10Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000061: On subsequent runs, detect new or changed files in the project directory by comparing against .duplo/file_hashes.json <!-- created_at: 2026-03-06T19:58:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000062: Analyze any new files the same way as first run (images to Vision, PDFs to text, URLs to scraper) <!-- created_at: 2026-03-06T20:04:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000063: Re-scrape the product URL with the improved deep extractor if the URL was already known <!-- created_at: 2026-03-06T20:06:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000064: Compare newly extracted features and examples against existing PLAN.md <!-- created_at: 2026-03-06T20:10:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000065: Append new unchecked tasks for missing features, uncovered examples, and design refinements <!-- created_at: 2026-03-06T20:14:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000066: Never modify or remove existing tasks (checked or unchecked) <!-- created_at: 2026-03-06T20:16:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000067: Print a summary of what was found and what was added <!-- created_at: 2026-03-06T20:22:10Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000077: Video reference extraction <!-- created_at: 2026-03-06T20:45:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000069: Detect video files in the project directory (mp4, mov, webm, avi) <!-- created_at: 2026-03-06T20:24:40Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000070: Use ffmpeg scene change detection to extract frames at visual transition points <!-- created_at: 2026-03-06T20:28:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000071: Deduplicate similar frames using perceptual image hashing <!-- created_at: 2026-03-06T20:32:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000072: Send candidate frames to Claude Vision to filter: keep only clear, stable screenshots of the application showing a distinct UI state. Discard transitions, blur, marketing overlays, loading screens. <!-- created_at: 2026-03-06T20:35:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000073: For each accepted frame, ask Claude Vision to describe what UI state it shows (main view, settings panel, dialog, menu, etc.) <!-- created_at: 2026-03-06T20:38:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000074: Store accepted frames in .duplo/references/ with their UI state descriptions <!-- created_at: 2026-03-06T20:41:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000075: Include extracted frames in the same analysis pipeline as user-provided screenshots <!-- created_at: 2026-03-06T20:43:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000076: Requires ffmpeg on PATH (document in README) <!-- created_at: 2026-03-06T20:45:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000081: Product disambiguation <!-- created_at: 2026-03-06T20:59:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000078: When a URL points to a company with multiple products, present the products found and ask which one to duplicate <!-- created_at: 2026-03-06T20:46:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000079: When a URL is a landing page with unclear product boundaries, ask the user to describe what specific product they want <!-- created_at: 2026-03-06T20:49:28Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000080: Store the confirmed product identity in .duplo/product.json so subsequent runs don't re-ask <!-- created_at: 2026-03-06T20:59:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000085: Non-destructive plan updates <!-- created_at: 2026-03-06T21:24:51Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000082: save_plan() must never overwrite an existing PLAN.md. If PLAN.md already exists, append new tasks to the end of the file instead of replacing it. Existing checked and unchecked items must be preserved exactly as they are. <!-- created_at: 2026-03-06T21:18:34Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000083: All other files duplo writes (CLAUDE.md, mcloop.json, README.md) must also be non-destructive on subsequent runs. Merge or append, never replace. <!-- created_at: 2026-03-06T21:20:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000084: Update README.md to document that duplo's update cycle is non-destructive: existing code, plans, and configuration are never removed or overwritten. <!-- created_at: 2026-03-06T21:24:51Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000086: Route all AI calls through claude -p instead of direct Anthropic API calls. Every module that creates an anthropic.Anthropic() client (extractor.py, design_extractor.py, validator.py, roadmap.py, planner.py, comparator.py, frame_filter.py, frame_describer.py, gap_detector.py) must be changed to use claude -p so the Max subscription is used instead of API credits. No direct API calls. <!-- created_at: 2026-03-07T01:16:29Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000087: Re-extract features on subsequent runs: _subsequent_run currently re-scrapes the product URL and updates page records, but never re-runs feature extraction on the new content. The gap detector compares the same features stored in duplo.json against the plan, so it always finds no gaps. On subsequent runs, after re-scraping, re-extract features from the updated scraped content using extract_features(), merge new features into duplo.json (without removing existing ones), then pass the combined feature list to the gap detector. <!-- created_at: 2026-03-07T04:14:33Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

---

## Phase 2: Phase Completion and Next-Phase Generation (complete)
<!-- phase_id: phase_002 -->

Duplo currently handles first-run (scrape, extract features, select, generate plan) and incremental updates (detect new files, re-scrape, append gap tasks). What is missing is the phase-completion loop: when all tasks in PLAN.md are done, duplo should track what was implemented, present the remaining work, and generate a scoped next-phase plan.

This phase added feature annotations in generated plans, deterministic status tracking in duplo.json, a next-phase flow with interactive feature selection and issue injection, and fixes to the state machine bugs that prevented any of this from working on existing projects.

- [x] T-000090: Fix phase-title regex to handle app-name prefixed headings <!-- created_at: 2026-03-10T04:06:07Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000088: `append_phase_to_history` uses `r"^#\s*(Phase\s+\d+[^\n]*)"` which fails on headings like `# McWhisper — Phase 1: Core`. The same regex pattern appears in `_complete_phase`, `_advance_to_next`, `_detect_next_phase_number`, and `_subsequent_run`. All instances must be relaxed to find a phase number anywhere in the first `#` heading line, e.g. `r"^#\s*.*?(Phase\s+\d+[^\n]*)"` or extract the phase number with `r"Phase\s+(\d+)"`. <!-- created_at: 2026-03-10T02:50:03Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000089: Add tests covering headings in both formats: `# Phase 1: Core` and `# McWhisper — Phase 1: Core` <!-- created_at: 2026-03-10T04:06:07Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000094: Add feature annotations to generated plans <!-- created_at: 2026-03-10T04:09:10Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000091: Modify the planner system prompt in `generate_phase_plan` so that every generated task line includes a `[feat: "Feature Name"]` annotation listing which features from the input list it addresses. Tasks addressing multiple features list them comma-separated: `[feat: "Push-to-talk recording", "Global keyboard shortcuts"]`. Tasks for bug fixes or issues use `[fix: "description"]`. Scaffolding or structural tasks that do not map to any feature use no annotation. <!-- created_at: 2026-03-10T04:06:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000092: Modify `generate_next_phase_plan` with the same annotation requirement <!-- created_at: 2026-03-10T04:07:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000093: Add a test that verifies generated plans contain `[feat: ...]` or `[fix: ...]` annotations on task lines <!-- created_at: 2026-03-10T04:09:10Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000100: Add `status`, `implemented_in`, and `issues` fields to feature tracking <!-- created_at: 2026-03-10T04:17:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000095: Each feature dict in `duplo.json` gets two optional fields: `status` (one of `pending`, `implemented`, `partial`) and `implemented_in` (phase label string). New features default to `status: "pending"`. <!-- created_at: 2026-03-10T04:10:13Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000096: Add a `save_feature_status(name, status, implemented_in)` function to saver.py that updates a feature by name <!-- created_at: 2026-03-10T04:11:34Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000097: Add a top-level `issues` list to `duplo.json` for implementation problems not tied to a specific feature <!-- created_at: 2026-03-10T04:13:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000098: Add `save_issue(description, source, phase)` and `resolve_issue(description)` functions to saver.py <!-- created_at: 2026-03-10T04:14:45Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000099: Existing features in duplo.json files that lack a `status` field should be treated as `pending` by all code that reads them <!-- created_at: 2026-03-10T04:17:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000106: Implement deterministic phase-completion tracking <!-- created_at: 2026-03-10T04:27:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000101: At phase completion (all checkboxes checked), parse PLAN.md for checked task lines <!-- created_at: 2026-03-10T04:19:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000102: For each checked line with a `[feat: ...]` annotation, mark the referenced features as `implemented` with the current phase label <!-- created_at: 2026-03-10T04:21:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000103: For each checked line with a `[fix: ...]` annotation, mark the corresponding issue as resolved <!-- created_at: 2026-03-10T04:23:20Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000104: For checked lines without annotations (user-added tasks or pre-annotation plans), batch them into a single `claude -p` call with the full feature list. Claude matches each task to an existing feature or confirms it is genuinely new. Mark matched features as implemented. Add genuinely new items as new feature entries with `status: "implemented"` and `implemented_in` set to the current phase. <!-- created_at: 2026-03-10T04:25:44Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000105: Add tests for the annotation parser covering annotated lines, unannotated lines, and mixed plans <!-- created_at: 2026-03-10T04:27:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000111: Prompt for issues at phase completion <!-- created_at: 2026-03-10T04:35:09Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000107: After status tracking, before advancing to the next phase, prompt the user for known issues with the completed phase <!-- created_at: 2026-03-10T04:29:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000108: Multi-line input, blank line to finish, skippable <!-- created_at: 2026-03-10T04:31:51Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000109: Each line becomes an entry in the `issues` list in `duplo.json` with `source: "user"` and the current phase label <!-- created_at: 2026-03-10T04:33:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000110: This is where the user reports bugs (e.g. "waveform shows static bars during recording") or incomplete wiring (e.g. "qwen3-asr-swift dependency is unused") <!-- created_at: 2026-03-10T04:35:09Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000116: Generate roadmap from remaining features when missing or consumed <!-- created_at: 2026-03-10T04:44:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000112: At the start of the next-phase flow, if `duplo.json` has no `roadmap` or the existing roadmap has been fully consumed (current_phase is past the last entry), generate a new one using `generate_roadmap` with only the remaining unimplemented features <!-- created_at: 2026-03-10T04:38:56Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000113: Pass the completion history (list of phase labels and what they implemented) as context so the roadmap builds on what exists rather than starting from scratch <!-- created_at: 2026-03-10T04:41:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000114: Save the new roadmap to `duplo.json`, resetting `current_phase` to 0 relative to the new roadmap <!-- created_at: 2026-03-10T04:43:22Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000115: Add a test that verifies roadmap generation excludes implemented features <!-- created_at: 2026-03-10T04:44:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000120: Redesign `_subsequent_run` state machine <!-- created_at: 2026-03-10T04:53:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000117: Replace the current branching (in_progress checks, roadmap lookups, history-based detection) with a clean flow: <!-- created_at: 2026-03-10T04:51:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - If PLAN.md exists and has unchecked items: print status summary and "Run mcloop to continue building." Exit.
    - If PLAN.md exists and all items are checked: run phase-completion flow (annotation parsing, status tracking, issue prompt). Delete PLAN.md. Fall through to next-phase flow.
    - If no PLAN.md: run next-phase flow.
  - [x] T-000118: Remove the separate `_advance_to_next` code path. The single next-phase flow handles everything. <!-- created_at: 2026-03-10T04:53:36Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000119: Remove the dependency on `in_progress` for flow control. The `in_progress` key can be removed entirely or repurposed for crash recovery only. <!-- created_at: 2026-03-10T04:53:36Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000129: Implement next-phase flow with feature selection <!-- created_at: 2026-03-10T05:16:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000121: Re-scrape the product site, re-extract features, merge new ones into `duplo.json` (already works) <!-- created_at: 2026-03-10T04:55:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000122: Partition features into implemented and remaining based on `status` field <!-- created_at: 2026-03-10T04:57:50Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000123: If roadmap is missing or consumed, generate a new one from remaining features (previous item) <!-- created_at: 2026-03-10T05:01:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000124: Use the next phase entry from the roadmap as the default recommendation during feature selection <!-- created_at: 2026-03-10T05:07:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000125: Present remaining features to the user using `select_features` (numbered, grouped by category), with the roadmap recommendation labeled (e.g. "Recommended for Phase 2: 3, 7, 12, 15") <!-- created_at: 2026-03-10T05:08:30Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000126: Show open issues from `duplo.json` and ask which should be addressed in this phase (same numbered selection pattern) <!-- created_at: 2026-03-10T05:11:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000127: Update `generate_phase_plan` to accept issues alongside features. The system prompt should instruct Claude to include fix tasks for issues alongside feature-implementation tasks, ordering fixes before new feature work when there are dependencies. <!-- created_at: 2026-03-10T05:12:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000128: Generate the next PLAN.md scoped to selected features + selected issues. Heading format: `# <AppName> — Phase N: <Title>`. All task lines include `[feat: ...]` or `[fix: ...]` annotations. Phase number derived from `phases` history length + 1. <!-- created_at: 2026-03-10T05:16:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000133: Print status summary on every run <!-- created_at: 2026-03-10T05:21:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000130: Before doing any work, print: current phase number, features implemented vs. remaining, open issues count <!-- created_at: 2026-03-10T05:18:28Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000131: Example output: `McWhisper: Phase 1 complete. 14/52 features implemented, 3 open issues.` <!-- created_at: 2026-03-10T05:19:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000132: If no phases have been completed yet, print feature count and "Phase 1 in progress" or "Ready to generate Phase 1" <!-- created_at: 2026-03-10T05:21:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000137: Automatic BATCH tag support in generated plans <!-- created_at: 2026-03-13T22:51:01Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000134: Update `_PHASE_SYSTEM` prompt in `planner.py` to instruct Claude to mark parent tasks with `[BATCH]` when all subtasks are specific enough to execute without design decisions (file paths, function names, explicit conditionals, concrete values). Include an example showing the `[BATCH]` syntax with concrete subtasks. Do NOT use `[BATCH]` on tasks whose subtasks require significant design decisions or architectural exploration. <!-- created_at: 2026-03-13T22:51:01Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000135: Update `_NEXT_PHASE_SYSTEM` prompt with the same `[BATCH]` instruction for next-phase plan generation. <!-- created_at: 2026-03-13T22:51:01Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000136: Update the example plan in `_PHASE_SYSTEM` to show a `[BATCH]` parent with concrete subtasks instead of the generic "Subtask if needed" placeholder. <!-- created_at: 2026-03-13T22:51:01Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

### Manual verification (all complete)

- [x] T-000138: Run duplo in the mcwhisper directory. Confirm it detects Phase 1 as complete, runs the unannotated-task matching via Claude, marks implemented features, prompts for issues, generates a roadmap from remaining features, presents feature selection with a recommendation, and generates a Phase 2 PLAN.md with proper annotations. <!-- created_at: 2026-03-10T05:46:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000139: Run duplo again immediately (Phase 2 not started). Confirm it prints the status summary and tells you to run mcloop. <!-- created_at: 2026-03-10T06:02:09Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000140: After mcloop completes Phase 2, run duplo again. Confirm annotated tasks are tracked deterministically (no Claude call needed), issues prompt appears, roadmap is regenerated if consumed, and Phase 3 is ready. <!-- created_at: 2026-03-10T06:17:22Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

---

## Phase 3: SPEC.md parser and prompt-injection-safe formatters (complete)
<!-- phase_id: phase_003 -->

The SPEC.md / `ref/` redesign restructures duplo's input contract so user intent lives in a typed, reviewable spec rather than in interactive prompts and ambient directory scanning. This phase implemented the data layer only: parser, dataclasses, validation, role-filtered formatters, and the rewrite of `format_spec_for_prompt` that closes the prompt-injection leak. No pipeline behavior changes; existing callers continued to work via a compatibility layer.

Design reference: `design/PARSER-design.md` (authoritative), with `SPEC-template.md` and `SPEC-guide.md` defining the on-disk schema and `REDESIGN-overview.md` providing context.

Critical safety invariant introduced in this phase: **no LLM call ever sees raw SPEC.md text.** `format_spec_for_prompt` was rewritten to serialize from parsed dataclasses with role/flag filtering. Without this, `proposed:`, `discovered:`, and `counter-example` entries would leak into every LLM prompt despite the role-filter helpers. The invariant has its own dedicated test that pins the property.

- [x] T-000146: [BATCH] Add new dataclasses and the comment-stripping helper to `spec_reader.py` <!-- created_at: 2026-04-13T06:43:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000141: Add `SourceEntry` dataclass with fields `url`, `role`, `scrape`, `notes`, `proposed`, `discovered`. Per design/PARSER-design.md § SourceEntry. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000142: Add `ReferenceEntry` dataclass with fields `path`, `roles` (list[str]), `notes`, `proposed`. Per design/PARSER-design.md § ReferenceEntry. Note `roles` is plural to support multiple-roles-per-entry. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000143: Add `DesignBlock` dataclass with fields `user_prose`, `auto_generated`, `has_fill_in_marker`. Per design/PARSER-design.md § DesignBlock. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000144: Add `_HTML_COMMENT_RE` and `_strip_comments(body)` helper. Per design/PARSER-design.md § `<FILL IN>` detection. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000145: Tests: dataclass field defaults, `_strip_comments` removes single-line and multi-line HTML comment blocks, comment-stripping leaves non-comment content intact. <!-- created_at: 2026-04-13T06:43:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000153: [BATCH] Add `<FILL IN>` detection for required sections <!-- created_at: 2026-04-13T06:50:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000147: Add the `_FILL_IN_RE` regex per design/PARSER-design.md (matches `<FILL IN>` permissively on whitespace and trailing hint text). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000148: Apply `_strip_comments` to a section body before regex matching, so commented-out template hints don't trigger detection. <!-- created_at: 2026-04-13T06:50:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000149: Wire detection into `_parse_spec` to set `spec.fill_in_purpose` after parsing `## Purpose`. <!-- created_at: 2026-04-13T06:50:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000150: Wire detection into `_parse_spec` to set `spec.fill_in_architecture` after parsing `## Architecture`. <!-- created_at: 2026-04-13T06:50:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000151: Wire detection into `_parse_spec` to set `spec.fill_in_design` per the rule: true ONLY when `design.has_fill_in_marker` AND no reference entries have `visual-target` in `roles`. <!-- created_at: 2026-04-13T06:50:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000152: Tests: marker present in body sets flag; marker present only in an HTML comment does NOT set flag; absent marker keeps flag false; `fill_in_design` rule covers both required conditions. <!-- created_at: 2026-04-13T06:50:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000160: Add `## Sources` parser <!-- created_at: 2026-04-13T07:17:13Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000154: Add `_SOURCE_ENTRY_START` and `_FIELD_LINE` regexes per design/PARSER-design.md § `## Sources` parser. Entry start matches a list-item line containing an http(s) URL; field lines match indented `key: value` pairs. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000155: Implement entry-block parser: scan section line-by-line, accumulate field lines until next entry or section end, support multi-line `notes:` continuations indented further than the field name. <!-- created_at: 2026-04-13T06:57:05Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000156: Validation per `SourceEntry`: drop entries with invalid URL; DROP entries with unknown role (do NOT default — typo `role: doc` must not silently widen authority); default unknown `scrape` to `none` (not `deep`); accept both `proposed` and `discovered` set without diagnostic. <!-- created_at: 2026-04-13T07:01:35Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000157: Diagnostic emission via existing `duplo.diagnostics.record_failure`. <!-- created_at: 2026-04-13T07:08:51Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000158: Add `sources` to `_KNOWN_SECTIONS`. <!-- created_at: 2026-04-13T07:14:00Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000159: Tests: single entry, multiple entries, all field combinations, invalid URLs dropped, invalid roles dropped (entry removed entirely), invalid scrape defaulting to `none`, comment-stripped examples not parsed as real entries, multi-line `notes:` parsed correctly. <!-- created_at: 2026-04-13T07:17:13Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000164: Add `## Notes` parser <!-- created_at: 2026-04-13T07:25:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000161: Trivial: store comment-stripped body as `spec.notes`. No structured parsing. <!-- created_at: 2026-04-13T07:21:47Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000162: Add `notes` to `_KNOWN_SECTIONS`. <!-- created_at: 2026-04-13T07:23:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000163: Tests: present section captured verbatim; absent section yields empty string; comment blocks stripped before storage. <!-- created_at: 2026-04-13T07:25:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000172: Convert `## References` parser from prose to structured entries <!-- created_at: 2026-04-13T08:09:32Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000165: Add bare and quoted entry-start regexes per design/PARSER-design.md § `## References` parser. Bare form matches list-item lines starting with `ref/` followed by a path with non-greedy whitespace handling (paths with spaces are common; macOS screenshots default to names like `Screen Shot 2025-10-12 at 14.30.png`). Quoted form matches `- "ref/..."` and strips the quotes after match (for paths with unusual characters). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000166: Implement entry parser sharing `_FIELD_LINE` with the Sources parser. <!-- created_at: 2026-04-13T07:43:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000167: Parse `role:` as comma-separated list into `roles: list[str]`. Support multiple roles per entry (the dual-use case for behavioral-and-visual videos). <!-- created_at: 2026-04-13T07:51:36Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000168: Validation per `ReferenceEntry`: drop entries with paths not under `ref/` (after quote-stripping); drop unknown roles from the comma-separated list with diagnostic; if all roles unknown, default to `["ignore"]`. <!-- created_at: 2026-04-13T07:57:33Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000169: Reject `discovered:` flag with diagnostic (only Sources can be discovered). <!-- created_at: 2026-04-13T08:01:10Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000170: Tests: single entry, multiple entries, paths with spaces (bare form), paths with unusual characters (quoted form), paths outside `ref/` dropped, multiple roles parsed correctly, unknown roles dropped while valid ones kept, all-unknown-roles defaults to `ignore`, `discovered:` rejected. <!-- created_at: 2026-04-13T08:04:24Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000171: Migration test: old prose-form `## References` parses to empty `references` list, prose preserved in `spec.raw`, diagnostic emitted suggesting migration. <!-- created_at: 2026-04-13T08:09:32Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000178: Add AUTO-GENERATED block parsing in `## Design` <!-- created_at: 2026-04-13T08:29:50Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000173: Add the `_AUTOGEN_RE` regex per design/PARSER-design.md § `## Design` parser (matches the BEGIN/END comment markers with DOTALL). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000174: If block present: split body into `user_prose` (text before block) and `auto_generated` (block contents, markers stripped). <!-- created_at: 2026-04-13T08:19:50Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000175: If block absent: entire comment-stripped body becomes `user_prose`; `auto_generated` is empty. <!-- created_at: 2026-04-13T08:23:22Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000176: Set `has_fill_in_marker` by checking `user_prose` (after comment stripping) against `_FILL_IN_RE`. <!-- created_at: 2026-04-13T08:25:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000177: Tests: block present (correct split); block absent (all to user_prose); malformed BEGIN-only or END-only markers treated as no block; nested or repeated markers handled deterministically. <!-- created_at: 2026-04-13T08:29:50Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000184: Update `ProductSpec` and audit existing callers <!-- created_at: 2026-04-13T08:54:32Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000179: Change `design` field from `str` to `DesignBlock`. <!-- created_at: 2026-04-13T08:39:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000180: Change `references` field from `str` to `list[ReferenceEntry]`. <!-- created_at: 2026-04-13T08:42:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000181: Add new fields: `sources: list[SourceEntry]`, `notes: str`, `fill_in_purpose: bool`, `fill_in_architecture: bool`, `fill_in_design: bool`. <!-- created_at: 2026-04-13T08:47:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000182: Grep the codebase for callers of `spec.references` and `spec.design` accessing them as strings. Update each call site to use `spec.design.user_prose` (or the new `format_design_for_prompt` helper, item below) and to treat `spec.references` as a list. <!-- created_at: 2026-04-13T08:50:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000183: Tests: existing `test_spec_reader.py` continues to pass for fields that didn't change type (purpose, architecture, scope, behavior); new fields populate correctly on a fully-filled SPEC.md fixture. <!-- created_at: 2026-04-13T08:54:32Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000191: [BATCH] Add per-stage role-filtering formatters <!-- created_at: 2026-04-13T08:57:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000185: `format_visual_references(spec) -> list[ReferenceEntry]`: entries where `visual-target` is in `roles`, excluding `proposed: true`. <!-- created_at: 2026-04-13T08:57:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000186: `format_behavioral_references(spec) -> list[ReferenceEntry]`: entries where `behavioral-target` is in `roles`, excluding `proposed: true`. Dual-role entries appear in both this and `format_visual_references` so the caller can detect dual-use via membership check on `entry.roles`. <!-- created_at: 2026-04-13T08:57:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000187: `format_doc_references(spec) -> list[ReferenceEntry]`: entries where `docs` is in `roles`, excluding `proposed: true`. <!-- created_at: 2026-04-13T08:57:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000188: `format_counter_examples(spec) -> list[ReferenceEntry]`: entries where `counter-example` is in `roles`, excluding `proposed: true`. <!-- created_at: 2026-04-13T08:57:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000189: All four return `list[ReferenceEntry]` (not `list[Path]`) so callers can inspect roles, notes, and flags. Path extraction is `[e.path for e in ...]` at the call site. <!-- created_at: 2026-04-13T08:57:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000190: Tests: each formatter returns the right filtered list; each excludes `proposed: true`; entries with multiple roles appear in every matching formatter; each handles empty input gracefully. <!-- created_at: 2026-04-13T08:57:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000195: Add `format_scrapeable_sources(spec) -> list[SourceEntry]` <!-- created_at: 2026-04-13T09:11:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000192: Returns source entries where `scrape` is `deep` or `shallow`, AND `discovered: false`, AND `proposed: false`, AND `role` is NOT `counter-example`. <!-- created_at: 2026-04-13T09:02:24Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000193: Counter-example entries with `scrape: deep` or `scrape: shallow` get a diagnostic (the user almost certainly meant `scrape: none`) and are treated as `none` regardless of declared value. <!-- created_at: 2026-04-13T09:07:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000194: Tests: each filter condition exercised independently; counter-example with non-`none` scrape diagnostic emitted; counter-example with `scrape: none` silent. <!-- created_at: 2026-04-13T09:11:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000200: Add `format_design_for_prompt(spec) -> str` <!-- created_at: 2026-04-13T09:26:54Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000196: If both `user_prose` and `auto_generated` are present, format them in that order with a separator. <!-- created_at: 2026-04-13T09:15:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000197: If only one is present, return that one. <!-- created_at: 2026-04-13T09:18:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000198: If neither, return empty string. <!-- created_at: 2026-04-13T09:22:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000199: Tests: each combination produces expected output; user_prose comes first when both present. <!-- created_at: 2026-04-13T09:26:54Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000209: Rewrite `format_spec_for_prompt` to serialize from dataclasses (prompt-injection safety invariant) <!-- created_at: 2026-04-13T10:22:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000201: Replace the existing implementation that returns `spec.raw`. The new implementation serializes from parsed `ProductSpec` fields, NOT from raw text. <!-- created_at: 2026-04-13T09:33:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000202: Include user-authored sections verbatim: `## Purpose`, `## Architecture`, `## Design.user_prose`, `## Scope`, `## Behavior`, `## Notes`. <!-- created_at: 2026-04-13T09:46:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000203: For `## Sources`: include only entries where `proposed: false` AND `discovered: false` AND `role` is NOT `counter-example`. <!-- created_at: 2026-04-13T09:53:28Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000204: For `## References`: include only entries where `proposed: false` AND no role is `counter-example` AND no role is `ignore`. <!-- created_at: 2026-04-13T10:00:40Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000205: For `## Design`: include `auto_generated` content alongside `user_prose` (autogen is derived from non-proposed visual targets only and has already been filtered upstream). <!-- created_at: 2026-04-13T10:05:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000206: Wrap output in the existing labelled prefix ("PRODUCT SPECIFICATION (authored by the user...") so existing consumers see equivalent framing. <!-- created_at: 2026-04-13T10:09:40Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000207: Update existing tests for `format_spec_for_prompt` (output format will differ) so they pin the new behavior. <!-- created_at: 2026-04-13T10:18:40Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000208: **Prompt-injection invariant test (highest-stakes test in the phase)**: construct a spec containing `proposed: true` source, `discovered: true` source, `counter-example` source, `proposed: true` reference, and `counter-example` reference, all with distinctive recognizable content; assert that `format_spec_for_prompt(spec)` output does NOT contain any of those entries' content. This test pins the safety property for all downstream LLM call sites. <!-- created_at: 2026-04-13T10:22:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000217: Add `validate_for_run(spec) -> list[str]` and wire into `main.py` <!-- created_at: 2026-04-13T11:03:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000210: Returns list of human-readable error messages; empty list means OK to run. <!-- created_at: 2026-04-13T10:27:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000211: Errors: purpose-fill-in, architecture-fill-in, and the no-source-and-no-ref-and-sparse-purpose condition (no scrapeable sources AND no non-ignore references AND `## Purpose` shorter than 50 characters). <!-- created_at: 2026-04-13T10:31:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000212: `fill_in_design` produces a WARNING (not an error) per design/PARSER-design.md § Validation API. The "URL alone" common pattern is valid even when `## Design` has no user prose and no visual-target references — duplo can still proceed by inferring design from scraped product-reference pages. Warnings print but do not block execution. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000213: Warnings for unreviewed entries: count `proposed: true` references and `discovered: true` sources, emit one warning each summarizing counts and what to do. <!-- created_at: 2026-04-13T10:47:20Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000214: Wire `validate_for_run` into `main.py` so it runs after `read_spec` and before any pipeline work. If errors returned, print them to stderr and exit 1. Warnings print to stdout but do not block. <!-- created_at: 2026-04-13T10:53:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000215: Tests: each error condition produces the expected message; valid spec returns empty list; `fill_in_design` produces warning not error; warnings include correct counts. <!-- created_at: 2026-04-13T10:59:40Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000216: Backward compatibility: old-format SPEC.md files (no fill-in markers anywhere because they predate the convention) keep `fill_in_purpose` and `fill_in_architecture` false and pass validation. Test this explicitly. <!-- created_at: 2026-04-13T11:03:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

### Manual verification (all complete)

- [x] T-000218: Write a fully-populated SPEC.md in the new format (every section filled, including `## Sources`, structured `## References`, `## Notes`) in a scratch directory and confirm `read_spec()` parses every section into the expected dataclass fields. Drop into a Python REPL or write a small script. <!-- created_at: 2026-04-13T11:07:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000219: Write a SPEC.md with deliberate `proposed: true`, `discovered: true`, and `counter-example` entries. Call `format_spec_for_prompt(spec)` and visually confirm the output contains none of those entries' content. This is the safety invariant. <!-- created_at: 2026-04-13T11:16:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000220: Write a SPEC.md with a fill-in marker left in `## Purpose`. Run `duplo` and confirm it exits 1 with a clear error message and does NOT proceed to scraping or extraction. <!-- created_at: 2026-04-13T11:22:11Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000221: Run `duplo` against an existing pre-redesign project (one with no SPEC.md or an old-format SPEC.md). Confirm it still runs end-to-end without errors. The new validation should not block legacy projects until they migrate. <!-- created_at: 2026-04-13T11:30:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000222: Write a SPEC.md with a reference path containing spaces (e.g. a list-item entry naming `ref/Screen Shot 2025-10-12 at 14.30.png`) and confirm it parses without dropping the entry. Same for a quoted path with unusual characters. <!-- created_at: 2026-04-13T11:51:34Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

---

## Phase 4: Migration detection gate (complete)
<!-- phase_id: phase_004 -->

This phase added a single gate at the start of `duplo` (no-subcommand path) that detects pre-redesign projects and prints manual-migration instructions instead of running the pipeline against them. Intentionally small: a detection function, a wrapper that prints and exits, dispatch wiring in `main()`, and tests. The pipeline refactor itself is Phase 5 and is NOT part of this phase.

Design reference: `design/MIGRATION-design.md` (authoritative).

This phase shipped the Phase-2-message-text version ("author a SPEC.md by hand" — `duplo init` does not exist yet). Phase 6 will replace it with the `duplo init` version as a one-line change.

- [x] T-000237: Add `needs_migration(target_dir: Path) -> bool` to `duplo/migration.py` (new module) <!-- created_at: 2026-04-13T18:08:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000223: Create `duplo/migration.py`. Import `re` and `Path`. Export `needs_migration`. <!-- created_at: 2026-04-13T17:34:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000224: Signal 1 (marker-string match, fast path): SPEC.md contains the literal substring `"How the pieces fit together:"`. This string appears in the top-matter comment of SPEC-template.md and will be present in any SPEC.md created by `duplo init` (once it ships) or by a user copying the template. <!-- created_at: 2026-04-13T17:36:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000225: Signal 2 (schema-structural match, fallback): SPEC.md contains an `## Sources` heading (matched via `re.search(r"^## Sources\s*$", spec_text, re.MULTILINE)`). Either signal is sufficient to classify as new-format. <!-- created_at: 2026-04-13T17:38:11Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000226: Returns False when `.duplo/duplo.json` does not exist (not a duplo project). <!-- created_at: 2026-04-13T17:41:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000227: Returns True when `.duplo/duplo.json` exists AND SPEC.md is absent OR SPEC.md has neither signal. <!-- created_at: 2026-04-13T17:43:13Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000228: Why two signals: Phase 4 instructs users to author SPEC.md by hand using the template as a starting point. A user who writes a valid minimal new-format SPEC.md without copying the top-matter comment would otherwise stay stuck in migration forever. The `## Sources` structural signal is the lowest-ceremony marker of new-format intent. <!-- created_at: 2026-04-13T22:33:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000236: Tests: <!-- created_at: 2026-04-13T06:43:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000229: returns True for old layout (has `.duplo/duplo.json`, no SPEC.md) <!-- created_at: 2026-04-13T17:50:00Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000230: returns True for old layout with an old-format SPEC.md (has `.duplo/duplo.json`, SPEC.md exists but has neither marker nor `## Sources`) <!-- created_at: 2026-04-13T17:56:45Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000231: returns False for new-format with marker string (has `.duplo/duplo.json`, SPEC.md contains `"How the pieces fit together:"`) <!-- created_at: 2026-04-13T17:58:30Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000232: returns False for new-format with `## Sources` heading but no marker string (structural fallback) <!-- created_at: 2026-04-13T18:00:01Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000233: returns False when `.duplo/duplo.json` does not exist (not a duplo project at all) <!-- created_at: 2026-04-13T18:01:40Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000234: returns False when both signals present (belt-and-braces) <!-- created_at: 2026-04-13T18:03:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000235: `## Sources` check uses multiline anchor so an `## Sources` line mid-document matches, but a line like `My sources` or `### Sources` does not <!-- created_at: 2026-04-13T18:08:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000246: Add the migration message constant and `_check_migration` wrapper <!-- created_at: 2026-04-13T22:33:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000238: Define `_MIGRATION_MESSAGE` as a module-level constant in `duplo/migration.py` containing the migration message text verbatim per design/MIGRATION-design.md § Behavior (the "Phase 2 message" block — the version that says "Author a SPEC.md by hand using SPEC-template.md"). Do NOT use the Phase 4 version (which references `duplo init`); `duplo init` does not exist yet. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000239: Message lists the five steps: create `ref/`, move reference files, hand-author SPEC.md using SPEC-template.md with minimum fields (Purpose, Architecture, Sources, References), run `duplo` again. Mentions that PLAN.md, `.duplo/duplo.json`, and source code are unchanged. <!-- created_at: 2026-04-13T18:18:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000240: Implement `_check_migration(target_dir: Path) -> None` per design/MIGRATION-design.md § Implementation. If `needs_migration(target_dir)` returns True, print `_MIGRATION_MESSAGE` and `sys.exit(1)`. Otherwise return without doing anything. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000245: Tests: <!-- created_at: 2026-04-13T06:43:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000241: `_check_migration` on an old-layout directory: patches `sys.exit` and `print` (or captures via `capsys`), confirms the message is printed and exit is called with code 1 <!-- created_at: 2026-04-13T18:23:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000242: `_check_migration` on a new-format directory: no output, no exit, function returns None <!-- created_at: 2026-04-13T18:26:13Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000243: `_check_migration` on a non-duplo directory (no `.duplo/duplo.json`): no output, no exit <!-- created_at: 2026-04-13T18:30:45Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000244: Message text test: pin the exact message content by snapshot comparison to a fixture file. This protects against accidental wording drift. <!-- created_at: 2026-04-13T18:32:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000256: Wire `_check_migration` into `main.py` dispatch <!-- created_at: 2026-04-13T19:28:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000247: Per design/MIGRATION-design.md § Implementation "Phase 2 dispatch order": at the top of `main()`, after argv parsing but before any other work, branch on subcommand. If subcommand is `fix` or `investigate`, dispatch to the existing handlers WITHOUT calling `_check_migration` (those subcommands work on already-initialized projects regardless of layout and should not be blocked by migration). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000248: If there is no subcommand (the default `duplo` invocation), call `_check_migration(Path.cwd())` FIRST, before any other work. If `_check_migration` exits, nothing else in `main()` runs. <!-- created_at: 2026-04-13T18:49:54Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000249: If `_check_migration` returns, proceed with the existing no-subcommand code path unchanged. `_first_run` and `_subsequent_run` are NOT touched in this phase. <!-- created_at: 2026-04-13T18:55:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000250: Do NOT add an `init` branch — `duplo init` does not exist yet; that lands in Phase 6. If the user types `duplo init` today, argparse should reject it with an unknown-subcommand error as it does now. <!-- created_at: 2026-04-13T22:33:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000255: Tests (these are integration-style tests against `main`, using `capsys` and monkeypatching `sys.argv` / `Path.cwd`): <!-- created_at: 2026-04-13T19:28:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000251: `duplo` (no args) in an old-layout temp directory: prints migration message, exits 1, does not call `_subsequent_run` or `_first_run` <!-- created_at: 2026-04-13T19:05:10Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000252: `duplo` (no args) in a new-format temp directory: migration check passes silently, proceeds to the existing dispatch (may exit for other reasons like missing purpose, but NOT the migration message) <!-- created_at: 2026-04-13T19:11:24Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000253: `duplo fix` in an old-layout directory: bypasses migration check, dispatches to existing `fix` handler. Confirm by patching the fix handler and asserting it was called. <!-- created_at: 2026-04-13T19:14:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
    - [x] T-000254: `duplo investigate` in an old-layout directory: same as above for the investigate handler. <!-- created_at: 2026-04-13T19:28:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000262: Add tests for edge cases specific to migration detection <!-- created_at: 2026-04-13T19:45:42Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000257: Case: `.duplo/duplo.json` is corrupted JSON. `needs_migration` should still return True (the presence of the file, not its contents, is what matters for migration detection). The check must NOT try to parse it. <!-- created_at: 2026-04-13T19:31:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000258: Case: `SPEC.md` is zero bytes. Same classification as "SPEC.md absent" — neither signal matches, so migration needed. <!-- created_at: 2026-04-13T19:33:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000259: Case: `SPEC.md` contains only the marker string inside an HTML comment (`<!-- How the pieces fit together: ... -->`). The substring match still hits; classifies as new-format. This is intentional — the marker exists in the template as part of a comment, and that's where it will appear in real specs. No special comment-handling needed. <!-- created_at: 2026-04-13T19:36:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000260: Case: `SPEC.md` is a BOM-prefixed UTF-8 file. The read must handle BOM correctly (use `Path.read_text(encoding="utf-8")` which strips BOM automatically, or equivalent). Test with a fixture that has a UTF-8 BOM and a new-format signal. <!-- created_at: 2026-04-13T19:41:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000261: Case: `SPEC.md` contains `## Sources` inside a fenced code block (e.g. an example in the top-matter comment). The multiline regex will match this as a false positive — document this as acceptable behavior (better to let through a near-new-format file than to force-migrate it) but add a test pinning the current behavior so any future fix is intentional. <!-- created_at: 2026-04-13T19:45:42Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000266: Update project documentation to reflect Phase 4 shipping <!-- created_at: 2026-04-13T22:33:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000263: In `README.md` (if it exists at the project root): add a short section or update the existing "Getting started" to mention that existing duplo projects will be prompted to migrate on their next run, and that migration is manual (author SPEC.md by hand; `duplo init` is not available yet). <!-- created_at: 2026-04-13T19:47:36Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000264: In `CLAUDE.md` (if it exists): if it currently mentions the old subcommand model or describes duplo's behavior in a way that's now stale, update to reference the current state (Phase 4 shipped: migration gate is in place; pipeline still uses `_subsequent_run` / `_first_run` as before). <!-- created_at: 2026-04-13T22:33:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000265: Do NOT update SPEC-template.md, SPEC-guide.md, or any design doc in `/Users/mhcoen/proj/duplo/*.md` — those are the forward-looking design specifications and are already current. <!-- created_at: 2026-04-13T20:06:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

### Manual verification (all complete)

- [x] T-000267: Create a scratch directory that looks like a pre-redesign duplo project (`mkdir -p scratch/.duplo && echo '{}' > scratch/.duplo/duplo.json`). Do NOT create a SPEC.md. Run `duplo` from that directory. Confirm the migration message prints and the command exits with status 1. Confirm the message contents match design/MIGRATION-design.md's Phase 2 message exactly. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000268: In the same scratch directory, author a minimal new-format SPEC.md by hand: `## Purpose`, `## Architecture`, and an empty `## Sources` section (just the heading). Run `duplo` again. Confirm it does NOT print the migration message and instead proceeds into the existing pipeline (which will likely error on other grounds like missing purpose content — that's expected; the point is that the migration gate no longer fires). <!-- created_at: 2026-04-13T22:33:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000269: In a completely empty directory (no `.duplo/` at all), run `duplo`. Confirm `needs_migration` returns False and `duplo` proceeds to its existing no-duplo-project behavior. No migration message should appear. <!-- created_at: 2026-04-13T22:33:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000270: In a pre-redesign scratch directory, run `duplo fix` and `duplo investigate`. Confirm neither prints the migration message — they dispatch to their existing handlers unchanged. <!-- created_at: 2026-04-13T22:33:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
- [x] T-000271: Run the full test suite: `pytest -x`. Confirm no pre-existing tests broke. Confirm the new migration tests pass. <!-- created_at: 2026-04-13T22:33:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

---

## Phase 5: Pipeline integration
<!-- phase_id: phase_005 -->

Wires the new SPEC-driven inputs through the actual orchestration code in `main.py`. The largest phase in the redesign.

Design reference: `PIPELINE-design.md` (authoritative). All tasks in this phase reference design sections by name; the design doc is the source of truth for contracts and edge cases. When a task description and the design doc disagree, the design doc wins; flag the discrepancy for resolution rather than silently picking one interpretation.

The principle: every pipeline stage takes role-filtered input from the parser instead of running heuristics on raw directory contents. Implementation order respects dependencies — foundation (URL canonicalization, fetcher, helper) before pipeline-stage updates, helpers before orchestration.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all code generation. Ruff for linting, pytest for tests. All AI calls go through `claude -p` (no direct API calls).

## Pre-work: missing per-stage formatter

- [x] T-000275: [BATCH] Add `format_counter_example_sources(spec) -> list[SourceEntry]` to `duplo/spec_reader.py` <!-- created_at: 2026-04-14T00:32:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000272: Returns source entries where `role` is `counter-example`, excluding `proposed: true` AND `discovered: true`. Per PIPELINE-design.md § `format_counter_example_sources`. <!-- created_at: 2026-04-14T00:32:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000273: This is the missing per-stage formatter from Phase 3 — Phase 3 added the four reference formatters and `format_scrapeable_sources` but not this one. Required by the investigator changes in 5.11. <!-- created_at: 2026-04-14T00:32:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000274: Tests: returns counter-example sources only; excludes proposed/discovered; empty input handled; counter-example sources with other flags (e.g. `scrape: deep`, which the user almost certainly didn't mean) still returned by this filter (separate concern from the scrape-depth diagnostic emitted by `format_scrapeable_sources`). <!-- created_at: 2026-04-14T00:32:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Foundation: URL canonicalization

- [x] T-000280: [BATCH] Create new module `duplo/url_utils.py` with `canonicalize_url(url: str) -> str` <!-- created_at: 2026-04-14T00:38:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000276: New module per design (the existing files are too long). Imports stdlib only (`urllib.parse`). <!-- created_at: 2026-04-14T00:38:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000277: Implement the four canonicalization rules per PIPELINE-design.md § "URL canonicalization": (1) lowercase scheme and host; (2) strip default ports (80 on http, 443 on https); (3) strip fragment (#section); (4) strip trailing slash from ALL paths INCLUDING the root path `/`. Preserve query strings. <!-- created_at: 2026-04-14T00:38:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000278: The trailing-slash rule MUST apply to the root path: `https://a.com/` → `https://a.com`. Do not special-case the root. Per PIPELINE-design.md § "Why strip all trailing slashes, including root" — root-path slash treatment is what makes user-authored host-only URLs and fetcher post-redirect URLs compare equal. <!-- created_at: 2026-04-14T00:38:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000279: Tests: each rule exercised individually; combined rules; root-path slash stripped (`https://a.com/` → `https://a.com`); non-root path slash stripped (`https://a.com/docs/` → `https://a.com/docs`); already-canonical URL unchanged; query string preserved (`https://a.com/?q=1` → `https://a.com?q=1` — root slash gone, query kept); fragment stripped; uppercase scheme/host lowercased; default port stripped on http (80) and https (443); non-default ports preserved (`https://a.com:8443/` → `https://a.com:8443`). <!-- created_at: 2026-04-14T00:38:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Foundation: fetch_site signature changes

- [x] T-000291: Add `scrape_depth` parameter and 5-tuple return to `duplo/fetcher.py:fetch_site` <!-- created_at: 2026-04-14T02:05:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000281: Per PIPELINE-design.md § `fetcher.py`. New signature: `fetch_site(url, *, scrape_depth: Literal["deep", "shallow", "none"] = "deep") -> tuple[str, list[CodeExample], DocStructures, list[PageRecord], dict[str, str]]`. <!-- created_at: 2026-04-14T00:51:09Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000282: `scrape_depth="deep"` follows links but ONLY same-origin (same scheme + host + port). Cross-origin links are NOT fetched in the same run — they are extracted later by `_collect_cross_origin_links` for SPEC.md `discovered:` write-back. <!-- created_at: 2026-04-14T01:02:13Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000283: `scrape_depth="shallow"` fetches only the entry URL, no link-following. <!-- created_at: 2026-04-14T01:06:24Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000284: `scrape_depth="none"` does no fetch, returns empty content tuple plus empty `raw_pages` dict. <!-- created_at: 2026-04-14T01:11:00Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000285: The fifth return value `raw_pages: dict[str, str]` maps EVERY successfully fetched canonical URL to its raw HTML. For `deep`, includes entry URL plus same-origin pages followed and successfully fetched. For `shallow`, exactly one entry on success, empty dict on failure. For `none`, empty dict. <!-- created_at: 2026-04-14T01:23:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000286: All URL keys in `raw_pages` and all `PageRecord.url` values MUST be canonicalized via `url_utils.canonicalize_url`. Apply post-redirect (after the HTTP response, on the final URL the fetcher landed on). <!-- created_at: 2026-04-14T01:36:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000287: Failed fetches (404, timeout, non-HTML content-type, decode failure) are NOT included in `raw_pages` and NOT included in `page_records`. Both structures stay in sync by construction. Failure surfaces via `record_failure("fetch_site", "fetch", ...)`. <!-- created_at: 2026-04-14T01:46:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000288: HTML decode: UTF-8 with `errors="replace"` per the design. <!-- created_at: 2026-04-14T01:53:20Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000289: Update existing callers of `fetch_site` in `duplo/main.py` to handle the new 5-tuple. Existing call sites that don't yet need `raw_pages` should still unpack it (assign to `_` if unused) so they don't crash on the tuple-length change. <!-- created_at: 2026-04-14T02:02:11Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000290: Tests: 5-tuple return shape; `scrape_depth="shallow"` fetches only entry URL and returns one `raw_pages` entry; `scrape_depth="deep"` follows same-origin links and returns multiple entries; `scrape_depth="deep"` does NOT fetch cross-origin links (cross-origin URL not in `raw_pages`, no PageRecord for it); `scrape_depth="none"` does no HTTP and returns empty `raw_pages`; failed fetch (mock 404) omits the URL from BOTH `raw_pages` AND `page_records`; canonical URL keys (post-redirect URL canonicalized); decode error doesn't crash, omits the URL with diagnostic. <!-- created_at: 2026-04-14T02:05:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Foundation: cross-origin link collection helper

- [x] T-000298: [BATCH] Implement `_collect_cross_origin_links(raw_pages, source_url) -> list[str]` in `duplo/orchestrator.py` (new module) <!-- created_at: 2026-04-14T02:11:29Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000292: Per PIPELINE-design.md § `_collect_cross_origin_links`. Place in a new `duplo/orchestrator.py` module since `main.py` is already long; helper functions used by orchestration go here. <!-- created_at: 2026-04-14T02:11:29Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000293: Parse each HTML page in `raw_pages.values()`, extract every `<a href="...">` target, resolve to absolute URL, canonicalize via `url_utils.canonicalize_url`. <!-- created_at: 2026-04-14T02:11:29Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000294: Compare canonical form's (scheme, host, port) against the canonical `source_url`'s. Different = cross-origin = include in result. <!-- created_at: 2026-04-14T02:11:29Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000295: Only `<a href>` is considered. NOT `<link>`, `<script src>`, `<img src>`, `<video src>`, `<source src>`, etc. Per design § "Decisions". <!-- created_at: 2026-04-14T02:11:29Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000296: Return deduplicated list of canonical URLs. Per design: dedup happens here (per-run) and again in `append_sources` (against existing SPEC.md). Belt and braces; both use `canonicalize_url` so divergence is impossible by construction. <!-- created_at: 2026-04-14T02:11:29Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000297: Tests: same-origin links excluded; cross-origin links included; subdomain treated as cross-origin (`https://numi.app` vs `https://docs.numi.app` are different hosts); only `<a href>` collected (`<img src>` to cross-origin CDN is NOT collected); duplicates within a single page collapsed; duplicates across pages collapsed; canonicalization applied (uppercase or trailing-slash variants of the same URL collapse to one); empty `raw_pages` returns `[]`; relative href resolved against the page URL it appeared on (not against `source_url`) — a relative `href="docs"` on `https://example.com/foo/page.html` resolves to `https://example.com/foo/docs`, not `https://example.com/docs`. <!-- created_at: 2026-04-14T02:11:29Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Pipeline stage updates

- [x] T-000305: Refactor `duplo/scanner.py:scan_directory` to point at `ref/` and drop relevance heuristics <!-- created_at: 2026-04-14T03:10:52Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000299: Per PIPELINE-design.md § `scanner.py`. `scan_directory(target_dir)` becomes `scan_directory(ref_dir)`; callers that pass `"."` change to pass `target_dir / "ref"`. <!-- created_at: 2026-04-14T02:31:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000300: Drop the relevance scoring (image dimensions, file size). Roles are declared in `## References`, not inferred. <!-- created_at: 2026-04-14T02:38:33Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000301: Add a diagnostic for files in `ref/` that are not listed in `## References`: `record_failure("scanner", "io", f"file in ref/ has no entry in ## References; will be ignored: {path}")`. Diagnostic only — does not error. <!-- created_at: 2026-04-14T02:46:42Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000302: `scan_files(paths)` (used for analyzing specific changed files in subsequent runs) keeps working but gets a parallel role lookup: each file's path is checked against the parsed `## References` to determine its role. <!-- created_at: 2026-04-14T02:51:36Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000303: Update existing callers in `duplo/main.py` to pass `ref/` instead of project root. <!-- created_at: 2026-04-14T03:01:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000304: Tests: `scan_directory` only enumerates files under `ref/`, ignoring everything else in project root; file in `ref/` listed in `## References` is included with its declared role; file in `ref/` NOT listed in `## References` produces diagnostic and is excluded from the result; relevance heuristics removed (a tiny image is included if declared, a huge irrelevant one is excluded if not declared); `scan_files` role-lookup matches paths against `## References` correctly. <!-- created_at: 2026-04-14T03:10:52Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000312: Refactor `duplo/extract_design` callers to use `format_visual_references` and the four-source design input set <!-- created_at: 2026-04-14T04:17:20Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000306: Per PIPELINE-design.md § `design_extractor.py`. The design input is the union of: (1) `format_visual_references(spec)` paths; (2) accepted frames from videos with `visual-target` in their roles; (3) accepted frames from scraped product-reference videos; (4) images downloaded from product-reference sources via `_download_site_media`. <!-- created_at: 2026-04-14T03:18:02Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000307: All four sources MUST exclude `proposed: true` references and frames derived from them. Filter via the existing per-stage formatters which already enforce this. <!-- created_at: 2026-04-14T03:26:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000308: Implement frame-content-hash dedup per design § "Compose the design extraction input set from FOUR sources". A user with both a ref/-declared local copy of a demo video AND the same video appearing on a scraped product page should not have its frames counted twice. Use `hashlib.sha256(frame.read_bytes()).hexdigest()` as the dedup key. ref-declared frames win on collision (added to seen set first). <!-- created_at: 2026-04-14T03:37:54Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000309: Update `extract_design`'s call site in `duplo/main.py` to pass `design_input` composed per the rules above instead of the current project-root scan. <!-- created_at: 2026-04-14T03:57:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000310: Implementation lives in the orchestrator (composition of input set), not in `design_extractor.py` itself. `extract_design` continues to take `list[Path]`. <!-- created_at: 2026-04-14T04:10:50Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000311: Tests: visual-target ref files included; non-visual ref files excluded; `proposed: true` visual ref excluded; dual-role behavioral+visual video contributes its accepted frames; behavioral-only video does NOT contribute frames to design; scraped product-reference video frames included; non-product-reference scraped videos do NOT contribute; site media images included; frame-content-hash dedup: same-content frame from ref/ and scraped path counted once; ref-declared frame wins on hash collision. <!-- created_at: 2026-04-14T04:17:20Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000318: Refactor video pipeline to use `format_behavioral_references` <!-- created_at: 2026-04-14T05:21:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000313: Per PIPELINE-design.md § `video_extractor.py and friends`. Callers of `extract_all_videos` pass paths from `format_behavioral_references(spec)` instead of all videos. <!-- created_at: 2026-04-14T04:32:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000314: EXTEND the behavioral input set with `site_videos` (the second element of `_download_site_media`'s return tuple) per the orchestration sketch. Scraped demo videos from product-reference pages are first-class behavioral input. <!-- created_at: 2026-04-14T04:52:01Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000315: Pin the source-path-preservation contract for `extract_all_videos` per PIPELINE-design.md § `_accepted_frames_by_source` "Source-path preservation contract": `ExtractionResult.source` MUST equal the input path byte-for-byte — no `Path.resolve()`, no symlink following, no normalization. Enforce in code (no transformation in `extract_all_videos`) and pin with a test that passes a relative path and asserts `result.source` equals that same relative path. <!-- created_at: 2026-04-14T04:56:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000316: Add an assertion at the orchestrator's behavioral-paths construction point per the orchestration sketch: `assert len(behavioral_paths) == len(set(behavioral_paths))`. ref/ and `.duplo/site_media/` live under different roots so collisions require user error; the assert surfaces that error visibly. <!-- created_at: 2026-04-14T05:14:02Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000317: Tests: `format_behavioral_references` paths are passed to `extract_all_videos`; site_videos are added; ref/ video and scraped video both present in input; source-path-preservation: relative input path round-trips through `ExtractionResult.source` unchanged; collision assertion fires when same path appears in both lists. <!-- created_at: 2026-04-14T05:21:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000322: [BATCH] Implement `_accepted_frames_by_source(filtered_results) -> dict[Path, list[Path]]` helper in `duplo/orchestrator.py` <!-- created_at: 2026-04-14T05:25:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000319: Per PIPELINE-design.md § `_accepted_frames_by_source`. One-line implementation (`{r.source: r.frames for r in filtered_results}`) but must live as a named helper so the post-filter contract has a named place in tests. <!-- created_at: 2026-04-14T05:25:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000320: **Critical**: the input must be POST-FILTER. Callers MUST run `frame_filter.apply_filter` on `ExtractionResult.frames` before passing to this helper. The orchestration sketch uses `dataclasses.replace(r, frames=apply_filter(filter_frames(r.frames)))` to preserve `source` and `error` while replacing `frames`. <!-- created_at: 2026-04-14T05:25:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000321: Tests: (a) lookup returns correct frames per source; (b) if called with unfiltered results (rejected frames present), rejected frames appear in output — demonstrating the contract violation is detectable; (c) source-path preservation: keys equal the input `ExtractionResult.source` values byte-for-byte (no path transformation). <!-- created_at: 2026-04-14T05:25:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000328: Refactor PDF/text/markdown doc extraction with `docs_text_extractor` <!-- created_at: 2026-04-14T06:30:43Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000323: Per PIPELINE-design.md § `pdf_extractor.py and text/markdown docs`. New function `docs_text_extractor` that takes references with `docs` in `roles` and produces a single text blob per file, routed by extension. <!-- created_at: 2026-04-14T05:37:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000324: Routing: `.pdf` → existing `extract_pdf_text` path; `.txt` → read directly; `.md` → read directly (markdown is text; the LLM handles formatting). <!-- created_at: 2026-04-14T05:44:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000325: Place `docs_text_extractor` in `duplo/pdf_extractor.py` (rename file later if it becomes misleading) OR in a new `duplo/docs_extractor.py` module. The new module is preferred per the "new module over extending long files" preference. <!-- created_at: 2026-04-14T05:55:36Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000326: Combined text feeds into feature extraction the same way today's PDF text does. Update the `extract_features` call site to include doc-references-derived text. <!-- created_at: 2026-04-14T06:18:47Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000327: Tests: PDF input routes to `extract_pdf_text`; `.txt` input read directly; `.md` input read directly; unknown extension produces diagnostic and is skipped; multiple docs combined into one blob. <!-- created_at: 2026-04-14T06:30:43Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000336: Refactor `extract_features` callers and add `_matches_excluded` post-extraction filter <!-- created_at: 2026-04-14T08:05:42Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000329: Per PIPELINE-design.md § `extractor.py (feature extraction)`. `scraped_text` becomes the concatenation of text from all scrapeable sources. `spec_text` continues to use `format_spec_for_prompt(spec)` (which already excludes unreviewed entries per Phase 3). `scope_include`/`scope_exclude` come from `spec.scope_include`/`spec.scope_exclude`. <!-- created_at: 2026-04-14T06:43:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000330: Implement `_matches_excluded(feature, scope_exclude) -> bool` per design § `_matches_excluded`. Place in `duplo/orchestrator.py` (new module from earlier task) or `duplo/extractor.py` if it fits naturally there. <!-- created_at: 2026-04-14T06:56:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000331: Matching rule: case-insensitive WORD-BOUNDARY regex (`\b...\b`), NOT substring. Multi-word excluded terms must match as contiguous word sequence. Per design: `"plugin API"` matches `"Plugin API"` and `"plugin API."` but not `"non-plugin-API"` or a description that mentions `"plugin API"` only as contrast. <!-- created_at: 2026-04-14T07:07:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000332: Compare against BOTH `feature.name` and `feature.description`. <!-- created_at: 2026-04-14T07:28:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000333: When a feature is dropped, emit diagnostic via `record_failure("extractor:scope_exclude", "...", f"scope_exclude '<term>' matched feature '<n>'; dropped")`. Use whichever of the existing diagnostics categories fits best (likely `"io"` since `extractor` doesn't have a dedicated category); flag in code review if a new category is warranted. <!-- created_at: 2026-04-15T03:25:47Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000334: Apply the post-extraction filter at the orchestrator level: `features = [f for f in features if not _matches_excluded(f, spec.scope_exclude)]` after `extract_features` returns, before `save_features`. <!-- created_at: 2026-04-14T07:59:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000335: Tests: word-boundary match (positive cases for exact phrase, with trailing punctuation, with leading whitespace); word-boundary non-match (negative cases for substring-only matches like `"non-plugin-API"`, `"plugins-API"`); case-insensitive; multi-word excluded term must match as contiguous sequence (`"plugin API"` excluded does NOT match a description that mentions `"plugin"` and `"API"` separately); feature dropped produces diagnostic naming term and feature; empty `scope_exclude` is no-op; multiple matches emit one diagnostic per (term, feature) pair. <!-- created_at: 2026-04-14T08:05:42Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000344: Refactor `_download_site_media` per the new signature <!-- created_at: 2026-04-14T09:48:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000337: Per PIPELINE-design.md § `_download_site_media signature under the new model`. New signature: `_download_site_media(raw_pages: dict[str, str]) -> tuple[list[Path], list[Path]]` returning `(image_paths, video_paths)`. <!-- created_at: 2026-04-14T08:18:50Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000338: Parameter is the dict of product-reference raw pages (NOT all raw pages). Caller passes `product_ref_raw_pages` per the orchestration sketch. <!-- created_at: 2026-04-14T08:43:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000339: Returns paths for EVERY embedded media resource that exists locally — BOTH newly-downloaded files AND files already present in cache. Per design § "Cached-vs-new rule": a URL-only project's second run finds all media cached; if the function returned only new paths, design extraction would silently get zero inputs on regeneration. <!-- created_at: 2026-04-14T08:53:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000340: Storage: `.duplo/site_media/<url-hash>/<filename>`. URL hash is the hash of the page URL the media was embedded in; filename is derived from the resource URL. <!-- created_at: 2026-04-14T09:07:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000341: Embedded-media origin handling per design § "Same-origin and embedded media": media is downloaded REGARDLESS of origin. The user authorized loading the page; the page's content includes its embedded media. This differs from cross-origin link behavior (which is recorded as discovered, not fetched). <!-- created_at: 2026-04-14T09:24:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000342: Parse `<img src>`, `<video src>`, AND `<source src>` tags. Resolve to absolute URLs against the embedding page URL (not against any `source_url`). <!-- created_at: 2026-04-14T09:30:55Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000343: Tests: image URL embedded in a page is downloaded and path returned; video URL same; cross-origin CDN image is downloaded (not skipped); cached file returns its existing path without re-downloading; mix of cached and new files all returned; zero embedded media returns `([], [])`; multiple pages each contributing media yields combined lists; HTTP failure on a single embed records diagnostic and skips that file but doesn't abort the function. <!-- created_at: 2026-04-14T09:48:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000349: Refactor `gap_detector` callers to pre-filter through `scope_exclude` <!-- created_at: 2026-04-14T10:33:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000345: Per PIPELINE-design.md § `gap_detector.py`. No change to `detect_gaps` itself. The features list passed in is filtered through `scope_exclude` at the orchestrator level (handled by the previous `_matches_excluded` task) before `detect_gaps` is called. <!-- created_at: 2026-04-14T09:55:56Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000346: Verify no existing call site of `detect_gaps` bypasses the filter. If any do, route them through the same filter. <!-- created_at: 2026-04-14T09:58:28Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000347: `detect_design_gaps` operates on the AUTO-GENERATED block in SPEC.md's `## Design` section AS WELL AS on `duplo.json`'s `design_requirements` (redundant during transition; can simplify in Phase 7). <!-- created_at: 2026-04-14T10:15:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000348: Tests: feature list passed to `detect_gaps` excludes scope_exclude'd entries; `detect_design_gaps` reads from both AUTO-GENERATED block and `duplo.json` (verify both code paths exist). <!-- created_at: 2026-04-14T10:33:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Investigator

- [x] T-000357: Update investigator to include counter-examples, counter-example sources, docs references, and behavior contracts <!-- created_at: 2026-04-14T12:04:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000350: Per PIPELINE-design.md § `investigator.py`. `investigate(bugs, ...)` gains role-filtered context inputs. <!-- created_at: 2026-04-14T10:54:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000351: Counter-example references via `format_counter_examples(spec)` get included in the prompt with an explicit "AVOID this pattern" label. <!-- created_at: 2026-04-14T11:03:15Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000352: Counter-example SOURCES via `format_counter_example_sources(spec)` (the new formatter from the pre-work task) get included as URL+notes context with the same "AVOID" framing. **The URL is NOT fetched** — declarative context only. Pin this with a test. <!-- created_at: 2026-04-14T11:20:03Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000353: Docs references via `format_doc_references(spec)` get included as supplementary context (PDF text via `extract_pdf_text`, .txt/.md via direct read — reuse the `docs_text_extractor` from the earlier task). <!-- created_at: 2026-04-14T11:26:56Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000354: `## Behavior` contracts via `spec.behavior_contracts` get included as ground-truth expectations. <!-- created_at: 2026-04-14T11:37:40Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000355: Update the investigator's structured-output prompt so that diagnoses can reference these new context types: e.g. `Diagnosis(... contradicts: "behavior contract X")` or `Diagnosis(... avoids_pattern: "counter-example Y")`. The exact prompt rewording is at Claude Code's discretion as long as the structure supports referencing the new context. <!-- created_at: 2026-04-14T11:51:47Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000356: Tests: counter-example refs included with AVOID label; counter-example source URLs included with AVOID label and NOT fetched (mock the fetcher and assert it was not called for counter-example URLs); docs refs included as supplementary; behavior contracts included as ground-truth; investigator output structure supports referencing all new context types. <!-- created_at: 2026-04-14T12:04:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Drafter write helpers (minimal subset)

- [x] T-000365: [BATCH] Create `duplo/spec_drafter.py` with `append_sources(spec_text, new_entries) -> str` <!-- created_at: 2026-04-14T12:18:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000358: New module per the design (text-layer module independent of pipeline stages — must NOT import from `duplo/extractor.py`, `duplo/design_extractor.py`, etc.). <!-- created_at: 2026-04-14T12:18:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000359: `append_sources(existing_spec_text: str, new_entries: list[SourceEntry]) -> str` returns modified spec text with new entries appended to `## Sources`. <!-- created_at: 2026-04-14T12:18:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000360: Dedup-by-canonical: skip entries whose canonical URL already exists in the spec's `## Sources` (regardless of whether the existing entry has `proposed:` or `discovered:` flags). Use `url_utils.canonicalize_url` for comparison; the parser stores canonical URLs in `SourceEntry.url` already (per Phase 3) so existing entries are already canonical. <!-- created_at: 2026-04-14T12:18:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000361: Idempotent: calling `append_sources(s, [])` returns `s` unchanged. Calling `append_sources(append_sources(s, [e]), [e])` returns the same string as `append_sources(s, [e])` (the second call's `e` is dedup'd). <!-- created_at: 2026-04-14T12:18:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000362: Format new entries with their flags: `discovered: true` and/or `proposed: true` lines appear as field lines under the entry per design/PARSER-design.md § `## Sources` parser format. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000363: If `## Sources` section does not exist in `existing_spec_text`, create it (heading + entries) appended to the spec. Place it after `## Architecture` if present, else at end of file. Maintain the same blank-line conventions as the rest of SPEC.md. <!-- created_at: 2026-04-14T12:18:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000364: Tests: append single new entry; append multiple; dedup against existing canonical URL (entry not added); dedup against existing URL with different trailing slash (canonicalization in action); dedup is case-insensitive on host; idempotent (double-call returns same result); empty new_entries returns input unchanged; missing `## Sources` section is created; flags `discovered: true` and `proposed: true` written correctly. <!-- created_at: 2026-04-14T12:18:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000372: [BATCH] Add `update_design_autogen(spec_text, body) -> str` to `duplo/spec_drafter.py` <!-- created_at: 2026-04-14T12:36:52Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000366: `update_design_autogen(existing_spec_text: str, body: str) -> str` returns modified spec text with the AUTO-GENERATED block in `## Design` populated. <!-- created_at: 2026-04-14T12:36:52Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000367: Write-once-never-replace semantics per PIPELINE-design.md § "Note on the autogen-cache divergence": if a well-formed AUTO-GENERATED block already exists with non-empty body, return `existing_spec_text` unchanged. The orchestrator is responsible for checking and skipping the Vision call when an autogen block already exists; this function is a defense-in-depth no-op in that case rather than an overwrite. <!-- created_at: 2026-04-14T12:36:52Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000368: If `## Design` section exists with no AUTO-GENERATED block: append the block (with BEGIN/END comment markers per design/PARSER-design.md § `## Design` parser) at the end of the section, after any existing user prose. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000369: If `## Design` section does not exist: create it with the AUTO-GENERATED block. Place after `## Architecture` (or after `## Sources` if both present). Maintain blank-line conventions. <!-- created_at: 2026-04-14T12:36:52Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000370: BEGIN/END markers: use the EXACT same comment-marker form that the parser's `_AUTOGEN_RE` matches (per design/PARSER-design.md). Pin with a test that round-trips through the parser. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000371: Tests: empty `## Design` gets autogen block appended; existing user prose in `## Design` preserved with autogen appended after it; existing autogen block with non-empty body NOT replaced (write-once); existing autogen block with empty body is replaced (allows regeneration after user clears the block); missing `## Design` section is created; round-trip: `update_design_autogen` output parses back to a spec where `spec.design.auto_generated` equals the body. <!-- created_at: 2026-04-14T12:36:52Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Save_raw_content update

- [x] T-000378: [BATCH] Update `duplo/saver.py:save_raw_content` per the new signature <!-- created_at: 2026-04-14T12:55:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000373: Per PIPELINE-design.md § `save_raw_content` signature. New signature: `save_raw_content(raw_pages: dict[str, str], page_records: list[PageRecord], *, target_dir: Path = Path.cwd()) -> None`. <!-- created_at: 2026-04-14T12:55:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000374: For each `PageRecord`, look up `raw_pages[record.url]` and write the HTML to `.duplo/raw_pages/<sha256(record.url)>.html`. <!-- created_at: 2026-04-14T12:55:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000375: Cache filename is the SHA-256 of the canonical URL. NOT the content hash. `PageRecord.content_hash` continues to be stored inside the record for change detection but is NOT used for the cache filename. <!-- created_at: 2026-04-14T12:55:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000376: **Behavior on missing key**: if `record.url` has no entry in `raw_pages`, this indicates a construction-invariant violation. Log via `record_failure("save_raw_content", "io", f"no raw_pages entry for {record.url}; record skipped")` and SKIP that record. Do NOT raise. Per design § "Behavior on missing keys: log and skip, do not raise." <!-- created_at: 2026-04-14T12:55:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000377: Tests: each record's HTML written to URL-hashed filename; URL-hash filename matches `sha256(record.url).hexdigest()`; existing file at the same hash overwritten (one file per URL); missing key for a record skipped with diagnostic; remaining records still persisted when one is skipped; empty `raw_pages` and empty `page_records` no-op without error. <!-- created_at: 2026-04-14T12:55:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## BuildPreferences and app_name

- [x] T-000384: Implement `parse_build_preferences(architecture_prose) -> BuildPreferences` in `duplo/build_prefs.py` (new module) <!-- created_at: 2026-04-14T14:20:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000379: Per PIPELINE-design.md § BuildPreferences. New module per the "new module over extending long files" preference. NOT in `spec_reader.py` (design/PARSER-design.md forbids LLM calls there) and NOT in `questioner.py` (which is being replaced). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000380: Calls `claude -p` with structured-output prompt asking for `{platform, language, framework, dependencies: list[str], other_constraints: list[str]}` extracted from the prose. Returns `BuildPreferences` with whatever fields the LLM populated; missing fields stay at default. <!-- created_at: 2026-04-14T13:16:11Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000381: Section-scoped hash invalidation per design: the bytes hashed are `spec.architecture` (the parsed, comment-stripped content of `## Architecture`), NOT the whole SPEC.md file. Stored in `.duplo/duplo.json` under `architecture_hash`. Re-parse only when the hash changes. <!-- created_at: 2026-04-14T13:47:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000382: When the LLM returns no usable fields, return `BuildPreferences()` (all defaults). Surface as a WARNING via `validate_for_run`, not an error — plan generation handles all-defaults gracefully. <!-- created_at: 2026-04-14T13:59:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000383: Tests: parse with a typical architecture prose (Swift macOS app etc.); fields populated correctly; missing fields default; hash invalidation works (changing architecture re-triggers parse); commented-out content in `## Architecture` does NOT change hash (per design/PARSER-design.md `_strip_comments` runs before storage); cache hit avoids the LLM call; all-defaults BuildPreferences emits warning via `validate_for_run`. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000390: [BATCH] Implement app_name derivation logic in `duplo/orchestrator.py` <!-- created_at: 2026-04-14T14:34:48Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000385: Per PIPELINE-design.md § app_name. New function `derive_app_name(spec, target_dir) -> str`. <!-- created_at: 2026-04-14T14:34:48Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000386: If `## Sources` includes a product-reference URL, derive a candidate app_name from the scraped product identity using existing `validator.validate_product_url` behavior (or whatever produces the product name today). <!-- created_at: 2026-04-14T14:34:48Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000387: If no URL, derive from project directory name as fallback (`numi-clone/` → `numi-clone`). <!-- created_at: 2026-04-14T14:34:48Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000388: Stored in `.duplo/product.json` under `app_name`. The user can edit this file directly if the auto-derived name is wrong. <!-- created_at: 2026-04-14T14:34:48Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000389: Tests: URL-based derivation produces expected name; no-URL fallback uses directory name; `product.json` written; user-edited `product.json` is NOT overwritten on subsequent runs (load and preserve existing `app_name` if present). <!-- created_at: 2026-04-14T14:34:48Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Orchestration: source iteration with first-source-wins dedup

- [x] T-000398: Implement multi-source iteration loop in `_subsequent_run` <!-- created_at: 2026-04-14T16:17:16Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000391: Per PIPELINE-design.md § `main.py orchestration` orchestration sketch. Iterate `format_scrapeable_sources(spec)` and call `fetch_site` for each. <!-- created_at: 2026-04-14T15:04:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000392: Maintain `seen_canonical_urls: set[str]` for first-source-wins dedup of `PageRecord` entries. <!-- created_at: 2026-04-14T15:16:34Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000393: Maintain `all_raw_pages: dict[str, str]` and `product_ref_raw_pages: dict[str, str]` using `setdefault` (NOT `update` — dict.update would silently let later sources overwrite earlier; setdefault preserves first-source-wins). <!-- created_at: 2026-04-14T15:22:39Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000394: Accumulate `combined_text`, `all_code_examples`, `merged_doc_structures` across sources. <!-- created_at: 2026-04-14T15:35:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000395: `discovered_urls` collected from `_collect_cross_origin_links(source_raw_pages, source.url)` ONLY when `source.scrape == "deep"`. Per design: shallow sources fetched only the entry URL; collecting cross-origin links and recording them as `discovered: true` would silently append URLs the user never asked duplo to explore. Pin with a test. <!-- created_at: 2026-04-14T15:48:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000396: After the loop, if `all_code_examples`, call `save_examples(all_code_examples)`. If `all_page_records`, call `save_reference_urls(all_page_records)` and `save_raw_content(all_raw_pages, all_page_records)`. If `merged_doc_structures`, call `save_doc_structures(merged_doc_structures)`. <!-- created_at: 2026-04-14T15:57:09Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000397: Tests: multi-source iteration calls `fetch_site` once per scrapeable source; first-source-wins dedup of page_records (URL appearing in source A and source B is recorded once with source A's record); first-source-wins for raw_pages (HTML from source A preserved over source B for the same canonical URL); discovered_urls collected only from `deep` sources, NOT from `shallow`; non-product-reference sources do not contribute to product_ref_raw_pages; doc_structures merged across sources. <!-- created_at: 2026-04-14T16:17:16Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000402: [BATCH] Wire SPEC.md write-back for discovered URLs in `_subsequent_run` <!-- created_at: 2026-04-14T17:14:52Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000399: After the source iteration loop, if `discovered_urls` is non-empty: read SPEC.md from disk, call `spec_drafter.append_sources(existing, [SourceEntry(url=u, role="docs", scrape="deep", discovered=True) for u in discovered_urls])`, and write back ONLY if the result differs from the input. Per PIPELINE-design.md orchestration sketch. <!-- created_at: 2026-04-14T16:45:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000400: Default `role="docs"` and `scrape="deep"` for discovered entries per the design. <!-- created_at: 2026-04-14T16:49:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000401: Tests: discovered URLs trigger SPEC.md write; SPEC.md unchanged when all discovered URLs are already in `## Sources` (idempotency through `append_sources` dedup); `discovered: true` flag and `role: docs` written; SPEC.md NOT modified when `discovered_urls` is empty. <!-- created_at: 2026-04-14T17:14:52Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Orchestration: design extraction with autogen-skip

- [x] T-000408: Compose design input set from four sources in `_subsequent_run` <!-- created_at: 2026-04-14T18:46:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000403: Per PIPELINE-design.md orchestration sketch "Compose the design extraction input set from FOUR sources". <!-- created_at: 2026-04-14T17:36:02Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000404: Sources: (1) `format_visual_references(spec)` paths; (2) accepted frames from videos with `visual-target` in roles via `accepted_frames_by_path.get(entry.path, [])`; (3) accepted frames from scraped `site_videos`; (4) `site_images` from `_download_site_media`. <!-- created_at: 2026-04-14T17:54:00Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000405: Apply frame-content-hash dedup per the design sketch: ref-declared frames (source 2) added to `seen_frame_hashes` first; scraped frames (source 3) added only if their content-hash is not already seen. <!-- created_at: 2026-04-14T18:12:15Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000406: `accepted_frames_by_path = _accepted_frames_by_source(filtered_results)` where `filtered_results` is the post-`apply_filter` list (use `dataclasses.replace(r, frames=apply_filter(filter_frames(r.frames)))` per the sketch). <!-- created_at: 2026-04-14T18:31:02Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000407: Tests: design_input contains all four sources when present; missing source gracefully omitted; frame-content-hash dedup verified with two videos containing identical frames at different paths; behavioral-only video does NOT contribute frames; `proposed: true` visual ref does NOT contribute. <!-- created_at: 2026-04-14T18:46:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000415: Wire SPEC.md write-back for autogen design with skip-when-present in `_subsequent_run` <!-- created_at: 2026-04-14T20:17:48Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000409: Per PIPELINE-design.md orchestration sketch "Check autogen block FIRST via the in-memory dataclass". <!-- created_at: 2026-04-14T19:13:27Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000410: Check `autogen_present = bool(spec.design.auto_generated.strip())` from the in-memory `spec` (NOT a re-read of SPEC.md, NOT a second regex pass). Per the design § "in-memory spec is source of truth within a single run" invariant. <!-- created_at: 2026-04-14T19:25:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000411: If `design_input` AND NOT `autogen_present`: call `extract_design(design_input)`, then read SPEC.md from disk, call `update_design_autogen(existing, format_design_block(design))`, write back if changed, then `save_design_requirements(dataclasses.asdict(design))` for the cache. <!-- created_at: 2026-04-14T19:32:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000412: If `design_input` AND `autogen_present`: skip extraction. Emit diagnostic via `record_failure("orchestrator:design_extraction", "io", f"Autogen design block exists in SPEC.md; skipped Vision extraction. Delete the BEGIN/END AUTO-GENERATED block to regenerate from {len(design_input)} input image(s).")`. <!-- created_at: 2026-04-14T19:49:25Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000413: Cache invariant per design § "Note on the autogen-cache divergence": when autogen is skipped, `save_design_requirements` is ALSO skipped — the cache stays consistent with SPEC.md autogen. <!-- created_at: 2026-04-14T20:03:11Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000414: Tests: autogen-absent triggers Vision call and write-back; autogen-present skips Vision call AND skips cache write; skip emits diagnostic naming the input count; SPEC.md write only happens when content differs (idempotency); in-memory `spec.design.auto_generated` consulted, not a re-read of SPEC.md from disk. <!-- created_at: 2026-04-14T20:17:48Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000420: [BATCH] Implement `format_design_block(design) -> str` in `duplo/design_extractor.py` <!-- created_at: 2026-04-14T20:23:03Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000416: Per PIPELINE-design.md § `format_design_block`. Wraps the existing `format_design_section(design)` in the same module, MINUS the section heading. <!-- created_at: 2026-04-14T20:23:03Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000417: **Lives in `design_extractor.py`, NOT `spec_drafter.py`** per the layering rationale (drafter must not depend on pipeline stages). <!-- created_at: 2026-04-14T20:23:03Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000418: The orchestrator imports `format_design_block` from `design_extractor` and passes the resulting string into `spec_drafter.update_design_autogen`. The drafter sees only a string. <!-- created_at: 2026-04-14T20:23:03Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000419: Tests: output equals `format_design_section(design)` minus the heading line; round-trip: `update_design_autogen(spec, format_design_block(design))` produces a spec where the parsed `spec.design.auto_generated` content reflects `design`'s fields. <!-- created_at: 2026-04-14T20:23:03Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Orchestration: full _subsequent_run restructure

- [x] T-000426: Restructure `_subsequent_run` to consume role-filtered inputs end-to-end <!-- created_at: 2026-04-14T22:37:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000421: This is the integration task that wires together everything from previous Phase 5 tasks. Follow the orchestration sketch in PIPELINE-design.md § `main.py orchestration` step by step. <!-- created_at: 2026-04-14T21:04:22Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000422: Order within the function: (1) `read_spec()`; (2) `validate_for_run(spec)` and exit on errors; (3) file-change detection (unchanged from today); (4) multi-source iteration loop with first-source-wins dedup; (5) save_examples / save_reference_urls / save_raw_content / save_doc_structures; (6) discovered-URLs SPEC.md write-back; (7) `extract_features` with merged scraped text and `_matches_excluded` post-filter; (8) `save_features`; (9) `_download_site_media(product_ref_raw_pages)` for site_images and site_videos; (10) behavioral-paths construction with collision assert; (11) `extract_all_videos` + filter + `_accepted_frames_by_source`; (12) compose design_input from four sources with frame-content-hash dedup; (13) check `autogen_present`, run Vision and write-back OR skip with diagnostic; (14) phase planning (unchanged from today). <!-- created_at: 2026-04-14T21:25:20Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000423: The in-memory `spec` from step 1 is the source of truth for ALL decisions in steps 2–13. SPEC.md is re-read ONLY in step 6 and step 13 (to stage writes), NOT to drive extraction. Per design § "in-memory spec is source of truth within a single run". <!-- created_at: 2026-04-14T21:45:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000424: `_first_run` is NOT touched in this phase per design § "`_first_run` removal is NOT part of Phase 3." That's Phase 7. <!-- created_at: 2026-04-14T21:58:55Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000425: Tests (integration-style, per design § "Test plan"): URL-only spec produces correct PLAN.md without consulting `ref/`; ref/-only spec produces correct PLAN.md without making any HTTP requests; both contribute to the plan; subsequent run with new files added to `ref/` produces `proposed: true` entries in SPEC.md and pipeline does NOT act on them; after user removes `proposed: true`, next run includes the files in pipeline stages. <!-- created_at: 2026-04-14T22:37:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Orchestration: _fix_mode update

- [x] T-000430: [BATCH] Update `_fix_mode` to use the new investigator with counter-examples and behavior contracts <!-- created_at: 2026-04-14T23:05:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000427: Per PIPELINE-design.md § `_fix_mode integration with new model`: "No structural change. The new investigator includes counter-examples and behavior contracts; existing `_fix_mode` tests should continue to pass with those added sources." <!-- created_at: 2026-04-14T23:05:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000428: Verify that `_fix_mode`'s call to `investigate(...)` passes the spec (or whatever context the investigator now needs to access counter-examples and behavior contracts via the formatters). <!-- created_at: 2026-04-14T23:05:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000429: Tests: existing `_fix_mode` tests still pass; new test that confirms counter-example references reach the investigator prompt when called from `_fix_mode`; new test for behavior contracts in `_fix_mode` context. <!-- created_at: 2026-04-14T23:05:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Multi-source persistence in duplo.json

- [x] T-000436: Add `sources` field to `.duplo/duplo.json` and update saver functions <!-- created_at: 2026-04-15T03:25:47Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000431: Per PIPELINE-design.md § "Multi-source persistence". `.duplo/duplo.json` gains a `sources` field: list of `{url, last_scraped, content_hash, scrape_depth_used}` entries, one per scrapeable source. <!-- created_at: 2026-04-14T23:37:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000432: Add `save_sources(sources_metadata)` and `load_sources()` functions to `duplo/saver.py`. Sources metadata accumulated during the iteration loop and persisted after. <!-- created_at: 2026-04-14T23:47:26Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000433: Backward compatibility per design: `.duplo/product.json` keeps the single `source_url` field, populated from the FIRST product-reference entry in `## Sources`. New code reads from the spec, not from `product.json`. The field is preserved only so old tooling and migration detection keep working. <!-- created_at: 2026-04-15T00:25:00Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000434: When user removes a URL from `## Sources`, the entry STAYS in `duplo.json` (idempotent state) but the pipeline doesn't re-scrape and doesn't include cached content in subsequent extractions. Per design. <!-- created_at: 2026-04-15T00:50:55Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000435: Tests: sources field populated correctly; existing `product.json:source_url` populated from first product-reference entry; removed source stays in `duplo.json` but is not re-scraped; `save_sources` is idempotent; multiple sources tracked independently. <!-- created_at: 2026-04-15T03:25:47Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Automated integration tests

All Phase 5 end-to-end behaviors are verified by automated pytest integration tests, not by manual user runs. Each test constructs a fixture project in a tmpdir, runs duplo's pipeline programmatically, and asserts on the output state. Tests must NOT make real HTTP requests — use `unittest.mock.patch` on `duplo.fetcher.fetch_site` (or a local HTTP fixture if mocking is awkward) so the suite is hermetic and fast. Vision/LLM calls must also be mocked so tests don't depend on `claude -p` availability or network. All tests live in `tests/test_phase5_integration.py` (new file).

The earlier USER verification block was authored incorrectly: every scenario in it is automatable and should never have been a manual task. The standing rule is: never ask the user to do what the system can do itself. USER tasks are reserved for genuine human-judgment cases (e.g. "does this look visually correct"). None of these scenarios meet that bar. They are rewritten below as automated integration tests that mcloop will execute.

- [x] T-000444: Add `tests/test_phase5_integration.py` with `test_url_only_spec_runs_end_to_end` <!-- created_at: 2026-04-15T04:58:05Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000437: Construct a tmpdir with a SPEC.md containing the marker comment, a `## Purpose` of >50 chars, a `## Architecture` block, and a `## Sources` block listing one entry with `role: product-reference` and `scrape: deep`. No `ref/` directory. <!-- created_at: 2026-04-15T03:38:01Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000438: Mock `duplo.fetcher.fetch_site` to return a fixture 5-tuple: a small scraped_text, empty code_examples, empty doc_structures, one PageRecord with the canonical URL, and a `raw_pages` dict mapping that URL to a small HTML fixture containing one `<a href>` to a same-origin path and one `<a href>` to a cross-origin path. <!-- created_at: 2026-04-15T03:51:14Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000439: Mock `duplo.design_extractor.extract_design` to return a deterministic DesignRequirements fixture. <!-- created_at: 2026-04-15T03:57:28Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000440: Mock `duplo.extractor.extract_features` to return a deterministic two-feature fixture. <!-- created_at: 2026-04-15T04:03:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000441: Mock `duplo.questioner.select_features` (or whatever interactive selector exists) to auto-select all features without prompting. <!-- created_at: 2026-04-15T04:11:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000442: Run duplo's `_subsequent_run` (or the top-level entry function) against the tmpdir. <!-- created_at: 2026-04-15T04:32:55Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000443: Assert: PLAN.md exists in tmpdir; `.duplo/raw_pages/` contains at least one `.html` file whose name is `sha256(canonical_url).hex` form; `.duplo/duplo.json` has the `sources` field populated with the URL; `.duplo/product.json` exists with `source_url` populated from the first product-reference; no `FileNotFoundError`, no diagnostic about missing `ref/` was recorded. <!-- created_at: 2026-04-15T04:58:05Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000452: Add `test_ref_only_spec_runs_without_http` <!-- created_at: 2026-04-15T06:32:05Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000445: Construct a tmpdir with a SPEC.md containing marker, Purpose, Architecture, NO `## Sources` (or empty `## Sources`), and a `## References` block listing two entries: one with `role: visual-target` and one with `role: docs`. Create `ref/` directory and place small fixture image and text files at the declared paths. <!-- created_at: 2026-04-15T05:10:10Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000446: Patch `duplo.fetcher.fetch_site` with a mock that raises if called — the test asserts no HTTP work happened. <!-- created_at: 2026-04-15T05:25:11Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000447: Mock `extract_design` to return deterministic output and assert it was called with the visual-target ref/ file paths in its `design_input`. <!-- created_at: 2026-04-15T05:41:53Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000448: Mock the docs-text path (`docs_text_extractor`) to return deterministic output and assert it was called with the docs ref/ file path. <!-- created_at: 2026-04-15T05:46:46Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000449: Mock `extract_features` and the interactive selectors as in the previous test. <!-- created_at: 2026-04-15T06:00:33Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000450: Run `_subsequent_run` against the tmpdir. <!-- created_at: 2026-04-15T06:11:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000451: Assert: PLAN.md produced; `fetch_site` mock recorded zero calls; `extract_design` was called with expected paths; no diagnostic about missing source URL. <!-- created_at: 2026-04-15T06:32:05Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000459: Add `test_url_and_ref_with_scope_exclude_drops_features` <!-- created_at: 2026-04-15T08:16:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000453: Construct a tmpdir with SPEC.md containing marker, Purpose, Architecture, one product-reference URL, one ref/ entry with `role: visual-target`, AND a `## Scope` block with `exclude: plugin API`. <!-- created_at: 2026-04-15T07:00:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000454: Mock `fetch_site` and `extract_design` deterministically. <!-- created_at: 2026-04-15T07:18:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000455: Mock `extract_features` to return three features whose names/descriptions are: (a) a clear match for `"plugin API"` as a whole phrase; (b) a non-match that contains the substring `"non-plugin-API"`; (c) an unrelated feature. <!-- created_at: 2026-04-15T07:36:51Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000456: Run `_subsequent_run` against the tmpdir. <!-- created_at: 2026-04-15T06:11:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000457: Assert: feature (a) was dropped — it does NOT appear in `.duplo/duplo.json` features list. Features (b) and (c) WERE kept (substring match must NOT trigger word-boundary regex). <!-- created_at: 2026-04-15T07:59:24Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000458: Assert: `duplo.diagnostics` recorded a `scope_exclude` diagnostic for feature (a) and only feature (a). <!-- created_at: 2026-04-15T08:16:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000464: Add `test_discovered_urls_appended_to_spec_and_not_rescraped_on_second_run` <!-- created_at: 2026-04-15T09:33:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000460: Construct a tmpdir with SPEC.md containing one product-reference URL with `scrape: deep`. Mock `fetch_site` to return a `raw_pages` dict whose HTML contains one cross-origin `<a href>` to a different host. <!-- created_at: 2026-04-15T08:23:15Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000461: Run `_subsequent_run` once. Assert: SPEC.md was modified; `## Sources` now has a new entry for the cross-origin URL with `discovered: true` flag; the cross-origin URL was NOT fetched. <!-- created_at: 2026-04-15T08:45:47Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000462: Run `_subsequent_run` a second time on the same tmpdir without modifying SPEC.md. Assert: the discovered entry is still present with the flag intact; the cross-origin URL was STILL not fetched. <!-- created_at: 2026-04-15T09:01:51Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000463: Programmatically edit SPEC.md to remove the `discovered: true` line from the discovered entry. Run `_subsequent_run` a third time. Assert: this time the previously-discovered URL WAS fetched. <!-- created_at: 2026-04-15T09:33:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000470: Add `test_autogen_design_block_present_skips_vision` <!-- created_at: 2026-04-15T11:01:55Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000465: Construct two tmpdirs sharing the same SPEC.md skeleton (URL-only, product-reference). Variant A: SPEC.md has `## Design` containing a populated AUTO-GENERATED block. Variant B: SPEC.md has `## Design` with no autogen block. <!-- created_at: 2026-04-15T09:55:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000466: Mock `extract_design` and assert call counts. <!-- created_at: 2026-04-15T10:09:30Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000467: Run `_subsequent_run` against Variant A. Assert: `extract_design` was NOT called; `duplo.diagnostics` recorded the autogen-skip message; SPEC.md was NOT modified by the run; `.duplo/duplo.json` has NO new `design_requirements`. <!-- created_at: 2026-04-15T10:29:07Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000468: Run `_subsequent_run` against Variant B. Assert: `extract_design` WAS called; SPEC.md was modified to add a populated AUTO-GENERATED block; `.duplo/duplo.json` `design_requirements` was populated. <!-- created_at: 2026-04-15T10:49:58Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000469: Modify Variant A's SPEC.md to delete the autogen block contents (leave block markers but empty body). Run `_subsequent_run` again. Assert: `extract_design` IS called this time; SPEC.md autogen block is now populated. <!-- created_at: 2026-04-15T11:01:55Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000477: Add `test_proposed_true_references_excluded_from_pipeline` <!-- created_at: 2026-04-15T12:53:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000471: Construct a tmpdir with SPEC.md containing two ref/ entries with the same `role: visual-target`: one with `proposed: true`, one without. Drop fixture image files for both. <!-- created_at: 2026-04-15T11:21:11Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000472: Mock `extract_design` and capture its `design_input` argument. <!-- created_at: 2026-04-15T11:33:24Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000473: Run `_subsequent_run` against the tmpdir. <!-- created_at: 2026-04-15T06:11:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000474: Assert: `extract_design` was called; its `design_input` contains the path of the non-proposed reference; its `design_input` does NOT contain the path of the proposed reference. <!-- created_at: 2026-04-15T12:19:11Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000475: Programmatically edit SPEC.md to remove `proposed: true` from the previously-proposed entry. Run `_subsequent_run` again. Assert: this time `extract_design` was called with both reference paths in `design_input`. <!-- created_at: 2026-04-15T12:29:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000476: Repeat the same pattern for a `behavioral-target` reference: assert that with `proposed: true`, `extract_all_videos` is NOT called for that path; without the flag, it IS called. <!-- created_at: 2026-04-15T12:53:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000484: Add `test_counter_example_reference_excluded_from_extraction_and_appears_in_investigator` <!-- created_at: 2026-04-16T04:32:13Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000478: Construct a tmpdir with SPEC.md containing one ref/ entry with `role: counter-example` and one ref/ entry with `role: visual-target`. <!-- created_at: 2026-04-15T13:14:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000479: Mock `extract_design` and `extract_features` and capture their inputs. <!-- created_at: 2026-04-15T13:32:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000480: Run `_subsequent_run`. Assert: `extract_design`'s `design_input` contains the visual-target path but NOT the counter-example path. The features list does NOT mention counter-example content. <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000481: Programmatically invoke `duplo fix "sample bug"`. Mock the investigator LLM call and capture the prompt. <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000482: Assert: the captured investigator prompt contains the counter-example reference's path or notes content, framed under an explicit "AVOID" label. <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000483: If the spec has counter-example SOURCES (URLs with `role: counter-example`), assert: the URL appears in the investigator prompt under the same AVOID framing, AND `fetch_site` was NOT called against that URL. <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000488: Run the full test suite and confirm Phase 5 closes cleanly <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000485: Execute `pytest -x` against the duplo repo. <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000486: Assert: all pre-existing tests still pass; all seven new Phase 5 integration tests pass. <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000487: If any test fails, the task fails and mcloop will retry. The retry should investigate the failure (read pytest output, identify the failing assertion, locate the responsible code, fix it). Phase 5 is not complete until this task passes. <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Followup: bugs surfaced during the manual run of the URL-only scenario

The manual run of the URL-only scenario (against numi.app) before this rewrite surfaced real bugs. Queued here so they don't get lost.

- [x] T-000492: Fix planner output wrapped in markdown code fences <!-- created_at: 2026-04-16T05:35:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000489: When duplo's planner generates a PLAN.md via `claude -p` and the LLM returns the markdown wrapped in triple-backtick fences, duplo writes the wrapped text verbatim. The resulting file is unparseable by mcloop because the H1 heading is buried inside a code fence. <!-- created_at: 2026-04-16T04:40:57Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000490: Fix: in `duplo/planner.py` (or wherever the planner output is written), strip leading/trailing fences before writing. Use the existing `strip_fences` helper from `duplo/parsing.py` if it covers this case; if not, extend it. <!-- created_at: 2026-04-16T04:41:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000491: Tests: planner output containing a fenced markdown block is written without the fences. Output without fences is written unchanged. Output with `~~~` fences is also handled. <!-- created_at: 2026-04-16T05:35:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000497: Fix planner placing feature tasks under `## Bugs` heading <!-- created_at: 2026-04-16T06:39:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000493: The Phase 1 PLAN.md generated during the manual run had its feature implementation tasks placed UNDER the `## Bugs` heading instead of as the plan body. `## Bugs` should be empty initially. Feature tasks should be at the top level under the phase H1 heading. <!-- created_at: 2026-04-16T05:55:47Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000494: Investigate `duplo/planner.py` (or saver.py's `save_plan`) to find where the structure is being assembled wrong. <!-- created_at: 2026-04-16T05:57:37Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000495: Fix: ensure the planner's output has the correct structure: H1 phase heading, then feature tasks at top level, then `## Bugs` heading at the end with no tasks below it. <!-- created_at: 2026-04-16T06:18:41Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000496: Tests: a generated PLAN.md has feature tasks at top level under the H1 heading. The `## Bugs` heading is present but contains no tasks. Mcloop's parser correctly identifies the feature tasks as Phase 1 work, not as bugs. <!-- created_at: 2026-04-16T06:39:06Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000502: Fix `derive_app_name` not writing `product_name` to product.json <!-- created_at: 2026-04-16T07:21:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000498: During the manual run, the PLAN.md heading correctly read `# numi — Phase 1: Scaffold` (so the app name was derived as "numi" somewhere), but `.duplo/product.json` had `product_name: ""` (empty string). <!-- created_at: 2026-04-16T06:53:08Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000499: Investigate `duplo/orchestrator.py:derive_app_name` and its callers. Either the function isn't writing `product_name` (only writing `app_name`), or `product.json` is initialized with empty `product_name` and never updated. <!-- created_at: 2026-04-16T07:03:49Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000500: Fix: ensure `product.json` ends up with `product_name` populated to the same value used in the PLAN.md heading. Backward-compat: if `product.json` already has a non-empty `product_name` (user-edited), do not overwrite. <!-- created_at: 2026-04-16T07:15:36Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000501: Tests: after `_subsequent_run`, `product.json:product_name` matches the value used in PLAN.md's H1 heading. User-edited `product_name` survives a subsequent run. <!-- created_at: 2026-04-16T07:21:19Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000507: Fix `frame_describer` parse-error on every video frame <!-- created_at: 2026-04-16T07:49:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000503: During the manual run, all 17 video frames extracted from a demo video were stored with `state: "unknown"`, `detail: "parse error"` in `.duplo/duplo.json` `frame_descriptions`. The frame describer's LLM call is returning output that the parser cannot handle. Pre-existing behavior, not caused by Phase 5, but surfaced clearly during Phase 5 verification. <!-- created_at: 2026-04-16T07:25:44Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000504: Investigate `duplo/frame_describer.py`. Capture a real LLM response sample and inspect what the parser is choking on. <!-- created_at: 2026-04-16T07:41:32Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000505: Fix: make the parser tolerant of common LLM output variations: strip code fences, strip leading/trailing prose, parse the first valid JSON object found. Alternatively, tighten the prompt to demand strict JSON output. <!-- created_at: 2026-04-16T07:46:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000506: Tests: parser handles JSON wrapped in fences; parser handles JSON preceded by prose; parser handles JSON with trailing whitespace; parser returns a useful error message when the LLM truly returned something unparseable. <!-- created_at: 2026-04-16T07:49:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000512: Investigate why AUTO-GENERATED design block was not written to SPEC.md during the manual URL-only run <!-- created_at: 2026-04-16T21:36:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000508: During the manual run, design extraction appears to have completed (no error logged), but SPEC.md ended the run with no `## Design` section and no AUTO-GENERATED block. <!-- created_at: 2026-04-16T07:53:31Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000509: Investigate: in `_subsequent_run`, after `extract_design` is called, what happens with the result? Is `format_design_block(design)` producing a non-empty string? Is `update_design_autogen` being invoked? Is its result being written back? Add a diagnostic at each step if the chain breaks. <!-- created_at: 2026-04-16T07:59:16Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000510: Fix: ensure the write-back happens reliably whenever `extract_design` returns a non-empty result. The `test_autogen_design_block_present_skips_vision` test will catch this regression once the bug is fixed. <!-- created_at: 2026-04-16T08:09:38Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000511: Note: this bug may be entangled with the frame_describer bug — if the design extractor receives only "unknown" frame descriptions, it may produce empty or trivial output that legitimately doesn't merit a write-back. Investigate the relationship. <!-- created_at: 2026-04-16T21:36:04Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

---

## Phase 6: Drafter and duplo init
<!-- phase_id: phase_006 -->

Adds duplo init and the full spec-drafter that creates SPEC.md entries from URL scrapes, prose descriptions, and existing reference files (via Vision). Updates the migration message from Phase 4 to reference duplo init.

Design references: design/DRAFTER-design.md (authoritative for spec_writer.py extensions), design/INIT-design.md (authoritative for duplo init UX and behavior). When a task description and the design doc disagree, the design doc wins; flag the discrepancy for resolution rather than silently picking one interpretation.

The module the design docs call spec_drafter.py is implemented as duplo/spec_writer.py. Phase 5 already shipped append_sources and update_design_autogen there. This phase adds the remaining drafter functions: format_spec, append_references, _draft_from_inputs, draft_spec, and the role-inference helpers.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all code generation. Ruff for linting, pytest for tests. All AI calls go through claude -p (no direct API calls).

## Drafter: DraftInputs and format_spec

- [x] T-000515: [BATCH] Add DraftInputs dataclass to duplo/spec_writer.py <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000513: Add DraftInputs dataclass with fields: url (str or None), url_scrape (str or None), description (str or None), existing_ref_files (list[Path], default empty), vision_proposals (dict[Path, str], default empty). Per design/DRAFTER-design.md section DraftInputs. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000514: Tests: dataclass construction with all fields; default values for optional fields; field types enforced. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000528: Implement format_spec(spec: ProductSpec) -> str in duplo/spec_writer.py <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000516: Serialize a ProductSpec to SPEC.md format. The inverse of the parser. Per design/DRAFTER-design.md section format_spec. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000517: Start with the standard top-matter comment block (the same block from SPEC-template.md, including the "How the pieces fit together:" marker string). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000518: Render sections in canonical order: Purpose, Sources, References, Architecture, Design, Scope, Behavior, Notes. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000519: For empty required sections (Purpose, Architecture): write the FILL IN marker from the template. <!-- created_at: 2026-04-17T06:24:40Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000520: For empty optional sections (Design, Scope, Behavior, Notes): write just the heading and the comment hint from the template. No FILL IN marker. <!-- created_at: 2026-04-17T06:37:24Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000521: For filled sections: write heading and content. Omit comment hints when content is present. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000522: Sources entries: serialize each SourceEntry with one blank line between entries, using the same format as _format_entry. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000523: References entries: serialize each ReferenceEntry with one blank line between entries, including roles (comma-separated), notes, and proposed flag. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000524: Design section: if DesignBlock has user_prose, write it first. If auto_generated is present, write the AUTO-GENERATED block after user_prose using the same markers the parser expects. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000525: Scope section: serialize scope_include and scope_exclude lists in the template format (include:/exclude: with indented list items). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000526: Behavior section: serialize behavior_contracts as input/output pairs in the template format. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000527: Tests: empty ProductSpec produces template-like output with FILL IN markers on required sections; fully populated ProductSpec serializes all sections; Sources and References entries formatted correctly with flags; Design section with user_prose and auto_generated renders both in order; Scope include/exclude rendered; Behavior contracts rendered; empty optional sections get heading and comment only. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000534: Implement round-trip property test for format_spec <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000529: Per design/DRAFTER-design.md section "Round-trip testing". Property: parse(format_spec(spec)) equals spec for all surviving fields. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000530: Implement _spec_equal_for_round_trip comparator that excludes raw, dropped_sources, and dropped_references fields per design/DRAFTER-design.md. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000531: Use hand-rolled fixture generation (not Hypothesis) to cover: empty spec, spec with all sections filled, spec with mixed filled/empty sections, spec with Sources and References containing proposed/discovered flags, spec with DesignBlock containing both user_prose and auto_generated, spec with scope_include and scope_exclude, spec with behavior_contracts. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000532: Add a separate test pinning that dropped_sources and dropped_references round-trip as empty lists (documenting the asymmetry per design/DRAFTER-design.md). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000533: Tests: each fixture round-trips; dropped fields excluded from comparison; round-tripped spec has empty dropped lists. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Drafter: append_references

- [x] T-000542: [BATCH] Implement append_references(existing: str, new_entries: list[ReferenceEntry]) -> str in duplo/spec_writer.py <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000535: Same pattern as append_sources but for the References section. Per design/DRAFTER-design.md section append_references. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000536: Deduplication is path-only: two entries with the same path (after normalization) are duplicates regardless of role. First-write-wins. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000537: Path normalization: compare paths as-is (no resolve, no symlink following). Paths are always relative to project root and start with ref/. Comparison is string equality after stripping any trailing slash. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000538: If References section does not exist, create it after Sources (if present), else after Purpose (if present), else at end of file. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000539: Serialize each new entry with roles (comma-separated), notes, and proposed flag. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000540: Side-effect-free: takes existing content as string, returns modified string. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000541: Tests: append single new entry; append multiple; dedup against existing path (entry not added); dedup is path-only (same path with different role still deduplicates); idempotent (double-call returns same result); empty new_entries returns input unchanged; missing References section is created; proposed flag written correctly; multiple roles serialized as comma-separated; entry with notes serialized correctly. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Drafter: role inference helpers

- [x] T-000548: [BATCH] Implement URL role inference heuristics in duplo/spec_writer.py <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000543: Per design/DRAFTER-design.md section "Inferring URL roles". Regex-based, not LLM-based. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000544: Add _infer_url_role(context: str) -> str function. Takes the surrounding prose context where a URL was mentioned. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000545: Rules: "like X" / "such as X" / "inspired by X" returns product-reference. "see also X" / "X for reference" returns docs. "not like X" / "unlike X" / "avoid X" returns counter-example. Default: product-reference. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000546: Case-insensitive matching. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000547: Tests: each pattern produces the expected role; default when no pattern matches; case-insensitive; multiple patterns in same context uses the first match. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000555: [BATCH] Implement Vision-based file role inference in duplo/spec_writer.py <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000549: Per design/DRAFTER-design.md section "Inferring file roles via Vision". Add _propose_file_role(path: Path) -> tuple[str, str] returning (description, role). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000550: For image files (.png, .jpg, .gif, .webp): call claude -p with the Vision prompt from design/DRAFTER-design.md that asks for description and role from the enum (visual-target, behavioral-target, docs, counter-example, ignore). Parse JSON response. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000551: For non-image files: use extension-based defaults. PDFs default to docs. Text/markdown files default to docs. Videos (.mp4, .mov, .webm, .avi) default to behavioral-target. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000552: All results are proposals (proposed: true is set by the caller, not by this function). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000553: Retry logic: two retry attempts with backoff on LLM failure, then fall back to ignore role with a diagnostic. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000554: Tests: image file triggers claude -p Vision call (mocked) and parses JSON response; PDF defaults to docs without Vision call; text file defaults to docs; video defaults to behavioral-target; unknown extension defaults to ignore with diagnostic; LLM failure after retries falls back to ignore; JSON parse error falls back to ignore with diagnostic. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Drafter: _draft_from_inputs and draft_spec

- [x] T-000564: Implement _draft_from_inputs(inputs: DraftInputs) -> ProductSpec in duplo/spec_writer.py <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000556: Per design/DRAFTER-design.md section "Drafting from inputs". The only LLM call in the drafter. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000557: Build structured-output prompt for claude -p per design/DRAFTER-design.md: request JSON with fields purpose, architecture, design, behavior_contracts, scope_include, scope_exclude. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000558: Architecture is filled ONLY when description prose explicitly states a stack/platform/language. URL scrapes do NOT inform architecture. Per design/DRAFTER-design.md and design/INIT-design.md. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000559: notes is deliberately NOT in the LLM schema (populated by draft_spec from raw description prose). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000560: Parse JSON response. Strip code fences before parsing (reuse strip_fences from duplo/parsing.py). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000561: Construct ProductSpec with: filled fields from JSON (when not null/empty); FILL IN markers for required fields the LLM returned null for; empty content for optional fields the LLM returned null for. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000562: Retry logic: two retry attempts with backoff on LLM failure or JSON parse error, then fall back to empty ProductSpec (template-only draft) with a diagnostic per design/DRAFTER-design.md section "Error handling". <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000563: Tests (all with mocked claude -p): URL-only input produces purpose from scrape, architecture null; prose-only input produces purpose and architecture when prose states a stack; prose that does not state a stack produces architecture null; both URL and prose merges them (prose wins on conflicts per design/INIT-design.md); neither URL nor prose produces empty ProductSpec; LLM returns malformed JSON triggers retry then fallback; LLM returns null for all fields produces template-like spec. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000573: Implement draft_spec(inputs: DraftInputs) -> str in duplo/spec_writer.py <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000565: Per design/DRAFTER-design.md section draft_spec. Orchestrates _draft_from_inputs and format_spec. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000566: Step 1: call _draft_from_inputs(inputs) to get a ProductSpec. <!-- created_at: 2026-04-17T07:57:20Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000567: Step 2: if inputs.description was provided, copy the original prose verbatim into spec.notes under a labeled header per design/DRAFTER-design.md: "Original description provided to duplo init:" followed by the verbatim prose. The LLM does NOT write notes. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000568: Step 3: add SourceEntry for the URL (if any) with role product-reference and scrape deep. No proposed/discovered flag (user provided the URL explicitly). <!-- created_at: 2026-04-17T08:02:18Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000569: Step 4: add ReferenceEntry for each existing ref/ file with proposed: true and the role from inputs.vision_proposals. <!-- created_at: 2026-04-17T08:05:45Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000570: Step 5: call format_spec(spec) to serialize. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000571: Step 6: return the resulting string. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000572: Tests: URL-only inputs produce SPEC.md with Sources entry and pre-filled Purpose; prose-only inputs produce SPEC.md with Notes containing verbatim prose; both inputs produce merged SPEC.md; existing ref/ files produce References entries with proposed: true; vision_proposals roles appear on the entries; format_spec output passes parser round-trip. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Drafter: error handling

- [x] T-000579: [BATCH] Add drafter exception classes to duplo/spec_writer.py <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000574: Per design/DRAFTER-design.md section "Error handling". Add SectionNotFound(name: str), MalformedSpec(reason: str), DraftingFailed(reason: str) exception classes. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000575: SectionNotFound: raised by append/update functions when the target section is not in the file. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000576: MalformedSpec: raised when parse-during-modify fails because the existing file is not valid SPEC.md format. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000577: DraftingFailed: raised when the LLM call in _draft_from_inputs fails after retries. Caller falls back to template-only draft. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000578: Tests: each exception class can be instantiated and carries its message; draft_spec catches DraftingFailed and falls back to template-only output. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Edit-safety property test

- [x] T-000584: Add edit-safety property test per design/DRAFTER-design.md section "Round-trip testing" <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000580: Property: for any well-formed ProductSpec and any new SourceEntry, append_sources(format_spec(spec), [new_entry]) produces a spec where every field other than sources is unchanged after re-parsing. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000581: Same property for append_references with ReferenceEntry. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000582: Same property for update_design_autogen: all fields other than design.auto_generated unchanged. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000583: Tests: each property exercised with multiple fixture combinations; unrecognized/custom sections preserved byte-for-byte through modify operations. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## duplo init: argument parsing

- [x] T-000593: [BATCH] Add init subcommand to argument parser in duplo/main.py <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000585: Per design/INIT-design.md section "Command surface". Add init as a recognized subcommand alongside fix and investigate. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000586: Positional argument: url (optional). Validated as starting with http:// or https://. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000587: Flag: --from-description PATH (or - for stdin). Path to a text file containing prose description. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000588: Flag: --deep (boolean, default false). Opt-in to deep scraping during init. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000589: Flag: --force (boolean, default false). Overwrite existing SPEC.md. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000590: Dispatch to duplo.init.run_init(args) when subcommand is init. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000591: init subcommand bypasses migration check (same as fix and investigate). Per design/MIGRATION-design.md: migration check applies only to the no-subcommand path. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000592: Tests: argparse accepts duplo init with no args; accepts duplo init URL; accepts duplo init --from-description FILE; accepts duplo init URL --from-description FILE; accepts --deep and --force flags; rejects invalid URL (not http/https); init dispatches to run_init (mock and assert called). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## duplo init: core implementation

- [x] T-000596: Create duplo/init.py with run_init(args) entry point <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000594: Per design/INIT-design.md section "Implementation shape". New module with a single run_init entry point. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000595: Dependencies: duplo.spec_writer (for draft_spec, format_spec), duplo.fetcher (for fetch_site with scrape_depth), duplo.validator (for validate_product_url), duplo.scanner (for scan_directory on ref/). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000605: Implement run_init for the no-arguments case <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000597: Per design/INIT-design.md section "duplo init (no arguments)". <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000598: Check for existing SPEC.md: if present and --force not set, print error message per design/INIT-design.md and exit 1. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000599: Create ref/ directory if it does not exist. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000600: Write ref/README.md with the static content from design/INIT-design.md section "ref/README.md content". Write-once: do not overwrite if ref/README.md already exists. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000601: Write SPEC.md with the static SPEC-template.md content (via format_spec on an empty ProductSpec). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000602: Print the output message per design/INIT-design.md: "Created ref/...", "Wrote SPEC.md (template, no inputs).", and the "Next steps:" block. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000603: Exit 0. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000604: Tests: SPEC.md written with template content; ref/ created; ref/README.md written; existing SPEC.md without --force exits 1 with error message; existing SPEC.md with --force overwrites; existing ref/ not recreated; existing ref/README.md not overwritten; output messages match design/INIT-design.md. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000616: Implement run_init for the URL-only case <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000606: Per design/INIT-design.md section "duplo init URL". <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000607: Canonicalize URL via url_canon.canonicalize_url before any use (per design/INIT-design.md error cases). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000608: Call fetch_site(url, scrape_depth="shallow") for product identity. If --deep flag set, use scrape_depth="deep" instead. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000609: On fetch success: extract product identity from scraped content. Build DraftInputs with url and url_scrape populated. Call draft_spec(inputs). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000610: On fetch failure (network error, NXDOMAIN, timeout): per design/INIT-design.md section "URL fetch fails", continue with template-only setup. Write URL to Sources with scrape: none. Print failure message. Exit 0 (not 1). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000611: On fetch success but no product identified: per design/INIT-design.md section "URL fetch succeeds but identifies nothing", pre-fill Sources only. Leave Purpose as FILL IN. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000612: Scan existing ref/ files (if ref/ exists and has files): call _propose_file_role for each image, use extension defaults for non-images. Populate DraftInputs.vision_proposals. Per design/INIT-design.md section "ref/ already exists with files". <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000613: Write SPEC.md from draft_spec output. Create ref/ and ref/README.md as in the no-arguments case. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000614: Print output per design/INIT-design.md (shallow scrape message, product identity, pre-filled sections, next steps). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000615: Tests (all with mocked fetch_site and claude -p): successful scrape produces pre-filled Purpose and Sources; failed scrape writes URL with scrape: none and exits 0; unidentified product fills Sources only; existing ref/ files get role proposals with proposed: true; --deep flag passes scrape_depth="deep" to fetch_site; --force overwrites existing SPEC.md; URL canonicalized before writing to Sources. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000625: Implement run_init for the --from-description case <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000617: Per design/INIT-design.md section "duplo init --from-description description.txt". <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000618: Read description from file path or stdin (- argument). If file not found, print error per design/INIT-design.md and exit 1. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000619: If stdin: print "Reading description from stdin. Press Ctrl-D when done." when stdin is a TTY. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000620: Build DraftInputs with description populated. Call draft_spec(inputs). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000621: Per design/DRAFTER-design.md: if prose mentions a URL, extract it and add to Sources with proposed: true and role inferred via _infer_url_role. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000622: Write SPEC.md, create ref/, write ref/README.md. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000623: Print output per design/INIT-design.md (character count, pre-filled sections, next steps). <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000624: Tests: description from file read correctly; description from stdin (mocked) read correctly; file not found exits 1; URL extracted from prose added to Sources with proposed: true; inferred role correct; Notes section contains verbatim prose. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000631: Implement run_init for the combined URL + --from-description case <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000626: Per design/INIT-design.md section "duplo init URL --from-description description.txt". <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000627: Build DraftInputs with both url/url_scrape and description populated. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000628: Prose wins on conflicts per design/INIT-design.md. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000629: Both error conditions checked: invalid URL and missing description file. Both errors reported if both fail per design/INIT-design.md. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000630: Tests: combined inputs produce merged SPEC.md; prose-stated architecture overrides (URL-only would leave it as FILL IN); both errors reported simultaneously when both inputs are bad. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## duplo init: output discipline

- [x] T-000638: [BATCH] Ensure all init output follows design/INIT-design.md section "Output discipline" <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000632: Present-tense or simple-past for actions: "Fetched X.", "Pre-filled Y.", "Created Z." <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000633: Indented bullets with arrow for sub-results: "  -> Identified product: Numi" <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000634: "Next steps" sections with numbered items at the end of successful runs. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000635: Errors to stderr, successful output to stdout. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000636: No emoji, no color codes. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000637: Tests: capture stdout/stderr and assert formatting rules hold for each input combination. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Migration message update

- [x] T-000643: Update migration message in duplo/migration.py to reference duplo init <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000639: Per REDESIGN-overview.md section "Implementation phasing" Phase 4: "Update the migration message from Phase 2 to reference duplo init (one-line change)." <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000640: Replace step 3 in _MIGRATION_MESSAGE: change "Author a SPEC.md by hand. Use SPEC-template.md..." to "Run duplo init to generate a SPEC.md. Or author one by hand using SPEC-template.md..." <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000641: Add duplo init as the recommended path. Keep the manual-authoring option as an alternative. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000642: Tests: update the migration-message snapshot test from Phase 4 to match the new wording. Pin the exact new message content. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Automated integration tests

All Phase 6 end-to-end behaviors are verified by automated pytest integration tests, not by manual user runs. Each test constructs a fixture in a tmpdir, runs duplo init programmatically, and asserts on the output state. LLM calls must be mocked so tests do not depend on claude -p availability or network. All tests live in tests/test_phase6_integration.py (new file).

- [x] T-000646: Add tests/test_phase6_integration.py with test_init_no_args_produces_template <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000644: Run run_init with no URL, no description in a tmpdir. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000645: Assert: SPEC.md exists and contains the marker string "How the pieces fit together:"; SPEC.md contains FILL IN markers for Purpose and Architecture; ref/ directory exists; ref/README.md exists and matches design/INIT-design.md content; needs_migration returns False for this directory. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000650: Add test_init_url_produces_prefilled_spec <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000647: Mock fetch_site to return a fixture scrape with identifiable product name. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000648: Run run_init with a URL argument. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000649: Assert: SPEC.md has pre-filled Purpose (non-empty, no FILL IN); Sources section contains the URL with role: product-reference and scrape: deep; Architecture still has FILL IN; SPEC.md round-trips through the parser without errors. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000654: Add test_init_description_produces_notes_with_verbatim_prose <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000651: Write a description.txt fixture. Mock the LLM call in _draft_from_inputs. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000652: Run run_init with --from-description pointing to the fixture. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000653: Assert: SPEC.md Notes section contains "Original description provided to duplo init:" followed by the exact prose from description.txt byte-for-byte; Purpose section populated from LLM output. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000658: Add test_init_with_existing_ref_files_proposes_roles <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000655: Create a tmpdir with ref/ containing a .png and a .pdf. Mock _propose_file_role for the image. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000656: Run run_init. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000657: Assert: SPEC.md References section contains entries for both files; both have proposed: true; image has Vision-inferred role; PDF has role: docs (extension default). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000662: Add test_init_url_fetch_failure_writes_scrape_none <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000659: Mock fetch_site to raise an exception (network error). <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000660: Run run_init with a URL argument. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000661: Assert: exit code 0 (not 1); SPEC.md written; Sources contains the URL with scrape: none; Purpose has FILL IN marker. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000666: Add test_init_force_overwrites_existing_spec <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000663: Create a tmpdir with an existing SPEC.md containing custom content. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000664: Run run_init with --force. Assert: SPEC.md overwritten with new content. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000665: Run run_init without --force. Assert: exits 1 with error message; SPEC.md unchanged. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000670: Add test_init_then_duplo_run_works_end_to_end <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000667: Run run_init with a URL to produce SPEC.md. Then programmatically edit SPEC.md to fill in Architecture (remove FILL IN). Then run _subsequent_run against the same tmpdir. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000668: Mock fetch_site (for the deep scrape), extract_features, and interactive selectors. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000669: Assert: PLAN.md produced; no migration message printed; pipeline consumed SPEC.md correctly. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000674: Run the full test suite and confirm Phase 6 closes cleanly <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000671: Execute pytest -x against the duplo repo. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000672: Assert: all pre-existing tests still pass; all new Phase 6 tests pass. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000673: If any test fails, the task fails and mcloop will retry. <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

---

## Phase 7: Cleanup
<!-- phase_id: phase_007 -->

Removes the legacy code paths that the new SPEC-driven flow has superseded. Updates documentation. No new functionality. Per REDESIGN-overview.md: "only the new model is supported; code is clean."

Design reference: REDESIGN-overview.md section "Implementation phasing" Phase 5 (cleanup). Also informed by PIPELINE-design.md (which identifies legacy paths to remove) and the "What stays the same" section of REDESIGN-overview.md (which identifies what must NOT be touched).

This phase is entirely about deletion, simplification, and documentation. No new features. Every removal must be preceded by a caller audit to confirm no live code path depends on the removed code.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all code generation. Ruff for linting, pytest for tests.

## _first_run removal

- [x] T-000678: Audit all callers of _first_run in duplo/main.py <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000675: Grep the codebase for references to _first_run. Identify every call site. The only known call is in main() dispatch: when .duplo/duplo.json does not exist the code calls _first_run(url=args.url). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000676: Confirm that duplo init fully replaces _first_run for new projects. _first_run handled URL input, interactive feature selection, and first PLAN.md generation. duplo init handles URL input and SPEC.md generation; _subsequent_run handles feature extraction and PLAN.md generation from SPEC.md. <!-- created_at: 2026-04-17T20:32:51Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000677: Confirm that the migration gate prevents any old-format project from reaching _first_run. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000685: Remove _first_run and its helper functions from duplo/main.py <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000679: Delete the _first_run function. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000680: Delete _confirm_product (interactive product confirmation prompt, only called by _first_run). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000681: Delete _validate_url (interactive URL disambiguation prompt, only called by _first_run). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000682: Delete _init_project (saves selections, generates tests, writes CLAUDE.md, builds roadmap for first run, only called by _first_run). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000683: For each deletion, audit callers first. If any live code path calls the function, do not delete it. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000684: Tests: no test references _first_run, _confirm_product, _validate_url, or _init_project. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000689: Update the no-subcommand dispatch in main() to remove the _first_run branch <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000686: After migration check passes: if SPEC.md exists, go to _subsequent_run. If SPEC.md does not exist and .duplo/duplo.json does not exist (fresh directory, not an old project), print a message directing the user to run duplo init and exit 0. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000687: Remove the import of ask_preferences from duplo.questioner in main.py (only used by _first_run). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000688: Tests: duplo in a fresh directory (no .duplo/, no SPEC.md) prints "run duplo init" message and exits 0; duplo in a directory with SPEC.md proceeds to _subsequent_run. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## BuildPreferences migration

- [x] T-000694: Move BuildPreferences dataclass from duplo/questioner.py to duplo/build_prefs.py <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000690: The BuildPreferences dataclass is used by _prefs_from_dict and _load_preferences in main.py, both called from _subsequent_run (live code). It must NOT be deleted with questioner.py. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000691: Move the dataclass definition to build_prefs.py where parse_build_preferences already lives. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000692: Update all imports across the codebase: main.py, build_prefs.py, any test files that reference BuildPreferences. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000693: Tests: all existing tests that use BuildPreferences still pass; no import of BuildPreferences from questioner remains. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## questioner.py removal

- [x] T-000699: Delete duplo/questioner.py after BuildPreferences migration <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000695: After BuildPreferences is moved to build_prefs.py and _first_run is deleted, audit all remaining imports of questioner across the codebase. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000696: ask_preferences should have no callers. select_features in the next-phase flow is imported from duplo.selector, not duplo.questioner. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000697: Delete duplo/questioner.py and tests/test_questioner.py. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000698: Tests: no remaining imports of duplo.questioner anywhere in the codebase; pytest -x passes. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## initializer.py removal

- [x] T-000705: Evaluate duplo/initializer.py for removal <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000700: Audit all callers of initializer.create_project_dir and initializer.project_name_from_url. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000701: _first_run used initializer to create a target project directory and git init it. Under the new model, the user creates their own project directory and runs duplo init. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000702: If project_name_from_url is used by derive_app_name (in saver.py) or another live path, keep only that function and delete the rest. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000703: If no remaining callers exist after _first_run removal: delete duplo/initializer.py and tests/test_initializer.py. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000704: Tests: no remaining imports of deleted functions. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## _rescrape_product_url legacy fallback

- [x] T-000711: Remove _rescrape_product_url from duplo/main.py <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000706: _subsequent_run has two branches for scraping: if spec has scrapeable sources, use _scrape_declared_sources; otherwise fall back to _rescrape_product_url which reads source_url from duplo.json directly. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000707: After _first_run removal, no new project can write source_url to duplo.json without SPEC.md. The only projects hitting _rescrape_product_url are pre-migration projects, but those are blocked by the migration gate. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000708: Confirm that every path reaching _subsequent_run has a SPEC.md (migration gate guarantees this). If confirmed, delete _rescrape_product_url and its else branch in _subsequent_run. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000709: If a path can still reach _subsequent_run without SPEC.md (e.g. .duplo/duplo.json exists but SPEC.md was deleted post-migration), keep the fallback and add a diagnostic instead. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000710: Tests: _subsequent_run with a valid SPEC.md never calls _rescrape_product_url; _subsequent_run without SPEC.md either errors or uses the fallback (whichever behavior is chosen). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Design-data redundancy simplification

- [x] T-000716: Simplify _detect_and_append_gaps to read design data from SPEC.md only <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000712: _detect_and_append_gaps currently merges design data from both duplo.json design_requirements AND SPEC.md AUTO-GENERATED block via _merge_design_dicts. Source code comment says "redundant during transition; can simplify in Phase 7." <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000713: After the new model is the only path, SPEC.md AUTO-GENERATED block is the canonical source. Remove the duplo.json design_requirements merge and read only from spec.design.auto_generated. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000714: Remove the _merge_design_dicts call at the gap-detection site. If _merge_design_dicts has no other callers, delete it from gap_detector.py. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000715: Tests: gap detection produces the same results when design data is only in SPEC.md; no regression in existing gap-detection tests. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Scanner heuristics removal

- [x] T-000720: Remove file-relevance scoring from duplo/scanner.py <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000717: Phase 5 changed scan_directory to point at ref/ and drop relevance heuristics. Confirm that no legacy scoring code remains (image dimension checks, file size thresholds, etc.). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000718: If any legacy scoring functions or constants remain in scanner.py that are no longer called, delete them. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000719: Tests: no reference to removed scoring functions; scan_directory works purely on ref/ file inventory. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## URL-in-text-file scanning removal

- [x] T-000725: Remove URL extraction from arbitrary text files in the project directory <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000721: Under the old model, duplo scanned the project root for text files containing URLs and used them as scrape targets. Under the new model, URLs live exclusively in SPEC.md Sources section. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000722: Audit main.py and any other module for code that scans the project root for text files containing URLs. Remove that code. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000723: Confirm that _subsequent_run reads URLs only from scrapeable_sources(spec), not from file scanning. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000724: Tests: placing a text file containing a URL in the project root does NOT cause duplo to scrape that URL; URLs come only from SPEC.md. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Compatibility layer removal

- [x] T-000729: Remove any compatibility shims for spec.references and spec.design string access <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000726: Phase 3 changed spec.references from str to list of ReferenceEntry and spec.design from str to DesignBlock. If any compatibility properties or helper methods were added to ProductSpec to support old-style string access, remove them now. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000727: Grep for any call site that accesses spec.references as a string or spec.design as a string. If found, update to use the structured types. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000728: Tests: no string-access patterns remain; all callers use list of ReferenceEntry and DesignBlock. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Dead code audit

- [x] T-000733: Audit duplo/saver.py for dead code <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000730: Identify functions and constants that were only used by _first_run: save_selections, save_screenshot_feature_map, move_references, write_claude_md, and any others that have no remaining callers after _first_run removal. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000731: For each candidate: grep the codebase for all call sites. If only called from _first_run or deleted test code, delete it. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000732: Tests: pytest -x passes after deletions. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000737: Audit duplo/screenshotter.py and duplo/comparator.py for dead code <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000734: map_screenshots_to_features and save_reference_screenshots in screenshotter.py were used by _init_project. Check for remaining callers. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000735: compare_screenshots in comparator.py is used by _compare_with_references in main.py. _compare_with_references is called from _complete_phase which is live code, so comparator.py likely stays. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000736: Tests: pytest -x passes after deletions. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000744: [BATCH] Audit remaining modules for dead code <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000738: Check duplo/fetcher.py for functions only used by URL-in-text-file scanning. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000739: Check duplo/extractor.py for functions only used by the old first-run feature selection flow. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000740: Check duplo/collector.py for functions with no remaining callers. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000741: Check duplo/notifier.py, duplo/issuer.py, duplo/doc_tables.py for dead exports. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000742: For each dead function: delete it and remove its import from any __init__.py or other module. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000743: Tests: pytest -x passes after all deletions. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Documentation updates

- [x] T-000750: Update README.md to reflect the new project setup flow <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000745: Remove any references to the old implicit first-run behavior (duplo auto-detecting a fresh directory and running interactively). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000746: Document the new flow: duplo init to create SPEC.md, edit SPEC.md, run duplo. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000747: Document the three input channels: URL in Sources, files in ref/, prose in Purpose/Architecture/Design/Behavior/Notes. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000748: Document duplo init command surface and flags. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000749: Keep existing documentation for duplo fix and duplo investigate unchanged. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000756: Update CLAUDE.md to reflect the current architecture <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000751: Remove any references to _first_run, interactive prompts, or URL-in-text-file scanning. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000752: Document that SPEC.md is the input contract and all pipeline stages consume role-filtered input from the parser. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000753: Document the module inventory: spec_reader.py (parser), spec_writer.py (drafter), init.py (duplo init), orchestrator.py (pipeline helpers), migration.py (migration gate). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000754: Document the safety invariant: no raw SPEC.md text in LLM prompts. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [USER] Decide on design document archival strategy
  - [x] T-000755: The design documents (design/PARSER-design.md, design/DRAFTER-design.md, design/INIT-design.md, PIPELINE-design.md, design/MIGRATION-design.md, REDESIGN-overview.md) were authoritative during implementation. Options: (a) move to docs/design/ as historical reference, (b) keep in place, (c) delete. User decides. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000760: Execute the design document archival strategy decided above <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000757: If archiving: create design/ directory and move delivered design docs there. Update any remaining cross-references in CLAUDE.md or README.md. <!-- created_at: 2026-05-24T14:10:23Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000758: If keeping in place: no action needed. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000759: If deleting: remove the files. Confirm no remaining cross-references. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Automated integration tests

- [x] T-000763: Add tests/test_phase7_integration.py with test_fresh_directory_without_init_prints_message <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000761: Run duplo (no subcommand) in a completely empty tmpdir (no .duplo/, no SPEC.md). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000762: Assert: prints a message directing user to run duplo init; exits 0 (not 1); does NOT attempt _first_run behavior (no interactive prompts, no directory creation). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000767: Add test_old_project_still_blocked_by_migration <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000764: Create a tmpdir with .duplo/duplo.json but no SPEC.md. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000765: Run duplo (no subcommand). <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000766: Assert: migration message printed (now referencing duplo init); exits 1. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000771: Add test_no_dead_imports_remain <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000768: Programmatically import every module in the duplo package. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000769: Assert: no ImportError from deleted modules; no AttributeError from deleted functions. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000770: This is a smoke test to catch stale imports that the individual deletion tests might miss. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000775: Run the full test suite and confirm Phase 7 closes cleanly <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000772: Execute pytest -x against the duplo repo. <!-- created_at: 2026-04-17T15:26:21Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000773: Assert: all tests pass; no test file references deleted modules or functions. <!-- created_at: 2026-04-17T22:53:17Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->
  - [x] T-000774: If any test fails, the task fails and mcloop will retry. <!-- created_at: 2026-04-16T04:32:12Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Phase 8: Platform Knowledge Library
<!-- phase_id: phase_008 -->

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

- [x] T-000776: Add structured platform entry syntax to the spec parser. In spec_reader.py, parse list-item entries under the Architecture section with fields: platform, language, build. Each entry becomes one element in a new list field on ProductSpec. Free-form prose after the structured entries is still captured in spec.architecture as before. Write tests in test_spec_reader.py covering: single entry, multiple entries, mixed entries plus prose, no entries (backward compatible prose-only). <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000777: Update SPEC-template.md and SPEC-guide.md. Add an example showing structured platform entries under Architecture. The template should show one entry with platform/language/build fields. The guide should explain that multiple entries are supported for multi-stack projects and that free-form prose can follow. <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000778: Extend BuildPreferences to support multiple stacks. In build_prefs.py, change parse_build_preferences to accept the structured entries from the spec parser when available, falling back to LLM extraction from prose when no structured entries exist. Return a list of BuildPreferences instead of a single instance. Update architecture_hash and validation. Update all callers in main.py to handle the list. Write tests in test_build_prefs.py. <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000779: Wire the resolver into the pipeline. In main.py, after loading preferences, call resolve_profiles() for each BuildPreferences in the list. Collect the union of matched profiles. Pass them downstream to the planner and CLAUDE.md writer. Write a test confirming resolve_profiles is called and its output is threaded through. <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000780: Wire platform rules into the planner system prompt. In planner.py, add a platform_addendum parameter to generate_phase_plan and generate_next_phase_plan. When provided, append it to the system prompt string before calling query(). The caller in main.py passes format_planner_system_addendum(profiles). Write tests in test_planner.py: mock query(), verify system prompt contains platform rules when addendum is provided, verify it does not when addendum is empty. <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000781: Wire scaffold generation into the pipeline. In main.py, before the first call to generate_phase_plan for a new project, call write_scaffold(profiles, project_name, target_dir). Pass format_scaffold_notice(written) into the planner as part of the platform addendum. Write tests in test_scaffold confirming: run.sh is created with correct content and executable bit, existing files are not overwritten, gitignore entries are appended without duplication. <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000782: Build the CLAUDE.md writer for target projects. Create a new function in saver.py that assembles and writes CLAUDE.md to the target project directory. Content includes: project name and stack from BuildPreferences, platform rules section from format_claude_md_section(profiles), and local overrides section from local.md if present. This function is called from main.py during project setup and on subsequent runs when profiles change. Write tests in test_saver.py. <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000783: Add local.md support. In main.py, check for local.md in the target project root. If present, read its content and pass it through format_local_overrides() into both the planner addendum and the CLAUDE.md writer. Add local.md to the gitignore entries written by initializer.py create_project_dir(). Write tests confirming local overrides appear in planner prompt and CLAUDE.md when local.md exists and are absent when it does not. <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000784: Add integration test for the full platform knowledge flow. Create test_platform_integration.py. Given a SPEC.md with a SwiftUI architecture entry, mock the LLM calls and verify that: resolve_profiles returns the swiftui_spm profile, the planner system prompt contains platform rules, a run.sh file exists on disk, CLAUDE.md contains the platform rules section, and gitignore contains .build/ and *.app/ entries. <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

- [x] T-000785: Run the full test suite and confirm all tests pass. Execute pytest -x against the duplo repo. If any test fails, the task fails and mcloop will retry. <!-- created_at: 2026-04-18T20:33:59Z --> <!-- completed_at: 2026-05-29T04:14:59Z -->

## Phase 9: Iterative plan authoring (opus/codex), council demoted
<!-- phase_id: phase_009 -->

Duplo's PLAN.md authoring path currently runs either a single-pass
council (`council.author_phase_plan` -> `council_four_canonical`) or a
single `claude_cli.query`. Neither is the intended development path,
which is an iterative refinement between the top arbitrators (opus and
gpt/codex): a proposer drafts the phase body, a reviewer critiques, a
judge accepts only when the body passes real canonical validation, else
the loop iterates. This phase routes `generate_phase_plan` through that
loop and demotes the council to explicit escalation only. Design
rationale: `bob-tools/.scratch/iterative_authoring_proposal.md` (rev 2,
Path 1 decided).

PRECONDITION (Orchestra phase_001, separate package PLAN.md): this
phase depends on two Orchestra extension points that must land first —
(A) role-scoped criteria reaching the executor via a compound role
binding, and (B) a caller-supplied transform-registration hook on
`run_workflow`/`run_role`. Do NOT start the tasks below until Orchestra
phase_001 is complete; the authoring loop's validation gate and
role-scoped criteria are not expressible without them.

Key design points (from the reviewed proposal):
- The authoring workflow is a FORK, `plan_author.orc`, based on
  `iterate_until_acceptable.orc` (a true proposer->reviewer->judge loop
  whose proposer writes the `proposal` body), NOT `design_loop.orc`
  (which refines only a verdict and writes no body).
- A post-accept validation STATE (not a hook — hooks swallow exceptions
  and cannot alter routing) runs a duplo-owned transform that calls
  `typed_plan_from_synthesizer_text` / `validate_plan(constructed=True)`
  / `assert_mcloop_canonical`; the transform returns `validation_ok`
  plus feedback, and a guard branches `validation_ok == true => done`,
  else `=> propose`. Convergence cannot accept a body that fails
  canonical validation.
- A NEW compound role `plan_author` (distinct from the shared `design`
  role, which `design_extractor` uses) carries the authoring criteria.
  Leaf bindings map the two arbitrators: proposer=opus, reviewer=codex,
  judge=opus (only the `design` role forbids same-actor judge/reviewer;
  the validation gate covers the opus self-accept risk).
- CAPPED (cap hit without accept) fails closed: no PLAN.md write; the
  best-so-far body goes only to audit output.
- `max_rounds` is an external input on `plan_author.orc` (the base
  workflow hardcodes `attempts.judge < 6`), configured on the compound
  binding, overridable per call only via an explicit duplo knob.

- [x] T-000786: Establish `duplo/workflows/plan_author.orc` as a DUPLO-OWNED, project-local fork of `iterate_until_acceptable.orc` (NOT added to Orchestra's packaged set; Orchestra resolves project-local workflows ahead of packaged ones). The file must declare `query`, `history`, `required_phase_id`, and `max_rounds` as external inputs and keep the proposer->reviewer->judge states with the proposer writing `proposal`. Scope is the fork skeleton and its external-input declarations ONLY — the validation state, cap routing, feedback wiring, and the judge-cap swap are separate tasks (T-000793/T-000794/T-000795). A `plan_author.orc` already exists on disk from prior work: verify it against this spec rather than rewriting it; correct only the fork/external-input surface if it diverges. One workflow-load/parse test: the fork parses and declares all four external inputs. <!-- created_at: 2026-06-03T19:01:54Z --> <!-- completed_at: 2026-06-04T07:10:00Z -->
- [x] T-000793: In `duplo/workflows/plan_author.orc`, ensure the post-accept validation state exists: a `validate` state with `actor transform validate_plan_body` that reads `proposal` and writes `validation_ok` (json/bool) and `validation_feedback` (text), with the judge's `accept` transition routing to `validate` instead of `done`. (The file on disk may already contain this from prior work — verify against this spec and correct only if it diverges.) One parse test: the workflow references the `validate_plan_body` transform and the judge `accept` edge targets the validation state. Scope is the validation state and its accept-edge wiring ONLY; cap routing is T-000794. <!-- created_at: 2026-06-03T19:01:54Z --> <!-- completed_at: 2026-06-04T07:10:00Z -->
- [x] T-000794: In `duplo/workflows/plan_author.orc`, ensure the validation state's cap routing matches the judge's `attempts.judge < max_rounds` discipline so a never-validating body terminates as CAPPED, not ERROR: `on complete when validation_ok == true => done`; `on complete when attempts.judge < max_rounds => propose`; `on complete => done` (cap reached with validation still failing routes to `done` with a non-accept outcome so `run_role` derives CAPPED, not an uncapped loop to `max_total_steps`). Also replace any hardcoded judge cap (`attempts.judge < 6`) with `max_rounds`. (Verify against the on-disk file; correct only if it diverges.) One routing test: cap routing yields CAPPED (not ERROR) when the body never validates. Scope is cap routing and the judge-cap swap ONLY. <!-- created_at: 2026-06-03T19:01:54Z --> <!-- completed_at: 2026-06-04T07:10:00Z -->
- [x] T-000795: In `duplo/workflows/plan_author.orc` and its proposer prompt template, ensure `validation_feedback` reaches the proposer: it must be in the proposer role's read set and rendered in the proposer prompt template, so a re-draft can address the canonical-validation failure rather than drafting blind. (Verify against the on-disk file and templates; correct only if they diverge.) One test: the proposer state reads `validation_feedback` and the proposer template references it. Scope is the proposer feedback path ONLY. <!-- created_at: 2026-06-03T19:01:54Z --> <!-- completed_at: 2026-06-04T07:10:00Z -->
- [x] T-000787: Implement the duplo-owned `validate_plan_body` transform and register it via Orchestra's caller-supplied transform-registration hook (Orchestra phase_001 extension point B). The transform runs the candidate body through `council.typed_plan_from_synthesizer_text(body, required_phase_id=...)` (which already parses, rebuilds constructed, migrates ids, runs `validate_plan(constructed=True)` and `assert_mcloop_canonical`, and checks the required phase id); on success it returns `validation_ok=true`; on `PlanSyntaxError`/`PlanValidationError` it returns `validation_ok=false` with the error text as `validation_feedback`. The transform is owned by duplo and supplied to Orchestra through the registration callback — Orchestra does not import duplo or bob_tools. Unit tests: a canonical body validates ok; a body with a wrong phase id, a malformed checklist, and a `## Bugs` section each return `validation_ok=false` with feedback; the transform never raises for a merely-invalid body (only `validation_ok=false`). <!-- created_at: 2026-06-03T19:01:54Z --> <!-- completed_at: 2026-06-04T07:25:54Z -->
- [x] T-000788: Define the `plan_author` compound role and its acceptance criteria, and add the leaf bindings (proposer=opus, reviewer=codex, judge=opus) plus `max_rounds`. Criteria encode the judgment-level PLAN.md quality rules from `planner._PHASE_SYSTEM` that the structural validation transform does not already enforce (task granularity 5-15, [BATCH]/[USER]/[AUTO] discipline, `[feat:]`/`[fix:]` annotation presence). The hard structural rules (canonical header, required phase id, no `## Bugs`, no project H1) are enforced by the validation transform, not duplicated as prose criteria. Document where this role config lives and that it is distinct from the shared `design` role. Tests: the role resolves with distinct-enough bindings; criteria reach the executor (via Orchestra phase_001 extension point A). <!-- created_at: 2026-06-03T19:01:54Z --> <!-- completed_at: 2026-06-04T07:34:55Z -->
- [x] T-000789: Add a duplo authoring adapter (NOT the shared `run_iterative_design`, which `design_extractor` uses) that calls `run_role("plan_author", query=..., history=..., required_phase_id=..., registry_customizer=<register validate_plan_body>)`. Build `query` from the same prompt/system material `author_phase_plan` assembles (system directive folded into the query text as `council._build_state_text` does). Build `history` as compact prior-phase context: prior phase ids/titles, completed-phase summaries, files already created, and prior validation failures on retry — NOT full transcripts, and NOT the current phase's source/spec (that stays in `query`). Translate the result: CONVERGED -> return the converged `proposal` body; CAPPED -> fail closed (raise, no body returned for PLAN.md; best-so-far to audit only); ERROR -> raise with transcript path. Note CAPPED is the disposition produced by T-000786's validation-cap routing when a body never validates within `max_rounds`; the adapter must treat it as fail-closed, never as a usable plan. Unit tests: CONVERGED returns the body; CAPPED raises and writes no plan; ERROR raises with transcript path; `history` carries the compact fields and not the full source. <!-- created_at: 2026-06-03T19:01:54Z --> <!-- completed_at: 2026-06-04T07:54:48Z -->
- [ ] T-000790: Route `planner.generate_phase_plan` through the new authoring adapter and the unchanged `typed_plan_from_synthesizer_text` -> `save_plan` tail. Replace the `council.is_enabled()` branch so iterative authoring is the unconditional default. The converged body flows through the existing validation/persistence tail with no change to `save_plan`. Demote council to explicit opt-in escalation: keep `council.py` for reauthor, audit value, and its spec/pyproject/run.sh preflight. NOTE: `council.is_enabled()` is ALREADY opt-in (true only when `DUPLO_USE_COUNCIL` is truthy) — do NOT invert the env var. The change is that `generate_phase_plan` no longer routes to council on that flag as part of normal authoring; iterative authoring is unconditional and council is reachable only via an explicit escalation/experiment path. Tests in `test_planner.py`: `generate_phase_plan` invokes the iterative adapter by default (council not called); the converged body is persisted via the typed tail; council runs only when the explicit escalation flag is set; a CAPPED authoring run does not write PLAN.md. <!-- created_at: 2026-06-03T19:01:54Z -->
- [ ] T-000791: Add an end-to-end test of the iterative authoring path with the LLM calls mocked: a proposer body that initially fails canonical validation (wrong phase id) and on the next round passes, exercising the validation-state feedback loop to convergence (assert the proposer actually receives `validation_feedback` on the retry round); assert the final PLAN.md is canonical, carries the runtime-supplied phase id, and that a body which never validates within `max_rounds` results in CAPPED fail-closed (no PLAN.md write) — and is derived as CAPPED, not ERROR. <!-- created_at: 2026-06-03T19:01:54Z -->
- [ ] T-000792: Run the full duplo test suite and confirm the phase closes cleanly. Execute pytest -x against the duplo package. Confirm the shared `design` role and `design_extractor` are unaffected by the new `plan_author` role. If any test fails, the task fails and mcloop will retry. <!-- created_at: 2026-06-03T19:01:54Z -->
