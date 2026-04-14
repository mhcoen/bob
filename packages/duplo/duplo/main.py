from __future__ import annotations

# mcloop:wrap:begin
import hashlib as _mcloop_hashlib
import json as _mcloop_json
import logging as _mcloop_logging
import signal as _mcloop_signal
import sys as _mcloop_sys
import traceback as _mcloop_traceback
from datetime import datetime as _mcloop_datetime, timezone as _mcloop_tz
from pathlib import Path as _mcloop_Path


class _McloopState:
    _providers = []
    _last_action = ""

    @classmethod
    def register(cls, provider):
        cls._providers.append(provider)

    @classmethod
    def record_action(cls, action):
        cls._last_action = str(action)

    @classmethod
    def snapshot(cls):
        result = {}
        for provider in cls._providers:
            try:
                result.update(provider())
            except Exception:
                pass
        return result

    @classmethod
    def last_action(cls):
        return cls._last_action


def _mcloop_setup_crash_handlers():
    error_dir = _mcloop_Path(".mcloop")
    error_dir.mkdir(parents=True, exist_ok=True)
    error_path = error_dir / "errors.json"

    def _write_error(report):
        entries = []
        if error_path.exists():
            try:
                entries = _mcloop_json.loads(error_path.read_text())
            except (ValueError, OSError):
                pass
        trace = report.get("stack_trace", "")
        sig = report.get("signal", report.get("exception_type", ""))
        raw = f"{trace}{sig}".encode()
        report["id"] = _mcloop_hashlib.md5(raw).hexdigest()[:8]
        entries.append(report)
        try:
            error_path.write_text(_mcloop_json.dumps(entries, indent=2) + "\n")
        except OSError:
            pass

    def _excepthook(exc_type, exc_value, exc_tb):
        frames = _mcloop_traceback.extract_tb(exc_tb)
        last = frames[-1] if frames else None
        local_vars = {}
        if exc_tb is not None:
            tb = exc_tb
            while tb.tb_next:
                tb = tb.tb_next
            local_vars = {
                k: repr(v) for k, v in tb.tb_frame.f_locals.items() if not k.startswith("_")
            }
        state = _McloopState.snapshot()
        state.update(local_vars)
        report = {
            "timestamp": _mcloop_datetime.now(_mcloop_tz.utc).isoformat(),
            "exception_type": exc_type.__name__,
            "description": str(exc_value),
            "stack_trace": "".join(
                _mcloop_traceback.format_exception(exc_type, exc_value, exc_tb)
            ),
            "source_file": last.filename if last else "",
            "line": last.lineno if last else 0,
            "app_state": state,
            "last_action": _McloopState.last_action(),
            "fix_attempts": 0,
        }
        _write_error(report)
        _loc = f"{last.filename}:{last.lineno}" if last else "unknown"
        _mcloop_sys.stderr.write(
            f"[McLoop] Crash captured: {exc_type.__name__} in {_loc}."
            f" Run mcloop from /Users/mhcoen/proj/duplo"
            f" to fix this bug.\n"
        )
        _mcloop_sys.__excepthook__(exc_type, exc_value, exc_tb)

    _mcloop_sys.excepthook = _excepthook

    def _signal_handler(signum, frame):
        source = ""
        lineno = 0
        if frame is not None:
            source = frame.f_code.co_filename
            lineno = frame.f_lineno
        # Avoid calling provider closures in signal context
        # (they may hold locks or do I/O). Read raw state only.
        try:
            state = dict(_McloopState.snapshot())
        except Exception:
            state = {}
        report = {
            "timestamp": _mcloop_datetime.now(_mcloop_tz.utc).isoformat(),
            "signal": signum,
            "exception_type": "Signal",
            "description": f"Received signal {signum}",
            "stack_trace": "".join(_mcloop_traceback.format_stack(frame)),
            "source_file": source,
            "line": lineno,
            "app_state": state,
            "last_action": _McloopState.last_action(),
            "fix_attempts": 0,
        }
        try:
            _write_error(report)
        except Exception:
            pass
        _loc = f"{source}:{lineno}" if source else "unknown"
        try:
            _mcloop_sys.stderr.write(
                f"[McLoop] Crash captured: Signal {signum} in {_loc}."
                f" Run mcloop from /Users/mhcoen/proj/duplo"
                f" to fix this bug.\n"
            )
        except Exception:
            pass
        _mcloop_signal.signal(signum, _mcloop_signal.SIG_DFL)
        import os

        os.kill(os.getpid(), signum)

    for _sig in (
        _mcloop_signal.SIGSEGV,
        _mcloop_signal.SIGABRT,
    ):
        try:
            _mcloop_signal.signal(_sig, _signal_handler)
        except OSError:
            pass

    class _McloopLogHandler(_mcloop_logging.Handler):
        def emit(self, record):
            if record.exc_info and record.exc_info[1] is not None:
                exc_type, exc_value, exc_tb = record.exc_info
                frames = _mcloop_traceback.extract_tb(exc_tb)
                last = frames[-1] if frames else None
                local_vars = {}
                if exc_tb is not None:
                    tb = exc_tb
                    while tb.tb_next:
                        tb = tb.tb_next
                    local_vars = {
                        k: repr(v)
                        for k, v in tb.tb_frame.f_locals.items()
                        if not k.startswith("_")
                    }
                state = _McloopState.snapshot()
                state.update(local_vars)
                report = {
                    "timestamp": _mcloop_datetime.now(_mcloop_tz.utc).isoformat(),
                    "exception_type": exc_type.__name__,
                    "description": str(exc_value),
                    "stack_trace": "".join(
                        _mcloop_traceback.format_exception(exc_type, exc_value, exc_tb)
                    ),
                    "source_file": last.filename if last else "",
                    "line": last.lineno if last else 0,
                    "app_state": state,
                    "last_action": _McloopState.last_action(),
                    "fix_attempts": 0,
                }
                _write_error(report)

    handler = _McloopLogHandler()
    handler.setLevel(_mcloop_logging.ERROR)
    _mcloop_logging.getLogger().addHandler(handler)


_mcloop_setup_crash_handlers()
# mcloop:wrap:end

"""Duplo CLI entry point."""

import argparse
import dataclasses
import hashlib
import json
import os
import re
import signal
import sys
import time
from pathlib import Path

from duplo.appshot import capture_appshot
from duplo.collector import collect_feedback, collect_issues
from duplo.comparator import compare_screenshots
from duplo.diagnostics import print_summary as diagnostics_print_summary, record_failure
from duplo.design_extractor import (
    DesignRequirements,
    extract_design,
    format_design_block,
    format_design_section,
)
from duplo.orchestrator import (
    _accepted_frames_by_source,
    _collect_cross_origin_links,
    collect_design_input,
)
from duplo.doc_tables import DocStructures
from duplo.issuer import generate_issue_list, save_issue_list
from duplo.extractor import Feature, _matches_excluded, extract_features
from duplo.gap_detector import (
    _merge_design_dicts,
    _parse_design_markdown,
    detect_design_gaps,
    detect_gaps,
    format_gap_tasks,
)
from duplo.notifier import notify_phase_complete
from duplo.fetcher import download_media, extract_media_urls, fetch_site
from duplo.docs_extractor import docs_text_extractor
from duplo.pdf_extractor import extract_pdf_text
from duplo.planner import (
    generate_phase_plan,
    parse_completed_tasks,
    save_plan,
)
from duplo.build_prefs import (
    architecture_hash,
    parse_build_preferences,
    validate_build_preferences,
)
from duplo.questioner import BuildPreferences, ask_preferences
from duplo.roadmap import format_roadmap, generate_roadmap

from duplo.scanner import scan_directory, scan_files
from duplo.test_generator import (
    detect_target_language,
    generate_test_source,
    save_test_file,
)
from duplo.validator import validate_product_url
from duplo.frame_describer import describe_frames
from duplo.verification_extractor import (
    extract_verification_cases,
    format_verification_tasks,
    load_frame_descriptions,
)
from duplo.frame_filter import apply_filter, filter_frames
from duplo.video_extractor import extract_all_videos
from duplo.hasher import compute_hashes, diff_hashes, load_hashes, save_hashes
from duplo.investigator import format_investigation, investigate, investigation_to_fix_tasks
from duplo.migration import _check_migration
from duplo.spec_reader import (
    ProductSpec,
    SourceEntry,
    format_behavioral_references,
    format_contracts_as_verification,
    format_counter_example_sources,
    format_counter_examples,
    format_doc_references,
    format_spec_for_prompt,
    read_spec,
    scrapeable_sources,
    validate_for_run,
)
from duplo.spec_writer import append_sources, update_design_autogen
from duplo.saver import (
    advance_phase,
    append_phase_to_history,
    append_to_bugs_section,
    get_current_phase,
    load_examples,
    load_product,
    mark_implemented_features,
    move_references,
    resolve_completed_fixes,
    save_build_preferences,
    save_design_requirements,
    save_doc_structures,
    save_examples,
    save_features,
    save_feedback,
    save_issue,
    save_product,
    save_raw_content,
    save_reference_urls,
    save_roadmap,
    save_screenshot_feature_map,
    save_selections,
    store_accepted_frames,
    write_claude_md,
)
from duplo.task_matcher import match_unannotated_tasks
from duplo.screenshotter import map_screenshots_to_features, save_reference_screenshots
from duplo.selector import select_features, select_issues

_SECTION_URL_RE = re.compile(r"^=== (.+?) ===$", re.MULTILINE)
_DUPLO_JSON = ".duplo/duplo.json"
# Files that are project artifacts, not user-provided reference materials.
_PROJECT_FILES = {"PLAN.md", "CLAUDE.md", "README.md", "ISSUES.md", "NOTES.md", "SPEC.md"}

_FEATURE_FIELDS = {fld.name for fld in dataclasses.fields(Feature)}


def _feature_from_dict(d: dict) -> Feature:
    """Build a :class:`Feature` from a raw dict, ignoring unknown keys."""
    return Feature(**{k: v for k, v in d.items() if k in _FEATURE_FIELDS})


def _prefs_from_dict(prefs_data: dict) -> BuildPreferences:
    """Build :class:`BuildPreferences` from a raw duplo.json dict."""
    return BuildPreferences(
        platform=prefs_data.get("platform", ""),
        language=prefs_data.get("language", ""),
        constraints=prefs_data.get("constraints", []),
        preferences=prefs_data.get("preferences", []),
    )


def _load_preferences(data: dict, spec) -> BuildPreferences:
    """Load build preferences with architecture-hash invalidation.

    If ``spec.architecture`` is present and its hash differs from the
    stored ``architecture_hash`` in *data*, re-parses preferences via
    LLM and persists the result.  Otherwise returns cached preferences.
    """
    prefs_data = data.get("preferences", {})
    cached = _prefs_from_dict(prefs_data)

    if not spec or not spec.architecture:
        return cached

    current_hash = architecture_hash(spec.architecture)
    stored_hash = data.get("architecture_hash", "")

    if current_hash == stored_hash:
        return cached

    # Hash changed — re-parse from the updated architecture prose.
    prefs = parse_build_preferences(spec.architecture)
    save_build_preferences(prefs, current_hash)
    # Update in-memory data so later accesses in the same run see it.
    data["preferences"] = dataclasses.asdict(prefs)
    data["architecture_hash"] = current_hash
    for w in validate_build_preferences(prefs):
        print(f"Warning: {w}")
    return prefs


def _run_video_frame_pipeline(
    videos: list[Path],
    *,
    indent: str = "",
) -> tuple[list[Path], dict[Path, list[Path]]]:
    """Extract, filter, describe, and store video frames.

    Returns ``(accepted_frames, accepted_frames_by_path)`` where
    *accepted_frames* is the flat list of kept frame paths and
    *accepted_frames_by_path* maps each input video path to its
    accepted (post-filter) frames via
    :func:`~duplo.orchestrator._accepted_frames_by_source`.
    """
    frames_dir = Path(".duplo") / "video_frames"
    results = extract_all_videos(videos, frames_dir)
    video_frames: list[Path] = []
    for vr in results:
        if vr.error:
            print(f"{indent}  {vr.source.name}: {vr.error}")
        elif vr.frames:
            print(f"{indent}  {vr.source.name}: {len(vr.frames)} frame(s) extracted")
            video_frames.extend(vr.frames)
    if not video_frames:
        return [], {}
    print(f"{indent}Filtering frames with Vision \u2026")
    decisions = filter_frames(video_frames)
    video_frames = apply_filter(decisions)
    kept = sum(1 for d in decisions if d.keep)
    rejected = len(decisions) - kept
    if rejected:
        print(f"{indent}  Kept {kept}, rejected {rejected} frame(s)")

    # Build per-source lookup from the kept set so callers can
    # compose design input by source role (visual-target vs scraped).
    kept_set = set(video_frames)
    filtered_results = [
        dataclasses.replace(r, frames=[f for f in r.frames if f in kept_set]) for r in results
    ]
    accepted_frames_by_path = _accepted_frames_by_source(filtered_results)

    if not video_frames:
        return [], accepted_frames_by_path
    print(f"{indent}Describing UI states \u2026")
    frame_descs = describe_frames(video_frames)
    for fd in frame_descs:
        print(f"{indent}  {fd.path.name}: {fd.state} \u2014 {fd.detail}")
    frame_entries = [
        {
            "path": fd.path,
            "filename": fd.path.name,
            "state": fd.state,
            "detail": fd.detail,
        }
        for fd in frame_descs
    ]
    stored = store_accepted_frames(frame_entries)
    if stored:
        print(f"{indent}  Stored {len(stored)} frame(s) in .duplo/references/")
    return video_frames, accepted_frames_by_path


_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi"}


def _visual_target_video_frames(
    spec: ProductSpec | None,
    videos: list[Path],
    frames: list[Path],
) -> list[Path]:
    """Return *frames* that came from videos with ``visual-target`` role.

    Matches frames to their source videos using filename stems (ffmpeg
    names frames ``{stem}_scene_NNNN.png``).
    """
    if not spec or not frames or not videos:
        return []
    from duplo.spec_reader import format_visual_references

    root = Path.cwd()
    vt_resolved = set()
    for entry in format_visual_references(spec):
        if entry.path.suffix.lower() in _VIDEO_EXTS:
            vt_resolved.add((root / entry.path).resolve())

    vt_stems = {v.stem for v in videos if v.resolve() in vt_resolved}
    if not vt_stems:
        return []

    return [f for f in frames if any(f.name.startswith(stem + "_") for stem in vt_stems)]


@dataclasses.dataclass
class UpdateSummary:
    """Accumulates what was found and added during a subsequent run."""

    files_added: int = 0
    files_changed: int = 0
    files_removed: int = 0
    images_analyzed: int = 0
    videos_found: int = 0
    video_frames_extracted: int = 0
    pdfs_extracted: int = 0
    text_files_read: int = 0
    urls_fetched: int = 0
    pages_rescraped: int = 0
    examples_rescraped: int = 0
    new_features: int = 0
    missing_features: int = 0
    missing_examples: int = 0
    design_refinements: int = 0
    tasks_appended: int = 0
    collected_text: str = ""


@dataclasses.dataclass
class ScrapeResult:
    """Accumulated results from scraping all declared sources."""

    combined_text: str = ""
    all_code_examples: list = dataclasses.field(default_factory=list)
    all_page_records: list = dataclasses.field(default_factory=list)
    all_raw_pages: dict = dataclasses.field(default_factory=dict)
    product_ref_raw_pages: dict = dataclasses.field(default_factory=dict)
    merged_doc_structures: DocStructures = dataclasses.field(default_factory=DocStructures)
    discovered_urls: list = dataclasses.field(default_factory=list)


def _scrape_declared_sources(spec: ProductSpec) -> ScrapeResult:
    """Iterate scrapeable sources from SPEC.md and fetch each.

    Accumulates scraped text, code examples, page records, raw pages,
    and doc structures from all sources.  Deduplicates page records and
    raw pages by canonical URL using first-source-wins semantics.
    Collects cross-origin links from deep-crawl sources for SPEC.md
    write-back.
    """
    result = ScrapeResult()
    seen_canonical_urls: set[str] = set()

    sources = scrapeable_sources(spec)
    if not sources:
        return result

    print(f"\nScraping {len(sources)} declared source(s) \u2026")
    for source in sources:
        print(f"  Fetching {source.url} (depth={source.scrape}) \u2026")
        try:
            (
                scraped_text,
                code_examples,
                doc_structures,
                page_records,
                source_raw_pages,
            ) = fetch_site(source.url, scrape_depth=source.scrape)
        except Exception as exc:
            print(f"  Failed to fetch {source.url}: {exc}")
            continue

        result.combined_text += scraped_text + "\n"
        result.all_code_examples.extend(code_examples)

        # First-source-wins dedup for PageRecord and raw HTML.
        for record in page_records:
            if record.url not in seen_canonical_urls:
                result.all_page_records.append(record)
                seen_canonical_urls.add(record.url)
        for url, html in source_raw_pages.items():
            result.all_raw_pages.setdefault(url, html)
            if source.role == "product-reference":
                result.product_ref_raw_pages.setdefault(url, html)

        if doc_structures:
            result.merged_doc_structures.feature_tables.extend(doc_structures.feature_tables)
            result.merged_doc_structures.operation_lists.extend(doc_structures.operation_lists)
            result.merged_doc_structures.unit_lists.extend(doc_structures.unit_lists)
            result.merged_doc_structures.function_refs.extend(doc_structures.function_refs)

        # Cross-origin discovery is a deep-crawl behavior only.
        if source.scrape == "deep":
            result.discovered_urls.extend(
                _collect_cross_origin_links(source.url, source_raw_pages)
            )

    return result


def _persist_scrape_result(result: ScrapeResult) -> None:
    """Save accumulated scrape artifacts to .duplo/.

    Persists code examples, page records, raw page HTML, and doc
    structures from a :class:`ScrapeResult`.  Appends discovered
    cross-origin URLs to SPEC.md with ``discovered: true``.
    """
    if result.all_code_examples:
        save_examples(result.all_code_examples)
    if result.all_page_records:
        save_reference_urls(result.all_page_records)
        if result.all_raw_pages:
            save_raw_content(result.all_raw_pages, result.all_page_records)
    if result.merged_doc_structures:
        save_doc_structures(result.merged_doc_structures)

    # Append discovered URLs to ## Sources with discovered: true.
    if result.discovered_urls:
        spec_path = Path.cwd() / "SPEC.md"
        if spec_path.exists():
            existing = spec_path.read_text(encoding="utf-8")
            modified = append_sources(
                existing,
                [
                    SourceEntry(
                        url=u,
                        role="docs",
                        scrape="deep",
                        discovered=True,
                    )
                    for u in result.discovered_urls
                ],
            )
            if modified != existing:
                spec_path.write_text(modified, encoding="utf-8")


def _download_site_media(
    raw_pages: dict[str, str],
) -> tuple[list[Path], list[Path]]:
    """Collect embedded media paths from fetched HTML pages.

    Scans each page's HTML for ``<video>``, ``<source>``, ``<img>``,
    and ``<picture>`` tags, downloads media files to
    ``.duplo/site_media/<url-hash>/<filename>``, and returns
    ``(image_paths, video_paths)`` where each list contains LOCAL
    PATHS TO ALL EMBEDDED MEDIA — both files newly downloaded during
    this call AND files already present in the cache from previous
    runs.  Callers receive a complete media inventory regardless of
    cache state.

    The URL hash is derived from the page URL the media was embedded
    in; the filename is derived from the resource URL.
    """
    base_dir = Path(".duplo") / "site_media"
    all_images: list[Path] = []
    all_videos: list[Path] = []
    seen: set[str] = set()

    for page_url, html in raw_pages.items():
        image_urls, video_urls = extract_media_urls(html, page_url)
        url_hash = hashlib.sha256(page_url.encode()).hexdigest()[:16]
        page_dir = base_dir / url_hash

        new_img_urls = [u for u in image_urls if u not in seen]
        new_vid_urls = [u for u in video_urls if u not in seen]
        seen.update(new_img_urls)
        seen.update(new_vid_urls)

        if not new_img_urls and not new_vid_urls:
            continue

        imgs, vids = download_media(new_img_urls, new_vid_urls, page_dir)
        all_images.extend(imgs)
        all_videos.extend(vids)

    return all_images, all_videos


def main() -> None:
    """Run duplo from the current directory.

    First run (no ``.duplo/duplo.json``): scan for reference materials,
    fetch URLs, extract features, generate roadmap and plan, build.

    Subsequent runs: resume interrupted phases or advance to the next one.
    """
    # Check for subcommands before parsing, since the default
    # mode uses a positional 'url' arg that would eat 'fix'/'investigate'.
    if len(sys.argv) > 1 and sys.argv[1] in ("fix", "investigate"):
        subcmd = sys.argv[1]
        fix_parser = argparse.ArgumentParser(
            prog=f"duplo {subcmd}",
            description=(
                "Investigate bugs with product-level AI diagnosis."
                if subcmd == "investigate"
                else "Report bugs and append fix tasks to the current PLAN.md."
            ),
        )
        fix_parser.add_argument(
            "bugs",
            nargs="*",
            help="Bug descriptions (one per argument). Use quotes for multi-word.",
        )
        fix_parser.add_argument(
            "--file",
            "-f",
            dest="bug_file",
            default=None,
            help="Read bug descriptions from a file (one per paragraph, blank-line separated).",
        )
        fix_parser.add_argument(
            "--screenshot",
            "-s",
            action="store_true",
            default=False,
            help="Capture a screenshot of the running app for context.",
        )
        fix_parser.add_argument(
            "--investigate",
            "-i",
            action="store_true",
            default=False,
            help=(
                "Alias for bare `duplo fix` (which also runs investigation). "
                "Retained for clarity and for use as `duplo investigate`."
            ),
        )
        fix_parser.add_argument(
            "--images",
            nargs="+",
            default=None,
            metavar="PATH",
            help="User-supplied screenshot files showing the bug.",
        )
        args = fix_parser.parse_args(sys.argv[2:])
        args.command = subcmd
        # 'duplo investigate' implies --investigate.
        if subcmd == "investigate":
            args.investigate = True
    else:
        parser = argparse.ArgumentParser(
            description="Duplicate an app from reference materials or a product URL.",
        )
        parser.add_argument(
            "url",
            nargs="?",
            default=None,
            help="Product URL to duplicate (e.g. https://numi.app)",
        )
        args = parser.parse_args()
        args.command = None

    def _handle_signal(signum, frame):
        print("\nInterrupted.", flush=True)
        os._exit(130)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTSTP, _handle_signal)

    duplo_path = Path(_DUPLO_JSON)

    if args.command in ("fix", "investigate"):
        if not duplo_path.exists():
            print("No duplo project found. Run duplo first to initialize.")
            sys.exit(1)
        _fix_mode(args)
    else:
        _check_migration(Path.cwd())
        if not duplo_path.exists():
            _first_run(url=args.url)
        else:
            if args.url:
                print("Project already initialized. URL argument ignored.")
            _subsequent_run()

    diagnostics_print_summary()


def _investigation_context(spec: ProductSpec | None) -> dict:
    """Build role-filtered keyword arguments for ``investigate()``.

    Returns a dict suitable for ``**kwargs`` expansion into
    :func:`duplo.investigator.investigate`.
    """
    if spec is None:
        return {}
    kwargs: dict = {}
    ce = format_counter_examples(spec)
    if ce:
        kwargs["counter_examples"] = ce
    ces = format_counter_example_sources(spec)
    if ces:
        kwargs["counter_example_sources"] = ces
    doc_refs = format_doc_references(spec)
    if doc_refs:
        kwargs["docs_text"] = docs_text_extractor(doc_refs)
    if spec.behavior_contracts:
        kwargs["behavior_contracts"] = spec.behavior_contracts
    return kwargs


def _fix_mode(args: argparse.Namespace) -> None:
    """Report bugs and append fix tasks to PLAN.md without phase changes.

    Both ``duplo fix`` and ``duplo fix --investigate`` run intelligent
    product-level diagnosis via :func:`duplo.investigator.investigate`,
    using all available context (features, design, examples, issues,
    current screenshot, reference frames, SPEC.md, user-supplied images).

    Behavior:
    - If :func:`investigate` returns one or more ``Diagnosis`` entries,
      they are formatted as structured diagnosed fix tasks and appended
      to the ``## Bugs`` section of PLAN.md (or reopened in place if
      already present as checked items).
    - If :func:`investigate` returns no diagnoses (LLM failure, timeout,
      or unparseable output), ``_fix_mode`` falls back to appending one
      raw ``- [ ] Fix: <bug text> [fix: "<bug text>"]`` line per reported
      bug so work can still proceed.

    In both modes the reported bugs are saved to ``duplo.json`` as
    open ``issues`` with ``source="user"`` and the current phase label.

    ``--investigate`` / ``duplo investigate`` is retained as an explicit
    alias for clarity; it does not alter the code path.

    Usage:
        duplo fix "labeled expressions don't evaluate"
        duplo fix --investigate "expressions don't evaluate"
        duplo investigate "expressions don't evaluate"
        duplo fix --images bug1.png bug2.png "wrong layout"
        duplo fix --file BUGS.md
        duplo fix --screenshot  # interactive + capture
        duplo fix               # interactive input
    """
    bugs: list[str] = []

    # Source 1: command-line arguments.
    if args.bugs:
        bugs.extend(args.bugs)

    # Source 2: file.
    if args.bug_file:
        bug_path = Path(args.bug_file)
        if not bug_path.exists():
            print(f"File not found: {args.bug_file}")
            sys.exit(1)
        text = bug_path.read_text(encoding="utf-8")
        # Split on blank lines — each paragraph is one bug.
        for paragraph in re.split(r"\n\s*\n", text):
            stripped = paragraph.strip()
            if stripped:
                bugs.append(stripped)
        file_bugs = len(bugs) - len(args.bugs) if args.bugs else len(bugs)
        print(f"Read {file_bugs} bug(s) from {args.bug_file}.")

    # Source 3: interactive input if no bugs provided yet.
    if not bugs:
        print("Describe each bug, then press Enter twice to record it.")
        print("Press Enter on an empty line when done.")
        print("")
        try:
            while True:
                lines: list[str] = []
                while True:
                    line = input("")
                    if line == "":
                        break
                    lines.append(line)
                text = "\n".join(lines).strip()
                if not text:
                    break
                bugs.append(text)
                print(f"  Recorded bug {len(bugs)}.")
        except EOFError:
            pass

    if not bugs:
        print("No bugs reported.")
        return

    # Load project data.
    duplo_path = Path(_DUPLO_JSON)
    try:
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"Error: {duplo_path} contains invalid JSON.")
        sys.exit(1)

    # Optionally capture a screenshot.
    if args.screenshot:
        app_name = data.get("app_name", "")
        if app_name:
            output_path = Path("screenshots") / "current" / "main.png"
            launch_cmd = "./run.sh" if Path("run.sh").exists() else None
            print(f"Capturing screenshot of {app_name} \u2026")
            shot_code = capture_appshot(app_name, output_path, launch=launch_cmd)
            if shot_code == 0:
                print(f"Screenshot saved to {output_path}")
            elif shot_code == -2:
                print("Screenshot capture timed out (skipping)")
            else:
                print("Screenshot capture failed (continuing without it).")
        else:
            print("No app_name in duplo.json \u2014 skipping screenshot.")

    # Collect user-supplied screenshot paths.
    user_screenshots: list[Path] | None = None
    if getattr(args, "images", None):
        user_screenshots = [Path(p) for p in args.images]
        missing = [p for p in user_screenshots if not p.exists()]
        if missing:
            for m in missing:
                print(f"Warning: screenshot not found: {m}")
            user_screenshots = [p for p in user_screenshots if p.exists()]
        if user_screenshots:
            print(f"Using {len(user_screenshots)} user-supplied screenshot(s).")

    # Save bugs as issues in duplo.json.
    phase_label = ""
    phase_num, phase_info = get_current_phase()
    if phase_info:
        phase_label = f"Phase {phase_num}: {phase_info['title']}"

    for desc in bugs:
        save_issue(desc, source="user", phase=phase_label)
    print(f"Saved {len(bugs)} issue(s) to duplo.json.")

    # Intelligent investigation mode.
    if getattr(args, "investigate", False):
        spec = read_spec()
        spec_prompt = format_spec_for_prompt(spec) if spec else ""
        inv_kwargs = _investigation_context(spec)
        print("\nRunning product-level investigation \u2026")
        result = investigate(
            bugs,
            user_screenshots=user_screenshots,
            spec_text=spec_prompt,
            **inv_kwargs,
        )
        print(format_investigation(result))

        if not result.diagnoses:
            print("No actionable diagnoses. Issues saved to duplo.json.")
            print("Run mcloop to start fixing.")
            return

        # Append diagnosed fix tasks to PLAN.md ## Bugs section.
        plan_path = Path("PLAN.md")
        if not plan_path.exists():
            print("No PLAN.md found. Diagnoses printed above but no fix tasks appended.")
            print("Run duplo to generate a plan, then run mcloop.")
            return

        fix_tasks = investigation_to_fix_tasks(result)
        writes = append_to_bugs_section(fix_tasks)
        print(f"Updated {writes} diagnosed fix task(s) in PLAN.md.")
        print("Run mcloop to start fixing.")
        return

    # Diagnose bugs via investigator before appending fix tasks.
    spec = read_spec()
    spec_prompt = format_spec_for_prompt(spec) if spec else ""
    inv_kwargs = _investigation_context(spec)
    print("\nDiagnosing reported bug(s) \u2026")
    result = investigate(
        bugs,
        user_screenshots=user_screenshots,
        spec_text=spec_prompt,
        **inv_kwargs,
    )

    plan_path = Path("PLAN.md")

    if result.diagnoses:
        print(format_investigation(result))

        if not plan_path.exists():
            print("No PLAN.md found. Diagnoses printed above but no fix tasks appended.")
            print("Run duplo to generate a plan, then run mcloop.")
            return

        fix_tasks = investigation_to_fix_tasks(result)
        writes = append_to_bugs_section(fix_tasks)
        print(f"Updated {writes} diagnosed fix task(s) in PLAN.md.")
        print("Run mcloop to start fixing.")
    else:
        # Investigator returned no diagnoses — fall back with error context.
        fallback_reason = result.summary or "Investigation produced no diagnoses"
        print(f"\nDiagnosis incomplete: {fallback_reason}")

        if not plan_path.exists():
            print("No PLAN.md found. Issues saved but no fix tasks appended.")
            print("Run duplo to generate a plan, then run mcloop.")
            return

        fix_tasks = []
        for desc in bugs:
            oneline = desc.replace("\n", " ").strip()
            fix_tasks.append(f'- [ ] Fix: {oneline} [fix: "{oneline}"]')

        writes = append_to_bugs_section(fix_tasks)
        print(f"Updated {writes} fix task(s) in PLAN.md (undiagnosed).")
        print("Run mcloop to start fixing.")


def _first_run(*, url: str | None = None) -> None:
    """Scan reference materials in the current directory and bootstrap the project."""
    spec = read_spec()
    if spec:
        print(f"Product spec loaded from SPEC.md ({len(spec.raw)} chars).")
        validation = validate_for_run(spec)
        for warning in validation.warnings:
            print(f"Warning: {warning}")
        if validation.errors:
            for err in validation.errors:
                print(f"Error: {err}", file=sys.stderr)
            sys.exit(1)
    spec_prompt = format_spec_for_prompt(spec) if spec else ""

    scan = scan_directory(Path(".") / "ref")
    if url:
        # URL provided on command line takes priority.
        if url not in scan.urls:
            scan.urls.insert(0, url)
    if (
        not scan.images
        and not scan.videos
        and not scan.pdfs
        and not scan.text_files
        and not scan.urls
    ):
        print(
            "No reference materials found.\n"
            "Provide a URL: duplo https://example.com\n"
            "Or drop images, PDFs, text files, or a file containing URLs\n"
            "into this directory and run duplo again."
        )
        sys.exit(1)

    print("Scanning reference materials \u2026")
    if scan.images:
        print(f"  Images: {len(scan.images)}")
    if scan.videos:
        print(f"  Videos: {len(scan.videos)}")
    if scan.pdfs:
        print(f"  PDFs:   {len(scan.pdfs)}")
    if scan.text_files:
        print(f"  Text:   {len(scan.text_files)}")
    if scan.urls:
        print(f"  URLs:   {len(scan.urls)}")

    # Collect text from text files.
    text_content = ""
    for tf in scan.text_files:
        try:
            text_content += tf.read_text(encoding="utf-8", errors="ignore") + "\n"
        except OSError:
            pass

    # Extract text from PDFs.
    if scan.pdfs:
        print("Extracting text from PDFs \u2026")
        pdf_text = extract_pdf_text(scan.pdfs)
        if pdf_text:
            text_content = text_content + pdf_text + "\n"
            print(f"  Extracted text from {len(scan.pdfs)} PDF(s).")

    # Scrape declared sources (when SPEC.md exists) or fall back to
    # single-URL fetch from scanner results.
    source_url = ""
    scraped_text = ""
    code_examples: list = []
    doc_structures = None
    page_records: list = []
    product_ref_raw_pages: dict[str, str] = {}
    site_images: list[Path] = []
    site_videos: list[Path] = []

    product_name = ""

    # Check for a previously confirmed product identity.
    saved_product = load_product()
    if saved_product:
        product_name, source_url = saved_product
        print(f"\nUsing saved product: {product_name}")
        if source_url:
            print(f"  Source: {source_url}")

    # When a spec declares scrapeable sources, iterate them all with
    # their declared scrape depths.  Otherwise fall back to single-URL
    # fetch from scanner results / saved product identity.
    spec_sources = scrapeable_sources(spec) if spec else []
    if spec_sources:
        scrape_result = _scrape_declared_sources(spec)
        scraped_text = scrape_result.combined_text
        code_examples = scrape_result.all_code_examples
        doc_structures = scrape_result.merged_doc_structures
        page_records = scrape_result.all_page_records
        product_ref_raw_pages = scrape_result.product_ref_raw_pages
        _persist_scrape_result(scrape_result)
        if code_examples:
            print(f"Extracted {len(code_examples)} code example(s) from docs.")
        # Use the first product-reference source URL as the canonical
        # source_url for product identity (if not already saved).
        if not source_url:
            for src in spec_sources:
                if src.role == "product-reference":
                    source_url = src.url
                    break
    else:
        if scan.urls and not source_url:
            source_url = scan.urls[0]
            source_url, product_name = _validate_url(source_url)
            if not source_url:
                return

        if source_url:
            print(f"\nFetching {source_url} \u2026")
            (
                scraped_text,
                code_examples,
                doc_structures,
                page_records,
                product_ref_raw_pages,
            ) = fetch_site(source_url)
            if code_examples:
                print(f"Extracted {len(code_examples)} code example(s) from docs.")

    # Download embedded images and videos from product-reference pages.
    # Returns all media (cached + new).  Kept separate from scan
    # so move_references does not try to relocate files that are
    # already under .duplo/site_media/.
    if product_ref_raw_pages:
        site_images, site_videos = _download_site_media(product_ref_raw_pages)
        if site_images:
            print(f"  {len(site_images)} image(s) from product site.")
        if site_videos:
            print(f"  {len(site_videos)} video(s) from product site.")

    if not saved_product:
        product_name = _confirm_product(product_name, source_url)
        if not product_name:
            return
        save_product(product_name, source_url)
        print("Product identity saved to .duplo/product.json.")

    # Extract frames from behavioral-target videos at scene change points.
    # When a spec is present, only videos declared as behavioral-target
    # are processed; otherwise fall back to all scanned videos.  Scraped
    # demo videos from product-reference pages (site_videos) are
    # first-class behavioral input and are merged into the set.
    if spec:
        behavioral_entries = [
            e for e in format_behavioral_references(spec) if e.path.suffix.lower() in _VIDEO_EXTS
        ]
        behavioral_videos = [e.path for e in behavioral_entries] + site_videos
    else:
        behavioral_entries = []
        behavioral_videos = list(scan.videos) + site_videos
    assert len(behavioral_videos) == len(set(behavioral_videos)), (
        "Duplicate source path across ref-declared and scraped videos"
    )
    video_frames: list[Path] = []
    accepted_frames_by_path: dict[Path, list[Path]] = {}
    if behavioral_videos:
        print(f"\nExtracting frames from {len(behavioral_videos)} video(s) \u2026")
        video_frames, accepted_frames_by_path = _run_video_frame_pipeline(
            behavioral_videos,
        )
        if video_frames:
            print(f"  Total: {len(video_frames)} frame(s) from video(s)")

    # Compose design input from four sources using per-source lookup.
    # Source 2: frames from videos with visual-target role.
    # Source 3: frames from scraped product-reference videos.
    # The per-source lookup uses exact path keys from
    # _accepted_frames_by_source, replacing stem-based matching.
    #
    # Frame-content-hash dedup: ref-declared frames (source 2) added
    # to seen_frame_hashes first; scraped frames (source 3) added only
    # if their content-hash is not already seen.  A user with both a
    # ref/-declared local copy of a demo video AND the same video on a
    # scraped product page should not have identical frames counted
    # twice.  ref-declared frames win on collision.
    if spec:
        vt_frames_raw = [
            frame
            for entry in behavioral_entries
            if "visual-target" in entry.roles
            for frame in accepted_frames_by_path.get(entry.path, [])
        ]
        scraped_frames_raw = [
            frame for vp in site_videos for frame in accepted_frames_by_path.get(vp, [])
        ]
        seen_frame_hashes: set[str] = set()
        vt_frames: list[Path] = []
        for frame in vt_frames_raw:
            h = hashlib.sha256(frame.read_bytes()).hexdigest()
            if h not in seen_frame_hashes:
                vt_frames.append(frame)
                seen_frame_hashes.add(h)
        site_video_frames: list[Path] = []
        for frame in scraped_frames_raw:
            h = hashlib.sha256(frame.read_bytes()).hexdigest()
            if h not in seen_frame_hashes:
                site_video_frames.append(frame)
                seen_frame_hashes.add(h)
        design_input = collect_design_input(
            spec,
            vt_frames,
            site_images,
            site_video_frames,
        )
    else:
        # No spec: dedup video_frames (ref/) against scraped video
        # frames using the same content-hash pattern.
        seen_frame_hashes_ns: set[str] = set()
        deduped_video_frames: list[Path] = []
        for frame in video_frames:
            h = hashlib.sha256(frame.read_bytes()).hexdigest()
            if h not in seen_frame_hashes_ns:
                deduped_video_frames.append(frame)
                seen_frame_hashes_ns.add(h)
        deduped_scraped_frames: list[Path] = []
        for frame_path in (
            frame for vp in site_videos for frame in accepted_frames_by_path.get(vp, [])
        ):
            h = hashlib.sha256(frame_path.read_bytes()).hexdigest()
            if h not in seen_frame_hashes_ns:
                deduped_scraped_frames.append(frame_path)
                seen_frame_hashes_ns.add(h)
        design_input = (
            list(scan.images) + deduped_video_frames + site_images + deduped_scraped_frames
        )
    design = DesignRequirements()
    autogen_present = bool(spec and spec.design.auto_generated.strip())
    if design_input and not autogen_present:
        print("\nExtracting visual design from images \u2026")
        design = extract_design(design_input)
        if design.colors or design.fonts or design.layout:
            print(f"Extracted design details from {len(design.source_images)} image(s).")
            # Write autogen block to SPEC.md if present.
            spec_path = Path.cwd() / "SPEC.md"
            if spec_path.exists():
                existing = spec_path.read_text(encoding="utf-8")
                body = format_design_block(design)
                if body:
                    modified = update_design_autogen(existing, body)
                    if modified != existing:
                        spec_path.write_text(modified, encoding="utf-8")
            save_design_requirements(dataclasses.asdict(design))
        else:
            print("Could not extract design details from images.")
    elif design_input:
        record_failure(
            "orchestrator:design_extraction",
            "io",
            f"Autogen design block exists in SPEC.md; skipped Vision extraction."
            f" Delete the BEGIN/END AUTO-GENERATED block to regenerate"
            f" from {len(design_input)} input image(s).",
        )
        print("\nDesign autogen block already exists in SPEC.md; skipping Vision.")

    # Extract text from docs-role references (PDFs, text, markdown).
    if spec:
        doc_refs = format_doc_references(spec)
        if doc_refs:
            print("Extracting text from docs references \u2026")
            docs_text = docs_text_extractor(doc_refs)
            if docs_text:
                text_content = text_content + docs_text + "\n"
                print(f"  Extracted text from {len(doc_refs)} docs reference(s).")

    combined_text = scraped_text
    if text_content:
        combined_text = text_content + "\n" + combined_text

    print("\nExtracting features \u2026")
    features = extract_features(
        combined_text,
        spec_text=spec_prompt,
        scope_include=spec.scope_include if spec else None,
        scope_exclude=spec.scope_exclude if spec else None,
    )
    if features and spec and spec.scope_exclude:
        features = [f for f in features if not _matches_excluded(f, spec.scope_exclude)]
    if features:
        print(f"Found {len(features)} feature(s).")
        features = select_features(features)
    else:
        print("No features extracted.")

    # Parse build preferences from ## Architecture (LLM) or ask interactively.
    arch_hash = ""
    if spec and spec.architecture:
        prefs = parse_build_preferences(spec.architecture)
        arch_hash = architecture_hash(spec.architecture)
        for w in validate_build_preferences(prefs):
            print(f"Warning: {w}")
    else:
        prefs = ask_preferences()

    project_name = Path.cwd().name
    default_app_name = project_name.replace("-", " ").replace("_", " ").title()
    app_name = (
        input(f"macOS app name for appshot screenshots [{default_app_name}]: ").strip()
        or default_app_name
    )

    # Ensure .duplo/ directory exists.
    Path(".duplo").mkdir(parents=True, exist_ok=True)

    project_dir = Path(".")
    roadmap = _init_project(
        url=source_url,
        project_name=project_name,
        project_dir=project_dir,
        features=features,
        prefs=prefs,
        app_name=app_name,
        text=scraped_text,
        code_examples=code_examples,
        doc_structures=doc_structures,
        page_records=page_records,
        raw_pages=product_ref_raw_pages,
        design=design,
        spec_text=spec_prompt,
        arch_hash=arch_hash,
    )

    # Move processed reference files into .duplo/references/.
    ref_files = list(scan.images) + list(scan.videos) + list(scan.pdfs) + list(scan.text_files)
    if ref_files:
        moved = move_references(ref_files)
        if moved:
            print(f"Moved {len(moved)} reference file(s) to .duplo/references/.")

    # Save initial file hash manifest.
    hashes = compute_hashes(".")
    save_hashes(hashes)
    print(f"File hash manifest saved ({len(hashes)} file(s)).")

    if roadmap:
        print("\n" + format_roadmap(roadmap))
        answer = input("Approve this roadmap? [Y/n] ").strip().lower()
        if answer and answer != "y":
            print("Roadmap not approved. Edit .duplo/duplo.json manually or re-run duplo.")
            return
        save_roadmap(roadmap, target_dir=project_dir)
        print("Roadmap saved.")

        # Generate and execute Phase 1.
        phase_num, phase_info = get_current_phase()
        phase_label = (
            f"Phase {phase_num}: {phase_info['title']}" if phase_info else f"Phase {phase_num}"
        )
        print(f"\nGenerating {phase_label} PLAN.md \u2026")
        design_section = format_design_section(design) if design else ""
        content = generate_phase_plan(
            source_url,
            features,
            prefs,
            phase=phase_info,
            project_name=app_name,
            design_section=design_section,
            spec_text=spec_prompt,
        )
        # Append verification tasks from video frame descriptions.
        frame_descs = load_frame_descriptions()
        if frame_descs:
            print("Extracting verification cases from demo video \u2026")
            vcases = extract_verification_cases(frame_descs)
            if vcases:
                vtasks = format_verification_tasks(vcases)
                content = content.rstrip() + "\n" + vtasks
                print(f"  {len(vcases)} verification case(s) added.")
        # Append verification tasks from SPEC.md behavior contracts.
        if spec:
            spec_vtasks = format_contracts_as_verification(spec)
            if spec_vtasks:
                content = content.rstrip() + "\n" + spec_vtasks
                print(f"  {len(spec.behavior_contracts)} spec verification case(s) added.")
        saved = save_plan(content)
        print(f"Plan saved to {saved}")
        _plan_ready(phase_label)
    else:
        print("Failed to generate roadmap.")


def _analyze_new_files(
    file_names: list[str],
    spec: ProductSpec | None = None,
) -> UpdateSummary:
    """Analyze new or changed files the same way as first run.

    Images are sent to Vision for design extraction, PDFs are
    converted to text, and URLs found in text files are scraped.
    Results are saved to duplo.json.

    When *spec* is provided, design extraction input is composed via
    :func:`collect_design_input` (four-source model with dedup).

    Returns an :class:`UpdateSummary` with counts of what was analyzed.
    """
    summary = UpdateSummary()
    paths = [Path(name) for name in file_names]
    paths = [p for p in paths if p.exists()]
    if not paths:
        return summary

    scan = scan_files(paths)
    analyzed_anything = False

    # Extract frames from new behavioral-target videos at scene change points.
    # When spec is present, only videos declared as behavioral-target are
    # processed; otherwise fall back to all scanned videos.
    if spec:
        behavioral_entries = [
            e for e in format_behavioral_references(spec) if e.path.suffix.lower() in _VIDEO_EXTS
        ]
        behavioral_set = {e.path.resolve() for e in behavioral_entries}
        behavioral_videos = [v for v in scan.videos if v.resolve() in behavioral_set]
    else:
        behavioral_entries = []
        behavioral_videos = list(scan.videos)
    video_frames: list[Path] = []
    accepted_frames_by_path: dict[Path, list[Path]] = {}
    if behavioral_videos:
        print(f"\nExtracting frames from {len(behavioral_videos)} new video(s) \u2026")
        video_frames, accepted_frames_by_path = _run_video_frame_pipeline(
            behavioral_videos,
        )
        summary.videos_found = len(behavioral_videos)
        analyzed_anything = True
        summary.video_frames_extracted = len(video_frames)

    # Compose design input via four-source model when spec is
    # available; fall back to all images + frames otherwise.
    if spec:
        vt_frames = [
            frame
            for entry in behavioral_entries
            if "visual-target" in entry.roles
            for frame in accepted_frames_by_path.get(entry.path, [])
        ]
        design_input = collect_design_input(spec, vt_frames)
    else:
        design_input = list(scan.images) + video_frames
    autogen_present = bool(spec and spec.design.auto_generated.strip())
    if design_input and not autogen_present:
        print(f"\nAnalyzing {len(design_input)} image(s) with Vision \u2026")
        design = extract_design(design_input)
        if design.colors or design.fonts or design.layout:
            spec_path = Path.cwd() / "SPEC.md"
            if spec_path.exists():
                existing = spec_path.read_text(encoding="utf-8")
                body = format_design_block(design)
                if body:
                    modified = update_design_autogen(existing, body)
                    if modified != existing:
                        spec_path.write_text(modified, encoding="utf-8")
            save_design_requirements(dataclasses.asdict(design))
            print(f"  Updated design requirements from {len(design.source_images)} image(s).")
            summary.images_analyzed = len(design.source_images)
            analyzed_anything = True
        else:
            print("  Could not extract design details from images.")
    elif design_input:
        record_failure(
            "orchestrator:design_extraction",
            "io",
            f"Autogen design block exists in SPEC.md; skipped Vision extraction."
            f" Delete the BEGIN/END AUTO-GENERATED block to regenerate"
            f" from {len(design_input)} input image(s).",
        )
        print("\nDesign autogen block already exists in SPEC.md; skipping Vision.")

    # Extract text from new PDFs.
    if scan.pdfs:
        print(f"\nExtracting text from {len(scan.pdfs)} new PDF(s) \u2026")
        pdf_text = extract_pdf_text(scan.pdfs)
        if pdf_text:
            summary.collected_text += pdf_text + "\n"
            print(f"  Extracted text from {len(scan.pdfs)} PDF(s).")
            summary.pdfs_extracted = len(scan.pdfs)
            analyzed_anything = True

    # Collect text from new text files.
    if scan.text_files:
        text_content = ""
        for tf in scan.text_files:
            try:
                text_content += tf.read_text(encoding="utf-8", errors="ignore") + "\n"
            except OSError:
                pass
        if text_content.strip():
            summary.collected_text += text_content
            print(f"\nRead {len(scan.text_files)} new text file(s).")
            summary.text_files_read = len(scan.text_files)
            analyzed_anything = True

    # Fetch new URLs.
    if scan.urls:
        existing_urls = _load_existing_urls()
        new_urls = [u for u in scan.urls if u not in existing_urls]
        if new_urls:
            print(f"\nFetching {len(new_urls)} new URL(s) \u2026")
            fetched = 0
            all_page_records = []
            all_raw_pages: dict[str, str] = {}
            all_code_examples = []
            all_doc_structures = DocStructures()
            for url in new_urls:
                print(f"  Fetching {url} \u2026")
                try:
                    url_text, code_examples, doc_structures, page_records, raw_pages = fetch_site(
                        url
                    )
                    if url_text:
                        summary.collected_text += url_text + "\n"
                    if page_records:
                        all_page_records.extend(page_records)
                        if raw_pages:
                            all_raw_pages.update(raw_pages)
                    if code_examples:
                        all_code_examples.extend(code_examples)
                    if doc_structures:
                        all_doc_structures.feature_tables.extend(doc_structures.feature_tables)
                        all_doc_structures.operation_lists.extend(doc_structures.operation_lists)
                        all_doc_structures.unit_lists.extend(doc_structures.unit_lists)
                        all_doc_structures.function_refs.extend(doc_structures.function_refs)
                    fetched += 1
                    analyzed_anything = True
                except Exception as exc:
                    print(f"  Failed to fetch {url}: {exc}")
            if all_page_records:
                save_reference_urls(all_page_records)
                if all_raw_pages:
                    save_raw_content(all_raw_pages, all_page_records)
            if all_code_examples:
                save_examples(all_code_examples)
            if all_doc_structures:
                save_doc_structures(all_doc_structures)
            summary.urls_fetched = fetched

    # Move processed reference files into .duplo/references/.
    ref_files = list(scan.images) + list(scan.videos) + list(scan.pdfs) + list(scan.text_files)
    if ref_files:
        moved = move_references(ref_files)
        if moved:
            print(f"Moved {len(moved)} new reference file(s) to .duplo/references/.")

    if not analyzed_anything:
        print("No analyzable reference materials in new files.")

    return summary


def _load_existing_urls() -> set[str]:
    """Load previously scraped URLs from duplo.json."""
    duplo_path = Path(_DUPLO_JSON)
    if not duplo_path.exists():
        return set()
    try:
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    records = data.get("reference_urls", [])
    return {r["url"] for r in records if "url" in r}


def _rescrape_product_url(
    spec: ProductSpec | None = None,
) -> tuple[int, int, str]:
    """Re-scrape the product URL stored in duplo.json with the deep extractor.

    If ``source_url`` is set, fetches it again via :func:`fetch_site` and
    updates the reference URLs and raw page content in duplo.json.  This
    picks up any changes on the product site since the last run.

    When *spec* is provided, design extraction input is composed via
    :func:`collect_design_input` (four-source model with dedup).

    Returns ``(pages_updated, examples_updated, scraped_text)`` counts
    and the scraped text content for downstream feature re-extraction.
    """
    duplo_path = Path(_DUPLO_JSON)
    if not duplo_path.exists():
        return 0, 0, ""
    try:
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0, 0, ""
    source_url = data.get("source_url", "")
    if not source_url:
        return 0, 0, ""

    # Skip re-scrape if last scrape was less than 10 minutes ago.
    last_scrape = data.get("last_scrape_timestamp", 0)
    elapsed = time.time() - last_scrape
    if last_scrape and elapsed < 600:
        minutes_ago = int(elapsed / 60)
        print(f"\nUsing recent scrape data ({minutes_ago} minutes ago).")
        return 0, 0, ""

    print(f"\nRe-scraping {source_url} \u2026")
    try:
        (
            scraped_text,
            code_examples,
            doc_structures,
            page_records,
            product_ref_raw_pages,
        ) = fetch_site(source_url)
    except Exception as exc:
        print(f"  Failed to re-scrape {source_url}: {exc}")
        return 0, 0, ""

    # Compare new content hashes against stored hashes to detect changes.
    # Only skip when we have new pages to compare and all hashes match.
    old_hashes = {r["content_hash"] for r in data.get("reference_urls", []) if "content_hash" in r}
    new_hashes = {r.content_hash for r in page_records} if page_records else set()

    if new_hashes and old_hashes == new_hashes:
        print("  Site content unchanged, skipping feature re-extraction.")
        data["last_scrape_timestamp"] = time.time()
        duplo_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return 0, 0, ""

    pages_updated = 0
    examples_updated = 0

    if page_records:
        save_reference_urls(page_records)
        if product_ref_raw_pages:
            save_raw_content(product_ref_raw_pages, page_records)
        pages_updated = len(page_records)
        print(f"  Updated {pages_updated} page record(s).")
    if code_examples:
        existing = load_examples()
        if existing:
            existing_keys = {(e.input, e.source_url) for e in existing}
            merged = list(existing)
            new_count = 0
            for ex in code_examples:
                key = (ex.input, ex.source_url)
                if key not in existing_keys:
                    merged.append(ex)
                    existing_keys.add(key)
                    new_count += 1
            save_examples(merged)
            examples_updated = new_count
        else:
            save_examples(code_examples)
            examples_updated = len(code_examples)
        print(f"  Updated {examples_updated} code example(s).")
    if doc_structures:
        save_doc_structures(doc_structures)

    # Download embedded media from product-reference pages.
    # The product URL is a product-reference source, so all raw pages
    # from this single-source re-scrape are product-reference pages.
    # Returns all media (cached + new) for a complete inventory.
    if product_ref_raw_pages:
        site_images, site_videos = _download_site_media(product_ref_raw_pages)
        if site_images:
            print(f"  {len(site_images)} image(s) from product site.")
        site_video_frames: list[Path] = []
        if site_videos:
            print(f"  {len(site_videos)} video(s) from product site.")
            site_video_frames, _ = _run_video_frame_pipeline(
                site_videos,
                indent="  ",
            )
        if spec:
            design_input = collect_design_input(
                spec,
                site_images=site_images,
                site_video_frames=site_video_frames,
            )
        else:
            design_input = site_images + site_video_frames
        autogen_present = bool(spec and spec.design.auto_generated.strip())
        if design_input and not autogen_present:
            design = extract_design(design_input)
            if design.colors or design.fonts or design.layout:
                spec_path = Path.cwd() / "SPEC.md"
                if spec_path.exists():
                    existing = spec_path.read_text(encoding="utf-8")
                    body = format_design_block(design)
                    if body:
                        modified = update_design_autogen(existing, body)
                        if modified != existing:
                            spec_path.write_text(modified, encoding="utf-8")
                save_design_requirements(dataclasses.asdict(design))
                print(f"  Updated design from {len(design.source_images)} image(s).")
        elif design_input:
            record_failure(
                "orchestrator:design_extraction",
                "io",
                f"Autogen design block exists in SPEC.md; skipped Vision"
                f" extraction. Delete the BEGIN/END AUTO-GENERATED block"
                f" to regenerate from {len(design_input)} input image(s).",
            )
            print("  Design autogen block already exists; skipping Vision.")

    # Re-read duplo.json to pick up writes from save_reference_urls,
    # save_doc_structures, etc. that happened since our initial read.
    try:
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        print("  Warning: could not re-read duplo.json; skipping timestamp update.")
        return pages_updated, examples_updated, scraped_text
    data["last_scrape_timestamp"] = time.time()
    duplo_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    return pages_updated, examples_updated, scraped_text


def _detect_and_append_gaps(
    scope_exclude: list[str] | None = None,
    spec: ProductSpec | None = None,
) -> tuple[int, int, int, int]:
    """Compare features and examples from duplo.json against PLAN.md.

    If gaps are found, appends new checklist tasks to PLAN.md for
    features or examples not yet covered by the current plan.

    Args:
        scope_exclude: Terms from SPEC.md ``scope_exclude``. Features
            matching any term are filtered out before gap detection.
        spec: Parsed SPEC.md.  When present, the AUTO-GENERATED block
            in ``## Design`` is parsed and merged with ``duplo.json``'s
            ``design_requirements`` for design-gap detection.  Redundant
            during transition; can simplify in Phase 7.

    Returns ``(missing_features, missing_examples, design_refinements,
    tasks_appended)`` counts.
    """
    plan_path = Path("PLAN.md")
    duplo_path = Path(_DUPLO_JSON)
    if not plan_path.exists() or not duplo_path.exists():
        return 0, 0, 0, 0

    plan_content = plan_path.read_text(encoding="utf-8")
    try:
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0, 0, 0, 0

    features = [_feature_from_dict(f) for f in data.get("features", [])]
    if scope_exclude:
        features = [f for f in features if not _matches_excluded(f, scope_exclude)]
    if not features:
        return 0, 0, 0, 0

    examples = load_examples()

    prefs = data.get("preferences", {})
    platform = prefs.get("platform", "")
    language = prefs.get("language", "")

    print("\nComparing features and examples against PLAN.md \u2026")
    result = detect_gaps(
        plan_content, features, examples or None, platform=platform, language=language
    )

    # Check for design refinements not yet in the plan.
    # Merge design data from duplo.json AND SPEC.md's AUTO-GENERATED
    # block (redundant during transition; can simplify in Phase 7).
    design_data = data.get("design_requirements", {})
    spec_design_data: dict = {}
    if spec and spec.design.auto_generated:
        spec_design_data = _parse_design_markdown(spec.design.auto_generated)
    merged_design = _merge_design_dicts(design_data, spec_design_data)
    if merged_design:
        design_gaps = detect_design_gaps(plan_content, merged_design)
        result.design_refinements = design_gaps

    if (
        not result.missing_features
        and not result.missing_examples
        and not result.design_refinements
    ):
        print("  All features, examples, and design details are covered by the plan.")
        return 0, 0, 0, 0

    tasks_appended = 0
    gap_tasks = format_gap_tasks(result)
    if gap_tasks:
        updated = plan_content.rstrip() + "\n" + gap_tasks
        plan_path.write_text(updated, encoding="utf-8")
        tasks_appended = sum(1 for line in gap_tasks.splitlines() if line.startswith("- [ ] "))
        print(f"  Appended {tasks_appended} gap task(s) to PLAN.md.")

    return (
        len(result.missing_features),
        len(result.missing_examples),
        len(result.design_refinements),
        tasks_appended,
    )


def _print_summary(summary: UpdateSummary) -> None:
    """Print a consolidated summary of what was found and added."""
    found_lines: list[str] = []
    if summary.files_added or summary.files_changed or summary.files_removed:
        parts = []
        if summary.files_added:
            parts.append(f"{summary.files_added} added")
        if summary.files_changed:
            parts.append(f"{summary.files_changed} changed")
        if summary.files_removed:
            parts.append(f"{summary.files_removed} removed")
        found_lines.append(f"  Files: {', '.join(parts)}")
    if summary.images_analyzed:
        found_lines.append(f"  Images analyzed: {summary.images_analyzed}")
    if summary.videos_found:
        found_lines.append(f"  Videos found: {summary.videos_found}")
    if summary.video_frames_extracted:
        found_lines.append(f"  Video frames extracted: {summary.video_frames_extracted}")
    if summary.pdfs_extracted:
        found_lines.append(f"  PDFs extracted: {summary.pdfs_extracted}")
    if summary.text_files_read:
        found_lines.append(f"  Text files read: {summary.text_files_read}")
    if summary.urls_fetched:
        found_lines.append(f"  URLs fetched: {summary.urls_fetched}")
    if summary.pages_rescraped:
        found_lines.append(f"  Pages re-scraped: {summary.pages_rescraped}")
    if summary.examples_rescraped:
        found_lines.append(f"  Code examples updated: {summary.examples_rescraped}")
    if summary.new_features:
        found_lines.append(f"  New features extracted: {summary.new_features}")

    added_lines: list[str] = []
    if summary.missing_features:
        added_lines.append(f"  Missing features: {summary.missing_features}")
    if summary.missing_examples:
        added_lines.append(f"  Missing examples: {summary.missing_examples}")
    if summary.design_refinements:
        added_lines.append(f"  Design refinements: {summary.design_refinements}")
    if summary.tasks_appended:
        added_lines.append(f"  Tasks appended to PLAN.md: {summary.tasks_appended}")

    if not found_lines and not added_lines:
        print("\nSummary: No changes detected, nothing added.")
        return

    print("\n--- Update summary ---")
    if found_lines:
        print("Found:")
        for line in found_lines:
            print(line)
    if added_lines:
        print("Added:")
        for line in added_lines:
            print(line)
    if not added_lines:
        print("No new tasks added.")
    print("---------------------")


def _subsequent_run() -> None:
    """Handle a subsequent duplo run.

    Three states:
    1. PLAN.md complete \u2192 record phase, advance, fall through to generate next.
    2. PLAN.md incomplete \u2192 tell user to run mcloop, return.
    3. No PLAN.md \u2192 regenerate roadmap if needed, generate plan for current phase.
    """
    spec = read_spec()
    if spec:
        print(f"Product spec loaded from SPEC.md ({len(spec.raw)} chars).")
        validation = validate_for_run(spec)
        for warning in validation.warnings:
            print(f"Warning: {warning}")
        if validation.errors:
            for err in validation.errors:
                print(f"Error: {err}", file=sys.stderr)
            sys.exit(1)
    spec_prompt = format_spec_for_prompt(spec) if spec else ""

    duplo_path = Path(_DUPLO_JSON)
    try:
        status_data = json.loads(duplo_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        status_data = {}
    _print_status(status_data, plan_exists=Path("PLAN.md").exists())

    summary = UpdateSummary()

    # Detect file changes since last run.
    old_hashes = load_hashes(".")
    new_hashes = compute_hashes(".")
    diff = diff_hashes(old_hashes, new_hashes)
    if diff.added or diff.changed or diff.removed:
        summary.files_added = len(diff.added)
        summary.files_changed = len(diff.changed)
        summary.files_removed = len(diff.removed)
        print("File changes detected since last run:")
        for name in diff.added:
            print(f"  + {name}")
        for name in diff.changed:
            print(f"  ~ {name}")
        for name in diff.removed:
            print(f"  - {name}")

        # Analyze new/changed files under ref/ (matching scan_directory).
        changed_files = [f for f in diff.added + diff.changed if f.startswith("ref/")]
        if changed_files:
            analysis = _analyze_new_files(changed_files, spec=spec)
            summary.images_analyzed = analysis.images_analyzed
            summary.videos_found = analysis.videos_found
            summary.pdfs_extracted = analysis.pdfs_extracted
            summary.text_files_read = analysis.text_files_read
            summary.urls_fetched = analysis.urls_fetched
            summary.video_frames_extracted = analysis.video_frames_extracted
            summary.collected_text = analysis.collected_text

    # Scrape declared sources (when SPEC.md has scrapeable entries) or
    # fall back to single-URL re-scrape from duplo.json.
    scraped_text = ""
    spec_sources = scrapeable_sources(spec) if spec else []
    if spec_sources:
        scrape_result = _scrape_declared_sources(spec)
        scraped_text = scrape_result.combined_text
        _persist_scrape_result(scrape_result)
        summary.pages_rescraped = len(scrape_result.all_page_records)
        summary.examples_rescraped = len(scrape_result.all_code_examples)
    else:
        pages, examples, scraped_text = _rescrape_product_url(spec=spec)
        summary.pages_rescraped = pages
        summary.examples_rescraped = examples

    # Extract text from docs-role references.
    if spec:
        doc_refs = format_doc_references(spec)
        if doc_refs:
            print("Extracting text from docs references \u2026")
            docs_text = docs_text_extractor(doc_refs)
            if docs_text:
                summary.collected_text += docs_text + "\n"
                print(f"  Extracted text from {len(doc_refs)} docs reference(s).")

    # Combine text from new files with re-scraped content for feature
    # extraction.
    combined_text = scraped_text
    if summary.collected_text.strip():
        combined_text = summary.collected_text + "\n" + combined_text

    # Re-extract features from the updated content and merge
    # new ones into duplo.json so the gap detector can find them.
    if combined_text:
        print("\nRe-extracting features \u2026")
        try:
            old_data = json.loads(Path(_DUPLO_JSON).read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old_data = {}
        existing_names = [f["name"] for f in old_data.get("features", [])]
        new_features = extract_features(
            combined_text,
            existing_names=existing_names,
            spec_text=spec_prompt,
            scope_include=spec.scope_include if spec else None,
            scope_exclude=spec.scope_exclude if spec else None,
        )
        if new_features and spec and spec.scope_exclude:
            new_features = [
                f for f in new_features if not _matches_excluded(f, spec.scope_exclude)
            ]
        if new_features:
            try:
                old_data = json.loads(Path(_DUPLO_JSON).read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                print(f"Error: {_DUPLO_JSON} contains invalid JSON. Delete or fix it.")
                return
            old_count = len(old_data.get("features", []))
            save_features(new_features)
            updated_data = json.loads(Path(_DUPLO_JSON).read_text(encoding="utf-8"))
            new_count = len(updated_data.get("features", [])) - old_count
            if new_count > 0:
                print(f"  {new_count} new feature(s) merged into duplo.json.")
                summary.new_features = new_count
            else:
                print("  No new features found.")
        else:
            print("  No features extracted.")

    save_hashes(compute_hashes("."))

    # Compare features/examples against current plan and append gap tasks.
    # Skip gap detection if the plan is fully complete (all tasks checked)
    # or if the plan has unchecked tasks (user may have manually edited
    # the plan, and appending gap tasks would create a State 2 deadlock).
    # Gaps will be incorporated into the next phase's plan instead.
    plan_path_check = Path("PLAN.md")
    plan_complete = plan_path_check.exists() and _plan_is_complete()
    plan_has_unchecked = plan_path_check.exists() and _plan_has_unchecked_tasks()
    if not plan_complete and not plan_has_unchecked:
        mf, me, dr, ta = _detect_and_append_gaps(
            scope_exclude=spec.scope_exclude if spec else None,
            spec=spec,
        )
        summary.missing_features = mf
        summary.missing_examples = me
        summary.design_refinements = dr
        summary.tasks_appended = ta

    _print_summary(summary)

    duplo_path = Path(_DUPLO_JSON)
    try:
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"Error: {duplo_path} contains invalid JSON. Delete or fix it.")
        return
    app_name = data.get("app_name", "")

    plan_path = Path("PLAN.md")

    # State 1: PLAN.md complete \u2192 record phase completion, then fall through.
    if plan_path.exists() and _plan_is_complete():
        phase_num, phase_info = get_current_phase()
        phase_label = (
            f"Phase {phase_num}: {phase_info['title']}" if phase_info else f"Phase {phase_num}"
        )
        content = plan_path.read_text(encoding="utf-8")
        print(f"Completing {phase_label} (all tasks done).")
        _complete_phase(content, app_name, phase_label)
        # Reload data after phase completion modified duplo.json.
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
        _print_feature_status(data)

    # State 2: PLAN.md incomplete \u2192 tell user to continue.
    elif plan_path.exists():
        phase_num, phase_info = get_current_phase()
        phase_label = (
            f"Phase {phase_num}: {phase_info['title']}" if phase_info else f"Phase {phase_num}"
        )
        print(f"{phase_label} has uncompleted tasks in PLAN.md.")
        print("Run mcloop to continue building.")
        return

    # State 3: No PLAN.md \u2192 generate plan for current phase.
    phase_num, phase_info = get_current_phase()

    # If no roadmap exists or the existing one is fully consumed,
    # regenerate from remaining unimplemented features.
    roadmap = data.get("roadmap", [])
    if not roadmap or phase_info is None:
        _print_feature_status(data)
        remaining = _unimplemented_features(data)
        if not remaining:
            print("All features implemented. Nothing to do.")
            return
        source_url = data.get("source_url", "")
        preferences = _load_preferences(data, spec)
        history = _build_completion_history(data)
        print(f"\nGenerating new roadmap for {len(remaining)} remaining feature(s) \u2026")
        new_roadmap = generate_roadmap(
            source_url,
            remaining,
            preferences,
            completion_history=history,
            spec_text=spec_prompt,
        )
        if not new_roadmap:
            print("Error: failed to generate roadmap.")
            return
        save_roadmap(new_roadmap)
        print(format_roadmap(new_roadmap))
        # Reload state after saving roadmap.
        data = json.loads(Path(_DUPLO_JSON).read_text(encoding="utf-8"))
        phase_num, phase_info = get_current_phase()

    if phase_info is None:
        print("All features implemented. Nothing to do.")
        return

    # Phase number = number of completed phases + 1.
    history_phase_number = len(data.get("phases", [])) + 1
    phase_label = (
        f"Phase {history_phase_number}: {phase_info['title']}"
        if phase_info
        else f"Phase {history_phase_number}"
    )

    # Let the user confirm/adjust which features go into this phase.
    remaining = _unimplemented_features(data)
    phase_feature_names = phase_info.get("features", [])
    if remaining:
        selected = select_features(
            remaining, recommended=phase_feature_names, phase_label=phase_label
        )
        if not selected:
            print("No features selected. Nothing to do.")
            return
        # Update phase_info to reflect user's selection.
        phase_info = dict(phase_info)
        phase_info["features"] = [f.name for f in selected]

    # Include open issues in the phase plan.
    all_issues = data.get("issues", [])
    open_issues = [i for i in all_issues if i.get("status", "open") == "open"]
    if open_issues:
        print(f"\n{len(open_issues)} open issue(s) will be included in this phase:")
        for iss in open_issues:
            print(f"  - {iss['description']}")
        selected_issues = select_issues(all_issues)
        if selected_issues:
            phase_info = dict(phase_info)
            phase_info["issues"] = [iss["description"] for iss in selected_issues]

    # Generate plan for current phase.
    source_url = data.get("source_url", "")
    features = [_feature_from_dict(f) for f in data.get("features", [])]
    preferences = _load_preferences(data, spec)

    print(f"Generating {phase_label} PLAN.md \u2026")
    design_data = data.get("design_requirements", {})
    design_section = ""
    if design_data:
        _dfields = {f.name for f in dataclasses.fields(DesignRequirements)}
        loaded_design = DesignRequirements(
            **{k: v for k, v in design_data.items() if k in _dfields}
        )
        design_section = format_design_section(loaded_design)
    content = generate_phase_plan(
        source_url,
        features,
        preferences,
        phase=phase_info,
        project_name=data.get("app_name", ""),
        design_section=design_section,
        phase_number=history_phase_number,
        spec_text=spec_prompt,
    )
    # Append verification tasks from video frame descriptions.
    frame_descs = load_frame_descriptions()
    if frame_descs:
        print("Extracting verification cases from demo video \u2026")
        vcases = extract_verification_cases(frame_descs)
        if vcases:
            vtasks = format_verification_tasks(vcases)
            content = content.rstrip() + "\n" + vtasks
            print(f"  {len(vcases)} verification case(s) added.")
    # Append verification tasks from SPEC.md behavior contracts.
    spec_vtasks = ""
    if spec:
        spec_vtasks = format_contracts_as_verification(spec)
    if spec_vtasks:
        content = content.rstrip() + "\n" + spec_vtasks
        print(f"  {len(spec.behavior_contracts)} spec verification case(s) added.")
    saved = save_plan(content)
    print(f"{phase_label} plan saved to {saved}")
    _plan_ready(phase_label)


def _partition_features(
    data: dict,
) -> tuple[list[Feature], list[Feature]]:
    """Split features into implemented and remaining lists.

    Returns ``(implemented, remaining)`` where *implemented* contains
    features with ``status == "implemented"`` and *remaining* contains
    everything else (``"pending"``, ``"partial"``, or missing status).
    """
    implemented: list[Feature] = []
    remaining: list[Feature] = []
    for f in data.get("features", []):
        feat = _feature_from_dict(f)
        if f.get("status", "pending") == "implemented":
            implemented.append(feat)
        else:
            remaining.append(feat)
    return implemented, remaining


def _unimplemented_features(data: dict) -> list[Feature]:
    """Return features from *data* whose status is not ``"implemented"``."""
    _, remaining = _partition_features(data)
    return remaining


def _print_feature_status(data: dict) -> None:
    """Print a summary of implemented vs remaining features."""
    implemented, remaining = _partition_features(data)
    total = len(implemented) + len(remaining)
    if total == 0:
        return
    print(f"\nFeature status: {len(implemented)}/{total} implemented")
    if implemented:
        print("  Implemented:")
        for f in implemented:
            phase = f" ({f.implemented_in})" if f.implemented_in else ""
            print(f"    - {f.name}{phase}")
    if remaining:
        print("  Remaining:")
        for f in remaining:
            status = f.status if f.status != "pending" else ""
            label = f" [{status}]" if status else ""
            print(f"    - {f.name}{label}")


def _print_status(data: dict, *, plan_exists: bool = False) -> None:
    """Print current phase number, features implemented vs remaining, and open issues."""
    phases_completed = len(data.get("phases", []))
    current_phase = phases_completed + 1

    implemented, remaining = _partition_features(data)
    total = len(implemented) + len(remaining)

    issues = data.get("issues", [])
    open_issues = [i for i in issues if i.get("status", "open") == "open"]

    app_name = data.get("app_name", "")
    prefix = f"{app_name}: " if app_name else ""
    if phases_completed > 0:
        phase_part = f"Phase {phases_completed} complete"
    elif plan_exists:
        phase_part = f"Phase {current_phase} in progress"
    else:
        phase_part = f"Ready to generate Phase {current_phase}"
    issue_part = f", {len(open_issues)} open issues" if open_issues else ""
    print(f"\n{prefix}{phase_part}. {len(implemented)}/{total} features implemented{issue_part}.")


def _build_completion_history(data: dict) -> list[dict]:
    """Build a completion history from implemented features in *data*.

    Groups features by their ``implemented_in`` phase label and returns
    a list of ``{"phase": label, "features": [name, ...]}`` dicts,
    ordered by first appearance.
    """
    phase_features: dict[str, list[str]] = {}
    for f in data.get("features", []):
        if f.get("status") == "implemented" and f.get("implemented_in"):
            label = f["implemented_in"]
            phase_features.setdefault(label, []).append(f["name"])
    return [{"phase": label, "features": names} for label, names in phase_features.items()]


def _confirm_product(product_name: str, source_url: str) -> str:
    """Clearly state which product Duplo will duplicate and get confirmation.

    Returns the confirmed product name, or empty string if the user cancels.
    """
    if product_name:
        print(f"\n>>> Duplo will duplicate: {product_name}")
        if source_url:
            print(f"    Source: {source_url}")
    elif source_url:
        print(f"\n>>> Duplo will duplicate the product at: {source_url}")
    else:
        print("\n>>> No product URL found.")

    if product_name:
        answer = input("Is this correct? [Y/n] ").strip().lower()
        if answer and answer != "y":
            new_name = input("Enter the product name (or 'q' to quit): ").strip()
            if not new_name or new_name.lower() == "q":
                print("Cancelled.")
                return ""
            return new_name
        return product_name

    # No product name identified \u2014 ask the user.
    name = input("What product should Duplo duplicate? ").strip()
    if not name:
        print("No product specified. Cancelled.")
        return ""
    return name


def _validate_url(url: str) -> tuple[str, str]:
    """Validate that *url* points to a single product.

    If the page appears to list multiple products, present them
    and let the user choose one by number, enter a more specific URL,
    or press Enter to quit. Returns ``(validated_url, product_name)``
    where *product_name* may be empty if unknown.  Returns ``("", "")``
    if the user cancels.
    """
    print(f"\nValidating {url} \u2026")
    try:
        result = validate_product_url(url)
    except Exception as exc:
        print(f"Could not validate URL ({exc}). Proceeding anyway.")
        return url, ""

    if result.single_product:
        label = result.product_name or url
        print(f"Identified product: {label}")
        return url, result.product_name

    if result.unclear_boundaries:
        print(f"This URL has unclear product boundaries: {result.reason}")
        print(
            "\nDuplo can't tell what specific product to duplicate from this page.\n"
            "Please describe the product you want, enter a more specific URL,\n"
            "or press Enter to cancel."
        )
        choice = input("Product or URL: ").strip()
        if not choice:
            print("Cancelled.")
            return "", ""
        if choice.startswith(("http://", "https://")):
            return choice, ""
        # Treat as a product description \u2014 use it as the product name.
        return url, choice

    print(f"This URL appears to list multiple products: {result.reason}")
    if result.products:
        print("\nWhich product do you want to duplicate?\n")
        for i, name in enumerate(result.products, 1):
            print(f"  {i}. {name}")
        print(
            "\nEnter a number to select a product, a URL for a specific product,\n"
            "or press Enter to cancel."
        )
        choice = input("Choice: ").strip()
        if not choice:
            print("Cancelled.")
            return "", ""
        # Check if the user entered a number.
        try:
            idx = int(choice)
            if 1 <= idx <= len(result.products):
                selected = result.products[idx - 1]
                print(f"Selected: {selected}")
                return url, selected
            print(f"Invalid selection: {idx}. Cancelled.")
            return "", ""
        except ValueError:
            pass
        # Treat as a URL.
        if choice.startswith(("http://", "https://")):
            return choice, ""
        print(f"Not a valid number or URL: {choice}")
        return "", ""
    # No product list \u2014 ask for a URL.
    print("\nPlease provide a URL that points to a single product,\nor press Enter to cancel.")
    new_url = input("Product URL: ").strip()
    if new_url:
        return new_url, ""
    print("Cancelled.")
    return "", ""


def _init_project(
    *,
    url: str,
    project_name: str,
    project_dir: Path,
    features: list[Feature],
    prefs: BuildPreferences,
    app_name: str,
    text: str,
    code_examples: list | None,
    doc_structures=None,
    page_records: list | None = None,
    raw_pages: dict[str, str] | None = None,
    design: DesignRequirements | None = None,
    spec_text: str = "",
    arch_hash: str = "",
) -> list | None:
    """Core init logic: save selections, generate tests, write CLAUDE.md, build roadmap.

    Returns the generated roadmap (list of phase dicts) or ``None``.
    """
    saved = save_selections(
        url,
        features,
        prefs,
        app_name=app_name,
        arch_hash=arch_hash,
        code_examples=code_examples or None,
        doc_structures=doc_structures or None,
        target_dir=project_dir,
    )
    print(f"\nSelections saved to {saved}")
    if page_records:
        save_reference_urls(page_records, target_dir=project_dir)
        print(f"Saved {len(page_records)} reference URL(s) to duplo.json.")
        if raw_pages:
            save_raw_content(raw_pages, page_records, target_dir=project_dir)
            print(f"Saved raw content for {len(raw_pages)} page(s).")
    if code_examples:
        save_examples(code_examples, target_dir=project_dir)
        print(f"Saved {len(code_examples)} code example(s) to .duplo/examples/.")
        target_lang = detect_target_language(project_dir)
        if target_lang == "Python" or target_lang == "unknown":
            test_source = generate_test_source(code_examples, project_name=project_name)
            if test_source:
                tests_dir = project_dir / "tests"
                test_path = save_test_file(test_source, target_dir=tests_dir)
                print(f"Generated {len(code_examples)} test case(s) in {test_path}")
        else:
            print(
                f"Test generation skipped (target language: {target_lang}, only Python supported)."
            )
    if doc_structures:
        print("Saved doc structures to duplo.json.")
    if design and (design.colors or design.fonts or design.layout):
        save_design_requirements(dataclasses.asdict(design), target_dir=project_dir)
        print("Saved design requirements to duplo.json.")

    claude_md = write_claude_md(target_dir=project_dir)
    print(f"CLAUDE.md written to {claude_md}")

    print("\nGenerating build roadmap \u2026")
    roadmap = generate_roadmap(url, features, prefs, spec_text=spec_text)

    urls = _SECTION_URL_RE.findall(text)
    if urls:
        output_dir = project_dir / "screenshots"
        print(f"\nSaving reference screenshots to {output_dir}/ \u2026")
        saved_shots = save_reference_screenshots(urls, output_dir)
        print(f"Saved {len(saved_shots)} screenshot(s).")
        feature_names = [f.name for f in features]
        screenshot_map = map_screenshots_to_features(text, feature_names, output_dir)
        if screenshot_map:
            save_screenshot_feature_map(screenshot_map, target_dir=project_dir)
            print(f"Screenshot\u2192feature map saved ({len(screenshot_map)} entries).")

    return roadmap


def _current_phase_content(content: str) -> str:
    """Return lines belonging to the current phase section in PLAN.md.

    Looks for a heading matching ``# ... Phase N: ...`` where *N* is the
    current phase number from duplo.json.  Returns text from that heading
    to the next phase heading (or end of file).  If no matching heading is
    found, returns the full content as a fallback.
    """
    phase_num, _ = get_current_phase()
    if phase_num == 0:
        return content

    lines = content.splitlines(keepends=True)
    phase_pattern = re.compile(rf"^#\s+.*(?:Phase|Stage)\s+{phase_num}\s*:", re.IGNORECASE)
    next_phase_pattern = re.compile(r"^#\s+.*(?:Phase|Stage)\s+\d+\s*:", re.IGNORECASE)

    start: int | None = None
    end: int | None = None
    for idx, line in enumerate(lines):
        if start is None:
            if phase_pattern.match(line):
                start = idx
        else:
            if next_phase_pattern.match(line):
                end = idx
                break

    if start is None:
        return content  # heading not found – fall back to full file
    return "".join(lines[start:end])


def _plan_is_complete() -> bool:
    """Return True if PLAN.md exists and all checkboxes are checked."""
    plan_path = Path("PLAN.md")
    if not plan_path.exists():
        return False
    content = plan_path.read_text(encoding="utf-8")
    section = _current_phase_content(content)
    has_tasks = False
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [!]"):
            return False
        if stripped.startswith("- [x]") or stripped.startswith("- [X]"):
            has_tasks = True
    return has_tasks


def _plan_has_unchecked_tasks() -> bool:
    """Return True if PLAN.md exists and contains at least one unchecked task."""
    plan_path = Path("PLAN.md")
    if not plan_path.exists():
        return False
    content = plan_path.read_text(encoding="utf-8")
    section = _current_phase_content(content)
    for line in section.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]") or stripped.startswith("- [!]"):
            return True
    return False


def _plan_ready(phase_label: str) -> None:
    """Print a message telling the user to run mcloop."""
    print(f"\nPlan ready for {phase_label}.")
    print("Run mcloop to start building.")


def _complete_phase(
    plan_content: str,
    app_name: str,
    phase_label: str,
) -> None:
    """Record a completed phase, capture screenshots, and advance."""
    # Scope to the current phase section so we don't re-process earlier phases.
    phase_section = _current_phase_content(plan_content)

    # Parse completed tasks and mark features before recording history.
    tasks = parse_completed_tasks(phase_section)
    if tasks:
        # Mark features from annotated tasks [feat: "..."].
        marked = mark_implemented_features(tasks, phase_label)
        if marked:
            print(f"Marked {len(marked)} annotated feature(s) as implemented.")

        # Resolve issues from annotated tasks [fix: "..."].
        resolved = resolve_completed_fixes(tasks)
        if resolved:
            print(f"Resolved {len(resolved)} annotated fix(es).")

        # Match unannotated tasks to features via Claude.
        duplo_path = Path(_DUPLO_JSON)
        try:
            data = json.loads(duplo_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        features = [_feature_from_dict(f) for f in data.get("features", [])]
        if features:
            unannotated = [t for t in tasks if not t.features and not t.fixes]
            if unannotated:
                print(f"Matching {len(unannotated)} unannotated task(s) to features \u2026")
                matched, new = match_unannotated_tasks(tasks, features, phase_label)
                if matched:
                    print(f"  Matched {len(matched)} existing feature(s):")
                    for name in matched:
                        print(f"    - {name}")
                if new:
                    print(f"  Discovered {len(new)} new feature(s):")
                    for name in new:
                        print(f"    - {name}")
                if not matched and not new:
                    print("  No feature matches found.")

    append_phase_to_history(phase_section)
    advance_phase()
    print(f"{phase_label} complete. Recorded in duplo.json.")

    # Prompt for known issues before advancing.
    issues = collect_issues()
    if issues:
        for desc in issues:
            save_issue(desc, source="user", phase=phase_label)
        print(f"Recorded {len(issues)} issue(s) in duplo.json.")
    else:
        print("No issues reported.")

    # Collect feedback for the next phase.
    try:
        feedback = collect_feedback()
    except (FileNotFoundError, ValueError):
        feedback = ""
    if feedback:
        save_feedback(feedback, after_phase=phase_label)
        print(f"Feedback recorded ({len(feedback)} chars).")

    if app_name:
        output_path = Path("screenshots") / "current" / "main.png"
        launch_cmd = "./run.sh" if Path("run.sh").exists() else None
        print(f"\nCapturing screenshots with appshot ({app_name}) \u2026")
        shot_code = capture_appshot(app_name, output_path, launch=launch_cmd)
        if shot_code == 0:
            print(f"Screenshot saved to {output_path}")
            _compare_with_references(output_path)
        elif shot_code == -1:
            print("appshot not found, skipping screenshot.")
        elif shot_code == -2:
            print("Screenshot capture timed out (skipping)")
        else:
            print(f"appshot exited with code {shot_code} (screenshot skipped)")

    notify_phase_complete(phase_label)


def _compare_with_references(current: Path) -> None:
    """Compare *current* screenshot against any reference images and print results.

    Reference lookup order (backward-compatible fallback):

    1. ``.duplo/references/*.png`` — the canonical location.  Accepted video
       frames, processed images, and moved reference files all live here.
    2. ``screenshots/*.png`` — legacy fallback for projects created before the
       ``.duplo/references/`` migration.  Ignored when (1) finds images.

    The fallback is intentional: removing it would break visual comparison for
    older projects that still store Playwright website captures in
    ``screenshots/``.  New projects never need the fallback because all
    reference material is stored in ``.duplo/references/`` during first run.
    """
    # Primary: .duplo/references/ (video frames, processed reference files).
    references: list[Path] = []
    duplo_refs = Path(".duplo") / "references"
    if duplo_refs.is_dir():
        references = sorted(duplo_refs.glob("*.png"))
    # Fallback: screenshots/ for pre-migration projects (see docstring).
    if not references:
        ref_dir = Path("screenshots")
        references = sorted(ref_dir.glob("*.png")) if ref_dir.is_dir() else []
    if not references:
        print("No reference screenshots found \u2014 skipping visual comparison.")
        return

    print(f"\nComparing screenshot against {len(references)} reference image(s) \u2026")
    result = compare_screenshots(current, references)
    verdict = "SIMILAR" if result.similar else "DIFFERENT"
    print(f"Visual comparison: {verdict}")
    print(f"  {result.summary}")
    for detail in result.details:
        print(f"  - {detail}")

    issues = generate_issue_list([result])
    if issues:
        issues_path = save_issue_list(issues)
        print(f"\nVisual issues ({len(issues)}) saved to {issues_path}")
    else:
        print("\nNo visual issues detected.")
