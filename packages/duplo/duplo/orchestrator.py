"""Orchestration helpers for duplo's multi-step pipeline.

Helper functions used by the main orchestration flow live here
to keep main.py from growing further.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from duplo.url_canon import canonicalize_url


def _collect_cross_origin_links(
    source_url: str,
    raw_pages: dict[str, str],
) -> list[str]:
    """Extract cross-origin ``<a href>`` targets from fetched pages.

    Parses every HTML page in *raw_pages*, extracts ``<a href="...">``
    targets (ignoring ``<link>``, ``<script src>``, ``<img src>``, etc.),
    resolves each to an absolute URL against the page it appeared on,
    canonicalizes via :func:`~duplo.url_canon.canonicalize_url`, and
    returns those whose origin (scheme + host + port) differs from
    *source_url*'s.

    The returned list is deduplicated by canonical form.  A second
    round of dedup happens downstream in ``append_sources`` against
    the existing SPEC.md entries.
    """
    source_parsed = urlparse(canonicalize_url(source_url))
    source_origin = (
        source_parsed.scheme,
        source_parsed.hostname or "",
        source_parsed.port,
    )

    seen: set[str] = set()
    result: list[str] = []

    for page_url, html in raw_pages.items():
        soup = BeautifulSoup(html, "lxml")
        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()
            if not href or href.startswith(("#", "mailto:", "javascript:")):
                continue
            absolute = urljoin(page_url, href)
            # Strip fragment before canonicalization
            absolute = absolute.split("#")[0]
            if not absolute:
                continue
            canon = canonicalize_url(absolute)
            if canon in seen:
                continue

            link_parsed = urlparse(canon)
            link_origin = (
                link_parsed.scheme,
                link_parsed.hostname or "",
                link_parsed.port,
            )
            if link_origin != source_origin:
                seen.add(canon)
                result.append(canon)

    return result
