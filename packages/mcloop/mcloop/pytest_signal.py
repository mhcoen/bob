"""Parse pytest stdout/stderr plus an exit code into a structured result.

This is a pure, side-effect-free reader. It does not run pytest; it only
interprets what pytest already printed. The aim is to turn the noisy tail
of a pytest run into a small record the loop can reason about (how many
tests ran, how many failed, whether anything was collected at all)
without re-deriving the regexes at every call site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Outcome keywords pytest emits in its summary line, mapped to the
# PytestSignal field that should hold the count. Order matters only for
# readability; each is matched independently. Note that "xpassed" and
# "xfailed" must be matched as whole words so the bare "passed"/"failed"
# patterns do not steal their counts.
_OUTCOME_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("passed", "passed"),
    ("failed", "failed"),
    ("skipped", "skipped"),
    ("deselected", "deselected"),
    ("xfailed", "xfailed"),
    ("xpassed", "xpassed"),
)

# A pytest summary line always carries a duration, e.g. "in 340.94s" or
# "in 65.43s (0:01:05)". We use that as the anchor that distinguishes a
# real summary line ("2474 passed in 340.94s") from an arbitrary line
# that happens to mention "passed".
_DURATION_RE = re.compile(r"\bin \d+(?:\.\d+)?s\b")

# "collected 2474 items", "collected 1 item", or
# "collected 10 items / 10 deselected".
_COLLECTED_RE = re.compile(r"\bcollected (\d+) items?\b")


@dataclass(frozen=True)
class PytestSignal:
    """Structured outcome counts parsed from a pytest run."""

    collected: int
    passed: int
    failed: int
    skipped: int
    deselected: int
    xfailed: int
    xpassed: int
    exit_code: int


# Sentinel returned when the output carries no parseable pytest summary.
NO_SIGNAL: PytestSignal | None = None


def parse_pytest_signal(
    stdout: str,
    stderr: str = "",
    exit_code: int = 0,
) -> PytestSignal | None:
    """Parse pytest output into a :class:`PytestSignal`.

    Recognizes both the ``===``-framed summary line and the bare,
    non-``-q`` form (e.g. ``2474 passed in 340.94s``). The "no tests
    ran" line that pytest prints on a zero-collected run is treated as a
    valid summary with all outcome counts at zero.

    Returns :data:`NO_SIGNAL` (``None``) when no parseable summary line
    is present, so callers can distinguish "pytest told us nothing we
    understand" from "pytest ran and everything is zero".
    """
    text = f"{stdout}\n{stderr}"

    summary = _find_summary_line(text)
    if summary is None:
        return NO_SIGNAL

    counts = {field: 0 for _, field in _OUTCOME_KEYWORDS}
    for keyword, field in _OUTCOME_KEYWORDS:
        m = re.search(rf"(\d+) {keyword}\b", summary)
        if m:
            counts[field] = int(m.group(1))

    collected = _parse_collected(text)
    if collected is None:
        # No explicit "collected N items" line; infer from the outcome
        # counts. Deselected items were collected before being filtered
        # out, so they count toward the total.
        collected = sum(counts.values())

    return PytestSignal(
        collected=collected,
        passed=counts["passed"],
        failed=counts["failed"],
        skipped=counts["skipped"],
        deselected=counts["deselected"],
        xfailed=counts["xfailed"],
        xpassed=counts["xpassed"],
        exit_code=exit_code,
    )


def _find_summary_line(text: str) -> str | None:
    """Return the last pytest summary line, stripped of ``=`` framing.

    A summary line is one that carries a run duration and either reports
    at least one outcome keyword or is the explicit "no tests ran" line.
    The last matching line wins, since pytest prints its final summary
    last.
    """
    found: str | None = None
    for raw in text.splitlines():
        line = raw.strip().strip("=").strip()
        if not _DURATION_RE.search(line):
            continue
        has_outcome = any(kw in line for kw, _ in _OUTCOME_KEYWORDS)
        if has_outcome or "no tests ran" in line:
            found = line
    return found


def _parse_collected(text: str) -> int | None:
    """Return the collected-item count, or None if not present."""
    m = _COLLECTED_RE.search(text)
    if m:
        return int(m.group(1))
    return None
