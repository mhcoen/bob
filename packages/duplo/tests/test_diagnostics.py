"""Tests for duplo.diagnostics — append-only non-fatal failure logging."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duplo.diagnostics import (
    CATEGORIES,
    count_failures,
    print_summary,
    record_failure,
)


@pytest.fixture()
def errors_path(tmp_path: Path) -> Path:
    """Return a temporary errors.jsonl path inside a .duplo dir."""
    duplo_dir = tmp_path / ".duplo"
    duplo_dir.mkdir()
    return duplo_dir / "errors.jsonl"


class TestRecordFailure:
    """record_failure writes valid JSONL records."""

    def test_creates_file_and_writes_record(self, errors_path: Path) -> None:
        record_failure(
            "screenshotter:save_reference_screenshots",
            "screenshot",
            "Failed to capture https://example.com",
            errors_path=errors_path,
        )

        assert errors_path.exists()
        lines = errors_path.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["site"] == "screenshotter:save_reference_screenshots"
        assert entry["category"] == "screenshot"
        assert entry["message"] == "Failed to capture https://example.com"
        assert "timestamp" in entry
        assert "context" not in entry

    def test_appends_multiple_records(self, errors_path: Path) -> None:
        record_failure("a:b", "fetch", "first", errors_path=errors_path)
        record_failure("c:d", "llm", "second", errors_path=errors_path)

        lines = errors_path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["message"] == "first"
        assert json.loads(lines[1])["message"] == "second"

    def test_includes_context_dict(self, errors_path: Path) -> None:
        record_failure(
            "fetcher:fetch_site",
            "fetch",
            "timeout",
            context={"url": "https://example.com", "status": 503},
            errors_path=errors_path,
        )

        entry = json.loads(errors_path.read_text().strip())
        assert entry["context"] == {"url": "https://example.com", "status": 503}

    def test_rejects_invalid_category(self, errors_path: Path) -> None:
        with pytest.raises(ValueError, match="Invalid category"):
            record_failure("mod:fn", "bogus", "msg", errors_path=errors_path)

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "a" / "b" / "errors.jsonl"
        record_failure("x:y", "io", "test", errors_path=deep_path)
        assert deep_path.exists()


class TestCountFailures:
    """count_failures returns the number of records."""

    def test_zero_when_no_file(self, tmp_path: Path) -> None:
        assert count_failures(tmp_path / "missing.jsonl") == 0

    def test_counts_records(self, errors_path: Path) -> None:
        record_failure("a:b", "fetch", "one", errors_path=errors_path)
        record_failure("c:d", "llm", "two", errors_path=errors_path)
        record_failure("e:f", "hash", "three", errors_path=errors_path)
        assert count_failures(errors_path) == 3


class TestPrintSummary:
    """print_summary outputs a one-liner when failures exist."""

    def test_no_output_when_empty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        print_summary(tmp_path / "missing.jsonl")
        assert capsys.readouterr().out == ""

    def test_prints_count(self, errors_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        record_failure("a:b", "io", "msg1", errors_path=errors_path)
        record_failure("c:d", "io", "msg2", errors_path=errors_path)
        print_summary(errors_path)
        out = capsys.readouterr().out
        assert "2 non-fatal failures" in out
        assert "errors.jsonl" in out

    def test_singular_form(self, errors_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        record_failure("a:b", "io", "only one", errors_path=errors_path)
        print_summary(errors_path)
        out = capsys.readouterr().out
        assert "1 non-fatal failure " in out


class TestCategories:
    """Every category used in source modules is valid."""

    def test_all_expected_categories_present(self) -> None:
        expected = {"fetch", "screenshot", "llm", "hash", "io"}
        assert CATEGORIES == expected


class TestScreenshotterIntegration:
    """screenshotter.py logs failures via diagnostics."""

    def test_failed_page_logs_diagnostic(self, errors_path: Path, tmp_path: Path) -> None:
        from duplo.screenshotter import save_reference_screenshots

        mock_page = type(
            "MockPage",
            (),
            {
                "goto": lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nav failed")),
                "screenshot": lambda *a, **kw: None,
            },
        )()

        mock_browser = type(
            "MockBrowser",
            (),
            {
                "new_page": lambda self: mock_page,
                "close": lambda self: None,
            },
        )()

        class MockPlaywright:
            class chromium:
                @staticmethod
                def launch():
                    return mock_browser

        class MockContextManager:
            def __enter__(self):
                return MockPlaywright()

            def __exit__(self, *a):
                pass

        with (
            patch(
                "duplo.screenshotter.record_failure",
                side_effect=lambda *a, **kw: record_failure(
                    *a,
                    errors_path=errors_path,
                    **{k: v for k, v in kw.items() if k != "errors_path"},
                ),
            ),
            patch(
                "playwright.sync_api.sync_playwright",
                return_value=MockContextManager(),
            ),
        ):
            out_dir = tmp_path / "shots"
            save_reference_screenshots(["https://example.com/broken"], out_dir)

        assert count_failures(errors_path) == 1
        entry = json.loads(errors_path.read_text().strip())
        assert entry["category"] == "screenshot"
        assert "broken" in entry["message"]


class TestFetcherIntegration:
    """fetcher.py logs failures via diagnostics."""

    def test_fetch_site_logs_failed_request(self, errors_path: Path) -> None:
        with (
            patch(
                "duplo.fetcher.record_failure",
                side_effect=lambda *a, **kw: record_failure(
                    *a,
                    errors_path=errors_path,
                    **{k: v for k, v in kw.items() if k != "errors_path"},
                ),
            ),
            patch(
                "duplo.fetcher.httpx.get",
                side_effect=Exception("connection refused"),
            ),
        ):
            from duplo.fetcher import fetch_site

            fetch_site("https://fail.example.com")

        assert count_failures(errors_path) >= 1
        entry = json.loads(errors_path.read_text().strip().splitlines()[0])
        assert entry["category"] == "fetch"
        assert "fail.example.com" in entry["message"]


class TestInvestigatorIntegration:
    """investigator.py logs failures via diagnostics."""

    def test_cli_error_logs_diagnostic(self, errors_path: Path) -> None:
        from duplo.claude_cli import ClaudeCliError

        with (
            patch(
                "duplo.investigator.record_failure",
                side_effect=lambda *a, **kw: record_failure(
                    *a,
                    errors_path=errors_path,
                    **{k: v for k, v in kw.items() if k != "errors_path"},
                ),
            ),
            patch(
                "duplo.investigator._gather_context",
                return_value={},
            ),
            patch(
                "duplo.investigator._build_prompt",
                return_value="test prompt",
            ),
            patch(
                "duplo.investigator.query",
                side_effect=ClaudeCliError(1, "boom"),
            ),
        ):
            from duplo.investigator import investigate

            result = investigate(["test bug"])

        assert result.diagnoses == []
        assert count_failures(errors_path) == 1
        entry = json.loads(errors_path.read_text().strip())
        assert entry["category"] == "llm"


class TestSaverIntegration:
    """saver.py dedup fallbacks log failures via diagnostics."""

    def test_deduplicate_features_llm_logs(self, errors_path: Path) -> None:
        from duplo.claude_cli import ClaudeCliError

        with (
            patch(
                "duplo.saver.record_failure",
                side_effect=lambda *a, **kw: record_failure(
                    *a,
                    errors_path=errors_path,
                    **{k: v for k, v in kw.items() if k != "errors_path"},
                ),
            ),
            patch(
                "duplo.claude_cli.query",
                side_effect=ClaudeCliError(1, "llm down"),
            ),
        ):
            from duplo.saver import _deduplicate_features_llm

            result = _deduplicate_features_llm(["A"], ["B"])

        assert result == {}
        assert count_failures(errors_path) == 1
        entry = json.loads(errors_path.read_text().strip())
        assert entry["site"] == "saver:_deduplicate_features_llm"

    def test_find_duplicate_groups_logs(self, errors_path: Path) -> None:
        from duplo.claude_cli import ClaudeCliError

        with (
            patch(
                "duplo.saver.record_failure",
                side_effect=lambda *a, **kw: record_failure(
                    *a,
                    errors_path=errors_path,
                    **{k: v for k, v in kw.items() if k != "errors_path"},
                ),
            ),
            patch(
                "duplo.claude_cli.query",
                side_effect=ClaudeCliError(1, "llm down"),
            ),
        ):
            from duplo.saver import _find_duplicate_groups

            result = _find_duplicate_groups(["A", "B"])

        assert result == []
        assert count_failures(errors_path) == 1
        entry = json.loads(errors_path.read_text().strip())
        assert entry["site"] == "saver:_find_duplicate_groups"

    def test_propagate_implemented_status_logs(self, errors_path: Path) -> None:
        from duplo.claude_cli import ClaudeCliError

        features = [
            {"name": "A", "status": "implemented"},
            {"name": "B", "status": "pending"},
        ]
        with (
            patch(
                "duplo.saver.record_failure",
                side_effect=lambda *a, **kw: record_failure(
                    *a,
                    errors_path=errors_path,
                    **{k: v for k, v in kw.items() if k != "errors_path"},
                ),
            ),
            patch(
                "duplo.claude_cli.query",
                side_effect=ClaudeCliError(1, "llm down"),
            ),
        ):
            from duplo.saver import _propagate_implemented_status

            result = _propagate_implemented_status(features)

        assert result == []
        assert count_failures(errors_path) == 1
        entry = json.loads(errors_path.read_text().strip())
        assert entry["site"] == "saver:_propagate_implemented_status"


class TestVideoExtractorIntegration:
    """video_extractor.py logs hash failures via diagnostics."""

    def test_hash_failure_logs_diagnostic(self, errors_path: Path, tmp_path: Path) -> None:
        frame = tmp_path / "frame.png"
        frame.write_bytes(b"not a real image")

        with (
            patch(
                "duplo.video_extractor.record_failure",
                side_effect=lambda *a, **kw: record_failure(
                    *a,
                    errors_path=errors_path,
                    **{k: v for k, v in kw.items() if k != "errors_path"},
                ),
            ),
            patch("duplo.video_extractor._PILLOW", True),
            patch(
                "duplo.video_extractor.Image.open",
                side_effect=Exception("corrupt image"),
            ),
        ):
            from duplo.video_extractor import deduplicate_frames

            result = deduplicate_frames([frame])

        assert len(result) == 1
        assert count_failures(errors_path) == 1
        entry = json.loads(errors_path.read_text().strip())
        assert entry["category"] == "hash"
