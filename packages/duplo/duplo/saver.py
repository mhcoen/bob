"""Save duplo selections and preferences to duplo.json in the target project."""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timezone
from pathlib import Path

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
    _ensure_duplo_dir(target_dir)
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
    _ensure_duplo_dir(target_dir)
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
    try:
        data: dict = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
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
    _ensure_duplo_dir(target_dir)
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
    _ensure_duplo_dir(target_dir)
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
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
    data: dict = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
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
        src.rename(dest)
        moved.append(dest)
    return moved
