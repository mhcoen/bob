"""Capture reference screenshots from a product website."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from duplo.diagnostics import record_failure

_SECTION_HEADER_RE = re.compile(r"^=== (.+?) ===$", re.MULTILINE)


def _url_to_filename(url: str) -> str:
    """Convert a URL to a safe PNG filename.

    >>> _url_to_filename("https://example.com/")
    'example_com_index.png'
    >>> _url_to_filename("https://example.com/docs/api")
    'example_com_docs_api.png'
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/").replace("/", "_") or "index"
    stem = f"{parsed.netloc}_{path}".replace(".", "_").replace("-", "_")
    return f"{stem}.png"


def save_reference_screenshots(urls: list[str], output_dir: Path) -> list[Path]:
    """Navigate to each URL with a headless browser and save a full-page screenshot.

    Screenshots are saved as PNG files under *output_dir*, which is created if
    it does not exist.  Pages that fail to load are silently skipped.

    Returns a list of paths for successfully saved screenshots.
    """
    from playwright.sync_api import sync_playwright

    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()

            for url in urls:
                dest = output_dir / _url_to_filename(url)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    page.screenshot(path=str(dest), full_page=True)
                    saved.append(dest)
                except Exception as exc:
                    record_failure(
                        "screenshotter:save_reference_screenshots",
                        "screenshot",
                        f"Failed to capture {url}: {exc}",
                        context={"url": url},
                    )
        finally:
            browser.close()

    return saved


def map_screenshots_to_features(
    scraped_text: str,
    feature_names: list[str],
    output_dir: Path,
) -> dict[str, list[str]]:
    """Return a mapping of screenshot filename to feature names.

    Splits *scraped_text* into per-URL sections (delimited by ``=== URL ===``
    headers) and matches each section against *feature_names* using
    case-insensitive substring search.  Only screenshots that exist in
    *output_dir* are included in the result.

    Args:
        scraped_text: Full text returned by :func:`duplo.fetcher.fetch_site`.
        feature_names: List of feature name strings to match against.
        output_dir: Directory where reference screenshots were saved.

    Returns:
        Dict mapping screenshot filename (basename only) to a list of
        feature names whose names appear in that page's text.
        Screenshots with no matched features are omitted.
    """
    headers = list(_SECTION_HEADER_RE.finditer(scraped_text))
    mapping: dict[str, list[str]] = {}

    for i, match in enumerate(headers):
        url = match.group(1)
        start = match.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(scraped_text)
        section_text = scraped_text[start:end].lower()

        filename = _url_to_filename(url)
        if not (output_dir / filename).exists():
            continue

        matched = [name for name in feature_names if name.lower() in section_text]
        if matched:
            mapping[filename] = matched

    return mapping
