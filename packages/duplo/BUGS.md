# Bugs

## video_extractor.py:191 -- Unhashable frames cause false duplicate detection
**Severity**: high
When a frame cannot be hashed (e.g., corrupted image), `deduplicate_frames()` stores it with hash value 0. Subsequent valid frames are compared against all kept hashes via `_hamming(h, 0)`. Any frame with a low Hamming weight (few bits set in its 64-bit dHash) will have a Hamming distance <= 6 from 0, causing it to be incorrectly classified as a duplicate and deleted from disk. For example, a mostly-black frame with only 4 bits set in its hash would be silently deleted as a "duplicate" of the unhashable frame.

## design_extractor.py:164 -- components field not type-validated
**Severity**: medium
In `_parse_design()`, the `colors`, `fonts`, `spacing`, and `layout` fields are all validated with `isinstance(..., dict)` checks (lines 160-163), but `components` is stored as-is from the API response. If the API returns a non-list value (e.g., a string like `"none"`), `format_design_section()` at line 204 will iterate over its characters, and line 205 `comp.get("name")` will crash with `AttributeError` since strings don't have `.get()`.

## design_extractor.py:205 -- No type check on component items before calling .get()
**Severity**: medium
In `format_design_section()`, `comp.get("name", "unknown")` is called without first checking that `comp` is a dict. If the API returns a list containing non-dict items (e.g., strings), this crashes with `AttributeError`. The same pattern in `gap_detector.py:228` correctly guards with `if not isinstance(comp, dict): continue`.

## saver.py:317 -- advance_phase() crashes if duplo.json missing
**Severity**: low
`advance_phase()` calls `path.read_text()` on `.duplo/duplo.json` without checking if the file exists first. If called before duplo.json is created (e.g., due to an interrupted first run or external tooling), it raises `FileNotFoundError`. Other similar functions like `get_current_phase()` (line 333) and `clear_in_progress()` properly check existence before reading.

## runner.py:24 -- Docstring says "mcloop sync" but code runs bare "mcloop"
**Severity**: low
The docstring for `run_mcloop()` says "Run ``mcloop sync`` in *target_dir*" but the actual subprocess command is `["mcloop"]` with no subcommand. If mcloop requires the "sync" argument to perform the intended operation, the function silently does the wrong thing.
