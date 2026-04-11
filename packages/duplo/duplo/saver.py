"""Save duplo selections and preferences to duplo.json in the target project."""

from __future__ import annotations

import dataclasses
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from duplo.diagnostics import record_failure
from duplo.doc_examples import CodeExample
from duplo.doc_tables import DocStructures
from duplo.extractor import Feature
from duplo.fetcher import PageRecord
from duplo.questioner import BuildPreferences

DUPLO_DIR = ".duplo"
DUPLO_JSON = ".duplo/duplo.json"
PRODUCT_JSON = ".duplo/product.json"
CLAUDE_MD = "CLAUDE.md"


def _ensure_duplo_dir(target_dir: Path | str = ".") -> Path:
    """Create the ``.duplo/`` directory inside *target_dir* if it does not exist."""
    duplo_dir = Path(target_dir) / DUPLO_DIR
    duplo_dir.mkdir(parents=True, exist_ok=True)
    return duplo_dir


def _safe_read_json(path: Path) -> dict:
    """Read *path* as JSON, returning ``{}`` if missing or corrupted."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_product(
    product_name: str,
    source_url: str,
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save the confirmed product identity to ``.duplo/product.json``.

    Stores the product name and source URL so subsequent runs skip
    re-validation and re-confirmation.

    Args:
        product_name: Confirmed product name.
        source_url: Validated product URL (may be empty).
        target_dir: Directory containing ``.duplo/``.

    Returns:
        Path to the written file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / PRODUCT_JSON).resolve()
    data = {"product_name": product_name, "source_url": source_url}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def load_product(
    *,
    target_dir: Path | str = ".",
) -> tuple[str, str] | None:
    """Load the confirmed product identity from ``.duplo/product.json``.

    Returns ``(product_name, source_url)`` if the file exists,
    or ``None`` if not found.
    """
    path = (Path(target_dir) / PRODUCT_JSON).resolve()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return data.get("product_name", ""), data.get("source_url", "")


_CLAUDE_MD_CONTENT = """\
# Visual verification

Use `appshot` to capture screenshots of the running application.
`appshot` captures a macOS app window by process name. It works
with any app that puts a window on screen.

```bash
appshot "AppName" screenshots/current/main.png
appshot "AppName" screenshots/current/main.png --launch ./run.sh
appshot "AppName" screenshots/current/main.png --wait 2
```

Always use appshot for visual verification. Do not reinvent
screenshot capture with other approaches.

After capturing, examine the screenshot to confirm:
- The window renders correctly with expected content
- Layout and spacing are reasonable
- No blank screens, missing elements, or rendering artifacts
- Text is readable and properly styled

Do not delete `.duplo/references/` -- those are the reference
frames and images duplo uses for visual comparison.

# Swift SPM + SwiftUI

SPM executables using SwiftUI do not automatically show windows.
Every SwiftUI app built with SPM must include this in the App
struct init:

```swift
init() {
    NSApplication.shared.setActivationPolicy(.regular)
    NSApplication.shared.activate(ignoringOtherApps: true)
}
```

Use `swift build --disable-sandbox` and `swift test --disable-sandbox`
inside Claude Code's sandbox.

# Debugging

When something crashes or behaves unexpectedly, find and read
the actual error output first. Check crash reports
(~/Library/Logs/DiagnosticReports/ on macOS), stderr, log
files, tracebacks. Do not guess from source code alone.
After fixing, reproduce the failure and verify it is gone.
"""


def save_selections(
    source_url: str,
    features: list[Feature],
    preferences: BuildPreferences,
    *,
    app_name: str = "",
    code_examples: list[CodeExample] | None = None,
    doc_structures: DocStructures | None = None,
    target_dir: Path | str = ".",
) -> Path:
    """Write selected features and build preferences to *duplo.json*.

    The file is created or overwritten in *target_dir*.  Returns the path
    to the written file.

    Args:
        source_url: URL of the product being duplicated.
        features: Selected features to include in the build.
        preferences: Build platform and language preferences.
        app_name: macOS process name used by appshot for screenshot capture.
        code_examples: Optional extracted code examples to persist.
        doc_structures: Optional extracted doc structures to persist.
        target_dir: Directory in which to write ``duplo.json``.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = {
        "source_url": source_url,
        "app_name": app_name,
        "features": [dataclasses.asdict(f) for f in features],
        "preferences": dataclasses.asdict(preferences),
    }
    if code_examples is not None:
        data["code_examples"] = [dataclasses.asdict(ex) for ex in code_examples]
    if doc_structures is not None:
        data["doc_structures"] = _serialize_doc_structures(doc_structures)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def append_phase_to_history(
    plan_content: str,
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Append a completed phase entry to the ``phases`` list in *duplo.json*.

    The entry records the phase title (extracted from the ``# Phase N`` heading),
    the full plan content, and an ISO 8601 completion timestamp.  Returns the
    path to the updated file.

    Args:
        plan_content: Markdown content of the completed PLAN.md.
        target_dir: Directory containing ``duplo.json``.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)

    match = re.search(
        r"^#\s*.*?((?:Phase|Stage)\s+\d+[^\n]*)", plan_content, re.IGNORECASE | re.MULTILINE
    )
    phase_title = match.group(1).strip() if match else "Unknown phase"

    entry = {
        "phase": phase_title,
        "plan": plan_content,
        "completed_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    data.setdefault("phases", []).append(entry)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def save_feedback(
    text: str,
    *,
    after_phase: str = "",
    target_dir: Path | str = ".",
) -> Path:
    """Append a feedback entry to the ``feedback`` list in *duplo.json*.

    Records the feedback text, which phase it follows, and an ISO 8601
    timestamp.  Creates *duplo.json* if it does not exist.  Returns the path
    to the updated file.

    Args:
        text: Feedback text collected from the user.
        after_phase: Label of the phase this feedback follows (e.g. ``"Phase 1"``).
        target_dir: Directory containing ``duplo.json``.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)

    entry = {
        "after_phase": after_phase,
        "text": text,
        "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    data.setdefault("feedback", []).append(entry)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def save_screenshot_feature_map(
    mapping: dict[str, list[str]],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Store a screenshot→features mapping in *duplo.json*.

    Writes ``{"screenshot_features": mapping}`` into *duplo.json*, preserving
    all existing keys.  Creates the file if it does not exist.

    Args:
        mapping: Dict mapping screenshot filename (basename) to a list of
            feature names visible on that page.
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    data["screenshot_features"] = mapping
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def save_roadmap(
    roadmap: list[dict],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save the roadmap to duplo.json."""
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    data["roadmap"] = roadmap
    first_phase = roadmap[0].get("phase", 0) if roadmap else 0
    data["current_phase"] = first_phase
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def advance_phase(
    *,
    target_dir: Path | str = ".",
) -> int:
    """Increment current_phase in duplo.json. Returns new phase number."""
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    if not path.exists():
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 1
    current = data.get("current_phase", 0)
    data["current_phase"] = current + 1
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return current + 1


def get_current_phase(
    *,
    target_dir: Path | str = ".",
) -> tuple[int, dict | None]:
    """Return (phase_number, phase_dict) for the current phase.

    Returns (0, None) if no roadmap exists.
    """
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    if not path.exists():
        return (0, None)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return (0, None)
    roadmap = data.get("roadmap", [])
    current = data.get("current_phase", 0)
    for phase in roadmap:
        if phase.get("phase") == current:
            return (current, phase)
    return (current, None)


def _deduplicate_features_llm(
    candidates: list[str],
    existing: list[str],
) -> dict[str, str]:
    """Use an LLM to identify which candidates duplicate existing features.

    Sends a single batch query to ``claude -p`` with all candidate names
    and existing names. Returns a dict mapping each duplicate candidate
    name to the existing name it matches. Candidates that are genuinely
    new are not included in the returned dict.

    Falls back to an empty dict (no dedup) if the LLM call fails.
    """
    if not candidates or not existing:
        return {}

    from duplo.claude_cli import ClaudeCliError, query

    existing_list = json.dumps(existing)
    candidates_list = json.dumps(candidates)
    system = (
        "You are deduplicating a feature list. Given a list of existing "
        "feature names and a list of candidate new feature names, identify "
        "which candidates are duplicates of existing features (same concept, "
        "different wording). Return ONLY a JSON object mapping each duplicate "
        "candidate name to the existing name it matches. Candidates that are "
        "genuinely new (not covered by any existing feature) should NOT appear "
        "in the output. Return {} if no duplicates are found."
    )
    prompt = f"Existing features:\n{existing_list}\n\nCandidate features:\n{candidates_list}"
    try:
        raw = query(prompt, system=system, model="haiku")
    except ClaudeCliError as exc:
        record_failure(
            "saver:_deduplicate_features_llm",
            "llm",
            f"Feature dedup LLM call failed: {exc}",
        )
        return {}

    text = raw.strip()
    fence_pos = text.find("```")
    if fence_pos != -1:
        text = text[fence_pos:]
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return {}

    if not isinstance(result, dict):
        return {}
    return {str(k): str(v) for k, v in result.items()}


def _find_duplicate_groups(names: list[str]) -> list[list[str]]:
    """Use an LLM to find groups of semantically identical feature names.

    Returns a list of groups, where each group is a list of names that
    refer to the same concept. Only groups with 2+ members are returned.
    Falls back to an empty list if the LLM call fails.
    """
    if len(names) < 2:
        return []

    from duplo.claude_cli import ClaudeCliError, query

    names_json = json.dumps(names)
    system = (
        "You are deduplicating a feature list. Given a list of feature "
        "names, identify groups of names that refer to the SAME concept "
        "(e.g. 'Custom vocabulary / glossary' and 'Custom vocabulary', "
        "or 'Bring-your-own API keys' and 'Bring your own API keys'). "
        "Return ONLY a JSON array of arrays, where each inner array is "
        "a group of 2+ names that are semantically identical. Names that "
        "are unique should NOT appear. Return [] if no duplicates exist."
    )
    prompt = f"Feature names:\n{names_json}"
    try:
        raw = query(prompt, system=system, model="haiku")
    except ClaudeCliError as exc:
        record_failure(
            "saver:_find_duplicate_groups",
            "llm",
            f"Duplicate group detection LLM call failed: {exc}",
        )
        return []

    text = raw.strip()
    fence_pos = text.find("```")
    if fence_pos != -1:
        text = text[fence_pos:]
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return []

    if not isinstance(result, list):
        return []

    groups: list[list[str]] = []
    for item in result:
        if isinstance(item, list) and len(item) >= 2:
            group = [str(n) for n in item]
            groups.append(group)
    return groups


def _merge_duplicate_group(
    features: list[dict],
    group: list[str],
) -> str | None:
    """Merge a group of duplicate feature dicts, keeping the best name.

    Picks the longest name as "most descriptive". Preserves
    ``status: "implemented"`` if any member has it. Returns the name
    that was kept, or ``None`` if no members were found.
    """
    name_set = set(group)
    members = [f for f in features if f["name"] in name_set]
    if len(members) < 2:
        return None

    # Keep the longest name (most descriptive).
    best = max(members, key=lambda f: len(f["name"]))

    # Preserve implemented status from any member.
    any_implemented = any(f.get("status") == "implemented" for f in members)
    if any_implemented and best.get("status") != "implemented":
        impl_member = next(f for f in members if f.get("status") == "implemented")
        best["status"] = "implemented"
        best["implemented_in"] = impl_member.get("implemented_in", "")

    # Remove all members except the best from the feature list.
    remove_names = name_set - {best["name"]}
    features[:] = [f for f in features if f["name"] not in remove_names]
    return best["name"]


def _propagate_implemented_status(features: list[dict]) -> list[str]:
    """Mark pending features as implemented if semantically identical to one.

    Compares all pending feature names against all implemented feature names
    using an LLM call. For each match, sets the pending feature's status to
    ``"implemented"`` and copies the ``implemented_in`` value from its match.

    Returns the list of feature names that were newly marked as implemented.
    """
    implemented = [f for f in features if f.get("status") == "implemented"]
    pending = [f for f in features if f.get("status") != "implemented"]
    if not implemented or not pending:
        return []

    impl_names = [f["name"] for f in implemented]
    pend_names = [f["name"] for f in pending]

    from duplo.claude_cli import ClaudeCliError, query

    impl_json = json.dumps(impl_names)
    pend_json = json.dumps(pend_names)
    system = (
        "You are deduplicating a feature list. Given a list of IMPLEMENTED "
        "feature names and a list of PENDING feature names, identify which "
        "pending features are semantically identical to an implemented feature "
        "(same concept, different wording — e.g. 'Local offline transcription' "
        "and 'Local on-device transcription'). Return ONLY a JSON object "
        "mapping each matching pending name to the implemented name it matches. "
        "Pending features that are genuinely different should NOT appear. "
        "Return {} if no matches are found."
    )
    prompt = f"Implemented features:\n{impl_json}\n\nPending features:\n{pend_json}"
    try:
        raw = query(prompt, system=system, model="haiku")
    except ClaudeCliError as exc:
        record_failure(
            "saver:_propagate_implemented_status",
            "llm",
            f"Status propagation LLM call failed: {exc}",
        )
        return []

    text = raw.strip()
    fence_pos = text.find("```")
    if fence_pos != -1:
        text = text[fence_pos:]
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return []

    if not isinstance(result, dict):
        return []

    # Build lookup for implemented features by name.
    impl_by_name = {f["name"]: f for f in implemented}
    marked: list[str] = []
    for pend_name, impl_name in result.items():
        pend_name = str(pend_name)
        impl_name = str(impl_name)
        if impl_name not in impl_by_name:
            continue
        # Find the pending feature dict and update it.
        for feat in features:
            if feat["name"] == pend_name and feat.get("status") != "implemented":
                impl_feat = impl_by_name[impl_name]
                feat["status"] = "implemented"
                feat["implemented_in"] = impl_feat.get("implemented_in", "")
                marked.append(pend_name)
                break
    return marked


def save_features(
    features: list[Feature],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Merge *features* into the ``features`` list in *duplo.json*.

    Adds any features whose name is not already present, using an LLM
    to detect semantic duplicates (e.g. "CLI tool" and "Command-line
    interface (CLI)"). After merging, runs a second LLM pass over all
    feature names to find and merge near-duplicates that accumulated
    across runs. Returns the path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    existing = data.get("features", [])
    existing_names = {f["name"] for f in existing}

    # Quick pass: filter out exact matches.
    candidates = [f for f in features if f.name not in existing_names]

    if candidates:
        # LLM pass: detect semantic duplicates in a single batch call.
        candidate_names = [f.name for f in candidates]
        duplicates = _deduplicate_features_llm(candidate_names, list(existing_names))

        for feat in candidates:
            if feat.name in duplicates:
                continue
            d = dataclasses.asdict(feat)
            d.setdefault("status", "pending")
            d.setdefault("implemented_in", "")
            existing.append(d)
            existing_names.add(feat.name)

    # Post-merge pass: find and merge semantic duplicates across ALL
    # features (existing + newly added) that accumulated over runs.
    all_names = [f["name"] for f in existing]
    groups = _find_duplicate_groups(all_names)
    merged_count = 0
    for group in groups:
        kept = _merge_duplicate_group(existing, group)
        if kept is not None:
            merged_count += len(group) - 1

    if merged_count:
        print(f"Merged {merged_count} duplicate feature(s).")

    # Propagate implemented status: if a pending feature is semantically
    # identical to an implemented one, mark it implemented too.
    propagated = _propagate_implemented_status(existing)
    if propagated:
        print(f"Marked {len(propagated)} feature(s) as implemented (duplicate of implemented).")

    data["features"] = existing
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def save_feature_status(
    name: str,
    status: str,
    implemented_in: str,
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Update the status of a feature by name in *duplo.json*.

    Finds the feature dict whose ``name`` matches and sets its ``status``
    and ``implemented_in`` fields.  Raises ``ValueError`` if no feature
    with the given name exists or if *status* is not one of the allowed
    values.

    Args:
        name: Feature name to update (must match exactly).
        status: One of ``"pending"``, ``"implemented"``, ``"partial"``.
        implemented_in: Phase label string (e.g. ``"Phase 1"``).
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.
    """
    allowed = {"pending", "implemented", "partial"}
    if status not in allowed:
        msg = f"Invalid status {status!r}; must be one of {sorted(allowed)}"
        raise ValueError(msg)

    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    features = data.get("features", [])
    for feat in features:
        if feat["name"] == name:
            feat["status"] = status
            feat["implemented_in"] = implemented_in
            break
    else:
        msg = f"No feature named {name!r}"
        raise ValueError(msg)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def mark_implemented_features(
    tasks: list,
    phase_label: str,
    *,
    target_dir: Path | str = ".",
) -> list[str]:
    """Mark features referenced in completed tasks as ``implemented``.

    Iterates over *tasks* (each a :class:`~duplo.planner.CompletedTask`),
    collects unique feature names from their ``features`` lists, and calls
    :func:`save_feature_status` for each one.  Features that do not exist
    in *duplo.json* are silently skipped.

    Args:
        tasks: Completed task objects with ``features`` attribute.
        phase_label: Phase label string (e.g. ``"Phase 1"``).
        target_dir: Directory containing ``duplo.json``.

    Returns:
        List of feature names that were successfully marked.
    """
    seen: set[str] = set()
    marked: list[str] = []
    for task in tasks:
        for name in task.features:
            if name in seen:
                continue
            seen.add(name)
            try:
                save_feature_status(name, "implemented", phase_label, target_dir=target_dir)
                marked.append(name)
            except ValueError:
                continue
    return marked


def save_issues(
    issues: list[dict],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Replace the top-level ``issues`` list in *duplo.json*.

    Each issue dict should have ``description`` and ``severity`` keys.
    Returns the path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    data["issues"] = issues
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def add_issue(
    description: str,
    severity: str = "major",
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Append a single issue to the ``issues`` list in *duplo.json*.

    Skips duplicates: if an issue with the same ``description`` already
    exists, the list is not modified.  Records an ISO 8601 timestamp.

    Args:
        description: What went wrong.
        severity: One of ``"critical"``, ``"major"``, ``"minor"``.
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.

    Raises:
        ValueError: If *severity* is not one of the allowed values.
    """
    allowed = {"critical", "major", "minor"}
    if severity not in allowed:
        msg = f"Invalid severity {severity!r}; must be one of {sorted(allowed)}"
        raise ValueError(msg)

    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    issues = data.get("issues", [])
    for existing in issues:
        if existing.get("description") == description:
            return path
    issues.append(
        {
            "description": description,
            "severity": severity,
            "added_at": datetime.now(tz=timezone.utc).isoformat(),
        }
    )
    data["issues"] = issues
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def load_issues(
    *,
    target_dir: Path | str = ".",
) -> list[dict]:
    """Load the ``issues`` list from *duplo.json*.

    Returns an empty list if no issues have been recorded.
    """
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data = _safe_read_json(path)
    return data.get("issues", [])


def clear_issues(
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Remove all issues from *duplo.json*.

    Returns the path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    data["issues"] = []
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def save_issue(
    description: str,
    source: str,
    phase: str,
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Record an implementation issue with its source and phase context.

    Appends to the ``issues`` list in *duplo.json*.  Skips duplicates:
    if an issue with the same ``description`` already exists, the list
    is not modified.  Records an ISO 8601 timestamp.

    Args:
        description: What went wrong.
        source: Where the issue was found (e.g. ``"visual comparison"``,
            ``"test failure"``).
        phase: Phase label when the issue was discovered (e.g. ``"Phase 1"``).
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    issues = data.get("issues", [])
    for existing in issues:
        if existing.get("description") == description:
            return path
    issues.append(
        {
            "description": description,
            "source": source,
            "phase": phase,
            "status": "open",
            "added_at": datetime.now(tz=timezone.utc).isoformat(),
        }
    )
    data["issues"] = issues
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_issue(
    description: str,
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Mark an issue as resolved by description.

    Finds the issue whose ``description`` matches and sets its ``status``
    to ``"resolved"`` with a ``resolved_at`` timestamp.  Raises
    ``ValueError`` if no matching issue is found.

    Args:
        description: Description of the issue to resolve (must match exactly).
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.

    Raises:
        ValueError: If no issue with the given description exists.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    issues = data.get("issues", [])
    for issue in issues:
        if issue.get("description") == description:
            issue["status"] = "resolved"
            issue["resolved_at"] = datetime.now(tz=timezone.utc).isoformat()
            break
    else:
        msg = f"No issue with description {description!r}"
        raise ValueError(msg)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_completed_fixes(
    tasks: list,
    *,
    target_dir: Path | str = ".",
) -> list[str]:
    """Resolve issues referenced in completed tasks' ``[fix: ...]`` annotations.

    Iterates over *tasks* (each a :class:`~duplo.planner.CompletedTask`),
    collects unique fix descriptions from their ``fixes`` lists, and calls
    :func:`resolve_issue` for each one.  Issues that do not exist in
    *duplo.json* are silently skipped.

    Args:
        tasks: Completed task objects with ``fixes`` attribute.
        target_dir: Directory containing ``duplo.json``.

    Returns:
        List of issue descriptions that were successfully resolved.
    """
    seen: set[str] = set()
    resolved: list[str] = []
    for task in tasks:
        for desc in task.fixes:
            if desc in seen:
                continue
            seen.add(desc)
            try:
                resolve_issue(desc, target_dir=target_dir)
                resolved.append(desc)
            except ValueError:
                continue
    return resolved


def save_code_examples(
    examples: list[CodeExample],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save extracted code examples to *duplo.json*.

    Writes ``{"code_examples": [...]}`` into *duplo.json*, preserving
    all existing keys.  Each example is stored as a dict with ``input``,
    ``expected_output``, ``source_url``, and ``language`` keys.

    Args:
        examples: List of :class:`CodeExample` objects to store.
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    data["code_examples"] = [dataclasses.asdict(ex) for ex in examples]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _example_filename(index: int, example: CodeExample) -> str:
    """Generate a filename for a code example: ``000_slug.json``."""
    first_line = example.input.split("\n", 1)[0].strip()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", first_line).strip("_")[:40].rstrip("_")
    return f"{index:03d}_{slug or 'example'}.json"


def save_examples(
    examples: list[CodeExample],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save each code example as a separate JSON file in ``.duplo/examples/``.

    Each file contains ``input``, ``expected_output``, ``source_url``, and
    ``language`` keys.  Files are named ``000_slug.json`` where the slug is
    derived from the first line of the input.  Any existing files in the
    directory are removed first so the directory always reflects the current
    set of examples.

    Args:
        examples: List of :class:`CodeExample` objects to store.
        target_dir: Directory containing ``.duplo/``.

    Returns:
        Path to the ``examples`` directory.
    """
    examples_dir = Path(target_dir) / EXAMPLES_DIR
    if examples_dir.exists():
        for existing in examples_dir.iterdir():
            if existing.suffix == ".json":
                existing.unlink()
    else:
        examples_dir.mkdir(parents=True, exist_ok=True)
    for idx, ex in enumerate(examples):
        filename = _example_filename(idx, ex)
        filepath = examples_dir / filename
        data = dataclasses.asdict(ex)
        filepath.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return examples_dir


def load_examples(
    *,
    target_dir: Path | str = ".",
) -> list[CodeExample]:
    """Load code examples from ``.duplo/examples/`` directory.

    Falls back to reading from ``duplo.json`` if the examples directory
    does not exist (backward compatibility).  Returns an empty list if
    neither source has examples.
    """
    examples_dir = Path(target_dir) / EXAMPLES_DIR
    if examples_dir.is_dir():
        examples: list[CodeExample] = []
        for filepath in sorted(examples_dir.glob("*.json")):
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                examples.append(
                    CodeExample(
                        input=data.get("input", ""),
                        expected_output=data.get("expected_output", ""),
                        source_url=data.get("source_url", ""),
                        language=data.get("language", ""),
                    )
                )
            except (json.JSONDecodeError, KeyError):
                continue
        return examples
    # Fallback: read from duplo.json for backward compatibility.
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("code_examples", [])
    except json.JSONDecodeError:
        return []
    return [
        CodeExample(
            input=ex.get("input", ""),
            expected_output=ex.get("expected_output", ""),
            source_url=ex.get("source_url", ""),
            language=ex.get("language", ""),
        )
        for ex in raw
    ]


def _serialize_doc_structures(structures: DocStructures) -> dict:
    """Convert a :class:`DocStructures` instance to a JSON-serialisable dict."""
    return {
        "feature_tables": [
            {"heading": ft.heading, "rows": ft.rows, "source_url": ft.source_url}
            for ft in structures.feature_tables
        ],
        "operation_lists": [
            {"heading": ol.heading, "items": ol.items, "source_url": ol.source_url}
            for ol in structures.operation_lists
        ],
        "unit_lists": [
            {"heading": ul.heading, "items": ul.items, "source_url": ul.source_url}
            for ul in structures.unit_lists
        ],
        "function_refs": [
            {
                "name": fr.name,
                "signature": fr.signature,
                "description": fr.description,
                "source_url": fr.source_url,
            }
            for fr in structures.function_refs
        ],
    }


def save_doc_structures(
    structures: DocStructures,
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save extracted doc structures to *duplo.json*.

    Writes ``{"doc_structures": {...}}`` into *duplo.json*, preserving
    all existing keys.  The structures dict contains ``feature_tables``,
    ``operation_lists``, ``unit_lists``, and ``function_refs``.

    Args:
        structures: :class:`DocStructures` to store.
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    data["doc_structures"] = _serialize_doc_structures(structures)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def save_reference_urls(
    records: list[PageRecord],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save reference URL records to *duplo.json*.

    Writes ``{"reference_urls": [...]}`` into *duplo.json*, preserving
    all existing keys.  Each record stores the URL, an ISO 8601 fetch
    timestamp, and a SHA-256 content hash.

    Args:
        records: List of :class:`PageRecord` objects from scraping.
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    data["reference_urls"] = [dataclasses.asdict(r) for r in records]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


EXAMPLES_DIR = ".duplo/examples"
RAW_PAGES_DIR = ".duplo/raw_pages"
REFERENCES_DIR = ".duplo/references"


def save_raw_content(
    raw_pages: dict[str, str],
    records: list[PageRecord],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save raw HTML content for each scraped page.

    Writes each page's HTML to ``.duplo/raw_pages/<content_hash>.html``
    so that re-runs can diff against what changed on the product site.
    The content hash from *records* is used as the filename, linking
    each file to its corresponding :class:`PageRecord`.

    Args:
        raw_pages: Dict mapping URL to raw HTML content.
        records: List of :class:`PageRecord` with content hashes.
        target_dir: Directory containing ``.duplo/``.

    Returns:
        Path to the ``raw_pages`` directory.
    """
    pages_dir = Path(target_dir) / RAW_PAGES_DIR
    pages_dir.mkdir(parents=True, exist_ok=True)
    for record in records:
        html = raw_pages.get(record.url)
        if html is None:
            continue
        page_path = pages_dir / f"{record.content_hash}.html"
        page_path.write_text(html, encoding="utf-8")
    return pages_dir


def save_design_requirements(
    design: dict,
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save extracted visual design requirements to *duplo.json*.

    Writes ``{"design_requirements": {...}}`` into *duplo.json*, preserving
    all existing keys.

    Args:
        design: Dict with colors, fonts, spacing, layout, components keys.
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    data["design_requirements"] = design
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def save_frame_descriptions(
    descriptions: list[dict],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save frame UI state descriptions to *duplo.json*.

    Writes ``{"frame_descriptions": [...]}`` into *duplo.json*, preserving
    all existing keys.  Each entry has ``filename``, ``state``, and ``detail``.

    Args:
        descriptions: List of dicts with ``filename``, ``state``, ``detail``.
        target_dir: Directory containing ``duplo.json``.

    Returns:
        Path to the updated file.
    """
    _ensure_duplo_dir(target_dir)
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = _safe_read_json(path)
    data["frame_descriptions"] = descriptions
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def store_accepted_frames(
    frame_descriptions: list[dict],
    *,
    target_dir: Path | str = ".",
) -> list[Path]:
    """Copy accepted frames to ``.duplo/references/`` and save descriptions.

    Each entry in *frame_descriptions* must have ``path`` (a :class:`Path`
    or string to the frame file), ``filename``, ``state``, and ``detail``.
    The frame file is copied (not moved) into ``.duplo/references/``.
    Descriptions are saved to ``duplo.json``.

    Args:
        frame_descriptions: List of dicts with ``path``, ``filename``,
            ``state``, ``detail`` keys.
        target_dir: Directory containing ``.duplo/``.

    Returns:
        List of destination paths for copied frames.
    """
    import shutil

    refs_dir = Path(target_dir) / REFERENCES_DIR
    refs_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    json_entries: list[dict] = []
    for entry in frame_descriptions:
        src = Path(entry["path"])
        if not src.exists():
            continue
        dest = refs_dir / entry["filename"]
        shutil.copy2(src, dest)
        copied.append(dest)
        json_entries.append(
            {
                "filename": entry["filename"],
                "state": entry["state"],
                "detail": entry["detail"],
            }
        )

    if json_entries:
        save_frame_descriptions(json_entries, target_dir=target_dir)

    return copied


def write_claude_md(*, target_dir: Path | str = ".") -> Path:
    """Write ``CLAUDE.md`` with appshot instructions to *target_dir*.

    If the file already exists, only sections whose headings are not
    already present are appended.  Existing content is never removed
    or replaced.  Returns the path to the written file.
    """
    path = (Path(target_dir) / CLAUDE_MD).resolve()
    if not path.exists():
        path.write_text(_CLAUDE_MD_CONTENT, encoding="utf-8")
        return path

    existing = path.read_text(encoding="utf-8")
    # Split template into sections by top-level headings (# ...).
    section_re = re.compile(r"(?=^# )", re.MULTILINE)
    template_sections = [s for s in section_re.split(_CLAUDE_MD_CONTENT) if s.strip()]

    new_sections: list[str] = []
    for section in template_sections:
        heading_match = re.match(r"^# (.+)", section)
        if not heading_match:
            continue
        heading = heading_match.group(1).strip()
        # Check if this heading already appears in the existing file.
        if re.search(rf"^# {re.escape(heading)}\s*$", existing, re.MULTILINE):
            continue
        new_sections.append(section)

    if new_sections:
        appended = "\n" + "".join(new_sections)
        path.write_text(existing.rstrip("\n") + "\n" + appended, encoding="utf-8")

    return path


_BUGS_HEADING = "## Bugs"

_PLAN_FILENAME = "PLAN.md"


def _task_body(line: str) -> str:
    """Extract the text after the checkbox prefix from a task line.

    ``- [ ] Fix: foo`` and ``- [x] Fix: foo`` both return
    ``Fix: foo``.  Non-checkbox lines return the stripped line.
    """
    stripped = line.lstrip()
    m = re.match(r"^- \[[x ]\] ", stripped)
    if m:
        return stripped[m.end() :]
    return stripped


def append_to_bugs_section(
    tasks: list[str],
    *,
    target_dir: Path | str = ".",
) -> int:
    """Append bug-fix task lines into the ``## Bugs`` section of PLAN.md.

    If PLAN.md does not exist, does nothing and returns 0.

    If a ``## Bugs`` section already exists (anywhere in the file),
    new tasks are handled with reopen-in-place semantics:

    - If the task body already exists as an unchecked ``- [ ]`` entry,
      it is skipped (no duplicate).
    - If the task body matches an existing checked ``- [x]`` entry,
      that entry is unchecked in place (reopened) rather than adding a
      new line.  This counts as an insertion.
    - Otherwise the task is appended at the end of the section.

    Existing entries are never removed or reordered.

    If no ``## Bugs`` section exists, one is created immediately after
    the intro prose / architecture note and before the first ``#``
    phase heading (i.e. the first line starting with ``# `` after the
    very first heading).  If the file has only one heading, the section
    is appended at the end.

    Args:
        tasks: Lines to insert, each should be a ``- [ ] ...`` string.
        target_dir: Directory containing PLAN.md.

    Returns:
        Number of tasks actually inserted or reopened (after dedup).
    """
    plan_path = (Path(target_dir) / _PLAN_FILENAME).resolve()
    if not plan_path.exists() or not tasks:
        return 0

    content = plan_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Locate existing ## Bugs section.
    bugs_start: int | None = None
    bugs_end: int | None = None
    for i, line in enumerate(lines):
        if line.strip().lower() == _BUGS_HEADING.lower():
            bugs_start = i
            # Find the end: next heading (H1 or H2) or EOF.
            for j in range(i + 1, len(lines)):
                if re.match(r"^#{1,2}\s", lines[j]):
                    bugs_end = j
                    break
            if bugs_end is None:
                bugs_end = len(lines)
            break

    if bugs_start is not None:
        # Build maps of existing task bodies → line indices, split by
        # checked vs unchecked so we can implement reopen-in-place.
        unchecked_bodies: set[str] = set()
        checked_body_to_line: dict[str, int] = {}
        for k in range(bugs_start + 1, bugs_end):
            stripped = lines[k].lstrip()
            if stripped.startswith("- [x] ") or stripped.startswith("- [X] "):
                body = _task_body(stripped)
                checked_body_to_line.setdefault(body, k)
            elif stripped.startswith("- [ ] "):
                unchecked_bodies.add(_task_body(stripped))

        inserted = 0
        to_append: list[str] = []
        for task in tasks:
            body = _task_body(task)
            if body in unchecked_bodies:
                # Already exists unchecked — skip.
                continue
            if body in checked_body_to_line:
                # Reopen in place: uncheck the existing line.
                idx = checked_body_to_line.pop(body)
                old = lines[idx]
                lines[idx] = re.sub(r"^(\s*- \[)[xX](\] )", r"\1 \2", old)
                unchecked_bodies.add(body)
                inserted += 1
            else:
                to_append.append(task)
                unchecked_bodies.add(body)
                inserted += 1

        if to_append:
            # Insert before bugs_end — find the last non-blank line
            # in the section to append after it.
            pos = bugs_end - 1
            while pos > bugs_start and lines[pos].strip() == "":
                pos -= 1
            insert_at = pos + 1

            for idx, task in enumerate(to_append):
                lines.insert(insert_at + idx, task)

        if inserted == 0:
            return 0

        plan_path.write_text("\n".join(lines), encoding="utf-8")
        return inserted

    # No ## Bugs section found — create one.
    # Place it after intro prose and before the first checklist item
    # or second heading, whichever comes first.
    first_heading: int | None = None
    first_checklist: int | None = None
    second_heading: int | None = None
    for i, line in enumerate(lines):
        if line.startswith("# "):
            if first_heading is None:
                first_heading = i
            elif second_heading is None:
                second_heading = i
        stripped = line.lstrip()
        if first_checklist is None and stripped.startswith("- ["):
            first_checklist = i

    # Insert position: pick the earliest structural boundary after
    # the title heading.
    candidates = [c for c in (second_heading, first_checklist) if c is not None]
    if candidates:
        insert_at = min(candidates)
    elif first_heading is not None:
        insert_at = len(lines)
    else:
        insert_at = len(lines)

    # Build the bugs block.
    block = ["", _BUGS_HEADING, ""]
    block.extend(tasks)
    block.append("")

    for idx, bline in enumerate(block):
        lines.insert(insert_at + idx, bline)

    plan_path.write_text("\n".join(lines), encoding="utf-8")
    return len(tasks)


def move_references(
    paths: list[Path],
    *,
    target_dir: Path | str = ".",
) -> list[Path]:
    """Move processed reference files into ``.duplo/references/``.

    Each file in *paths* is moved (renamed) into the references
    directory, preserving the original filename.  If a file with the
    same name already exists, it is overwritten.  Files that no longer
    exist are silently skipped.

    Returns the list of destination paths for files that were moved.
    """
    refs_dir = Path(target_dir) / REFERENCES_DIR
    refs_dir.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    for src in paths:
        if not src.exists():
            continue
        dest = refs_dir / src.name
        shutil.move(str(src), dest)
        moved.append(dest)
    return moved
