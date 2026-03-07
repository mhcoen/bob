# Bugs

## main.py:759 -- Stale PLAN.md from completed phase causes next phase to be skipped
**Severity**: high
After `_execute_phase` completes a phase, it calls `append_phase_to_history`, `advance_phase`, and `clear_in_progress`, but never removes or replaces PLAN.md. On the next `_subsequent_run`, `get_current_phase()` returns the new phase number (e.g., 1). The history check at line 748 correctly finds that Phase 1 is NOT in history. Then line 759 finds the stale PLAN.md (still containing Phase 0's all-checked-off content) and assumes it is from an interrupted run of the current phase. It re-runs McLoop (which is a no-op since all items are checked), records Phase 0's plan in history again, and calls `advance_phase()` to increment to phase 2. Phase 1 is completely skipped — its plan is never generated and its features are never built. This repeats for every subsequent phase, effectively skipping the entire roadmap after Phase 0.

## fetcher.py:179 -- Redirect target URLs bypass the visited set
**Severity**: low
`fetch_site` adds only the original `current_url` (normalized) to the `visited` set at line 179. When `httpx.get` follows a redirect (e.g., `http://example.com` → `https://example.com/home`), the final URL is not recorded. If another page links directly to the redirect target, it will not be found in `visited` or `queued`, so it gets fetched and processed a second time. This produces duplicate entries in `results`, `all_records`, `raw_pages`, and `all_examples`, and consumes page budget on content that was already crawled.
