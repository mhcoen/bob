"""Fetch a product URL and extract its text content, following priority links."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from duplo.diagnostics import record_failure
from duplo.doc_examples import CodeExample, extract_code_examples
from duplo.doc_tables import DocStructures, extract_doc_structures
from duplo.url_canon import canonicalize_url


@dataclass
class PageRecord:
    """Record of a single URL consulted during scraping."""

    url: str
    fetched_at: str  # ISO 8601 UTC timestamp
    content_hash: str  # SHA-256 hex digest of response body


_NOISE_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_VIDEO_EXTS = {".mp4", ".mov", ".webm"}
_MIN_IMAGE_BYTES = 10_000  # skip tiny icons/favicons
_TIMEOUT = 30.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
_HEADERS = {"User-Agent": _USER_AGENT}

# Patterns in URL path or anchor text that indicate a documentation link.
# Used to decide whether to follow cross-domain links during crawling.
_DOCS_LINK = re.compile(
    r"(docs?|documentation|wiki|guides?|handbook|reference|manual"
    r"|getting.started|quickstart|tutorial|api.ref|user.guide"
    r"|developer.guide|knowledge.base|help.center|support.portal"
    r"|read.the.docs|gitbook|howto|how.to|cookbook|examples?|learn"
    r"|instructions|usage|walkthrough)",
    re.IGNORECASE,
)

# Paths/anchors that indicate high-value technical content
_HIGH_PRIORITY = re.compile(
    r"(docs?|documentation|features?|guides?|changelog|changelogs?|"
    r"api|apis|reference|tutorial|tutorials|manual|manuals|"
    r"getting.started|quickstart|overview|faq|help)",
    re.IGNORECASE,
)

# Paths/anchors that indicate low-value pages for product understanding
_LOW_PRIORITY = re.compile(
    r"(blog|pricing|price|legal|login|signin|sign.in|signup|sign.up|"
    r"privacy|terms|contact|about|careers?|jobs?|press|news)",
    re.IGNORECASE,
)

# Hosting platform domains whose own documentation should never be
# followed as cross-domain docs links. When a product is hosted on
# one of these platforms (e.g. a GitHub repo), the platform's own
# docs/features/marketing pages are not part of the product.
# Product documentation paths on platform domains that should be allowed
# through even when the domain is in _PLATFORM_DOMAINS.
_PRODUCT_DOC_PATHS = re.compile(r"^/[^/]+/[^/]+/(wiki|docs|documentation|guide)")

_PLATFORM_DOMAINS = {
    "github.com",
    "docs.github.com",
    "gh.io",
    "gitlab.com",
    "docs.gitlab.com",
    "bitbucket.org",
    "support.atlassian.com",
    "sourceforge.net",
    "codeberg.org",
    "sr.ht",
    "npmjs.com",
    "www.npmjs.com",
    "pypi.org",
    "crates.io",
    "hub.docker.com",
    "formulae.brew.sh",
    "brew.sh",
}


def _is_html_content_type(ct: str) -> bool:
    """Return True if the Content-Type header indicates HTML content."""
    ct_lower = ct.lower().split(";")[0].strip()
    return ct_lower in ("text/html", "application/xhtml+xml")


def _is_platform_domain(url: str) -> bool:
    """Return True if *url* points to a hosting platform's own pages.

    Returns False for product-specific documentation hosted on a
    platform (e.g. ``github.com/org/repo/wiki``) since those are
    legitimate product docs, not platform marketing pages.
    """
    parsed = urlparse(url)
    domain = parsed.netloc
    path = parsed.path.lower()

    is_platform = False
    for plat in _PLATFORM_DOMAINS:
        if domain == plat or domain.endswith("." + plat):
            is_platform = True
            break
    if not is_platform:
        return False

    # Allow product documentation paths on platform domains.
    # e.g. github.com/org/repo/wiki, gitlab.com/org/repo/-/wikis
    if _PRODUCT_DOC_PATHS.search(path):
        return False

    return True


def is_docs_link(url: str, anchor: str) -> bool:
    """Return True if the link likely points to documentation.

    Checks the URL path and anchor text for documentation-related words
    rather than matching a hardcoded list of hosting platforms.
    """
    path = urlparse(url).path
    text = f"{path} {anchor}"
    return bool(_DOCS_LINK.search(text))


def detect_docs_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Extract documentation links from *html*, including cross-domain ones.

    Returns (absolute_url, anchor_text) pairs for links whose text or URL
    path indicates they point to documentation.
    """
    all_links = extract_links(html, base_url)
    return [(url, anchor) for url, anchor in all_links if is_docs_link(url, anchor)]


def score_link(url: str, anchor: str) -> int:
    """Return a priority score for a link.

    Returns 1 for high-priority (docs, features, guides, changelog, API),
    -1 for low-priority (marketing, blog, pricing, legal, login),
    and 0 for neutral links.
    """
    path = urlparse(url).path
    text = f"{path} {anchor}"
    if _LOW_PRIORITY.search(text):
        return -1
    if _HIGH_PRIORITY.search(text):
        return 1
    return 0


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return (absolute_url, anchor_text) pairs extracted from *html*.

    Relative URLs are resolved against *base_url*. Fragment-only links,
    mailto: and javascript: hrefs are excluded.
    """
    soup = BeautifulSoup(html, "lxml")
    links: list[tuple[str, str]] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(base_url, href).split("#")[0]
        anchor = tag.get_text(separator=" ").strip()
        links.append((absolute, anchor))
    return links


def _same_origin(url_a: str, url_b: str) -> bool:
    """Return True if *url_a* and *url_b* share scheme, host, and port."""
    a = urlparse(url_a)
    b = urlparse(url_b)
    return (
        a.scheme.lower() == b.scheme.lower()
        and (a.hostname or "").lower() == (b.hostname or "").lower()
        and a.port == b.port
    )


def fetch_site(
    url: str,
    *,
    scrape_depth: Literal["deep", "shallow", "none"] = "deep",
) -> tuple[str, list[CodeExample], DocStructures, list[PageRecord], dict[str, str]]:
    """Fetch *url* and return extracted artifacts plus raw HTML.

    *scrape_depth* controls how aggressively the fetcher follows links:

    * ``"deep"`` — fetch the entry URL and follow **same-origin** links
      (same scheme + host + port).  High-priority links (docs, features,
      guides, changelog, API references) are visited before neutral links.
      Low-priority links (marketing, blog, pricing, legal, login) are
      skipped.  Cross-origin links are **not** followed; they are
      recorded for the orchestrator to discover separately.
    * ``"shallow"`` — fetch only the entry URL, no link-following.
    * ``"none"`` — don't fetch anything; return empty results.

    Returns a tuple of
    ``(text, code_examples, doc_structures, page_records, raw_pages)``
    where *text* is concatenated text content from all visited pages,
    *code_examples* is a list of :class:`CodeExample` objects,
    *doc_structures* is a :class:`DocStructures` with feature tables,
    operation lists, unit lists, and function references,
    *page_records* is a list of :class:`PageRecord` with URL, timestamp,
    and content hash for every successfully fetched page, and
    *raw_pages* is a dict mapping each fetched URL to its raw HTML
    content so re-runs can diff against what changed on the product
    site.  For every URL in *raw_pages* there is a corresponding
    :class:`PageRecord`; failed fetches appear in neither.
    """
    empty: tuple[str, list[CodeExample], DocStructures, list[PageRecord], dict[str, str]] = (
        "",
        [],
        DocStructures(),
        [],
        {},
    )

    if scrape_depth == "none":
        return empty

    # --- shallow: single page, no link-following ---
    if scrape_depth == "shallow":
        try:
            resp = httpx.get(
                url,
                follow_redirects=True,
                timeout=_TIMEOUT,
                headers=_HEADERS,
            )
            resp.raise_for_status()
        except Exception as exc:
            record_failure(
                "fetcher:fetch_site",
                "fetch",
                f"Failed to fetch {url}: {exc}",
                context={"url": url},
            )
            return empty

        ct = resp.headers.get("content-type", "")
        if not _is_html_content_type(ct):
            record_failure(
                "fetcher:fetch_site",
                "fetch",
                f"Non-HTML content-type for {url}: {ct}",
                context={"url": url, "content_type": ct},
            )
            return empty

        try:
            html = resp.text
        except Exception as exc:
            record_failure(
                "fetcher:fetch_site",
                "fetch",
                f"Decode failure for {url}: {exc}",
                context={"url": url},
            )
            return empty

        canon = canonicalize_url(str(resp.url))
        record = PageRecord(
            url=canon,
            fetched_at=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(html.encode()).hexdigest(),
        )
        text = extract_text(html)
        text_out = f"=== {canon} ===\n{text}" if text else ""
        examples = extract_code_examples(html, url)
        structures = extract_doc_structures(html, url)
        return text_out, examples, structures, [record], {canon: html}

    # --- deep: same-origin BFS crawl ---
    visited: set[str] = set()
    queued: set[str] = set()
    results: list[str] = []
    all_examples: list[CodeExample] = []
    all_structures = DocStructures()
    all_records: list[PageRecord] = []
    raw_pages: dict[str, str] = {}

    seed_norm = canonicalize_url(url)
    queue: list[tuple[int, str]] = [(2, url)]  # seed at highest priority
    queued.add(seed_norm)

    while queue:
        queue.sort(key=lambda x: x[0])
        _, current_url = queue.pop()

        norm = canonicalize_url(current_url)
        if norm in visited:
            continue

        visited.add(norm)

        try:
            resp = httpx.get(
                current_url,
                follow_redirects=True,
                timeout=_TIMEOUT,
                headers=_HEADERS,
            )
            resp.raise_for_status()
        except Exception as exc:
            record_failure(
                "fetcher:fetch_site",
                "fetch",
                f"Failed to fetch {current_url}: {exc}",
                context={"url": current_url},
            )
            continue

        ct = resp.headers.get("content-type", "")
        if not _is_html_content_type(ct):
            record_failure(
                "fetcher:fetch_site",
                "fetch",
                f"Non-HTML content-type for {current_url}: {ct}",
                context={"url": current_url, "content_type": ct},
            )
            continue

        try:
            html = resp.text
        except Exception as exc:
            record_failure(
                "fetcher:fetch_site",
                "fetch",
                f"Decode failure for {current_url}: {exc}",
                context={"url": current_url},
            )
            continue

        final_norm = canonicalize_url(str(resp.url))
        if final_norm != norm:
            visited.add(final_norm)

        record = PageRecord(
            url=final_norm,
            fetched_at=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(html.encode()).hexdigest(),
        )
        all_records.append(record)
        raw_pages[final_norm] = html

        text = extract_text(html)
        if text:
            results.append(f"=== {current_url} ===\n{text}")

        page_examples = extract_code_examples(html, current_url)
        all_examples.extend(page_examples)

        page_structures = extract_doc_structures(html, current_url)
        all_structures.merge(page_structures)

        for link_url, anchor in extract_links(html, current_url):
            link_norm = canonicalize_url(link_url)
            if link_norm in visited or link_norm in queued:
                continue
            # Only follow same-origin links; cross-origin links are
            # recorded by the orchestrator, not fetched here.
            if not _same_origin(link_url, url):
                continue
            link_score = score_link(link_url, anchor)
            if link_score >= 0:
                queue.append((link_score, link_url))
                queued.add(link_norm)

    return "\n\n".join(results), all_examples, all_structures, all_records, raw_pages


def fetch_text(url: str) -> str:
    """Fetch *url* and return its visible text content."""
    response = httpx.get(url, follow_redirects=True, timeout=_TIMEOUT, headers=_HEADERS)
    response.raise_for_status()
    return extract_text(response.text)


def extract_text(html: str) -> str:
    """Parse *html* and return visible text with noise tags removed."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(_NOISE_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def extract_media_urls(html: str, base_url: str) -> tuple[list[str], list[str]]:
    """Extract image and video URLs from *html*.

    Looks for ``<img>``, ``<video>``, ``<source>``, and ``<picture>``
    tags. Returns ``(image_urls, video_urls)`` with absolute URLs.
    Deduplicates within each list.
    """
    soup = BeautifulSoup(html, "lxml")
    images: list[str] = []
    videos: list[str] = []
    seen: set[str] = set()

    def _add(url: str, target: list[str]) -> None:
        absolute = urljoin(base_url, url).split("?")[0].split("#")[0]
        if absolute not in seen:
            seen.add(absolute)
            target.append(absolute)

    # Video sources
    for tag in soup.find_all("video"):
        src = tag.get("src", "")
        if src:
            _add(src, videos)
        poster = tag.get("poster", "")
        if poster:
            _add(poster, images)
    for tag in soup.find_all("source"):
        src = tag.get("src", "")
        if not src:
            continue
        media_type = tag.get("type", "")
        if "video" in media_type or any(src.lower().endswith(e) for e in _VIDEO_EXTS):
            _add(src, videos)

    # Images (skip tiny icons, data URIs, SVGs)
    for tag in soup.find_all("img"):
        src = tag.get("src", "") or tag.get("data-src", "")
        if not src or src.startswith("data:"):
            continue
        if src.lower().endswith(".svg"):
            continue
        _add(src, images)
    for tag in soup.find_all("picture"):
        for source in tag.find_all("source"):
            srcset = source.get("srcset", "")
            if srcset:
                # Take the first URL from srcset
                first = srcset.split(",")[0].strip().split()[0]
                if not first.lower().endswith(".svg"):
                    _add(first, images)

    return images, videos


def download_media(
    image_urls: list[str],
    video_urls: list[str],
    output_dir: Path,
) -> tuple[list[Path], list[Path]]:
    """Download images and videos to *output_dir*.

    Skips files that already exist locally. Skips images smaller than
    ``_MIN_IMAGE_BYTES``. Returns ``(new_images, new_videos)`` containing
    only files that were actually downloaded (not cached).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    new_images: list[Path] = []
    new_videos: list[Path] = []

    for url in video_urls:
        path, is_new = _download_file(url, output_dir)
        if path and is_new:
            new_videos.append(path)

    for url in image_urls:
        path, is_new = _download_file(url, output_dir)
        if path and path.stat().st_size >= _MIN_IMAGE_BYTES:
            if is_new:
                new_images.append(path)
        elif path and is_new:
            # Too small, likely icon
            path.unlink(missing_ok=True)

    return new_images, new_videos


def _download_file(url: str, output_dir: Path) -> tuple[Path | None, bool]:
    """Download a single file.

    Returns ``(path, is_new)`` where *is_new* is False if the file
    already existed locally (cache hit).
    """
    parsed = urlparse(url)
    filename = Path(parsed.path).name
    if not filename:
        return None, False
    # Avoid collisions by prefixing with domain
    domain = parsed.netloc.replace(".", "_")
    dest = output_dir / f"{domain}_{filename}"
    if dest.exists():
        return dest, False
    try:
        with httpx.stream(
            "GET", url, follow_redirects=True, timeout=60.0, headers=_HEADERS
        ) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        return dest, True
    except Exception as exc:
        record_failure(
            "fetcher:_download_file",
            "fetch",
            f"Failed to download {url}: {exc}",
            context={"url": url},
        )
        dest.unlink(missing_ok=True)
        return None, False
