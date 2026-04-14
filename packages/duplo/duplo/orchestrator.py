"""Orchestration helpers for duplo's multi-step pipeline.

Helper functions used by the main orchestration flow live here
to keep main.py from growing further.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from duplo.url_canon import canonicalize_url

if TYPE_CHECKING:
    from duplo.spec_reader import ProductSpec
    from duplo.video_extractor import ExtractionResult


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


def _accepted_frames_by_source(
    filtered_results: list[ExtractionResult],
) -> dict[Path, list[Path]]:
    """Map each video source to its accepted (post-filter) frames.

    Input MUST be post-filter: callers run ``frame_filter.apply_filter``
    on each result's frames before passing to this helper.  The helper
    does not filter — it trusts the caller.
    """
    return {r.source: r.frames for r in filtered_results}


def collect_design_input(
    spec: ProductSpec | None,
    visual_target_frames: list[Path] | None = None,
    site_images: list[Path] | None = None,
    site_video_frames: list[Path] | None = None,
    *,
    target_dir: Path | None = None,
) -> list[Path]:
    """Build the combined image list for design extraction.

    The design input is the union of:

    1. ``format_visual_references(spec)`` paths — user-declared
       ``visual-target`` files in ``ref/``, excluding ``proposed: true``.
    2. Accepted frames from videos with ``visual-target`` in their roles.
    3. Images downloaded from product-reference sources via
       ``_download_site_media``.
    4. Accepted frames from scraped product-reference videos.

    Deduplicates by resolved path and content hash.  Order is
    deterministic: sources (1)–(4) are appended in order, with
    duplicates dropped on second occurrence.
    """
    from duplo.spec_reader import format_visual_references

    result: list[Path] = []
    seen_paths: set[Path] = set()
    seen_hashes: set[str] = set()

    def _add(path: Path) -> None:
        resolved = path.resolve()
        if resolved in seen_paths:
            return
        try:
            content_hash = hashlib.sha256(resolved.read_bytes()).hexdigest()
        except OSError:
            content_hash = None
        if content_hash is not None and content_hash in seen_hashes:
            return
        seen_paths.add(resolved)
        if content_hash is not None:
            seen_hashes.add(content_hash)
        result.append(path)

    # (1) visual-target reference files from SPEC.md ## References.
    if spec is not None:
        root = target_dir or Path.cwd()
        for entry in format_visual_references(spec):
            _add(root / entry.path)

    # (2) accepted frames from visual-target videos.
    for frame in visual_target_frames or []:
        _add(frame)

    # (3) images from product-reference site media.
    for img in site_images or []:
        _add(img)

    # (4) frames from scraped product-reference videos.
    for frame in site_video_frames or []:
        _add(frame)

    return result
