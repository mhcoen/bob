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
    write_claude_md,
)
from duplo.screenshotter import map_screenshots_to_features, save_reference_screenshots
from duplo.selector import select_features

_SECTION_URL_RE = re.compile(r"^=== (.+?) ===$", re.MULTILINE)
_DUPLO_JSON = ".duplo/duplo.json"
# Files that are project artifacts, not user-provided reference materials.
_PROJECT_FILES = {"PLAN.md", "CLAUDE.md", "README.md", "ISSUES.md", "NOTES.md"}


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
    if not scan.images and not scan.pdfs and not scan.text_files and not scan.urls:
        print(
            "No reference materials found.\n"
            "Drop images, PDFs, text files, or a file containing URLs\n"
            "into this directory and run duplo again."
        )
        sys.exit(1)

    print("Scanning reference materials …")
    if scan.images:
        print(f"  Images: {len(scan.images)}")
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

    # Extract visual design from reference images.
    design = DesignRequirements()
    relevant_images = [r.path for r in scan.relevance if r.category == "image" and r.relevant]
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
    ref_files = list(scan.images) + list(scan.pdfs) + list(scan.text_files)
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


def _analyze_new_files(file_names: list[str]) -> None:
    """Analyze new or changed files the same way as first run.

    Images are sent to Vision for design extraction, PDFs are
    converted to text, and URLs found in text files are scraped.
    Results are saved to duplo.json.
    """
    paths = [Path(name) for name in file_names]
    paths = [p for p in paths if p.exists()]
    if not paths:
        return

    scan = scan_files(paths)
    analyzed_anything = False

    # Extract visual design from new images.
    relevant_images = [r.path for r in scan.relevance if r.category == "image" and r.relevant]
    if relevant_images:
        print(f"\nAnalyzing {len(relevant_images)} new image(s) with Vision …")
        design = extract_design(relevant_images)
        if design.colors or design.fonts or design.layout:
            save_design_requirements(dataclasses.asdict(design))
            print(f"  Updated design requirements from {len(design.source_images)} image(s).")
            analyzed_anything = True
        else:
            print("  Could not extract design details from new images.")

    # Extract text from new PDFs.
    relevant_pdfs = [r.path for r in scan.relevance if r.category == "pdf" and r.relevant]
    if relevant_pdfs:
        print(f"\nExtracting text from {len(relevant_pdfs)} new PDF(s) …")
        pdf_text = extract_pdf_text(relevant_pdfs)
        if pdf_text:
            print(f"  Extracted text from {len(relevant_pdfs)} PDF(s).")
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
            analyzed_anything = True

    # Fetch new URLs.
    if scan.urls:
        existing_urls = _load_existing_urls()
        new_urls = [u for u in scan.urls if u not in existing_urls]
        if new_urls:
            print(f"\nFetching {len(new_urls)} new URL(s) …")
            for url in new_urls:
                print(f"  Fetching {url} …")
                try:
                    _, code_examples, doc_structures, page_records, raw_pages = fetch_site(url)
                    if page_records:
                        save_reference_urls(page_records)
                        if raw_pages:
                            save_raw_content(raw_pages, page_records)
                    analyzed_anything = True
                except Exception as exc:
                    print(f"  Failed to fetch {url}: {exc}")

    # Move processed reference files into .duplo/references/.
    ref_files = list(scan.images) + list(scan.pdfs) + list(scan.text_files)
    if ref_files:
        moved = move_references(ref_files)
        if moved:
            print(f"Moved {len(moved)} new reference file(s) to .duplo/references/.")

    if not analyzed_anything:
        print("No analyzable reference materials in new files.")


def _load_existing_urls() -> set[str]:
    """Load previously scraped URLs from duplo.json."""
    duplo_path = Path(_DUPLO_JSON)
    if not duplo_path.exists():
        return set()
    data = json.loads(duplo_path.read_text(encoding="utf-8"))
    records = data.get("reference_urls", [])
    return {r["url"] for r in records if "url" in r}


def _subsequent_run() -> None:
    """Resume an interrupted phase or advance to the next one."""
    # Detect file changes since last run.
    old_hashes = load_hashes(".")
    new_hashes = compute_hashes(".")
    diff = diff_hashes(old_hashes, new_hashes)
    if diff.added or diff.changed or diff.removed:
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
            _analyze_new_files(changed_files)

    save_hashes(new_hashes)

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

    If the page appears to list multiple products, prompt the user
    to provide a more specific URL. Returns ``(validated_url, product_name)``
    where *product_name* may be empty if unknown.
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

    print(f"This URL appears to list multiple products: {result.reason}")
    if result.products:
        print("Products found:")
        for i, name in enumerate(result.products, 1):
            print(f"  {i}. {name}")
    print(
        "\nPlease provide a URL that points to a single product,\n"
        "or press Enter to proceed with the original URL anyway."
    )
    new_url = input("Product URL: ").strip()
    if new_url:
        return new_url, ""
    return url, ""


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
