"""Helpers for PLAN.md fixture text used by run_loop tests."""

from __future__ import annotations

import re

from bob_tools.planfile import parse_plan, render_plan
from bob_tools.planfile.preflight import _normalize_to_constructed

_ACTIONABLE_CHECKBOX_RE = re.compile(r"^\s*- \[[ !]\] ", re.MULTILINE)
_CHECKBOX_RE = re.compile(r"^\s*- \[[ xX!]\] ", re.MULTILINE)
_PHASE_HEADER_RE = re.compile(r"^##\s+(?:Stage|Phase)\s+\d+\b", re.MULTILINE)
_BUGS_HEADER_RE = re.compile(r"^##\s+Bugs\s*$", re.MULTILINE | re.IGNORECASE)


def assert_canonical_checkbox(content: str, marker: str, text: str) -> None:
    """Assert ``content`` has a canonical checkbox line for exact task text.

    Tolerates trailing ``<!-- key: value -->`` HTML-comment annotations
    (e.g. ``created_at`` / ``completed_at``) that the renderer appends
    after the task text, so a checked-off task carrying its checkoff
    timestamp still matches.
    """
    pattern = (
        rf"(?m)^\s*- \[{re.escape(marker)}\] T-\d{{6}}: "
        rf"{re.escape(text)}(?: <!--[^\n]*-->)*$"
    )
    assert re.search(pattern, content), (
        f"missing canonical checkbox {marker!r} {text!r} in:\n{content}"
    )


def canonical_plan_text(text: str) -> str:
    """Return fixture text in canonical phase+ID planfile form.

    B3 increment 3 rejects grammar-narrowed inputs whose incomplete
    checkboxes sit outside a ``## Stage`` / ``## Phase`` header. Most
    historical run_loop tests used compact phaseless snippets because
    mcloop.checklist accepted them. B3 R2 also rejects parsed tasks
    without ``T-NNNNNN`` ids, so this helper applies the same
    parse -> migrate -> render composition as ``bob-plan fmt``.
    """
    if not _ACTIONABLE_CHECKBOX_RE.search(text):
        if _CHECKBOX_RE.search(text) and (
            _PHASE_HEADER_RE.search(text) or _BUGS_HEADER_RE.search(text)
        ):
            return render_plan(_normalize_to_constructed(parse_plan(text)))
        return text
    if _PHASE_HEADER_RE.search(text) or _BUGS_HEADER_RE.search(text):
        return render_plan(_normalize_to_constructed(parse_plan(text)))

    lines = text.splitlines(keepends=True)
    first_task = next(
        (index for index, line in enumerate(lines) if _CHECKBOX_RE.match(line)),
        None,
    )
    if first_task is None:
        return text

    preamble = "".join(lines[:first_task])
    task_body = "".join(lines[first_task:])
    parts: list[str] = []
    if preamble:
        parts.append(preamble)
        if not preamble.endswith("\n\n"):
            parts.append("\n")
    parts.append("## Stage 1: Test\n")
    parts.append("<!-- phase_id: phase_001 -->\n")
    parts.append("\n")
    parts.append(task_body)
    return render_plan(_normalize_to_constructed(parse_plan("".join(parts))))
