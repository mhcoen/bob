"""Tests for duplo.screenshotter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from duplo.screenshotter import (
    _url_to_filename,
    map_screenshots_to_features,
    save_reference_screenshots,
)


class TestUrlToFilename:
    def test_root_url_becomes_index(self):
        assert _url_to_filename("https://example.com/") == "example_com_index.png"

    def test_no_path_becomes_index(self):
        assert _url_to_filename("https://example.com") == "example_com_index.png"

    def test_single_path_segment(self):
        assert _url_to_filename("https://example.com/docs") == "example_com_docs.png"

    def test_nested_path_uses_underscores(self):
        assert _url_to_filename("https://example.com/docs/api") == "example_com_docs_api.png"

    def test_dots_in_domain_become_underscores(self):
        name = _url_to_filename("https://sub.example.com/page")
        assert "." not in name.replace(".png", "")

    def test_hyphens_become_underscores(self):
        name = _url_to_filename("https://example.com/getting-started")
        assert "-" not in name

    def test_always_ends_with_png(self):
        assert _url_to_filename("https://example.com/foo").endswith(".png")


class TestSaveReferenceScreenshots:
    def _make_playwright_mocks(self):
        mock_page = MagicMock()
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page

        mock_chromium = MagicMock()
        mock_chromium.launch.return_value = mock_browser

        mock_pw = MagicMock()
        mock_pw.chromium = mock_chromium
        mock_pw.__enter__ = MagicMock(return_value=mock_pw)
        mock_pw.__exit__ = MagicMock(return_value=False)

        return mock_pw, mock_browser, mock_page

    def test_creates_output_dir(self, tmp_path):
        output_dir = tmp_path / "screenshots"
        mock_pw, mock_browser, mock_page = self._make_playwright_mocks()

        with patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            save_reference_screenshots(["https://example.com/"], output_dir)

        assert output_dir.is_dir()

    def test_returns_saved_paths(self, tmp_path):
        mock_pw, mock_browser, mock_page = self._make_playwright_mocks()

        with patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            result = save_reference_screenshots(
                ["https://example.com/", "https://example.com/docs"],
                tmp_path,
            )

        assert len(result) == 2
        assert all(isinstance(p, Path) for p in result)

    def test_calls_goto_and_screenshot_for_each_url(self, tmp_path):
        mock_pw, mock_browser, mock_page = self._make_playwright_mocks()
        urls = ["https://example.com/", "https://example.com/docs"]

        with patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            save_reference_screenshots(urls, tmp_path)

        assert mock_page.goto.call_count == 2
        assert mock_page.screenshot.call_count == 2

    def test_goto_uses_domcontentloaded(self, tmp_path):
        mock_pw, mock_browser, mock_page = self._make_playwright_mocks()

        with patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            save_reference_screenshots(["https://example.com/"], tmp_path)

        call_kwargs = mock_page.goto.call_args[1]
        assert call_kwargs.get("wait_until") == "domcontentloaded"

    def test_screenshot_uses_full_page(self, tmp_path):
        mock_pw, mock_browser, mock_page = self._make_playwright_mocks()

        with patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            save_reference_screenshots(["https://example.com/"], tmp_path)

        call_kwargs = mock_page.screenshot.call_args[1]
        assert call_kwargs.get("full_page") is True

    def test_skips_failed_pages_and_continues(self, tmp_path):
        mock_pw, mock_browser, mock_page = self._make_playwright_mocks()

        def fail_first(url, **kwargs):
            if "fail" in url:
                raise Exception("timeout")

        mock_page.goto.side_effect = fail_first
        urls = ["https://example.com/fail", "https://example.com/ok"]

        with patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            result = save_reference_screenshots(urls, tmp_path)

        # only the ok page should be saved
        assert len(result) == 1

    def test_empty_url_list_returns_empty(self, tmp_path):
        mock_pw, mock_browser, mock_page = self._make_playwright_mocks()

        with patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            result = save_reference_screenshots([], tmp_path)

        assert result == []
        mock_page.goto.assert_not_called()

    def test_browser_closed_after_run(self, tmp_path):
        mock_pw, mock_browser, mock_page = self._make_playwright_mocks()

        with patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            save_reference_screenshots(["https://example.com/"], tmp_path)

        mock_browser.close.assert_called_once()

    def test_output_filenames_derived_from_urls(self, tmp_path):
        mock_pw, mock_browser, mock_page = self._make_playwright_mocks()

        with patch("playwright.sync_api.sync_playwright", return_value=mock_pw):
            result = save_reference_screenshots(["https://example.com/docs"], tmp_path)

        assert result[0].name == "example_com_docs.png"


class TestMapScreenshotsToFeatures:
    def _make_scraped(self, entries: list[tuple[str, str]]) -> str:
        """Build a fake fetch_site output from (url, text) pairs."""
        sections = [f"=== {url} ===\n{text}" for url, text in entries]
        return "\n\n".join(sections)

    def test_returns_empty_when_no_sections(self, tmp_path):
        result = map_screenshots_to_features("", ["Search"], tmp_path)
        assert result == {}

    def test_returns_empty_when_screenshot_missing(self, tmp_path):
        text = self._make_scraped([("https://example.com/", "full-text search support")])
        result = map_screenshots_to_features(text, ["Search"], tmp_path)
        assert result == {}

    def test_matches_feature_name_in_section(self, tmp_path):
        text = self._make_scraped([("https://example.com/", "full-text search support")])
        (tmp_path / "example_com_index.png").touch()
        result = map_screenshots_to_features(text, ["Search"], tmp_path)
        assert result == {"example_com_index.png": ["Search"]}

    def test_case_insensitive_match(self, tmp_path):
        text = self._make_scraped([("https://example.com/", "REST API endpoints available")])
        (tmp_path / "example_com_index.png").touch()
        result = map_screenshots_to_features(text, ["rest api"], tmp_path)
        assert "example_com_index.png" in result

    def test_multiple_features_matched_in_one_section(self, tmp_path):
        text = self._make_scraped(
            [("https://example.com/features", "search and rest api and oauth")]
        )
        (tmp_path / "example_com_features.png").touch()
        result = map_screenshots_to_features(text, ["Search", "REST API", "OAuth"], tmp_path)
        assert set(result["example_com_features.png"]) == {"Search", "REST API", "OAuth"}

    def test_multiple_sections_mapped_separately(self, tmp_path):
        text = self._make_scraped(
            [
                ("https://example.com/", "search functionality"),
                ("https://example.com/api", "rest api documentation"),
            ]
        )
        (tmp_path / "example_com_index.png").touch()
        (tmp_path / "example_com_api.png").touch()
        result = map_screenshots_to_features(text, ["Search", "REST API"], tmp_path)
        assert result["example_com_index.png"] == ["Search"]
        assert result["example_com_api.png"] == ["REST API"]

    def test_section_with_no_matching_features_omitted(self, tmp_path):
        text = self._make_scraped([("https://example.com/", "pricing plans available")])
        (tmp_path / "example_com_index.png").touch()
        result = map_screenshots_to_features(text, ["Search", "REST API"], tmp_path)
        assert result == {}

    def test_empty_feature_names_list(self, tmp_path):
        text = self._make_scraped([("https://example.com/", "some content")])
        (tmp_path / "example_com_index.png").touch()
        result = map_screenshots_to_features(text, [], tmp_path)
        assert result == {}

    def test_only_existing_screenshots_included(self, tmp_path):
        text = self._make_scraped(
            [
                ("https://example.com/", "search content"),
                ("https://example.com/docs", "search docs"),
            ]
        )
        # Only create one of the two screenshots
        (tmp_path / "example_com_index.png").touch()
        result = map_screenshots_to_features(text, ["Search"], tmp_path)
        assert "example_com_index.png" in result
        assert "example_com_docs.png" not in result
