"""Detect gaps between extracted features/examples and the current PLAN.md."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from duplo.claude_cli import query
from duplo.parsing import strip_fences
from duplo.doc_examples import CodeExample
from duplo.extractor import Feature

_SYSTEM = """\
You are a project analyst comparing extracted product features and code examples
against an existing build plan (PLAN.md).

Your job is to identify features and code examples that are NOT yet covered by
the plan. A feature is "covered" if the plan contains a task or description that
clearly addresses it — even if the wording differs. A code example is "covered"
if the plan includes a task to implement or test the functionality it demonstrates.

IMPORTANT: Skip any feature that is infeasible or irrelevant for the target
platform and language. For example, do not report "Windows support" as missing
for a macOS-only SwiftUI app, or "JavaScript plugin API" for a project that
has no extension system in its architecture. Only report features that could
reasonably be implemented within the project's technology stack.

Return ONLY a JSON object with these fields:
  "missing_features" – array of objects, each with:
      "name" – the feature name (from the input list)
      "reason" – one sentence explaining why it is not covered
  "missing_examples" – array of objects, each with:
      "index" – the example index (from the input list)
      "summary" – brief description of what the example demonstrates
      "reason" – one sentence explaining why it is not covered

If everything is covered, return empty arrays.
Do NOT include features or examples that ARE covered by the plan.
Do NOT include features that are infeasible for the target platform.
Do NOT invent new features. Only return features whose "name" field EXACTLY
matches one of the names in the extracted features list. If a feature is not
in the input list, it does not exist and must not be reported as missing.
"""

_MAX_PLAN_CHARS = 30_000
_MAX_FEATURES_CHARS = 15_000
_MAX_EXAMPLES_CHARS = 15_000


@dataclass
class MissingFeature:
    name: str
    reason: str


@dataclass
class MissingExample:
    index: int
    summary: str
    reason: str


@dataclass
class DesignRefinement:
    category: str
    detail: str
    reason: str


@dataclass
class GapResult:
    missing_features: list[MissingFeature]
    missing_examples: list[MissingExample]
    design_refinements: list[DesignRefinement] = field(default_factory=list)


def _format_features(features: list[Feature]) -> str:
    """Format features as a numbered list for the prompt."""
    lines = []
    for i, feat in enumerate(features):
        lines.append(f"{i + 1}. [{feat.category}] {feat.name}: {feat.description}")
    return "\n".join(lines)


def _format_examples(examples: list[CodeExample]) -> str:
    """Format code examples as a numbered list for the prompt."""
    lines = []
    for i, ex in enumerate(examples):
        snippet = ex.input[:200]
        if len(ex.input) > 200:
            snippet += " …"
        source = f" (from {ex.source_url})" if ex.source_url else ""
        lines.append(f"{i}. {snippet}{source}")
    return "\n".join(lines)


def detect_gaps(
    plan_content: str,
    features: list[Feature],
    examples: list[CodeExample] | None = None,
    *,
    platform: str = "",
    language: str = "",
) -> GapResult:
    """Compare *features* and *examples* against *plan_content*.

    Returns a :class:`GapResult` with lists of features and examples
    not yet covered by the plan.
    """
    if not features and not examples:
        return GapResult(missing_features=[], missing_examples=[])

    features_text = _format_features(features)[:_MAX_FEATURES_CHARS]
    examples_text = ""
    if examples:
        examples_text = _format_examples(examples)[:_MAX_EXAMPLES_CHARS]

    plan_text = plan_content[:_MAX_PLAN_CHARS]

    user_parts = []
    if platform or language:
        parts = []
        if platform:
            parts.append(f"platform: {platform}")
        if language:
            parts.append(f"language: {language}")
        user_parts.append(f"Target project: {', '.join(parts)}\n")
    user_parts.append(f"Current PLAN.md:\n{plan_text}\n")
    user_parts.append(f"Extracted features:\n{features_text}\n")
    if examples_text:
        user_parts.append(f"Extracted code examples:\n{examples_text}\n")
    else:
        user_parts.append("No code examples to compare.\n")
    user_parts.append("Identify any features or examples NOT covered by the plan.")

    prompt = "\n".join(user_parts)
    raw = query(prompt, system=_SYSTEM)
    return _parse_result(raw, features, examples or [])


def _parse_result(
    raw: str,
    features: list[Feature],
    examples: list[CodeExample],
) -> GapResult:
    """Parse the JSON response into a :class:`GapResult`."""
    text = strip_fences(raw)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return GapResult(missing_features=[], missing_examples=[])

    if not isinstance(data, dict):
        return GapResult(missing_features=[], missing_examples=[])

    # Build a set of valid feature names so we can reject hallucinated ones.
    valid_names = {f.name for f in features}

    missing_features: list[MissingFeature] = []
    for item in data.get("missing_features", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        reason = str(item.get("reason", "")).strip()
        if name and name in valid_names:
            missing_features.append(MissingFeature(name=name, reason=reason))

    missing_examples: list[MissingExample] = []
    for item in data.get("missing_examples", []):
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(examples):
            continue
        summary = str(item.get("summary", "")).strip()
        reason = str(item.get("reason", "")).strip()
        missing_examples.append(MissingExample(index=index, summary=summary, reason=reason))

    return GapResult(
        missing_features=missing_features,
        missing_examples=missing_examples,
    )


def _parse_design_markdown(text: str) -> dict:
    """Parse AUTO-GENERATED design block markdown into a design dict.

    Recognises the markdown format produced by
    :func:`~duplo.design_extractor.format_design_block`:

    - ``### Colors`` → ``colors`` dict (values may be backtick-wrapped)
    - ``### Typography`` → ``fonts`` dict
    - ``### Spacing`` → ``spacing`` dict
    - ``### Layout`` → ``layout`` dict
    - ``### Component Styles`` → ``components`` list of ``{name, style}``

    Returns a dict with the same shape as ``duplo.json``'s
    ``design_requirements``.
    """
    if not text or not text.strip():
        return {}

    result: dict = {}
    section: str = ""
    _SECTION_MAP = {
        "colors": "colors",
        "typography": "fonts",
        "spacing": "spacing",
        "layout": "layout",
        "component styles": "components",
    }

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            heading = stripped[4:].strip().lower()
            section = _SECTION_MAP.get(heading, "")
            continue
        if not section or not stripped.startswith("- **"):
            continue
        # Parse "- **key**: value" pattern.
        rest = stripped[4:]  # after "- **"
        close = rest.find("**")
        if close < 0:
            continue
        key = rest[:close].rstrip(":")
        value = rest[close + 2 :].lstrip(": ").strip()
        # Strip backtick wrapping (colors use `#hex`).
        if value.startswith("`") and value.endswith("`"):
            value = value[1:-1]
        if section == "components":
            comps = result.setdefault("components", [])
            entry: dict[str, str] = {"name": key}
            if value:
                entry["style"] = value
            comps.append(entry)
        else:
            result.setdefault(section, {})[key] = value

    return result


def _merge_design_dicts(a: dict, b: dict) -> dict:
    """Merge two design dicts, unioning their contents.

    For dict-valued keys (colors, fonts, spacing, layout), entries from
    *a* take precedence on key collision.  For ``components``, lists are
    concatenated and deduplicated by ``name`` (first occurrence wins).
    """
    merged: dict = {}
    for key in ("colors", "fonts", "spacing", "layout"):
        av = a.get(key, {})
        bv = b.get(key, {})
        if av or bv:
            combined = dict(bv) if isinstance(bv, dict) else {}
            if isinstance(av, dict):
                combined.update(av)  # a wins on collision
            merged[key] = combined

    ac = a.get("components", [])
    bc = b.get("components", [])
    if ac or bc:
        seen: set[str] = set()
        merged_comps: list[dict] = []
        for comp in list(ac) + list(bc):
            if not isinstance(comp, dict):
                continue
            name = comp.get("name", "")
            if name and name not in seen:
                seen.add(name)
                merged_comps.append(comp)
        merged["components"] = merged_comps

    return merged


def detect_design_gaps(
    plan_content: str,
    design: dict,
) -> list[DesignRefinement]:
    """Compare *design* requirements against *plan_content*.

    Checks whether specific colors, fonts, layout details, and components
    from the design requirements are mentioned in the plan. Returns a list
    of :class:`DesignRefinement` for items not found.

    Args:
        plan_content: Current PLAN.md content.
        design: Design requirements dict with colors, fonts, spacing,
            layout, and components keys.

    Returns:
        List of design refinements not yet reflected in the plan.
    """
    if not design:
        return []

    plan_lower = plan_content.lower()
    refinements: list[DesignRefinement] = []

    colors = design.get("colors", {})
    for key, value in colors.items() if isinstance(colors, dict) else ():
        if isinstance(value, str) and value.lower() not in plan_lower:
            refinements.append(
                DesignRefinement(
                    category="color",
                    detail=f"{key}: {value}",
                    reason=f"Color {value} ({key}) not mentioned in plan",
                )
            )

    fonts = design.get("fonts", {})
    for key, value in fonts.items() if isinstance(fonts, dict) else ():
        if isinstance(value, str) and value.lower() not in plan_lower:
            refinements.append(
                DesignRefinement(
                    category="font",
                    detail=f"{key}: {value}",
                    reason=f"Font {value} ({key}) not mentioned in plan",
                )
            )

    for comp in design.get("components", []):
        if not isinstance(comp, dict):
            continue
        name = comp.get("name", "")
        if name and name.lower() not in plan_lower:
            style = comp.get("style", "")
            refinements.append(
                DesignRefinement(
                    category="component",
                    detail=f"{name}: {style}" if style else name,
                    reason=f"Component '{name}' not mentioned in plan",
                )
            )

    return refinements


def format_gap_tasks(result: GapResult) -> str:
    """Format gap results as PLAN.md checklist items to append."""
    if (
        not result.missing_features
        and not result.missing_examples
        and not result.design_refinements
    ):
        return ""

    lines: list[str] = []
    lines.append("")
    lines.append("## Gaps detected from updated reference materials")
    lines.append("")

    for feat in result.missing_features:
        reason_suffix = f" ({feat.reason})" if feat.reason else ""
        lines.append(f"- [ ] Implement {feat.name}{reason_suffix}")

    for ex in result.missing_examples:
        desc = ex.summary or f"example #{ex.index}"
        reason_suffix = f" ({ex.reason})" if ex.reason else ""
        lines.append(f"- [ ] Add test/implementation for {desc}{reason_suffix}")

    # Group design refinements by category into composite tasks
    # instead of listing one task per raw design token.
    if result.design_refinements:
        by_category: dict[str, list[DesignRefinement]] = {}
        for dr in result.design_refinements:
            by_category.setdefault(dr.category, []).append(dr)
        category_labels = {
            "color": "Update color palette",
            "font": "Update typography",
            "component": "Update component styles",
        }
        for cat, items in by_category.items():
            label = category_labels.get(cat, f"Update {cat} design")
            details = ", ".join(item.detail for item in items)
            lines.append(f"- [ ] {label}: {details}")

    lines.append("")
    return "\n".join(lines)
