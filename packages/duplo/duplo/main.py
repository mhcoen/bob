"""Duplo CLI entry point."""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path

from duplo.appshot import capture_appshot
from duplo.collector import collect_feedback
from duplo.comparator import compare_screenshots
from duplo.design_extractor import (
    DesignRequirements,
    extract_design,
    format_design_section,
)
from duplo.issuer import generate_issue_list, save_issue_list
from duplo.extractor import Feature, extract_features
from duplo.gap_detector import detect_design_gaps, detect_gaps, format_gap_tasks
from duplo.notifier import notify_phase_complete
from duplo.fetcher import fetch_site
from duplo.pdf_extractor import extract_pdf_text
from duplo.planner import (
    append_test_tasks,
    generate_next_phase_plan,
    generate_phase_plan,
    save_plan,
)
from duplo.questioner import BuildPreferences, ask_preferences
from duplo.roadmap import format_roadmap, generate_roadmap
from duplo.runner import run_mcloop
from duplo.scanner import scan_directory, scan_files
from duplo.test_generator import (
    generate_plan_test_tasks,
    generate_test_source,
    load_code_examples,
    save_test_file,
)
from duplo.validator import validate_product_url
from duplo.frame_describer import describe_frames
from duplo.frame_filter import apply_filter, filter_frames
from duplo.video_extractor import extract_all_videos
from duplo.hasher import compute_hashes, diff_hashes, load_hashes, save_hashes
from duplo.saver import (
    advance_phase,
    append_phase_to_history,
    clear_in_progress,
    get_current_phase,
    move_references,
    save_design_requirements,
    save_examples,
    save_feedback,
    save_raw_content,
    save_reference_urls,
    save_roadmap,
    save_screenshot_feature_map,
    save_selections,
    set_in_progress,
    store_accepted_frames,
    write_claude_md,
)
from duplo.screenshotter import map_screenshots_to_features, save_reference_screenshots
from duplo.selector import select_features

_SECTION_URL_RE = re.compile(r"^=== (.+?) ===$", re.MULTILINE)
_DUPLO_JSON = ".duplo/duplo.json"
# Files that are project artifacts, not user-provided reference materials.
_PROJECT_FILES = {"PLAN.md", "CLAUDE.md", "README.md", "ISSUES.md", "NOTES.md"}


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
    missing_features: int = 0
    missing_examples: int = 0
    design_refinements: int = 0
    tasks_appended: int = 0


def main() -> None:
    """Run duplo from the current directory.

    First run (no ``.duplo/duplo.json``): scan for reference materials,
    fetch URLs, extract features, generate roadmap and plan, build.

    Subsequent runs: resume interrupted phases or advance to the next one.
    """
    duplo_path = Path(_DUPLO_JSON)
    if not duplo_path.exists():
        _first_run()
    else:
        _subsequent_run()


def _first_run() -> None:
    """Scan reference materials in the current directory and bootstrap the project."""
    scan = scan_directory(".")
    if (
        not scan.images
        and not scan.videos
        and not scan.pdfs
        and not scan.text_files
        and not scan.urls
    ):
        print(
            "No reference materials found.\n"
            "Drop images, PDFs, text files, or a file containing URLs\n"
            "into this directory and run duplo again."
        )
        sys.exit(1)

    print("Scanning reference materials …")
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
    relevant_pdfs = [r.path for r in scan.relevance if r.category == "pdf" and r.relevant]
    if relevant_pdfs:
        print("Extracting text from PDFs …")
        pdf_text = extract_pdf_text(relevant_pdfs)
        if pdf_text:
            text_content = text_content + pdf_text + "\n"
            print(f"  Extracted text from {len(relevant_pdfs)} PDF(s).")

    # Fetch the first URL found (primary product URL).
    source_url = ""
    scraped_text = ""
    code_examples: list = []
    doc_structures = None
    page_records: list = []
    raw_pages: dict[str, str] = {}

    product_name = ""

    if scan.urls:
        source_url = scan.urls[0]
        source_url, product_name = _validate_url(source_url)
        if not source_url:
            return
        print(f"\nFetching {source_url} …")
        scraped_text, code_examples, doc_structures, page_records, raw_pages = fetch_site(
            source_url
        )
        if code_examples:
            print(f"Extracted {len(code_examples)} code example(s) from docs.")

    product_name = _confirm_product(product_name, source_url)
    if not product_name:
        return

    # Extract frames from video files at scene change points.
    video_frames: list[Path] = []
    relevant_videos = [r.path for r in scan.relevance if r.category == "video" and r.relevant]
    if relevant_videos:
        print(f"\nExtracting frames from {len(relevant_videos)} video(s) …")
        frames_dir = Path(".duplo") / "video_frames"
        results = extract_all_videos(relevant_videos, frames_dir)
        for vr in results:
            if vr.error:
                print(f"  {vr.source.name}: {vr.error}")
            elif vr.frames:
                print(f"  {vr.source.name}: {len(vr.frames)} frame(s) extracted")
                video_frames.extend(vr.frames)
        if video_frames:
            print(f"  Total: {len(video_frames)} frame(s) from video(s)")
            print("Filtering frames with Vision …")
            decisions = filter_frames(video_frames)
            video_frames = apply_filter(decisions)
            kept = sum(1 for d in decisions if d.keep)
            rejected = len(decisions) - kept
            if rejected:
                print(f"  Kept {kept}, rejected {rejected} frame(s)")
            if video_frames:
                print("Describing UI states …")
                frame_descs = describe_frames(video_frames)
                for fd in frame_descs:
                    print(f"  {fd.path.name}: {fd.state} — {fd.detail}")
                # Store accepted frames in .duplo/references/.
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
                    print(f"  Stored {len(stored)} frame(s) in .duplo/references/")

    # Extract visual design from reference images (including video frames).
    design = DesignRequirements()
    relevant_images = [r.path for r in scan.relevance if r.category == "image" and r.relevant]
    relevant_images.extend(video_frames)
    if relevant_images:
        print("\nExtracting visual design from images …")
        design = extract_design(relevant_images)
        if design.colors or design.fonts or design.layout:
            print(f"Extracted design details from {len(design.source_images)} image(s).")
        else:
            print("Could not extract design details from images.")

    combined_text = scraped_text
    if text_content:
        combined_text = text_content + "\n" + combined_text

    print("\nExtracting features …")
    features = extract_features(combined_text)
    if features:
        print(f"Found {len(features)} feature(s).")
        features = select_features(features)
    else:
        print("No features extracted.")

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
        raw_pages=raw_pages,
        design=design,
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
        print(f"\nGenerating {phase_label} PLAN.md …")
        design_section = format_design_section(design) if design else ""
        content = generate_phase_plan(
            source_url,
            features,
            prefs,
            phase=phase_info,
            project_name=app_name,
            design_section=design_section,
        )
        doc_examples = load_code_examples()
        test_tasks = generate_plan_test_tasks(doc_examples)
        if test_tasks:
            content = append_test_tasks(content, test_tasks)
        saved = save_plan(content)
        print(f"Plan saved to {saved}")
        _execute_phase(content, app_name, phase_label)
    else:
        print("Failed to generate roadmap.")


def _analyze_new_files(file_names: list[str]) -> UpdateSummary:
    """Analyze new or changed files the same way as first run.

    Images are sent to Vision for design extraction, PDFs are
    converted to text, and URLs found in text files are scraped.
    Results are saved to duplo.json.

    Returns an :class:`UpdateSummary` with counts of what was analyzed.
    """
    summary = UpdateSummary()
    paths = [Path(name) for name in file_names]
    paths = [p for p in paths if p.exists()]
    if not paths:
        return summary

    scan = scan_files(paths)
    analyzed_anything = False

    # Collect user-provided images.
    relevant_images = [r.path for r in scan.relevance if r.category == "image" and r.relevant]

    # Extract frames from new video files at scene change points.
    video_frames: list[Path] = []
    if scan.videos:
        relevant_vids = [r.path for r in scan.relevance if r.category == "video" and r.relevant]
        if relevant_vids:
            print(f"\nExtracting frames from {len(relevant_vids)} new video(s) …")
            frames_dir = Path(".duplo") / "video_frames"
            vid_results = extract_all_videos(relevant_vids, frames_dir)
            for vr in vid_results:
                if vr.error:
                    print(f"  {vr.source.name}: {vr.error}")
                elif vr.frames:
                    print(f"  {vr.source.name}: {len(vr.frames)} frame(s)")
                    video_frames.extend(vr.frames)
            summary.videos_found = len(relevant_vids)
            analyzed_anything = True

            # Filter frames with Vision before design extraction.
            if video_frames:
                print("Filtering frames with Vision …")
                decisions = filter_frames(video_frames)
                video_frames = apply_filter(decisions)
                kept = sum(1 for d in decisions if d.keep)
                rejected = len(decisions) - kept
                if rejected:
                    print(f"  Kept {kept}, rejected {rejected} frame(s)")
                if video_frames:
                    print("Describing UI states …")
                    frame_descs = describe_frames(video_frames)
                    for fd in frame_descs:
                        print(f"  {fd.path.name}: {fd.state} — {fd.detail}")
                    # Store accepted frames in .duplo/references/.
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
                        print(f"  Stored {len(stored)} frame(s) in .duplo/references/")

            summary.video_frames_extracted = len(video_frames)
        else:
            summary.videos_found = len(scan.videos)

    # Combine user images and accepted video frames for design extraction.
    all_images = relevant_images + video_frames
    if all_images:
        print(f"\nAnalyzing {len(all_images)} image(s) with Vision …")
        design = extract_design(all_images)
        if design.colors or design.fonts or design.layout:
            save_design_requirements(dataclasses.asdict(design))
            print(f"  Updated design requirements from {len(design.source_images)} image(s).")
            summary.images_analyzed = len(design.source_images)
            analyzed_anything = True
        else:
            print("  Could not extract design details from images.")

    # Extract text from new PDFs.
    relevant_pdfs = [r.path for r in scan.relevance if r.category == "pdf" and r.relevant]
    if relevant_pdfs:
        print(f"\nExtracting text from {len(relevant_pdfs)} new PDF(s) …")
        pdf_text = extract_pdf_text(relevant_pdfs)
        if pdf_text:
            print(f"  Extracted text from {len(relevant_pdfs)} PDF(s).")
            summary.pdfs_extracted = len(relevant_pdfs)
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
            print(f"\nRead {len(scan.text_files)} new text file(s).")
            summary.text_files_read = len(scan.text_files)
            analyzed_anything = True

    # Fetch new URLs.
    if scan.urls:
        existing_urls = _load_existing_urls()
        new_urls = [u for u in scan.urls if u not in existing_urls]
        if new_urls:
            print(f"\nFetching {len(new_urls)} new URL(s) …")
            fetched = 0
            for url in new_urls:
                print(f"  Fetching {url} …")
                try:
                    _, code_examples, doc_structures, page_records, raw_pages = fetch_site(url)
                    if page_records:
                        save_reference_urls(page_records)
                        if raw_pages:
                            save_raw_content(raw_pages, page_records)
                    fetched += 1
                    analyzed_anything = True
                except Exception as exc:
                    print(f"  Failed to fetch {url}: {exc}")
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
    data = json.loads(duplo_path.read_text(encoding="utf-8"))
    records = data.get("reference_urls", [])
    return {r["url"] for r in records if "url" in r}


def _rescrape_product_url() -> tuple[int, int]:
    """Re-scrape the product URL stored in duplo.json with the deep extractor.

    If ``source_url`` is set, fetches it again via :func:`fetch_site` and
    updates the reference URLs and raw page content in duplo.json.  This
    picks up any changes on the product site since the last run.

    Returns ``(pages_updated, examples_updated)`` counts.
    """
    duplo_path = Path(_DUPLO_JSON)
    if not duplo_path.exists():
        return 0, 0
    data = json.loads(duplo_path.read_text(encoding="utf-8"))
    source_url = data.get("source_url", "")
    if not source_url:
        return 0, 0

    print(f"\nRe-scraping {source_url} …")
    try:
        scraped_text, code_examples, doc_structures, page_records, raw_pages = fetch_site(
            source_url
        )
    except Exception as exc:
        print(f"  Failed to re-scrape {source_url}: {exc}")
        return 0, 0

    pages_updated = 0
    examples_updated = 0

    if page_records:
        save_reference_urls(page_records)
        if raw_pages:
            save_raw_content(raw_pages, page_records)
        pages_updated = len(page_records)
        print(f"  Updated {pages_updated} page record(s).")
    if code_examples:
        save_examples(code_examples)
        examples_updated = len(code_examples)
        print(f"  Updated {examples_updated} code example(s).")

    return pages_updated, examples_updated


def _detect_and_append_gaps() -> tuple[int, int, int, int]:
    """Compare features and examples from duplo.json against PLAN.md.

    If gaps are found, appends new checklist tasks to PLAN.md for
    features or examples not yet covered by the current plan.

    Returns ``(missing_features, missing_examples, design_refinements,
    tasks_appended)`` counts.
    """
    plan_path = Path("PLAN.md")
    duplo_path = Path(_DUPLO_JSON)
    if not plan_path.exists() or not duplo_path.exists():
        return 0, 0, 0, 0

    plan_content = plan_path.read_text(encoding="utf-8")
    data = json.loads(duplo_path.read_text(encoding="utf-8"))

    features = [Feature(**f) for f in data.get("features", [])]
    if not features:
        return 0, 0, 0, 0

    from duplo.saver import load_examples

    examples = load_examples()

    print("\nComparing features and examples against PLAN.md …")
    result = detect_gaps(plan_content, features, examples or None)

    # Check for design refinements not yet in the plan.
    design_data = data.get("design_requirements", {})
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
        tasks_appended = (
            len(result.missing_features)
            + len(result.missing_examples)
            + len(result.design_refinements)
        )
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
    """Resume an interrupted phase or advance to the next one."""
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

        # Analyze new/changed top-level files like first run.
        # Only top-level files are reference materials (matching scan_directory).
        # Exclude known project artifacts.
        changed_files = [
            f for f in diff.added + diff.changed if "/" not in f and f not in _PROJECT_FILES
        ]
        if changed_files:
            analysis = _analyze_new_files(changed_files)
            summary.images_analyzed = analysis.images_analyzed
            summary.videos_found = analysis.videos_found
            summary.pdfs_extracted = analysis.pdfs_extracted
            summary.text_files_read = analysis.text_files_read
            summary.urls_fetched = analysis.urls_fetched

    # Re-scrape the product URL to pick up site changes.
    pages, examples = _rescrape_product_url()
    summary.pages_rescraped = pages
    summary.examples_rescraped = examples

    save_hashes(new_hashes)

    # Compare features/examples against current plan and append gap tasks.
    mf, me, dr, ta = _detect_and_append_gaps()
    summary.missing_features = mf
    summary.missing_examples = me
    summary.design_refinements = dr
    summary.tasks_appended = ta

    _print_summary(summary)

    duplo_path = Path(_DUPLO_JSON)
    data = json.loads(duplo_path.read_text(encoding="utf-8"))
    app_name = data.get("app_name", "")
    in_progress = data.get("in_progress")

    # Resume interrupted phase.
    if in_progress:
        phase_label = in_progress["label"]
        plan_path = Path("PLAN.md")
        content = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
        if in_progress.get("mcloop_done"):
            print(f"Resuming {phase_label}: McLoop already done, completing phase …")
            _execute_phase(content, app_name, phase_label, skip_mcloop=True)
        else:
            print(f"Resuming {phase_label}: re-running McLoop …")
            _execute_phase(content, app_name, phase_label)
        return

    phase_num, phase_info = get_current_phase()
    phase_label = (
        f"Phase {phase_num}: {phase_info['title']}" if phase_info else f"Phase {phase_num}"
    )

    # Check if current phase is already in history.
    history = data.get("phases", [])
    if any(h.get("phase", "").startswith(phase_label) for h in history):
        # Current phase is done — advance to next.
        _advance_to_next(data, app_name)
        return

    plan_path = Path("PLAN.md")

    # PLAN.md exists from a prior interrupted run.
    if plan_path.exists():
        print(f"Resuming {phase_label}: PLAN.md found, re-running McLoop …")
        content = plan_path.read_text(encoding="utf-8")
        _execute_phase(content, app_name, phase_label)
        return

    # Generate plan for current phase and execute.
    source_url = data.get("source_url", "")
    features = [Feature(**f) for f in data.get("features", [])]
    prefs_data = data.get("preferences", {})
    preferences = BuildPreferences(
        platform=prefs_data.get("platform", ""),
        language=prefs_data.get("language", ""),
        constraints=prefs_data.get("constraints", []),
        preferences=prefs_data.get("preferences", []),
    )

    print(f"Generating {phase_label} PLAN.md …")
    design_data = data.get("design_requirements", {})
    design_section = ""
    if design_data:
        loaded_design = DesignRequirements(**design_data)
        design_section = format_design_section(loaded_design)
    content = generate_phase_plan(
        source_url,
        features,
        preferences,
        phase=phase_info,
        project_name=data.get("app_name", ""),
        design_section=design_section,
    )
    doc_examples = load_code_examples()
    test_tasks = generate_plan_test_tasks(doc_examples)
    if test_tasks:
        content = append_test_tasks(content, test_tasks)
    saved = save_plan(content)
    print(f"{phase_label} plan saved to {saved}")
    _execute_phase(content, app_name, phase_label)


def _advance_to_next(data: dict, app_name: str) -> None:
    """Collect feedback and generate the next phase plan."""
    plan_path = Path("PLAN.md")
    if not plan_path.exists():
        print("All phases complete. Nothing to do.")
        return

    current_plan = plan_path.read_text(encoding="utf-8")

    issues_text = ""
    issues_path = Path("ISSUES.md")
    if issues_path.exists():
        issues_text = issues_path.read_text(encoding="utf-8")

    try:
        feedback = collect_feedback()
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    print(f"\nFeedback recorded ({len(feedback)} chars).")

    current_phase_match = re.search(
        r"#\s*(Phase\s+\d+[^\n]*)", current_plan, re.IGNORECASE | re.MULTILINE
    )
    current_phase_label = current_phase_match.group(1).strip() if current_phase_match else ""
    save_feedback(feedback, after_phase=current_phase_label)

    print("Generating next phase PLAN.md …")
    content = generate_next_phase_plan(current_plan, feedback, issues_text)
    doc_examples = load_code_examples()
    test_tasks = generate_plan_test_tasks(doc_examples)
    if test_tasks:
        content = append_test_tasks(content, test_tasks)
    saved = save_plan(content)
    print(f"Next phase plan saved to {saved}")

    match = re.search(r"#\s*(Phase\s+\d+[^\n]*)", content, re.IGNORECASE | re.MULTILINE)
    phase_label = match.group(1).strip() if match else "Next Phase"
    _execute_phase(content, app_name, phase_label)


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

    # No product name identified — ask the user.
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
    print(f"\nValidating {url} …")
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
        # Treat as a product description — use it as the product name.
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
    # No product list — ask for a URL.
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
) -> list | None:
    """Core init logic: save selections, generate tests, write CLAUDE.md, build roadmap.

    Returns the generated roadmap (list of phase dicts) or ``None``.
    """
    saved = save_selections(
        url,
        features,
        prefs,
        app_name=app_name,
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
        test_source = generate_test_source(code_examples, project_name=project_name)
        if test_source:
            tests_dir = project_dir / "tests"
            test_path = save_test_file(test_source, target_dir=tests_dir)
            print(f"Generated {len(code_examples)} test case(s) in {test_path}")
    if doc_structures:
        print("Saved doc structures to duplo.json.")
    if design and (design.colors or design.fonts or design.layout):
        save_design_requirements(dataclasses.asdict(design), target_dir=project_dir)
        print("Saved design requirements to duplo.json.")

    claude_md = write_claude_md(target_dir=project_dir)
    print(f"CLAUDE.md written to {claude_md}")

    print("\nGenerating build roadmap …")
    roadmap = generate_roadmap(url, features, prefs)

    urls = _SECTION_URL_RE.findall(text)
    if urls:
        output_dir = project_dir / "screenshots"
        print(f"\nSaving reference screenshots to {output_dir}/ …")
        saved_shots = save_reference_screenshots(urls, output_dir)
        print(f"Saved {len(saved_shots)} screenshot(s).")
        feature_names = [f.name for f in features]
        screenshot_map = map_screenshots_to_features(text, feature_names, output_dir)
        if screenshot_map:
            save_screenshot_feature_map(screenshot_map, target_dir=project_dir)
            print(f"Screenshot→feature map saved ({len(screenshot_map)} entries).")

    return roadmap


def _execute_phase(
    plan_content: str,
    app_name: str,
    phase_label: str,
    *,
    skip_mcloop: bool = False,
) -> None:
    """Run McLoop, capture screenshots, compare, notify, and record history."""
    if not skip_mcloop:
        set_in_progress(phase_label, mcloop_done=False)
        print("\nRunning McLoop …")
        exit_code = run_mcloop(".")
        if exit_code != 0:
            print(f"McLoop exited with code {exit_code}")
            sys.exit(exit_code)
        print("McLoop complete.")
        set_in_progress(phase_label, mcloop_done=True)

    append_phase_to_history(plan_content)
    advance_phase()
    clear_in_progress()
    print("Phase complete. Recorded in duplo.json.")

    if app_name:
        output_path = Path("screenshots") / "current" / "main.png"
        launch_cmd = "./run.sh" if Path("run.sh").exists() else None
        print(f"\nCapturing screenshots with appshot ({app_name}) …")
        shot_code = capture_appshot(app_name, output_path, launch=launch_cmd)
        if shot_code == 0:
            print(f"Screenshot saved to {output_path}")
            _compare_with_references(output_path)
        else:
            print(f"appshot exited with code {shot_code} (screenshot skipped)")

    notify_phase_complete(phase_label)


def _compare_with_references(current: Path) -> None:
    """Compare *current* screenshot against any reference images and print results."""
    ref_dir = Path("screenshots")
    references = sorted(ref_dir.glob("*.png")) if ref_dir.is_dir() else []
    if not references:
        print("No reference screenshots found — skipping visual comparison.")
        return

    print(f"\nComparing screenshot against {len(references)} reference image(s) …")
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
