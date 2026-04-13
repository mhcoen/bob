# `spec_drafter.py` design

This document specifies the module that writes SPEC.md: drafting
during `duplo init`, appending proposed/discovered entries on
subsequent runs, and inserting AUTO-GENERATED blocks in
`## Design`.

The parser (`PARSER-design.md`) reads SPEC.md and produces
typed data. The drafter writes SPEC.md. They're inverse modules
and share dataclass definitions.

`spec_drafter.py` is a new module. There is no existing
equivalent in the codebase — today, SPEC.md is entirely
user-authored, and duplo never writes to it.


## Goals

1. Generate a fresh SPEC.md from inputs (URL scrape, prose
   description, existing `ref/` files) for `duplo init`.
2. Append proposed/discovered entries to existing `## Sources`
   and `## References` sections without disturbing other content.
3. Insert and update AUTO-GENERATED blocks inside `## Design`
   without overwriting user-authored prose.
4. Preserve all user edits to all sections — never modify
   user content.
5. Produce SPEC.md that round-trips cleanly through the parser
   (write, read, write again, get the same file).


## Non-goals

1. Reading SPEC.md. That's the parser.
2. Validating SPEC.md content (e.g. checking that URLs are
   reachable, file paths exist). That's the validator.
3. Running LLM calls directly. The drafter takes structured
   input from callers (which may have used LLM calls to
   produce that input) and serializes it. Keeping LLM use
   out of the drafter makes it easier to test.

   Exception: drafting a fresh SPEC.md from a URL scrape or
   prose description does require an LLM call to translate
   inputs into draft section content. That LLM call is
   isolated in `_draft_from_inputs` (described below) and the
   rest of the module operates on already-structured data.


## API

### `draft_spec(inputs) -> str`

Generate a fresh SPEC.md from `DraftInputs`. Returns the SPEC.md
content as a string. Used by `duplo init`.

```python
@dataclass
class DraftInputs:
    """Inputs for drafting a fresh SPEC.md."""
    url: str | None = None
    url_scrape: str | None = None       # raw scraped text from URL
    description: str | None = None      # user-supplied prose
    existing_ref_files: list[Path] = ... # files already in ref/
    vision_proposals: dict[Path, str] = ... # path → proposed role
```

The function:

1. Calls `_draft_from_inputs(inputs)` (LLM call) to get a
   `ProductSpec` with sections filled in based on what inputs
   provided. Sections without enough input get `<FILL IN>`
   markers.
2. **If a `description` was provided**, copy the original
   prose verbatim into `spec.notes` with a labeled header.
   Do NOT let the LLM invent or paraphrase content for
   `## Notes`. The LLM may produce structured summaries in
   the typed sections (`## Purpose`, etc.) but `## Notes`
   preserves the user's original words. Format:

   ```
   ## Notes

   Original description provided to `duplo init`:

   <verbatim prose>
   ```

   This guarantees no description content is lost or distorted
   even if the LLM's structured extraction missed nuances.
3. Adds `SourceEntry` for the URL (if any), with role
   `product-reference` and scrape `deep`.
4. Adds `ReferenceEntry` for each existing ref/ file with
   `proposed: true` and the role from `vision_proposals`.
5. Calls `format_spec(spec)` to serialize.
6. Returns the resulting string.

Caller writes the returned string to SPEC.md.


### `format_spec(spec: ProductSpec) -> str`

Serialize a `ProductSpec` to SPEC.md format. The inverse of
the parser. Used by `draft_spec` and by tests.

The output:

- Starts with the standard top-matter comment block
  (the same block from `SPEC-template.md`).
- Renders sections in canonical order: `## Purpose`, `## Sources`,
  `## References`, `## Architecture`, `## Design`, `## Scope`,
  `## Behavior`, `## Notes`.
- Includes section comment blocks (the `<!-- ... -->` hints
  from the template) for sections that are empty or contain
  `<FILL IN>` markers.
- Omits comment blocks for sections the user has filled in
  with content (no point repeating hints once content is there).
- Renders `## Sources` and `## References` entries with one
  blank line between entries.
- Renders empty optional sections with just the heading and
  the comment block (no `<FILL IN>` for optional sections).
- Renders empty required sections (`## Purpose`,
  `## Architecture`) with the `<FILL IN>` marker from the
  template.

Round-trip property: `parse(format_spec(spec)) == spec` for
any well-formed `ProductSpec`. Tests pin this.


### `append_sources(existing: str, new_entries: list[SourceEntry]) -> str`

Append source entries to an existing SPEC.md's `## Sources`
section without modifying anything else.

The function:

1. Parses existing SPEC.md to find the `## Sources` section.
2. If the section doesn't exist, errors (caller should ensure
   the section exists, possibly by template-generating it
   first).
3. Identifies the insertion point: end of the section, before
   the next `##` heading.
4. **Canonicalizes every new entry's URL** via
   `canonicalize_url` (specified in PIPELINE-design.md § "URL
   canonicalization") before any comparison. Existing entries
   in the SPEC.md section were already canonicalized by the
   parser on read, so both sides of the dedup comparison use
   the same form. The canonicalized URL is also what gets
   serialized into the file, so a future parse will find the
   entry already in canonical form.
5. **Applies counter-example scrape coercion** to each new
   entry before serializing: if `entry.role == "counter-example"`
   and `entry.scrape in {"deep", "shallow"}`, rewrite to
   `entry.scrape = "none"` and emit a diagnostic. This
   mirrors the parse-time coercion in PARSER-design.md and
   prevents a one-run-stale-file window where a Phase 4
   drafter infers a counter-example role with a non-`none`
   scrape and serializes the wrong combination — the parser
   would fix it on next read, but the intervening file state
   would be incorrect. `append_sources` fixes it at write
   time so the file is never incorrect. (Phase 3 only
   constructs `role="docs"` entries through this path, so
   the coercion is a no-op for Phase 3; Phase 4's drafter
   depends on it.)
6. **Deduplicates against existing entries**: any new entry
   whose canonical URL already appears in the section
   (regardless of role, scrape depth, or flags) is skipped,
   with a diagnostic. This prevents the failure mode where
   a deep crawl rediscovers the same cross-origin URL on
   every run and re-appends it indefinitely until the user
   removes the `discovered: true` flag.
7. Serializes each remaining new entry in canonical format
   with `discovered: true` or `proposed: true` set on the
   entry.
8. Inserts the entries before the section boundary.
9. Returns the modified content.

Deduplication is URL-only, comparing canonical forms. Two
entries with the same URL but different roles are still
considered duplicates; the first-write-wins rule applies. The
dedup rule matches `_collect_cross_origin_links` in
PIPELINE-design.md by construction: both call `canonicalize_url`,
so a URL that passes one dedup cannot fail the other.

Side-effect-free: takes existing content as a string argument
rather than reading SPEC.md itself. Caller reads, calls
`append_sources`, writes back.


### `append_references(existing: str, new_entries: list[ReferenceEntry]) -> str`

Same as `append_sources` but for `## References`. Deduplication
is path-only (after path normalization). Two entries with the
same path but different roles are duplicates; first-write-wins.


### `update_design_autogen(existing: str, design_content: str) -> str`

Insert an AUTO-GENERATED block inside `## Design` if one does
not already exist. If a block already exists, leave it alone.

The function:

1. Parses existing SPEC.md to find the `## Design` section.
2. If an AUTO-GENERATED block already exists in the section,
   return the content unchanged. Emit a diagnostic noting
   that the block was preserved (so the user knows duplo
   considered updating it).
3. If no AUTO-GENERATED block exists, append one to the end of
   the `## Design` section.
4. Returns the modified content.

The block is delimited by:

```
<!-- BEGIN AUTO-GENERATED: design details extracted from ref/ images.
     Delete this entire block (markers and content) to regenerate
     on the next duplo run. -->
{design_content}
<!-- END AUTO-GENERATED -->
```

The write-once-never-replace rule is the simplest contract
that preserves the user-trust property ("duplo will not
overwrite manual changes"). To get a fresh extraction, the
user deletes the entire block; duplo writes a new one on the
next run.

This avoids hash-based edit detection, the `.duplo/design_hash`
file, and the failure modes that come with stale or missing
hashes (a deleted hash file would cause an edited block to be
clobbered).

The tradeoff: a user who changes their `visual-target`
references and wants updated extraction must explicitly delete
the block. The block's comment text tells them exactly how.
This is acceptable because design extraction is the kind of
thing users want to review before re-running anyway — surfacing
the "do you really want to regenerate" decision is feature, not
friction.


## Drafting from inputs (the LLM call)

`_draft_from_inputs(inputs: DraftInputs) -> ProductSpec` is the
internal function that turns raw inputs into a `ProductSpec`
with section content. This is the only place in the drafter
that calls an LLM.

The function:

1. Builds a structured-output prompt for Claude:

   ```
   You are drafting a SPEC.md for a software project.
   Given the inputs below, produce a JSON object with these
   fields:

   - purpose: one or two sentences describing what to build,
     or null if you can't determine it from inputs.
   - architecture: language/framework/platform constraints,
     ONLY IF the description prose explicitly states a stack,
     platform, or language. Do NOT infer architecture from
     scraped product pages or from product identity. Return
     null otherwise.
   - design: visual direction (colors, typography, aesthetic),
     or null if not specified.
   - behavior_contracts: list of {input, expected} pairs
     extracted from inputs, or empty list.
   - scope_include: list of feature names the user explicitly
     wants, or empty list.
   - scope_exclude: list of feature names the user explicitly
     doesn't want, or empty list.

   Inputs:
   {url and scrape}
   {description prose}
   {ref/ file inventory}

   Return ONLY the JSON object.
   ```

   Note: `notes` is deliberately NOT in the schema. The
   `## Notes` section is populated by `draft_spec` (not the
   LLM) by copying the original description prose verbatim.
   See `draft_spec` step 2.

2. Sends to Claude via `claude_cli.query`.
3. Parses the JSON response.
4. Constructs a `ProductSpec` with:
   - Filled fields from JSON (when not null/empty).
   - `<FILL IN>` markers for fields the LLM returned null for,
     IF the field is required (purpose, architecture).
   - Empty content for optional fields the LLM returned null for.

The LLM is allowed to leave fields null when it has nothing to
go on. Filling in `<FILL IN>` markers happens at the
`ProductSpec` → SPEC.md serialization step, not in the LLM
prompt.

Why JSON output rather than markdown directly: the parser
expects a specific markdown structure. Asking the LLM to
produce that structure correctly is brittle. Asking it for
JSON, then formatting that into markdown deterministically,
is robust. Same pattern used elsewhere in the codebase
(`_deduplicate_features_llm`, `_find_duplicate_groups`,
`_propagate_implemented_status` in `saver.py`).


## Inferring URL roles

When a description mentions a URL (e.g. "like numi at
https://numi.app"), the drafter extracts the URL and adds it
to `## Sources` with `proposed: true`. The role is inferred
from context:

- "like X" / "such as X" / "inspired by X" → `product-reference`.
- "see also X" / "X for reference" → `docs`.
- "not like X" / "unlike X" / "avoid X" → `counter-example`.
- Default: `product-reference`.

The inference is light heuristic, not LLM-based, because it
runs once per URL extracted from prose and the cost of
getting it wrong is just a flag the user removes.

`proposed: true` is set on the entry. `discovered: true` is
NOT set (discovered is reserved for URLs duplo found via
crawling, not URLs extracted from user prose).


## Inferring file roles via Vision

When existing files are in `ref/` during `duplo init`, the
drafter calls `design_extractor.extract_design` (or a similar
Vision call) on each image to propose a role.

The Vision prompt is augmented to ask "what role does this
image play":

```
Look at this image and answer two questions:
1. Describe the visual content (1 sentence).
2. What role does this play in a software project? Choose ONE:
   - visual-target: a screenshot or mockup of a UI to build
   - behavioral-target: a recording or sequence showing how
     an app behaves
   - docs: a diagram, spec illustration, or reference figure
   - counter-example: a screenshot of something to AVOID
   - ignore: irrelevant to building the product (e.g. a logo,
     stock photo, or unrelated image)

Return JSON: {"description": "...", "role": "..."}
```

For non-image files (PDFs, text, video):

- PDFs default to `docs`.
- Text/markdown files default to `docs`.
- Videos default to `behavioral-target`.

These defaults are written with `proposed: true` so the user
reviews and corrects.


## Edit safety

Three rules the drafter enforces:

1. **Never modify user-authored content.** Sections other than
   `## Sources`, `## References`, and `## Design`
   AUTO-GENERATED block are read-only to the drafter.

2. **Always preserve unrecognized content.** If a user adds a
   custom section (e.g. `## Custom`), the drafter preserves
   it byte-for-byte during any write. The append/update
   functions operate on specific sections; everything else
   passes through untouched.

3. **Always preserve formatting in untouched sections.** If a
   user formats `## Architecture` with their own indentation,
   blank lines, etc., the drafter doesn't reformat it during
   writes to other sections. The append/update functions
   work at the section-boundary level, not the file level.

The implementation strategy that supports these: load the
existing file, locate the section to modify by parsing
just enough to find boundaries, modify only that section's
content, leave the surrounding text exactly as it was.

```python
def _modify_section(content: str, section_name: str, new_section_content: str) -> str:
    lines = content.splitlines(keepends=True)
    # Find section start and end (next ## heading or EOF).
    start = _find_section_start(lines, section_name)
    if start is None:
        raise ValueError(f"Section {section_name} not found in SPEC.md")
    end = _find_next_section_start(lines, start + 1) or len(lines)
    # Replace section body, preserve heading line.
    return "".join(lines[:start + 1]) + new_section_content + "".join(lines[end:])
```

This is the same pattern the existing `saver.py::append_to_bugs_section`
uses for PLAN.md. Reuse the same approach for consistency.


## Round-trip testing

A property test:

```python
def test_round_trip(spec: ProductSpec):
    serialized = format_spec(spec)
    parsed = parse(serialized)
    # Compare every field except `raw` and the `dropped_*`
    # lists. See _spec_equal_for_round_trip below.
    assert _spec_equal_for_round_trip(parsed, spec)
```

Where `_spec_equal_for_round_trip` excludes fields that
legitimately do not survive round-tripping:

```python
_ROUND_TRIP_EXCLUDED_FIELDS = {
    "raw",                  # parsed.raw == serialized, not spec.raw
    "dropped_sources",      # not serialized (format_spec has no
                            # notion of "dropped"); a round-tripped
                            # spec has empty dropped_* regardless
                            # of the original's content.
    "dropped_references",
}

def _spec_equal_for_round_trip(a: ProductSpec, b: ProductSpec) -> bool:
    fields = [
        f for f in dataclasses.fields(ProductSpec)
        if f.name not in _ROUND_TRIP_EXCLUDED_FIELDS
    ]
    return all(getattr(a, f.name) == getattr(b, f.name) for f in fields)
```

The `dropped_*` exclusion matters because `format_spec` does
not (and should not) serialize those lists — they represent
entries the parser rejected at read time. A round-tripped
spec will have empty `dropped_*` lists regardless of what the
original contained, so including them in the comparator
produces spurious failures for generators that populate them.

Test-generator note: generators producing `ProductSpec`
instances for this property test MAY populate `dropped_*`
(the comparator ignores those fields), but a separate test
pins that `dropped_*` entries round-trip as empty lists
— documenting the asymmetry rather than hiding it.

Run against generated `ProductSpec` instances covering all
field combinations. Catches any drift between parser and
drafter on the fields that DO survive round-tripping.

A second property test:

```python
def test_modify_preserves_other_sections(spec: ProductSpec, new_entry: SourceEntry):
    original = format_spec(spec)
    modified = append_sources(original, [new_entry])
    parsed = parse(modified)
    # Every field other than sources is unchanged.
    spec_with_new_source = replace(spec, sources=spec.sources + [new_entry])
    assert parsed == spec_with_new_source
```

This tests the edit-safety property concretely.


## Error handling

The drafter raises specific exceptions for caller handling:

- `SectionNotFound(name)` — append/update called on a section
  that doesn't exist in the file.
- `MalformedSpec(reason)` — parse-during-modify failed because
  the existing file isn't valid SPEC.md format. Caller should
  decide whether to overwrite or bail.
- `DraftingFailed(reason)` — LLM call in `_draft_from_inputs`
  failed (timeout, parse error, etc.). Caller should fall back
  to template-only draft (the static `SPEC-template.md`
  contents).

No silent failures. Every error gets either an exception or a
diagnostic via `record_failure`.


## Implementation order

When this becomes mcloop tasks:

1. Add the dataclasses (`DraftInputs`). Tests.
2. Implement `format_spec` for empty `ProductSpec` (template
   output). Tests.
3. Extend `format_spec` to handle filled fields. Tests for
   each section.
4. Implement round-trip test infrastructure. Run against
   parser. Pin baseline.
5. Implement `_modify_section` helper. Tests.
6. Implement `append_sources`, including canonicalization via
   `canonicalize_url` and counter-example scrape coercion. Tests
   pin the dedup behavior across `/docs` vs `/docs/` variants,
   the coercion (counter-example + scrape: deep → scrape: none
   on write), and the no-op behavior for Phase 3's `role="docs"`
   callers. Tests.
7. Implement `append_references`. Tests.
8. Implement `update_design_autogen` with write-once-never-
   replace semantics. Tests, including the case where the
   block already exists (no-op).
9. Implement role-inference heuristics for URLs (regex-based).
   Tests.
10. Implement `_draft_from_inputs` (the LLM call). Tests with
    mocked LLM responses for each input combination
    (URL only, prose only, both, neither).
11. Implement `draft_spec` orchestrator. Integration tests
    against the four `duplo init` input combinations.
12. Wire `draft_spec` into `duplo/init.py`.

Steps 1-9 are pure-functional and easy to test. Steps 10-12
involve the LLM and need mocking discipline.


## Open questions

1. **The `## Notes` section.** Resolved: the LLM does NOT
   write to `## Notes`. When `--from-description` is used,
   the original prose is copied verbatim into `## Notes`
   under a labeled header. The LLM's structured outputs
   populate the typed sections only.

2. **AUTO-GENERATED block hash storage.** Resolved: no hash.
   Write-once-never-replace; user deletes block to regenerate.

3. **The drafter's behavior when `## Design` exists but has
   no AUTO-GENERATED block AND user_prose is non-empty.**
   The new block is appended below the user_prose. The block's
   header comment explains the order of precedence (user prose
   above the block takes precedence over the block content).

4. **What if the LLM returns malformed JSON?** Two retry
   attempts (with backoff), then fall back to template-only
   draft with a diagnostic. Better than a hard failure during
   `duplo init`.

5. **Concurrency.** Out of scope. duplo is single-user; users
   running two `duplo init` processes simultaneously in the
   same directory will race, same as today's pipeline.
