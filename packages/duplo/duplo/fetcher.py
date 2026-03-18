"""Fetch a product URL and extract its text content, following priority links."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from duplo.doc_examples import CodeExample, extract_code_examples
from duplo.doc_tables import DocStructures, extract_doc_structures


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
    _PRODUCT_DOC_PATHS = re.compile(r"^/[^/]+/[^/]+/(wiki|docs|documentation|guide)")
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


def fetch_site(
    url: str, max_pages: int = 10, max_docs_pages: int = 50
) -> tuple[str, list[CodeExample], DocStructures, list[PageRecord], dict[str, str]]:
    """Fetch *url* and follow prioritized same-domain links.

    High-priority links (docs, features, guides, changelog, API references)
    are visited before neutral links. Low-priority links (marketing, blog,
    pricing, legal, login) are skipped entirely. Cross-domain documentation
    links (detected by link text and URL path) are always followed, and once
    a cross-domain docs site is reached, same-domain links within that docs
    site are followed too (priority-scored like the seed domain).

    *max_pages* limits pages fetched from the seed domain.
    *max_docs_pages* limits pages fetched from documentation domains
    (cross-domain docs sites). Doc pages are individually small but
    collectively important, so this defaults higher than *max_pages*.

    Returns a tuple of
    ``(text, code_examples, doc_structures, page_records, raw_pages)``
    where *text* is concatenated text content from all visited pages,
    *code_examples* is a list of :class:`CodeExample` objects,
    *doc_structures* is a :class:`DocStructures` with feature tables,
    operation lists, unit lists, and function references,
    *page_records* is a list of :class:`PageRecord` with URL, timestamp,
    and content hash for every successfully fetched page, and
    *raw_pages* is a dict mapping each fetched URL to its raw HTML content
    so re-runs can diff against what changed on the product site.
    """
    visited: set[str] = set()
    queued: set[str] = set()
    results: list[str] = []
    all_examples: list[CodeExample] = []
    all_structures = DocStructures()
    all_records: list[PageRecord] = []
    raw_pages: dict[str, str] = {}
    docs_domains: set[str] = set()
    seed_visited = 0
    docs_visited = 0

    seed_domain = urlparse(url).netloc
    seed_norm = url.rstrip("/")
    queue: list[tuple[int, str]] = [(2, url)]  # seed at highest priority
    queued.add(seed_norm)

    while queue:
        queue.sort(key=lambda x: -x[0])
        _, current_url = queue.pop(0)

        norm = current_url.rstrip("/")
        if norm in visited:
            continue

        current_domain = urlparse(current_url).netloc
        is_docs = current_domain in docs_domains
        if is_docs:
            if docs_visited >= max_docs_pages:
                continue
        else:
            if seed_visited >= max_pages:
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
            html = resp.text
        except Exception:
            continue

        final_norm = str(resp.url).rstrip("/")
        if final_norm != norm:
            visited.add(final_norm)

        if is_docs:
            docs_visited += 1
        else:
            seed_visited += 1

        record = PageRecord(
            url=current_url,
            fetched_at=datetime.now(tz=timezone.utc).isoformat(),
            content_hash=hashlib.sha256(html.encode()).hexdigest(),
        )
        all_records.append(record)
        raw_pages[current_url] = html

        text = extract_text(html)
        if text:
            results.append(f"=== {current_url} ===\n{text}")

        page_examples = extract_code_examples(html, current_url)
        all_examples.extend(page_examples)

        page_structures = extract_doc_structures(html, current_url)
        all_structures.merge(page_structures)

        for link_url, anchor in extract_links(html, current_url):
            link_norm = link_url.rstrip("/")
            if link_norm in visited or link_norm in queued:
                continue
            link_domain = urlparse(link_url).netloc
            if is_docs_link(link_url, anchor):
                if link_domain != seed_domain:
                    # Never follow cross-domain links into hosting
                    # platform docs (e.g. GitHub's own features).
                    if _is_platform_domain(link_url):
                        continue
                    docs_domains.add(link_domain)
                queue.append((1, link_url))
                queued.add(link_norm)
            elif link_domain == seed_domain or link_domain in docs_domains:
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
    except Exception:
        dest.unlink(missing_ok=True)
        return None, False
