# Bugs

## main.py:454 -- save_reference_urls in loop overwrites previous URL records
**Severity**: medium
In `_analyze_new_files()`, when multiple new URLs are found (lines 449-460), `save_reference_urls(page_records)` is called inside the loop for each URL. `save_reference_urls()` (saver.py:531) replaces the entire `reference_urls` key in duplo.json with the new records. Each iteration overwrites the previous URL's page records, so only the last URL's records survive.

## main.py:452 -- Code examples and doc structures from new URLs silently discarded
**Severity**: medium
In `_analyze_new_files()`, `fetch_site()` returns `code_examples` and `doc_structures` at line 452, but neither is saved. Only `page_records` and `raw_pages` are persisted. Compare with `_first_run()` (lines 960-968) which saves both. Similarly, PDF text (line 423) and text file content (lines 431-434) are extracted but never stored or fed into feature extraction, unlike the first run where they feed into `extract_features()`.

## main.py:481 -- Unhandled JSONDecodeError in _load_existing_urls
**Severity**: medium
`_load_existing_urls()` calls `json.loads()` at line 481 without catching `JSONDecodeError`. It is called from `_analyze_new_files()` during `_subsequent_run()` at line 675, which runs before the JSONDecodeError handler at line 702. If duplo.json is corrupted, the program crashes with an unhandled exception instead of reaching the user-friendly error message.

## main.py:498 -- Unhandled JSONDecodeError in _rescrape_product_url
**Severity**: medium
`_rescrape_product_url()` calls `json.loads()` at line 498 without catching `JSONDecodeError`. Called from `_subsequent_run()` at line 684, before the handler at line 702. Same crash scenario as `_load_existing_urls`.

## main.py:544 -- Unhandled JSONDecodeError in _detect_and_append_gaps
**Severity**: medium
`_detect_and_append_gaps()` calls `json.loads()` at line 544 without catching `JSONDecodeError`. Called from `_subsequent_run()` at line 691, before the handler at line 702. Same crash scenario.

## saver.py:322 -- Unhandled JSONDecodeError in advance_phase
**Severity**: medium
`advance_phase()` calls `json.loads()` at line 322 without catching `JSONDecodeError`. Called from `_execute_phase()` in main.py at line 1014 after McLoop completes. If duplo.json was corrupted during the McLoop run, the phase completion recording crashes. Other saver functions like `clear_in_progress()` (line 262) and `load_product()` (line 68) do handle this correctly.

## saver.py:340 -- Unhandled JSONDecodeError in get_current_phase
**Severity**: medium
`get_current_phase()` calls `json.loads()` at line 340 without catching `JSONDecodeError`. Called during both first run (line 311) and subsequent run (line 721). Crashes on corrupted duplo.json.

## saver.py:431 -- Unhandled JSONDecodeError and KeyError in load_examples
**Severity**: medium
`load_examples()` reads individual JSON files from `.duplo/examples/` at line 431 with `json.loads()` but has no try/except for `JSONDecodeError`. Lines 434-435 access `data["input"]` and `data["expected_output"]` with direct key access instead of `.get()`, so a malformed example file missing either key raises `KeyError`. The fallback path at line 445 has the same `JSONDecodeError` risk.

## main.py:728 -- startswith phase matching can match wrong phases
**Severity**: low
In `_subsequent_run()`, the check `any(h.get("phase", "").startswith(phase_label) for h in history)` uses `startswith` to match phase labels. When `phase_info` is None (phase number not in roadmap), `phase_label` is `"Phase 1"` without a title suffix. A history entry like `"Phase 10: Polish"` starts with `"Phase 1"`, causing the code to incorrectly conclude Phase 1 is already done and skip to the next phase.

## video_extractor.py:128 -- Glob pattern injection from video filename
**Severity**: low
`_run_ffmpeg_scene_detect()` collects extracted frames using `output_dir.glob(f"{stem}_scene_*.png")` at line 128, where `stem` is derived from the video filename. If the video filename contains glob special characters (`[`, `]`, `*`, `?`), the glob pattern is corrupted and silently returns no results, losing all extracted frames even though ffmpeg created them successfully.
