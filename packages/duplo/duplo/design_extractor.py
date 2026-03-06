"""Extract visual design details from images using Claude Vision."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

_MODEL = "claude-haiku-4-5-20251001"

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


def _image_media_type(path: Path) -> str:
    """Return the MIME type for an image file based on extension."""
    ext = path.suffix.lower()
    types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    return types.get(ext, "image/png")


def extract_design(
    images: list[Path],
    *,
    client: anthropic.Anthropic | None = None,
) -> DesignRequirements:
    """Analyse reference images and extract visual design requirements.

    Sends up to ``_MAX_IMAGES`` images to Claude Vision and returns
    structured design details (colors, fonts, spacing, layout, components).

    Args:
        images: Paths to reference image files (PNG, JPG, GIF, WEBP).
        client: Optional Anthropic client; a default client is created
            if omitted.

    Returns:
        A :class:`DesignRequirements` with extracted visual details.
        Returns an empty instance if no images are provided or extraction
        fails.
    """
    if not images:
        return DesignRequirements()

    if client is None:
        client = anthropic.Anthropic()

    to_send = images[:_MAX_IMAGES]

    content: list[dict] = []
    source_names: list[str] = []

    for img in to_send:
        data = base64.standard_b64encode(img.read_bytes()).decode()
        media = _image_media_type(img)
        content.append({"type": "text", "text": f"Screenshot ({img.name}):"})
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": media, "data": data},
            }
        )
        source_names.append(img.name)

    content.append(
        {
            "type": "text",
            "text": (
                "Analyse these screenshot(s) and extract the visual design details.\n"
                "Return ONLY the JSON object as described."
            ),
        }
    )

    message = client.messages.create(
        model=_MODEL,
        max_tokens=2048,
        system=_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )

    raw = message.content[0].text.strip()
    result = _parse_design(raw)
    result.source_images = source_names
    return result


def _parse_design(raw: str) -> DesignRequirements:
    """Parse Claude's JSON response into a DesignRequirements.

    Tolerates markdown code fences wrapping the JSON.
    Returns an empty DesignRequirements if parsing fails.
    """
    text = raw
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

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
