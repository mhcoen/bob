"""Save duplo selections and preferences to duplo.json in the target project."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from duplo.doc_examples import CodeExample
from duplo.extractor import Feature
from duplo.questioner import BuildPreferences

DUPLO_JSON = "duplo.json"
CLAUDE_MD = "CLAUDE.md"

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

Do not delete `screenshots/reference/` -- those are the
target images duplo uses for visual comparison.

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
        target_dir: Directory in which to write ``duplo.json``.
    """
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data = {
        "source_url": source_url,
        "app_name": app_name,
        "features": [dataclasses.asdict(f) for f in features],
        "preferences": dataclasses.asdict(preferences),
    }
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
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    match = re.search(r"^#\s*(Phase\s+\d+[^\n]*)", plan_content, re.IGNORECASE | re.MULTILINE)
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
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    entry = {
        "after_phase": after_phase,
        "text": text,
        "recorded_at": datetime.now(tz=timezone.utc).isoformat(),
    }
    data.setdefault("feedback", []).append(entry)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def set_in_progress(
    label: str,
    *,
    mcloop_done: bool = False,
    target_dir: Path | str = ".",
) -> None:
    """Record the currently-running phase in *duplo.json*.

    Writes ``{"in_progress": {"label": label, "mcloop_done": mcloop_done}}``
    into *duplo.json*, creating the file if it does not exist.  This is used
    to resume an interrupted run.

    Args:
        label: Human-readable phase label (e.g. ``"Phase 1"``).
        mcloop_done: ``True`` once McLoop has completed successfully.
        target_dir: Directory containing ``duplo.json``.
    """
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["in_progress"] = {"label": label, "mcloop_done": mcloop_done}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def clear_in_progress(*, target_dir: Path | str = ".") -> None:
    """Remove the ``in_progress`` key from *duplo.json* (no-op if absent).

    Args:
        target_dir: Directory containing ``duplo.json``.
    """
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    if not path.exists():
        return
    data: dict = json.loads(path.read_text(encoding="utf-8"))
    if "in_progress" not in data:
        return
    data.pop("in_progress")
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["screenshot_features"] = mapping
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def save_roadmap(
    roadmap: list[dict],
    *,
    target_dir: Path | str = ".",
) -> Path:
    """Save the roadmap to duplo.json."""
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["roadmap"] = roadmap
    data["current_phase"] = 0
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def advance_phase(
    *,
    target_dir: Path | str = ".",
) -> int:
    """Increment current_phase in duplo.json. Returns new phase number."""
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
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
    data = json.loads(path.read_text(encoding="utf-8"))
    roadmap = data.get("roadmap", [])
    current = data.get("current_phase", 0)
    for phase in roadmap:
        if phase.get("phase") == current:
            return (current, phase)
    return (current, None)


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
    path = (Path(target_dir) / DUPLO_JSON).resolve()
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data["code_examples"] = [dataclasses.asdict(ex) for ex in examples]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def write_claude_md(*, target_dir: Path | str = ".") -> Path:
    """Write ``CLAUDE.md`` with appshot instructions to *target_dir*.

    The file is created or overwritten.  Returns the path to the written file.
    """
    path = (Path(target_dir) / CLAUDE_MD).resolve()
    path.write_text(_CLAUDE_MD_CONTENT, encoding="utf-8")
    return path
