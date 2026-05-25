"""Tests for duplo.url_canon — URL canonicalization."""

from duplo.url_canon import canonicalize_url


class TestLowercaseSchemeAndHost:
    def test_uppercase_scheme(self):
        assert canonicalize_url("HTTPS://example.com/path") == "https://example.com/path"

    def test_uppercase_host(self):
        assert canonicalize_url("https://EXAMPLE.COM/path") == "https://example.com/path"

    def test_mixed_case(self):
        assert canonicalize_url("HTTP://Numi.App/Docs") == "http://numi.app/Docs"


class TestStripDefaultPorts:
    def test_strip_https_443(self):
        assert canonicalize_url("https://a.com:443/path") == "https://a.com/path"

    def test_strip_http_80(self):
        assert canonicalize_url("http://a.com:80/path") == "http://a.com/path"

    def test_preserve_non_default_port(self):
        assert canonicalize_url("https://a.com:8443/") == "https://a.com:8443"

    def test_preserve_http_443(self):
        """443 is not the default for http, so keep it."""
        assert canonicalize_url("http://a.com:443/path") == "http://a.com:443/path"


class TestStripFragment:
    def test_strip_fragment(self):
        assert canonicalize_url("https://a.com/docs#section") == "https://a.com/docs"

    def test_strip_fragment_only(self):
        assert canonicalize_url("https://a.com/#top") == "https://a.com"


class TestStripTrailingSlash:
    def test_root_path_slash_stripped(self):
        assert canonicalize_url("https://a.com/") == "https://a.com"

    def test_non_root_path_slash_stripped(self):
        assert canonicalize_url("https://a.com/docs/") == "https://a.com/docs"

    def test_no_trailing_slash_unchanged(self):
        assert canonicalize_url("https://a.com/docs") == "https://a.com/docs"

    def test_host_only_no_slash(self):
        assert canonicalize_url("https://a.com") == "https://a.com"


class TestQueryStringPreserved:
    def test_query_with_root_slash(self):
        assert canonicalize_url("https://a.com/?q=1") == "https://a.com?q=1"

    def test_query_with_path(self):
        assert (
            canonicalize_url("https://a.com/search?q=hello&lang=en")
            == "https://a.com/search?q=hello&lang=en"
        )


class TestAlreadyCanonical:
    def test_already_canonical(self):
        url = "https://example.com/path?q=1"
        assert canonicalize_url(url) == url


class TestCombinedRules:
    def test_all_rules_at_once(self):
        result = canonicalize_url("HTTPS://EXAMPLE.COM:443/docs/#section")
        assert result == "https://example.com/docs"

    def test_combined_with_query(self):
        result = canonicalize_url("HTTP://A.COM:80/api/?key=val#frag")
        assert result == "http://a.com/api?key=val"
