"""Validate that a URL points to a single clear product."""

from __future__ import annotations

import json
from dataclasses import dataclass

from duplo.claude_cli import query
from duplo.fetcher import fetch_text
from duplo.parsing import strip_fences

_SYSTEM = """\
You are a product analyst. Given text scraped from a URL, determine whether
it represents a single, clearly identifiable product or software project,
a company homepage / portfolio page listing multiple distinct products,
or a landing page with unclear product boundaries.

Return ONLY a JSON object with these fields:
  "single_product"       – true if the page is about one specific product,
                           false otherwise
  "unclear_boundaries"   – true if the page is a landing page or marketing
                           site where it is hard to tell what the specific
                           product is — the page is too vague, generic, or
                           broad to identify a concrete product to duplicate.
                           false if the product(s) are clearly identifiable.
  "product_name"         – name of the product (if single_product is true),
                           or empty string
  "products"             – if single_product is false and unclear_boundaries
                           is false, a list of product names found on the
                           page (up to 10). Empty list otherwise.
  "reason"               – one-sentence explanation of your assessment

A page is "single product" if it focuses on one tool, library, service,
or application—even if that product has many features. A page is "multiple
products" if it showcases a suite or portfolio of separate, independently
named products (e.g. "Google Cloud" listing Compute Engine, BigQuery, etc.).
A page has "unclear boundaries" if it is a generic landing page, platform
overview, or marketing site where the product boundaries are vague — you
cannot clearly name what specific product someone would duplicate from it
(e.g. a consulting firm's homepage, a generic "AI platform" page with no
concrete product, or a page that describes capabilities without naming a
specific tool or service).
"""

_MAX_CONTENT_CHARS = 30_000


@dataclass
class ValidationResult:
    """Result of validating whether a URL points to a single product."""

    single_product: bool
    product_name: str
    products: list[str]
    reason: str
    unclear_boundaries: bool = False


def validate_product_url(
    url: str,
    *,
    text: str | None = None,
) -> ValidationResult:
    """Check whether *url* points to a single product or a multi-product page.

    Args:
        url: The URL to validate.
        text: Pre-fetched page text. If not provided, fetches the URL.

    Returns:
        A :class:`ValidationResult` describing what was found.
    """
    if text is None:
        text = fetch_text(url)

    content = text[:_MAX_CONTENT_CHARS]
    prompt = f"Analyze this page scraped from {url}:\n\n{content}"
    raw = query(prompt, system=_SYSTEM)
    return _parse_result(raw)


def _parse_result(raw: str) -> ValidationResult:
    """Parse a JSON validation result from *raw*.

    Tolerates markdown code fences. Falls back to single_product=True
    if parsing fails (benefit of the doubt).
    """
    text = strip_fences(raw)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ValidationResult(
            single_product=True,
            product_name="",
            products=[],
            reason="Could not parse validation response.",
        )

    if not isinstance(data, dict):
        return ValidationResult(
            single_product=True,
            product_name="",
            products=[],
            reason="Unexpected response format.",
        )

    return ValidationResult(
        single_product=bool(data.get("single_product", True)),
        product_name=str(data.get("product_name", "")),
        products=[str(p) for p in data.get("products", [])],
        reason=str(data.get("reason", "")),
        unclear_boundaries=bool(data.get("unclear_boundaries", False)),
    )
