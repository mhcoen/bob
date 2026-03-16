"""Extract a structured feature list from scraped product content using Claude."""

from __future__ import annotations

import json
from dataclasses import dataclass

from duplo.claude_cli import query

_SYSTEM = """\
You are a product analyst. Given scraped text from a product website, extract a
structured list of the product's features. Focus on what the product actually
does—its capabilities, integrations, and notable behaviours—not on marketing
copy or company information.

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
  "name"        – short feature name (3-6 words)
  "description" – one-sentence description of what the feature does
  "category"    – one of: core, ui, integrations, api, security, other

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
) -> list[Feature]:
    """Return a structured feature list extracted from *scraped_text*.

    Uses ``claude -p`` to analyse the content. Truncates input to
    *_MAX_CONTENT_CHARS* characters to stay within context limits.

    If *existing_names* is provided, the extraction prompt instructs
    the LLM to reuse those names for features that match existing
    ones rather than inventing new names. This prevents near-duplicate
    features from accumulating across runs.

    Args:
        scraped_text: Raw text scraped from a product website.
        existing_names: Feature names already in duplo.json (optional).

    Returns:
        List of :class:`Feature` objects. Empty list if nothing could be extracted.
    """
    content = scraped_text[:_MAX_CONTENT_CHARS]
    system = _SYSTEM
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
    prompt = f"Extract features from this product website content:\n\n{content}"
    raw = query(prompt, system=system)
    return _parse_features(raw)


def _parse_features(raw: str) -> list[Feature]:
    """Parse a JSON array of feature objects from *raw*.

    Tolerates markdown code fences (``` or ```json) wrapping the JSON.
    Returns an empty list if parsing fails.
    """
    text = raw
    fence_pos = text.find("```")
    if fence_pos != -1:
        text = text[fence_pos:]
        lines = text.splitlines()
        # strip opening fence
        lines = lines[1:]
        # strip closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
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
