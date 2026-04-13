"""Read and parse a product specification from SPEC.md.

The spec is a user-authored Markdown document that expresses intent and
constraints for the build.  Duplo reads it on every run and injects its
content into the LLM prompts that shape feature extraction, roadmap
generation, plan generation, and investigation.

Recognised headings (all optional):

    ## Purpose       — what the product is, who it is for
    ## Scope         — explicit include/exclude feature overrides
    ## Behavior      — concrete input → expected output contracts
    ## Architecture  — technology, dependency, and structural constraints
    ## Design        — visual / UX intent
    ## References    — which reference materials are authoritative and why

Any content under unrecognised headings (or outside any heading) is
preserved as general guidance and still injected into prompts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from duplo.diagnostics import record_failure

_SPEC_FILENAME = "SPEC.md"

# Headings we parse into structured fields.
_KNOWN_SECTIONS = {
    "purpose",
    "scope",
    "behavior",
    "behaviour",  # accept British spelling
    "architecture",
    "design",
    "references",
    "sources",
    "notes",
}

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

_FILL_IN_RE = re.compile(r"<FILL\s+IN[^>]*>")

# Pattern for AUTO-GENERATED blocks inside ## Design.
_AUTOGEN_RE = re.compile(
    r"<!--\s*BEGIN AUTO-GENERATED[^>]*-->(.*?)<!--\s*END AUTO-GENERATED\s*-->",
    re.DOTALL,
)

# Patterns for ## Sources parser.
# Entry start: a list-item line containing an HTTP(S) URL.
_SOURCE_ENTRY_START = re.compile(r"^-\s+(https?://\S+)\s*$")
# Field line: indented key: value pair (at least 2 spaces of indent).
_FIELD_LINE = re.compile(r"^\s{2,}(\w+):\s*(.*)$")

# Patterns for ## References parser.
# Bare form: list-item line starting with ref/ followed by a path.
# Non-greedy match with trailing-whitespace anchor handles paths with spaces
# (e.g. "Screen Shot 2025-10-12 at 14.30.png").
_REFERENCE_ENTRY_START_BARE = re.compile(r"^-\s+(ref/.+?)\s*$")
# Quoted form: list-item line with "ref/..." — strips quotes after match.
# Allows any character except literal `"` inside the quotes.
_REFERENCE_ENTRY_START_QUOTED = re.compile(r'^-\s+"(ref/[^"]+)"\s*$')

# Valid values for SourceEntry validation.
_VALID_SOURCE_ROLES = frozenset({"product-reference", "docs", "counter-example"})
_VALID_SCRAPE_VALUES = frozenset({"deep", "shallow", "none"})

# Valid values for ReferenceEntry validation.
_VALID_REFERENCE_ROLES = frozenset(
    {"visual-target", "behavioral-target", "docs", "counter-example", "ignore"}
)


def _strip_comments(body: str) -> str:
    """Remove HTML comments from *body*."""
    return _HTML_COMMENT_RE.sub("", body)


def _parse_source_entries(
    body: str,
    *,
    errors_path: Path | str = ".duplo/errors.jsonl",
) -> list[SourceEntry]:
    """Parse ``## Sources`` section body into :class:`SourceEntry` objects.

    Scans *body* line-by-line.  An entry starts with a list-item line
    matching ``_SOURCE_ENTRY_START`` (``- <url>``).  Subsequent indented
    ``key: value`` lines (matching ``_FIELD_LINE``) are accumulated as
    fields.  Multi-line ``notes:`` values are supported: any line
    indented further than the field-name column is appended as a
    continuation.  An entry ends at the next entry start, a blank line,
    or a line that is neither a field nor a continuation.

    After parsing, each entry is validated: invalid URLs and unknown
    roles cause the entry to be dropped; unknown scrape values default
    to ``"none"``.  Diagnostics are recorded via
    :func:`~duplo.diagnostics.record_failure`.
    """
    entries: list[SourceEntry] = []
    current_url: str | None = None
    fields: dict[str, str] = {}
    in_notes = False
    notes_indent = 0

    def _flush() -> None:
        nonlocal current_url, fields, in_notes, notes_indent
        if current_url is not None:
            entry = SourceEntry(
                url=current_url,
                role=fields.get("role", ""),
                scrape=fields.get("scrape", ""),
                notes=fields.get("notes", "").strip(),
                proposed=fields.get("proposed", "").lower() == "true",
                discovered=fields.get("discovered", "").lower() == "true",
            )
            entries.append(entry)
        current_url = None
        fields = {}
        in_notes = False
        notes_indent = 0

    for line in body.splitlines():
        # Check for new entry start.
        entry_m = _SOURCE_ENTRY_START.match(line)
        if entry_m:
            _flush()
            current_url = entry_m.group(1)
            continue

        # Outside an entry, skip.
        if current_url is None:
            continue

        # Blank line ends the current entry.
        if not line.strip():
            _flush()
            continue

        # Try matching a field line.
        field_m = _FIELD_LINE.match(line)
        if field_m:
            key = field_m.group(1).lower()
            value = field_m.group(2)
            fields[key] = value
            in_notes = key == "notes"
            if in_notes:
                # Record the indent of the field name for continuation
                # detection.  The field name starts after the leading
                # whitespace, so we measure leading whitespace length.
                notes_indent = len(line) - len(line.lstrip())
            continue

        # Continuation of a multi-line notes field: the line must be
        # indented further than the field-name indent.
        if in_notes:
            line_indent = len(line) - len(line.lstrip())
            if line_indent > notes_indent:
                fields["notes"] = fields.get("notes", "") + "\n" + line.strip()
                continue

        # Unrecognised line — end the current entry.
        _flush()

    _flush()
    return _validate_source_entries(entries, errors_path=errors_path)


def _validate_source_entries(
    entries: list[SourceEntry],
    *,
    errors_path: Path | str = ".duplo/errors.jsonl",
) -> list[SourceEntry]:
    """Validate parsed source entries, dropping invalid ones.

    - Invalid URL (not ``http://`` or ``https://``): entry dropped.
    - Unknown role: entry dropped (typo must not silently widen authority).
    - Unknown scrape value: defaulted to ``none``.
    """
    valid: list[SourceEntry] = []
    for entry in entries:
        if not entry.url.startswith(("http://", "https://")):
            record_failure(
                "spec_reader:_validate_source_entries",
                "io",
                f"Dropped source entry with invalid URL: {entry.url!r}",
                errors_path=errors_path,
            )
            continue
        if entry.role not in _VALID_SOURCE_ROLES:
            record_failure(
                "spec_reader:_validate_source_entries",
                "io",
                f"Dropped source entry {entry.url!r}: unknown role {entry.role!r}",
                errors_path=errors_path,
            )
            continue
        if entry.scrape not in _VALID_SCRAPE_VALUES:
            record_failure(
                "spec_reader:_validate_source_entries",
                "io",
                f"Source entry {entry.url!r}: unknown scrape "
                f"{entry.scrape!r}, defaulting to 'none'",
                errors_path=errors_path,
            )
            entry = SourceEntry(
                url=entry.url,
                role=entry.role,
                scrape="none",
                notes=entry.notes,
                proposed=entry.proposed,
                discovered=entry.discovered,
            )
        # Counter-example entries should not be scraped.  If the user
        # declared scrape: deep or scrape: shallow on a counter-example,
        # they almost certainly meant scrape: none.
        if entry.role == "counter-example" and entry.scrape in ("deep", "shallow"):
            record_failure(
                "spec_reader:_validate_source_entries",
                "io",
                f"Source entry {entry.url!r}: counter-example with "
                f"scrape={entry.scrape!r}, overriding to 'none'",
                errors_path=errors_path,
            )
            entry = SourceEntry(
                url=entry.url,
                role=entry.role,
                scrape="none",
                notes=entry.notes,
                proposed=entry.proposed,
                discovered=entry.discovered,
            )
        valid.append(entry)
    return valid


def _parse_reference_entries(
    body: str,
    *,
    errors_path: Path | str = ".duplo/errors.jsonl",
) -> list[ReferenceEntry]:
    """Parse ``## References`` section body into :class:`ReferenceEntry` objects.

    Shares ``_FIELD_LINE`` with the Sources parser.  Entry starts match
    either ``_REFERENCE_ENTRY_START_BARE`` or
    ``_REFERENCE_ENTRY_START_QUOTED``.  Field parsing, multi-line
    ``notes:`` continuation, and flush logic are identical to
    ``_parse_source_entries``.

    After parsing, entries are validated: unknown roles are dropped from
    the comma-separated list (if all roles are unknown the entry defaults
    to ``["ignore"]``).  ``discovered:`` is not valid for References; a
    diagnostic is emitted if present.
    """
    entries: list[ReferenceEntry] = []
    current_path: str | None = None
    fields: dict[str, str] = {}
    in_notes = False
    notes_indent = 0

    def _flush() -> None:
        nonlocal current_path, fields, in_notes, notes_indent
        if current_path is not None:
            # Parse comma-separated roles.
            raw_roles = [r.strip() for r in fields.get("role", "").split(",") if r.strip()]
            entry = ReferenceEntry(
                path=Path(current_path),
                roles=raw_roles,
                notes=fields.get("notes", "").strip(),
                proposed=fields.get("proposed", "").lower() == "true",
            )
            # Warn if discovered: is present (not valid for References).
            if "discovered" in fields:
                record_failure(
                    "spec_reader:_parse_reference_entries",
                    "io",
                    f"Reference entry {current_path!r}: "
                    "'discovered' is not valid for References; ignored",
                    errors_path=errors_path,
                )
            entries.append(entry)
        current_path = None
        fields = {}
        in_notes = False
        notes_indent = 0

    for line in body.splitlines():
        # Check for new entry start (bare or quoted form).
        entry_m = _REFERENCE_ENTRY_START_QUOTED.match(line) or _REFERENCE_ENTRY_START_BARE.match(
            line
        )
        if entry_m:
            _flush()
            current_path = entry_m.group(1)
            continue

        # Outside an entry, skip.
        if current_path is None:
            continue

        # Blank line ends the current entry.
        if not line.strip():
            _flush()
            continue

        # Try matching a field line.
        field_m = _FIELD_LINE.match(line)
        if field_m:
            key = field_m.group(1).lower()
            value = field_m.group(2)
            fields[key] = value
            in_notes = key == "notes"
            if in_notes:
                notes_indent = len(line) - len(line.lstrip())
            continue

        # Continuation of a multi-line notes field.
        if in_notes:
            line_indent = len(line) - len(line.lstrip())
            if line_indent > notes_indent:
                fields["notes"] = fields.get("notes", "") + "\n" + line.strip()
                continue

        # Unrecognised line — end the current entry.
        _flush()

    _flush()
    return _validate_reference_entries(entries, errors_path=errors_path)


def _validate_reference_entries(
    entries: list[ReferenceEntry],
    *,
    errors_path: Path | str = ".duplo/errors.jsonl",
) -> list[ReferenceEntry]:
    """Validate parsed reference entries.

    - Unknown roles are dropped from the list; if all roles are unknown
      the entry defaults to ``["ignore"]``.
    - Path must start with ``ref/``; entries with other paths are dropped.
    """
    valid: list[ReferenceEntry] = []
    for entry in entries:
        # Path must be under ref/.
        if not str(entry.path).startswith("ref/"):
            record_failure(
                "spec_reader:_validate_reference_entries",
                "io",
                f"Dropped reference entry with path outside ref/: {entry.path!r}",
                errors_path=errors_path,
            )
            continue
        # Filter unknown roles.
        good_roles = [r for r in entry.roles if r in _VALID_REFERENCE_ROLES]
        bad_roles = [r for r in entry.roles if r not in _VALID_REFERENCE_ROLES]
        for bad in bad_roles:
            record_failure(
                "spec_reader:_validate_reference_entries",
                "io",
                f"Reference entry {entry.path!r}: unknown role {bad!r} dropped",
                errors_path=errors_path,
            )
        if not good_roles and entry.roles:
            # All roles were unknown — default to ["ignore"].
            good_roles = ["ignore"]
            record_failure(
                "spec_reader:_validate_reference_entries",
                "io",
                f"Reference entry {entry.path!r}: all roles unknown, defaulting to ['ignore']",
                errors_path=errors_path,
            )
        entry = ReferenceEntry(
            path=entry.path,
            roles=good_roles,
            notes=entry.notes,
            proposed=entry.proposed,
        )
        valid.append(entry)
    return valid


# Patterns inside the Scope section.
_INCLUDE_RE = re.compile(
    r"^\s*[-*]\s*(?:include|add|keep|want|need)\s*:\s*(.+)",
    re.IGNORECASE | re.MULTILINE,
)
_EXCLUDE_RE = re.compile(
    r"^\s*[-*]\s*(?:exclude|skip|drop|remove|omit|don't need|do not)\s*:\s*(.+)",
    re.IGNORECASE | re.MULTILINE,
)

# Pattern for behavior contracts: ``input`` → ``expected``
# Accepts →, ->, =>, and "expect"/"should produce"/"should be" as separators.
_CONTRACT_RE = re.compile(
    r"`([^`]+)`\s*(?:→|->|=>|should\s+(?:produce|be|show|display|return|give)|expect(?:s|ed)?(?:\s+result)?)\s*`([^`]+)`",
    re.IGNORECASE,
)


@dataclass
class BehaviorContract:
    """A single input → expected output pair from the spec."""

    input: str
    expected: str


@dataclass
class SourceEntry:
    """A URL declared in the ## Sources section."""

    url: str
    role: str  # "product-reference" | "docs" | "counter-example"
    scrape: str  # "deep" | "shallow" | "none"
    notes: str = ""
    proposed: bool = False
    discovered: bool = False


@dataclass
class ReferenceEntry:
    """A file declared in the ## References section."""

    path: Path  # relative to project root, typically ref/<filename>
    roles: list[str] = field(
        default_factory=list
    )  # e.g. "visual-target", "behavioral-target", "docs", etc.
    notes: str = ""
    proposed: bool = False


@dataclass
class DesignBlock:
    """Parsed contents of the ## Design section."""

    user_prose: str = ""
    auto_generated: str = ""
    has_fill_in_marker: bool = False


@dataclass
class ProductSpec:
    """Parsed product specification.

    All fields are optional.  ``raw`` always contains the full text of
    SPEC.md for injection into LLM prompts.
    """

    raw: str = ""
    purpose: str = ""
    scope: str = ""
    scope_include: list[str] = field(default_factory=list)
    scope_exclude: list[str] = field(default_factory=list)
    behavior: str = ""
    behavior_contracts: list[BehaviorContract] = field(default_factory=list)
    architecture: str = ""
    design: DesignBlock = field(default_factory=DesignBlock)
    references: list[ReferenceEntry] = field(default_factory=list)
    sources: list[SourceEntry] = field(default_factory=list)
    notes: str = ""
    fill_in_purpose: bool = False
    fill_in_architecture: bool = False
    fill_in_design: bool = False


def _parse_design_block(body: str) -> DesignBlock:
    """Parse ``## Design`` section body into a :class:`DesignBlock`.

    If the body contains an AUTO-GENERATED block (delimited by
    ``<!-- BEGIN AUTO-GENERATED ... -->`` and
    ``<!-- END AUTO-GENERATED -->``), the text before the block
    becomes ``user_prose`` and the block contents (markers stripped)
    become ``auto_generated``.

    If no AUTO-GENERATED block is found, the entire body (after
    stripping HTML comments) becomes ``user_prose``; ``auto_generated``
    is empty.

    ``has_fill_in_marker`` is set by checking ``user_prose`` (after
    comment stripping) for ``<FILL IN>``.
    """
    m = _AUTOGEN_RE.search(body)
    if m:
        user_prose = _strip_comments(body[: m.start()]).strip()
        auto_generated = m.group(1).strip()
    else:
        user_prose = _strip_comments(body).strip()
        auto_generated = ""
    has_fill_in = bool(_FILL_IN_RE.search(_strip_comments(user_prose)))
    return DesignBlock(
        user_prose=user_prose,
        auto_generated=auto_generated,
        has_fill_in_marker=has_fill_in,
    )


def read_spec(*, target_dir: Path | str = ".") -> ProductSpec | None:
    """Read and parse ``SPEC.md`` from *target_dir*.

    Returns a :class:`ProductSpec` if the file exists, or ``None`` if
    it does not.
    """
    path = Path(target_dir) / _SPEC_FILENAME
    if not path.exists():
        return None

    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return None

    return _parse_spec(text)


def _parse_spec(text: str) -> ProductSpec:
    """Parse *text* into a :class:`ProductSpec`."""
    spec = ProductSpec(raw=text)
    sections = _split_sections(text)

    for heading, body in sections.items():
        key = heading.lower().strip()
        if key == "purpose":
            spec.purpose = body.strip()
            if _FILL_IN_RE.search(_strip_comments(body)):
                spec.fill_in_purpose = True
        elif key == "scope":
            spec.scope = body.strip()
            spec.scope_include = _parse_scope_list(body, _INCLUDE_RE)
            spec.scope_exclude = _parse_scope_list(body, _EXCLUDE_RE)
        elif key in ("behavior", "behaviour"):
            spec.behavior = body.strip()
            spec.behavior_contracts = _parse_contracts(body)
        elif key == "architecture":
            spec.architecture = body.strip()
            if _FILL_IN_RE.search(_strip_comments(body)):
                spec.fill_in_architecture = True
        elif key == "design":
            spec.design = _parse_design_block(body)
        elif key == "references":
            spec.references = _parse_reference_entries(body)
            if not spec.references and body.strip():
                record_failure(
                    "spec_reader:_parse_spec",
                    "io",
                    "## References contains prose but no structured entries; "
                    "consider migrating to the new entry format "
                    "(see MIGRATION-design.md)",
                )
        elif key == "sources":
            spec.sources = _parse_source_entries(body)
        elif key == "notes":
            spec.notes = _strip_comments(body).strip()

    # fill_in_design: true only when design body has <FILL IN> marker
    # AND no reference entries have visual-target role.
    has_visual_target = any("visual-target" in entry.roles for entry in spec.references)
    spec.fill_in_design = spec.design.has_fill_in_marker and not has_visual_target

    return spec


def _split_sections(text: str) -> dict[str, str]:
    """Split *text* into ``{heading: body}`` pairs.

    Content before the first heading is stored under the empty-string key.
    """
    sections: dict[str, str] = {}
    current_heading = ""
    current_lines: list[str] = []

    for line in text.splitlines(keepends=True):
        match = _HEADING_RE.match(line)
        if match:
            # Save the previous section (append if heading seen before).
            body = "".join(current_lines)
            if current_heading in sections:
                sections[current_heading] += body
            else:
                sections[current_heading] = body
            new_heading = match.group(1).strip()
            if new_heading in sections:
                record_failure(
                    "spec_reader:_split_sections",
                    "io",
                    f"Duplicate heading '## {new_heading}' in SPEC.md"
                    " — content merged with earlier section",
                )
            current_heading = new_heading
            current_lines = []
        else:
            current_lines.append(line)

    # Save the last section (append if heading seen before).
    body = "".join(current_lines)
    if current_heading in sections:
        sections[current_heading] += body
    else:
        sections[current_heading] = body
    return sections


def _parse_scope_list(text: str, pattern: re.Pattern) -> list[str]:
    """Extract a list of feature names from scope include/exclude lines."""
    items: list[str] = []
    for match in pattern.finditer(text):
        raw = match.group(1).strip()
        # Split on commas for lists like "include: X, Y, Z"
        for part in raw.split(","):
            cleaned = part.strip().strip('"').strip("'").strip()
            if cleaned:
                items.append(cleaned)
    return items


def _parse_contracts(text: str) -> list[BehaviorContract]:
    """Extract input→expected pairs from behavior section text."""
    contracts: list[BehaviorContract] = []
    for match in _CONTRACT_RE.finditer(text):
        inp = match.group(1).strip()
        expected = match.group(2).strip()
        if inp and expected:
            contracts.append(BehaviorContract(input=inp, expected=expected))
    return contracts


def format_visual_references(spec: ProductSpec) -> list[ReferenceEntry]:
    """Return reference entries with ``visual-target`` role, excluding proposed."""
    return [e for e in spec.references if "visual-target" in e.roles and not e.proposed]


def format_behavioral_references(spec: ProductSpec) -> list[ReferenceEntry]:
    """Return reference entries with ``behavioral-target`` role, excluding proposed."""
    return [e for e in spec.references if "behavioral-target" in e.roles and not e.proposed]


def format_doc_references(spec: ProductSpec) -> list[ReferenceEntry]:
    """Return reference entries with ``docs`` role, excluding proposed."""
    return [e for e in spec.references if "docs" in e.roles and not e.proposed]


def format_counter_examples(spec: ProductSpec) -> list[ReferenceEntry]:
    """Return reference entries with ``counter-example`` role, excluding proposed."""
    return [e for e in spec.references if "counter-example" in e.roles and not e.proposed]


def scrapeable_sources(spec: ProductSpec) -> list[SourceEntry]:
    """Return source entries eligible for scraping.

    Filters to entries where scrape is ``deep`` or ``shallow``, AND
    ``discovered`` is false, AND ``proposed`` is false, AND ``role`` is
    not ``counter-example``.
    """
    return [
        e
        for e in spec.sources
        if e.scrape in ("deep", "shallow")
        and not e.discovered
        and not e.proposed
        and e.role != "counter-example"
    ]


def format_design_for_prompt(spec: ProductSpec) -> str:
    """Format the design section for plan generation.

    If both ``user_prose`` and ``auto_generated`` are present, returns
    them in that order separated by a divider.  If only one is present,
    returns that one.  If neither, returns an empty string.
    """
    prose = spec.design.user_prose
    auto = spec.design.auto_generated
    if prose and auto:
        return f"{prose}\n\n---\n\n{auto}"
    return prose or auto


def format_spec_for_prompt(spec: ProductSpec) -> str:
    """Format the spec for injection into an LLM system or user prompt.

    Serializes from parsed ``ProductSpec`` fields — **never** from
    ``spec.raw``.  This ensures that ``proposed:``, ``discovered:``,
    and ``counter-example`` entries are excluded from every LLM prompt.

    Filtering rules:

    - ``## Sources``: only entries where ``proposed`` is false AND
      ``discovered`` is false AND role is NOT ``counter-example``.
    - ``## References``: only entries where ``proposed`` is false AND
      no role is ``counter-example`` AND no role is ``ignore``.
    - All other user-authored sections are included verbatim.
    """
    parts: list[str] = []

    if spec.purpose:
        parts.append(f"## Purpose\n\n{spec.purpose}")

    if spec.scope:
        parts.append(f"## Scope\n\n{spec.scope}")

    if spec.behavior:
        parts.append(f"## Behavior\n\n{spec.behavior}")

    if spec.architecture:
        parts.append(f"## Architecture\n\n{spec.architecture}")

    design_text = format_design_for_prompt(spec)
    if design_text:
        parts.append(f"## Design\n\n{design_text}")

    # Sources: exclude proposed, discovered, and counter-example.
    safe_sources = [
        s
        for s in spec.sources
        if not s.proposed and not s.discovered and s.role != "counter-example"
    ]
    if safe_sources:
        lines: list[str] = []
        for s in safe_sources:
            lines.append(f"- {s.url}")
            lines.append(f"  role: {s.role}")
            if s.scrape != "none":
                lines.append(f"  scrape: {s.scrape}")
            if s.notes:
                lines.append(f"  notes: {s.notes}")
        parts.append("## Sources\n\n" + "\n".join(lines))

    # References: exclude proposed, counter-example, and ignore.
    safe_refs = [
        r
        for r in spec.references
        if not r.proposed and "counter-example" not in r.roles and "ignore" not in r.roles
    ]
    if safe_refs:
        ref_lines: list[str] = []
        for r in safe_refs:
            ref_lines.append(f"- {r.path}")
            if r.roles:
                ref_lines.append(f"  roles: {', '.join(r.roles)}")
            if r.notes:
                ref_lines.append(f"  notes: {r.notes}")
        parts.append("## References\n\n" + "\n".join(ref_lines))

    if spec.notes:
        parts.append(f"## Notes\n\n{spec.notes}")

    body = "\n\n".join(parts)
    if not body:
        return ""

    return (
        "PRODUCT SPECIFICATION (authored by the user — this is authoritative "
        "and takes precedence over scraped content when they conflict):\n\n" + body
    )


def format_scope_override_prompt(spec: ProductSpec) -> str:
    """Format scope overrides as an addendum to the feature extraction prompt.

    Returns an empty string if no scope overrides are present.
    """
    parts: list[str] = []
    if spec.scope_include:
        names = ", ".join(f'"{n}"' for n in spec.scope_include)
        parts.append(
            f"The user REQUIRES these features to be included: [{names}]. "
            "If the scraped text does not mention them, include them anyway "
            "based on the user's specification."
        )
    if spec.scope_exclude:
        names = ", ".join(f'"{n}"' for n in spec.scope_exclude)
        parts.append(
            f"The user has EXCLUDED these features: [{names}]. "
            "Do NOT include them in the output even if the scraped text "
            "describes them."
        )
    if not parts:
        return ""
    return "\n\n" + "\n".join(parts)


@dataclass
class ValidationResult:
    """Result of :func:`validate_for_run`."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_for_run(spec: ProductSpec) -> ValidationResult:
    """Check whether *spec* is complete enough for a duplo run.

    Returns a :class:`ValidationResult` whose ``errors`` list is empty
    when the spec is OK to run.  Warnings are informational and do not
    block execution.

    Error conditions:

    - ``## Purpose`` still contains ``<FILL IN>``.
    - ``## Architecture`` still contains ``<FILL IN>``.
    - No scrapeable sources AND no non-ignore references AND
      ``## Purpose`` shorter than 50 characters (too sparse to plan
      from).

    Warning conditions:

    - ``fill_in_design`` is true (design will be inferred from scraped
      sources only).
    - ``proposed: true`` references exist (need user review).
    - ``discovered: true`` sources exist (need user review).
    """
    errors: list[str] = []
    warnings: list[str] = []

    if spec.fill_in_purpose:
        errors.append("## Purpose still contains <FILL IN>")

    if spec.fill_in_architecture:
        errors.append("## Architecture still contains <FILL IN>")

    # Check the "too sparse to plan" condition: no scrapeable sources,
    # no non-ignore references, and purpose is too short.
    has_scrapeable = bool(scrapeable_sources(spec))
    non_ignore_refs = [r for r in spec.references if r.roles != ["ignore"] and not r.proposed]
    has_refs = bool(non_ignore_refs)
    purpose_sparse = len(spec.purpose) < 50
    if not has_scrapeable and not has_refs and purpose_sparse:
        errors.append(
            "No source URL or reference file declared. Add a URL to ## Sources or files to ref/."
        )

    if spec.fill_in_design:
        warnings.append(
            "## Design has <FILL IN> and no visual-target references; "
            "design will be inferred from scraped sources only."
        )

    proposed_count = sum(1 for r in spec.references if r.proposed)
    if proposed_count:
        warnings.append(
            f"{proposed_count} proposed: true entries in ## References. "
            "Review and remove the flag, or delete entries duplo got wrong."
        )

    discovered_count = sum(1 for s in spec.sources if s.discovered)
    if discovered_count:
        warnings.append(
            f"{discovered_count} discovered: true entries in ## Sources. "
            "Review and remove the flag, or delete URLs you don't want "
            "crawled again."
        )

    return ValidationResult(errors=errors, warnings=warnings)


def format_contracts_as_verification(spec: ProductSpec) -> str:
    """Format behavior contracts as PLAN.md verification tasks.

    Returns a Markdown section suitable for appending to a plan, or
    an empty string if no contracts are present.
    """
    if not spec.behavior_contracts:
        return ""
    lines: list[str] = [
        "",
        "## Functional verification from product spec",
        "",
        "These input/output pairs are specified in SPEC.md and must hold.",
        "",
    ]
    for contract in spec.behavior_contracts:
        lines.append(f"- [ ] Verify: type `{contract.input}`, expect result `{contract.expected}`")
    lines.append("")
    return "\n".join(lines)
