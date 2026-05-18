"""Helpers for PLAN.md fixture text used by run_loop tests."""

from __future__ import annotations

import re

_INCOMPLETE_OR_FAILED_RE = re.compile(r"^\s*- \[[ !]\] ", re.MULTILINE)
_PHASE_HEADER_RE = re.compile(r"^##\s+(?:Stage|Phase)\s+\d+\b", re.MULTILINE)
_BUGS_HEADER_RE = re.compile(r"^##\s+Bugs\s*$", re.MULTILINE | re.IGNORECASE)


def canonical_plan_text(text: str) -> str:
    """Wrap legacy phaseless task fixtures in a canonical phase.

    B3 increment 3 rejects grammar-narrowed inputs whose incomplete
    checkboxes sit outside a ``## Stage`` / ``## Phase`` header. Most
    historical run_loop tests used compact phaseless snippets because
    mcloop.checklist accepted them. The production precondition does
    not require task IDs yet (R2 is a later increment), so this helper
    adds only the phase shell needed for the R1 gate while preserving
    checklist task text byte-for-byte.
    """
    if not _INCOMPLETE_OR_FAILED_RE.search(text):
        return text
    if _PHASE_HEADER_RE.search(text) or _BUGS_HEADER_RE.search(text):
        return text

    lines = text.splitlines(keepends=True)
    first_task = next(
        (index for index, line in enumerate(lines) if re.match(r"^\s*- \[[ xX!]\] ", line)),
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
    return "".join(parts)
