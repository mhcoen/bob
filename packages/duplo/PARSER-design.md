# SPEC.md parser design

This document specifies how `duplo/spec_reader.py` evolves to handle
the new SPEC.md schema (`## Sources`, structured `## References`,
`<FILL IN>` markers, AUTO-GENERATED blocks, proposed/discovered flags).

It is a design document, not production code. It describes the
shape of the parser, the dataclasses, the validation rules, and
the migration path from the existing parser. Implementation
follows from this spec.

The current parser is `duplo/spec_reader.py` (commit ff05209). The
new parser extends it without breaking the existing field-level
API where possible.


## Goals

1. Parse the new SPEC.md sections (`## Sources`, structured
   `## References`, `## Notes`) into typed dataclasses.
2. Detect `<FILL IN>` markers in required sections so duplo can
   refuse to run with a clear error.
3. Detect `proposed: true` and `discovered: true` flags on entries
   so pipeline stages can skip unreviewed inferences.
4. Detect AUTO-GENERATED blocks inside `## Design` and parse their
   contents separately from user-authored design prose.
5. Preserve the existing `BehaviorContract` parsing and
   `ProductSpec` field-level API where possible. Two fields
   change type (see "ProductSpec extended" below); callers must
   be audited.
6. Provide per-stage formatters that filter by role and never
   leak unreviewed entries into LLM prompts.


## Non-goals

1. Writing SPEC.md. The parser only reads. Writes (drafting the
   template, appending proposed/discovered entries, inserting
   AUTO-GENERATED blocks) belong to a separate module
   (`spec_drafter.py`) covered by its own design doc.
2. Validating semantic correctness of user content (e.g. "is this
   a real URL", "does this file exist"). The parser produces
   structured data; semantic validation belongs to the pipeline
   stages that consume it.
3. Resolving SPEC.md against `.duplo/duplo.json` state. The
   parser produces the spec; orchestration code in `main.py`
   reconciles it with extracted state.


## Dataclasses

### `SourceEntry`

```python
@dataclass
class SourceEntry:
    """A URL declared in the ## Sources section."""
    url: str
    role: str               # "product-reference" | "docs" | "counter-example"
    scrape: str             # "deep" | "shallow" | "none"
    notes: str = ""
    proposed: bool = False  # duplo proposed this; user has not confirmed
    discovered: bool = False  # duplo found this via crawling; user has not confirmed
```

Validation:

- `url` must be a valid HTTP(S) URL (basic check: starts with
  `http://` or `https://`). Invalid URLs are dropped with a
  diagnostic.
- `role` must be one of the three valid values. Unknown roles
  cause the entry to be DROPPED with a diagnostic. Defaulting
  unknown roles silently widens authority (a typo `role: doc`
  could turn a docs link into a `product-reference`); dropping
  is safer.
- `scrape` must be one of the three valid values. Unknown values
  default to `none` with a diagnostic. Defaulting to `none`
  keeps the entry visible in the spec but prevents accidental
  scraping until the user fixes the value.
- `proposed` and `discovered` are mutually exclusive in normal
  use but the parser accepts both being true (interpreted as
  "duplo discovered this and proposed it as a Source"). No
  diagnostic for that case.

Diagnostics go through `duplo.diagnostics.record_failure`, the
existing failure-recording channel. They surface in the run
summary via `print_summary()`.


### `ReferenceEntry`

```python
@dataclass
class ReferenceEntry:
    """A file declared in the ## References section."""
    path: Path              # relative to project root, typically ref/<filename>
    roles: list[str]        # one or more of: "visual-target",
                            # "behavioral-target", "docs",
                            # "counter-example", "ignore"
    notes: str = ""
    proposed: bool = False  # duplo proposed this role; user has not confirmed
```

Validation:

- `path` must be a relative path under `ref/`. Absolute paths
  and paths outside `ref/` are dropped with a diagnostic.
- `roles` accepts one or more of the five valid values, written
  comma-separated in SPEC.md (`role: behavioral-target, visual-target`).
  Multiple roles per entry support the dual-use case (a demo
  video that is both `behavioral-target` for verification and
  `visual-target` for design extraction). Per-stage formatters
  return the entry if any of its roles match.
- Unknown roles in the comma-separated list are dropped with a
  diagnostic; the remaining valid roles are kept. If all roles
  are unknown, the entry defaults to `["ignore"]` with a
  diagnostic.
- File existence is NOT checked at parse time. The parser
  produces entries; a separate validation pass (in `main.py`
  or a verification module) checks that referenced files exist
  and that all files in `ref/` have entries.

Multiple-role example in SPEC.md:

```
- ref/demo.mp4
  role: behavioral-target, visual-target
```

This preserves the dual-use case that today's pipeline supports
implicitly (frame extraction extends `relevant_images` for
design extraction).


### `DesignBlock`

```python
@dataclass
class DesignBlock:
    """Parsed contents of the ## Design section."""
    user_prose: str = ""           # everything before the AUTO-GENERATED block
    auto_generated: str = ""       # contents of the AUTO-GENERATED block, if present
    has_fill_in_marker: bool = False  # user_prose still contains <FILL IN
```

The split lets the formatter give priority to user_prose when
both are present, matching the rule in the template that user
prose above the AUTO-GENERATED block wins.


### `ProductSpec` (extended)

```python
@dataclass
class ProductSpec:
    raw: str = ""                          # full file text, unchanged
    purpose: str = ""                      # existing
    scope: str = ""                        # existing
    scope_include: list[str] = ...         # existing
    scope_exclude: list[str] = ...         # existing
    behavior: str = ""                     # existing
    behavior_contracts: list[BehaviorContract] = ...  # existing
    architecture: str = ""                 # existing
    design: DesignBlock = ...              # CHANGED from str to DesignBlock
    references: list[ReferenceEntry] = ... # CHANGED from str to list
    sources: list[SourceEntry] = ...       # NEW
    notes: str = ""                        # NEW

    # Validation state
    fill_in_purpose: bool = False          # NEW
    fill_in_architecture: bool = False     # NEW
    fill_in_design: bool = False           # NEW (warning condition only;
                                           # see Validation API)
```

Two fields change type from `str` to a structured form
(`design`, `references`). This breaks any existing caller that
accessed them as strings. Audit the codebase before implementing:

- `format_spec_for_prompt` — currently uses `spec.raw`, but is
  itself being rewritten (see "Per-stage formatters" below).
- `format_scope_override_prompt` — uses `spec.scope_include`
  and `spec.scope_exclude`, unaffected.
- `format_contracts_as_verification` — uses
  `spec.behavior_contracts`, unaffected.
- Anywhere in `main.py` or other modules accessing
  `spec.design` or `spec.references` as strings — needs update
  to use `spec.design.user_prose` (or `format_design_for_prompt`)
  and `spec.references` as a list.

The grep for callers of `spec.references` and `spec.design` is
the first implementation step.


## Section parsing

### Section detection

The existing `_split_sections` function works as-is. It splits
on H1/H2/H3 headings and produces `{heading: body}` pairs. The
new sections (`Sources`, expanded `References`, `Notes`) get
matched by name in `_parse_spec`.

Add to `_KNOWN_SECTIONS`:
- `sources`
- `notes`

`references` is already in the set but its parser changes from
"store as prose" to "parse as structured entries".


### `<FILL IN>` detection

A section is considered to contain a fill-in marker if its body
matches the regex:

```python
_FILL_IN_RE = re.compile(r"<FILL\s+IN[^>]*>")
```

Permissive on whitespace and on trailing colon-and-hint text
(e.g. `<FILL IN: one or two sentences ...>` matches).

Detection happens per-section after stripping HTML comments. The
results populate the `fill_in_*` boolean fields on `ProductSpec`.

Why strip HTML comments first: the template includes example
`<FILL IN>` markers inside `<!-- ... -->` comment blocks (in the
section explanations). Those are documentation, not real markers.
A comment-stripping pass before regex matching avoids false
positives.

```python
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

def _strip_comments(body: str) -> str:
    return _HTML_COMMENT_RE.sub("", body)
```

Apply `_strip_comments` to a section body before checking for
`<FILL IN>` markers AND before parsing entries (Sources,
References) so commented-out examples don't get parsed as real
entries.


### `## Sources` parser

Each entry is a list-item block. The format is:

```
- <url>
  role: <role>
  scrape: <scrape>
  proposed: true        # optional
  discovered: true      # optional
  notes: <free text>    # optional, may span multiple lines
```

Parsing strategy: scan the section line by line, detecting entry
starts and accumulating field lines until the next entry or end
of section.

```python
_SOURCE_ENTRY_START = re.compile(r"^-\s+(https?://\S+)\s*$")
_FIELD_LINE = re.compile(r"^\s{2,}(\w+):\s*(.*)$")
```

Multi-line `notes:` values are detected as continuation lines
indented further than the field name. Stop on blank line, next
entry, or unindented line.

Validation per entry (from the SourceEntry section above) runs
as each entry is finalized. Invalid entries are dropped with
diagnostics; valid entries get appended to the list.


### `## References` parser

Identical structure to `## Sources`, but entry starts match a
file path instead of a URL.

Filenames may contain spaces (macOS screenshots default to names
like `Screen Shot 2025-10-12 at 14.30.png`). The parser uses a
non-greedy match anchored to end-of-line whitespace, plus an
optional quoted form for paths with unusual characters:

```python
_REFERENCE_ENTRY_START_BARE   = re.compile(r"^-\s+(ref/.+?)\s*$")
_REFERENCE_ENTRY_START_QUOTED = re.compile(r'^-\s+"(ref/[^"]+)"\s*$')
```

When the path is quoted with `"..."`, the quotes are stripped
and any character (except a literal `"`) is allowed inside.
Users with unusual filenames can quote; the bare form covers
normal-but-spaced filenames.

Restrict to paths under `ref/` (the only valid reference
location under the new model). Paths not starting with `ref/`
(after quote-stripping) are dropped with a diagnostic.

Field parsing reuses `_FIELD_LINE`. The `role:` field accepts
a comma-separated list (see ReferenceEntry validation).
`discovered:` is not valid for References (only Sources can be
discovered via crawling); parser ignores it with a diagnostic
if present.


### `## Design` parser

Two modes:

1. If the body contains an AUTO-GENERATED block (delimited by
   `<!-- BEGIN AUTO-GENERATED ... -->` and
   `<!-- END AUTO-GENERATED -->`), split body into user_prose
   (everything before the block) and auto_generated (block
   contents, with markers stripped).

2. If no AUTO-GENERATED block, the entire body (after stripping
   other HTML comments) becomes user_prose; auto_generated is empty.

```python
_AUTOGEN_RE = re.compile(
    r"<!--\s*BEGIN AUTO-GENERATED[^>]*-->(.*?)<!--\s*END AUTO-GENERATED\s*-->",
    re.DOTALL,
)
```

`has_fill_in_marker` is set by checking user_prose for `<FILL IN>`
after comment stripping.

The `fill_in_design` flag on `ProductSpec` is set when:
- `design.has_fill_in_marker` is true, AND
- No reference entries have role `visual-target`.

This is a WARNING condition only. Per the validation rules
below, duplo still proceeds; the warning surfaces in the run
summary so the user knows design is being inferred from minimal
input (or not at all).


### `## Scope` parser

Existing parser unchanged. The `_INCLUDE_RE` and `_EXCLUDE_RE`
patterns continue to extract include/exclude lists. The full
section body still goes into `spec.scope` for free-form prose
that doesn't match the patterns.


### `## Behavior` parser

Existing parser unchanged. `_CONTRACT_RE` continues to extract
input → expected pairs. Tested format is preserved.


### `## Notes` parser

Trivial: store the comment-stripped body as `spec.notes`. No
structured parsing.


### `## Purpose` and `## Architecture` parsers

Existing parsers unchanged for content. Add `<FILL IN>` detection
that sets `spec.fill_in_purpose` and `spec.fill_in_architecture`.


## Per-stage formatters

**Critical invariant: no LLM call ever sees raw SPEC.md text.**

The existing `format_spec_for_prompt` returns the full raw text.
That behavior is the source of a leak: if a `proposed:`,
`discovered:`, or `counter-example` entry is in `## Sources` or
`## References`, the raw text injects that entry into every LLM
prompt that uses `format_spec_for_prompt`. The role-filtered
helpers below are ineffective if the raw text bypasses them.

The replacement: `format_spec_for_prompt` is rewritten to
serialize the spec from the parsed dataclasses, NOT to return
`spec.raw`. Specifically:

- User-authored sections are included verbatim (`## Purpose`,
  `## Architecture`, `## Design.user_prose`, `## Scope`,
  `## Behavior`, `## Notes`).
- For `## Sources`, only entries where `proposed: false` AND
  `discovered: false` AND role is NOT `counter-example`.
- For `## References`, only entries where `proposed: false` AND
  no role is `counter-example` AND no role is `ignore`.
- For `## Design`, includes `auto_generated` content alongside
  `user_prose` (autogen is derived from non-proposed visual
  targets only, so it has already been filtered upstream).

`spec.raw` remains available for narrow uses (diff display,
debug output) but is never used for prompt construction. Code
review should flag any new use of `spec.raw` in a prompt path.

Per-stage helpers that filter by role. They return
`ReferenceEntry` or `SourceEntry` objects (not just paths) so
callers can inspect roles, notes, and flags. Path extraction is
a trivial `[e.path for e in ...]` at the call site when only
paths are needed.

### `format_visual_references(spec) -> list[ReferenceEntry]`

Returns reference entries where `visual-target` is in `roles`,
excluding entries flagged `proposed: true`. Used by design
extraction.

### `format_behavioral_references(spec) -> list[ReferenceEntry]`

Returns reference entries where `behavioral-target` is in
`roles`, excluding entries flagged `proposed: true`. Used by
video frame extraction and verification case generation.
Dual-role entries (e.g. `behavioral-target, visual-target`)
appear in both this formatter and `format_visual_references`,
so the caller can detect the dual-use case via
`"visual-target" in entry.roles`.

### `format_doc_references(spec) -> list[ReferenceEntry]`

Returns reference entries where `docs` is in `roles`, excluding
`proposed: true`. Used as supplementary text in feature
extraction.

### `format_counter_examples(spec) -> list[ReferenceEntry]`

Returns reference entries where `counter-example` is in `roles`,
excluding `proposed: true`. Used only in investigation, never
in extraction or planning.

### `format_scrapeable_sources(spec) -> list[SourceEntry]`

Returns source entries where:
- `scrape` is `deep` or `shallow`.
- `discovered` is false (don't auto-scrape unreviewed discoveries).
- `proposed` is false (same reason).
- `role` is NOT `counter-example`. Counter-example URLs are
  declarative context, never scraped for extraction. The parser
  emits a diagnostic if a counter-example has `scrape: deep` or
  `scrape: shallow` (the user almost certainly meant `none`)
  and treats it as `none` regardless of the declared value.

Used by the scraping pipeline to decide what URLs to fetch.

### `format_design_for_prompt(spec) -> str`

Returns the design section formatted for plan generation. If
both `user_prose` and `auto_generated` are present, formats them
in that order with a separator. If only one is present, returns
that one. If neither, returns empty string.

This replaces the existing pattern of accessing `spec.design`
directly as a string. Callers in `main.py` switch to this helper.


## Validation API

Add a top-level validation function:

### `validate_for_run(spec) -> list[str]`

Returns a list of human-readable error messages describing why
duplo can't run against this spec. Empty list means OK to run.

Errors include:

- `"## Purpose still contains <FILL IN>"` if `fill_in_purpose`.
- `"## Architecture still contains <FILL IN>"` if `fill_in_architecture`.
- `"No source URL or reference file declared. Add a URL to ## Sources or files to ref/."` if no scrapeable sources AND no non-ignore references AND `## Purpose` is shorter than 50 characters (heuristic for "too sparse to plan from").

`fill_in_design` is a WARNING, not an error. The "URL alone"
common pattern in SPEC-guide.md is valid even when `## Design`
has no user prose and no visual-target references — duplo can
still proceed by inferring design from scraped product-reference
pages (see PIPELINE-design.md, design extraction from site
media). The warning surfaces in the run summary so the user
knows design is being inferred from minimal input.

Warnings (returned separately or via diagnostics) include:

- `"<n> proposed: true entries in ## References. Review and remove the flag, or delete entries duplo got wrong."`
- `"<n> discovered: true entries in ## Sources. Review and remove the flag, or delete URLs you don't want crawled again."`
- `"## Design has <FILL IN> and no visual-target references; design will be inferred from scraped sources only."` if `fill_in_design`.

Warnings warn; errors block. There is no user-facing strict
mode. The pipeline always ignores unreviewed entries
(`proposed: true`, `discovered: true`); a strict mode that
turns warnings into errors would add friction without adding
safety, since unreviewed entries are already inert.

This function is called from `main.py` at the start of every
`duplo` run (after `read_spec`, before any pipeline work). If
it returns a non-empty list of errors, print them and exit 1.
Warnings print but don't block.


## Migration from existing SPEC.md format

The migration discipline at the parser level is **opportunistic
preservation**: the parser produces a valid `ProductSpec` even
from old-format SPEC.md files, but with reduced structure. The
heavier auto-migration work (creating `ref/`, generating a fresh
SPEC.md from `.duplo/duplo.json` state) is out of scope for the
parser — see `MIGRATION-design.md`.

The parser-level behaviors:

1. **Old `## References` prose** parses to an empty
   `references` list. The prose content is preserved in
   `spec.raw` and emits a diagnostic. (The new
   `format_spec_for_prompt` does NOT inject `spec.raw`, so old
   prose-form references won't reach LLM prompts; this is
   acceptable since pre-migration projects should be migrated
   per `MIGRATION-design.md` rather than continuing to run.)

2. **No `## Sources` section** parses to an empty `sources`
   list. The pipeline falls back to scraping URLs from
   `.duplo/product.json` (the existing source URL) for backward
   compatibility, and emits a diagnostic suggesting the user
   add a `## Sources` section.

3. **No `## Notes` section** parses to empty `spec.notes`. No
   diagnostic — `## Notes` is fully optional.

4. **No `<FILL IN>` markers** in old files means
   `fill_in_purpose` and `fill_in_architecture` stay false.
   Validation passes. Old projects keep running until the user
   migrates per the manual instructions in `MIGRATION-design.md`.


## Test plan

The existing `tests/test_spec_reader.py` covers:
- `read_spec` returning None when file is absent.
- Section splitting.
- Behavior contract parsing.
- Scope include/exclude parsing.
- `format_spec_for_prompt` formatting.
- `format_contracts_as_verification` output.

The `format_spec_for_prompt` tests need updating (the function
is being rewritten; output will differ). Other existing tests
stay valid.

Add new tests:

1. `## Sources` parsing — single entry, multiple entries,
   entries with all field combinations, invalid URLs dropped,
   invalid roles dropped (entry removed entirely), invalid
   scrape defaulting to `none`, comment-stripped examples not
   being parsed as real entries.
2. `## References` parsing — same as Sources, plus path
   restriction to `ref/`, paths with spaces, quoted paths,
   multiple roles per entry, `discovered:` flag rejected.
3. `<FILL IN>` detection — present in body, present in comment
   (should NOT trigger), present in required section
   (sets flag), absent (flag stays false).
4. `## Design` AUTO-GENERATED block parsing — block present
   (split into user_prose and auto_generated), block absent
   (all goes to user_prose), block with malformed markers
   (treated as no block, all to user_prose).
5. `fill_in_design` rule — true when user_prose has marker AND
   no visual-target references; false when either condition
   fails.
6. Per-stage formatters — each returns the right filtered list,
   each excludes proposed: true, each excludes counter-example
   (where applicable), entries with multiple roles appear in
   each matching formatter, each handles empty input gracefully.
7. **Prompt-injection invariant**: `format_spec_for_prompt`
   output for a spec containing `proposed: true`,
   `discovered: true`, and `counter-example` entries does NOT
   contain those entries' content. This is the highest-leverage
   test in the suite — it pins the safety property.
8. `validate_for_run` — each error condition produces the
   expected message; valid spec returns empty list;
   `fill_in_design` produces a warning, not an error.
9. Migration — old-format SPEC.md (with prose `## References`,
   no Sources, no Notes) parses without errors, raw content
   preserved in `spec.raw`, diagnostics emitted.

Each test is small and isolated. Aim for one assertion per test
where possible, matching the existing test style in the codebase.


## Implementation order

When this becomes mcloop tasks:

1. Add the new dataclasses (`SourceEntry`, `ReferenceEntry`,
   `DesignBlock`). Tests for each.
2. Add the comment-stripping helper. Tests.
3. Add `<FILL IN>` detection. Tests.
4. Add `## Sources` parser. Tests.
5. Convert `## References` parser from prose to structured.
   Tests. Migration tests for old format.
6. Add `## Notes` parser. Tests.
7. Add AUTO-GENERATED block parsing in `## Design`. Tests.
8. Update `ProductSpec` with new fields. Audit callers in
   `main.py`, update them to use new helpers.
9. Add per-stage formatters. Tests, including the
   prompt-injection invariant test.
10. Rewrite `format_spec_for_prompt` to serialize from
    dataclasses. Tests.
11. Add `validate_for_run`. Tests.
12. Wire `validate_for_run` into `main.py` at run start.

Each step is small enough to be one mcloop task with a test
subtask. Steps 5, 8, and 10 are the highest-risk because they
change the behavior of existing callers; everything else is
additive.


## Open questions

1. **`.duplo/product.json` under the new model.** Resolved:
   keep it as a cache. Product identity in `## Purpose` is
   prose, not a stable key; the JSON cache provides a stable
   identifier that survives prose edits. Possible future
   cleanup: fold into `duplo.json` to reduce file count.

2. **Strict mode.** Resolved: don't add a user-facing strict
   mode. The pipeline always ignores unreviewed entries; a
   strict mode would only convert warnings into errors, which
   adds friction without adding safety.

3. **Order of entries in `## Sources` and `## References`.**
   No semantic meaning, but the parser preserves order (lists,
   not sets) so writes-back to SPEC.md preserve the user's
   chosen order. Existing `format_*` helpers don't depend on
   order.

4. **Empty `ref/` directory.** If `## References` has no entries
   AND `ref/` has files in it, the parser does nothing special
   (it doesn't read the directory). A separate validation pass
   in `main.py` should diagnose this case ("N files in ref/ have
   no entries in ## References; they will be ignored"). Auto-
   proposal of entries for unlabeled files happens in
   `spec_drafter`, not the parser.

5. **Multi-source state in `.duplo/`.** The current pipeline
   centers on a single `source_url`; the new model allows
   multiple `## Sources` entries. Persistence model needs
   updating: `.duplo/duplo.json` should track per-URL scrape
   timestamps and content hashes, not a single cache. This is
   a pipeline concern (see `PIPELINE-design.md`); the parser
   only produces the multi-source list.
