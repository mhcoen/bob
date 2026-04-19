"""Orchestration pipelines for ``duplo`` runs and ``duplo fix``.

This module owns the multi-step workflows that the thin CLI in
``duplo.main`` dispatches into:

* :func:`_subsequent_run` — the default run pipeline that scans for
  reference changes, scrapes declared sources, re-extracts features,
  detects gaps, completes phases, and generates the next ``PLAN.md``.
* :func:`_fix_mode` — the ``duplo fix`` / ``duplo investigate``
  subcommand that runs product-level diagnosis and appends fix tasks.

The display helpers (``_print_status``, ``_print_summary``,
``_plan_is_complete``, etc.) live in :mod:`duplo.status`.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from duplo.appshot import capture_appshot
from duplo.collector import collect_feedback, collect_issues
from duplo.comparator import compare_screenshots
from duplo.diagnostics import record_failure
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
from duplo.platforms.formatter import format_local_overrides, format_planner_system_addendum
from duplo.platforms.resolver import resolve_profiles
from duplo.platforms.scaffold import format_scaffold_notice, write_scaffold
from duplo.platforms.schema import PlatformProfile
from duplo.questioner import BuildPreferences
from duplo.roadmap import format_roadmap, generate_roadmap
from duplo.scanner import scan_files
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
    derive_app_name,
    get_current_phase,
    load_examples,
    load_product,
    mark_implemented_features,
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
    save_sources,
    store_accepted_frames,
    write_claude_md,
)
from duplo.selector import select_features, select_issues  # noqa: F401  # for test patches
from duplo.task_matcher import match_unannotated_tasks
from duplo.status import (
    UpdateSummary,
    _current_phase_content,
    _feature_from_dict,
    _plan_has_unchecked_tasks,
    _plan_is_complete,
    _print_feature_status,
    _print_status,
    _print_summary,
)

_DUPLO_JSON = ".duplo/duplo.json"

_VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi"}


def _source_url_from_spec(spec: ProductSpec | None) -> str:
    """Return the first product-reference URL from the spec, or ``""``."""
    if not spec or not spec.sources:
        return ""
    for src in spec.sources:
        if src.role == "product-reference" and not src.proposed and not src.discovered:
            return src.url
    return ""


def _prefs_from_dict(prefs_data: dict) -> BuildPreferences:
    """Build :class:`BuildPreferences` from a raw duplo.json dict."""
    return BuildPreferences(
        platform=prefs_data.get("platform", ""),
        language=prefs_data.get("language", ""),
        constraints=prefs_data.get("constraints", []),
        preferences=prefs_data.get("preferences", []),
    )


def _prefs_list_from_data(prefs_data: object) -> list[BuildPreferences]:
    """Decode ``data["preferences"]`` into a list of :class:`BuildPreferences`.

    Accepts both storage formats: a single dict (legacy, pre-multi-stack)
    is wrapped into a one-element list; a list of dicts is parsed
    element-wise.  Anything else returns an empty list.
    """
    if isinstance(prefs_data, list):
        return [_prefs_from_dict(d) for d in prefs_data if isinstance(d, dict)]
    if isinstance(prefs_data, dict):
        return [_prefs_from_dict(prefs_data)]
    return []


def _primary_prefs(prefs: list[BuildPreferences]) -> BuildPreferences:
    """Return the first entry in *prefs* or an all-defaults BuildPreferences."""
    if prefs:
        return prefs[0]
    return BuildPreferences(platform="", language="", constraints=[], preferences=[])


def _load_preferences(data: dict, spec) -> list[BuildPreferences]:
    """Load build preferences with architecture-hash invalidation.

    Returns one :class:`BuildPreferences` per target stack declared in
    ``## Architecture``.  If ``spec.architecture`` is present and its
    hash (combined with any structured platform entries) differs from
    the stored ``architecture_hash`` in *data*, re-parses preferences
    and persists the result.  Otherwise returns cached preferences.
    """
    prefs_data = data.get("preferences", [])
    cached = _prefs_list_from_data(prefs_data)

    if not spec or not spec.architecture:
        return cached

    structured = list(getattr(spec, "platform_entries", []) or [])
    current_hash = architecture_hash(spec.architecture, structured_entries=structured)
    stored_hash = data.get("architecture_hash", "")

    if current_hash == stored_hash:
        return cached

    # Hash changed - re-parse from the updated architecture prose.
    prefs = parse_build_preferences(spec.architecture, structured_entries=structured)
    save_build_preferences(prefs, current_hash)
    # Update in-memory data so later accesses in the same run see it.
    data["preferences"] = [dataclasses.asdict(p) for p in prefs]
    data["architecture_hash"] = current_hash
    for w in validate_build_preferences(prefs):
        print(f"Warning: {w}")
    return prefs


def _resolve_platform_profiles(
    prefs_list: list[BuildPreferences],
) -> list[PlatformProfile]:
    """Return the union of platform profiles matched across *prefs_list*.

    Calls :func:`resolve_profiles` for each entry and concatenates the
    results, preserving per-entry best-match order while dropping
    duplicates by ``profile.id``.  Returns an empty list when no entry
    matches any registered profile.
    """
    seen: set[str] = set()
    union: list[PlatformProfile] = []
    for prefs in prefs_list:
        for profile in resolve_profiles(prefs):
            if profile.id in seen:
                continue
            seen.add(profile.id)
            union.append(profile)
    return union


def _announce_profiles(profiles: list[PlatformProfile]) -> None:
    """Print the matched platform profiles, or note that none matched."""
    if not profiles:
        print("No platform profiles matched; planner will run without platform rules.")
        return
    names = ", ".join(p.display_name for p in profiles)
    print(f"Platform profiles: {names}")


def _read_local_md(target_dir: Path | str = ".") -> str:
    """Return the contents of ``local.md`` in *target_dir*, or ``""`` if absent.

    ``local.md`` is a user-owned, gitignored file holding project-specific
    overrides that flow into both the planner prompt and CLAUDE.md.
    """
    path = Path(target_dir) / "local.md"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


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
class ScrapeResult:
    """Accumulated results from scraping all declared sources."""

    combined_text: str = ""
    all_code_examples: list = dataclasses.field(default_factory=list)
    all_page_records: list = dataclasses.field(default_factory=list)
    all_raw_pages: dict = dataclasses.field(default_factory=dict)
    product_ref_raw_pages: dict = dataclasses.field(default_factory=dict)
    merged_doc_structures: DocStructures = dataclasses.field(default_factory=DocStructures)
    discovered_urls: list = dataclasses.field(default_factory=list)
    source_records: list = dataclasses.field(default_factory=list)


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

        # Per-source persistence metadata.
        content_hash = hashlib.sha256(scraped_text.encode("utf-8")).hexdigest()
        result.source_records.append(
            {
                "url": source.url,
                "last_scraped": datetime.now(timezone.utc).isoformat(),
                "content_hash": content_hash,
                "scrape_depth_used": source.scrape,
            }
        )

    return result


def _persist_scrape_result(result: ScrapeResult) -> None:
    """Save accumulated scrape artifacts to .duplo/.

    Persists code examples, page records, raw page HTML, doc
    structures, and per-source scraping metadata from a
    :class:`ScrapeResult`.  Appends discovered cross-origin URLs
    to SPEC.md with ``discovered: true``.
    """
    if result.source_records:
        save_sources(result.source_records)
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
    PATHS TO ALL EMBEDDED MEDIA - both files newly downloaded during
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
        # Split on blank lines - each paragraph is one bug.
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
        # Investigator returned no diagnoses - fall back with error context.
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


def _readable_text_refs(
    paths: list[Path],
    spec: ProductSpec | None,
) -> list[Path]:
    """Filter PDFs/text files against SPEC.md reference roles.

    When *spec* is None, all *paths* are returned unchanged. When
    provided, paths listed in ``## References`` are dropped if they are
    ``proposed: true`` or their only roles are ``counter-example`` and/or
    ``ignore``. Paths not listed in ``## References`` pass through
    unchanged (backward compat).
    """
    if spec is None:
        return list(paths)
    index: dict[str, tuple[list[str], bool]] = {}
    for entry in spec.references:
        index[str(entry.path)] = (list(entry.roles), entry.proposed)
    kept: list[Path] = []
    for p in paths:
        key = str(p)
        match = index.get(key)
        if match is None:
            for ref_path, value in index.items():
                parts = ref_path.split("/")
                if len(parts) >= 2 and parts[0] == "ref" and parts[-1] == p.name:
                    match = value
                    break
        if match is None:
            kept.append(p)
            continue
        roles, proposed = match
        if proposed:
            continue
        if any(r not in ("counter-example", "ignore") for r in roles):
            kept.append(p)
    return kept


def _analyze_new_files(
    file_names: list[str],
    spec: ProductSpec | None = None,
) -> UpdateSummary:
    """Analyze new or changed files in ref/.

    Images are sent to Vision for design extraction, PDFs and text
    files are read for prompt context. URLs are sourced exclusively
    from SPEC.md's ``## Sources`` section, not from file contents.

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
            existing = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
            body = format_design_block(design)
            if body:
                modified = update_design_autogen(existing, body)
                if modified != existing:
                    spec_path.write_text(modified, encoding="utf-8")
                else:
                    record_failure(
                        "orchestrator:design_autogen",
                        "io",
                        "update_design_autogen returned unchanged text; SPEC.md was not modified.",
                    )
            else:
                record_failure(
                    "orchestrator:design_format",
                    "io",
                    "format_design_block returned empty despite non-empty colors/fonts/layout.",
                )
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

    # Extract text from new PDFs (skipping counter-example/ignore/proposed refs).
    readable_pdfs = _readable_text_refs(scan.pdfs, spec)
    if readable_pdfs:
        print(f"\nExtracting text from {len(readable_pdfs)} new PDF(s) \u2026")
        pdf_text = extract_pdf_text(readable_pdfs)
        if pdf_text:
            summary.collected_text += pdf_text + "\n"
            print(f"  Extracted text from {len(readable_pdfs)} PDF(s).")
            summary.pdfs_extracted = len(readable_pdfs)
            analyzed_anything = True

    # Collect text from new text files (same SPEC-role gating).
    readable_text_files = _readable_text_refs(scan.text_files, spec)
    if readable_text_files:
        text_content = ""
        for tf in readable_text_files:
            try:
                text_content += tf.read_text(encoding="utf-8", errors="ignore") + "\n"
            except OSError:
                pass
        if text_content.strip():
            summary.collected_text += text_content
            print(f"\nRead {len(readable_text_files)} new text file(s).")
            summary.text_files_read = len(readable_text_files)
            analyzed_anything = True

    if not analyzed_anything:
        print("No analyzable reference materials in new files.")

    return summary


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
                existing = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
                body = format_design_block(design)
                if body:
                    modified = update_design_autogen(existing, body)
                    if modified != existing:
                        spec_path.write_text(modified, encoding="utf-8")
                    else:
                        record_failure(
                            "orchestrator:design_autogen",
                            "io",
                            "update_design_autogen returned unchanged text;"
                            " SPEC.md was not modified.",
                        )
                else:
                    record_failure(
                        "orchestrator:design_format",
                        "io",
                        "format_design_block returned empty despite"
                        " non-empty colors/fonts/layout.",
                    )
                save_design_requirements(dataclasses.asdict(design))
                print(f"  Updated design from {len(design.source_images)} image(s).")
            else:
                print("  Could not extract design details from images.")
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

    primary = _primary_prefs(_prefs_list_from_data(data.get("preferences", [])))
    platform = primary.platform
    language = primary.language

    print("\nComparing features and examples against PLAN.md \u2026")
    result = detect_gaps(
        plan_content, features, examples or None, platform=platform, language=language
    )

    # Check for design refinements not yet in the plan.
    # Design data comes from SPEC.md AUTO-GENERATED block only.
    design_data: dict = {}
    if spec and spec.design.auto_generated:
        design_data = _parse_design_markdown(spec.design.auto_generated)
    if design_data:
        design_gaps = detect_design_gaps(plan_content, design_data)
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


def _insert_design_after_heading(content: str, design_section: str) -> str:
    """Return *content* with *design_section* placed after the first H1.

    The design section must live INSIDE the Phase 0 plan body so that
    mcloop's phase parser sees a phase heading as the first line of
    PLAN.md.  A preamble above the heading would be treated as outside
    any phase and break task dispatch.

    If *content* has no H1 heading or *design_section* is empty, the
    content is returned unchanged.
    """
    if not design_section.strip():
        return content
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.lstrip().startswith("# "):
            before = lines[: i + 1]
            after = lines[i + 1 :]
            block = design_section.rstrip()
            new_lines = before + ["", block]
            if after:
                new_lines.append("")
                new_lines.extend(after)
            return "\n".join(new_lines)
    return content


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
            summary.video_frames_extracted = analysis.video_frames_extracted
            summary.collected_text = analysis.collected_text

    # Scrape declared sources (when SPEC.md has scrapeable entries) or
    # fall back to single-URL re-scrape from duplo.json.
    scraped_text = ""
    site_images: list[Path] = []
    site_videos: list[Path] = []
    product_ref_raw_pages: dict[str, str] = {}
    spec_sources = scrapeable_sources(spec) if spec else []
    if spec_sources:
        scrape_result = _scrape_declared_sources(spec)
        scraped_text = scrape_result.combined_text
        _persist_scrape_result(scrape_result)
        summary.pages_rescraped = len(scrape_result.all_page_records)
        summary.examples_rescraped = len(scrape_result.all_code_examples)
        product_ref_raw_pages = scrape_result.product_ref_raw_pages
        # Keep product.json source_url in sync with spec (backward compat).
        spec_url = _source_url_from_spec(spec)
        if spec_url:
            saved = load_product()
            if not saved or saved[1] != spec_url:
                pname = saved[0] if saved else derive_app_name(spec)
                save_product(pname, spec_url)
    else:
        legacy_source_url = (
            status_data.get("source_url", "") if isinstance(status_data, dict) else ""
        )
        if legacy_source_url:
            record_failure(
                "orchestrator:_subsequent_run",
                "io",
                "SPEC.md authority boundary bypassed: spec_sources is empty,"
                " falling back to duplo.json source_url for re-scrape.",
                context={"source_url": legacy_source_url},
            )
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

    # Download embedded media from product-reference pages (step 9).
    # Returns all media (cached + new).
    if product_ref_raw_pages:
        site_images, site_videos = _download_site_media(product_ref_raw_pages)
        if site_images:
            print(f"  {len(site_images)} image(s) from product site.")
        if site_videos:
            print(f"  {len(site_videos)} video(s) from product site.")

    # Behavioral references -> frame extraction -> design input.
    # When spec declares scrapeable sources, process behavioral
    # videos (ref/ + site_videos) and compose design input from
    # four sources.  The non-spec path handles this inside
    # _rescrape_product_url.
    if spec and spec_sources:
        behavioral_entries = [
            e for e in format_behavioral_references(spec) if e.path.suffix.lower() in _VIDEO_EXTS
        ]
        behavioral_paths = [e.path for e in behavioral_entries] + site_videos
        assert len(behavioral_paths) == len(set(behavioral_paths)), (
            "Duplicate source path across ref-declared and scraped videos"
        )
        accepted_by_path: dict[Path, list[Path]] = {}
        if behavioral_paths:
            print(f"\nProcessing {len(behavioral_paths)} behavioral video(s) \u2026")
            _, accepted_by_path = _run_video_frame_pipeline(
                behavioral_paths,
            )

        # Compose design input from four sources with
        # content-hash dedup.
        vt_frames_raw = [
            frame
            for entry in behavioral_entries
            if "visual-target" in entry.roles
            for frame in accepted_by_path.get(entry.path, [])
        ]
        scraped_frames_raw = [
            frame for vp in site_videos for frame in accepted_by_path.get(vp, [])
        ]
        seen_fh: set[str] = set()
        vt_frames: list[Path] = []
        for frame in vt_frames_raw:
            h = hashlib.sha256(frame.read_bytes()).hexdigest()
            if h not in seen_fh:
                vt_frames.append(frame)
                seen_fh.add(h)
        site_vf: list[Path] = []
        for frame in scraped_frames_raw:
            h = hashlib.sha256(frame.read_bytes()).hexdigest()
            if h not in seen_fh:
                site_vf.append(frame)
                seen_fh.add(h)
        design_input = collect_design_input(
            spec,
            vt_frames,
            site_images,
            site_vf,
        )

        autogen_present = bool(spec.design.auto_generated.strip())
        if design_input and not autogen_present:
            print("\nExtracting visual design from images \u2026")
            design = extract_design(design_input)
            if design.colors or design.fonts or design.layout:
                spec_path = Path.cwd() / "SPEC.md"
                existing = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
                body = format_design_block(design)
                if body:
                    modified = update_design_autogen(existing, body)
                    if modified != existing:
                        spec_path.write_text(modified, encoding="utf-8")
                    else:
                        record_failure(
                            "orchestrator:design_autogen",
                            "io",
                            "update_design_autogen returned unchanged text;"
                            " SPEC.md was not modified.",
                        )
                else:
                    record_failure(
                        "orchestrator:design_format",
                        "io",
                        "format_design_block returned empty despite"
                        " non-empty colors/fonts/layout.",
                    )
                save_design_requirements(dataclasses.asdict(design))
                print(f"  Updated design from {len(design.source_images)} image(s).")
            else:
                print("  Could not extract design details from images.")
        elif design_input:
            record_failure(
                "orchestrator:design_extraction",
                "io",
                "Autogen design block exists in SPEC.md;"
                " skipped Vision extraction. Delete the"
                " BEGIN/END AUTO-GENERATED block to"
                f" regenerate from {len(design_input)}"
                " input image(s).",
            )
            print("\nDesign autogen block already exists in SPEC.md; skipping Vision.")

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
    app_name = derive_app_name(spec)

    plan_path = Path("PLAN.md")

    # State 1: PLAN.md complete -> record phase completion, then fall through.
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

    # State 2: PLAN.md incomplete -> tell user to continue.
    elif plan_path.exists():
        phase_num, phase_info = get_current_phase()
        phase_label = (
            f"Phase {phase_num}: {phase_info['title']}" if phase_info else f"Phase {phase_num}"
        )
        print(f"{phase_label} has uncompleted tasks in PLAN.md.")
        print("Run mcloop to continue building.")
        return

    # State 3: No PLAN.md -> generate plan for current phase.
    _, phase_info = get_current_phase()

    # If no roadmap exists or the existing one is fully consumed,
    # regenerate from remaining unimplemented features.
    roadmap = data.get("roadmap", [])
    if not roadmap or phase_info is None:
        _print_feature_status(data)
        remaining = _unimplemented_features(data)
        if not remaining:
            print("All features implemented. Nothing to do.")
            return
        source_url = _source_url_from_spec(spec) or data.get("source_url", "")
        preferences = _load_preferences(data, spec)
        profiles = _resolve_platform_profiles(preferences)
        _announce_profiles(profiles)
        history = _build_completion_history(data)
        print(f"\nGenerating new roadmap for {len(remaining)} remaining feature(s) \u2026")
        new_roadmap = generate_roadmap(
            source_url,
            remaining,
            _primary_prefs(preferences),
            completion_history=history,
            spec_text=spec_prompt,
        )
        if not new_roadmap:
            print("Error: failed to generate roadmap.")
            return
        save_roadmap(new_roadmap)
        print(format_roadmap(new_roadmap))
        # Use the freshly generated roadmap directly so the first-run
        # path loops over every phase (starting at Phase 0) even if the
        # save/reload round-trip drops entries.  Reload `data` so later
        # steps see the persisted current_phase and any other state.
        roadmap = new_roadmap
        data = json.loads(Path(_DUPLO_JSON).read_text(encoding="utf-8"))

    # Bail out only if the regen block failed to leave us with a
    # usable roadmap. We intentionally key off ``roadmap`` rather than
    # ``phase_info`` so the first-run path always proceeds into the
    # loop below (which iterates over every roadmap entry starting at
    # Phase 0) instead of short-circuiting on a missing ``current_phase``
    # pointer.
    if not roadmap:
        print("All features implemented. Nothing to do.")
        return

    # On first runs (no completed phases) the roadmap's own phase keys
    # (starting at 0 for the scaffold) are the authoritative phase
    # numbers.  On subsequent runs we continue numbering from the
    # completion history so labels don't collide with phases already
    # recorded in ``phases``.
    phases_completed = len(data.get("phases", []))

    # Shared inputs used for every phase's plan generation.
    source_url = _source_url_from_spec(spec) or data.get("source_url", "")
    features = [_feature_from_dict(f) for f in data.get("features", [])]
    preferences = _load_preferences(data, spec)
    profiles = _resolve_platform_profiles(preferences)
    _announce_profiles(profiles)

    design_data = data.get("design_requirements", {})
    design_section = ""
    if design_data:
        _dfields = {f.name for f in dataclasses.fields(DesignRequirements)}
        loaded_design = DesignRequirements(
            **{k: v for k, v in design_data.items() if k in _dfields}
        )
        design_section = format_design_section(loaded_design)

    # On the first phase of a new project, lay down platform scaffold
    # artifacts (run.sh, .gitignore entries, etc.) before the planner
    # runs, so tasks can reference them instead of recreating them.
    scaffold_notice = ""
    if phases_completed == 0 and profiles:
        written = write_scaffold(profiles, app_name, target_dir=Path.cwd())
        if written:
            for p in written:
                try:
                    print(f"  Scaffold: {p.relative_to(Path.cwd())}")
                except ValueError:
                    print(f"  Scaffold: {p}")
        scaffold_notice = format_scaffold_notice(written, target_dir=Path.cwd())

    local_md_content = _read_local_md(Path.cwd())
    local_overrides = format_local_overrides(local_md_content)

    # Refresh CLAUDE.md whenever platform profiles are present so it
    # stays in sync with the resolved stack. On first-phase runs this
    # creates the file; on later runs it overwrites with current rules.
    if profiles:
        write_claude_md(
            profiles,
            preferences,
            app_name,
            local_md_content=local_md_content,
            target_dir=Path.cwd(),
        )

    platform_addendum = (
        format_planner_system_addendum(profiles) + scaffold_notice + local_overrides
    )

    # Generate a plan for every phase in the roadmap and append each
    # result to PLAN.md. mcloop will consume the phases in order.
    total_phases = len(roadmap)
    for idx, phase_dict in enumerate(roadmap):
        if phases_completed == 0:
            # First run: honour the roadmap's own phase numbers so the
            # scaffold plan is labelled "Phase 0".
            phase_number_i = phase_dict.get("phase", idx)
        else:
            # Subsequent run: continue numbering from the history so
            # new plans never collide with already-completed phases.
            phase_number_i = phases_completed + idx + 1
        phase_title = phase_dict.get("title", "")
        phase_label_i = (
            f"Phase {phase_number_i}: {phase_title}" if phase_title else f"Phase {phase_number_i}"
        )
        print(f"Generating {phase_label_i} PLAN.md \u2026")
        content = generate_phase_plan(
            source_url,
            features,
            _primary_prefs(preferences),
            phase=phase_dict,
            project_name=app_name,
            phase_number=phase_number_i,
            spec_text=spec_prompt,
            platform_addendum=platform_addendum,
        )
        # Inject visual-design requirements AFTER the Phase 0 heading so
        # the section lives inside the phase rather than as a preamble
        # (which would confuse mcloop's phase parser).
        if phase_number_i == 0 and design_section:
            content = _insert_design_after_heading(content, design_section)
        # Verification tasks are authored once against the first phase;
        # they describe product-level behavior, not per-phase scope.
        if idx == 0:
            frame_descs = load_frame_descriptions()
            if frame_descs:
                print("Extracting verification cases from demo video \u2026")
                vcases = extract_verification_cases(frame_descs)
                if vcases:
                    vtasks = format_verification_tasks(vcases)
                    content = content.rstrip() + "\n" + vtasks
                    print(f"  {len(vcases)} verification case(s) added.")
            spec_vtasks = ""
            if spec:
                spec_vtasks = format_contracts_as_verification(spec)
            if spec_vtasks:
                content = content.rstrip() + "\n" + spec_vtasks
                print(f"  {len(spec.behavior_contracts)} spec verification case(s) added.")
        saved = save_plan(content)
        print(f"{phase_label_i} plan saved to {saved}")

    print(f"\nPlan ready for all {total_phases} phases.")
    print("Run mcloop to start building.")


def _partition_features(
    data: dict,
) -> tuple[list[Feature], list[Feature]]:
    """Split features into implemented and remaining lists.

    Thin wrapper around :func:`duplo.status._partition_features` retained
    so callers that import from :mod:`duplo.pipeline` keep working.
    """
    from duplo.status import _partition_features as _impl

    return _impl(data)


def _unimplemented_features(data: dict) -> list[Feature]:
    """Return features from *data* whose status is not ``"implemented"``."""
    _, remaining = _partition_features(data)
    return remaining


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

    1. ``.duplo/references/*.png`` - the canonical location.  Accepted video
       frames, processed images, and moved reference files all live here.
    2. ``screenshots/*.png`` - legacy fallback for projects created before the
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
