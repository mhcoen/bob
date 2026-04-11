# Duplo

Duplo duplicates apps. The user creates a project directory, drops
in reference material (screenshots, PDFs, text files, URLs), and
runs `duplo`. Duplo analyzes everything, identifies the product,
extracts features and visual design, and generates a plan. The user
then runs McLoop separately to build it. Running `duplo` again
detects new files and appends tasks for anything missing.

## Source files

### duplo/ (package)

- `main.py`: CLI entry point. Runs from the current directory with
  no required arguments. On first run (no `.duplo/duplo.json`):
  scans for reference materials via `scanner.py`, checks for a
  previously confirmed product identity in `.duplo/product.json`
  (via `load_product()` in `saver.py`) — if found, skips URL
  validation and product confirmation. Otherwise validates URLs
  point to a single product (via `validator.py`), fetches URLs,
  extracts frames from video files at scene change points (via
  `video_extractor.py`), filters frames with Codex Vision to keep
  only clear UI screenshots (via `frame_filter.py`), describes
  each frame's UI state (via `frame_describer.py`), stores accepted
  frames with descriptions in `.duplo/references/` (via
  `store_accepted_frames()` in `saver.py`), sends reference
  images and filtered video frames to Codex Vision for design
  extraction (via `design_extractor.py`),
  extracts text from PDFs (via `pdf_extractor.py`), extracts
  features, asks user to
  confirm and select, saves confirmed product identity to
  `.duplo/product.json` (via `save_product()` in `saver.py`),
  generates roadmap, moves processed reference
  files to `.duplo/references/`, saves file hash manifest to
  `.duplo/file_hashes.json` (via `hasher.py`), creates Phase 1
  PLAN.md with design requirements, and runs McLoop. On subsequent
  runs: detects file changes via hash manifest, analyzes new/changed
  top-level files the same way as first run (images to Vision, PDFs
  to text extraction, URLs to scraper) via `_analyze_new_files()`,
  re-scrapes the product URL (if set) via `_rescrape_product_url()`
  to pick up site changes (updates reference URLs, raw pages, and
  code examples) — skips re-scrape if `last_scrape_timestamp` in
  duplo.json is less than 10 minutes old, re-extracts features from the updated scraped
  content via `extract_features()` and merges new ones into
  duplo.json via `save_features()` (without removing existing
  features), then compares the combined feature list, examples,
  and design requirements against PLAN.md via
  `_detect_and_append_gaps()`
  (appends new checklist tasks for uncovered items and design
  refinements — skipped when PLAN.md has unchecked tasks to avoid
  State 2 deadlock), prints a consolidated update summary via
  `_print_summary()`, then enters a three-state flow: (1) if
  PLAN.md exists and all tasks are checked, completes the phase
  via `_complete_phase()` (records history, advances
  `current_phase`, collects issues and feedback, captures
  screenshots) and falls through; (2) if PLAN.md exists with
  unchecked tasks, tells the user to run mcloop and returns;
  (3) if no PLAN.md, generates a plan for the current roadmap
  phase. If no roadmap exists or the existing roadmap is fully
  consumed (current_phase past last entry),
  `_partition_features()` splits features into implemented and
  remaining lists by `status` field, `_unimplemented_features()`
  returns just the remaining list, and `_print_feature_status()`
  displays the partition summary after phase completion.
  `generate_roadmap()` creates a new roadmap from the remaining
  features. `UpdateSummary`
  dataclass accumulates counts (files, images, videos, PDFs, URLs,
  pages re-scraped, gaps found, tasks appended) across the
  subsequent-run steps. `_PROJECT_FILES` set excludes known
  project artifacts (PLAN.md, AGENTS.md, etc.) from analysis.
  Subcommands: ``duplo fix`` records bugs and appends
  fix tasks to PLAN.md (simple text pipe). ``duplo investigate``
  (or ``duplo fix --investigate``) runs intelligent product-level
  diagnosis via ``investigator.py``, gathering all available
  product context (reference frames, frame descriptions, design
  requirements, current screenshot, features, code examples,
  open issues) and sending it to an LLM for structured diagnosis.
  Both accept ``--images`` for user-supplied screenshots and
  ``--screenshot`` to capture via appshot. ``--file`` reads bugs
  from a file. Without subcommands, state detection is automatic.

- `scanner.py`: Scans the current directory for reference materials.
  `ScanResult` dataclass (images, videos, pdfs, text_files, urls, relevance).
  `FileRelevance` dataclass (path, category, relevant, reason).
  `scan_files()` classifies a list of specific file paths into a
  ScanResult (used by subsequent runs for new/changed files).
  `scan_directory()` finds images (png, jpg, gif, webp), videos
  (mp4, mov, webm, avi), PDFs, text/markdown files, and extracts
  HTTP(S) URLs from any readable
  file (not just text-extension files). Skips `.duplo/`, `.git/`,
  and other non-project directories. Skips binary/archive extensions
  (`_IGNORE_EXTS`). Deduplicates URLs while preserving order.
  Assesses relevance of each file: flags tiny images (<1KB), empty
  PDFs, empty/very-short text files as irrelevant. Tracks non-text
  files that contributed URLs as `url_source` relevance entries.

- `fetcher.py`: HTTP fetching and HTML text extraction. `fetch_text()`
  fetches a single URL. `extract_text()` strips noise tags (script,
  style, nav, footer). `extract_links()` finds same-domain links.
  `score_link()` prioritizes docs/features over blog/pricing/legal.
  `is_docs_link()` detects documentation links by examining URL path
  and anchor text for docs-related words (not hardcoded platforms).
  `_PLATFORM_DOMAINS` set and `_is_platform_domain()` function
  prevent the crawler from following cross-domain links into hosting
  platform marketing pages (GitHub features, GitLab pricing, etc.)
  which would contaminate the feature list with platform features.
  Product documentation hosted on platforms (e.g.
  ``github.com/org/repo/wiki``) is allowed through — the function
  checks the URL path, not just the domain.
  `detect_docs_links()` extracts documentation links from HTML.
  `PageRecord` dataclass (url, fetched_at, content_hash) records
  each successfully fetched page.
  `fetch_site()` does BFS crawl with priority ordering, separate page
  caps for seed domain (`max_pages=10`) and documentation domains
  (`max_docs_pages=50`), follows cross-domain documentation links
  automatically, and once a docs domain is reached, follows
  same-domain links within it. Returns `(text, code_examples,
  doc_structures, page_records, raw_pages)` tuple where `raw_pages`
  is a `dict[str, str]` mapping each URL to its raw HTML content.

- `claude_cli.py`: Runs AI queries through the ``Codex -p`` CLI
  instead of direct Anthropic API calls. `ClaudeCliError` exception
  for non-zero exit codes. `query()` sends a text prompt with
  optional system prompt and model selection. `query_with_images()`
  sends a prompt with image file paths, enabling the Read tool so
  Codex can view the images. All AI modules route through this
  helper so the Max subscription is used instead of API credits.

- `extractor.py`: Calls ``Codex -p`` to extract a structured feature
  list from scraped text. `Feature` dataclass (name, description,
  category). `extract_features()` accepts optional `existing_names`
  parameter: when provided, the extraction prompt instructs the LLM
  to reuse existing feature names for matching concepts instead of
  inventing new ones (prevents near-duplicate accumulation across
  runs). The system prompt enforces strict anti-hallucination
  constraints: only demonstrably offered features, no platform
  features, no passing mentions, omit when in doubt.
  `_parse_features()` handles JSON parsing with fence
  stripping and error tolerance.

- `task_matcher.py`: Matches unannotated completed tasks to features
  using ``Codex -p``. `match_unannotated_tasks()` filters tasks
  without ``[feat: ...]`` or ``[fix: ...]`` annotations, batches them
  into a single CLI call with the full feature list, and parses the
  response. For each task Codex classifies it as matching an existing
  feature ("existing"), representing new functionality ("new"), or
  being structural/scaffolding ("none"). Matched features are marked
  as ``implemented`` via `save_feature_status()`. New features are
  added to duplo.json with ``status: "implemented"`` and
  ``implemented_in`` set to the current phase via `save_features()`.
  `_parse_matches()` handles JSON parsing with fence stripping.
  Returns ``(matched_names, new_names)`` tuple.

- `gap_detector.py`: Compares extracted features, code examples, and
  design requirements against the current PLAN.md to identify gaps.
  Uses ``Codex -p`` to determine which features/examples are not yet
  covered by the plan. `GapResult` dataclass (missing_features,
  missing_examples, design_refinements). `MissingFeature` dataclass
  (name, reason). `MissingExample` dataclass (index, summary, reason).
  `DesignRefinement` dataclass (category, detail, reason).
  `detect_gaps()` sends the plan, features, and examples via
  `claude_cli.query()` and returns a `GapResult`. Accepts optional
  `platform` and `language` keyword arguments so it can skip features
  that are infeasible for the target stack (e.g. Windows support for
  a macOS-only SwiftUI app). `detect_design_gaps()` compares design
  requirements (colors, fonts, components) against the plan text
  without an API call — returns a list of `DesignRefinement` for
  items not mentioned. `_parse_result()` handles JSON parsing with
  fence stripping. `format_gap_tasks()` renders gaps (features,
  examples, and design refinements) as PLAN.md checklist items.
  Called during subsequent runs by `_detect_and_append_gaps()` in
  main.py after re-scraping and before phase execution.

- `design_extractor.py`: Sends reference images via ``Codex -p``
  to extract visual design details. `DesignRequirements`
  dataclass (colors, fonts, spacing, layout, components,
  source_images). `extract_design()` passes up to 10 image paths
  to `claude_cli.query_with_images()` and parses the structured
  JSON response. `_parse_design()` handles JSON parsing with fence
  stripping. `format_design_section()` renders the extracted
  design as a Markdown section for inclusion in PLAN.md. Called
  during first run on relevant images found by `scanner.py`.
  Results saved to duplo.json via `save_design_requirements()`.

- `pdf_extractor.py`: Extracts text content from PDF files using
  pypdf. `extract_pdf_text()` accepts a list of paths, extracts
  text from all pages of each PDF, and returns the combined text
  with filename headers. `_extract_single()` handles one file.
  Skips unreadable files silently. Called during first run on
  relevant PDFs found by `scanner.py`. Extracted text is included
  in the combined text sent to feature extraction.

- `video_extractor.py`: Extracts frames from video files at scene
  change points using ffmpeg. `ExtractionResult` dataclass (source,
  frames, error). `ffmpeg_available()` checks if ffmpeg is on PATH.
  `extract_scene_frames()` runs ffmpeg with the `select` filter
  using `gt(scene,threshold)` to detect visual transitions, saves
  frames as PNG files. Retries with a lower threshold if too few
  frames are found. After extraction, deduplicates similar frames
  using perceptual image hashing via `deduplicate_frames()`.
  `_dhash()` computes a 64-bit difference hash (dHash) for an image.
  `_hamming()` computes Hamming distance between two hashes.
  `deduplicate_frames()` compares each frame against kept frames
  and removes near-duplicates (Hamming distance <= threshold,
  default 6). Requires Pillow; gracefully skips deduplication if
  Pillow is unavailable. `extract_all_videos()` processes multiple
  videos. Called during first run and subsequent runs on relevant
  video files found by `scanner.py`. Extracted frames are fed
  into the frame filtering and design extraction pipelines alongside
  user-provided images. Frames are saved to `.duplo/video_frames/`.

- `frame_filter.py`: Sends candidate video frames via ``Codex -p``
  to classify each one. `FilterDecision` dataclass (path,
  keep, reason). `filter_frames()` sends frames in batches of 10
  via `claude_cli.query_with_images()`. `_filter_batch()` sends
  a batch and parses the response. `_parse_decisions()` handles JSON parsing
  with fence stripping; falls back to keeping all frames on parse
  error. `apply_filter()` returns kept frame paths and deletes
  rejected frames from disk. Keeps clear, stable UI screenshots;
  discards transitions, blur, marketing overlays, loading screens.
  Called by `main.py` after video frame extraction and deduplication,
  before frames are passed to design extraction.

- `frame_describer.py`: Sends accepted video frames via ``Codex -p``
  to describe what UI state each one shows. `FrameDescription`
  dataclass (path, state, detail). `describe_frames()` sends frames
  in batches of 10 via `claude_cli.query_with_images()`.
  `_describe_batch()` sends a batch and parses the response. `_parse_descriptions()`
  handles JSON parsing with fence stripping; falls back to "unknown"
  state on parse error. Called by `main.py` after frame filtering,
  before design extraction. Prints each frame's UI state description.

- `hasher.py`: Computes and persists a SHA-256 hash manifest of all
  files in the project directory. `compute_hashes()` walks the
  directory tree (skipping `.duplo/`, `.git/`, etc.) and returns a
  `{relative_path: sha256}` dict. `save_hashes()` writes to
  `.duplo/file_hashes.json`. `load_hashes()` reads it back.
  `diff_hashes()` compares old and new manifests, returning a
  `HashDiff` dataclass (added, changed, removed). Called during
  first run (initial manifest) and subsequent runs (detect changes).

- `validator.py`: Validates that a URL points to a single clear
  product, not a company portfolio or homepage with multiple products,
  and not a landing page with unclear product boundaries.
  `ValidationResult` dataclass (single_product, product_name,
  products, reason, unclear_boundaries). `validate_product_url()`
  fetches the page and uses ``Codex -p`` to classify it into one of
  three categories: single product, multiple products, or unclear
  boundaries (vague landing page where the product is not clearly
  identifiable). `_parse_result()` handles JSON parsing with fence
  stripping and fallback defaults. Called by `main.py` during first
  run before `fetch_site()`.

- `selector.py`: Interactive feature selection. Displays features
  grouped by category with numbers. Accepts "all", "none", ranges
  like "1-4,7", or comma-separated numbers. `select_features()`
  accepts optional `recommended` (list of feature names from the
  roadmap's next phase) and `phase_label` (e.g. ``"Phase 2: Title"``)
  parameters; when provided, those features are marked with `*` in
  the display, the recommendation line includes the phase name and
  numbered indices, and they are used as the default selection
  instead of "all". `select_issues()` displays open issues
  (filtering out resolved ones) with numbered selection using
  the same pattern; default is "none" (skip). `_recommended_indices()`
  returns 0-based indices of recommended features.
  `_parse_selection()` handles input parsing.

- `questioner.py`: Interactive build preferences. `BuildPreferences`
  dataclass (platform, language, constraints, preferences).
  `ask_preferences()` prompts for each with platform prefix matching.

- `saver.py`: Writes `.duplo/duplo.json` with selections and
  preferences. `DUPLO_DIR` (`.duplo`), `DUPLO_JSON`
  (`.duplo/duplo.json`), and `PRODUCT_JSON` (`.duplo/product.json`)
  constants define the state directory layout.
  `_ensure_duplo_dir()` creates the `.duplo/` directory before
  writes. `save_product()` writes the confirmed product identity
  (name and source URL) to `.duplo/product.json`.
  `load_product()` reads it back, returning
  ``(product_name, source_url)`` or ``None`` if absent.
  `save_features()` merges new features into duplo.json, using
  `_deduplicate_features_llm()` to detect semantic duplicates via
  a single batch ``Codex -p`` call (e.g. "CLI tool" and
  "Command-line interface (CLI)" are recognized as the same
  feature). Falls back to exact-name matching if the LLM call
  fails. After merging, runs a post-merge pass via
  `_find_duplicate_groups()` over ALL feature names to detect
  near-duplicates that accumulated across runs (e.g. "Custom
  vocabulary / glossary" vs "Custom vocabulary").
  `_merge_duplicate_group()` keeps the most descriptive name
  (longest) and preserves ``status: implemented`` if any member
  has it. Prints "Merged N duplicate feature(s)." when merges
  occur. After dedup merging, `_propagate_implemented_status()`
  compares remaining pending features against implemented ones via
  a single LLM call; any pending feature semantically identical to
  an implemented one is marked as implemented (e.g. "Local offline
  transcription" pending when "Local on-device transcription" is
  implemented). Never removes existing features except via dedup
  merge. Called during subsequent runs after re-extraction.
  `save_feature_status()` updates a single feature's `status` and
  `implemented_in` fields by name. Raises `ValueError` if the
  feature name is not found or the status is invalid.
  `mark_implemented_features()` takes a list of `CompletedTask`
  objects and a phase label, collects unique feature names from
  their `[feat: ...]` annotations, and calls `save_feature_status()`
  for each. Silently skips features not found in duplo.json.
  Returns the list of feature names that were marked.
  `resolve_completed_fixes()` takes a list of `CompletedTask`
  objects, collects unique fix descriptions from their `[fix: ...]`
  annotations, and calls `resolve_issue()` for each. Silently
  skips issues not found in duplo.json. Returns the list of issue
  descriptions that were resolved.
  `save_issues()` replaces the top-level `issues` list in duplo.json.
  `add_issue()` appends a single issue (description, severity,
  timestamp) with duplicate detection by description. Validates
  severity is one of `critical`, `major`, `minor`.
  `save_issue()` appends an issue with `description`, `source`,
  `phase`, and `status` ("open") fields, plus an `added_at`
  timestamp. Skips duplicates by description.
  `resolve_issue()` finds an issue by description and sets its
  `status` to "resolved" with a `resolved_at` timestamp. Raises
  `ValueError` if no matching issue exists.
  `load_issues()` reads the issues list back.
  `clear_issues()` empties the issues list.
  `save_code_examples()` stores extracted code examples
  in duplo.json. `save_examples()` saves each code example as a
  separate JSON file in `.duplo/examples/<index>_<slug>.json` for
  review and editing. `load_examples()` reads examples back from
  `.duplo/examples/`, falling back to duplo.json for backward
  compatibility. `EXAMPLES_DIR` constant (`.duplo/examples`).
  `save_doc_structures()` stores extracted doc structures.
  `save_reference_urls()` stores `PageRecord` list (URL, timestamp,
  content hash) for all pages consulted during scraping.
  `save_raw_content()` saves raw HTML for each scraped page to
  `.duplo/raw_pages/<content_hash>.html` so re-runs can diff against
  what changed on the product site. `RAW_PAGES_DIR` constant
  (`.duplo/raw_pages`).
  `save_design_requirements()` stores visual design requirements
  (colors, fonts, spacing, layout, components) extracted from
  reference images.
  `write_claude_md()` writes a AGENTS.md template to target projects
  with appshot and debugging instructions. Non-destructive: if the
  file already exists, only appends sections whose headings are not
  already present.
  `save_frame_descriptions()` stores frame UI state descriptions
  (filename, state, detail) in duplo.json under `frame_descriptions`.
  `store_accepted_frames()` copies accepted video frames into
  `.duplo/references/` and saves their descriptions to duplo.json.
  `move_references()` moves processed reference files (images, PDFs,
  text files) into `.duplo/references/` to keep the project directory
  clean. `REFERENCES_DIR` constant (`.duplo/references`).

- `initializer.py`: Creates target project directory with git init,
  a `.duplo/` subdirectory for Duplo's working state, and a
  `.gitignore` that excludes `.duplo/`.
  `project_name_from_url()` derives name from hostname.

- `roadmap.py`: Generates a phased build roadmap via ``Codex -p``.
  `generate_roadmap()` produces a JSON array of phases (phase number,
  title, goal, features, test criteria). `format_roadmap()` renders
  it for terminal display. `_parse_roadmap()` handles JSON parsing
  with fence stripping.

- `planner.py`: Generates PLAN.md for a specific roadmap phase.
  `generate_phase_plan()` accepts a roadmap phase dict and produces
  a McLoop-compatible checklist scoped to that phase. Heading format:
  `# <AppName> — Phase N: <Title>`. Accepts optional `phase_number`
  keyword to override the phase dict's number (derived from
  `phases` history length + 1 in main.py).
  `generate_next_phase_plan()` incorporates feedback and visual
  issues. `append_test_tasks()` appends doc-example test checklist
  items to a plan. `save_plan()` writes the file; if PLAN.md already
  exists it appends new content after a blank line, preserving all
  existing checked and unchecked items. `CompletedTask` dataclass
  (text, features, fixes, indent). `parse_completed_tasks()` parses
  checked ``- [x]`` lines from PLAN.md content, extracting the task
  description, ``[feat: "..."]`` feature annotations, ``[fix: "..."]``
  fix annotations, and indentation level. Used at phase completion
  to determine what was implemented.

- `screenshotter.py`: Uses playwright to capture full-page screenshots
  of product website URLs. `save_reference_screenshots()` launches
  headless Chromium, captures each URL.

- `doc_examples.py`: Extracts code examples from documentation HTML
  as input/expected_output pairs. `CodeExample` dataclass (input,
  expected_output, source_url, language). `extract_code_examples()`
  finds `<pre>`/`<code>` blocks and pairs them using three strategies:
  labeled pairs (input/output headings), Python doctest style (`>>>`
  prompts), and shell style (`$`/`%` prompts). Called automatically
  by `fetch_site()` on each crawled page; results stored in
  .duplo/duplo.json via `save_code_examples()`.

- `doc_tables.py`: Extracts feature tables, operation lists, unit
  lists, and function references from documentation HTML.
  `DocStructures` dataclass aggregates `FeatureTable`,
  `OperationList`, `UnitList`, and `FunctionRef` dataclasses.
  `extract_doc_structures()` scans `<table>`, `<ul>`/`<ol>`, `<dl>`,
  and `<code>` elements, classifying each by its nearest heading.
  Called automatically by `fetch_site()` on each crawled page;
  results stored in .duplo/duplo.json via `save_doc_structures()`.

- `test_generator.py`: Turns extracted documentation examples into
  unit test files. `detect_target_language()` checks the target
  project directory for build-system files (pyproject.toml → Python,
  Package.swift → Swift, Cargo.toml → Rust, go.mod → Go,
  package.json → JS/TS) and returns the language name or "unknown".
  `main.py` calls this before test generation; if the target is not
  Python (or unknown), `generate_test_source` and `save_test_file`
  are skipped with a message. `load_code_examples()` reads examples
  from .duplo/duplo.json. `generate_test_source()` produces a test
  file with one test function per example calling a `run_example()`
  stub, grouped into classes by source URL for easy failure diagnosis.
  `generate_parametrized_test_source()` produces a compact
  pytest-parametrized variant, also grouped by source URL.
  `_category_class_name()` derives a class name from a URL.
  `_group_by_source()` groups examples by source URL preserving
  original indices. `save_test_file()` writes the generated file.

- (To be created) Update module. `duplo update` re-scrapes the
  product, compares against existing features and PLAN.md, appends
  new unchecked tasks for anything missing. Never modifies existing
  tasks.

- `verification_extractor.py`: Extracts functional verification cases
  from video frame descriptions stored in duplo.json. The frame
  describer already captures expression/result pairs (e.g.
  ``"'Price: $7 × 4' with the result '$28'"``); this module uses
  ``Codex -p`` to parse those into structured
  ``VerificationCase(input, expected, frame)`` objects.
  ``extract_verification_cases()`` sends frame descriptions to the
  LLM and returns parsed cases. ``format_verification_tasks()``
  renders them as PLAN.md checklist items (e.g.
  ``- [ ] Verify: type \`Price: $10\`, expect result \`$10\```).
  ``load_frame_descriptions()`` reads the ``frame_descriptions``
  list from duplo.json. ``_parse_cases()`` handles JSON parsing
  with fence stripping. Called during plan generation in both
  ``_first_run`` and ``_subsequent_run`` (State 3).

- `spec_reader.py`: Reads and parses ``SPEC.md`` from the project
  root. `ProductSpec` dataclass (raw, purpose, scope, scope_include,
  scope_exclude, behavior, behavior_contracts, architecture, design,
  references). `BehaviorContract` dataclass (input, expected).
  `read_spec()` reads the file and returns a `ProductSpec` or
  ``None`` if absent. `_parse_spec()` splits the Markdown into
  sections by heading, extracting known sections (Purpose, Scope,
  Behavior, Architecture, Design, References) into structured
  fields. Scope include/exclude lines are parsed with regex;
  behavior contracts are parsed from backtick-delimited
  ``\`input\` \u2192 \`expected\``` patterns. `format_spec_for_prompt()`
  wraps the raw text for LLM injection with an authority label.
  `format_scope_override_prompt()` formats scope overrides as an
  addendum to the feature extraction prompt.
  `format_contracts_as_verification()` renders behavior contracts
  as PLAN.md verification tasks. The spec is read by `main.py`
  in `_first_run()`, `_subsequent_run()`, and `_fix_mode()`,
  and threaded into `extract_features()`, `generate_roadmap()`,
  `generate_phase_plan()`, and `investigate()`.

- `appshot.py`: Python wrapper around McLoop's `bin/appshot` script.
  `capture_appshot()` runs the subprocess with app name and output path.
  Accepts a `timeout` keyword argument (default 60 seconds) passed to
  `subprocess.run()`. Returns -2 on timeout with a printed warning.

- `comparator.py`: Compares app screenshots against references using
  ``Codex -p`` via `claude_cli.query_with_images()`. The comparison
  function `_compare_with_references()` in `main.py` checks
  ``.duplo/references/`` first (video frames from the product demo),
  falling back to ``screenshots/`` (Playwright website captures).
  `ComparisonResult` dataclass (similar, summary, details).
  `_parse_response()` parses structured output.

- `investigator.py`: Intelligent product-level bug diagnosis using
  LLM analysis. `Diagnosis` dataclass (symptom, expected, severity,
  area, evidence_sources). `InvestigationResult` dataclass
  (diagnoses, summary, raw_response). `investigate()` gathers all
  available product context via `_gather_context()` (reference
  frames from `.duplo/references/*.png`, current screenshot from
  `screenshots/current/main.png`, frame descriptions, design
  requirements, features, code examples, and open issues from
  `duplo.json`), builds a structured prompt via `_build_prompt()`
  with image legend so the LLM knows which image is reference vs
  current vs user-supplied, and sends everything to Codex via
  `query_with_images()`. Falls back to text-only `query()` when
  no images are available. `_parse_result()` handles JSON parsing
  with fence stripping and brace-extraction fallback.
  `format_investigation()` renders diagnoses for terminal display
  with severity tags. `investigation_to_fix_tasks()` converts
  diagnoses into `- [ ] Fix: ... [fix: "..."]` lines for PLAN.md.
  Called by `_fix_mode()` in `main.py` when `--investigate` is set
  or when invoked as `duplo investigate`.

- `issuer.py`: Converts `ComparisonResult` list into `VisualIssue`
  list. `format_issue_list()` renders Markdown. `save_issue_list()`
  writes ISSUES.md.

- `collector.py`: Collects user feedback and known issues from file
  or interactive input. `collect_feedback()` and `_read_interactive()`
  accept `input_fn`/`print_fn` for dependency injection (testable
  without patching builtins). `collect_issues()` prompts the user for
  known issues with multi-line input support — each issue can span
  multiple lines, a blank line finishes the current issue, and an
  immediate blank line (or EOF) ends input entirely. Returns a list
  of issue description strings (empty list if none). Called by
  `_complete_phase()` after status tracking.

- `notifier.py`: Sends macOS notification and prints terminal banner
  when a phase completes.

- `runner.py`: Previously ran McLoop as a subprocess. Duplo no
  longer calls McLoop directly. The user runs McLoop separately
  after duplo generates the plan.

### Top-level files

- `PLAN.md`: Task checklist for building duplo itself.
- `README.md`: User-facing documentation.
- `AGENTS.md`: This file. Read by Codex at session start.
- `mcloop.json`: Check config (ruff check, ruff format, pytest).
- `pyproject.toml`: Package config with httpx, beautifulsoup4, lxml,
  playwright, anthropic, Pillow dependencies.

### tests/

One test file per source module, mirroring the package structure:
test_claude_cli.py, test_main.py, test_fetcher.py, test_extractor.py, test_selector.py,
test_questioner.py, test_saver.py, test_initializer.py,
test_planner.py, test_screenshotter.py, test_appshot.py,
test_comparator.py, test_issuer.py, test_collector.py,
test_notifier.py, test_runner.py, test_doc_examples.py,
test_doc_tables.py, test_test_generator.py, test_scanner.py,
test_validator.py, test_design_extractor.py,
test_pdf_extractor.py, test_hasher.py, test_gap_detector.py,
test_frame_filter.py, test_frame_describer.py, test_video_extractor.py,
test_task_matcher.py, test_verification_extractor.py,
test_investigator.py, test_spec_reader.py, test_roadmap.py.

## Keeping this file current

**If you add, rename, or significantly change any source file, update
the relevant entry in this AGENTS.md file before finishing.** This file
is the manifest that every future session reads first. If it is stale,
sessions waste time searching for files instead of working.

## Conventions

- Python 3.11+, ruff for linting, pytest for tests
- Lines must not exceed 99 characters
- Do not import pytest unless you use it (e.g., pytest.raises, pytest.fixture)
- Do not use `l` as a variable name (ruff E741). Use `line`, `link`, `item`, etc.
- Run `ruff format .` as the absolute last thing before finishing. After every edit. No exceptions.
- Run `ruff check .` after formatting to catch unused imports
- Do not chain shell commands with && or ;

## Debugging

When something crashes or behaves unexpectedly, find and read the
actual error output first. Check crash reports, stderr, log files,
tracebacks. Do not guess from source code alone. After fixing,
reproduce the failure and verify it is gone.
