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
}

_HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

_FILL_IN_RE = re.compile(r"<FILL\s+IN[^>]*>")

# Patterns for ## Sources parser.
# Entry start: a list-item line containing an HTTP(S) URL.
_SOURCE_ENTRY_START = re.compile(r"^-\s+(https?://\S+)\s*$")
# Field line: indented key: value pair (at least 2 spaces of indent).
_FIELD_LINE = re.compile(r"^\s{2,}(\w+):\s*(.*)$")


def _strip_comments(body: str) -> str:
    """Remove HTML comments from *body*."""
    return _HTML_COMMENT_RE.sub("", body)


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
    design: str = ""
    references: str = ""
    fill_in_purpose: bool = False
    fill_in_architecture: bool = False
    fill_in_design: bool = False


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
            spec.design = body.strip()
        elif key == "references":
            spec.references = body.strip()

    # fill_in_design: true only when design body has <FILL IN> marker
    # AND no reference entries have visual-target role.
    design_body = sections.get("Design", "")
    has_design_marker = bool(_FILL_IN_RE.search(_strip_comments(design_body)))
    refs_body = sections.get("References", "")
    has_visual_target = "visual-target" in refs_body
    spec.fill_in_design = has_design_marker and not has_visual_target

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
            # Save the previous section.
            sections[current_heading] = "".join(current_lines)
            current_heading = match.group(1).strip()
            current_lines = []
        else:
            current_lines.append(line)

    # Save the last section.
    sections[current_heading] = "".join(current_lines)
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


def format_spec_for_prompt(spec: ProductSpec) -> str:
    """Format the spec for injection into an LLM system or user prompt.

    Returns the full raw text wrapped in a labelled section so the LLM
    understands the authority of the content.
    """
    return (
        "PRODUCT SPECIFICATION (authored by the user — this is authoritative "
        "and takes precedence over scraped content when they conflict):\n\n"
        f"{spec.raw}"
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
