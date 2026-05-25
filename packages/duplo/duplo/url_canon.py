"""URL canonicalization for duplo.

Normalizes URLs to a canonical form so that user-authored URLs,
fetcher post-redirect URLs, and href-extracted URLs compare equal.
"""

from urllib.parse import urlparse, urlunparse

_DEFAULT_PORTS = {"http": 80, "https": 443}


def canonicalize_url(url: str) -> str:
    """Normalize a URL to its canonical duplo form.

    1. Lowercase scheme and host.
    2. Strip default ports (80 on http, 443 on https).
    3. Strip fragment (#section).
    4. Strip trailing slash from ALL paths INCLUDING root.

    Preserves query strings.
    """
    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname or ""
    hostname = hostname.lower()

    # Reconstruct netloc: strip default port
    port = parsed.port
    if port and _DEFAULT_PORTS.get(scheme) == port:
        port = None

    if port:
        netloc = f"{hostname}:{port}"
    else:
        netloc = hostname

    # Strip trailing slash from path (including root "/")
    path = parsed.path.rstrip("/")

    # Strip fragment
    fragment = ""

    return urlunparse((scheme, netloc, path, parsed.params, parsed.query, fragment))
