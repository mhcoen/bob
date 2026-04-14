"""Tests for duplo.fetcher."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from duplo.fetcher import (
    PageRecord,
    _same_origin,
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
    def _make_response(self, html: str, status_code: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.text = html
        resp.status_code = status_code
        resp.raise_for_status = MagicMock()
        return resp

    def test_fetches_seed_url(self):
        html = "<html><body><h1>Product</h1></body></html>"
        with patch("duplo.fetcher.httpx.get", return_value=self._make_response(html)):
            text, _examples, _structs, _records, _raw = fetch_site("https://example.com")
        assert "Product" in text
        assert "https://example.com" in text

    def test_follows_high_priority_same_origin_links(self):
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
            return self._make_response(seed_html)

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
            return self._make_response(seed_html)

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
                return self._make_response(docs_html)
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com")

        docs_calls = [u for u in fetch_calls if "docs" in u]
        assert len(docs_calls) == 1

    def test_section_headers_in_output(self):
        html = "<html><body><p>Content</p></body></html>"
        with patch("duplo.fetcher.httpx.get", return_value=self._make_response(html)):
            text, _examples, _structs, _records, _raw = fetch_site("https://example.com")
        assert "=== https://example.com ===" in text

    def test_skips_failed_pages(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'

        def fake_get(url, **kwargs):
            if "docs" in url:
                raise Exception("connection error")
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _examples, _structs, _records, _raw = fetch_site("https://example.com")

        assert "Home" in text  # seed page still returned

    def test_returns_page_records(self):
        html = "<html><body><h1>Product</h1></body></html>"
        with patch("duplo.fetcher.httpx.get", return_value=self._make_response(html)):
            _text, _examples, _structs, records, _raw = fetch_site("https://example.com")
        assert len(records) == 1
        assert isinstance(records[0], PageRecord)
        assert records[0].url == "https://example.com"

    def test_page_record_has_timestamp(self):
        html = "<html><body><p>Hello</p></body></html>"
        with patch("duplo.fetcher.httpx.get", return_value=self._make_response(html)):
            _text, _examples, _structs, records, _raw = fetch_site("https://example.com")
        assert records[0].fetched_at.endswith("+00:00")

    def test_page_record_has_content_hash(self):
        import hashlib

        html = "<html><body><p>Hello</p></body></html>"
        expected_hash = hashlib.sha256(html.encode()).hexdigest()
        with patch("duplo.fetcher.httpx.get", return_value=self._make_response(html)):
            _text, _examples, _structs, records, _raw = fetch_site("https://example.com")
        assert records[0].content_hash == expected_hash

    def test_page_records_for_multiple_pages(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'
        docs_html = "<html><body><p>Docs</p></body></html>"
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
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _examples, _structs, records, _raw = fetch_site("https://example.com")
        assert len(records) == 1
        assert records[0].url == "https://example.com"

    def test_returns_raw_pages_keyed_by_canonical_url(self):
        html = "<html><body><h1>Product</h1></body></html>"
        with patch("duplo.fetcher.httpx.get", return_value=self._make_response(html)):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com")
        assert "https://example.com" in raw
        assert raw["https://example.com"] == html

    def test_raw_pages_multiple(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'
        docs_html = "<html><body><p>Docs</p></body></html>"
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
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com")
        assert len(raw) == 2
        assert raw["https://example.com"] == seed_html
        assert raw["https://example.com/docs"] == docs_html

    def test_failed_pages_not_in_raw(self):
        seed_html = '<html><body><p>Home</p><a href="/docs">Docs</a></body></html>'

        def fake_get(url, **kwargs):
            if "docs" in url:
                raise Exception("connection error")
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com")
        assert len(raw) == 1
        assert "https://example.com" in raw


class TestFetchSiteScrapeDepth:
    """Tests for the scrape_depth parameter."""

    def _make_response(self, html: str, status_code: int = 200) -> MagicMock:
        resp = MagicMock()
        resp.text = html
        resp.status_code = status_code
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
            return self._make_response(seed_html)

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
            return self._make_response(seed_html)

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
        """shallow mode keys raw_pages by canonical URL."""
        html = "<html><body><p>Hello</p></body></html>"
        with patch(
            "duplo.fetcher.httpx.get",
            return_value=self._make_response(html),
        ):
            _text, _ex, _st, _rec, raw = fetch_site("https://Example.COM/", scrape_depth="shallow")
        # Canonical form: lowercase host, trailing slash stripped
        assert "https://example.com" in raw

    def test_shallow_extracts_code_examples(self):
        """shallow mode extracts code examples from the single page."""
        html = "<html><body><pre><code>print('hello')</code></pre></body></html>"
        with patch(
            "duplo.fetcher.httpx.get",
            return_value=self._make_response(html),
        ):
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
        with patch(
            "duplo.fetcher.httpx.get",
            return_value=self._make_response(html),
        ):
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
        with patch(
            "duplo.fetcher.httpx.get",
            return_value=self._make_response(html),
        ):
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
        with patch(
            "duplo.fetcher.httpx.get",
            return_value=self._make_response(html),
        ):
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

        responses = {
            "https://example.com": self._make_response(seed_html),
            "https://example.com/docs": self._make_response(docs_html),
        }

        fetch_calls: list[str] = []

        def fake_get(url, **kwargs):
            fetch_calls.append(url)
            url_stripped = url.rstrip("/")
            for key, val in responses.items():
                if key.rstrip("/") == url_stripped:
                    return val
            return self._make_response("")

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
            return self._make_response(seed_html)

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
            return self._make_response(seed_html)

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
            return self._make_response(seed_html)

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            fetch_site("https://example.com", scrape_depth="deep")

        assert len(fetch_calls) == 1

    def test_deep_is_default(self):
        """Default scrape_depth is 'deep' (follows same-origin links)."""
        seed_html = '<html><body><p>Home</p><a href="/page2">Page 2</a></body></html>'
        page2_html = "<html><body><p>Page 2 content</p></body></html>"

        responses = {
            "https://example.com": self._make_response(seed_html),
            "https://example.com/page2": self._make_response(page2_html),
        }

        def fake_get(url, **kwargs):
            url_stripped = url.rstrip("/")
            for key, val in responses.items():
                if key.rstrip("/") == url_stripped:
                    return val
            return self._make_response("")

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            text, _ex, _st, _rec, _raw = fetch_site("https://example.com")

        assert "Page 2 content" in text

    def test_deep_raw_pages_keyed_by_canonical_url(self):
        """Deep mode keys raw_pages by canonicalized URL."""
        html = "<html><body><p>Hello</p></body></html>"
        with patch(
            "duplo.fetcher.httpx.get",
            return_value=self._make_response(html),
        ):
            _text, _ex, _st, _rec, raw = fetch_site("https://Example.COM/", scrape_depth="deep")
        assert "https://example.com" in raw

    def test_records_and_raw_pages_in_sync(self):
        """Every PageRecord has a corresponding raw_pages entry."""
        seed_html = '<html><body><p>Home</p><a href="/a">A</a></body></html>'
        a_html = "<html><body><p>A</p></body></html>"

        responses = {
            "https://example.com": self._make_response(seed_html),
            "https://example.com/a": self._make_response(a_html),
        }

        def fake_get(url, **kwargs):
            url_stripped = url.rstrip("/")
            for key, val in responses.items():
                if key.rstrip("/") == url_stripped:
                    return val
            return self._make_response("")

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")

        assert len(records) == len(raw)
        # Every record URL's canonical form should be in raw_pages
        from duplo.url_canon import canonicalize_url

        for rec in records:
            assert canonicalize_url(rec.url) in raw

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
        with patch(
            "duplo.fetcher.httpx.get",
            return_value=self._make_response(html),
        ):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com", scrape_depth="shallow")
        assert raw["https://example.com"] == html
        assert "<nav>" in raw["https://example.com"]

    def test_deep_redirect_keyed_by_original_canonical_url(self):
        """Deep mode stores raw HTML under the original request URL, not redirect target."""
        seed_html = '<html><body><p>Home</p><a href="/old-page">Link</a></body></html>'
        redirected_html = "<html><body><p>Redirected</p></body></html>"

        def fake_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            if "/old-page" in url:
                resp.text = redirected_html
                resp.url = "https://example.com/new-page"
            else:
                resp.text = seed_html
                resp.url = url
            return resp

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, records, raw = fetch_site("https://example.com", scrape_depth="deep")

        from duplo.url_canon import canonicalize_url

        # Original URL is key, not redirect target
        assert canonicalize_url("https://example.com/old-page") in raw
        assert raw[canonicalize_url("https://example.com/old-page")] == redirected_html
        # Redirect target is NOT a separate key (no fetch was made for it)
        assert canonicalize_url("https://example.com/new-page") not in raw
        # Both pages have records
        assert len(records) == 2
        assert len(raw) == 2

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
            if "/redir" in url:
                resp.text = redir_html
                resp.url = "https://example.com/final"
            else:
                resp.text = seed_html
                resp.url = url
            return resp

        with patch("duplo.fetcher.httpx.get", side_effect=fake_get):
            _text, _ex, _st, _rec, raw = fetch_site("https://example.com", scrape_depth="deep")

        # /final should NOT be fetched — the redirect marked it visited
        final_fetches = [u for u in fetch_calls if "/final" in u]
        assert final_fetches == []


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
