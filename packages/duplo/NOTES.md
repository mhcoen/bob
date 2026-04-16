# Notes

## Observations

### [5.38.1] LLM JSON extraction fragility in Vision modules — 2026-04-16

`frame_describer` and `frame_filter` both used `strip_fences` to clean LLM output before `json.loads`. When the LLM returns JSON wrapped in conversational prose without markdown code fences, `strip_fences` is a no-op and parsing fails. Fixed by adding `extract_json` to `parsing.py` (tries `strip_fences` first, then scans for outermost `{...}` / `[...]`). Applied to `frame_describer` and `frame_filter`. Other modules using `strip_fences` + `json.loads` (extractor, gap_detector, build_prefs, validator, etc.) have the same latent vulnerability but weren't hit in practice — they use `query` (text-only), not `query_with_images` (tool-augmented), so the LLM is less likely to produce prose-wrapped JSON.

### [5.27.7] `save_raw_content` default `target_dir` bug — 2026-04-14

`saver.py:save_raw_content` uses `target_dir: Path = Path.cwd()` as a default argument (line 1213). Unlike every other function in `saver.py` which uses `target_dir: Path | str = "."`, this one evaluates `Path.cwd()` at import time, not call time. In production this works because duplo's cwd doesn't change between import and use. In tests using `monkeypatch.chdir(tmp_path)`, the default points to the original cwd instead of `tmp_path`. Integration tests must either pass `target_dir` explicitly or call `save_raw_content` directly rather than through `_persist_scrape_result`. Consider aligning with the `"."` convention used everywhere else.

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
