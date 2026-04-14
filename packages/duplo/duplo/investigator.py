"""Intelligent product-level bug diagnosis using LLM analysis.

Gathers all available product context — reference frames, frame descriptions,
design requirements, current screenshot, features, code examples, user
complaints, and user-supplied screenshots — then sends everything to an LLM
to produce specific, evidence-cited bug descriptions.

This is the duplo-level analog of mcloop's ``investigate`` command, operating
at the product-knowledge level rather than the code level.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from typing import TYPE_CHECKING

from duplo.claude_cli import ClaudeCliError, query, query_with_images
from duplo.diagnostics import record_failure
from duplo.parsing import strip_fences

if TYPE_CHECKING:
    from duplo.spec_reader import BehaviorContract, ReferenceEntry, SourceEntry

_SYSTEM = """\
You are a product-level QA analyst. You have deep knowledge of the target
product being duplicated. Your job is to compare the current state of the
app against the original product's behavior and appearance, then produce
a precise, evidence-cited bug report.

You will receive:
- Reference screenshots from the original product (if available)
- The current app screenshot (if available)
- User-supplied screenshots showing the problem (if available)
- Frame descriptions from the original product demo video
- Design requirements extracted from the original product
- Feature list with implementation status
- Code examples from the product documentation
- Counter-example references: files the user flagged as patterns to AVOID
- Counter-example source URLs: URLs the user flagged as patterns to AVOID
- Documentation references: supplementary text from docs-role files
- Behavior contracts: ground-truth input → expected output pairs
- The user's bug report or complaint

For each bug you identify, you must:
1. State what is wrong (the symptom)
2. State what it SHOULD look like or do, citing specific evidence
   (reference frame filename, design requirement, feature spec,
   code example, behavior contract, documentation reference, or
   counter-example)
3. If the bug violates a behavior contract, set "contradicts" to
   identify which contract (e.g. "behavior contract: `input` → `expected`")
4. If the app's behavior resembles a counter-example pattern the user
   flagged to avoid, set "avoids_pattern" to identify which
   counter-example (e.g. "counter-example: filename.png — notes" or
   "counter-example URL: https://... — notes")
5. Classify severity as critical/major/minor
6. Suggest the likely area of the codebase to investigate
7. List all evidence sources used in "evidence_sources" — include
   frame filenames, "frame_descriptions", "design_requirements",
   "documentation", "behavior_contracts", "counter-examples",
   "code_examples", or "counter-example_sources" as appropriate

Respond ONLY with a JSON object:
{
  "diagnosis": [
    {
      "symptom": "Labeled expressions like 'Price: $7 × 4' show no result",
      "expected": "Should display '$28' on the right (ref: frame_0003.png, docs: API guide § expressions)",
      "severity": "critical",
      "area": "Expression parser — label/colon prefix handling",
      "evidence_sources": ["frame_0003.png", "frame_descriptions", "behavior_contracts", "documentation"],
      "contradicts": "behavior contract: `Price: $7 × 4` → `$28`",
      "avoids_pattern": null
    },
    {
      "symptom": "Layout uses a cluttered sidebar instead of clean inline results",
      "expected": "Results should appear inline next to the expression (ref: frame_0001.png)",
      "severity": "major",
      "area": "UI layout — result display component",
      "evidence_sources": ["frame_0001.png", "design_requirements", "counter-examples"],
      "contradicts": null,
      "avoids_pattern": "counter-example: bad_layout.png — cluttered sidebar"
    }
  ],
  "summary": "One-paragraph overall assessment of the app's current state relative to the target product."
}

Be specific. Cite filenames, exact values, exact colors. Do not speculate
beyond what the evidence supports. If you cannot determine a bug from the
available evidence, say so rather than guessing.
"""


@dataclass
class Diagnosis:
    """A single diagnosed bug with evidence citations."""

    symptom: str
    expected: str
    severity: str  # "critical" | "major" | "minor"
    area: str
    evidence_sources: list[str] = field(default_factory=list)
    contradicts: str = ""
    avoids_pattern: str = ""


@dataclass
class InvestigationResult:
    """Complete investigation output."""

    diagnoses: list[Diagnosis]
    summary: str
    raw_response: str = ""


def investigate(
    complaints: list[str],
    *,
    user_screenshots: list[Path] | None = None,
    spec_text: str = "",
    counter_examples: list[ReferenceEntry] | None = None,
    counter_example_sources: list[SourceEntry] | None = None,
    docs_text: str = "",
    behavior_contracts: list[BehaviorContract] | None = None,
    model: str = "opus",
) -> InvestigationResult:
    """Run an intelligent product-level investigation.

    Gathers all available context from the duplo project directory,
    combines it with the user's complaints and optional screenshots,
    and sends everything to an LLM for diagnosis.

    Args:
        complaints: User-provided bug descriptions.
        user_screenshots: Optional paths to user-supplied screenshot files.
        spec_text: Formatted spec text for the prompt.
        counter_examples: Reference entries with counter-example role.
        counter_example_sources: Source entries with counter-example role.
        docs_text: Combined text from docs-role reference files.
        behavior_contracts: Ground-truth input/output pairs from the spec.
        model: Claude model alias (default ``"opus"``).

    Returns:
        An :class:`InvestigationResult` with structured diagnoses.
    """
    context = _gather_context()
    context["spec_text"] = spec_text
    context["counter_examples"] = counter_examples or []
    context["counter_example_sources"] = counter_example_sources or []
    context["docs_text"] = docs_text
    context["behavior_contracts"] = behavior_contracts or []
    prompt = _build_prompt(complaints, context, user_screenshots=user_screenshots)

    # Collect all image paths for the vision call.
    image_paths: list[Path] = []

    # Reference frames first.
    image_paths.extend(context.get("reference_images", []))

    # Current app screenshot.
    current_shot = context.get("current_screenshot")
    if current_shot:
        image_paths.append(current_shot)

    # User-supplied screenshots.
    if user_screenshots:
        for p in user_screenshots:
            if p.exists():
                image_paths.append(p)

    # Counter-example images (patterns to AVOID).
    for entry in context.get("counter_examples", []):
        if entry.path.exists():
            image_paths.append(entry.path)

    if not image_paths:
        # No images — fall back to text-only query.
        try:
            raw = query(prompt, system=_SYSTEM, model=model)
        except ClaudeCliError as exc:
            record_failure(
                "investigator:investigate",
                "llm",
                f"Investigation query failed: {exc}",
            )
            return InvestigationResult(
                diagnoses=[],
                summary=f"Investigation failed: {exc}",
                raw_response="",
            )
    else:
        try:
            raw = query_with_images(prompt, image_paths, system=_SYSTEM, model=model)
        except ClaudeCliError as exc:
            record_failure(
                "investigator:investigate",
                "llm",
                f"Investigation query_with_images failed: {exc}",
            )
            return InvestigationResult(
                diagnoses=[],
                summary=f"Investigation failed: {exc}",
                raw_response="",
            )

    return _parse_result(raw)


def _gather_context() -> dict:
    """Collect all available product context from the duplo project directory.

    Reads from ``.duplo/duplo.json``, ``.duplo/references/``,
    ``screenshots/current/``, and ``.duplo/examples/``.

    Returns a dict with keys:
        - ``reference_images``: list[Path] of reference frame PNGs
        - ``current_screenshot``: Path | None
        - ``frame_descriptions``: list[dict]
        - ``design_requirements``: dict
        - ``features``: list[dict]
        - ``code_examples``: list[dict]
        - ``issues``: list[dict]  (open issues)
        - ``app_name``: str
        - ``source_url``: str
    """
    context: dict = {
        "reference_images": [],
        "current_screenshot": None,
        "frame_descriptions": [],
        "design_requirements": {},
        "features": [],
        "code_examples": [],
        "issues": [],
        "app_name": "",
        "source_url": "",
    }

    duplo_path = Path(".duplo/duplo.json")
    if not duplo_path.exists():
        return context

    try:
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return context

    context["app_name"] = data.get("app_name", "")
    context["source_url"] = data.get("source_url", "")
    context["frame_descriptions"] = data.get("frame_descriptions", [])
    context["design_requirements"] = data.get("design_requirements", {})
    context["features"] = data.get("features", [])
    context["code_examples"] = data.get("code_examples", [])

    # Open issues only.
    all_issues = data.get("issues", [])
    context["issues"] = [i for i in all_issues if i.get("status", "open") == "open"]

    # Reference images from .duplo/references/.
    refs_dir = Path(".duplo/references")
    if refs_dir.is_dir():
        context["reference_images"] = sorted(refs_dir.glob("*.png"))

    # Current app screenshot.
    current = Path("screenshots/current/main.png")
    if current.exists():
        context["current_screenshot"] = current

    return context


def _build_prompt(
    complaints: list[str],
    context: dict,
    *,
    user_screenshots: list[Path] | None = None,
) -> str:
    """Assemble the investigation prompt from complaints and gathered context.

    The prompt is structured so the LLM receives all textual context inline,
    while image files are passed separately via ``query_with_images``.
    """
    sections: list[str] = []

    # Image legend — tell the LLM what each image is.
    image_index = 0
    ref_images = context.get("reference_images", [])
    if ref_images:
        legend_lines = ["REFERENCE IMAGES (from the original product):"]
        for img in ref_images:
            image_index += 1
            legend_lines.append(f"  Image {image_index}: {img.name}")
        sections.append("\n".join(legend_lines))

    current_shot = context.get("current_screenshot")
    if current_shot:
        image_index += 1
        sections.append(f"CURRENT APP SCREENSHOT:\n  Image {image_index}: {current_shot.name}")

    # User-supplied screenshots.
    if user_screenshots:
        valid = [p for p in user_screenshots if p.exists()]
        if valid:
            legend_lines = ["USER-SUPPLIED SCREENSHOTS (showing the reported problem):"]
            for img in valid:
                image_index += 1
                legend_lines.append(f"  Image {image_index}: {img.name}")
            sections.append("\n".join(legend_lines))

    # Counter-example images (patterns to AVOID).
    counter_examples = context.get("counter_examples", [])
    ce_images = [e for e in counter_examples if e.path.exists()]
    if ce_images:
        legend_lines = [
            "COUNTER-EXAMPLE IMAGES — AVOID this pattern "
            "(the user flagged these as anti-patterns):"
        ]
        for entry in ce_images:
            image_index += 1
            notes_part = f" — {entry.notes}" if entry.notes else ""
            legend_lines.append(f"  Image {image_index}: {entry.path.name}{notes_part}")
        sections.append("\n".join(legend_lines))

    # Frame descriptions.
    frame_descs = context.get("frame_descriptions", [])
    if frame_descs:
        fd_lines = ["FRAME DESCRIPTIONS (from original product demo video):"]
        for fd in frame_descs:
            fd_lines.append(
                f"  {fd.get('filename', '?')}: [{fd.get('state', '?')}] {fd.get('detail', '')}"
            )
        sections.append("\n".join(fd_lines))

    # Design requirements.
    design = context.get("design_requirements", {})
    if design:
        sections.append(f"DESIGN REQUIREMENTS:\n{json.dumps(design, indent=2)}")

    # Features (abbreviated — just name, status, category).
    features = context.get("features", [])
    if features:
        feat_lines = ["FEATURE LIST:"]
        for f in features:
            status = f.get("status", "pending")
            feat_lines.append(f"  [{status}] {f.get('name', '?')}: {f.get('description', '')}")
        sections.append("\n".join(feat_lines))

    # Code examples (truncated to avoid overwhelming the context).
    code_examples = context.get("code_examples", [])
    if code_examples:
        ex_lines = ["CODE EXAMPLES FROM PRODUCT DOCS:"]
        for i, ex in enumerate(code_examples[:20]):  # Cap at 20.
            inp = ex.get("input", "")
            out = ex.get("expected_output", "")
            if inp and out:
                # Truncate long examples.
                if len(inp) > 200:
                    inp = inp[:200] + "…"
                if len(out) > 200:
                    out = out[:200] + "…"
                ex_lines.append(f"  Example {i + 1}: {inp} → {out}")
        sections.append("\n".join(ex_lines))

    # Open issues already known.
    issues = context.get("issues", [])
    if issues:
        iss_lines = ["KNOWN OPEN ISSUES:"]
        for iss in issues:
            iss_lines.append(f"  - {iss.get('description', '?')}")
        sections.append("\n".join(iss_lines))

    # Counter-example references (files the user flagged as anti-patterns).
    counter_examples = context.get("counter_examples", [])
    if counter_examples:
        ce_lines = [
            "COUNTER-EXAMPLES — AVOID these patterns "
            "(the user has flagged these as anti-patterns):"
        ]
        for entry in counter_examples:
            notes_part = f" — {entry.notes}" if entry.notes else ""
            ce_lines.append(f"  {entry.path.name}{notes_part}")
        sections.append("\n".join(ce_lines))

    # Counter-example source URLs (declarative context only, NOT fetched).
    counter_example_sources = context.get("counter_example_sources", [])
    if counter_example_sources:
        ces_lines = [
            "COUNTER-EXAMPLE URLS — AVOID these patterns "
            "(the user has flagged these URLs as anti-patterns):"
        ]
        for entry in counter_example_sources:
            notes_part = f" — {entry.notes}" if entry.notes else ""
            ces_lines.append(f"  {entry.url}{notes_part}")
        sections.append("\n".join(ces_lines))

    # Documentation references (supplementary text from docs-role files).
    docs_text = context.get("docs_text", "")
    if docs_text:
        sections.append(
            "SUPPLEMENTARY DOCUMENTATION (from docs-role reference files):\n" + docs_text
        )

    # Behavior contracts (ground-truth input → expected output pairs).
    behavior_contracts = context.get("behavior_contracts", [])
    if behavior_contracts:
        bc_lines = ["BEHAVIOR CONTRACTS (ground-truth — if the app violates these, it is a bug):"]
        for contract in behavior_contracts:
            bc_lines.append(f"  `{contract.input}` → `{contract.expected}`")
        sections.append("\n".join(bc_lines))

    # Product spec.
    spec_text = context.get("spec_text", "")
    if spec_text:
        sections.append(
            "PRODUCT SPECIFICATION (authoritative, from the user — "
            "this defines intended behavior):\n" + spec_text
        )

    # User complaints.
    complaint_lines = ["USER BUG REPORT:"]
    for c in complaints:
        complaint_lines.append(f"  {c}")
    sections.append("\n".join(complaint_lines))

    # Final instruction.
    sections.append(
        "Analyze all the evidence above and produce your diagnosis. "
        "If user-supplied screenshots are present (after the current app screenshot), "
        "use them as additional evidence of the reported bugs. "
        "Respond with ONLY the JSON object as specified."
    )

    return "\n\n".join(sections)


def _ensure_list(value: object) -> list[str]:
    """Coerce *value* to a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [value]
    return []


def _parse_result(raw: str) -> InvestigationResult:
    """Parse the LLM's JSON response into an InvestigationResult."""
    text = strip_fences(raw.strip())

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON from a larger response.
        brace_start = raw.find("{")
        brace_end = raw.rfind("}")
        if brace_start != -1 and brace_end > brace_start:
            try:
                data = json.loads(raw[brace_start : brace_end + 1])
            except json.JSONDecodeError:
                return InvestigationResult(
                    diagnoses=[],
                    summary="Failed to parse investigation response.",
                    raw_response=raw,
                )
        else:
            return InvestigationResult(
                diagnoses=[],
                summary="Failed to parse investigation response.",
                raw_response=raw,
            )

    if not isinstance(data, dict):
        return InvestigationResult(
            diagnoses=[],
            summary="Unexpected response format.",
            raw_response=raw,
        )

    diagnoses: list[Diagnosis] = []
    for item in data.get("diagnosis", []):
        if not isinstance(item, dict):
            continue
        diagnoses.append(
            Diagnosis(
                symptom=str(item.get("symptom", "")),
                expected=str(item.get("expected", "")),
                severity=str(item.get("severity", "major")),
                area=str(item.get("area", "")),
                evidence_sources=_ensure_list(item.get("evidence_sources", [])),
                contradicts=str(item.get("contradicts", "") or ""),
                avoids_pattern=str(item.get("avoids_pattern", "") or ""),
            )
        )

    summary = str(data.get("summary", ""))

    return InvestigationResult(
        diagnoses=diagnoses,
        summary=summary,
        raw_response=raw,
    )


def format_investigation(result: InvestigationResult) -> str:
    """Format an investigation result as human-readable text for the terminal."""
    lines: list[str] = []

    if result.summary:
        lines.append(f"\n{result.summary}")

    if not result.diagnoses:
        if not result.summary:
            lines.append("\nNo specific bugs diagnosed from the available evidence.")
        return "\n".join(lines)

    lines.append(f"\n{len(result.diagnoses)} bug(s) diagnosed:\n")

    for i, diag in enumerate(result.diagnoses, 1):
        severity_tag = diag.severity.upper()
        lines.append(f"  [{severity_tag}] Bug {i}: {diag.symptom}")
        lines.append(f"    Expected: {diag.expected}")
        lines.append(f"    Area: {diag.area}")
        if diag.contradicts:
            lines.append(f"    Contradicts: {diag.contradicts}")
        if diag.avoids_pattern:
            lines.append(f"    Avoids pattern: {diag.avoids_pattern}")
        if diag.evidence_sources:
            lines.append(f"    Evidence: {', '.join(diag.evidence_sources)}")
        lines.append("")

    return "\n".join(lines)


def investigation_to_fix_tasks(result: InvestigationResult) -> list[str]:
    """Convert diagnoses into fix task lines for PLAN.md.

    Each diagnosis becomes a checklist item with the symptom, expected
    behavior, and area to investigate.

    Returns a list of ``- [ ] Fix: ...`` strings.
    """
    tasks: list[str] = []
    for diag in result.diagnoses:
        parts = [diag.symptom]
        if diag.expected:
            parts.append(f"Expected: {diag.expected}")
        if diag.area:
            parts.append(f"Area: {diag.area}")
        description = ". ".join(parts)
        # Collapse to single line.
        oneline = description.replace("\n", " ").strip()
        tasks.append(f'- [ ] Fix: {oneline} [fix: "{diag.symptom}"]')
    return tasks
