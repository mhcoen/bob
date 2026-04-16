# Notes

## Observations

### [1.5] `strip_fences` + `json.loads` migration is incomplete — 2026-04-16

Phases 1.1–1.4 migrated `extractor.py`, `gap_detector.py`, `build_prefs.py`, and
`validator.py` to use `extract_json`. Five modules still contain the old
pattern and are allow-listed in `tests/test_parsing_invariant.py`
(`ALLOWED_UNMIGRATED`): `roadmap.py`, `verification_extractor.py`,
`investigator.py`, `task_matcher.py`, `saver.py` (3 occurrences in saver).
The regression test catches reintroduction into migrated files today; when
each remaining module is migrated, its entry should be removed from
`ALLOWED_UNMIGRATED` so the guard covers it too. A companion test
(`test_allowed_unmigrated_list_is_accurate`) fails loudly if a file is
migrated without removing its allowlist entry.

### [5.38.2] Diagnostic logging added to frame_describer — 2026-04-16

Added `record_failure` calls to all three parse-error exit paths in `_parse_descriptions`. Each records the raw LLM response (first 2000 chars) and the extracted text to `.duplo/errors.jsonl`. The next manual run with video frames will capture the actual response that the parser is choking on. No existing `frame_describer` entries were found in `errors.jsonl` because the logging wasn't present during the [5.38.1] manual run.

### [5.39.2] Design extraction chain had silent failure paths — 2026-04-16

Traced the full chain in `_subsequent_run` after `extract_design` is called. Four
places in `main.py` run the extract→format→update pipeline. Two of them
(`_subsequent_run`'s spec_sources path and `_rescrape_product_url`) were missing
the `else` branch for when `design.colors/fonts/layout` are all empty — extraction
would fail silently with no message or diagnostic. All four paths were missing
diagnostics for two inner steps: `format_design_block` returning empty despite
non-empty design fields, and `update_design_autogen` returning unchanged text.
Added `record_failure` calls at both inner failure points in all four paths, and
added the missing "Could not extract" messages in the two paths that lacked them.
The most likely cause of the [5.39.1] issue: `extract_design` returned a
`DesignRequirements` with populated `source_images` but empty colors/fonts/layout
(from a `ClaudeCliError` or parse failure), and `_subsequent_run` silently skipped
writing to SPEC.md because there was no else branch.

### [5.39.1] design_extractor had the same strip_fences fragility — 2026-04-16

`design_extractor._parse_design` used `strip_fences` + `json.loads`, the same pattern fixed in `frame_describer`/`frame_filter` during [5.38.3]. When the Vision LLM returned JSON preceded by prose (e.g. "Here is the design analysis:\n\n{...}"), `strip_fences` was a no-op, `json.loads` raised `JSONDecodeError`, and `_parse_design` returned an empty `DesignRequirements`. The caller in `main.py` then skipped writing `## Design` to SPEC.md because `design.colors` was empty. No diagnostic was logged because the error path returns silently. Fixed by switching to `extract_json`. This was noted as a latent risk in [5.38.1] ("Other modules using `strip_fences` + `json.loads` … have the same latent vulnerability").

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

## Hypotheses

### [5.38.2] `claude -p --tools Read` output format — 2026-04-16

`query_with_images` runs `claude -p --tools Read`. The most likely cause of universal parse failure is that `claude -p` with `--tools` outputs in a structured format (e.g., streaming JSON, JSONL with tool-use messages, or a result wrapper object) rather than plain text. If the output contains multiple JSON objects (one per tool use + final response), `extract_json` would find the first `{` and last `}` across the entire output, producing an invalid JSON candidate that spans multiple objects. This would fail `json.loads` and hit the "parse error" path. The diagnostic logging added in 5.38.2 will capture the actual raw response to confirm or eliminate this. Potential fixes: (a) add `--output-format text` to the `claude -p` command, (b) parse the structured output to extract only the final text block, or (c) split the output by lines and extract JSON from only the last text block.

## Eliminated

### [5.39.4] Frame describer ↔ design extractor entanglement — 2026-04-16

Investigated whether the frame_describer bug (all frames getting "unknown" state) could cause the design extractor to produce empty output. **They are independent pipelines.** `extract_design` receives raw image paths (via `collect_design_input`) and sends them directly to Vision — it never consumes frame descriptions. Frame descriptions are consumed only by `extract_verification_cases` for PLAN.md verification tasks. Both bugs shared the same root cause (`strip_fences` + `json.loads` fragility, fixed in [5.38.3] and [5.39.1] by switching to `extract_json`), but they cannot cause each other. Eliminated by code path tracing: `collect_design_input` → `extract_design` → `query_with_images` (image paths); vs. `describe_frames` → `load_frame_descriptions` → `extract_verification_cases` (frame descriptions).
