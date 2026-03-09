"""Tests for mcloop.web_interact."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mcloop import web_interact


class TestIsPlaywrightAvailable:
    @patch("importlib.import_module")
    def test_true_when_installed(self, mock_import):
        mock_import.return_value = MagicMock()
        assert web_interact.is_playwright_available() is True
        mock_import.assert_called_with("playwright.sync_api")

    @patch("importlib.import_module", side_effect=ImportError)
    def test_false_when_not_installed(self, mock_import):
        assert web_interact.is_playwright_available() is False

    @patch("importlib.import_module", side_effect=ModuleNotFoundError)
    def test_false_on_module_not_found(self, mock_import):
        assert web_interact.is_playwright_available() is False


class TestRequirePlaywright:
    @patch("importlib.import_module", side_effect=ImportError)
    def test_raises_runtime_error(self, mock_import):
        with pytest.raises(RuntimeError, match="Playwright is not installed"):
            web_interact._require_playwright()

    @patch("importlib.import_module")
    def test_returns_module(self, mock_import):
        mock_mod = MagicMock()
        mock_import.return_value = mock_mod
        result = web_interact._require_playwright()
        assert result is mock_mod


class TestLaunchBrowser:
    @patch("mcloop.web_interact._require_playwright")
    def test_launches_headless_chromium(self, mock_req):
        mock_mod = MagicMock()
        mock_req.return_value = mock_mod
        mock_pw = MagicMock()
        mock_mod.sync_playwright.return_value.start.return_value = mock_pw
        mock_browser = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_page = MagicMock()
        mock_browser.new_page.return_value = mock_page

        browser = web_interact.launch_browser()
        mock_pw.chromium.launch.assert_called_once_with(headless=True)
        mock_browser.new_page.assert_called_once()
        assert browser._page is mock_page
        assert browser._browser is mock_browser

    @patch("mcloop.web_interact._require_playwright")
    def test_launch_non_headless(self, mock_req):
        mock_mod = MagicMock()
        mock_req.return_value = mock_mod
        mock_pw = MagicMock()
        mock_mod.sync_playwright.return_value.start.return_value = mock_pw

        web_interact.launch_browser(headless=False)
        mock_pw.chromium.launch.assert_called_once_with(headless=False)

    @patch("mcloop.web_interact._require_playwright")
    def test_raises_on_start_failure(self, mock_req):
        mock_mod = MagicMock()
        mock_req.return_value = mock_mod
        mock_mod.sync_playwright.return_value.start.side_effect = Exception("fail")

        with pytest.raises(RuntimeError, match="Failed to start Playwright"):
            web_interact.launch_browser()

    @patch("mcloop.web_interact._require_playwright")
    def test_raises_on_launch_failure_stops_pw(self, mock_req):
        mock_mod = MagicMock()
        mock_req.return_value = mock_mod
        mock_pw = MagicMock()
        mock_mod.sync_playwright.return_value.start.return_value = mock_pw
        mock_pw.chromium.launch.side_effect = Exception("no browser")

        with pytest.raises(RuntimeError, match="Failed to launch browser"):
            web_interact.launch_browser()
        mock_pw.stop.assert_called_once()


class TestBrowserNavigate:
    def test_calls_goto(self):
        page = MagicMock()
        browser = web_interact.Browser(_playwright=MagicMock(), _browser=MagicMock(), _page=page)
        browser.navigate("https://example.com")
        page.goto.assert_called_once_with("https://example.com", timeout=30000)

    def test_custom_timeout(self):
        page = MagicMock()
        browser = web_interact.Browser(_playwright=MagicMock(), _browser=MagicMock(), _page=page)
        browser.navigate("https://example.com", timeout=5000)
        page.goto.assert_called_once_with("https://example.com", timeout=5000)


class TestBrowserClick:
    def test_clicks_selector(self):
        page = MagicMock()
        browser = web_interact.Browser(_playwright=MagicMock(), _browser=MagicMock(), _page=page)
        browser.click("button#submit")
        page.click.assert_called_once_with("button#submit", timeout=5000)

    def test_custom_timeout(self):
        page = MagicMock()
        browser = web_interact.Browser(_playwright=MagicMock(), _browser=MagicMock(), _page=page)
        browser.click("a.link", timeout=10000)
        page.click.assert_called_once_with("a.link", timeout=10000)


class TestBrowserContent:
    def test_returns_html(self):
        page = MagicMock()
        page.content.return_value = "<html><body>Hello</body></html>"
        browser = web_interact.Browser(_playwright=MagicMock(), _browser=MagicMock(), _page=page)
        assert browser.content() == "<html><body>Hello</body></html>"


class TestBrowserText:
    def test_returns_body_text(self):
        page = MagicMock()
        body = MagicMock()
        body.inner_text.return_value = "Hello World"
        page.query_selector.return_value = body
        browser = web_interact.Browser(_playwright=MagicMock(), _browser=MagicMock(), _page=page)
        assert browser.text() == "Hello World"
        page.query_selector.assert_called_once_with("body")

    def test_returns_empty_when_no_body(self):
        page = MagicMock()
        page.query_selector.return_value = None
        browser = web_interact.Browser(_playwright=MagicMock(), _browser=MagicMock(), _page=page)
        assert browser.text() == ""


class TestBrowserScreenshot:
    def test_takes_screenshot(self):
        page = MagicMock()
        browser = web_interact.Browser(_playwright=MagicMock(), _browser=MagicMock(), _page=page)
        browser.screenshot("/tmp/shot.png")
        page.screenshot.assert_called_once_with(path="/tmp/shot.png", full_page=False)

    def test_full_page_screenshot(self):
        page = MagicMock()
        browser = web_interact.Browser(_playwright=MagicMock(), _browser=MagicMock(), _page=page)
        browser.screenshot("/tmp/shot.png", full_page=True)
        page.screenshot.assert_called_once_with(path="/tmp/shot.png", full_page=True)


class TestBrowserClose:
    def test_closes_browser_and_playwright(self):
        pw = MagicMock()
        br = MagicMock()
        browser = web_interact.Browser(_playwright=pw, _browser=br, _page=MagicMock())
        browser.close()
        br.close.assert_called_once()
        pw.stop.assert_called_once()

    def test_close_ignores_errors(self):
        pw = MagicMock()
        br = MagicMock()
        br.close.side_effect = Exception("already closed")
        pw.stop.side_effect = Exception("already stopped")
        browser = web_interact.Browser(_playwright=pw, _browser=br, _page=MagicMock())
        browser.close()  # Should not raise


class TestBrowserContextManager:
    def test_context_manager_closes(self):
        pw = MagicMock()
        br = MagicMock()
        browser = web_interact.Browser(_playwright=pw, _browser=br, _page=MagicMock())
        with browser:
            pass
        br.close.assert_called_once()
        pw.stop.assert_called_once()

    def test_context_manager_closes_on_exception(self):
        pw = MagicMock()
        br = MagicMock()
        browser = web_interact.Browser(_playwright=pw, _browser=br, _page=MagicMock())
        with pytest.raises(ValueError):
            with browser:
                raise ValueError("test error")
        br.close.assert_called_once()
        pw.stop.assert_called_once()
