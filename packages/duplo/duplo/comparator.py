"""Compare app screenshots against reference images using the Claude API."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path

import anthropic


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
    model: str = "claude-opus-4-6",
) -> ComparisonResult:
    """Compare *current* screenshot against *references* using the Claude vision API.

    Sends all images to Claude and asks it to assess how closely the current
    app matches the reference screenshots visually.

    Args:
        current: Path to the current app screenshot (PNG).
        references: Paths to reference screenshots to compare against.
        model: Claude model to use for comparison.

    Returns:
        A ComparisonResult with a similarity verdict, summary sentence, and
        bullet-point observations.
    """
    if not references:
        return ComparisonResult(similar=False, summary="No reference images provided.")

    client = anthropic.Anthropic()

    content: list[dict] = []

    for ref in references:
        data = base64.standard_b64encode(ref.read_bytes()).decode()
        content.append({"type": "text", "text": f"Reference image ({ref.name}):"})
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": data},
            }
        )

    data = base64.standard_b64encode(current.read_bytes()).decode()
    content.append({"type": "text", "text": "Current app screenshot:"})
    content.append(
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": data},
        }
    )

    content.append(
        {
            "type": "text",
            "text": (
                "Compare the current app screenshot against the reference image(s) above.\n"
                "Assess how closely the current implementation matches the reference visually.\n"
                "Respond using exactly this format:\n"
                "SIMILAR: yes or no\n"
                "SUMMARY: one sentence verdict\n"
                "DETAILS:\n"
                "- observation one\n"
                "- observation two\n"
            ),
        }
    )

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )

    return _parse_response(message.content[0].text)


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
