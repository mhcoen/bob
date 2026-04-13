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

#### Return signature

`fetch_site` today returns a 4-tuple of
`(scraped_text, code_examples, doc_structures, page_records)`.
Under the new model it returns a 5-tuple, adding `raw_pages`
as the final element:

```python
def fetch_site(
    url: str,
    *,
    scrape_depth: Literal["deep", "shallow", "none"] = "deep",
) -> tuple[str, list[CodeExample], DocStructures, list[PageRecord], dict[str, str]]:
    """Fetch *url* and return extracted artifacts plus raw HTML.

    The returned raw_pages dict maps EVERY fetched URL (entry
    point plus any same-origin links followed during deep crawl)
    to its HTML content. This is the substrate that the
    orchestrator uses for site-media extraction and
    cross-origin link collection.
    """
```

Key properties of `raw_pages`:

- For `scrape_depth="shallow"`: contains exactly one entry
  (the entry URL) on success; empty dict on fetch failure.
- For `scrape_depth="deep"`: contains the entry URL plus
  every same-origin page that was followed and successfully
  fetched. Cross-origin pages are NOT in this dict (they
  weren't fetched per the same-origin restriction).
- For `scrape_depth="none"`: empty dict (no fetch happened).
- Keys are the absolute URLs actually fetched (post-redirect,
  canonicalized via `canonicalize_url`).
- Values are the raw HTML bytes decoded as UTF-8 with
  error replacement.

**Failed fetches are NOT included in `raw_pages`.** If a
same-origin link returns 404, times out, has a non-HTML
content-type, or fails to decode, the fetcher records the
failure via `record_failure("fetch_site", "fetch", ...)`
and omits the URL from both `raw_pages` AND `page_records`.
The two structures stay in sync: for every URL in
`raw_pages`, there is a corresponding `PageRecord`; for
every `PageRecord`, the URL is in `raw_pages`. This makes
the `save_raw_content` invariant (keys match `record.url`
exactly) hold by construction. A fetch failure is visible
to the user through diagnostics but silent to downstream
pipeline stages — they simply see one fewer page, which
is the correct behavior (they can't do anything about a
network failure).

The orchestrator uses `raw_pages` for three purposes:
1. `_collect_cross_origin_links(raw_pages, source_url)` —
   extract cross-origin `<a href>` targets from every fetched
   page. This means cross-origin links on followed same-origin
   pages are discovered, not just links on the entry page.
2. `_download_site_media(product_ref_raw_pages)` — download
   embedded `<img>`, `<video>`, `<source>` media from
   product-reference pages.
3. `save_raw_content(all_raw_pages, page_records)` — cache
   HTML to `.duplo/raw_pages/` keyed by canonical-URL hash
   (see `save_raw_content` signature below for the full
   rule and rationale; content hash is stored inside each
   `PageRecord` for change detection, NOT used as the
   cache-file filename).

Today's `fetch_site` already accumulates per-page HTML
internally for content-hash computation in `PageRecord`. The
change is to expose that accumulation as a return value
rather than discarding it after hashing.


## URL canonicalization

Multiple consumers operate on URLs: `raw_pages` keys,
`PageRecord.url`, `_collect_cross_origin_links` output,
`append_sources` dedup, `.duplo/raw_pages/` cache keys,
`save_raw_content`'s lookup. If any two of these use different
normalization rules, state goes inconsistent silently. This
section pins the one canonical form used everywhere.

**The canonical form** (applied to every URL at the point it
enters duplo state — scraped URLs, href-extracted URLs, and
user-authored `## Sources` URLs alike):

```python
def canonicalize_url(url: str) -> str:
    """Normalize a URL to its canonical duplo form.

    1. Lowercase scheme and host.
    2. Strip default ports (80 on http, 443 on https).
    3. Strip fragment (#section).
    4. Strip trailing slash from ALL paths, including the
       root path "/". That is: https://a.com/ → https://a.com,
       https://a.com/docs/ → https://a.com/docs,
       https://a.com/docs → https://a.com/docs (unchanged).
    5. Preserve query strings (different queries are different
       resources).
    """
```

**Why strip all trailing slashes, including root.** Deep-crawl
output routinely contains both `/docs/` and `/docs` variants
of the same page (the site's canonical link tags go one way,
inline `<a href>` tags go the other). Without uniform
trailing-slash stripping, dedup in `_collect_cross_origin_links`
and `append_sources` sees these as distinct, both pass through,
SPEC.md grows an append entry on every run. This is exactly
the failure mode dedup was designed to prevent. The root-path
slash is syntactic rather than a path segment, but it needs
the same treatment so `https://a.com/` and `https://a.com`
compare equal — otherwise dedup fails at the most common
case (the user-authored host-only URL versus the fetcher's
post-redirect URL).

**Where canonicalization is applied:**

1. `fetch_site` returns `raw_pages` with canonicalized URLs
   as keys, AND produces `PageRecord` objects whose `url`
   field is the canonicalized form. These two structures
   therefore agree by construction; callers can look up HTML
   by `record.url` without translation.
2. `fetch_site` follows redirects; the final (post-redirect)
   URL is canonicalized and used as the key.
3. `_collect_cross_origin_links` canonicalizes each extracted
   `<a href>` target before the cross-origin check and before
   deduplication.
4. `append_sources` canonicalizes incoming `SourceEntry.url`
   values before comparing against existing SPEC.md entries.
   Existing entries in SPEC.md are canonicalized on read by
   the parser (another point-of-entry).
5. The parser's `## Sources` entry validator canonicalizes
   each URL into the stored `SourceEntry`. User-authored
   `https://numi.app/` and duplo-written `https://numi.app`
   compare equal.
6. `.duplo/raw_pages/` uses the SHA-256 of the canonical URL
   as the filename key. `PageRecord.content_hash` continues
   to hash the HTML body (it's a content hash, not a URL
   hash). The two hashes are independent:
   `<url_hash>.html` identifies the cache slot;
   `content_hash` inside the record detects changes.

**Source URL overlap handling.** With canonicalization in
place, a multi-source spec that includes both
`https://numi.app` and `https://numi.app/docs` may still
deep-crawl into overlapping same-origin territory (both can
reach `https://numi.app/about`). The orchestrator's
accumulation is therefore dedup-by-canonical-URL:

```python
for source in format_scrapeable_sources(spec):
    scraped_text, code_examples, doc_structures, page_records, source_raw_pages = (
        fetch_site(source.url, scrape_depth=source.scrape)
    )
    combined_text += scraped_text + "\n"
    all_code_examples.extend(code_examples)
    # Dedup page_records by canonical URL: first source to yield
    # a page for a given URL wins; subsequent duplicate records
    # are dropped. Prevents two PageRecord entries for one URL
    # (which would produce inconsistent .duplo/reference_urls/
    # state when save_reference_urls serializes them).
    for record in page_records:
        if record.url not in seen_canonical_urls:
            all_page_records.append(record)
            seen_canonical_urls.add(record.url)
    # Similarly dedup raw_pages: dict.update overwrites, so
    # subsequent sources would silently win. Use setdefault
    # to preserve first-seen content for a given canonical URL.
    for url, html in source_raw_pages.items():
        all_raw_pages.setdefault(url, html)
        if source.role == "product-reference":
            product_ref_raw_pages.setdefault(url, html)
    # ... doc_structures merge, discovered_urls.extend ...
```

**First-source-wins rationale.** Within a single run, a page
fetched twice within the same second will yield identical
HTML (the network doesn't change in milliseconds). The
concerns are ordering stability (same canonical URL should
produce the same record on every run) and single-writer
discipline for the cache (one HTML body per URL, one
`PageRecord` per URL). First-source-wins provides both:
SPEC.md source order is stable across runs, so whichever
source yields a URL first in run N yields it first in run
N+1, producing stable cache state.


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
4. Accepted frames from scraped product-reference VIDEOS
   (also via `_download_site_media`). Scraped demo videos
   flow through `extract_all_videos` → frame filter → frame
   describer the same way ref/-declared videos do, and
   accepted frames feed both verification case extraction
   and design extraction. This preserves today's behavior
   where a marketing demo video on a product page produces
   verification cases and design frames without the user
   needing to download and ref/-declare it.

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

#### `_download_site_media` signature under the new model

Today's `_download_site_media` lives in the fetcher/pipeline
glue and pulls images and videos from scraped pages. Under
the new model its signature becomes explicit:

```python
def _download_site_media(
    raw_pages: dict[str, str],
) -> tuple[list[Path], list[Path]]:
    """Collect embedded <img>, <video>, and <source> media paths
    for the HTML of product-reference pages.

    Returns (image_paths, video_paths) where each list contains
    LOCAL PATHS TO ALL EMBEDDED MEDIA — both files newly
    downloaded during this call AND files already present in
    the cache from previous runs. Callers receive a complete
    media inventory regardless of cache state.

    Media files live in .duplo/site_media/<url-hash>/<filename>.
    On a cache hit, no HTTP fetch happens; the existing path
    is returned unchanged. On a cache miss, the file is
    downloaded and the new path is returned. Either way, the
    function reads every <img>/<video>/<source> tag in each
    page and yields a path for every referenced resource that
    exists locally (cached or newly downloaded).
    """
```

**Cached-vs-new rule.** The orchestrator must see a complete
media inventory on every run, not just "what got downloaded
this time." Consider: a URL-only project's second run. All
media is cached; nothing new to download. If
`_download_site_media` returned only newly-downloaded paths,
`design_input` would be empty on that second run. Then if the
user deletes the autogen block to regenerate, the Vision call
runs against zero inputs and produces nothing. The cache
becomes a trap that silently breaks regeneration.

The fix: `_download_site_media` returns paths for every
embedded media resource that exists locally, whether it was
downloaded this call or previously. Callers can't tell the
difference, which is what they want.

Key changes from today:

- Parameter is a `dict[str, str]` mapping URL to HTML rather
  than a single page's HTML. This matches the multi-source
  model — a deep crawl produces multiple pages, each of which
  may contain embedded media.
- Return is a tuple `(images, videos)` where BOTH are used by
  the orchestrator. Videos are no longer discarded; they feed
  `extract_all_videos` alongside ref/-declared behavioral
  targets.
- Return includes cached paths, not just newly-downloaded
  paths. See the cached-vs-new rule above.
- Callers pass only `product_reference_raw_pages` (not the
  full `all_raw_pages`), matching the existing rule that only
  product-reference sources contribute to design and
  behavioral extraction.

Embedded-media origin handling follows "Same-origin and
embedded media" below: media is downloaded regardless of
origin because the page that embeds it was user-authorized.

#### `format_design_block` location

`format_design_block(design) -> str` is the serializer that
produces the markdown body for the AUTO-GENERATED block in
SPEC.md's `## Design` section. It's specified in the
orchestrator-helpers section, but its IMPLEMENTATION LOCATION
is `design_extractor.py`, not `spec_drafter.py`.

Rationale: `format_design_block` wraps the existing
`format_design_section(design)` already in
`design_extractor.py`, minus the section heading. Placing
`format_design_block` in `spec_drafter.py` would create a
drafter → pipeline import inversion (the drafter is supposed
to be a text-layer module independent of pipeline stages).
Keeping it in `design_extractor.py` preserves clean layering:
the orchestrator calls `design_extractor.format_design_block`,
then passes the resulting string into
`spec_drafter.update_design_autogen(existing, body)`. The
drafter sees only a string; it doesn't depend on the design
dataclass or on the extraction pipeline.


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
  `scope_exclude` after extraction using the `_matches_excluded`
  helper (see orchestrator-helpers section for the full spec).
  Matching is **case-insensitive word-boundary regex**, not
  substring: an excluded term `"plugin API"` matches
  `"Plugin API"` and `"plugin API."` but not
  `"non-plugin-API"` or a feature description that merely
  mentions `"plugin API"` as contrast. Word-boundary matching
  avoids the false positives that substring matching produces.
  The LLM is also told via `spec_text` what's excluded; the
  post-filter is belt-and-braces.


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
- `counter-example` references (files in `ref/` with role
  `counter-example`, via `format_counter_examples(spec)`) get
  included in the prompt with a "AVOID this pattern" label.
  Today, counter-examples exist nowhere in the pipeline; the
  new model surfaces them here.
- `counter-example` SOURCES (URLs in `## Sources` with role
  `counter-example`, via `format_counter_example_sources(spec)`)
  get included as URL+notes context with the same "AVOID"
  framing. The URL is NOT fetched — declarative context only.
  This closes the gap where counter-example URLs would
  otherwise be inert (filtered out of scraping AND out of
  `format_spec_for_prompt`, with no consumer).
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
    # extraction, code examples and doc structures from all sources,
    # cross-origin discoveries for SPEC.md write-back, and full raw
    # HTML for the .duplo/raw_pages/ cache. Per "URL canonicalization"
    # above, dedup page records and raw_pages by canonical URL using
    # first-source-wins to keep cache state consistent when multiple
    # sources deep-crawl into overlapping same-origin territory.
    all_raw_pages: dict[str, str] = {}          # canonical url -> HTML, EVERY page
    product_ref_raw_pages: dict[str, str] = {}  # canonical url -> HTML, product-ref subset
    discovered_urls: list[str] = []             # cross-origin links from deep crawl
    seen_canonical_urls: set[str] = set()       # dedup guard for page_records
    combined_text = ""
    all_code_examples: list = []
    all_page_records: list = []
    merged_doc_structures = DocStructures()
    for source in format_scrapeable_sources(spec):
        scraped_text, code_examples, doc_structures, page_records, source_raw_pages = (
            fetch_site(source.url, scrape_depth=source.scrape)
        )
        combined_text += scraped_text + "\n"
        all_code_examples.extend(code_examples)
        # First-source-wins dedup for PageRecord and raw HTML.
        for record in page_records:
            if record.url not in seen_canonical_urls:
                all_page_records.append(record)
                seen_canonical_urls.add(record.url)
        for url, html in source_raw_pages.items():
            all_raw_pages.setdefault(url, html)
            if source.role == "product-reference":
                product_ref_raw_pages.setdefault(url, html)
        if doc_structures:
            merged_doc_structures.feature_tables.extend(doc_structures.feature_tables)
            merged_doc_structures.operation_lists.extend(doc_structures.operation_lists)
            merged_doc_structures.unit_lists.extend(doc_structures.unit_lists)
            merged_doc_structures.function_refs.extend(doc_structures.function_refs)
        # Cross-origin discovery is a deep-crawl behavior only.
        # Shallow sources fetched just the entry URL; collecting
        # cross-origin links from a shallow page and recording
        # them as `discovered: true` would silently append URLs
        # the user never asked duplo to explore. Skip discovery
        # for non-deep sources.
        if source.scrape == "deep":
            discovered_urls.extend(
                _collect_cross_origin_links(source_raw_pages, source.url)
            )
    if all_code_examples:
        save_examples(all_code_examples)
    if all_page_records:
        save_reference_urls(all_page_records)
        if all_raw_pages:
            # save_raw_content writes each page HTML to
            # .duplo/raw_pages/<sha256(canonical_url)>.html. Because
            # raw_pages keys and record.url are both canonical (per
            # URL canonicalization above), the lookup by record.url
            # succeeds for every record.
            save_raw_content(all_raw_pages, all_page_records)
    if merged_doc_structures:
        save_doc_structures(merged_doc_structures)

    # Append discovered URLs to ## Sources with discovered: true.
    # `append_sources` deduplicates against existing entries; only
    # write SPEC.md if the content actually changed.
    if discovered_urls:
        existing = (Path.cwd() / "SPEC.md").read_text()
        modified = append_sources(existing, [
            SourceEntry(url=u, role="docs", scrape="deep", discovered=True)
            for u in discovered_urls
        ])
        if modified != existing:
            (Path.cwd() / "SPEC.md").write_text(modified)

    # Re-extract features.
    features = extract_features(
        combined_text,
        existing_names=existing_names,
        spec_text=format_spec_for_prompt(spec),
        scope_include=spec.scope_include,
        scope_exclude=spec.scope_exclude,
    )
    # Post-extraction scope_exclude filter. Uses word-boundary
    # regex matching via _matches_excluded (see helper spec);
    # drops produce diagnostics so false positives are visible.
    features = [f for f in features if not _matches_excluded(f, spec.scope_exclude)]
    save_features(features)

    # Download site media from product-reference pages. Returns BOTH
    # images (for design extraction) and videos (for behavioral pipeline).
    # Scraped videos are first-class behavioral input — the URL-only
    # common pattern relies on demo videos producing verification cases.
    site_images, site_videos = (
        _download_site_media(product_ref_raw_pages)
        if product_ref_raw_pages else ([], [])
    )

    # Behavioral references → frame extraction → verification cases.
    # Behavioral input is the union of ref/-declared behavioral targets
    # AND scraped videos from product-reference pages. Source paths
    # must be unique (ref/ and .duplo/site_media/ live under different
    # roots, so path collisions require a user error); an assertion
    # guards the invariant before the per-source lookup is built.
    behavioral_entries = format_behavioral_references(spec)
    behavioral_paths = [e.path for e in behavioral_entries] + site_videos
    assert len(behavioral_paths) == len(set(behavioral_paths)), (
        "Duplicate source path across ref-declared and scraped videos"
    )
    video_results = extract_all_videos(behavioral_paths)  # list[ExtractionResult]
    # Filter rejected frames (transitions, blur, marketing overlays)
    # BEFORE building the per-source lookup. Rejected frames must NOT
    # flow into design extraction for dual-role videos. Reuse the
    # existing ExtractionResult dataclass; replace the frames list
    # with the filtered subset, preserving source and error fields.
    filtered_results = [
        dataclasses.replace(r, frames=apply_filter(filter_frames(r.frames)))
        for r in video_results
    ]
    # ... frame_describer, verification_extractor on filtered_results ...
    accepted_frames_by_path: dict[Path, list[Path]] = (
        _accepted_frames_by_source(filtered_results)
    )

    # Compose the design extraction input set from FOUR sources.
    # All return Paths; extract_design takes list[Path].
    #   1. visual-target reference files in ref/
    #   2. accepted frames from videos that include visual-target in their roles
    #      (ref/-declared dual-role videos)
    #   3. accepted frames from scraped product-reference videos
    #      (always contribute; no role declaration possible)
    #   4. images downloaded from product-reference sources
    visual_paths = [e.path for e in format_visual_references(spec)]
    visual_video_frames = [
        frame
        for entry in behavioral_entries
        if "visual-target" in entry.roles
        for frame in accepted_frames_by_path.get(entry.path, [])
    ]
    scraped_video_frames = [
        frame
        for video_path in site_videos
        for frame in accepted_frames_by_path.get(video_path, [])
    ]
    # Dedup frames by content hash before composing design_input.
    # A user who ref/-declares a local copy of a demo video that
    # also appears on a scraped product page will otherwise have
    # frames from both copies counted twice: once via
    # visual_video_frames (ref/ dual-role path) and once via
    # scraped_video_frames (site_media path). The frames are
    # different files (different paths) but describe identical
    # visual content, so path-based dedup doesn't help — content
    # hash does. ref-declared frames win on collision (the user
    # declared them; their roles carry more intent than a
    # scraped-page auto-inclusion).
    seen_frame_hashes: set[str] = set()
    deduped_visual_video_frames: list[Path] = []
    for frame in visual_video_frames:
        h = hashlib.sha256(frame.read_bytes()).hexdigest()
        if h not in seen_frame_hashes:
            deduped_visual_video_frames.append(frame)
            seen_frame_hashes.add(h)
    deduped_scraped_video_frames: list[Path] = []
    for frame in scraped_video_frames:
        h = hashlib.sha256(frame.read_bytes()).hexdigest()
        if h not in seen_frame_hashes:
            deduped_scraped_video_frames.append(frame)
            seen_frame_hashes.add(h)
    design_input = (
        visual_paths
        + deduped_visual_video_frames
        + deduped_scraped_video_frames
        + site_images
    )

    # Check autogen block FIRST via the in-memory dataclass (NOT a
    # second disk read or a second regex pass). The parser already
    # produced spec.design.auto_generated; consulting it here keeps
    # the in-memory spec as the single source of truth and avoids
    # maintaining two redundant block-detection implementations.
    # If present, skip the Vision call entirely — update_design_autogen
    # would silently discard the result anyway (write-once-never-replace).
    autogen_present = bool(spec.design.auto_generated.strip())
    if design_input and not autogen_present:
        design = extract_design(design_input)
        existing = (Path.cwd() / "SPEC.md").read_text()
        modified = update_design_autogen(existing, format_design_block(design))
        if modified != existing:
            (Path.cwd() / "SPEC.md").write_text(modified)
        save_design_requirements(dataclasses.asdict(design))  # cache
    elif design_input:
        # Autogen block already exists; tell the user we skipped
        # extraction so they know any new visual-target files won't
        # produce new design output until they delete the block.
        # Use category="io" since the existing diagnostics categories
        # are {fetch, screenshot, llm, hash, io} and this is closest
        # (file-state-gated skip). If a dedicated "skipped" category
        # is desired, extending diagnostics.CATEGORIES is a separate
        # Phase 3 prerequisite task.
        record_failure(
            "orchestrator:design_extraction",
            "io",
            "Autogen design block exists in SPEC.md; skipped Vision "
            "extraction. Delete the BEGIN/END AUTO-GENERATED block "
            f"to regenerate from {len(design_input)} input image(s)."
        )

    # Phase planning (unchanged from today).
    ...
```

Note on the in-memory spec dataclass: the `spec` variable from
`read_spec()` at the top of `_subsequent_run` is the source of
truth for pipeline decisions within a single run. SPEC.md is
re-read only to stage writes (the discovered-URL append and the
autogen update), not to drive extraction. Validation, formatter
filtering, and prompt construction all run against the in-memory
dataclass as it existed before any writes. This invariant must
hold even through future refactors: "the spec we validated is
the spec we acted on."

Note on the autogen-cache divergence: when the autogen block is
skipped above, `save_design_requirements` is also skipped, so the
`.duplo/duplo.json` cache stays consistent with the SPEC.md autogen
block. The cache and SPEC.md never diverge silently. The cost is
that new visual-target files added after the first design extraction
don't update the cache either — but that's the intended semantics
of write-once-never-replace, surfaced consistently in both stores.


## Orchestrator helper functions

The orchestration sketch above references several helpers that
don't exist yet. They live in `main.py` (or a new
`duplo/orchestrator.py` module if separation feels worthwhile)
because they're orchestration glue, not parser or pipeline
logic. Specs:

### `_collect_cross_origin_links(raw_pages, source_url) -> list[str]`

Given the dict of `{canonical_url: HTML}` returned from a
deep crawl of `source_url`, extract URLs from `<a href="...">`
tags whose resolved absolute URL has a different origin
(scheme + host + port) from `source_url`. Returns a
deduplicated list of canonical-form URLs.

Decisions:
- Only `<a href>` is considered. `<link>`, `<script src>`,
  `<img src>`, and other resource references are NOT collected.
  Resource URLs are loaded by the page itself and don't
  represent user-actionable navigation targets. (This means
  CDN images on a deep-crawled page are downloaded as part of
  `_download_site_media`, not recorded as discovered. See
  "Same-origin and embedded media" below.)
- Every extracted href is resolved to an absolute URL and
  then passed through `canonicalize_url` (see "URL
  canonicalization" above). All downstream comparisons
  (cross-origin check, dedup) operate on canonical form.
  This is the ONLY normalization rule this helper applies;
  it does not maintain its own rule set.
- Cross-origin classification is strict: a link from
  `https://numi.app` to `https://docs.numi.app` is
  cross-origin (different host). A link from `https://numi.app`
  to `https://numi.app/docs` is same-origin.
- Dedup via canonical form happens here and again in
  `append_sources`. Belt and braces; the helper's dedup is
  per-run, `append_sources` is against existing SPEC.md
  content. Both use the same `canonicalize_url`, so a URL
  that passes one dedup and fails the other is impossible
  by construction.

### `_accepted_frames_by_source(filtered_results) -> dict[Path, list[Path]]`

Given a list of `ExtractionResult` objects **after
`frame_filter` has been applied** — each containing a
`source` (input video path) and `frames` (the accepted
frames that survived filtering) — build a lookup table from
source video path to its list of accepted frame paths.

Used in the orchestration sketch to thread frames from videos
with `visual-target` in their roles (or all scraped videos)
into the design extraction input set, while leaving
`behavioral-target`-only ref videos unaffected.

**Critical: the input must be post-filter, not the raw
`extract_all_videos` output.** Today's `extract_all_videos`
returns frames BEFORE `frame_filter.apply_filter` removes
transitions, blur, and marketing overlays. Building the
lookup over unfiltered frames would leak rejected frames into
design extraction for dual-role videos — producing a
degraded `DesignRequirements` where e.g. a frame describing
"the video is loading" contributes as if it were a real UI
state. The orchestration sketch runs `apply_filter` on each
`ExtractionResult.frames` BEFORE calling this helper via
`dataclasses.replace` (preserving `source` and `error`
fields).

**Source-path preservation contract.** `extract_all_videos`
MUST preserve each input path as `ExtractionResult.source`
byte-for-byte — no absolute-path resolution, no symlink
following, no normalization. The lookup in the orchestration
sketch does `accepted_frames_by_path.get(entry.path, ...)`
and `accepted_frames_by_path.get(video_path, ...)`, comparing
dict keys against paths that came from `ReferenceEntry.path`
or `site_videos`. If `extract_all_videos` rewrites paths,
the `.get()` silently returns `[]` and dual-role videos
contribute zero frames — no error, degraded output. Callers
are responsible for passing paths in the form they intend to
look up; `extract_all_videos` is responsible for not
transforming them. A test pins this: pass a relative path,
assert `ExtractionResult.source` equals that same relative
path (not its resolved absolute form).

Implementation is a one-liner (`{r.source: r.frames for r in
filtered_results}`) but lives as a named helper so the
orchestration sketch reads cleanly and so the
post-filter-only contract has a named place to live in tests.

Tests pin: (a) lookup returns correct frames per source; (b)
if called with unfiltered results, rejected frames are
present in the output (demonstrating the contract violation
is detectable); (c) path-preservation as described above.

### ~~`_has_autogen_block`~~ (removed: use `spec.design.auto_generated`)

Earlier drafts specified a helper `_has_autogen_block(spec_text)`
that re-ran the autogen regex against SPEC.md text from disk.
That has been removed. The parser already produces
`spec.design.auto_generated` (a string that is non-empty iff
a well-formed block exists), and the orchestrator uses
`bool(spec.design.auto_generated.strip())` directly. Consulting
the in-memory dataclass is consistent with the "in-memory spec
is source of truth within a single run" invariant stated
above, and avoids maintaining two block-detection
implementations that must stay in lockstep.

### `format_design_block(design) -> str`

Given a `DesignRequirements` dataclass (the return type of
`extract_design`), serialize it to the markdown body that goes
inside the AUTO-GENERATED block in `## Design`. Format matches
the existing `format_design_section(design)` in
`design_extractor.py`, minus the section heading (the heading
belongs to the user-authored part of `## Design` above the
block; the block contains only the body content).

**Lives in `design_extractor.py`, NOT `spec_drafter.py`.** See
`design_extractor.py` § `format_design_block` location above
for the layering rationale. The orchestrator imports
`format_design_block` from `design_extractor`, calls it, and
passes the resulting string into
`spec_drafter.update_design_autogen`. The drafter stays a
text-layer module with no dependency on pipeline stages.

### `_matches_excluded(feature, scope_exclude) -> bool`

Returns True if `feature.name` or `feature.description`
should cause the feature to be dropped per
`spec.scope_exclude`. Replaces a naive substring match (which
produces false positives like "plugin API" excluding a
feature whose description mentions "plugin API" only as
contrast).

Matching rule:
- Compare each excluded term against `feature.name` and
  `feature.description` using **word-boundary regex match**
  (case-insensitive). `\bPLUGIN API\b` matches "Plugin API"
  and "plugin API." but not "non-plugin-API" or
  "plugins'-API".
- For multi-word excluded terms, the entire phrase must
  match as a contiguous word sequence.
- When a feature is dropped, emit a diagnostic via
  `duplo.diagnostics.record_failure` naming the excluded term
  and the feature title:
  `"scope_exclude '<term>' matched feature '<name>'; dropped"`.
  Diagnostics surface in the run summary so false positives
  are visible to the user.

Lives in `main.py` orchestrator section (or `extractor.py`
if the function naturally fits there).

### `format_counter_example_sources(spec) -> list[SourceEntry]`

Returns source entries where `role` is `counter-example`,
excluding `proposed: true` and `discovered: true`. Used by
the investigator to include counter-example URLs as
declarative context ("these URLs are anti-patterns; do not
emulate").

This closes a gap in the previous design where
counter-example URLs were filtered out of
`format_scrapeable_sources` and `format_spec_for_prompt`,
but had no consumer. Without this helper, declaring a URL
as `role: counter-example` would have no effect.

The investigator uses it as follows: for each entry, include
the URL and the `notes:` field (if any) in the prompt's
"counter-examples" section, with framing like "User has
flagged the following URLs as patterns to AVOID:". The URL
itself is not fetched — declarative context only, matching
the "declarative, never scraped" semantics of
counter-example sources.

Lives in `spec_reader.py` alongside the other per-stage
formatters.

### `parse_build_preferences(architecture_prose) -> BuildPreferences`

Specified in "BuildPreferences and app_name" below. Lives in
`questioner.py` (which currently owns the
`ask_preferences` interactive flow being replaced) or a new
`build_prefs.py` module. NOT in `spec_reader.py` — PARSER-design.md
explicitly forbids LLM calls in the parser, and this is an
LLM call.

The invalidation mechanism ("re-parsed when `## Architecture`
changes per file-hash detection") uses a section-scoped hash:
the SHA-256 of `spec.architecture` content (the parsed string
for the section, not the whole SPEC.md file), stored in
`.duplo/duplo.json` under `architecture_hash`. When the hash
changes, re-parse. A whole-file SPEC.md hash would re-parse
on any edit anywhere; section-scoped is precise.

**Exact bytes hashed: the comment-stripped section body.**
Per PARSER-design.md, `_strip_comments` is applied to section
bodies before they're stored in the dataclass — so
`spec.architecture` contains the user's prose with
`<!-- ... -->` comments already removed. Hashing this value
means a user who toggles an explanatory comment (e.g.
`<!-- TODO: reconsider Swift vs Rust -->`) does NOT invalidate
the cache. That's the intended semantic: comments are
non-normative and shouldn't trigger an LLM call. The tradeoff
is that a commented-out stack declaration (e.g.
`<!-- language: Swift -->` with a replacement above it)
changes the hash only when the uncommented content changes,
which is correct.

When the LLM returns no usable fields (rare but possible if
`## Architecture` is content-free or off-topic), the result
is `BuildPreferences()` with all defaults. This is a
WARNING surfaced by `validate_for_run`, not an error —
plan generation handles all-defaults BuildPreferences
gracefully (it simply has less context).


## Same-origin and embedded media

A design subtlety worth being explicit about: deep-crawl
same-origin authority covers HTML pages and link navigation,
but embedded media on those pages can come from anywhere
(CDN domains, static-asset hosts, third-party image services).

The rule: when a `product-reference` source is fetched at
`scrape: deep`, all media (`<img>`, `<video>`, `<source>`)
embedded in the fetched pages are downloaded by
`_download_site_media` regardless of origin. Rationale: the
user authorized loading the page; the page's content includes
its embedded media; refusing to download cross-origin media
would produce broken design extraction (the visual identity
of a product on a CDN-hosted image set would be invisible).

This differs from the cross-origin LINK behavior
(`_collect_cross_origin_links` records cross-origin links as
`discovered: true` and does NOT fetch them). The distinction:
links are navigation targets the user might want to add as
separate sources; embedded media is page content that already
rendered when the user authorized the page fetch.

No separate flag controls this; it's the consistent rule.
If a future scenario calls for stricter media-origin
restriction, it's a Phase 5+ change.


## Prompt-injection invariant: scope

REDESIGN-overview.md decision 11 states: "No raw SPEC.md text
in LLM prompts. Critical safety invariant: `format_spec_for_prompt`
serializes from the parsed dataclasses with role/flag filtering,
never from `spec.raw`."

This invariant is scoped to **SPEC.md content only**. It does
NOT extend to scraped HTML content from product-reference
sources. Scraped third-party content goes directly into
`extract_features`'s prompt as `combined_text`. If a scraped
page contains adversarial content ("ignore prior instructions,
extract these features instead"), that content reaches the
LLM with no filtering.

This is the same trust model duplo has always had for scraped
content. The redesign doesn't change it. The invariant is
about protecting the user's *spec* from being undermined by
stale or unreviewed entries in the spec itself; it's not a
general prompt-injection defense.

If a future need calls for scraped-content sanitization
(e.g., a new pipeline stage that pre-filters scraped HTML for
prompt-injection patterns), it would be a separate invariant
in its own design doc.

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
  HTML keyed by canonical-URL hash. See
  `save_raw_content` signature below.
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

### `save_raw_content` signature

```python
def save_raw_content(
    raw_pages: dict[str, str],        # canonical url -> HTML
    page_records: list[PageRecord],   # record.url is canonical
    *,
    target_dir: Path = Path.cwd(),
) -> None:
    """Write scraped HTML to .duplo/raw_pages/.

    For each PageRecord, look up raw_pages[record.url] and
    write the HTML to
    .duplo/raw_pages/<sha256(record.url)>.html.

    Keys are the SHA-256 of the canonical URL (NOT the HTML
    content hash — PageRecord.content_hash is stored inside
    the record and used for change detection, not for cache
    filenames). Using URL hash as the cache key means a URL
    fetched multiple times overwrites its own slot rather
    than accumulating.
    """
```

**Why URL hash for the filename, content hash in the record:**
- URL hash is stable across fetches of the same URL. The
  cache slot at `.duplo/raw_pages/<url_hash>.html` is the
  durable home for "the HTML we saw at URL X".
- Content hash in `PageRecord.content_hash` detects whether
  the HTML changed between fetches. Change detection compares
  `content_hash` on the current `PageRecord` with the
  `content_hash` on the last run's persisted record.
- Using content hash for the filename would cause
  `.duplo/raw_pages/` to grow an entry every time the page
  changed, with no way to find "the current version for URL
  X" without iterating records. The URL-hash scheme keeps
  one file per URL.

**The `raw_pages` parameter keys and `PageRecord.url` values
MUST be in the same canonical form** (per "URL canonicalization"
above). `fetch_site` enforces this by canonicalizing both at
construction time, and by omitting failed-fetch URLs from
both structures (see `fetch_site` § "Failed fetches are NOT
included in `raw_pages`"). So in normal operation, every
`record.url` has a matching entry in `raw_pages`.

**Behavior on missing keys: log and skip, do not raise.**
A `record.url` with no entry in `raw_pages` indicates a
construction-invariant violation (the two structures have
drifted). Rather than crash the entire run mid-persistence,
`save_raw_content` logs the mismatch via
`record_failure("save_raw_content", "io", ...)` and skips
that record. Remaining records still get persisted; the
orchestrator's reference-urls list stays consistent with
what's in the cache. Rationale: fail-soft here because the
invariant-violation is already a bug — raising would add a
second cascading failure (lost cache writes for unrelated
pages) on top of the first, and the diagnostic is enough
signal for the user and for tests to catch the drift.


## Backward compatibility during transition

Three-phase transition (matches REDESIGN-overview.md numbering):

**Phase 2 (migration detection ships first):** Old projects
get a printed migration message on first `duplo` invocation
(per `MIGRATION-design.md`) and exit. The user manually
creates `ref/`, moves files, and authors SPEC.md by hand.
This lands BEFORE pipeline integration so that the new code
paths in Phase 3 never run against an unmigrated project.

**Phase 3 (pipeline integration):** New code paths exist
alongside old. `_subsequent_run` reads SPEC.md / ref/.
`_first_run` is unchanged — a project with no `.duplo/` and
no SPEC.md hits the old `_first_run` path the way it always
did. Migrated projects (those with SPEC.md and ref/) use
the new code paths exclusively.

**Phase 4 (drafter and `duplo init`):** Manual SPEC.md
authoring is replaced by `duplo init` for new projects. The
migration message from Phase 2 is updated to reference
`duplo init` (one-line change).

**Phase 5 (cleanup):** Remove old code paths (`_first_run`,
URL-in-text-file scanning, file-relevance heuristics). At
that point, only the new model is supported. Pre-migration
projects that still exist still get the migration message.

Phase 5 isn't part of pipeline integration. It happens when
we're confident the new model has shaken out.


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

Pipeline integration is **Phase 3** in the redesign sequence
(parser=1, migration detection=2, pipeline=3, drafter+init=4,
cleanup=5). It consumes the parser from Phase 1 and the
migration check from Phase 2; drafter/init (Phase 4) comes
later and is not a precondition (Phase 3 ships the minimal
drafter write helpers it needs — `append_sources` and
`update_design_autogen` — with the rest deferred to Phase 4).

**`_first_run` removal is NOT part of Phase 3.** It lands in
Phase 5 (cleanup), after `duplo init` exists in Phase 4. If
`_first_run` were removed in Phase 3, projects without a
`.duplo/` directory would have no entry path — they wouldn't
be pre-redesign (so migration wouldn't fire) and they'd have
no SPEC.md (so the new `_subsequent_run` would error). Phase 3
leaves `_first_run` untouched; Phase 5 removes it once
`duplo init` is the documented replacement.

Within Phase 3, the work is:

1. Add `scrape_depth` parameter to `fetch_site` and add the
   new `raw_pages` return value (5-tuple instead of today's
   4-tuple). Tests.
2. Add per-stage formatters in `spec_reader` (already in
   PARSER-design.md). Tests.
3. Refactor `scanner.scan_directory` to point at `ref/`.
   Update existing callers. Tests.
4. Update `extract_design` callers to use
   `format_visual_references`. Tests.
5. Update video pipeline callers to use
   `format_behavioral_references`, AND extend the input set
   with `_site_videos` from `_download_site_media` so scraped
   demo videos still feed frame extraction. Tests.
6. Update PDF extractor callers to use docs-role filter.
   Tests.
7. Update `extract_features` callers to consume merged
   scraped text from multiple sources. Tests.
8. Implement the minimal `spec_drafter.py` subset needed for
   write-back: `append_sources` and `update_design_autogen`.
   The rest of the drafter (drafting from inputs via LLM)
   is Phase 4. Tests.
9. Wire SPEC.md write-back into `_subsequent_run`:
   discovered URLs → `append_sources`, design extraction
   → `update_design_autogen`. Tests.
10. Update investigator to include counter-examples and
    behavior contracts. Tests.
11. Restructure `_subsequent_run` and `_fix_mode` to use
    the new orchestration shape. Tests, including the
    integration tests above. Leave `_first_run` untouched.

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
