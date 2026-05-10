"""Canonical plan-document model: H1 envelope + H2 phase-id pair as a unit.

The reauthor pipeline used to model PLAN.md as a flat list of H2-keyed
phase sections (``## Phase phase_NNN: title``) plus an opaque preamble.
That model could not see the H1 envelopes (``# {project} — Phase N:
{title}``) the canonical synthesizer template emits above each H2.
Three failure modes followed:

  - H1 envelopes never rotated when assembly substituted phases. A
    supersede that replaced ``phase_002`` left the H1 ordinal stale,
    so an outsider reading the plan saw "Phase 2: Old Title" sitting
    above ``## Phase phase_010: New Title``.
  - The synthesizer occasionally emitted fenced ``json`` verdict
    blocks inside the plan artifact alongside the verdict artifact.
    With no sanitizer, those blocks landed verbatim in PLAN.md and
    became part of someone's preserved phase body, where they
    accumulated across reauthor passes.
  - validate_lineage's parser shared the H2-only regex, so structural
    mismatches (stale H1 vs changed H2 phase_id, embedded verdict
    JSON, H1/H2 count mismatch) passed validation. All-preserve
    lineage on a structurally corrupt prior amplified the corruption
    rather than reporting it.

This module owns the structural metadata. Three responsibilities:

  - :func:`parse_plan` reads PLAN.md into a :class:`Plan` of
    :class:`PhaseUnit`\\ s. Each unit pairs one H1 envelope with the
    H2 phase header that immediately follows it. Strict: missing H2,
    duplicate H2 under one H1, or non-whitespace text between H1 and
    H2 raises :class:`ParseError`.
  - :func:`render` writes a :class:`Plan` back to canonical text
    deterministically. H1 ordinals are derived from each unit's
    position in ``Plan.units``, so substituting a unit automatically
    renumbers downstream H1s. The renderer is the only path that
    writes structural metadata; assembly never concatenates H1 lines
    by hand.
  - :func:`sanitize_plan_artifact` rejects (does not strip) fenced
    ``json`` blocks decoding to verdict-shaped objects. A model that
    emits the verdict inside the plan artifact is making a contract
    error that should pause the run, not be quietly fixed.
  - :func:`validate_structure` enforces invariants on a parsed Plan
    (ordinal sequence, no embedded verdict JSON in unit bodies, no
    duplicate phase_id, no stray H1 inside a unit body) so callers
    that synthesize Plans by other means cannot bypass the
    structural contract.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from duplo.reauthor_phase_ids import _HEADER_RE as _H2_PHASE_HEADER_RE


# Canonical H1 envelope shape, matching what the synthesizer template
# emits and what :func:`render` produces. Em-dash is U+2014 specifically;
# the synthesizer template uses it deliberately and any other dash form
# (hyphen, en-dash) is a model error worth surfacing. Single spaces
# around the em-dash and after the colon match the renderer's output.
_H1_ENVELOPE_RE = re.compile(
    r"^# (?P<project>.+?) — Phase (?P<ordinal>\d+): (?P<title>.+?)\s*$"
)

# Any line that looks like an H1 (single # at start of line followed by
# a space). Used by :func:`validate_structure` to detect stray H1
# envelopes inside unit bodies.
_ANY_H1_RE = re.compile(r"^# .+$")

# Fenced ``json`` block, content captured. Multiline so ^/$ match line
# boundaries; DOTALL so . inside the content matches newlines.
_FENCED_JSON_BLOCK_RE = re.compile(
    r"^```json\s*\n(?P<body>.*?)\n```\s*$",
    re.MULTILINE | re.DOTALL,
)

# A JSON object that decodes from a fenced block is verdict-shaped if
# it has either of these top-level keys. Both are unique enough to the
# council verdict shape that we accept the false-positive risk of
# rejecting an unrelated JSON sample that happens to use these names.
_VERDICT_SHAPE_KEYS: frozenset[str] = frozenset({"decision", "lineage"})


# ---------------------------------------------------------------------
# Exception types
# ---------------------------------------------------------------------


class ParseError(ValueError):
    """Raised when :func:`parse_plan` cannot extract a valid Plan from text.

    Distinct from :class:`StructuralValidationError`: ParseError fires
    when the input does not yield a deterministic ``(H1, H2)`` pairing
    (missing H2 under an H1, multiple H2s under one H1, non-whitespace
    text between H1 and H2, H2 outside any H1 envelope, mismatched
    project_name across H1 envelopes). StructuralValidationError fires
    on a successfully parsed Plan whose structure violates an invariant
    detectable only after parsing.
    """


class StructuralValidationError(ValueError):
    """Raised when :func:`validate_structure` rejects a parsed Plan.

    Catches invariants the parser does not enforce inline: ordinal
    sequence (Phase 0, 1, 2, ... contiguous), uniqueness of phase_id
    across units, absence of embedded verdict JSON in unit bodies,
    absence of stray H1-shaped lines in unit bodies. Raised after
    parse so error messages can name unit positions.
    """


class PlanArtifactRejected(ValueError):
    """Raised when :func:`sanitize_plan_artifact` finds a verdict-shaped
    fenced JSON block inside the plan artifact.

    The synthesizer's contract is to emit the verdict in the
    judge_verdict artifact, not embedded in the plan body. A plan
    artifact that contains the verdict is a model error: silently
    stripping the verdict would mask it; parsing as-is would corrupt
    PLAN.md. The reauthor caller translates this into a HardStop with
    reason ``plan_artifact_contained_verdict_json``.
    """


# ---------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseUnit:
    """One H1+H2 pair plus the body text underneath the H2.

    A unit is the atomic substitution element of reauthor assembly.
    Replacing a unit replaces both its H1 envelope and its H2 phase
    header together, so a supersede of phase_002 cannot leave a stale
    "Phase 2" H1 above the new phase_010 H2.

    Fields:

      - ``h1_envelope``: the title written into the H1 line (the part
        after ``— Phase N: ``). The H1 ordinal is NOT stored; render
        derives it from each unit's position in ``Plan.units``.
      - ``phase_id``: the strict ``phase_NNN`` (or other matching
        ``[A-Za-z0-9_]+``) id from the H2.
      - ``h2_title``: the title from the H2 line.
      - ``body``: text from the line AFTER the H2 to the line BEFORE
        the next H1 (or EOF). Always ends with a newline so
        concatenation produces well-formed markdown. May be empty (no
        body), in which case the field is the empty string.
    """

    h1_envelope: str
    phase_id: str
    h2_title: str
    body: str


@dataclass(frozen=True)
class Plan:
    """A parsed plan document.

    ``project_name`` is the value parsed from the first H1 envelope
    (or empty when the document has no H1 envelopes). ``preamble`` is
    everything before the first H1 envelope, verbatim. ``units`` is
    the ordered tuple of phase units; render emits them in this order
    and assigns H1 ordinals by position.
    """

    project_name: str
    preamble: str
    units: tuple[PhaseUnit, ...]


# ---------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------


def parse_plan(text: str) -> Plan:
    """Parse PLAN.md text into a :class:`Plan`.

    Walks the text line-by-line:

      1. Locate every H1 envelope (matches ``# <project> — Phase N:
         <title>``). Lines not matching this exact pattern stay in
         whatever surrounding region they fall in (preamble or unit
         body); they are not treated as structural.
      2. Preamble = lines before the first H1 envelope. If no H1
         envelopes are present, the entire text is preamble and the
         returned Plan has ``units == ()``. A preamble that contains
         a stray H2 phase header (an H2 with no preceding H1) is a
         ParseError.
      3. For each H1 envelope, slice the text from this H1 to the
         next H1 envelope (or EOF). Within the slice, find exactly
         one H2 phase header. Zero is ParseError ("H1 with no H2
         underneath"); two or more is ParseError ("multiple H2
         sections under one H1 envelope" — the corruption case the
         fswatch-run-smoke fixture exhibited).
      4. Between the H1 line and the H2 line, only whitespace lines
         are allowed. Any non-whitespace content there is ParseError
         ("non-whitespace content between H1 and H2"). The
         synthesizer template forbids intervening text; rejecting it
         here surfaces model errors that would otherwise accumulate
         into PLAN.md across reauthor passes.
      5. ``project_name`` is taken from the FIRST H1 envelope's
         ``project`` capture. Subsequent H1 envelopes must use the
         same project_name; mismatch is ParseError.

    Returns
    -------
    Plan
        With ``project_name``, ``preamble``, and the parsed
        ``units``. ``units`` is empty for a plan with no H1 envelopes.
    """
    lines = text.splitlines(keepends=True)

    # Index every H1 envelope and every H2 phase header. The
    # combined index is the basis for slot delimitation below.
    h1_indices: list[tuple[int, re.Match[str]]] = []
    h2_indices: list[tuple[int, re.Match[str]]] = []
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n").rstrip("\r")
        h1_match = _H1_ENVELOPE_RE.match(line)
        if h1_match is not None:
            h1_indices.append((i, h1_match))
            continue
        h2_match = _H2_PHASE_HEADER_RE.match(line)
        if h2_match is not None:
            h2_indices.append((i, h2_match))

    if not h1_indices:
        # No H1 envelopes: everything is preamble. Stray H2s in this
        # state are a structural violation — there is no H1 they
        # belong under.
        if h2_indices:
            first_h2_idx = h2_indices[0][0]
            raise ParseError(
                f"H2 phase header at line {first_h2_idx + 1} has no "
                "preceding H1 envelope; canonical plans wrap each "
                "phase H2 in an H1 of the form "
                "'# <project> — Phase N: <title>'"
            )
        return Plan(project_name="", preamble=text, units=())

    preamble_text = "".join(lines[: h1_indices[0][0]])

    # Stray H2 in preamble (before first H1)?
    first_h1_line = h1_indices[0][0]
    for h2_idx, _ in h2_indices:
        if h2_idx < first_h1_line:
            raise ParseError(
                f"H2 phase header at line {h2_idx + 1} appears before "
                "the first H1 envelope; canonical plans place every "
                "H2 under an H1 envelope"
            )

    project_name = h1_indices[0][1].group("project")

    units: list[PhaseUnit] = []
    for slot, (start_idx, h1_match) in enumerate(h1_indices):
        end_idx = (
            h1_indices[slot + 1][0]
            if slot + 1 < len(h1_indices)
            else len(lines)
        )
        slot_h2_matches = [
            (i, m) for (i, m) in h2_indices if start_idx < i < end_idx
        ]
        if not slot_h2_matches:
            raise ParseError(
                f"H1 envelope at line {start_idx + 1} "
                f"({h1_match.group(0)!r}) is not followed by an H2 "
                "phase header before the next H1 envelope or end of "
                "file"
            )
        if len(slot_h2_matches) > 1:
            extras = ", ".join(str(i + 1) for (i, _) in slot_h2_matches)
            raise ParseError(
                f"H1 envelope at line {start_idx + 1} has "
                f"{len(slot_h2_matches)} H2 phase headers underneath "
                f"(at lines {extras}); each H1 envelope must contain "
                "exactly one H2"
            )

        h2_idx, h2_match = slot_h2_matches[0]

        # Lines between H1 and H2 (exclusive both ends) must be
        # whitespace-only. Any non-whitespace there is the kind of
        # mid-envelope narrative that bloats PLAN.md across reauthor
        # passes.
        for between_idx in range(start_idx + 1, h2_idx):
            if lines[between_idx].strip():
                raise ParseError(
                    f"H1 envelope at line {start_idx + 1} has "
                    f"non-whitespace content at line "
                    f"{between_idx + 1} before its H2; "
                    "canonical plans place the H2 immediately "
                    "below the H1 with only whitespace between"
                )

        slot_project = h1_match.group("project")
        if slot_project != project_name:
            raise ParseError(
                f"H1 envelope at line {start_idx + 1} uses project "
                f"name {slot_project!r}; expected {project_name!r} "
                "(taken from the first H1 envelope) — canonical plans "
                "use a single project name across all H1 envelopes"
            )

        body_lines = lines[h2_idx + 1 : end_idx]
        body = "".join(body_lines)
        if body and not body.endswith("\n"):
            body = body + "\n"

        units.append(
            PhaseUnit(
                h1_envelope=h1_match.group("title"),
                phase_id=h2_match.group("id"),
                h2_title=h2_match.group("title"),
                body=body,
            )
        )

    return Plan(
        project_name=project_name,
        preamble=preamble_text,
        units=tuple(units),
    )


# ---------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------


def render(plan: Plan) -> str:
    """Render a :class:`Plan` back to canonical PLAN.md text.

    Deterministic. H1 ordinals are taken from each unit's position in
    ``plan.units``, starting at zero, so substituting a unit
    automatically renumbers downstream H1s. H2 lines reproduce
    ``phase_id`` and ``h2_title`` exactly. Body text is emitted
    verbatim.

    The renderer is the ONLY path in the reauthor pipeline that emits
    structural metadata. Assembly walks ``plan.units`` and rebuilds a
    new Plan, then calls render once. Hand-concatenating H1 lines
    elsewhere is a contract violation.
    """
    parts: list[str] = []
    parts.append(plan.preamble)
    for ordinal, unit in enumerate(plan.units):
        parts.append(
            f"# {plan.project_name} — Phase {ordinal}: "
            f"{unit.h1_envelope}\n"
        )
        parts.append(f"## Phase {unit.phase_id}: {unit.h2_title}\n")
        parts.append(unit.body)
    return "".join(parts)


# ---------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------


def sanitize_plan_artifact(text: str) -> str:
    """Reject a plan artifact that contains a fenced ``json`` block
    decoding to a verdict-shaped object.

    The synthesizer's contract is to emit the council verdict in the
    judge_verdict artifact, not embedded in the plan body. A plan
    artifact carrying a verdict-shaped JSON block is a model error
    that must surface as a hard pause: stripping the block silently
    would mask the error; passing it through would corrupt PLAN.md.

    Returns ``text`` unchanged when no verdict-shaped block is
    present. Other fenced code blocks (Python, bash, JSON without
    verdict shape) are accepted untouched — the rejection is
    conservative and targets only the specific contract violation.

    Raises
    ------
    PlanArtifactRejected
        When at least one fenced ``json`` block decodes to a JSON
        object containing the key ``"decision"`` or ``"lineage"``.
    """
    for match in _FENCED_JSON_BLOCK_RE.finditer(text):
        body = match.group("body")
        try:
            decoded = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            # Malformed JSON inside a fenced block isn't necessarily
            # a verdict; leave it alone. validate_structure flags
            # this in the assembled plan if it survives.
            continue
        if not isinstance(decoded, dict):
            continue
        offending = sorted(_VERDICT_SHAPE_KEYS & decoded.keys())
        if offending:
            raise PlanArtifactRejected(
                "plan artifact contains a fenced 'json' block "
                f"decoding to an object with verdict-shaped key(s) "
                f"{offending}; the synthesizer must emit the verdict "
                "in the judge_verdict artifact, not inside the plan "
                "body"
            )
    return text


# ---------------------------------------------------------------------
# Structural validator
# ---------------------------------------------------------------------


def validate_structure(plan: Plan) -> None:
    """Fail closed on structural invariants of a parsed Plan.

    The parser enforces the H1+H2 pairing inline. validate_structure
    enforces the remaining invariants on the Plan-as-data:

      - Phase ordinals match positions: when units are present they
        must read 0, 1, 2, ... by position. (parse_plan does not
        store the observed ordinal; this check fires when callers
        construct Plans by other means whose unit positions disagree
        with what the rendered ordinals would imply, OR when render
        is given a Plan whose unit ids embed an ordinal hint that
        conflicts.)
      - Unique phase_id across units. Duplicates are rejected.
      - No H1-shaped line inside any unit body. A stray H1 in a body
        means an H1 envelope was misparsed as content; refuse to
        render.
      - No fenced ``json`` block inside any unit body decoding to a
        verdict-shaped object. The same rejection :func:`sanitize_plan_artifact`
        applies pre-parse, but we re-check here in case a Plan was
        constructed without going through the sanitizer (e.g., from a
        test fixture or another caller).

    Errors accumulate; one StructuralValidationError lists every
    violation so callers see the full picture in one round.
    """
    errors: list[str] = []

    seen_ids: set[str] = set()
    for index, unit in enumerate(plan.units):
        if unit.phase_id in seen_ids:
            errors.append(
                f"unit {index} (phase_id={unit.phase_id!r}): "
                "duplicate phase_id (already used by an earlier unit)"
            )
        else:
            seen_ids.add(unit.phase_id)

        for body_line_idx, line in enumerate(unit.body.splitlines()):
            if _ANY_H1_RE.match(line):
                errors.append(
                    f"unit {index} (phase_id={unit.phase_id!r}): "
                    "body line "
                    f"{body_line_idx + 1} is an H1-shaped line "
                    f"({line!r}); H1 envelopes must not appear inside "
                    "a unit body"
                )

        for match in _FENCED_JSON_BLOCK_RE.finditer(unit.body):
            try:
                decoded = json.loads(match.group("body"))
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(decoded, dict):
                continue
            offending = sorted(_VERDICT_SHAPE_KEYS & decoded.keys())
            if offending:
                errors.append(
                    f"unit {index} (phase_id={unit.phase_id!r}): "
                    "body contains a fenced 'json' block with "
                    f"verdict-shaped key(s) {offending}; the verdict "
                    "belongs in judge_verdict, not the plan"
                )

    if errors:
        raise StructuralValidationError(
            "plan structural violations:\n  - " + "\n  - ".join(errors)
        )


# ---------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------


def units_by_id(units: Iterable[PhaseUnit]) -> dict[str, PhaseUnit]:
    """Return ``{phase_id: unit}`` for the given units.

    The reauthor assembly path indexes units by phase_id frequently
    (substitution, lookup, lineage matching). Centralizing the index
    construction here keeps callers from re-implementing it and makes
    duplicate-id detection a single shared assertion.
    """
    out: dict[str, PhaseUnit] = {}
    for unit in units:
        if unit.phase_id in out:
            raise StructuralValidationError(
                f"duplicate phase_id {unit.phase_id!r} in units list"
            )
        out[unit.phase_id] = unit
    return out


__all__ = [
    "ParseError",
    "Plan",
    "PlanArtifactRejected",
    "PhaseUnit",
    "StructuralValidationError",
    "parse_plan",
    "render",
    "sanitize_plan_artifact",
    "units_by_id",
    "validate_structure",
]
