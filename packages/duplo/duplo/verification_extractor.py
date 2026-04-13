"""Extract functional verification cases from video frame descriptions.

The frame describer already produces structured descriptions of what each
frame shows, including expressions and their results (e.g. ``"'Price: $7 × 4'
with the result '$28'"``).  This module uses an LLM to extract those
expression/result pairs as structured test cases that can be included in
PLAN.md as verification tasks.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from duplo.claude_cli import query
from duplo.parsing import strip_fences

_SYSTEM = """\
You are a test-case extractor. Given descriptions of application UI frames,
extract every expression/input and its displayed result as a test case.

Each frame description may mention one or more expressions shown in the app.
For each expression, extract:
  "input"    – the expression the user typed (e.g. "Price: $7 × 4")
  "expected" – the result the app displayed (e.g. "$28")
  "frame"    – the filename of the frame this came from

Only extract expression/result pairs that are EXPLICITLY described in the
frame detail text. Do NOT invent or guess results.

Return ONLY a JSON array of objects. Return [] if no expression/result
pairs can be found.

Example output:
[
  {"input": "Price: $7 × 4", "expected": "$28", "frame": "demo_0003.png"},
  {"input": "Fee: 4 GBP in Euro", "expected": "5.71 EUR", "frame": "demo_0005.png"}
]
"""


@dataclass
class VerificationCase:
    """A single input→expected_output test case from a video frame."""

    input: str
    expected: str
    frame: str


def extract_verification_cases(
    frame_descriptions: list[dict],
) -> list[VerificationCase]:
    """Extract functional verification cases from frame descriptions.

    Sends the frame descriptions to Claude to identify expression/result
    pairs embedded in the natural-language ``detail`` field.

    Args:
        frame_descriptions: List of dicts with ``filename``, ``state``,
            and ``detail`` keys (as stored in ``duplo.json``).

    Returns:
        List of :class:`VerificationCase` objects. Empty if none found
        or if the LLM call fails.
    """
    if not frame_descriptions:
        return []

    # Filter to frames that likely contain expressions (not settings, menus, etc.).
    relevant = [fd for fd in frame_descriptions if fd.get("detail") and fd.get("filename")]
    if not relevant:
        return []

    descriptions_text = "\n".join(
        f"Frame: {fd['filename']}\nState: {fd.get('state', 'unknown')}\nDetail: {fd['detail']}\n"
        for fd in relevant
    )

    prompt = (
        "Extract all expression/result test cases from these "
        "frame descriptions:\n\n"
        f"{descriptions_text}"
    )
    raw = query(prompt, system=_SYSTEM)
    return _parse_cases(raw)


def _parse_cases(raw: str) -> list[VerificationCase]:
    """Parse a JSON array of verification cases from *raw*.

    Tolerates markdown code fences. Returns an empty list on failure.
    """
    text = strip_fences(raw)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    cases: list[VerificationCase] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        inp = str(item.get("input", "")).strip()
        expected = str(item.get("expected", "")).strip()
        frame = str(item.get("frame", "")).strip()
        if inp and expected:
            cases.append(VerificationCase(input=inp, expected=expected, frame=frame))

    return cases


def format_verification_tasks(cases: list[VerificationCase]) -> str:
    """Render verification cases as PLAN.md checklist items.

    Returns a Markdown section with one task per case, suitable for
    appending to a phase plan.
    """
    if not cases:
        return ""

    lines: list[str] = []
    lines.append("")
    lines.append("## Functional verification from demo video")
    lines.append("")
    lines.append("Type each expression and verify the result matches the product's demo.")
    lines.append("")
    for case in cases:
        lines.append(f"- [ ] Verify: type `{case.input}`, expect result `{case.expected}`")

    lines.append("")
    return "\n".join(lines)


def load_frame_descriptions(
    *,
    target_dir: str | None = None,
) -> list[dict]:
    """Load frame descriptions from duplo.json.

    Args:
        target_dir: Directory containing ``.duplo/duplo.json``.
            Defaults to the current directory.

    Returns:
        List of frame description dicts, or empty list.
    """
    from pathlib import Path

    base = Path(target_dir) if target_dir else Path(".")
    duplo_path = base / ".duplo" / "duplo.json"
    if not duplo_path.exists():
        return []
    try:
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data.get("frame_descriptions", [])
