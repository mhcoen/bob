"""Match unannotated completed tasks to features using Claude."""

from __future__ import annotations

import json

from duplo.claude_cli import query
from duplo.extractor import Feature
from duplo.planner import CompletedTask
from duplo.saver import save_feature_status, save_features

_SYSTEM = """\
You are a feature tracker. Given a list of completed tasks and a list of known
features, determine which tasks correspond to which features.

For each task, either:
1. Match it to an existing feature by name (exact match from the feature list).
2. Mark it as "new" if it represents genuinely new functionality not covered
   by any existing feature. Provide a short name and one-sentence description.
3. Mark it as "none" if it is a scaffolding, structural, or housekeeping task
   that does not implement any user-facing feature.

Return ONLY a JSON array with one object per task. Each object must have:
  "task_index" – zero-based index of the task in the input list
  "match"      – one of: "existing", "new", "none"
  "feature"    – for "existing": the exact feature name from the list
                 for "new": a short name (3-6 words) for the new feature
                 for "none": null
  "description" – for "new": a one-sentence description; otherwise null
  "category"    – for "new": one of core, ui, integrations, api, security, other;
                  otherwise null

Example:
[
  {"task_index": 0, "match": "existing", "feature": "Dark mode", "description": null, "category": null},
  {"task_index": 1, "match": "new", "feature": "Export to CSV", "description": "Export data tables to CSV files.", "category": "core"},
  {"task_index": 2, "match": "none", "feature": null, "description": null, "category": null}
]
"""


def match_unannotated_tasks(
    tasks: list[CompletedTask],
    features: list[Feature],
    phase_label: str,
    *,
    target_dir: str = ".",
) -> tuple[list[str], list[str]]:
    """Match unannotated tasks to features via a single ``claude -p`` call.

    Tasks with ``[feat: ...]`` or ``[fix: ...]`` annotations are skipped
    (they should be handled by :func:`mark_implemented_features` and
    :func:`resolve_completed_fixes` instead).

    Args:
        tasks: Completed task objects from :func:`parse_completed_tasks`.
        features: Full feature list from duplo.json.
        phase_label: Phase label string (e.g. ``"Phase 1"``).
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Tuple of (matched_feature_names, new_feature_names) where
        matched are existing features marked as implemented, and new
        are freshly added feature entries.
    """
    unannotated = [t for t in tasks if not t.features and not t.fixes]
    if not unannotated:
        return ([], [])

    feature_list_text = "\n".join(f"- {f.name}: {f.description}" for f in features)
    task_list_text = "\n".join(f"{i}. {t.text}" for i, t in enumerate(unannotated))

    prompt = (
        f"Known features:\n{feature_list_text}\n\n"
        f"Completed tasks:\n{task_list_text}\n\n"
        f"Match each task to a feature or classify it."
    )

    raw = query(prompt, system=_SYSTEM)
    matches = _parse_matches(raw, len(unannotated))

    matched: list[str] = []
    new: list[str] = []

    for entry in matches:
        kind = entry.get("match")
        if kind == "existing":
            name = entry.get("feature", "")
            if not name:
                continue
            try:
                save_feature_status(name, "implemented", phase_label, target_dir=target_dir)
                matched.append(name)
            except ValueError:
                continue
        elif kind == "new":
            name = entry.get("feature", "")
            desc = entry.get("description", "")
            cat = entry.get("category", "other")
            if not name or not desc:
                continue
            feat = Feature(
                name=name,
                description=desc,
                category=cat,
                status="implemented",
                implemented_in=phase_label,
            )
            save_features([feat], target_dir=target_dir)
            try:
                save_feature_status(name, "implemented", phase_label, target_dir=target_dir)
            except ValueError:
                pass
            new.append(name)

    return (matched, new)


def _parse_matches(raw: str, expected_count: int) -> list[dict]:
    """Parse a JSON array of match objects from *raw*.

    Tolerates markdown code fences wrapping the JSON.
    Returns an empty list if parsing fails.
    """
    text = raw
    fence_pos = text.find("```")
    if fence_pos != -1:
        text = text[fence_pos:]
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    results: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        match = item.get("match", "")
        if match not in ("existing", "new", "none"):
            continue
        results.append(item)

    return results
