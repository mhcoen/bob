"""Fetch a product URL and extract its text content, following priority links."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

_NOISE_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside"}
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


def fetch_site(url: str, max_pages: int = 10) -> str:
    """Fetch *url* and follow prioritized same-domain links.

    High-priority links (docs, features, guides, changelog, API references)
    are visited before neutral links. Low-priority links (marketing, blog,
    pricing, legal, login) are skipped entirely. Cross-domain documentation
    links (detected by link text and URL path) are always followed, and once
    a cross-domain docs site is reached, same-domain links within that docs
    site are followed too (priority-scored like the seed domain).

    Returns concatenated text content from all visited pages, each prefixed
    with its URL as a section header.
    """
    visited: set[str] = set()
    queued: set[str] = set()
    results: list[str] = []
    docs_domains: set[str] = set()

    seed_domain = urlparse(url).netloc
    seed_norm = url.rstrip("/")
    queue: list[tuple[int, str]] = [(2, url)]  # seed at highest priority
    queued.add(seed_norm)

    while queue and len(visited) < max_pages:
        queue.sort(key=lambda x: -x[0])
        _, current_url = queue.pop(0)

        norm = current_url.rstrip("/")
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
            html = resp.text
        except Exception:
            continue

        text = extract_text(html)
        if text:
            results.append(f"=== {current_url} ===\n{text}")

        for link_url, anchor in extract_links(html, current_url):
            link_norm = link_url.rstrip("/")
            if link_norm in visited or link_norm in queued:
                continue
            link_domain = urlparse(link_url).netloc
            if is_docs_link(link_url, anchor):
                if link_domain != seed_domain:
                    docs_domains.add(link_domain)
                queue.append((1, link_url))
                queued.add(link_norm)
            elif link_domain == seed_domain or link_domain in docs_domains:
                link_score = score_link(link_url, anchor)
                if link_score >= 0:
                    queue.append((link_score, link_url))
                    queued.add(link_norm)

    return "\n\n".join(results)


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
