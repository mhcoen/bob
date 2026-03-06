"""Validate that a URL points to a single clear product."""

from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from duplo.fetcher import fetch_text

_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM = """\
You are a product analyst. Given text scraped from a URL, determine whether
it represents a single, clearly identifiable product or software project,
or whether it is a company homepage / portfolio page listing multiple
distinct products.

Return ONLY a JSON object with these fields:
  "single_product" – true if the page is about one specific product, false
                     if it lists multiple distinct products or is a company
                     portfolio page
  "product_name"   – name of the product (if single_product is true), or
                     empty string
  "products"       – if single_product is false, a list of product names
                     found on the page (up to 10). Empty list if
                     single_product is true.
  "reason"         – one-sentence explanation of your assessment

A page is "single product" if it focuses on one tool, library, service,
or application—even if that product has many features. A page is "multiple
products" if it showcases a suite or portfolio of separate, independently
named products (e.g. "Google Cloud" listing Compute Engine, BigQuery, etc.).
"""

_MAX_CONTENT_CHARS = 30_000


@dataclass
class ValidationResult:
    """Result of validating whether a URL points to a single product."""

    single_product: bool
    product_name: str
    products: list[str]
    reason: str


def validate_product_url(
    url: str,
    *,
    client: anthropic.Anthropic | None = None,
    text: str | None = None,
) -> ValidationResult:
    """Check whether *url* points to a single product or a multi-product page.

    Args:
        url: The URL to validate.
        client: Optional Anthropic client; created if omitted.
        text: Pre-fetched page text. If not provided, fetches the URL.

    Returns:
        A :class:`ValidationResult` describing what was found.
    """
    if client is None:
        client = anthropic.Anthropic()

    if text is None:
        text = fetch_text(url)

    content = text[:_MAX_CONTENT_CHARS]

    message = client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        system=_SYSTEM,
        messages=[
            {
                "role": "user",
                "content": (f"Analyze this page scraped from {url}:\n\n{content}"),
            }
        ],
    )

    raw = message.content[0].text.strip()
    return _parse_result(raw)


def _parse_result(raw: str) -> ValidationResult:
    """Parse a JSON validation result from *raw*.

    Tolerates markdown code fences. Falls back to single_product=True
    if parsing fails (benefit of the doubt).
    """
    text = raw
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

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
    )
