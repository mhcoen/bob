"""Compare app screenshots against reference images using claude -p."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from duplo.claude_cli import query_with_images


@dataclass
class ComparisonResult:
    """Result of comparing a current screenshot against reference images."""

    similar: bool
    summary: str
    details: list[str] = field(default_factory=list)


def compare_screenshots(
    current: Path,
    references: list[Path],
    *,
    model: str = "opus",
) -> ComparisonResult:
    """Compare *current* screenshot against *references* using ``claude -p``.

    Sends all images to Claude and asks it to assess how closely the current
    app matches the reference screenshots visually.

    Args:
        current: Path to the current app screenshot (PNG).
        references: Paths to reference screenshots to compare against.
        model: Claude model alias or full name for comparison.

    Returns:
        A ComparisonResult with a similarity verdict, summary sentence, and
        bullet-point observations.
    """
    if not references:
        return ComparisonResult(similar=False, summary="No reference images provided.")

    all_images = list(references) + [current]

    prompt = (
        "The first image(s) are reference screenshots. "
        "The last image is the current app screenshot.\n"
        "Compare the current app screenshot against the reference image(s).\n"
        "Assess how closely the current implementation matches the reference visually.\n"
        "Respond using exactly this format:\n"
        "SIMILAR: yes or no\n"
        "SUMMARY: one sentence verdict\n"
        "DETAILS:\n"
        "- observation one\n"
        "- observation two\n"
    )

    raw = query_with_images(prompt, all_images, model=model)
    return _parse_response(raw)


def _parse_response(text: str) -> ComparisonResult:
    """Parse Claude's structured comparison response into a ComparisonResult."""
    similar = False
    summary = ""
    details: list[str] = []
    in_details = False

    for line in text.strip().splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("SIMILAR:"):
            val = stripped.split(":", 1)[1].strip().lower()
            similar = val in ("yes", "true", "1")
            in_details = False
        elif upper.startswith("SUMMARY:"):
            summary = stripped.split(":", 1)[1].strip()
            in_details = False
        elif upper.startswith("DETAILS:"):
            in_details = True
        elif in_details and stripped.startswith("-"):
            details.append(stripped[1:].strip())

    if not summary:
        first_line = text.strip().splitlines()[0] if text.strip() else ""
        summary = first_line or "No comparison available."

    return ComparisonResult(similar=similar, summary=summary, details=details)
