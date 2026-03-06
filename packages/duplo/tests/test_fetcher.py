"""Tests for duplo.fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from duplo.fetcher import extract_links, extract_text, fetch_site, fetch_text, score_link


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
            '<a href="https://other.com/docs">External docs</a>'
            "</body></html>"
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
        mock_get.assert_called_once_with(
            "https://example.com", follow_redirects=True, timeout=30.0
        )
        assert "Product" in result
        assert "Description" in result

    def test_raises_on_http_error(self):
        resp = self._mock_response("", 404)
        resp.raise_for_status.side_effect = Exception("404 Not Found")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            with pytest.raises(Exception, match="404"):
                fetch_text("https://example.com/missing")
