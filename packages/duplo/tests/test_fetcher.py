"""Tests for duplo.fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from duplo.fetcher import (
    detect_docs_links,
    extract_links,
    extract_text,
    fetch_site,
    fetch_text,
    is_docs_link,
    score_link,
)


class TestExtractText:
    def test_returns_visible_text(self):
        html = "<html><body><h1>Hello</h1><p>World</p></body></html>"
        assert "Hello" in extract_text(html)
        assert "World" in extract_text(html)

    def test_strips_script_tags(self):
        html = "<html><body><p>Visible</p><script>var x=1;</script></body></html>"
        result = extract_text(html)
        assert "Visible" in result
        assert "var x" not in result

    def test_strips_style_tags(self):
        html = "<html><body><p>Text</p><style>body{color:red}</style></body></html>"
        result = extract_text(html)
        assert "Text" in result
        assert "color" not in result

    def test_strips_nav_and_footer(self):
        html = (
            "<html><body><nav>Menu</nav><main>Content</main><footer>Footer</footer></body></html>"
        )
        result = extract_text(html)
        assert "Content" in result
        assert "Menu" not in result
        assert "Footer" not in result

    def test_removes_blank_lines(self):
        html = "<html><body><p>A</p><p>   </p><p>B</p></body></html>"
        result = extract_text(html)
        assert "\n\n" not in result
        lines = result.splitlines()
        assert all(line.strip() for line in lines)

    def test_empty_html(self):
        result = extract_text("<html><body></body></html>")
        assert result == ""

    def test_nested_noise(self):
        html = "<html><body><header><nav>Skip</nav></header><article>Keep</article></body></html>"
        result = extract_text(html)
        assert "Keep" in result
        assert "Skip" not in result


class TestScoreLink:
    @pytest.mark.parametrize(
        "url,anchor",
        [
            ("https://example.com/docs", ""),
            ("https://example.com/documentation", ""),
            ("https://example.com/features", ""),
            ("https://example.com/guide", ""),
            ("https://example.com/guides", ""),
            ("https://example.com/changelog", ""),
            ("https://example.com/api", ""),
            ("https://example.com/api-reference", ""),
            ("https://example.com/reference", ""),
            ("https://example.com/tutorial", ""),
            ("https://example.com/manual", ""),
            ("https://example.com/quickstart", ""),
            ("https://example.com/overview", ""),
            ("https://example.com/faq", ""),
            ("https://example.com/help", ""),
            ("https://example.com/page", "Read the docs"),
            ("https://example.com/page", "API Reference"),
            ("https://example.com/page", "Features overview"),
        ],
    )
    def test_high_priority(self, url, anchor):
        assert score_link(url, anchor) == 1

    @pytest.mark.parametrize(
        "url,anchor",
        [
            ("https://example.com/blog", ""),
            ("https://example.com/pricing", ""),
            ("https://example.com/legal", ""),
            ("https://example.com/login", ""),
            ("https://example.com/signin", ""),
            ("https://example.com/signup", ""),
            ("https://example.com/privacy", ""),
            ("https://example.com/terms", ""),
            ("https://example.com/contact", ""),
            ("https://example.com/about", ""),
            ("https://example.com/careers", ""),
            ("https://example.com/jobs", ""),
            ("https://example.com/press", ""),
            ("https://example.com/news", ""),
            ("https://example.com/page", "Sign in"),
            ("https://example.com/page", "Pricing plans"),
        ],
    )
    def test_low_priority(self, url, anchor):
        assert score_link(url, anchor) == -1

    @pytest.mark.parametrize(
        "url,anchor",
        [
            ("https://example.com/", "Home"),
            ("https://example.com/product", "Product"),
            ("https://example.com/community", "Community"),
        ],
    )
    def test_neutral(self, url, anchor):
        assert score_link(url, anchor) == 0


class TestIsDocsLink:
    @pytest.mark.parametrize(
        "url,anchor",
        [
            # Detected by URL path
            ("https://example.com/docs", ""),
            ("https://example.com/wiki", ""),
            ("https://example.com/documentation", ""),
            ("https://example.com/guide", ""),
            ("https://example.com/handbook", ""),
            ("https://example.com/reference", ""),
            ("https://example.com/manual", ""),
            ("https://example.com/getting-started", ""),
            ("https://example.com/quickstart", ""),
            ("https://example.com/tutorial", ""),
            ("https://example.com/howto", ""),
            ("https://example.com/examples", ""),
            ("https://example.com/learn", ""),
            # Detected by anchor text
            ("https://other.com/abc", "Documentation"),
            ("https://other.com/abc", "Read the docs"),
            ("https://other.com/abc", "Wiki"),
            ("https://other.com/abc", "User Guide"),
            ("https://other.com/abc", "Developer Guide"),
            ("https://other.com/abc", "Getting Started"),
            ("https://other.com/abc", "API Reference"),
            ("https://other.com/abc", "Help Center"),
            # Known platforms detected by content in URL or anchor
            ("https://github.com/org/repo/wiki", ""),
            ("https://myproject.gitbook.io/docs/", ""),
            ("https://myproject.readthedocs.io/en/latest/", "Read the Docs"),
        ],
    )
    def test_detects_docs_links(self, url, anchor):
        assert is_docs_link(url, anchor) is True

    @pytest.mark.parametrize(
        "url,anchor",
        [
            ("https://example.com/pricing", "Pricing"),
            ("https://example.com/blog", "Blog"),
            ("https://example.com/", "Home"),
            ("https://example.com/product", "Product"),
            ("https://github.com/org/repo", "Repository"),
            ("https://github.com/org/repo/issues", "Issues"),
        ],
    )
    def test_rejects_non_docs_links(self, url, anchor):
        assert is_docs_link(url, anchor) is False


class TestDetectDocsLinks:
    def test_finds_docs_links_in_html(self):
        html = (
            "<html><body>"
            '<a href="https://other.com/docs">Documentation</a>'
            '<a href="/features">Features</a>'
            '<a href="https://github.com/org/repo/wiki">Wiki</a>'
            "</body></html>"
        )
        links = detect_docs_links(html, "https://example.com")
        urls = [url for url, _ in links]
        assert "https://other.com/docs" in urls
        assert "https://github.com/org/repo/wiki" in urls
        assert len(links) == 2

    def test_returns_empty_when_no_docs_links(self):
        html = '<html><body><a href="/pricing">Pricing</a></body></html>'
        assert detect_docs_links(html, "https://example.com") == []


class TestExtractLinks:
    def test_returns_absolute_urls(self):
        html = '<html><body><a href="/docs">Docs</a></body></html>'
        links = extract_links(html, "https://example.com")
        assert ("https://example.com/docs", "Docs") in links

    def test_resolves_relative_urls(self):
        html = '<html><body><a href="guide">Guide</a></body></html>'
        links = extract_links(html, "https://example.com/section/")
        urls = [url for url, _ in links]
        assert "https://example.com/section/guide" in urls

    def test_strips_fragment(self):
        html = '<html><body><a href="/docs#section">Section</a></body></html>'
        links = extract_links(html, "https://example.com")
        urls = [url for url, _ in links]
        assert "https://example.com/docs" in urls
        assert any("#" in u for u in urls) is False

    def test_excludes_fragment_only(self):
        html = '<html><body><a href="#top">Top</a></body></html>'
        links = extract_links(html, "https://example.com")
        assert links == []

    def test_excludes_mailto(self):
        html = '<html><body><a href="mailto:hi@example.com">Email</a></body></html>'
        links = extract_links(html, "https://example.com")
        assert links == []

    def test_excludes_javascript(self):
        html = '<html><body><a href="javascript:void(0)">Click</a></body></html>'
        links = extract_links(html, "https://example.com")
        assert links == []

    def test_captures_anchor_text(self):
        html = '<html><body><a href="/page">Read More</a></body></html>'
        links = extract_links(html, "https://example.com")
        assert links[0][1] == "Read More"

    def test_multiple_links(self):
        html = '<html><body><a href="/docs">Docs</a><a href="/blog">Blog</a></body></html>'
        links = extract_links(html, "https://example.com")
        urls = [url for url, _ in links]
        assert "https://example.com/docs" in urls
        assert "https://example.com/blog" in urls


class TestFetchSite:
    def _make_response(self, html: str, status_code: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.text = html
        resp.status_code = status_code
        resp.raise_for_status = MagicMock()
        return resp

    def test_fetches_seed_url(self):
        html = "<html><body><h1>Product</h1></body></html>"
        with patch("duplo.fetcher.httpx.get", return_value=self._make_response(html)):
            result = fetch_site("https://example.com", max_pages=1)
        assert "Product" in result
        assert "https://example.com" in result

    def test_follows_high_priority_links(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Documentation</a></body></html>'
        docs_html = "<html><body><p>API docs here</p></body></html>"

        responses = {
            "https://example.com": self._make_response(seed_html),
            "https://example.com/docs": self._make_response(docs_html),
        }

        def fake_get(url, **kwargs):
            url_stripped = url.rstrip("/")
            for key, val in responses.items():
                if key.rstrip("/") == url_stripped:
                    return val
            return self._make_response("")

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            result = fetch_site("https://example.com", max_pages=5)

        assert "Home" in result
        assert "API docs here" in result

    def test_skips_low_priority_links(self):
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="/pricing">Pricing</a>'
            '<a href="/blog">Blog</a>'
            "</body></html>"
        )

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com", max_pages=5)

        fetched_paths = [url for url in fetch_calls if "pricing" in url or "blog" in url]
        assert fetched_paths == []

    def test_respects_max_pages(self):
        def make_page(links: list[str]) -> str:
            hrefs = "".join(f'<a href="{u}">page</a>' for u in links)
            return f"<html><body><p>Content</p>{hrefs}</body></html>"

        seed_html = make_page(["/a", "/b", "/c", "/d", "/e"])

        def fake_get(url, **kwargs):
            return self._make_response(seed_html)

        fetch_calls: list[str] = []
        original_get = fake_get

        def tracking_get(url, **kwargs):
            fetch_calls.append(url)
            return original_get(url, **kwargs)

        with patch("duplo.fetcher.httpx.get", side_effect=tracking_get):
            fetch_site("https://example.com", max_pages=3)

        assert len(fetch_calls) == 3

    def test_stays_on_same_domain(self):
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="https://other.com/product">Check it out</a>'
            "</body></html>"
        )

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com", max_pages=5)

        assert not any("other.com" in url for url in fetch_calls)

    def test_follows_cross_domain_docs_links(self):
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="https://other.com/docs/intro">Documentation</a>'
            '<a href="https://github.com/org/repo/wiki">Wiki</a>'
            "</body></html>"
        )
        docs_html = "<html><body><p>External docs content</p></body></html>"
        wiki_html = "<html><body><p>Wiki content</p></body></html>"

        responses = {
            "https://example.com": self._make_response(seed_html),
            "https://other.com/docs/intro": self._make_response(docs_html),
            "https://github.com/org/repo/wiki": self._make_response(wiki_html),
        }

        def fake_get(url, **kwargs):
            url_stripped = url.rstrip("/")
            for key, val in responses.items():
                if key.rstrip("/") == url_stripped:
                    return val
            return self._make_response("")

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            result = fetch_site("https://example.com", max_pages=5)

        assert "External docs content" in result
        assert "Wiki content" in result

    def test_follows_links_within_docs_domain(self):
        """Once a cross-domain docs site is reached, follow its internal links."""
        seed_html = (
            '<html><body><p>Home</p><a href="https://docs.other.com/guide">Guide</a></body></html>'
        )
        guide_html = (
            "<html><body><p>Guide content</p>"
            '<a href="https://docs.other.com/concepts">Concepts</a>'
            '<a href="https://docs.other.com/advanced">Advanced</a>'
            "</body></html>"
        )
        concepts_html = "<html><body><p>Concepts content</p></body></html>"
        advanced_html = "<html><body><p>Advanced content</p></body></html>"

        responses = {
            "https://example.com": self._make_response(seed_html),
            "https://docs.other.com/guide": self._make_response(guide_html),
            "https://docs.other.com/concepts": self._make_response(concepts_html),
            "https://docs.other.com/advanced": self._make_response(advanced_html),
        }

        def fake_get(url, **kwargs):
            url_stripped = url.rstrip("/")
            for key, val in responses.items():
                if key.rstrip("/") == url_stripped:
                    return val
            return self._make_response("")

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            result = fetch_site("https://example.com", max_pages=10)

        assert "Guide content" in result
        assert "Concepts content" in result
        assert "Advanced content" in result

    def test_does_not_follow_non_docs_cross_domain(self):
        seed_html = (
            '<html><body><p>Home</p><a href="https://other.com/product">Product</a></body></html>'
        )

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com", max_pages=5)

        assert not any("other.com" in url for url in fetch_calls)

    def test_does_not_visit_same_url_twice(self):
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="/docs">Docs</a>'
            '<a href="/docs">Docs again</a>'
            "</body></html>"
        )
        docs_html = '<html><body><a href="/">Back</a></body></html>'

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            if "docs" in url:
                return self._make_response(docs_html)
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com", max_pages=10)

        docs_calls = [u for u in fetch_calls if "docs" in u]
        assert len(docs_calls) == 1

    def test_section_headers_in_output(self):
        html = "<html><body><p>Content</p></body></html>"
        with patch("duplo.fetcher.httpx.get", return_value=self._make_response(html)):
            result = fetch_site("https://example.com", max_pages=1)
        assert "=== https://example.com ===" in result

    def test_respects_max_docs_pages(self):
        """Docs-domain pages are limited by max_docs_pages, not max_pages."""
        seed_html = (
            '<html><body><p>Home</p><a href="https://docs.other.com/guide">Guide</a></body></html>'
        )
        # Guide page links to many docs subpages
        guide_links = "".join(
            f'<a href="https://docs.other.com/page{i}">Page {i}</a>' for i in range(10)
        )
        guide_html = f"<html><body><p>Guide</p>{guide_links}</body></html>"
        subpage_html = "<html><body><p>Subpage</p></body></html>"

        responses = {
            "https://example.com": self._make_response(seed_html),
            "https://docs.other.com/guide": self._make_response(guide_html),
        }
        for i in range(10):
            url = f"https://docs.other.com/page{i}"
            responses[url] = self._make_response(subpage_html)

        docs_fetched: list[str] = []

        def fake_get(url, **kwargs):
            if "docs.other.com" in url:
                docs_fetched.append(url)
            url_stripped = url.rstrip("/")
            for key, val in responses.items():
                if key.rstrip("/") == url_stripped:
                    return val
            return self._make_response("")

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site(
                "https://example.com",
                max_pages=5,
                max_docs_pages=3,
            )

        assert len(docs_fetched) == 3

    def test_docs_limit_independent_of_seed_limit(self):
        """Seed and docs page budgets are tracked independently."""
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="/page1">P1</a>'
            '<a href="/page2">P2</a>'
            '<a href="https://docs.ext.com/guide">Guide</a>'
            "</body></html>"
        )
        page_html = "<html><body><p>Seed page</p></body></html>"
        guide_html = (
            "<html><body><p>Guide</p>"
            '<a href="https://docs.ext.com/a">A</a>'
            '<a href="https://docs.ext.com/b">B</a>'
            "</body></html>"
        )
        doc_html = "<html><body><p>Doc page</p></body></html>"

        responses = {
            "https://example.com": self._make_response(seed_html),
            "https://example.com/page1": self._make_response(page_html),
            "https://example.com/page2": self._make_response(page_html),
            "https://docs.ext.com/guide": self._make_response(guide_html),
            "https://docs.ext.com/a": self._make_response(doc_html),
            "https://docs.ext.com/b": self._make_response(doc_html),
        }

        seed_fetched: list[str] = []
        docs_fetched: list[str] = []

        def fake_get(url, **kwargs):
            if "docs.ext.com" in url:
                docs_fetched.append(url)
            elif "example.com" in url:
                seed_fetched.append(url)
            url_stripped = url.rstrip("/")
            for key, val in responses.items():
                if key.rstrip("/") == url_stripped:
                    return val
            return self._make_response("")

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site(
                "https://example.com",
                max_pages=2,
                max_docs_pages=10,
            )

        # Seed limited to 2 (home + page1 or page2)
        assert len(seed_fetched) == 2
        # Docs not limited (all 3 fit within max_docs_pages=10)
        assert len(docs_fetched) == 3

    def test_skips_failed_pages(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'

        def fake_get(url, **kwargs):
            if "docs" in url:
                raise Exception("connection error")
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            result = fetch_site("https://example.com", max_pages=5)

        assert "Home" in result  # seed page still returned


class TestFetchText:
    def _mock_response(self, html: str, status_code: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.text = html
        resp.status_code = status_code
        resp.raise_for_status = MagicMock()
        return resp

    def test_fetches_and_extracts(self):
        html = "<html><body><h1>Product</h1><p>Description</p></body></html>"
        with patch("duplo.fetcher.httpx.get", return_value=self._mock_response(html)) as mock_get:
            result = fetch_text("https://example.com")
        args, kwargs = mock_get.call_args
        assert args == ("https://example.com",)
        assert kwargs["follow_redirects"] is True
        assert kwargs["timeout"] == 30.0
        assert "User-Agent" in kwargs["headers"]
        assert "Product" in result
        assert "Description" in result

    def test_raises_on_http_error(self):
        resp = self._mock_response("", 404)
        resp.raise_for_status.side_effect = Exception("404 Not Found")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            with pytest.raises(Exception, match="404"):
                fetch_text("https://example.com/missing")
