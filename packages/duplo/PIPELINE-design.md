# Pipeline integration design

This document specifies how the existing duplo pipeline stages
adapt to the new role-based input model from SPEC.md. Most
changes are "consume role-filtered input from the parser instead
of running heuristics on raw directory contents."

The audit reference for current behavior is the static-audit
document we discussed earlier (Codex audit, commit ff05209).
Pipeline modules referenced below all exist today.


## The principle

Today's pipeline runs on a "scan everything, infer roles" model:

- `scanner.scan_directory` heuristically classifies files in the
  project root as image/video/PDF/text and assesses relevance.
- `extract_design` runs on all relevant images regardless of
  whether the user intended them as design references.
- `extract_all_videos` extracts frames from all videos
  regardless of whether they're demos, recorded UIs, or
  irrelevant clips.
- `_download_site_media` pulls images and videos from any
  scraped page and pipes them into design extraction.

Under the new model, every pipeline stage takes role-filtered
input from the parser. The role assignment lives in SPEC.md
where the user can see and correct it, not buried in
heuristics.


## Stage-by-stage changes

### `scanner.py`

Today: scans the project root, classifies files by extension,
assesses relevance via size/dimension heuristics.

New: scans `ref/` instead of the project root. The role-based
filtering replaces relevance assessment; if a file is in `ref/`
and listed in `## References` with a non-`ignore` role, it's
relevant. Files in `ref/` not listed in `## References` get a
diagnostic ("file in ref/ has no entry in ## References; will
be ignored").

`scan_directory(target_dir)` becomes `scan_directory(ref_dir)`.
Callers that pass `"."` change to pass `target_dir / "ref"`.

The relevance scoring (image dimensions, file size) goes away.
Roles are declared, not inferred.

The existing `scan_files(paths)` function (used for analyzing
specific changed files in subsequent runs) keeps working but
gets a parallel role lookup: each file's path is checked
against the parsed `## References` to determine its role.


### `fetcher.py`

Today: `fetch_site(url)` does a deep crawl with link-following.
URLs come from `_first_run` (command-line arg) or
`_subsequent_run` (re-scrape from `duplo.json`).

New: `fetch_site` gains a `scrape_depth` parameter:

- `deep`: fetch the entry URL and follow links, but only
  same-origin links (same scheme + host + port). Cross-origin
  links found during the crawl are NOT fetched in the same run;
  they are recorded as discovered for review.
- `shallow`: fetch only the entry URL, no link-following.
- `none`: don't fetch (used when the orchestrator wants to
  iterate sources without actually scraping).

The same-origin restriction on `deep` is the resolution to a
specific contradiction: today, `deep` follows links anywhere
and uses their content immediately; the discovered-URL
appending happens AFTER the content has already influenced
extraction. Restricting `deep` to same-origin means "deep"
is bounded by the user-declared source's authority, while
cross-origin discoveries are flagged for review on a future
run and have no influence on the current run.

Callers come from a new orchestrator function that iterates
`format_scrapeable_sources(spec)` and calls `fetch_site` with
each URL's declared scrape depth.

URL discovery during deep crawl: when `fetch_site` encounters
a cross-origin link, that URL is recorded for the new
"discovered URLs" appending behavior. The orchestrator
collects them after the run and calls
`spec_drafter.append_sources` with `discovered: true` flags
and `role: docs` (default; user can change). They are NOT
scraped in the current run.

Same-origin discovered links (e.g. `https://numi.app/docs`
found while crawling `https://numi.app`) ARE fetched as part
of the deep crawl and their content does influence the
current run's extraction. They are not separately recorded
as discovered, since they fall under the source's declared
authority.


### `design_extractor.py`

Today: `extract_design(images)` takes any list of images and
returns design requirements via Vision. The image set is built
by combining (a) relevant images from the project root via
`scanner`, (b) extended frames from videos, and (c) images
downloaded from scraped pages via `_download_site_media`.

New: the input set comes from three sources, all role-filtered:

1. `format_visual_references(spec)` — user-declared
   `visual-target` files in `ref/`, excluding `proposed: true`.
2. Extended frames from `format_behavioral_references(spec)`
   (videos that include `visual-target` in their roles, or
   the existing convention of pulling frames from any
   behavioral video). See "Behavioral video dual use" below.
3. Images downloaded from `product-reference` source URLs via
   `_download_site_media`. This preserves the URL-only
   common pattern documented in SPEC-guide.md: a user with
   only `## Sources` and no `ref/` files still gets visual
   design extracted from product pages. Counter-example and
   docs sources do NOT contribute site media.

Unreviewed proposals (`proposed: true`) never appear in any of
the three input sources.

#### Behavioral video dual use

A reference entry can declare multiple roles
(`role: behavioral-target, visual-target`). When a video
includes `visual-target` in its roles, frame extraction also
contributes frames to the design extraction input set. This
preserves the implicit dual-use behavior of today's pipeline
(where `extend(video_frames)` adds video frames to
`relevant_images`).

If a user declares a video as `behavioral-target` only (no
`visual-target`), frames go to verification only, not design.
The explicit declaration replaces today's implicit "all video
frames contribute to design" rule.

#### Output

The resulting `DesignRequirements` is serialized into the
AUTO-GENERATED block in SPEC.md's `## Design` section via
`spec_drafter.update_design_autogen`. The `duplo.json` cache
still gets populated for compatibility with subsequent-run
diff detection (per the open-question resolution to keep
`duplo.json` design fields as a cache during transition), but
SPEC.md is the source of truth for what gets injected into
plan generation.


### `video_extractor.py` and friends

Today: `extract_all_videos(videos)` extracts frames from all
videos. Then `frame_filter.filter_frames` and
`frame_describer.describe_frames` process them.

New: callers pass `format_behavioral_references(spec)` instead
of all videos. Same filtering/description pipeline runs but
only on declared behavioral targets.

Output: frame descriptions land in `.duplo/frame_descriptions`
(unchanged) and are used for verification case extraction
(unchanged). No SPEC.md write-back from this stage.


### `pdf_extractor.py` and text/markdown docs

Today: `extract_pdf_text(pdfs)` extracts text from any PDFs.
No handling for text or markdown reference files.

New: a `docs_text_extractor` function takes references with
`docs` in `roles` and produces a single text blob per file,
routed by extension:

- `.pdf` — existing `extract_pdf_text` path.
- `.txt` — read directly.
- `.md` — read directly (markdown is text; the LLM handles
  formatting).

The combined text feeds into feature extraction the same way
as today's PDF text. This closes a gap from the previous
design where `ref/notes.md` would be classified as `docs` but
then ignored by the pipeline.


### `verification_extractor.py`

Today: `extract_verification_cases(frame_descriptions)` parses
input/output pairs from frame descriptions.

New: input source unchanged (frame descriptions from
`behavioral-target` videos). Output unchanged (verification
tasks appended to PLAN.md via `format_verification_tasks`).

Adding a parallel input source: `## Behavior` section's
`behavior_contracts` get formatted via
`format_contracts_as_verification` (existing) and merged with
video-derived cases. This already works today; no change.


### `extractor.py` (feature extraction)

Today: `extract_features(scraped_text, ...)` takes raw scraped
text plus optional spec_text and scope overrides.

New:
- `scraped_text` becomes the concatenation of text from all
  scrapeable sources (`format_scrapeable_sources` output),
  not just one URL.
- `spec_text` continues to use SPEC.md content via
  `format_spec_for_prompt` — but per PARSER-design.md, that
  function now serializes from dataclasses and excludes
  unreviewed entries. The LLM never sees `proposed:`,
  `discovered:`, or counter-example content.
- `scope_include` / `scope_exclude` continue to come from
  `spec.scope_include` / `spec.scope_exclude`.
- **Scope exclusion is enforced post-extraction, not via
  `existing_names`.** `existing_names` is a name-reuse hint to
  the LLM, not an exclusion filter; using it for exclusion
  fails when the LLM rephrases an excluded concept. Instead,
  the orchestrator filters extracted features against
  `scope_exclude` after extraction, dropping any feature whose
  name OR description contains an excluded term (case-
  insensitive substring match). The LLM is also told via
  `spec_text` what's excluded; the post-filter is belt-and-
  braces.


### `gap_detector.py`

Today: `detect_gaps(plan_content, features, examples, ...)`
finds features missing from PLAN.md and proposes tasks.

New: no change to the gap-detection logic itself. The features
list it operates on is now filtered to exclude
`spec.scope_exclude` items at the orchestrator level before
being passed in. Detection of design gaps (`detect_design_gaps`)
operates on the AUTO-GENERATED block in SPEC.md's `## Design`
section as well as on `duplo.json`'s `design_requirements`
(redundant during transition; can simplify later).


### `investigator.py`

Today: `investigate(bugs, ...)` collects all available context
and sends to Claude.

New: gains role-filtered context:
- `counter-example` references get included in the prompt
  with a "AVOID this pattern" label. Today, counter-examples
  exist nowhere in the pipeline; the new model surfaces them
  here.
- `docs` references (PDF text, doc-role files) get included
  as supplementary context.
- `## Behavior` contracts get included as ground-truth
  expectations.

The investigator's structured-output prompt expands to
acknowledge counter-examples and behavior contracts in its
diagnoses (e.g. `Diagnosis(... contradicts: "behavior contract X")`).


### `main.py` orchestration

The biggest change. The current `_first_run`,
`_subsequent_run`, and `_fix_mode` functions are restructured
to consume role-filtered inputs from the parser:

```python
def _subsequent_run() -> None:
    spec = read_spec()
    if spec is None:
        # No SPEC.md → tell user to run `duplo init`.
        ...
        return

    errors = validate_for_run(spec)
    if errors:
        for e in errors:
            print(e)
        sys.exit(1)

    # File-change detection (unchanged) ...

    # Re-scrape declared sources. Collect raw pages for site-media
    # extraction and cross-origin discoveries for SPEC.md write-back.
    raw_pages: dict[str, str] = {}        # url -> HTML, for product-reference sources
    discovered_urls: list[str] = []       # cross-origin links found during deep crawl
    combined_text = ""
    for source in format_scrapeable_sources(spec):
        scraped_text, code_examples, doc_structures, page_records, source_raw_pages = (
            fetch_site(source.url, scrape_depth=source.scrape)
        )
        combined_text += scraped_text + "\n"
        if source.role == "product-reference":
            raw_pages.update(source_raw_pages)
        discovered_urls.extend(_collect_cross_origin_links(source_raw_pages, source.url))

    # Append discovered URLs to ## Sources with discovered: true.
    # `append_sources` deduplicates against existing entries.
    if discovered_urls:
        existing = (Path.cwd() / "SPEC.md").read_text()
        modified = append_sources(existing, [
            SourceEntry(url=u, role="docs", scrape="deep", discovered=True)
            for u in discovered_urls
        ])
        (Path.cwd() / "SPEC.md").write_text(modified)

    # Re-extract features.
    features = extract_features(
        combined_text,
        existing_names=existing_names,
        spec_text=format_spec_for_prompt(spec),
        scope_include=spec.scope_include,
        scope_exclude=spec.scope_exclude,
    )
    # Post-extraction scope_exclude filter (see extractor.py section).
    features = [f for f in features if not _matches_excluded(f, spec.scope_exclude)]
    save_features(features)

    # Behavioral references → frame extraction → verification cases.
    behavioral_entries = format_behavioral_references(spec)
    behavioral_paths = [e.path for e in behavioral_entries]
    video_results = extract_all_videos(behavioral_paths)  # existing pipeline
    # ... frame_filter, frame_describer, verification_extractor ...
    # Map each behavioral entry's path to its accepted frames for the
    # dual-use lookup below.
    accepted_frames_by_path: dict[Path, list[Path]] = _accepted_frames_by_source(video_results)

    # Compose the design extraction input set from THREE sources.
    # All three return Paths; extract_design takes list[Path].
    #   1. visual-target reference files in ref/
    #   2. accepted frames from videos that include visual-target in their roles
    #   3. images downloaded from product-reference sources via _download_site_media
    visual_paths = [e.path for e in format_visual_references(spec)]
    visual_video_frames = [
        frame
        for entry in behavioral_entries
        if "visual-target" in entry.roles
        for frame in accepted_frames_by_path.get(entry.path, [])
    ]
    site_images, _site_videos = _download_site_media(raw_pages) if raw_pages else ([], [])
    design_input = visual_paths + visual_video_frames + site_images

    if design_input:
        design = extract_design(design_input)
        existing = (Path.cwd() / "SPEC.md").read_text()
        modified = update_design_autogen(existing, format_design_block(design))
        (Path.cwd() / "SPEC.md").write_text(modified)
        save_design_requirements(dataclasses.asdict(design))  # cache

    # Phase planning (unchanged from today).
    ...
```

`_first_run` is bypassed for new SPEC-based projects during
Phase 2 (when `_subsequent_run` learns to read SPEC.md and
existing projects keep using the old `_first_run` path) and
removed entirely in Phase 5. After Phase 5, first-run setup
is `duplo init`, and running `duplo` with no SPEC.md prints
an error: "No SPEC.md found. Run `duplo init` to set up the
project."


## State reconciliation

A subtlety: SPEC.md and `.duplo/duplo.json` both contain
state that needs to stay in sync. The new model resolves
this with a clear ownership split:

| Data                    | Owner    | Stored in                               |
|-------------------------|----------|-----------------------------------------|
| Product purpose         | User     | SPEC.md `## Purpose`                    |
| Product identity        | duplo    | `.duplo/product.json` (cache)           |
| Source URLs (declared)  | User     | SPEC.md `## Sources`                    |
| URLs scraped (record)   | duplo    | `.duplo/reference_urls/`                |
| Reference files         | User     | `ref/` + SPEC.md `## References`        |
| Derived ref artifacts   | duplo    | `.duplo/references/` (frames, copies)   |
| Architecture choices    | User     | SPEC.md `## Architecture`               |
| Design (user prose)     | User     | SPEC.md `## Design` (above autogen)     |
| Design (extracted)      | duplo    | SPEC.md autogen + `.duplo/duplo.json`   |
| Scope overrides         | User     | SPEC.md `## Scope`                      |
| Behavior contracts      | User     | SPEC.md `## Behavior`                   |
| Features (extracted)    | duplo    | `.duplo/duplo.json`                     |
| Features (status)       | duplo    | `.duplo/duplo.json` (set on completion) |
| Roadmap                 | duplo    | `.duplo/duplo.json`                     |
| Phase history           | duplo    | `.duplo/duplo.json`                     |
| Issues                  | duplo    | `.duplo/duplo.json`                     |

The rule: SPEC.md describes intent (user-owned); `.duplo/`
describes state (duplo-owned). When duplo writes to SPEC.md
(append sources/references, update autogen design), it does so
with explicit markers (`proposed:`, `discovered:`, `BEGIN
AUTO-GENERATED`) that the user can review.


## What goes away

A few existing behaviors that don't carry forward:

1. **Crawling URLs from arbitrary text files in the project
   root.** Replaced by explicit `## Sources`. Old behavior
   was the source of the README warning ("be careful with
   files containing URLs"); new behavior makes URLs explicit.

2. **Vision on all images in the project root.** Replaced by
   Vision on `visual-target` references in `ref/`. Files
   that aren't in `ref/` aren't seen by Vision.

3. **Moving processed reference files into
   `.duplo/references/`.** Files stay in `ref/` where the
   user put them. `.duplo/references/` becomes derived-only.

4. **Inferring relevance from image dimensions.** Roles are
   declared, not inferred from file properties.

5. **The `_PROJECT_FILES` exclusion list in `main.py`.** No
   longer needed because we only scan `ref/`, not the project
   root.

6. **Interactive feature selection on first run.** Replaced
   by SPEC.md's `## Scope include/exclude` written by the
   user before `duplo` runs feature extraction. The
   `select_features` interactive prompt still exists for
   subsequent-run "what features for next phase" — that's
   different and stays.

7. **Interactive product confirmation.** Replaced by
   `duplo init` writing the product identity into
   `.duplo/product.json` non-interactively (drafted from
   scrape, confirmed by user editing SPEC.md).

8. **`ask_preferences` interactive flow on first run.**
   Replaced by SPEC.md's `## Architecture` written by user.
   `BuildPreferences` dataclass still exists internally;
   it's populated by parsing `## Architecture` content
   rather than by Q&A. See "BuildPreferences and app_name"
   below for the parsing rule.


## BuildPreferences and app_name

The current pipeline depends on two pieces of structured data
that have no obvious home in the new SPEC.md schema:

1. **BuildPreferences** — `platform`, `language`, `framework`,
   `dependencies`, and other constraints. Today populated by
   `ask_preferences` interactive Q&A and stored in
   `.duplo/duplo.json` under `preferences`.
2. **app_name** — the name of the built application, used by
   `_complete_phase` for screenshot capture (`appshot.capture`
   needs the running app's name to find its window).

Neither is captured cleanly by `## Architecture` (which is
free-form prose) or `## Purpose` (which is the product being
cloned, not the app being built).

The resolution:

### BuildPreferences

Parsed from `## Architecture` prose by an LLM call during
first `duplo` run after init (cached in `.duplo/duplo.json`
thereafter, re-parsed when `## Architecture` changes per
file-hash detection):

```python
def parse_build_preferences(architecture_prose: str) -> BuildPreferences:
    """Parse free-form ## Architecture into structured fields.

    Calls Claude with a structured-output prompt asking for
    {platform, language, framework, dependencies: list[str],
     other_constraints: list[str]} extracted from the prose.

    Returns BuildPreferences with whatever fields the LLM
    could populate. Missing fields stay at default values.
    """
```

The parsed result is cached in `duplo.json` under `preferences`.
File-hash tracking detects when `## Architecture` changes and
triggers re-parsing. The LLM call is small and runs once per
spec edit; cost is negligible.

### app_name

Not directly representable in SPEC.md — it's a build artifact
property, not user intent. Captured at first `duplo` run after
init:

- If `## Sources` includes a product-reference URL, derive a
  candidate app_name from the scraped product identity
  (existing `validator.validate_product_url` behavior).
- If no URL, derive from the project directory name as a
  fallback (e.g. `numi-clone/` → `numi-clone`).
- Stored in `.duplo/product.json` under `app_name`. The user
  can edit this file directly if the auto-derived name is
  wrong; duplo doesn't surface it in SPEC.md because it's not
  intent.

This matches the open-question resolution to keep
`.duplo/product.json` as a cache: it's the right home for
build-artifact metadata that the user shouldn't have to
maintain in SPEC.md.


## Multi-source persistence

The current pipeline centers on a single `source_url` stored
in `duplo.json` and `product.json`. The new model allows
multiple URLs in `## Sources`. Persistence updates:

- `.duplo/duplo.json` gains a `sources` field: a list of
  `{url, last_scraped, content_hash, scrape_depth_used}`
  entries, one per scrapeable source.
- `.duplo/raw_pages/` (existing) continues to store scraped
  HTML keyed by URL hash. No schema change.
- `.duplo/reference_urls/` (existing) continues to track
  same-origin discovered URLs from deep crawls. No schema
  change.
- `.duplo/product.json` keeps the single `source_url` field
  for backward compatibility, populated from the first
  product-reference entry in `## Sources`. New code reads from
  the spec, not from `product.json`. The field is preserved
  only so old tooling and migration detection keep working.

When the user adds a new URL to `## Sources`, the next `duplo`
run scrapes it and adds an entry to `duplo.json`'s `sources`
list. When the user removes a URL from `## Sources`, the
entry stays in `duplo.json` (idempotent state), but the
pipeline doesn't re-scrape and doesn't include the cached
content in subsequent extractions. Users who want to purge
cached content delete `.duplo/raw_pages/<hash>` manually.


## Backward compatibility during transition

Two-phase transition:

**Phase 1 (during this redesign):** New code paths exist
alongside old. Old projects get a printed migration message
on first `duplo` invocation (per `MIGRATION-design.md`) and
exit; the user manually creates `ref/`, moves files, and
runs `duplo init`. After manual migration, projects use the
new code paths exclusively.

**Phase 2 (after a release or two):** Remove old code paths
(the original `_first_run`, the URL-in-text-file scanning,
the file-relevance heuristics). At that point, only the new
model is supported. Pre-migration projects that still exist
still get the same manual-migration message.

Phase 2 isn't part of the immediate redesign. It happens
when we're confident the new model has shaken out.


## Test plan

Each pipeline stage gets new unit tests around role-filtered
input:

1. `scanner` only sees files in `ref/`.
2. `fetcher` respects `scrape_depth` parameter.
3. `extract_design` only sees `visual-target` references
   (excluding `proposed: true`).
4. `extract_all_videos` only sees `behavioral-target`
   references.
5. `pdf_extractor` only sees `docs` references.
6. `extractor` correctly merges scraped text from multiple
   sources with the spec.
7. `gap_detector` excludes `scope_exclude` features.
8. `investigator` includes counter-example references with
   "avoid" labeling.

Integration tests at the orchestrator level:

1. End-to-end run with URL-only SPEC.md → produces correct
   PLAN.md without consulting `ref/` (which is empty).
2. End-to-end run with `ref/`-only SPEC.md → produces
   correct PLAN.md without making any HTTP requests.
3. End-to-end run with both → both contribute to the plan;
   conflicts resolve in favor of explicit content over
   inference.
4. Subsequent run with new files added to `ref/` → SPEC.md
   gets new `proposed: true` entries appended; pipeline
   proceeds without acting on the new files.
5. After user removes `proposed: true`, next run uses the
   files in their pipeline stages.


## Implementation order

Pipeline integration (Phase 2) consumes the parser from
Phase 1; drafter/init (Phase 3) and migration (Phase 4) come
later and are not preconditions. The cleanup work in this
section that depends on `_first_run` removal lands in Phase 5.
Within Phase 2, the work is mostly:

1. Add `scrape_depth` parameter to `fetch_site`. Tests.
2. Add per-stage formatters in `spec_reader` (already in
   PARSER-design.md). Tests.
3. Refactor `scanner.scan_directory` to point at `ref/`.
   Update existing callers. Tests.
4. Update `extract_design` callers to use
   `format_visual_references`. Tests.
5. Update video pipeline callers to use
   `format_behavioral_references`. Tests.
6. Update PDF extractor callers to use docs-role filter.
   Tests.
7. Update `extract_features` callers to consume merged
   scraped text from multiple sources. Tests.
8. Wire SPEC.md write-back into `_subsequent_run`:
   discovered URLs → `append_sources`, design extraction
   → `update_design_autogen`. Tests.
9. Update investigator to include counter-examples and
   behavior contracts. Tests.
10. Restructure `_subsequent_run` and `_fix_mode` to use
    the new orchestration shape. Tests, including the
    integration tests above.
11. Remove `_first_run` and direct users to `duplo init`.
    Tests.

This is the largest implementation phase by far. Worth
breaking into sub-phases when writing the actual PLAN.md
for mcloop.


## Open questions

1. **The `duplo.json` design_requirements cache.** Resolved:
   keep it during transition. Removing it requires updating
   `gap_detector.detect_design_gaps` to read from the
   AUTO-GENERATED block in SPEC.md instead, which is a
   non-trivial refactor better deferred to Phase 2.

2. **The order of write-backs vs. extraction.** Resolved:
   wait. Discovered URLs are appended with `discovered: true`
   and are NOT scraped in the current run. The user reviews,
   removes the flag, and the next `duplo` run scrapes them.
   This means a fresh project takes two `duplo` runs to
   fully crawl across origins; same-origin links during deep
   crawl are scraped immediately so a single-domain site
   still completes in one run.

3. **`_fix_mode` integration with new model.** No structural
   change. The new investigator includes counter-examples
   and behavior contracts; existing `_fix_mode` tests should
   continue to pass with those added sources.

4. **Gap detection lifecycle with scope_exclude.** Already
   covered: features filtered through `scope_exclude` before
   being passed to gap detection. Tests pin this.

5. **Prose-only specs (no `## Sources`, no `ref/` files).**
   Confirmed wanted. The validator allows this when
   `## Purpose` is substantive (>50 chars heuristic in
   `validate_for_run`). Feature extraction proceeds with
   `spec_text` as the only input. Output is sparser but
   usable for behaviorally-specified apps (CLIs, libraries,
   backend services).
