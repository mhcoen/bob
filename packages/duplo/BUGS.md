# Bugs

## main.py:719 -- Unhandled JSONDecodeError during feature re-extraction
**Severity**: medium
In `_subsequent_run()`, line 719 reads `.duplo/duplo.json` via `json.loads(Path(_DUPLO_JSON).read_text(...))` without catching `json.JSONDecodeError`. If duplo.json is corrupted, this crashes with an unhandled exception. The same file is read 25 lines later (line 744) with proper `try/except json.JSONDecodeError` handling and a user-friendly error message. The fix is to wrap the read at line 719 in the same error handling.

## selector.py:85 -- Reversed range silently produces empty selection
**Severity**: low
In `_parse_selection()`, a range like `"5-2"` produces no results because `range(5, 3)` is empty in Python. The code does `for n in range(start, end + 1)` without checking whether `start <= end`. A user entering `"5-2"` would expect features 2 through 5 to be selected, but gets nothing. The fix is to swap start/end when start > end: `range(min(start, end), max(start, end) + 1)`.

## doc_examples.py:169-191 -- _parse_doctest silently discards all but last example
**Severity**: low
When a doctest code block contains multiple prompt/output pairs (e.g. `>>> 1+1` / `2` / `>>> 2+2` / `4`), the function resets `input_lines` and `output_lines` each time a new `>>>` prompt follows output (lines 178-181, 184-186), keeping only the final example. The function returns `CodeExample | None` so it can only yield one result per block. Any earlier examples in the same block are silently lost. Multi-example doctest blocks are common in Python documentation.

## saver.py:184 -- append_phase_to_history crashes on corrupted duplo.json
**Severity**: medium
`append_phase_to_history()` reads duplo.json with `json.loads(path.read_text(...)) if path.exists() else {}` (line 184) without catching `json.JSONDecodeError`. This function is called from `_execute_phase()` (main.py:1061) before `advance_phase()` and `clear_in_progress()`. If duplo.json is corrupted at this point, the crash prevents the phase from being properly finalized: `current_phase` is not advanced, `in_progress` is not cleared, and PLAN.md is not deleted. On the next run, duplo detects `in_progress` and re-invokes `_execute_phase`, which calls `append_phase_to_history` again, creating an unrecoverable loop. The same unguarded pattern is repeated in `save_feedback` (line 218), `set_in_progress` (line 249), and several other saver write functions.
