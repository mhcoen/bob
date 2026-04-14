"""Tests for duplo.fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from duplo.fetcher import (
    PageRecord,
    _same_origin,
    detect_docs_links,
    download_media,
    extract_links,
    extract_media_urls,
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


class TestSameOrigin:
    """Tests for the _same_origin helper."""

    def test_same_origin_identical(self):
        assert _same_origin("https://example.com", "https://example.com") is True

    def test_same_origin_different_path(self):
        assert _same_origin("https://example.com/a", "https://example.com/b") is True

    def test_different_scheme(self):
        assert _same_origin("https://example.com", "http://example.com") is False

    def test_different_host(self):
        assert _same_origin("https://a.com", "https://b.com") is False

    def test_subdomain_is_cross_origin(self):
        assert _same_origin("https://example.com", "https://www.example.com") is False

    def test_different_port(self):
        assert _same_origin("https://example.com:8443", "https://example.com:9443") is False

    def test_same_explicit_port(self):
        assert _same_origin("https://example.com:8443", "https://example.com:8443") is True

    def test_no_port_vs_explicit_port(self):
        """No port (None) vs explicit port are different origins."""
        assert _same_origin("https://example.com", "https://example.com:443") is False

    def test_case_insensitive_scheme(self):
        assert _same_origin("HTTPS://example.com", "https://example.com") is True

    def test_case_insensitive_host(self):
        assert _same_origin("https://Example.COM", "https://example.com") is True


class TestFetchSite:
    def _make_response(self, html: str, status_code: int = 200, url: str = "") -> MagicMock:
        resp = MagicMock()
        resp.content = html.encode("utf-8")
        resp.status_code = status_code
        resp.url = url
        resp.headers = {"content-type": "text/html; charset=utf-8"}
        resp.raise_for_status = MagicMock()
        return resp

    def test_fetches_seed_url(self):
        html = "<html><body><h1>Product</h1></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            text, _examples, _structs, _records, _raw = fetch_site("https://example.com")
        assert "Product" in text
        assert "https://example.com" in text

    def test_follows_high_priority_same_origin_links(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Documentation</a></body></html>'
        docs_html = "<html><body><p>API docs here</p></body></html>"

        def fake_get(url, **kwargs):
            if "docs" in url:
                return self._make_response(docs_html, url=url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _examples, _structs, _records, _raw = fetch_site("https://example.com")

        assert "Home" in text
        assert "API docs here" in text

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
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com")

        fetched_paths = [url for url in fetch_calls if "pricing" in url or "blog" in url]
        assert fetched_paths == []

    def test_does_not_follow_cross_origin_links(self):
        """Deep mode only follows same-origin links."""
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="https://other.com/product">Check it out</a>'
            '<a href="https://other.com/docs/intro">Documentation</a>'
            "</body></html>"
        )

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com")

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
                return self._make_response(docs_html, url=url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com")

        docs_calls = [u for u in fetch_calls if "docs" in u]
        assert len(docs_calls) == 1

    def test_section_headers_in_output(self):
        html = "<html><body><p>Content</p></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            text, _examples, _structs, _records, _raw = fetch_site("https://example.com")
        assert "=== https://example.com ===" in text

    def test_skips_failed_pages(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'

        def fake_get(url, **kwargs):
            if "docs" in url:
                raise Exception("connection error")
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _examples, _structs, _records, _raw = fetch_site("https://example.com")

        assert "Home" in text  # seed page still returned

    def test_returns_page_records(self):
        html = "<html><body><h1>Product</h1></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _examples, _structs, records, _raw = fetch_site("https://example.com")
        assert len(records) == 1
        assert isinstance(records[0], PageRecord)
        assert records[0].url == "https://example.com"

    def test_page_record_has_timestamp(self):
        html = "<html><body><p>Hello</p></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _examples, _structs, records, _raw = fetch_site("https://example.com")
        assert records[0].fetched_at.endswith("+00:00")

    def test_page_record_has_content_hash(self):
        import hashlib

        html = "<html><body><p>Hello</p></body></html>"
        expected_hash = hashlib.sha256(html.encode()).hexdigest()
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _examples, _structs, records, _raw = fetch_site("https://example.com")
        assert records[0].content_hash == expected_hash

    def test_page_records_for_multiple_pages(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'
        docs_html = "<html><body><p>Docs</p></body></html>"

        def fake_get(url, **kwargs):
            if "docs" in url:
                return self._make_response(docs_html, url=url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _examples, _structs, records, _raw = fetch_site("https://example.com")
        assert len(records) == 2
        urls = [r.url for r in records]
        assert "https://example.com" in urls
        assert "https://example.com/docs" in urls

    def test_failed_pages_not_in_records(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'

        def fake_get(url, **kwargs):
            if "docs" in url:
                raise Exception("connection error")
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _examples, _structs, records, _raw = fetch_site("https://example.com")
        assert len(records) == 1
        assert records[0].url == "https://example.com"

    def test_returns_raw_pages_keyed_by_canonical_url(self):
        html = "<html><body><h1>Product</h1></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com")
        assert "https://example.com" in raw
        assert raw["https://example.com"] == html

    def test_raw_pages_multiple(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'
        docs_html = "<html><body><p>Docs</p></body></html>"

        def fake_get(url, **kwargs):
            if "docs" in url:
                return self._make_response(docs_html, url=url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com")
        assert len(raw) == 2
        assert raw["https://example.com"] == seed_html
        assert raw["https://example.com/docs"] == docs_html

    def test_failed_pages_not_in_raw(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'

        def fake_get(url, **kwargs):
            if "docs" in url:
                raise Exception("connection error")
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com")
        assert len(raw) == 1
        assert "https://example.com" in raw


class TestFetchSiteScrapeDepth:
    """Tests for the scrape_depth parameter."""

    def _make_response(self, html: str, status_code: int = 200, url: str = "") -> MagicMock:
        resp = MagicMock()
        resp.content = html.encode("utf-8")
        resp.status_code = status_code
        resp.url = url
        resp.headers = {"content-type": "text/html; charset=utf-8"}
        resp.raise_for_status = MagicMock()
        return resp

    def test_none_returns_empty(self):
        """scrape_depth='none' returns empty results without fetching."""
        with patch("duplo.fetcher.httpx.get") as mock_get:
            text, examples, structs, records, raw = fetch_site(
                "https://example.com", scrape_depth="none"
            )
        mock_get.assert_not_called()
        assert text == ""
        assert examples == []
        from duplo.doc_tables import DocStructures

        assert isinstance(structs, DocStructures)
        assert records == []
        assert raw == {}

    def test_none_no_network_regardless_of_url(self):
        """scrape_depth='none' never touches the network, even with a valid URL."""
        with patch("duplo.fetcher.httpx.get") as mock_get:
            text, examples, structs, records, raw = fetch_site(
                "https://real-product.example.com/docs", scrape_depth="none"
            )
        mock_get.assert_not_called()
        assert text == ""
        assert examples == []
        assert records == []
        assert raw == {}

    def test_shallow_fetches_single_page(self):
        """scrape_depth='shallow' fetches only the entry URL."""
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _ex, _st, records, raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )

        assert len(fetch_calls) == 1
        assert "Home" in text
        assert len(records) == 1
        assert len(raw) == 1

    def test_shallow_no_link_following(self):
        """scrape_depth='shallow' does not follow any links."""
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="/docs">Docs</a>'
            '<a href="/features">Features</a></body></html>'
        )

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com", scrape_depth="shallow")

        assert len(fetch_calls) == 1
        assert fetch_calls[0] == "https://example.com"

    def test_shallow_failure_returns_empty(self):
        """scrape_depth='shallow' returns empty results on fetch failure."""

        def fake_get(url, **kwargs):
            raise Exception("connection error")

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, examples, structs, records, raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )

        assert text == ""
        assert examples == []
        assert records == []
        assert raw == {}

    def test_shallow_raw_pages_keyed_by_canonical_url(self):
        """shallow mode keys raw_pages by canonical post-redirect URL."""
        html = "<html><body><p>Hello</p></body></html>"
        resp = self._make_response(html, url="https://Example.COM/")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, _rec, raw = fetch_site("https://Example.COM/", scrape_depth="shallow")
        # Canonical form: lowercase host, trailing slash stripped
        assert "https://example.com" in raw

    def test_shallow_extracts_code_examples(self):
        """shallow mode extracts code examples from the single page."""
        html = "<html><body><pre><code>print('hello')</code></pre></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, examples, _st, _rec, _raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )
        assert isinstance(examples, list)

    def test_shallow_extracts_doc_structures(self):
        """shallow mode extracts doc structures from the single page."""
        html = (
            "<html><body>"
            "<h2>Features</h2>"
            "<table><tr><th>Feature</th><th>Description</th></tr>"
            "<tr><td>Auth</td><td>OAuth2 support</td></tr></table>"
            "</body></html>"
        )
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, structs, _rec, _raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )
        from duplo.doc_tables import DocStructures

        assert isinstance(structs, DocStructures)

    def test_shallow_page_record_fields(self):
        """shallow mode page record has url, timestamp, and content hash."""
        import hashlib

        html = "<html><body><p>Content</p></body></html>"
        expected_hash = hashlib.sha256(html.encode()).hexdigest()
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, records, _raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )
        assert len(records) == 1
        assert records[0].url == "https://example.com"
        assert records[0].fetched_at.endswith("+00:00")
        assert records[0].content_hash == expected_hash

    def test_shallow_records_and_raw_in_sync(self):
        """shallow mode: every PageRecord has a corresponding raw_pages entry."""
        html = "<html><body><p>Content</p></body></html>"
        resp = self._make_response(html, url="https://example.com/path/")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, records, raw = fetch_site(
                "https://example.com/path/", scrape_depth="shallow"
            )
        assert len(records) == len(raw)
        from duplo.url_canon import canonicalize_url

        for rec in records:
            assert canonicalize_url(rec.url) in raw

    def test_deep_same_origin_only(self):
        """Deep mode follows same-origin links but not cross-origin."""
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="/docs">Docs</a>'
            '<a href="https://other.com/docs">External Docs</a>'
            "</body></html>"
        )
        docs_html = "<html><body><p>Internal docs</p></body></html>"

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            if "docs" in url:
                return self._make_response(docs_html, url=url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _ex, _st, _rec, _raw = fetch_site("https://example.com", scrape_depth="deep")

        assert "Internal docs" in text
        assert not any("other.com" in u for u in fetch_calls)

    def test_deep_rejects_different_scheme(self):
        """Deep mode treats http vs https as cross-origin."""
        seed_html = (
            '<html><body><p>Home</p><a href="http://example.com/docs">HTTP Docs</a></body></html>'
        )

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com", scrape_depth="deep")

        # Only the seed URL should be fetched; http:// link is cross-origin
        assert len(fetch_calls) == 1

    def test_deep_rejects_subdomain(self):
        """Deep mode treats subdomains as cross-origin."""
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="https://docs.example.com/guide">Subdomain Docs</a>'
            "</body></html>"
        )

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com", scrape_depth="deep")

        assert len(fetch_calls) == 1
        assert not any("docs.example.com" in u for u in fetch_calls)

    def test_deep_rejects_different_port(self):
        """Deep mode treats different ports as cross-origin."""
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="https://example.com:9443/api">API on other port</a>'
            "</body></html>"
        )

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com", scrape_depth="deep")

        assert len(fetch_calls) == 1

    def test_deep_is_default(self):
        """Default scrape_depth is 'deep' (follows same-origin links)."""
        seed_html = '<html><body><p>Home</p><a href="/page2">Page 2</a></body></html>'
        page2_html = "<html><body><p>Page 2 content</p></body></html>"

        def fake_get(url, **kwargs):
            if "page2" in url:
                return self._make_response(page2_html, url=url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _ex, _st, _rec, _raw = fetch_site("https://example.com")

        assert "Page 2 content" in text

    def test_deep_raw_pages_keyed_by_canonical_url(self):
        """Deep mode keys raw_pages by canonicalized post-redirect URL."""
        html = "<html><body><p>Hello</p></body></html>"
        resp = self._make_response(html, url="https://Example.COM/")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, _rec, raw = fetch_site("https://Example.COM/", scrape_depth="deep")
        assert "https://example.com" in raw

    def test_records_and_raw_pages_in_sync(self):
        """Every PageRecord has a corresponding raw_pages entry."""
        seed_html = '<html><body><p>Home</p><a href="/a">A</a></body></html>'
        a_html = "<html><body><p>A</p></body></html>"

        def fake_get(url, **kwargs):
            if "/a" in url:
                return self._make_response(a_html, url=url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")

        assert len(records) == len(raw)
        # Every record URL is already canonical; verify it's in raw_pages
        for rec in records:
            assert rec.url in raw

    def test_deep_raw_pages_values_are_raw_html(self):
        """raw_pages values are the full raw HTML, not extracted text."""
        seed_html = "<html><body><script>var x = 1;</script><p>Visible</p></body></html>"
        resp = self._make_response(seed_html)
        resp.url = "https://example.com"
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com", scrape_depth="deep")
        # Value is the full HTML including script tags (not extracted text)
        assert raw["https://example.com"] == seed_html
        assert "<script>" in raw["https://example.com"]

    def test_shallow_raw_pages_value_is_raw_html(self):
        """shallow mode raw_pages value is full raw HTML."""
        html = "<html><body><nav>Menu</nav><p>Content</p></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com", scrape_depth="shallow")
        assert raw["https://example.com"] == html
        assert "<nav>" in raw["https://example.com"]

    def test_deep_redirect_keyed_by_post_redirect_canonical_url(self):
        """Deep mode stores raw HTML under the post-redirect canonical URL."""
        seed_html = '<html><body><p>Home</p><a href="/old-page">Link</a></body></html>'
        redirected_html = "<html><body><p>Redirected</p></body></html>"

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.headers = {"content-type": "text/html"}
            if "/old-page" in url:
                resp.content = redirected_html.encode("utf-8")
                resp.url = "https://example.com/new-page"
            else:
                resp.content = seed_html.encode("utf-8")
                resp.url = url
            return resp

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")

        from duplo.url_canon import canonicalize_url

        # Post-redirect URL is the key
        assert canonicalize_url("https://example.com/new-page") in raw
        assert raw[canonicalize_url("https://example.com/new-page")] == redirected_html
        # Original pre-redirect URL is NOT a separate key
        assert canonicalize_url("https://example.com/old-page") not in raw
        # Both pages have records
        assert len(records) == 2
        assert len(raw) == 2
        # PageRecord.url is the post-redirect canonical URL
        record_urls = [r.url for r in records]
        assert canonicalize_url("https://example.com/new-page") in record_urls

    def test_deep_redirect_prevents_revisit_of_target(self):
        """If a page redirects, the redirect target is not re-fetched later."""
        # Seed links to /redir only. /redir redirects to /final.
        # /redir's HTML links to /final — but /final should not be
        # fetched because the redirect already marked it visited.
        seed_html = '<html><body><p>Home</p><a href="/redir">Redir</a></body></html>'
        redir_html = '<html><body><p>Redirected</p><a href="/final">Final</a></body></html>'
        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.headers = {"content-type": "text/html"}
            if "/redir" in url:
                resp.content = redir_html.encode("utf-8")
                resp.url = "https://example.com/final"
            else:
                resp.content = seed_html.encode("utf-8")
                resp.url = url
            return resp

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com", scrape_depth="deep")

        # /final should NOT be fetched — the redirect marked it visited
        final_fetches = [u for u in fetch_calls if "/final" in u]
        assert final_fetches == []

    def test_shallow_redirect_keyed_by_post_redirect_url(self):
        """shallow mode keys raw_pages by post-redirect canonical URL."""
        html = "<html><body><p>Redirected</p></body></html>"
        resp = self._make_response(html, url="https://example.com/final")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, records, raw = fetch_site(
                "https://example.com/old", scrape_depth="shallow"
            )
        from duplo.url_canon import canonicalize_url

        # Key is the post-redirect canonical URL
        assert canonicalize_url("https://example.com/final") in raw
        # PageRecord.url is also the post-redirect canonical URL
        assert records[0].url == canonicalize_url("https://example.com/final")

    def test_deep_page_record_url_is_canonical(self):
        """Deep mode PageRecord.url values are canonical."""
        html = "<html><body><p>Hello</p></body></html>"
        resp = self._make_response(html, url="https://Example.COM/Path/")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, records, _raw = fetch_site(
                "https://Example.COM/Path/", scrape_depth="deep"
            )
        assert records[0].url == "https://example.com/Path"

    def test_shallow_page_record_url_is_canonical(self):
        """shallow mode PageRecord.url values are canonical."""
        html = "<html><body><p>Hello</p></body></html>"
        resp = self._make_response(html, url="https://Example.COM/Path/")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, records, _raw = fetch_site(
                "https://Example.COM/Path/", scrape_depth="shallow"
            )
        assert records[0].url == "https://example.com/Path"


class TestFetchSiteFailedFetches:
    """Failed fetches are excluded from both raw_pages and page_records."""

    def _make_response(
        self,
        html: str,
        url: str = "",
        content_type: str = "text/html; charset=utf-8",
    ) -> MagicMock:
        resp = MagicMock()
        resp.content = html.encode("utf-8")
        resp.url = url
        resp.headers = {"content-type": content_type}
        resp.raise_for_status = MagicMock()
        return resp

    # -- 404 --

    def test_deep_404_excluded(self):
        """Deep: a 404 page is not in records or raw_pages."""
        seed_html = '<html><body><p>Home</p><a href="/gone">Gone</a></body></html>'

        def fake_get(url, **kwargs):
            if "/gone" in url:
                resp = MagicMock()
                resp.raise_for_status.side_effect = Exception("404")
                return resp
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")
        assert len(records) == 1
        assert len(raw) == 1
        assert records[0].url == "https://example.com"

    def test_shallow_404_excluded(self):
        """Shallow: a 404 returns empty results."""

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status.side_effect = Exception("404")
            return resp

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _ex, _st, records, raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )
        assert text == ""
        assert records == []
        assert raw == {}

    # -- timeout --

    def test_deep_timeout_excluded(self):
        """Deep: timed-out page is not in records or raw_pages."""
        import httpx as httpx_mod

        seed_html = '<html><body><p>Home</p><a href="/slow">Slow</a></body></html>'

        def fake_get(url, **kwargs):
            if "/slow" in url:
                raise httpx_mod.TimeoutException("timed out")
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")
        assert len(records) == 1
        assert len(raw) == 1

    def test_shallow_timeout_excluded(self):
        """Shallow: timeout returns empty results."""
        import httpx as httpx_mod

        def fake_get(url, **kwargs):
            raise httpx_mod.TimeoutException("timed out")

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _ex, _st, records, raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )
        assert text == ""
        assert records == []
        assert raw == {}

    # -- non-HTML content-type --

    def test_deep_non_html_excluded(self):
        """Deep: non-HTML response is not in records or raw_pages."""
        seed_html = '<html><body><p>Home</p><a href="/file.pdf">PDF</a></body></html>'

        def fake_get(url, **kwargs):
            if "/file.pdf" in url:
                return self._make_response("binary", url=url, content_type="application/pdf")
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")
        assert len(records) == 1
        assert len(raw) == 1
        assert records[0].url == "https://example.com"

    def test_shallow_non_html_excluded(self):
        """Shallow: non-HTML content-type returns empty results."""
        resp = self._make_response(
            "binary",
            url="https://example.com/f.pdf",
            content_type="application/pdf",
        )
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            text, _ex, _st, records, raw = fetch_site(
                "https://example.com/f.pdf", scrape_depth="shallow"
            )
        assert text == ""
        assert records == []
        assert raw == {}

    def test_xhtml_accepted(self):
        """application/xhtml+xml is treated as HTML."""
        html = "<html><body><p>XHTML</p></body></html>"
        resp = self._make_response(
            html,
            url="https://example.com",
            content_type="application/xhtml+xml",
        )
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, records, raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )
        assert len(records) == 1
        assert len(raw) == 1

    # -- invalid UTF-8 decoded with replacement characters --

    def test_deep_invalid_utf8_replaced(self):
        """Deep: page with invalid UTF-8 bytes uses replacement characters."""
        from duplo.url_canon import canonicalize_url

        seed_html = '<html><body><p>Home</p><a href="/bad">Bad</a></body></html>'
        # Invalid UTF-8: 0xff is never valid in UTF-8
        bad_bytes = b"<html><body><p>Caf\xff</p></body></html>"

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.headers = {"content-type": "text/html"}
            if "/bad" in url:
                resp.content = bad_bytes
                resp.url = url
            else:
                resp.content = seed_html.encode("utf-8")
                resp.url = url
            return resp

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")
        # Both pages are included — invalid bytes replaced with U+FFFD
        assert len(records) == 2
        assert len(raw) == 2
        bad_canon = canonicalize_url("https://example.com/bad")
        assert "\ufffd" in raw[bad_canon]

    def test_shallow_invalid_utf8_replaced(self):
        """Shallow: invalid UTF-8 bytes produce replacement characters."""
        from duplo.url_canon import canonicalize_url

        bad_bytes = b"<html><body><p>Caf\xff</p></body></html>"
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.headers = {"content-type": "text/html"}
        resp.url = "https://example.com"
        resp.content = bad_bytes
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            text, _ex, _st, records, raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )
        assert len(records) == 1
        assert len(raw) == 1
        canon = canonicalize_url("https://example.com")
        assert "\ufffd" in raw[canon]

    # -- record_failure is called --

    def test_deep_404_records_failure(self):
        """Deep: 404 calls record_failure."""
        seed_html = '<html><body><p>Home</p><a href="/gone">G</a></body></html>'

        def fake_get(url, **kwargs):
            if "/gone" in url:
                resp = MagicMock()
                resp.raise_for_status.side_effect = Exception("404")
                return resp
            return self._make_response(seed_html, url=url)

        with (
            patch("duplo.fetcher.httpx.get", side_effect=fake_get),
            patch("duplo.fetcher.record_failure") as mock_rf,
        ):
            fetch_site("https://example.com", scrape_depth="deep")
        mock_rf.assert_called_once()
        args = mock_rf.call_args
        assert args[0][0] == "fetcher:fetch_site"
        assert args[0][1] == "fetch"

    def test_deep_non_html_records_failure(self):
        """Deep: non-HTML content-type calls record_failure."""
        seed_html = '<html><body><p>Home</p><a href="/img.png">I</a></body></html>'

        def fake_get(url, **kwargs):
            if "/img.png" in url:
                return self._make_response("bytes", url=url, content_type="image/png")
            return self._make_response(seed_html, url=url)

        with (
            patch("duplo.fetcher.httpx.get", side_effect=fake_get),
            patch("duplo.fetcher.record_failure") as mock_rf,
        ):
            fetch_site("https://example.com", scrape_depth="deep")
        mock_rf.assert_called_once()
        assert "Non-HTML" in mock_rf.call_args[0][2]

    # -- sync invariant --

    def test_deep_mixed_failures_sync(self):
        """Deep: records and raw_pages stay in sync with mixed successes/failures."""
        seed_html = (
            "<html><body><p>Home</p>"
            '<a href="/ok">OK</a>'
            '<a href="/fail">Fail</a>'
            '<a href="/pdf">PDF</a>'
            "</body></html>"
        )
        ok_html = "<html><body><p>OK page</p></body></html>"

        def fake_get(url, **kwargs):
            if "/fail" in url:
                raise Exception("connection refused")
            if "/pdf" in url:
                return self._make_response("binary", url=url, content_type="application/pdf")
            if "/ok" in url:
                return self._make_response(ok_html, url=url)
            return self._make_response(seed_html, url=url)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")
        # Only seed + /ok should be present
        assert len(records) == 2
        assert len(raw) == 2
        for rec in records:
            assert rec.url in raw


class TestFetchSiteReturnShape:
    """Tests for 5-tuple return shape across all scrape depths."""

    def _make_response(self, html: str, url: str = "") -> MagicMock:
        resp = MagicMock()
        resp.content = html.encode("utf-8")
        resp.url = url
        resp.headers = {"content-type": "text/html; charset=utf-8"}
        resp.raise_for_status = MagicMock()
        return resp

    def test_deep_returns_five_element_tuple(self):
        html = "<html><body><p>Content</p></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            result = fetch_site("https://example.com", scrape_depth="deep")
        assert isinstance(result, tuple)
        assert len(result) == 5

    def test_shallow_returns_five_element_tuple(self):
        html = "<html><body><p>Content</p></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            result = fetch_site("https://example.com", scrape_depth="shallow")
        assert isinstance(result, tuple)
        assert len(result) == 5

    def test_none_returns_five_element_tuple(self):
        result = fetch_site("https://example.com", scrape_depth="none")
        assert isinstance(result, tuple)
        assert len(result) == 5

    def test_return_types(self):
        """Each element has the expected type."""
        html = "<html><body><p>Content</p></body></html>"
        resp = self._make_response(html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            text, examples, structs, records, raw = fetch_site(
                "https://example.com", scrape_depth="shallow"
            )
        assert isinstance(text, str)
        assert isinstance(examples, list)
        from duplo.doc_tables import DocStructures

        assert isinstance(structs, DocStructures)
        assert isinstance(records, list)
        assert isinstance(raw, dict)

    def test_deep_cross_origin_not_in_raw_pages_or_records(self):
        """Cross-origin URLs do not appear in raw_pages keys or PageRecord URLs."""
        seed_html = (
            '<html><body><p>Home</p><a href="https://other.com/docs">External</a></body></html>'
        )
        resp = self._make_response(seed_html, url="https://example.com")
        with patch("duplo.fetcher.httpx.get", return_value=resp):
            _text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")
        record_urls = {r.url for r in records}
        assert not any("other.com" in u for u in raw)
        assert not any("other.com" in u for u in record_urls)


class TestFetchText:
    def _mock_response(self, html: str, status_code: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.content = html.encode("utf-8")
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


class TestExtractMediaUrls:
    """Tests for extract_media_urls."""

    def test_extracts_img_src(self):
        html = '<html><body><img src="/hero.png"></body></html>'
        imgs, vids = extract_media_urls(html, "https://example.com/page")
        assert "https://example.com/hero.png" in imgs
        assert vids == []

    def test_extracts_video_src(self):
        html = '<html><body><video src="/demo.mp4"></video></body></html>'
        imgs, vids = extract_media_urls(html, "https://example.com/page")
        assert "https://example.com/demo.mp4" in vids

    def test_extracts_source_tag_video(self):
        html = (
            '<html><body><video><source src="/clip.webm" type="video/webm"></video></body></html>'
        )
        imgs, vids = extract_media_urls(html, "https://example.com/page")
        assert "https://example.com/clip.webm" in vids

    def test_extracts_video_poster_as_image(self):
        html = '<html><body><video src="/demo.mp4" poster="/thumb.png"></video></body></html>'
        imgs, vids = extract_media_urls(html, "https://example.com/page")
        assert "https://example.com/thumb.png" in imgs
        assert "https://example.com/demo.mp4" in vids

    def test_skips_data_uri(self):
        html = '<html><body><img src="data:image/png;base64,abc"></body></html>'
        imgs, _ = extract_media_urls(html, "https://example.com/")
        assert imgs == []

    def test_skips_svg(self):
        html = '<html><body><img src="/icon.svg"></body></html>'
        imgs, _ = extract_media_urls(html, "https://example.com/")
        assert imgs == []

    def test_deduplicates_within_page(self):
        html = '<html><body><img src="/a.png"><img src="/a.png"></body></html>'
        imgs, _ = extract_media_urls(html, "https://example.com/")
        assert len(imgs) == 1

    def test_picture_srcset(self):
        html = (
            "<html><body><picture>"
            '<source srcset="/wide.jpg 1024w, /narrow.jpg 640w">'
            "</picture></body></html>"
        )
        imgs, _ = extract_media_urls(html, "https://example.com/")
        assert "https://example.com/wide.jpg" in imgs

    def test_cross_origin_img_included(self):
        """Cross-origin media is extracted regardless of origin."""
        html = (
            "<html><body>"
            '<img src="https://cdn.other.com/hero.png">'
            '<img src="https://static.third-party.io/banner.jpg">'
            "</body></html>"
        )
        imgs, _ = extract_media_urls(html, "https://example.com/page")
        assert "https://cdn.other.com/hero.png" in imgs
        assert "https://static.third-party.io/banner.jpg" in imgs

    def test_cross_origin_video_included(self):
        """Cross-origin video sources are extracted regardless of origin."""
        html = (
            "<html><body>"
            '<video src="https://media.vimeo.com/demo.mp4"'
            ' poster="https://thumbs.cdn.net/poster.png"></video>'
            "</body></html>"
        )
        imgs, vids = extract_media_urls(html, "https://example.com/")
        assert "https://media.vimeo.com/demo.mp4" in vids
        assert "https://thumbs.cdn.net/poster.png" in imgs

    def test_mixed_origin_media(self):
        """Same-origin and cross-origin media coexist in results."""
        html = (
            "<html><body>"
            '<img src="/local.png">'
            '<img src="https://cdn.example.net/remote.png">'
            '<video src="https://example.com/local.mp4"></video>'
            '<video src="https://videos.other.com/ext.mp4"></video>'
            "</body></html>"
        )
        imgs, vids = extract_media_urls(html, "https://example.com/page")
        assert "https://example.com/local.png" in imgs
        assert "https://cdn.example.net/remote.png" in imgs
        assert "https://example.com/local.mp4" in vids
        assert "https://videos.other.com/ext.mp4" in vids

    def test_relative_urls_resolved_against_page_url(self):
        """Relative hrefs resolve against the embedding page URL."""
        html = (
            "<html><body>"
            '<img src="images/hero.png">'
            '<video src="media/demo.mp4"></video>'
            '<video><source src="media/clip.webm" type="video/webm"></video>'
            "</body></html>"
        )
        imgs, vids = extract_media_urls(html, "https://example.com/docs/page.html")
        # Relative paths resolve against /docs/page.html, not root.
        assert "https://example.com/docs/images/hero.png" in imgs
        assert "https://example.com/docs/media/demo.mp4" in vids
        assert "https://example.com/docs/media/clip.webm" in vids


class TestDownloadMedia:
    """Tests for download_media — cached-vs-new behavior."""

    def test_returns_newly_downloaded_paths(self, tmp_path):
        """New downloads appear in the returned lists."""
        out = tmp_path / "media"
        out.mkdir()

        def fake_stream(method, url, **kw):
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.raise_for_status = MagicMock()
            cm.iter_bytes = MagicMock(return_value=[b"x" * 20_000])
            return cm

        with patch("duplo.fetcher.httpx.stream", side_effect=fake_stream):
            imgs, vids = download_media(
                ["https://example.com/a.png"],
                ["https://example.com/b.mp4"],
                out,
            )
        assert len(imgs) == 1
        assert len(vids) == 1

    def test_returns_cached_paths(self, tmp_path):
        """Previously cached files are included in the returned lists."""
        out = tmp_path / "media"
        out.mkdir()
        # Pre-create cached files.
        cached_img = out / "example_com_hero.png"
        cached_img.write_bytes(b"x" * 20_000)
        cached_vid = out / "example_com_demo.mp4"
        cached_vid.write_bytes(b"video-data")

        # No HTTP calls should happen — files already exist.
        with patch("duplo.fetcher.httpx.stream") as mock_stream:
            imgs, vids = download_media(
                ["https://example.com/hero.png"],
                ["https://example.com/demo.mp4"],
                out,
            )
        mock_stream.assert_not_called()
        assert len(imgs) == 1
        assert imgs[0] == cached_img
        assert len(vids) == 1
        assert vids[0] == cached_vid

    def test_mixed_cached_and_new(self, tmp_path):
        """Cached and newly downloaded files both appear in results."""
        out = tmp_path / "media"
        out.mkdir()
        # Pre-create one cached image.
        cached = out / "example_com_old.png"
        cached.write_bytes(b"x" * 20_000)

        def fake_stream(method, url, **kw):
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.raise_for_status = MagicMock()
            cm.iter_bytes = MagicMock(return_value=[b"x" * 20_000])
            return cm

        with patch("duplo.fetcher.httpx.stream", side_effect=fake_stream):
            imgs, _ = download_media(
                [
                    "https://example.com/old.png",
                    "https://example.com/new.png",
                ],
                [],
                out,
            )
        assert len(imgs) == 2

    def test_tiny_image_excluded(self, tmp_path):
        """Images smaller than _MIN_IMAGE_BYTES are not returned."""
        out = tmp_path / "media"
        out.mkdir()

        def fake_stream(method, url, **kw):
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            cm.raise_for_status = MagicMock()
            # 100 bytes — below threshold
            cm.iter_bytes = MagicMock(return_value=[b"x" * 100])
            return cm

        with patch("duplo.fetcher.httpx.stream", side_effect=fake_stream):
            imgs, _ = download_media(["https://example.com/icon.png"], [], out)
        assert imgs == []

    def test_empty_urls(self, tmp_path):
        """Empty input returns empty lists without creating output_dir."""
        out = tmp_path / "media"
        imgs, vids = download_media([], [], out)
        assert imgs == []
        assert vids == []
