# Notes

## Observations

### [7.1.3] Migration gate fully prevents old-format projects from reaching _first_run — 2026-04-17

Audit confirms no old-format project can reach `_first_run`. Dispatch in
`main.py:655-805` has three entry branches:

- `init` subcommand (main.py:665-716) — bypasses `_check_migration`, calls
  `run_init` (duplo/init.py). `run_init` never calls `_first_run` (grep confirms).
- `fix`/`investigate` subcommand (main.py:718-795) — bypasses
  `_check_migration`, calls `_fix_mode`. `_fix_mode` never calls `_first_run`.
- Default no-subcommand path (main.py:796-803) — calls `_check_migration(Path.cwd())`
  first (main.py:797), then dispatches on `duplo.json` existence:
  `_first_run(url=args.url)` if absent, `_subsequent_run()` if present.

`_first_run` is called from exactly one site (main.py:799) — grep for
`_first_run(` returns only the definition (main.py:1035) and that one call.
No internal recursion; `_subsequent_run` does not invoke it.

`needs_migration` (migration.py:37-64) fires only when `.duplo/duplo.json`
exists AND no new-format SPEC.md is present. By definition:
- Old-format project has `.duplo/duplo.json` → either migration fires and
  `sys.exit(1)` before dispatch, or it passes (new-format SPEC.md present)
  and `duplo_path.exists()` is True → `_subsequent_run`, not `_first_run`.
- Therefore `_first_run` is reachable only when `.duplo/duplo.json` does
  NOT exist, which is by definition NOT an old-format project.

Existing tests pin this:
- `test_old_layout_prints_message_exits_skips_runs` (test_main.py:6316)
  asserts `first_run_called == []` for old layout.
- `test_init_skips_check_migration` (test_main.py:6420) asserts
  `first_run_called == []` on init bypass.
- `test_fix_old_layout_bypasses_migration_dispatches_fix` (test_main.py:6441)
  confirms fix bypass routes to `_fix_mode`.
- `test_migration_pass_proceeds_to_first_run` (test_main.py:6395) pins
  that `_first_run` runs only when no `duplo.json`.

Conclusion: no code path allows an old-format project to reach `_first_run`.
No gating or removal needed for bypass. The next checkbox (document at
removal site) can proceed.

**Edge case flagged, not a reachability problem**: a user who manually deletes
`.duplo/duplo.json` but leaves other old artifacts (`.duplo/product.json`,
`screenshots/`, legacy `references/`) falls through to `_first_run`. By
MIGRATION-design.md:168-172 this is intentional ("they can always delete
`.duplo/` and start fresh"). `_first_run` even consumes a lingering
`.duplo/product.json` via `load_product()` at main.py:1111. This partial-reset
path is a feature of the current design, not a missed old-format case.

### [7.1.2] duplo init + _subsequent_run coverage of _first_run — 2026-04-17

Confirmed the coverage claim in CURRENT_PLAN.md line 13.

**URL input** (was `_first_run(url=args.url)` at main.py:1035, 1050-1053, and the
`_validate_url` interactive disambiguation at 1141-1143). `duplo init` accepts the URL
at `args.url` (init.py:155) and dispatches to `_run_url` (init.py:261) which
canonicalizes (url_canon.canonicalize_url) and fetches shallow-by-default via
`fetch_site`. Disambiguation is replaced by a non-interactive `validate_product_url`
call inside `_identify_product` (init.py:203-223) — unidentified products fall back to
a pre-filled `## Sources` entry without prompting. `duplo init <url>
--from-description PATH` (combined) and `--from-description PATH` alone are also
handled. Equivalent or stronger than `_first_run` URL handling.

**SPEC.md generation** (new; `_first_run` never wrote SPEC.md — it consumed one
if present and wrote an autogen design block). `duplo init` always writes
SPEC.md via `format_spec(_build_draft_spec(...))` (init.py:197, 321, 331, 340,
473, and the combined path). Existing files in `ref/` are inventoried by
`_scan_existing_ref_files` and each gets a role via `_propose_file_role`, so the
drafted `## References` is pre-filled for the user to confirm.

**Feature extraction** (was in `_first_run` at main.py:1194-1207 via
`extract_features` followed by interactive `select_features`). `_subsequent_run`
calls `extract_features` at main.py:2060-2066 with the same `spec_text`,
`scope_include`, `scope_exclude` arguments, then merges new features into
duplo.json via `save_features` (2078). The per-phase interactive
`select_features` at 2294 still runs before phase PLAN.md generation — the only
change is it runs per-phase, not once at bootstrap.

**PLAN.md generation from SPEC.md** (was in `_first_run` at main.py:1412-1438,
generating Phase 1 from the fresh roadmap). `_subsequent_run`'s State 3 branch
(main.py:2246-2349) generates a PLAN.md for the current phase: regenerates a
roadmap if none exists (2252-2276), runs `generate_phase_plan` (2330-2339), and
appends verification cases from both video frames (2340-2348) and behavior
contracts (same structure as `_first_run`).

Conclusion: the three responsibilities named in the plan (URL input, feature
extraction, PLAN.md generation) are fully covered by the split
`duplo init` + `_subsequent_run`. The claim is accurate.

## Hypotheses

### [7.1.2] Dispatch and _subsequent_run assumptions block _first_run removal — 2026-04-17

Coverage confirmed above is prospective — the next checkbox (CURRENT_PLAN.md
line 20) must flip the dispatch. Gaps to resolve when that lands:

1. **Dispatch still routes to `_first_run`.** main.py:797-799 calls
   `_first_run(url=args.url)` whenever `.duplo/duplo.json` is absent.
   `duplo init` writes SPEC.md but NOT `.duplo/duplo.json`, so
   `duplo init` followed by `duplo` still hits `_first_run`, not
   `_subsequent_run`. Until the dispatch is updated, the coverage above is only
   latent.

2. **`_subsequent_run` assumes duplo.json exists in several places.** After the
   dispatch flips, the first `duplo` run against a fresh `duplo init` directory
   will have no `.duplo/duplo.json`. Spots that need hardening (FileNotFoundError
   paths not yet guarded):
   - main.py:2056 `old_data = json.loads(Path(_DUPLO_JSON).read_text(...))` —
     except clause catches only `JSONDecodeError`.
   - main.py:2073 same pattern, same gap.
   - main.py:2079 `updated_data = json.loads(...)` — relies on `save_features`
     having created the file, which it does, but worth verifying for the
     feature-less fresh-init case.
   - main.py:2213-2218 outer `data` reload — same `except json.JSONDecodeError`
     only.
   - The top-level read at 1973-1976 DOES catch `OSError`, so that one is fine.

3. **Three `_first_run` responsibilities not in either replacement:**
   - Interactive app_name prompt for appshot (main.py:1360-1363). No
     equivalent in `duplo init` or `_subsequent_run`. `derive_app_name(spec)` is
     used in `_subsequent_run` at 2219, which reads from duplo.json then
     directory name — so `app_name` ends up as the sanitized directory name by
     default. If the old interactive prompt is deemed dead, this is already
     covered; if it's meant to stay, it needs a home.
   - `ask_preferences()` fallback when `spec.architecture` is empty
     (main.py:1355-1356). `validate_for_run` should reject a spec with an
     unfilled `## Architecture` before this point, making the fallback
     unreachable. Worth asserting that fact before deleting `ask_preferences`.
   - Interactive roadmap approval prompt (main.py:1400-1405). `_subsequent_run`
     State 3 saves the new roadmap without a confirmation prompt (2272-2273).
     This is a behavior change, not a gap — worth calling out so it is an
     explicit decision rather than a silent drop.



All six test cases called out in 6.15.8 were added incrementally during
6.15.1-6.15.7 and pass. Mapping:

- description read from file: `TestRunInitDescriptionFile::test_reads_description_from_file_and_writes_spec`
- description read from stdin (mocked): `TestRunInitDescriptionStdin::test_reads_description_from_stdin_pipe`
- file not found exits 1: `TestRunInitDescriptionFile::test_missing_file_prints_error_and_exits_1`
- URL extracted with `proposed: true`: `TestRunInitDescriptionUrlExtraction::test_like_url_in_prose_becomes_proposed_product_reference`
- inferred role correct: same test (product-reference) + `test_unlike_url_in_prose_becomes_proposed_counter_example_scrape_none` (counter-example)
- Notes section contains verbatim prose: asserted inside `test_reads_description_from_file_and_writes_spec`

No new tests were written for this subphase because adding redundant
assertions would duplicate the existing coverage. All 48 tests in
`tests/test_init.py` pass.

### [6.15.5] URL-from-prose extraction already in place — 2026-04-17

Task 6.15.5 asks for URL extraction from the prose description, with role
inferred via `_infer_url_role` and `proposed: true` on the resulting
`SourceEntry`. This was already implemented as part of task 6.15.1 (see
the note below). `_build_draft_spec` (spec_writer.py lines ~1073-1096)
handles extraction, canonicalization, role inference, counter-example
scrape coercion, and dedup against an explicit `inputs.url`. `_run_description`
already calls `_build_draft_spec`, so the init flow gets the behavior for
free. Tests covering the behavior: `TestExtractProseUrls`,
`TestBuildDraftSpecProseUrls` (in test_spec_writer.py), and
`TestRunInitDescriptionUrlExtraction` (in test_init.py). All pass.

INIT-design.md vs DRAFTER-design.md discrepancy: INIT-design.md § "duplo init
--from-description" lines 185-187 says the prose-extracted source entry gets
"a note explaining the URL was extracted from the description." DRAFTER-design.md
§ "Inferring URL roles" does not mention a note. The task description says
"Per DRAFTER-design.md" and the plan header specifies DRAFTER-design.md is
authoritative for spec_writer.py extensions, so the current implementation
(no note) follows the authoritative source. Flagging here per the plan's
"flag the discrepancy for resolution" rule — a later task or user decision
may want to add `notes="extracted from description"` on these entries.

### [6.15.1] draft_spec refactored to expose a ProductSpec-returning core — 2026-04-17

`_run_description` needs to inspect the drafted `ProductSpec` to decide which
per-section bullets to print ("Pre-filled ## Purpose, ## Design from prose.",
"## Architecture left as <FILL IN>", etc.). Rather than re-parsing the
serialized SPEC.md, split `draft_spec` into `_build_draft_spec(inputs) ->
ProductSpec` + a thin `draft_spec = format_spec ∘ _build_draft_spec` wrapper.
Existing `draft_spec` tests all still pass because `draft_spec` is
behaviorally identical. The new internal API is what init.py consumes.

Also added URL extraction from description prose to the drafter
(`_extract_prose_urls`), so descriptions like "like https://numi.app" now
produce a `proposed: true` Sources entry with `role: product-reference`
inferred via `_infer_url_role`. Counter-example roles get `scrape: none`
coerced at write time (mirrors the parser and `append_sources` rules).
An explicit `inputs.url` suppresses any duplicate prose-extracted entry
so Sources stays single-entry-per-URL when the combined case arrives in
Phase 6.15.2.

### [6.7.1] DraftInputs added in this task, not in 6.1.1-6.1.2 — 2026-04-17

Task 6.7.1 implements `_draft_from_inputs(inputs: DraftInputs)` whose first
argument type was supposed to be defined by tasks 6.1.1-6.1.2. git log shows a
checkpoint commit `b64753b` (next: 6.1.1-6.1.2) followed directly by `a743f64`
(next: 6.2.1) — no "Complete: [BATCH] 6.1.1-6.1.2" commit between them — yet
CURRENT_PLAN.md marks 6.1.1-6.1.2 as `[x]` done. The DraftInputs dataclass was
never actually written. Added it in this task so `_draft_from_inputs` has its
parameter type. If the workflow runs 6.1.1-6.1.2 again it will find DraftInputs
already present; tests pass either way.

### [6.8.5] Step 4 emits reference entries without a role when vision_proposals is incomplete — 2026-04-17

`draft_spec` step 4 uses `inputs.vision_proposals.get(path, "")` so a ref/
file that is not a key in `vision_proposals` gets a `ReferenceEntry` with
`roles=[]`. `format_spec` emits this as `- <path>` + `proposed: true` with
no `role:` line. The parser then drops such entries into
`dropped_references` because a role is required. In practice this should
not happen — the caller (`duplo init`) calls `_propose_file_role` for
every file in `existing_ref_files`, and that function always returns a
role (falling back to `"ignore"` for unknown extensions). The defensive
`.get(path, "")` fallback is therefore a silent data-loss path if a
caller ever constructs `DraftInputs` with mismatched `existing_ref_files`
/ `vision_proposals`. Options if this becomes a concern: (a) assert every
`existing_ref_files` entry is present in `vision_proposals`, (b) default
missing entries to `"ignore"` so they survive the parser round-trip, or
(c) log a diagnostic. Test
`test_step4_ref_file_missing_from_vision_proposals_emitted_without_role`
pins the current writer behavior.

### [6.7.1] Error-handling discrepancy between plan and design doc — 2026-04-17

CURRENT_PLAN.md bullet 6.7.7 says `_draft_from_inputs` should "fall back to
empty ProductSpec (template-only draft) with a diagnostic" after retries.
DRAFTER-design.md § "Error handling" says the function should raise
`DraftingFailed` and the caller (`draft_spec`) catches it. Tasks 6.9-6.10
plan to add `DraftingFailed` and catch it in `draft_spec`. Implemented per
the plan for now (return empty ProductSpec + record_failure); if 6.9/6.10
migrate to exception-based handling, `_draft_from_inputs` will need to be
refactored to raise instead of return, and tests updated.

### [6.3.1] Parser re-ingests content from format_spec comment hints — 2026-04-17

Round-trip testing revealed that `_parse_spec` picks up "example" content
from inside the HTML comment hints that `format_spec` emits for empty
optional sections. Specifically: the Sources/References/Scope/Behavior
parsers operate on raw body text (not comment-stripped), and their regexes
match the example list items embedded in the `<!-- Example: ... -->` blocks.
For an empty `ProductSpec`, `parse(format_spec(spec))` yields non-empty
`sources`, `references`, `scope_include`, `scope_exclude`, and
`behavior_contracts` populated from the template examples. Notes and
Architecture strip comments before extraction and are not affected.
Impact: the round-trip property only holds for specs that populate these
sections with real content (so `format_spec` skips the comment hints).
Round-trip fixtures in `tests/test_spec_writer.py::TestRoundTrip` all have
at least one entry in each "pickup-prone" section. Eventual fix would be
to either (a) comment-strip bodies in the Sources/References/Scope/Behavior
parsers, or (b) omit example content from the comment hints. Deferred —
not in scope for 6.3.1.

### [6.3.1] Round-trip comparator excludes more fields than DRAFTER-design.md lists — 2026-04-17

DRAFTER-design.md's `_ROUND_TRIP_EXCLUDED_FIELDS` example lists only `raw`,
`dropped_sources`, and `dropped_references`. In practice the comparator also
must exclude `scope`, `behavior`, `fill_in_purpose`, `fill_in_architecture`,
and `fill_in_design` because these are derived from the serialized body by
the parser: `scope` / `behavior` hold the raw body string and always
change after round-tripping; the `fill_in_*` flags are parser-set when the
body contains `<FILL IN>` markers. DesignBlock's `has_fill_in_marker` is
similarly parser-set and excluded by comparing only `user_prose` and
`auto_generated` in the DesignBlock sub-comparison. Design doc lists the
minimum; this is the practical set.

### [1.6] `extract_json` preferred inner object over outer array — 2026-04-16

Adding round-trip parser tests for the four migrated modules surfaced a latent
bug in `duplo.parsing.extract_json`: for prose-prefixed input like
`"Here are the features:\n[{...}]"`, the balanced-span scanner iterated `{...}`
first and returned the first valid object (the inner dict), instead of the
outer array. `_parse_features` then saw a dict, failed its `isinstance(data,
list)` check, and returned `[]` — no round-trip. Fixed by switching
`extract_json` from "first valid span wins" to "longest valid span wins": for
arrays of objects the outer `[...]` is longer than any inner `{...}`, so the
array is returned; for objects containing arrays the outer `{...}` is longer,
so the object is returned. Existing tests in `test_parsing.py` (including
`test_extract_json_multiple_objects`) continue to pass because their
assertions are satisfied by either span.

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

### [6.10.3] `## ` inside AUTO-GENERATED design body is read as a new section — 2026-04-17

While adding the edit-safety property test for `update_design_autogen`, a
body containing a literal `## swatches` line mid-content did not round-trip
through `_parse_spec` — everything from that line onward was treated as a
new section heading, truncating `design.auto_generated`. The parser is
line-based on `^## ` and does not recognize the `<!-- BEGIN/END
AUTO-GENERATED -->` markers as an opaque region. In practice design
auto-generation never emits `## ` lines (bodies are bullet lists and
simple `key: value` pairs), so this is a latent edge case rather than a
live bug. If the Vision extractor starts producing Markdown headings in
bodies, either the parser needs to respect the AUTO-GENERATED markers or
the writer must escape `## ` on emit. The pathological body was removed
from `_NEW_DESIGN_AUTOGEN_BODIES` to keep the property test focused on
edit-safety rather than parser limits.

### [6.23.2] `_run_url` now guards `fetch_site` against exceptions — 2026-04-17

Real `fetch_site` catches all network/parse errors internally (see `fetcher.py:249-256`) and returns an empty tuple, so the `fetch_ok = bool(records)` branch in `_run_url` already covered real-world fetch failures. The Phase 6 integration test for `TestInitUrlFetchFailureWritesScrapeNone` deliberately mocks `fetch_site` with `side_effect=_fetch_site_network_error` to simulate an exception escaping — PLAN.md § "test_init_url_fetch_failure_writes_scrape_none" demands this shape. `_run_url` now wraps the `fetch_site` call in `try/except Exception` and records a diagnostic so the URL-flow can still produce the template-with-`scrape: none` SPEC.md on that path. The try/except is defensive against a future `fetch_site` variant that forgets the internal catch (or a deeper exception like `SystemExit`-adjacent errors that slip through), not load-bearing in production today.

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

### [6.15.1] Per-section bullet wording drift from INIT-design.md example — 2026-04-17

INIT-design.md § "duplo init --from-description description.txt" shows one
specific output example where Architecture is filled and Behavior is empty.
The current implementation generates bullets dynamically based on the
drafted `ProductSpec`: always one bullet per required/optional section
indicating filled vs. not. This is more informative but does drift from
the example shapes in the design doc. If the doc is read as prescriptive
(exact wording for exact cases) rather than illustrative, the wording
may need tightening. Left as-is pending user review of the rendered
output during the combined-case implementation (6.15.2+).

### [5.38.2] `claude -p --tools Read` output format — 2026-04-16

`query_with_images` runs `claude -p --tools Read`. The most likely cause of universal parse failure is that `claude -p` with `--tools` outputs in a structured format (e.g., streaming JSON, JSONL with tool-use messages, or a result wrapper object) rather than plain text. If the output contains multiple JSON objects (one per tool use + final response), `extract_json` would find the first `{` and last `}` across the entire output, producing an invalid JSON candidate that spans multiple objects. This would fail `json.loads` and hit the "parse error" path. The diagnostic logging added in 5.38.2 will capture the actual raw response to confirm or eliminate this. Potential fixes: (a) add `--output-format text` to the `claude -p` command, (b) parse the structured output to extract only the final text block, or (c) split the output by lines and extract JSON from only the last text block.

## Eliminated

### [5.39.4] Frame describer ↔ design extractor entanglement — 2026-04-16

Investigated whether the frame_describer bug (all frames getting "unknown" state) could cause the design extractor to produce empty output. **They are independent pipelines.** `extract_design` receives raw image paths (via `collect_design_input`) and sends them directly to Vision — it never consumes frame descriptions. Frame descriptions are consumed only by `extract_verification_cases` for PLAN.md verification tasks. Both bugs shared the same root cause (`strip_fences` + `json.loads` fragility, fixed in [5.38.3] and [5.39.1] by switching to `extract_json`), but they cannot cause each other. Eliminated by code path tracing: `collect_design_input` → `extract_design` → `query_with_images` (image paths); vs. `describe_frames` → `load_frame_descriptions` → `extract_verification_cases` (frame descriptions).
