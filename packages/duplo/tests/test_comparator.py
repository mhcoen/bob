"""Tests for duplo.comparator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from duplo.comparator import ComparisonResult, _parse_response, compare_screenshots


class TestParseResponse:
    def test_parses_similar_yes(self):
        text = "SIMILAR: yes\nSUMMARY: Looks good.\nDETAILS:\n- Colours match\n- Layout correct"
        result = _parse_response(text)
        assert result.similar is True
        assert result.summary == "Looks good."
        assert result.details == ["Colours match", "Layout correct"]

    def test_parses_similar_no(self):
        text = "SIMILAR: no\nSUMMARY: Missing sidebar.\nDETAILS:\n- Sidebar absent"
        result = _parse_response(text)
        assert result.similar is False
        assert result.summary == "Missing sidebar."
        assert result.details == ["Sidebar absent"]

    def test_case_insensitive_keys(self):
        text = "similar: Yes\nsummary: Matches.\ndetails:\n- good"
        result = _parse_response(text)
        assert result.similar is True
        assert result.summary == "Matches."

    def test_empty_details(self):
        text = "SIMILAR: yes\nSUMMARY: Perfect match."
        result = _parse_response(text)
        assert result.details == []

    def test_fallback_summary_from_first_line(self):
        text = "Everything looks great"
        result = _parse_response(text)
        assert result.summary == "Everything looks great"
        assert result.similar is False

    def test_empty_text_fallback(self):
        result = _parse_response("")
        assert result.summary == "No comparison available."
        assert result.similar is False


class TestCompareScreenshots:
    def _make_png(self, tmp_path: Path, name: str) -> Path:
        png_bytes = (
            b"\x89PNG\r\n\x1a\n"
            b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x02\x00\x00\x00\x90wS\xde"
            b"\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        path = tmp_path / name
        path.write_bytes(png_bytes)
        return path

    def test_returns_no_references_result_when_empty(self, tmp_path):
        current = self._make_png(tmp_path, "current.png")
        result = compare_screenshots(current, [])
        assert result.similar is False
        assert "No reference" in result.summary

    def test_calls_query_with_images(self, tmp_path):
        current = self._make_png(tmp_path, "current.png")
        ref = self._make_png(tmp_path, "ref.png")

        mock_text = "SIMILAR: yes\nSUMMARY: Matches well.\nDETAILS:\n- Layout correct"
        with patch("duplo.comparator.query_with_images", return_value=mock_text) as mock_q:
            result = compare_screenshots(current, [ref])

        assert result.similar is True
        assert result.summary == "Matches well."
        assert result.details == ["Layout correct"]
        mock_q.assert_called_once()

    def test_sends_reference_and_current_images(self, tmp_path):
        current = self._make_png(tmp_path, "current.png")
        ref1 = self._make_png(tmp_path, "ref1.png")
        ref2 = self._make_png(tmp_path, "ref2.png")

        with patch(
            "duplo.comparator.query_with_images",
            return_value="SIMILAR: no\nSUMMARY: Missing features.",
        ) as mock_q:
            compare_screenshots(current, [ref1, ref2])

        image_paths = mock_q.call_args[0][1]
        assert len(image_paths) == 3  # ref1, ref2, current
        assert image_paths[-1] == current

    def test_uses_specified_model(self, tmp_path):
        current = self._make_png(tmp_path, "current.png")
        ref = self._make_png(tmp_path, "ref.png")

        with patch(
            "duplo.comparator.query_with_images",
            return_value="SIMILAR: yes\nSUMMARY: Good.",
        ) as mock_q:
            compare_screenshots(current, [ref], model="haiku")

        assert mock_q.call_args.kwargs["model"] == "haiku"

    def test_returns_comparison_result_type(self, tmp_path):
        current = self._make_png(tmp_path, "current.png")
        ref = self._make_png(tmp_path, "ref.png")

        with patch(
            "duplo.comparator.query_with_images",
            return_value="SIMILAR: yes\nSUMMARY: OK.",
        ):
            result = compare_screenshots(current, [ref])

        assert isinstance(result, ComparisonResult)
