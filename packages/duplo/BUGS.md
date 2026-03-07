# Bugs

## main.py:785 -- No end-of-roadmap detection causes infinite phase generation
**Severity**: medium
After all roadmap phases are complete, `_subsequent_run` keeps generating and executing generic phases forever. `get_current_phase()` returns `(N, None)` both when no roadmap exists and when `current_phase` exceeds the last roadmap phase. The code at line 785-825 does not distinguish between these cases — when `phase_info` is `None`, it falls through to `generate_phase_plan(..., phase=None, ...)` which produces a generic plan, executes it, advances the counter, and repeats on the next run. The code should check whether a roadmap exists and `current_phase` is past its last entry, then stop or prompt the user.

## extractor.py:63 -- JSON code fence stripping fails when AI response has preamble text
**Severity**: medium
All JSON response parsers use `text.startswith("```")` to detect and strip markdown code fences. If the AI includes any preamble text before the fence (e.g., `"Here are the results:\n```json\n[...]\n```"`), the check fails, `json.loads` fails on the full text, and the parser silently returns empty/default results. This same pattern appears in `gap_detector.py:126`, `design_extractor.py:88`, `roadmap.py:87`, `validator.py:88`, `frame_filter.py:85`, and `frame_describer.py:81`. Although the system prompts instruct the AI to return "ONLY" JSON, LLMs frequently add preamble text, making this a realistic failure mode that silently drops valid data.

## main.py:457 -- doc_structures from subsequent URL fetches are silently dropped
**Severity**: low
In `_analyze_new_files` (line 457) and `_rescrape_product_url` (line 525), the `doc_structures` return value from `fetch_site()` is captured in a variable but never accumulated or saved to duplo.json. On first run, doc structures are saved via `save_selections` in `_init_project`. On subsequent runs, newly fetched doc structures (feature tables, operation lists, unit lists, function refs) from new or re-scraped URLs are lost. This means structural documentation changes on the product site are never picked up after the first run.

## main.py:862 -- save_plan appends to stale PLAN.md during crash recovery
**Severity**: low
In `_advance_to_next` (crash-recovery path where the current phase is in history but PLAN.md was not deleted), `save_plan(content)` at line 862 appends the new phase content to the existing PLAN.md which still contains the old completed phase's plan. McLoop then reads the combined file and sees both old checked items and new unchecked items. The new plan content should replace the old content, not be appended to it.
