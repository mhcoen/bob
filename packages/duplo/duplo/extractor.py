"""Extract a structured feature list from scraped product content using Claude."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from duplo.claude_cli import ClaudeCliError, query
from duplo.diagnostics import record_failure
from duplo.parsing import extract_json

_SYSTEM = """\
You are a product analyst. Given text from product sources (websites,
documentation, PDFs, and other reference materials), extract a structured list
of the product's features. Focus on what the product actually does\u2014its
capabilities, integrations, and notable behaviours\u2014not on marketing copy or
company information.

CRITICAL RULES:
1. Only extract features that the product DEMONSTRABLY OFFERS based on the text.
   A feature must be explicitly described as something the product does or
   provides. Do not infer features from passing mentions, testimonials, or
   comparisons to other products.
2. Do NOT extract features that are merely mentioned in passing (e.g. "works
   great alongside iCloud" does not mean the product offers iCloud sync).
3. Do NOT extract features of the PLATFORM or ECOSYSTEM the product runs on.
   Only extract features of the product itself.
4. Do NOT hallucinate features that seem plausible but are not described in
   the text. If the text does not explicitly say the product does something,
   do not list it.
5. Do NOT extract marketing claims, company values, or business model details
   as features (e.g. "free to use", "trusted by thousands" are not features).
6. When in doubt, OMIT the feature. It is far better to return a short,
   accurate list than a long list with invented features.

Return ONLY a JSON array. Each element must be an object with these fields:
  "name"        \u2013 short feature name (3-6 words)
  "description" \u2013 one-sentence description of what the feature does
  "category"    \u2013 one of: core, ui, integrations, api, security, other

Example output (do not include in your response):
[
  {"name": "Real-time collaboration", "description": "Multiple users can edit the same document simultaneously.", "category": "core"},
  {"name": "REST API", "description": "Full CRUD access to all resources via a JSON REST API.", "category": "api"}
]
"""

_MAX_CONTENT_CHARS = 60_000


@dataclass
class Feature:
    name: str
    description: str
    category: str
    status: str = "pending"
    implemented_in: str = ""


def extract_features(
    scraped_text: str,
    existing_names: list[str] | None = None,
    *,
    spec_text: str = "",
    scope_include: list[str] | None = None,
) -> list[Feature]:
    """Return a structured feature list extracted from *scraped_text*.

    Uses ``claude -p`` to analyse the content. Truncates input to
    *_MAX_CONTENT_CHARS* characters to stay within context limits.

    *scraped_text* is the concatenation of text from all scrapeable
    sources (websites, documentation, PDFs, and other reference
    materials).  The caller is responsible for composing this text
    from all sources before calling this function.

    If *existing_names* is provided, the extraction prompt instructs
    the LLM to reuse those names for features that match existing
    ones rather than inventing new names. This prevents near-duplicate
    features from accumulating across runs.

    If *spec_text* is provided, it is injected into the system prompt
    so the LLM can use the user's stated intent to guide extraction.

    If *scope_include* is provided, unmatched required scope items are
    synthesized as features so a required feature present in no scraped
    Source is never dropped.

    Exclusion is deliberately NOT handled here. ``scope_exclude`` is an
    orchestrator-level concern: the caller filters excluded features via
    :func:`_matches_excluded` after this function returns. Do not add a
    ``scope_exclude`` parameter back to this signature -- a parameter that
    silently does nothing is a trap for callers who trust it.

    Args:
        scraped_text: Combined text from all scrapeable product sources.
        existing_names: Feature names already in duplo.json (optional).
        spec_text: Product specification text (optional).
        scope_include: Feature names the user requires (optional).

    Returns:
        List of :class:`Feature` objects. Empty list if nothing could
        be extracted.
    """
    content = scraped_text[:_MAX_CONTENT_CHARS]
    system = _SYSTEM
    if spec_text:
        system += (
            "\n\nThe user has provided a product specification that "
            "describes their intent. Use it to guide your extraction \u2014 "
            "prioritise features the spec mentions, respect any scope "
            "constraints, and use the spec\u2019s terminology when it aligns "
            "with what the scraped content describes.\n\n"
            f"{spec_text}"
        )
    if existing_names:
        names_list = ", ".join(f'"{n}"' for n in existing_names)
        system += (
            "\n\nIMPORTANT: These features have already been extracted "
            "from previous runs. If you find a feature that matches "
            "one of these (same concept, even if worded differently), "
            "use the EXACT existing name instead of inventing a new "
            "one. Only create a new name for genuinely new features "
            "not covered by any existing entry.\n"
            f"Existing features: [{names_list}]"
        )
    prompt = f"Extract features from this product content:\n\n{content}"
    try:
        raw = query(prompt, system=system, call_site="extract_features")
    except ClaudeCliError:
        # Even when extraction fails entirely, user scope includes are
        # authoritative and must still surface as features.
        return _reconcile_scope_include([], scope_include)
    features = _parse_features(raw)

    # User spec scope is authoritative; scraped Sources only add. Any
    # scope_include item the LLM did not surface is synthesized below so a
    # required feature present in no scraped Source is never dropped.
    # scope_exclude filtering is applied at the orchestrator level after
    # this function returns; see _matches_excluded and its pipeline callers.
    features = _reconcile_scope_include(features, scope_include)

    return features


def _reconcile_scope_include(
    features: list[Feature],
    scope_include: list[str] | None,
) -> list[Feature]:
    """Append a synthesized :class:`Feature` for unmatched scope includes.

    For every name in *scope_include* that has no matching feature in
    *features* (case-insensitive name match), a deterministic ``Feature``
    sourced from the spec item is appended. User spec scope is
    authoritative, so a required item present in no scraped Source is
    never dropped.

    The original ordering of *features* is preserved; synthesized
    features are appended in *scope_include* order. Blank include items
    are ignored, and duplicate include items synthesize only once.
    """
    if not scope_include:
        return features

    have = {f.name.strip().lower() for f in features}
    result = list(features)
    for item in scope_include:
        name = item.strip()
        key = name.lower()
        if not key or key in have:
            continue
        have.add(key)
        result.append(
            Feature(
                name=name,
                description=f"User-specified scope item from SPEC: {name}.",
                category="core",
            )
        )
    return result


def _matches_excluded(
    feature: Feature,
    scope_exclude: list[str],
) -> bool:
    """Return True if *feature* matches any term in *scope_exclude*.

    Matching uses case-insensitive word-boundary regex (``\\b``).
    Multi-word terms match as a contiguous word sequence.  Checks both
    ``feature.name`` and ``feature.description``.

    When a match is found, emits a diagnostic via
    :func:`~duplo.diagnostics.record_failure` so false positives are
    visible in the run summary.
    """
    for term in scope_exclude:
        escaped = re.escape(term)
        # Use \b only where the term edge is a word character; otherwise
        # use a zero-width assertion that the adjacent character is not a
        # word character (or is start/end of string).
        left = r"\b" if re.match(r"\w", term) else r"(?<!\w)"
        right = r"\b" if re.search(r"\w$", term) else r"(?!\w)"
        pattern = re.compile(left + escaped + right, re.IGNORECASE)
        if pattern.search(feature.name) or pattern.search(feature.description):
            record_failure(
                "extractor:scope_exclude",
                "io",
                (f"scope_exclude '{term}' matched feature '{feature.name}'; dropped"),
            )
            return True
    return False


def _parse_features(raw: str) -> list[Feature]:
    """Parse a JSON array of feature objects from *raw*.

    Tolerates markdown code fences (``` or ```json) wrapping the JSON.
    Returns an empty list if parsing fails.
    """
    try:
        data = json.loads(extract_json(raw))
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    features: list[Feature] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        description = str(item.get("description", "")).strip()
        category = str(item.get("category", "other")).strip()
        if name and description:
            features.append(Feature(name=name, description=description, category=category))

    return features
