"""Web app interaction via Playwright (optional dependency)."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field


def is_playwright_available() -> bool:
    """Check if Playwright is installed and importable."""
    try:
        importlib.import_module("playwright.sync_api")
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def _require_playwright():
    """Import and return the playwright sync_api module.

    Raises RuntimeError if Playwright is not installed.
    """
    try:
        return importlib.import_module("playwright.sync_api")
    except (ImportError, ModuleNotFoundError):
        raise RuntimeError(
            "Playwright is not installed. "
            "Install it with: pip install playwright && playwright install"
        )


@dataclass
class Browser:
    """Wrapper around a Playwright browser instance."""

    _playwright: object = field(repr=False)
    _browser: object = field(repr=False)
    _page: object = field(repr=False)

    def navigate(self, url: str, timeout: float = 30000) -> None:
        """Navigate to a URL.

        Args:
            url: The URL to navigate to.
            timeout: Navigation timeout in milliseconds.
        """
        self._page.goto(url, timeout=timeout)

    def click(self, selector: str, timeout: float = 5000) -> None:
        """Click an element matching a CSS selector.

        Args:
            selector: CSS selector for the element to click.
            timeout: Max milliseconds to wait for the element.
        """
        self._page.click(selector, timeout=timeout)

    def content(self) -> str:
        """Read the current page content as HTML."""
        return self._page.content()

    def text(self) -> str:
        """Read the visible text content of the page body."""
        body = self._page.query_selector("body")
        if body is None:
            return ""
        return body.inner_text()

    def screenshot(self, path: str, full_page: bool = False) -> None:
        """Take a screenshot of the current page.

        Args:
            path: File path to save the screenshot (PNG).
            full_page: If True, capture the full scrollable page.
        """
        self._page.screenshot(path=path, full_page=full_page)

    def close(self) -> None:
        """Close the browser and stop Playwright."""
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._playwright.stop()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def launch_browser(headless: bool = True) -> Browser:
    """Launch a headless Chromium browser via Playwright.

    Args:
        headless: Run in headless mode (default True).

    Returns:
        A Browser instance with a blank page ready for navigation.

    Raises:
        RuntimeError: If Playwright is not installed or browser
            launch fails.
    """
    pw_mod = _require_playwright()
    try:
        pw = pw_mod.sync_playwright().start()
    except Exception as exc:
        raise RuntimeError(f"Failed to start Playwright: {exc}")
    try:
        browser = pw.chromium.launch(headless=headless)
    except Exception as exc:
        pw.stop()
        raise RuntimeError(f"Failed to launch browser: {exc}")
    page = browser.new_page()
    return Browser(_playwright=pw, _browser=browser, _page=page)
