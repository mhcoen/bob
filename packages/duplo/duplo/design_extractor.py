"""Extract visual design details from images using Claude Vision."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from duplo.claude_cli import ClaudeCliError, query_with_images
from duplo.parsing import strip_fences

if TYPE_CHECKING:
    from duplo.spec_reader import ProductSpec

_SYSTEM = """\
You are a visual design analyst. Given screenshot(s) of a product, extract
concrete visual design details that a developer would need to recreate the UI.

Return ONLY a JSON object with these fields:
  "colors" – object with keys: primary, secondary, background, text, accent,
             each a hex color string (e.g. "#1a73e8"). Include any other
             notable colors as additional keys.
  "fonts"  – object with keys: headings, body, mono (if applicable). Each
             value is a string describing the font family and approximate
             size (e.g. "Inter or similar sans-serif, ~16px body").
  "spacing" – object describing padding/margin patterns: content_padding,
              section_gap, element_gap. Values are approximate CSS-like
              strings (e.g. "16px", "24px 32px").
  "layout" – object with keys: navigation (top/side/bottom/none), sidebar
             (left/right/none), content_width (narrow/medium/wide/full),
             grid (description of grid/flex patterns observed).
  "components" – array of objects, each with "name" (e.g. "card", "button",
                 "input field") and "style" (brief description of visual
                 style: border-radius, shadows, borders, etc.).

Be specific about what you see. Use hex colors, approximate pixel sizes,
and concrete descriptions. If you cannot determine a value, use "unknown".
"""

_MAX_IMAGES = 10


@dataclass
class DesignRequirements:
    """Visual design requirements extracted from reference images."""

    colors: dict[str, str] = field(default_factory=dict)
    fonts: dict[str, str] = field(default_factory=dict)
    spacing: dict[str, str] = field(default_factory=dict)
    layout: dict[str, str] = field(default_factory=dict)
    components: list[dict[str, str]] = field(default_factory=list)
    source_images: list[str] = field(default_factory=list)


def extract_design(images: list[Path]) -> DesignRequirements:
    """Analyse reference images and extract visual design requirements.

    Sends up to ``_MAX_IMAGES`` images to ``claude -p`` and returns
    structured design details (colors, fonts, spacing, layout, components).

    Args:
        images: Paths to reference image files (PNG, JPG, GIF, WEBP).

    Returns:
        A :class:`DesignRequirements` with extracted visual details.
        Returns an empty instance if no images are provided or extraction
        fails.
    """
    if not images:
        return DesignRequirements()

    to_send = images[:_MAX_IMAGES]
    source_names = [img.name for img in to_send]

    prompt = (
        "Analyse these screenshot(s) and extract the visual design details.\n"
        "Return ONLY the JSON object as described."
    )
    try:
        raw = query_with_images(prompt, to_send, system=_SYSTEM)
    except ClaudeCliError:
        return DesignRequirements(source_images=source_names)
    result = _parse_design(raw)
    result.source_images = source_names
    return result


def _parse_design(raw: str) -> DesignRequirements:
    """Parse Claude's JSON response into a DesignRequirements.

    Tolerates markdown code fences wrapping the JSON.
    Returns an empty DesignRequirements if parsing fails.
    """
    text = strip_fences(raw)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return DesignRequirements()

    if not isinstance(data, dict):
        return DesignRequirements()

    colors = data.get("colors", {})
    fonts = data.get("fonts", {})
    spacing = data.get("spacing", {})
    layout = data.get("layout", {})
    return DesignRequirements(
        colors=colors if isinstance(colors, dict) else {},
        fonts=fonts if isinstance(fonts, dict) else {},
        spacing=spacing if isinstance(spacing, dict) else {},
        layout=layout if isinstance(layout, dict) else {},
        components=comps if isinstance((comps := data.get("components", [])), list) else [],
    )


def collect_design_input(
    spec: ProductSpec | None,
    visual_target_frames: list[Path] | None = None,
    site_images: list[Path] | None = None,
    site_video_frames: list[Path] | None = None,
    *,
    target_dir: Path | None = None,
) -> list[Path]:
    """Build the combined image list for design extraction.

    The design input is the union of:

    1. ``format_visual_references(spec)`` paths — user-declared
       ``visual-target`` files in ``ref/``, excluding ``proposed: true``.
    2. Accepted frames from videos with ``visual-target`` in their roles.
    3. Images downloaded from product-reference sources via
       ``_download_site_media``.
    4. Accepted frames from scraped product-reference videos.

    Deduplicates by resolved path.  Order is deterministic: sources
    (1)–(4) are appended in order, with duplicates dropped on second
    occurrence.
    """
    from duplo.spec_reader import format_visual_references

    result: list[Path] = []
    seen: set[Path] = set()

    def _add(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(path)

    # (1) visual-target reference files from SPEC.md ## References.
    if spec is not None:
        root = target_dir or Path.cwd()
        for entry in format_visual_references(spec):
            _add(root / entry.path)

    # (2) accepted frames from visual-target videos.
    for frame in visual_target_frames or []:
        _add(frame)

    # (3) images from product-reference site media.
    for img in site_images or []:
        _add(img)

    # (4) frames from scraped product-reference videos.
    for frame in site_video_frames or []:
        _add(frame)

    return result


def format_design_section(design: DesignRequirements) -> str:
    """Format design requirements as a Markdown section for PLAN.md.

    Returns an empty string if the design has no extracted data.
    """
    if not design.colors and not design.fonts and not design.layout:
        return ""

    lines = ["## Visual Design Requirements", ""]

    if design.colors:
        lines.append("### Colors")
        for key, val in design.colors.items():
            lines.append(f"- **{key}**: `{val}`")
        lines.append("")

    if design.fonts:
        lines.append("### Typography")
        for key, val in design.fonts.items():
            lines.append(f"- **{key}**: {val}")
        lines.append("")

    if design.spacing:
        lines.append("### Spacing")
        for key, val in design.spacing.items():
            lines.append(f"- **{key}**: {val}")
        lines.append("")

    if design.layout:
        lines.append("### Layout")
        for key, val in design.layout.items():
            lines.append(f"- **{key}**: {val}")
        lines.append("")

    if design.components:
        lines.append("### Component Styles")
        for comp in design.components:
            if not isinstance(comp, dict):
                continue
            name = comp.get("name", "unknown")
            style = comp.get("style", "")
            lines.append(f"- **{name}**: {style}")
        lines.append("")

    return "\n".join(lines)


def format_design_block(design: DesignRequirements) -> str:
    """Produce the markdown body for the AUTO-GENERATED block in SPEC.md.

    Same content as :func:`format_design_section` but without the
    ``## Visual Design Requirements`` heading, so the caller can wrap
    it in ``<!-- BEGIN AUTO-GENERATED --> ... <!-- END AUTO-GENERATED -->``.

    Returns an empty string when the design has no extracted data.
    """
    section = format_design_section(design)
    if not section:
        return ""
    # Strip the "## Visual Design Requirements" heading and the blank
    # line that follows it.
    lines = section.splitlines()
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("## "):
            body_start = i + 1
            break
    # Skip any leading blank lines after the heading.
    while body_start < len(lines) and not lines[body_start].strip():
        body_start += 1
    return "\n".join(lines[body_start:])
