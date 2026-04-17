# Phase 6: Drafter and duplo init

Adds duplo init and the full spec-drafter that creates SPEC.md entries from URL scrapes, prose descriptions, and existing reference files (via Vision). Updates the migration message from Phase 4 to reference duplo init.

Design references: DRAFTER-design.md (authoritative for spec_writer.py extensions), INIT-design.md (authoritative for duplo init UX and behavior). When a task description and the design doc disagree, the design doc wins; flag the discrepancy for resolution rather than silently picking one interpretation.

The module the design docs call spec_drafter.py is implemented as duplo/spec_writer.py. Phase 5 already shipped append_sources and update_design_autogen there. This phase adds the remaining drafter functions: format_spec, append_references, _draft_from_inputs, draft_spec, and the role-inference helpers.

Python 3.11+, depends on McLoop. Uses Claude Code via McLoop for all code generation. Ruff for linting, pytest for tests. All AI calls go through claude -p (no direct API calls).

## Drafter: DraftInputs and format_spec

- [x] [BATCH] Add DraftInputs dataclass to duplo/spec_writer.py
  - [x] Add DraftInputs dataclass with fields: url (str or None), url_scrape (str or None), description (str or None), existing_ref_files (list[Path], default empty), vision_proposals (dict[Path, str], default empty). Per DRAFTER-design.md section DraftInputs.
  - [x] Tests: dataclass construction with all fields; default values for optional fields; field types enforced.

- [x] Implement format_spec(spec: ProductSpec) -> str in duplo/spec_writer.py
  - [x] Serialize a ProductSpec to SPEC.md format. The inverse of the parser. Per DRAFTER-design.md section format_spec.
  - [x] Start with the standard top-matter comment block (the same block from SPEC-template.md, including the "How the pieces fit together:" marker string).
  - [x] Render sections in canonical order: Purpose, Sources, References, Architecture, Design, Scope, Behavior, Notes.
  - [x] For empty required sections (Purpose, Architecture): write the FILL IN marker from the template.
  - [x] For empty optional sections (Design, Scope, Behavior, Notes): write just the heading and the comment hint from the template. No FILL IN marker.
  - [x] For filled sections: write heading and content. Omit comment hints when content is present.
  - [x] Sources entries: serialize each SourceEntry with one blank line between entries, using the same format as _format_entry.
  - [x] References entries: serialize each ReferenceEntry with one blank line between entries, including roles (comma-separated), notes, and proposed flag.
  - [x] Design section: if DesignBlock has user_prose, write it first. If auto_generated is present, write the AUTO-GENERATED block after user_prose using the same markers the parser expects.
  - [x] Scope section: serialize scope_include and scope_exclude lists in the template format (include:/exclude: with indented list items).
  - [x] Behavior section: serialize behavior_contracts as input/output pairs in the template format.
  - [x] Tests: empty ProductSpec produces template-like output with FILL IN markers on required sections; fully populated ProductSpec serializes all sections; Sources and References entries formatted correctly with flags; Design section with user_prose and auto_generated renders both in order; Scope include/exclude rendered; Behavior contracts rendered; empty optional sections get heading and comment only.

- [x] Implement round-trip property test for format_spec
  - [x] Per DRAFTER-design.md section "Round-trip testing". Property: parse(format_spec(spec)) equals spec for all surviving fields.
  - [x] Implement _spec_equal_for_round_trip comparator that excludes raw, dropped_sources, and dropped_references fields per DRAFTER-design.md.
  - [x] Use hand-rolled fixture generation (not Hypothesis) to cover: empty spec, spec with all sections filled, spec with mixed filled/empty sections, spec with Sources and References containing proposed/discovered flags, spec with DesignBlock containing both user_prose and auto_generated, spec with scope_include and scope_exclude, spec with behavior_contracts.
  - [x] Add a separate test pinning that dropped_sources and dropped_references round-trip as empty lists (documenting the asymmetry per DRAFTER-design.md).
  - [x] Tests: each fixture round-trips; dropped fields excluded from comparison; round-tripped spec has empty dropped lists.

## Drafter: append_references

- [x] [BATCH] Implement append_references(existing: str, new_entries: list[ReferenceEntry]) -> str in duplo/spec_writer.py
  - [x] Same pattern as append_sources but for the References section. Per DRAFTER-design.md section append_references.
  - [x] Deduplication is path-only: two entries with the same path (after normalization) are duplicates regardless of role. First-write-wins.
  - [x] Path normalization: compare paths as-is (no resolve, no symlink following). Paths are always relative to project root and start with ref/. Comparison is string equality after stripping any trailing slash.
  - [x] If References section does not exist, create it after Sources (if present), else after Purpose (if present), else at end of file.
  - [x] Serialize each new entry with roles (comma-separated), notes, and proposed flag.
  - [x] Side-effect-free: takes existing content as string, returns modified string.
  - [x] Tests: append single new entry; append multiple; dedup against existing path (entry not added); dedup is path-only (same path with different role still deduplicates); idempotent (double-call returns same result); empty new_entries returns input unchanged; missing References section is created; proposed flag written correctly; multiple roles serialized as comma-separated; entry with notes serialized correctly.

## Drafter: role inference helpers

- [x] [BATCH] Implement URL role inference heuristics in duplo/spec_writer.py
  - [x] Per DRAFTER-design.md section "Inferring URL roles". Regex-based, not LLM-based.
  - [x] Add _infer_url_role(context: str) -> str function. Takes the surrounding prose context where a URL was mentioned.
  - [x] Rules: "like X" / "such as X" / "inspired by X" returns product-reference. "see also X" / "X for reference" returns docs. "not like X" / "unlike X" / "avoid X" returns counter-example. Default: product-reference.
  - [x] Case-insensitive matching.
  - [x] Tests: each pattern produces the expected role; default when no pattern matches; case-insensitive; multiple patterns in same context uses the first match.

- [x] [BATCH] Implement Vision-based file role inference in duplo/spec_writer.py
  - [x] Per DRAFTER-design.md section "Inferring file roles via Vision". Add _propose_file_role(path: Path) -> tuple[str, str] returning (description, role).
  - [x] For image files (.png, .jpg, .gif, .webp): call claude -p with the Vision prompt from DRAFTER-design.md that asks for description and role from the enum (visual-target, behavioral-target, docs, counter-example, ignore). Parse JSON response.
  - [x] For non-image files: use extension-based defaults. PDFs default to docs. Text/markdown files default to docs. Videos (.mp4, .mov, .webm, .avi) default to behavioral-target.
  - [x] All results are proposals (proposed: true is set by the caller, not by this function).
  - [x] Retry logic: two retry attempts with backoff on LLM failure, then fall back to ignore role with a diagnostic.
  - [x] Tests: image file triggers claude -p Vision call (mocked) and parses JSON response; PDF defaults to docs without Vision call; text file defaults to docs; video defaults to behavioral-target; unknown extension defaults to ignore with diagnostic; LLM failure after retries falls back to ignore; JSON parse error falls back to ignore with diagnostic.

## Drafter: _draft_from_inputs and draft_spec

- [x] Implement _draft_from_inputs(inputs: DraftInputs) -> ProductSpec in duplo/spec_writer.py
  - [x] Per DRAFTER-design.md section "Drafting from inputs". The only LLM call in the drafter.
  - [x] Build structured-output prompt for claude -p per DRAFTER-design.md: request JSON with fields purpose, architecture, design, behavior_contracts, scope_include, scope_exclude.
  - [x] Architecture is filled ONLY when description prose explicitly states a stack/platform/language. URL scrapes do NOT inform architecture. Per DRAFTER-design.md and INIT-design.md.
  - [x] notes is deliberately NOT in the LLM schema (populated by draft_spec from raw description prose).
  - [x] Parse JSON response. Strip code fences before parsing (reuse strip_fences from duplo/parsing.py).
  - [x] Construct ProductSpec with: filled fields from JSON (when not null/empty); FILL IN markers for required fields the LLM returned null for; empty content for optional fields the LLM returned null for.
  - [x] Retry logic: two retry attempts with backoff on LLM failure or JSON parse error, then fall back to empty ProductSpec (template-only draft) with a diagnostic per DRAFTER-design.md section "Error handling".
  - [x] Tests (all with mocked claude -p): URL-only input produces purpose from scrape, architecture null; prose-only input produces purpose and architecture when prose states a stack; prose that does not state a stack produces architecture null; both URL and prose merges them (prose wins on conflicts per INIT-design.md); neither URL nor prose produces empty ProductSpec; LLM returns malformed JSON triggers retry then fallback; LLM returns null for all fields produces template-like spec.

- [x] Implement draft_spec(inputs: DraftInputs) -> str in duplo/spec_writer.py
  - [x] Per DRAFTER-design.md section draft_spec. Orchestrates _draft_from_inputs and format_spec.
  - [x] Step 1: call _draft_from_inputs(inputs) to get a ProductSpec.
  - [x] Step 2: if inputs.description was provided, copy the original prose verbatim into spec.notes under a labeled header per DRAFTER-design.md: "Original description provided to duplo init:" followed by the verbatim prose. The LLM does NOT write notes.
  - [x] Step 3: add SourceEntry for the URL (if any) with role product-reference and scrape deep. No proposed/discovered flag (user provided the URL explicitly).
  - [x] Step 4: add ReferenceEntry for each existing ref/ file with proposed: true and the role from inputs.vision_proposals.
  - [x] Step 5: call format_spec(spec) to serialize.
  - [x] Step 6: return the resulting string.
  - [x] Tests: URL-only inputs produce SPEC.md with Sources entry and pre-filled Purpose; prose-only inputs produce SPEC.md with Notes containing verbatim prose; both inputs produce merged SPEC.md; existing ref/ files produce References entries with proposed: true; vision_proposals roles appear on the entries; format_spec output passes parser round-trip.

## Drafter: error handling

- [ ] [BATCH] Add drafter exception classes to duplo/spec_writer.py
  - [ ] Per DRAFTER-design.md section "Error handling". Add SectionNotFound(name: str), MalformedSpec(reason: str), DraftingFailed(reason: str) exception classes.
  - [ ] SectionNotFound: raised by append/update functions when the target section is not in the file.
  - [ ] MalformedSpec: raised when parse-during-modify fails because the existing file is not valid SPEC.md format.
  - [ ] DraftingFailed: raised when the LLM call in _draft_from_inputs fails after retries. Caller falls back to template-only draft.
  - [ ] Tests: each exception class can be instantiated and carries its message; draft_spec catches DraftingFailed and falls back to template-only output.

## Edit-safety property test

- [ ] Add edit-safety property test per DRAFTER-design.md section "Round-trip testing"
  - [ ] Property: for any well-formed ProductSpec and any new SourceEntry, append_sources(format_spec(spec), [new_entry]) produces a spec where every field other than sources is unchanged after re-parsing.
  - [ ] Same property for append_references with ReferenceEntry.
  - [ ] Same property for update_design_autogen: all fields other than design.auto_generated unchanged.
  - [ ] Tests: each property exercised with multiple fixture combinations; unrecognized/custom sections preserved byte-for-byte through modify operations.

## duplo init: argument parsing

- [ ] [BATCH] Add init subcommand to argument parser in duplo/main.py
  - [ ] Per INIT-design.md section "Command surface". Add init as a recognized subcommand alongside fix and investigate.
  - [ ] Positional argument: url (optional). Validated as starting with http:// or https://.
  - [ ] Flag: --from-description PATH (or - for stdin). Path to a text file containing prose description.
  - [ ] Flag: --deep (boolean, default false). Opt-in to deep scraping during init.
  - [ ] Flag: --force (boolean, default false). Overwrite existing SPEC.md.
  - [ ] Dispatch to duplo.init.run_init(args) when subcommand is init.
  - [ ] init subcommand bypasses migration check (same as fix and investigate). Per MIGRATION-design.md: migration check applies only to the no-subcommand path.
  - [ ] Tests: argparse accepts duplo init with no args; accepts duplo init URL; accepts duplo init --from-description FILE; accepts duplo init URL --from-description FILE; accepts --deep and --force flags; rejects invalid URL (not http/https); init dispatches to run_init (mock and assert called).

## duplo init: core implementation

- [ ] Create duplo/init.py with run_init(args) entry point
  - [ ] Per INIT-design.md section "Implementation shape". New module with a single run_init entry point.
  - [ ] Dependencies: duplo.spec_writer (for draft_spec, format_spec), duplo.fetcher (for fetch_site with scrape_depth), duplo.validator (for validate_product_url), duplo.scanner (for scan_directory on ref/).

- [ ] Implement run_init for the no-arguments case
  - [ ] Per INIT-design.md section "duplo init (no arguments)".
  - [ ] Check for existing SPEC.md: if present and --force not set, print error message per INIT-design.md and exit 1.
  - [ ] Create ref/ directory if it does not exist.
  - [ ] Write ref/README.md with the static content from INIT-design.md section "ref/README.md content". Write-once: do not overwrite if ref/README.md already exists.
  - [ ] Write SPEC.md with the static SPEC-template.md content (via format_spec on an empty ProductSpec).
  - [ ] Print the output message per INIT-design.md: "Created ref/...", "Wrote SPEC.md (template, no inputs).", and the "Next steps:" block.
  - [ ] Exit 0.
  - [ ] Tests: SPEC.md written with template content; ref/ created; ref/README.md written; existing SPEC.md without --force exits 1 with error message; existing SPEC.md with --force overwrites; existing ref/ not recreated; existing ref/README.md not overwritten; output messages match INIT-design.md.

- [ ] Implement run_init for the URL-only case
  - [ ] Per INIT-design.md section "duplo init URL".
  - [ ] Canonicalize URL via url_canon.canonicalize_url before any use (per INIT-design.md error cases).
  - [ ] Call fetch_site(url, scrape_depth="shallow") for product identity. If --deep flag set, use scrape_depth="deep" instead.
  - [ ] On fetch success: extract product identity from scraped content. Build DraftInputs with url and url_scrape populated. Call draft_spec(inputs).
  - [ ] On fetch failure (network error, NXDOMAIN, timeout): per INIT-design.md section "URL fetch fails", continue with template-only setup. Write URL to Sources with scrape: none. Print failure message. Exit 0 (not 1).
  - [ ] On fetch success but no product identified: per INIT-design.md section "URL fetch succeeds but identifies nothing", pre-fill Sources only. Leave Purpose as FILL IN.
  - [ ] Scan existing ref/ files (if ref/ exists and has files): call _propose_file_role for each image, use extension defaults for non-images. Populate DraftInputs.vision_proposals. Per INIT-design.md section "ref/ already exists with files".
  - [ ] Write SPEC.md from draft_spec output. Create ref/ and ref/README.md as in the no-arguments case.
  - [ ] Print output per INIT-design.md (shallow scrape message, product identity, pre-filled sections, next steps).
  - [ ] Tests (all with mocked fetch_site and claude -p): successful scrape produces pre-filled Purpose and Sources; failed scrape writes URL with scrape: none and exits 0; unidentified product fills Sources only; existing ref/ files get role proposals with proposed: true; --deep flag passes scrape_depth="deep" to fetch_site; --force overwrites existing SPEC.md; URL canonicalized before writing to Sources.

- [ ] Implement run_init for the --from-description case
  - [ ] Per INIT-design.md section "duplo init --from-description description.txt".
  - [ ] Read description from file path or stdin (- argument). If file not found, print error per INIT-design.md and exit 1.
  - [ ] If stdin: print "Reading description from stdin. Press Ctrl-D when done." when stdin is a TTY.
  - [ ] Build DraftInputs with description populated. Call draft_spec(inputs).
  - [ ] Per DRAFTER-design.md: if prose mentions a URL, extract it and add to Sources with proposed: true and role inferred via _infer_url_role.
  - [ ] Write SPEC.md, create ref/, write ref/README.md.
  - [ ] Print output per INIT-design.md (character count, pre-filled sections, next steps).
  - [ ] Tests: description from file read correctly; description from stdin (mocked) read correctly; file not found exits 1; URL extracted from prose added to Sources with proposed: true; inferred role correct; Notes section contains verbatim prose.

- [ ] Implement run_init for the combined URL + --from-description case
  - [ ] Per INIT-design.md section "duplo init URL --from-description description.txt".
  - [ ] Build DraftInputs with both url/url_scrape and description populated.
  - [ ] Prose wins on conflicts per INIT-design.md.
  - [ ] Both error conditions checked: invalid URL and missing description file. Both errors reported if both fail per INIT-design.md.
  - [ ] Tests: combined inputs produce merged SPEC.md; prose-stated architecture overrides (URL-only would leave it as FILL IN); both errors reported simultaneously when both inputs are bad.

## duplo init: output discipline

- [ ] [BATCH] Ensure all init output follows INIT-design.md section "Output discipline"
  - [ ] Present-tense or simple-past for actions: "Fetched X.", "Pre-filled Y.", "Created Z."
  - [ ] Indented bullets with arrow for sub-results: "  -> Identified product: Numi"
  - [ ] "Next steps" sections with numbered items at the end of successful runs.
  - [ ] Errors to stderr, successful output to stdout.
  - [ ] No emoji, no color codes.
  - [ ] Tests: capture stdout/stderr and assert formatting rules hold for each input combination.

## Migration message update

- [ ] Update migration message in duplo/migration.py to reference duplo init
  - [ ] Per REDESIGN-overview.md section "Implementation phasing" Phase 4: "Update the migration message from Phase 2 to reference duplo init (one-line change)."
  - [ ] Replace step 3 in _MIGRATION_MESSAGE: change "Author a SPEC.md by hand. Use SPEC-template.md..." to "Run duplo init to generate a SPEC.md. Or author one by hand using SPEC-template.md..."
  - [ ] Add duplo init as the recommended path. Keep the manual-authoring option as an alternative.
  - [ ] Tests: update the migration-message snapshot test from Phase 4 to match the new wording. Pin the exact new message content.

## Automated integration tests

All Phase 6 end-to-end behaviors are verified by automated pytest integration tests, not by manual user runs. Each test constructs a fixture in a tmpdir, runs duplo init programmatically, and asserts on the output state. LLM calls must be mocked so tests do not depend on claude -p availability or network. All tests live in tests/test_phase6_integration.py (new file).

- [ ] Add tests/test_phase6_integration.py with test_init_no_args_produces_template
  - [ ] Run run_init with no URL, no description in a tmpdir.
  - [ ] Assert: SPEC.md exists and contains the marker string "How the pieces fit together:"; SPEC.md contains FILL IN markers for Purpose and Architecture; ref/ directory exists; ref/README.md exists and matches INIT-design.md content; needs_migration returns False for this directory.

- [ ] Add test_init_url_produces_prefilled_spec
  - [ ] Mock fetch_site to return a fixture scrape with identifiable product name.
  - [ ] Run run_init with a URL argument.
  - [ ] Assert: SPEC.md has pre-filled Purpose (non-empty, no FILL IN); Sources section contains the URL with role: product-reference and scrape: deep; Architecture still has FILL IN; SPEC.md round-trips through the parser without errors.

- [ ] Add test_init_description_produces_notes_with_verbatim_prose
  - [ ] Write a description.txt fixture. Mock the LLM call in _draft_from_inputs.
  - [ ] Run run_init with --from-description pointing to the fixture.
  - [ ] Assert: SPEC.md Notes section contains "Original description provided to duplo init:" followed by the exact prose from description.txt byte-for-byte; Purpose section populated from LLM output.

- [ ] Add test_init_with_existing_ref_files_proposes_roles
  - [ ] Create a tmpdir with ref/ containing a .png and a .pdf. Mock _propose_file_role for the image.
  - [ ] Run run_init.
  - [ ] Assert: SPEC.md References section contains entries for both files; both have proposed: true; image has Vision-inferred role; PDF has role: docs (extension default).

- [ ] Add test_init_url_fetch_failure_writes_scrape_none
  - [ ] Mock fetch_site to raise an exception (network error).
  - [ ] Run run_init with a URL argument.
  - [ ] Assert: exit code 0 (not 1); SPEC.md written; Sources contains the URL with scrape: none; Purpose has FILL IN marker.

- [ ] Add test_init_force_overwrites_existing_spec
  - [ ] Create a tmpdir with an existing SPEC.md containing custom content.
  - [ ] Run run_init with --force. Assert: SPEC.md overwritten with new content.
  - [ ] Run run_init without --force. Assert: exits 1 with error message; SPEC.md unchanged.

- [ ] Add test_init_then_duplo_run_works_end_to_end
  - [ ] Run run_init with a URL to produce SPEC.md. Then programmatically edit SPEC.md to fill in Architecture (remove FILL IN). Then run _subsequent_run against the same tmpdir.
  - [ ] Mock fetch_site (for the deep scrape), extract_features, and interactive selectors.
  - [ ] Assert: PLAN.md produced; no migration message printed; pipeline consumed SPEC.md correctly.

- [ ] Run the full test suite and confirm Phase 6 closes cleanly
  - [ ] Execute pytest -x against the duplo repo.
  - [ ] Assert: all pre-existing tests still pass; all new Phase 6 tests pass.
  - [ ] If any test fails, the task fails and mcloop will retry.

---
