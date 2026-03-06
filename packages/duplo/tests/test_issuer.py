"""Tests for duplo.issuer."""

from __future__ import annotations

from pathlib import Path

from duplo.comparator import ComparisonResult
from duplo.issuer import VisualIssue, format_issue_list, generate_issue_list, save_issue_list


class TestGenerateIssueList:
    def test_empty_results(self):
        assert generate_issue_list([]) == []

    def test_similar_result_produces_no_issues(self):
        result = ComparisonResult(similar=True, summary="Looks good.", details=["Nice layout"])
        assert generate_issue_list([result]) == []

    def test_non_similar_with_details_produces_major_issues(self):
        result = ComparisonResult(
            similar=False,
            summary="Missing features.",
            details=["Sidebar absent", "Wrong font"],
        )
        issues = generate_issue_list([result])
        assert len(issues) == 2
        assert all(i.severity == "major" for i in issues)
        assert issues[0].description == "Sidebar absent"
        assert issues[1].description == "Wrong font"

    def test_non_similar_without_details_produces_critical_from_summary(self):
        result = ComparisonResult(similar=False, summary="Completely broken.")
        issues = generate_issue_list([result])
        assert len(issues) == 1
        assert issues[0].severity == "critical"
        assert issues[0].description == "Completely broken."

    def test_multiple_results_combined(self):
        results = [
            ComparisonResult(similar=True, summary="OK."),
            ComparisonResult(similar=False, summary="Bad.", details=["Issue A"]),
            ComparisonResult(similar=False, summary="Worse.", details=["Issue B", "Issue C"]),
        ]
        issues = generate_issue_list(results)
        assert len(issues) == 3
        descs = [i.description for i in issues]
        assert "Issue A" in descs
        assert "Issue B" in descs
        assert "Issue C" in descs

    def test_returns_list_of_visual_issue(self):
        result = ComparisonResult(similar=False, summary="Bad.", details=["X"])
        issues = generate_issue_list([result])
        assert all(isinstance(i, VisualIssue) for i in issues)


class TestFormatIssueList:
    def test_empty_issues_returns_no_issues_message(self):
        output = format_issue_list([])
        assert "No visual issues detected" in output

    def test_critical_section_present_when_critical_issue(self):
        issues = [VisualIssue(description="Broken layout", severity="critical")]
        output = format_issue_list(issues)
        assert "## Critical" in output
        assert "Broken layout" in output

    def test_major_section_present_when_major_issue(self):
        issues = [VisualIssue(description="Wrong colour", severity="major")]
        output = format_issue_list(issues)
        assert "## Major" in output
        assert "Wrong colour" in output

    def test_minor_section_present_when_minor_issue(self):
        issues = [VisualIssue(description="Slight misalignment", severity="minor")]
        output = format_issue_list(issues)
        assert "## Minor" in output
        assert "Slight misalignment" in output

    def test_sections_ordered_critical_major_minor(self):
        issues = [
            VisualIssue(description="Minor thing", severity="minor"),
            VisualIssue(description="Critical thing", severity="critical"),
            VisualIssue(description="Major thing", severity="major"),
        ]
        output = format_issue_list(issues)
        crit_pos = output.index("Critical")
        major_pos = output.index("Major")
        minor_pos = output.index("Minor")
        assert crit_pos < major_pos < minor_pos

    def test_issues_formatted_as_bullet_list(self):
        issues = [VisualIssue(description="Missing button", severity="major")]
        output = format_issue_list(issues)
        assert "- Missing button" in output

    def test_skips_empty_severity_sections(self):
        issues = [VisualIssue(description="Only critical", severity="critical")]
        output = format_issue_list(issues)
        assert "## Major" not in output
        assert "## Minor" not in output

    def test_starts_with_heading(self):
        output = format_issue_list([])
        assert output.startswith("# Visual Issues")


class TestSaveIssueList:
    def test_writes_issues_md(self, tmp_path: Path):
        issues = [VisualIssue(description="Bad header", severity="major")]
        path = save_issue_list(issues, target_dir=tmp_path)
        assert path.name == "ISSUES.md"
        assert path.exists()
        assert "Bad header" in path.read_text(encoding="utf-8")

    def test_returns_path_to_written_file(self, tmp_path: Path):
        path = save_issue_list([], target_dir=tmp_path)
        assert isinstance(path, Path)
        assert path.suffix == ".md"

    def test_default_target_dir_is_cwd(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        path = save_issue_list([])
        assert path.parent == tmp_path.resolve()
