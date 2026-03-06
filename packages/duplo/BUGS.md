# Bugs

## video_extractor.py:195 -- TypeError when deduplicating frames that failed to hash
**Severity**: high
When a frame fails to open/hash (line 189 except branch), it is appended to `kept` with `None` as its hash value. On subsequent iterations, line 195 checks `kh is not None` for kept frames but does not check whether the *current* frame's hash `h` is also non-None. If `h` is `None` (unhashable current frame) and some kept frame has `kh is not None`, `_hamming(h, kh)` is called with `h=None`, causing `TypeError: unsupported operand type(s) for ^: 'NoneType' and 'int'` in `_hamming` at line 153.

## fetcher.py:179-183 -- Failed fetches consume page cap
**Severity**: medium
In `fetch_site()`, the page visit counter (`seed_visited` or `docs_visited`) is incremented on lines 181-183 *before* the HTTP request on line 186. If the request fails (caught by `except Exception` on line 194), the counter is already incremented, so a failed page permanently consumes one slot from the `max_pages` or `max_docs_pages` budget. With `max_pages=10`, a few timeouts or 404s can significantly reduce useful content fetched.

## saver.py:262 -- Unhandled JSONDecodeError in clear_in_progress()
**Severity**: medium
`clear_in_progress()` reads and parses `.duplo/duplo.json` with `json.loads()` on line 262 without catching `json.JSONDecodeError`. If the file contains invalid JSON (e.g. from a partial write or corruption), this crashes with an unhandled exception. Compare with `_subsequent_run()` in main.py (lines 700-704) which properly catches this case.

## doc_examples.py:213-214 -- Shell parser comment contradicts behavior
**Severity**: medium
In `_parse_shell()`, when a new shell prompt (`$` or `%`) is encountered after output has started, the comment on line 213 says "keep accumulating" but line 214 does `break`, stopping parsing entirely. This means only the first command/output pair is captured from multi-command shell blocks. A block like `$ cmd1\noutput1\n$ cmd2\noutput2` will only capture cmd1/output1 and discard cmd2/output2.

## planner.py:248-253 -- append_test_tasks docstring says "insert before final item" but appends at end
**Severity**: low
The docstring on line 248 says "Inserts the tasks before the final checklist item if one exists" but the implementation on line 253 always appends at the end of the plan. Test tasks will always appear after the final checklist item rather than before it.
