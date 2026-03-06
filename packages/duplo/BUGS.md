# Bugs

## design_extractor.py:155 -- format_design_section crashes on non-dict design fields
**Severity**: medium
`_parse_design()` stores whatever JSON types Claude returns via `data.get("colors", {})` etc. without validating they are dicts. If the LLM returns a string or list instead of an object for `colors`, `fonts`, `spacing`, or `layout`, the values are stored in `DesignRequirements` as-is. When `format_design_section()` later calls `.items()` on these fields (lines 176, 182, 188, 194), it crashes with `AttributeError: 'str' object has no attribute 'items'` (or similar for list). The prompt asks for objects but LLM responses are not guaranteed to conform.

## gap_detector.py:205 -- detect_design_gaps crashes on non-dict design values
**Severity**: medium
`detect_design_gaps()` calls `.items()` on `design.get("colors", {})` and `design.get("fonts", {})` at lines 205 and 215. If the stored `design_requirements` in duplo.json has non-dict values for these keys (possible if the original extraction stored bad types from the LLM, or if the file was manually edited), this crashes with `AttributeError`. Same root cause as the `design_extractor.py` bug but a separate code path.

## hasher.py:68 -- load_hashes crashes on corrupted file_hashes.json
**Severity**: medium
`load_hashes()` handles the case where `.duplo/file_hashes.json` does not exist (returns `{}`), but if the file exists with invalid JSON (e.g., truncated by an interrupted write during `save_hashes()`), `json.loads()` raises an unhandled `JSONDecodeError`. Since `save_hashes()` uses non-atomic `path.write_text()`, a Ctrl-C or crash during write can corrupt this file. This is called on every subsequent run (`_subsequent_run()` at main.py:653).

## saver.py:68 -- load_product crashes on corrupted product.json
**Severity**: low
`load_product()` handles missing `.duplo/product.json` (returns `None`) but does not catch `JSONDecodeError` if the file exists with invalid JSON. Same class of bug as `hasher.py:68` -- non-atomic writes mean an interrupted process can leave a corrupt file that crashes the next run.

## main.py:700 -- _subsequent_run crashes on corrupted duplo.json
**Severity**: medium
In `_subsequent_run()`, `json.loads(duplo_path.read_text())` at line 700 has no `JSONDecodeError` handling. This is the main entry point for all subsequent runs. If `.duplo/duplo.json` is corrupted (truncated write, manual edit error), the entire tool becomes unusable with an unhelpful traceback. Many functions in `saver.py` that read duplo.json (e.g., `advance_phase`, `get_current_phase`, `clear_in_progress`) have the same issue, but this call site is the most impactful since it gates all subsequent-run functionality.
