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


def extract_features(scraped_text: str) -> list[Feature]:
    """Return a structured feature list extracted from *scraped_text*.

    Uses ``claude -p`` to analyse the content. Truncates input to
    *_MAX_CONTENT_CHARS* characters to stay within context limits.

    Args:
        scraped_text: Raw text scraped from a product website.

    Returns:
        List of :class:`Feature` objects. Empty list if nothing could be extracted.
    """
    content = scraped_text[:_MAX_CONTENT_CHARS]
    prompt = f"Extract features from this product website content:\n\n{content}"
    raw = query(prompt, system=_SYSTEM)
    return _parse_features(raw)


def _parse_features(raw: str) -> list[Feature]:
    """Parse a JSON array of feature objects from *raw*.

    Tolerates markdown code fences (``` or ```json) wrapping the JSON.
    Returns an empty list if parsing fails.
    """
    text = raw
    if text.startswith("```"):
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
