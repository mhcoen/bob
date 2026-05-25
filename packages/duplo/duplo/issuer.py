"""Generate a structured visual issue list from screenshot comparison results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from duplo.comparator import ComparisonResult

_ISSUE_FILENAME = "ISSUES.md"


@dataclass
class VisualIssue:
    """A single visual discrepancy identified during screenshot comparison."""

    description: str
    severity: str  # "critical" | "major" | "minor"
    source: str = ""  # which screenshot/comparison the issue came from


def generate_issue_list(results: list[ComparisonResult]) -> list[VisualIssue]:
    """Extract actionable visual issues from one or more comparison results.

    Only non-similar results produce issues.  Each detail observation becomes
    a ``major`` issue.  When no detail observations are present but the result
    is non-similar, the summary is promoted to a ``critical`` issue.

    Args:
        results: One or more :class:`~duplo.comparator.ComparisonResult` objects
            produced by :func:`~duplo.comparator.compare_screenshots`.

    Returns:
        A list of :class:`VisualIssue` objects, empty when all results are similar.
    """
    issues: list[VisualIssue] = []
    for result in results:
        if result.similar:
            continue
        if result.details:
            for detail in result.details:
                issues.append(VisualIssue(description=detail, severity="major"))
        else:
            issues.append(VisualIssue(description=result.summary, severity="critical"))
    return issues


def format_issue_list(issues: list[VisualIssue]) -> str:
    """Return a Markdown-formatted visual issue list.

    Args:
        issues: Issues produced by :func:`generate_issue_list`.

    Returns:
        A Markdown string.  Returns a brief "no issues" message when *issues*
        is empty.
    """
    if not issues:
        return "# Visual Issues\n\nNo visual issues detected.\n"

    lines = ["# Visual Issues", ""]
    for severity in ("critical", "major", "minor"):
        group = [i for i in issues if i.severity == severity]
        if not group:
            continue
        lines.append(f"## {severity.capitalize()}")
        lines.append("")
        for issue in group:
            lines.append(f"- {issue.description}")
        lines.append("")

    return "\n".join(lines)


def save_issue_list(
    issues: list[VisualIssue],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Write the formatted issue list to ``ISSUES.md`` in *target_dir*.

    Args:
        issues: Issues produced by :func:`generate_issue_list`.
        target_dir: Directory to write the file to.

    Returns:
        The path of the written file.
    """
    path = (Path(target_dir) / _ISSUE_FILENAME).resolve()
    path.write_text(format_issue_list(issues), encoding="utf-8")
    return path
