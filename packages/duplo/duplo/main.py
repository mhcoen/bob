"""Duplo CLI entry point."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from duplo.appshot import capture_appshot
from duplo.collector import collect_feedback
from duplo.comparator import compare_screenshots
from duplo.issuer import generate_issue_list, save_issue_list
from duplo.extractor import Feature, extract_features
from duplo.notifier import notify_phase_complete
from duplo.fetcher import fetch_site
from duplo.initializer import create_project_dir, project_name_from_url
from duplo.planner import generate_next_phase_plan, generate_phase_plan, save_plan
from duplo.questioner import BuildPreferences, ask_preferences
from duplo.runner import run_mcloop
from duplo.saver import (
    append_phase_to_history,
    clear_in_progress,
    save_feedback,
    save_selections,
    set_in_progress,
    write_claude_md,
)
from duplo.screenshotter import save_reference_screenshots
from duplo.selector import select_features

_SECTION_URL_RE = re.compile(r"^=== (.+?) ===$", re.MULTILINE)
_DUPLO_JSON = "duplo.json"


def main() -> None:
    args = _parse_args()
    if args.command == "run":
        _cmd_run()
    elif args.command == "next":
        _cmd_next(feedback_file=args.feedback_file)
    elif args.command == "init":
        default_name = project_name_from_url(args.url)
        project_name = input(f"Project directory name [{default_name}]: ").strip() or default_name
        try:
            project_dir = create_project_dir(project_name)
        except FileExistsError as exc:
            print(f"Error: {exc}")
            sys.exit(1)
        print(f"Created project directory: {project_dir}")

        print(f"\nFetching {args.url} …")
        text = fetch_site(args.url)
        print(text)
        print("\nExtracting features …")
        features = extract_features(text)
        if features:
            print(f"\nFound {len(features)} feature(s).")
            features = select_features(features)
        else:
            print("No features extracted.")

        prefs = ask_preferences()

        default_app_name = project_name.replace("-", " ").title()
        app_name = (
            input(f"macOS app name for appshot screenshots [{default_app_name}]: ").strip()
            or default_app_name
        )

        saved = save_selections(
            args.url, features, prefs, app_name=app_name, target_dir=project_dir
        )
        print(f"\nSelections saved to {saved}")

        claude_md = write_claude_md(target_dir=project_dir)
        print(f"Appshot instructions written to {claude_md}")

        urls = _SECTION_URL_RE.findall(text)
        if urls:
            output_dir = project_dir / "screenshots"
            print(f"\nSaving reference screenshots to {output_dir}/ …")
            saved = save_reference_screenshots(urls, output_dir)
            print(f"Saved {len(saved)} screenshot(s).")
    else:
        print("Usage: duplo init <url> | duplo run | duplo next")
        sys.exit(1)


def _cmd_next(feedback_file: str | None = None) -> None:
    """Collect feedback, generate the next phase PLAN.md, and run McLoop."""
    app_name = ""
    in_progress = None
    duplo_path = Path(_DUPLO_JSON)
    if duplo_path.exists():
        data = json.loads(duplo_path.read_text(encoding="utf-8"))
        app_name = data.get("app_name", "")
        in_progress = data.get("in_progress")

    plan_path = Path("PLAN.md")
    if not plan_path.exists():
        print("Error: PLAN.md not found. Run 'duplo run' first.")
        sys.exit(1)

    # Resume an interrupted phase — skip feedback collection and plan generation.
    if in_progress:
        phase_label = in_progress["label"]
        content = plan_path.read_text(encoding="utf-8")
        if in_progress.get("mcloop_done"):
            print(f"Resuming {phase_label}: McLoop already done, completing phase …")
            _execute_phase(content, app_name, phase_label, skip_mcloop=True)
        else:
            print(f"Resuming {phase_label}: re-running McLoop …")
            _execute_phase(content, app_name, phase_label)
        return

    current_plan = plan_path.read_text(encoding="utf-8")

    issues_text = ""
    issues_path = Path("ISSUES.md")
    if issues_path.exists():
        issues_text = issues_path.read_text(encoding="utf-8")

    try:
        feedback = collect_feedback(feedback_file=feedback_file)
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
    saved = save_plan(content)
    print(f"Next phase plan saved to {saved}")

    match = re.search(r"#\s*(Phase\s+\d+[^\n]*)", content, re.IGNORECASE | re.MULTILINE)
    phase_label = match.group(1).strip() if match else "Next Phase"
    _execute_phase(content, app_name, phase_label)


def _cmd_run() -> None:
    duplo_path = Path(_DUPLO_JSON)
    if not duplo_path.exists():
        print(f"Error: {_DUPLO_JSON} not found. Run 'duplo init <url>' first.")
        sys.exit(1)

    data = json.loads(duplo_path.read_text(encoding="utf-8"))
    app_name = data.get("app_name", "")
    in_progress = data.get("in_progress")

    # If Phase 1 is already recorded in history it is complete.
    history = data.get("history", [])
    if any(re.search(r"^Phase\s+1\b", h.get("phase", ""), re.IGNORECASE) for h in history):
        print("Phase 1 already complete. Run 'duplo next' to continue.")
        return

    plan_path = Path("PLAN.md")

    # McLoop finished but post-processing was interrupted.
    if in_progress and in_progress.get("mcloop_done"):
        print(f"Resuming {in_progress['label']}: McLoop already done, completing phase …")
        content = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
        _execute_phase(content, app_name, in_progress["label"], skip_mcloop=True)
        return

    # PLAN.md exists from a prior interrupted run — skip plan generation.
    if plan_path.exists():
        print("Resuming Phase 1: PLAN.md found, re-running McLoop …")
        content = plan_path.read_text(encoding="utf-8")
        _execute_phase(content, app_name, "Phase 1")
        return

    source_url = data["source_url"]
    features = [Feature(**f) for f in data["features"]]
    prefs_data = data["preferences"]
    preferences = BuildPreferences(
        platform=prefs_data["platform"],
        language=prefs_data["language"],
        constraints=prefs_data.get("constraints", []),
        preferences=prefs_data.get("preferences", []),
    )

    print("Generating Phase 1 PLAN.md …")
    content = generate_phase_plan(source_url, features, preferences)
    saved = save_plan(content)
    print(f"Phase 1 plan saved to {saved}")
    _execute_phase(content, app_name, "Phase 1")


def _execute_phase(
    plan_content: str,
    app_name: str,
    phase_label: str,
    *,
    skip_mcloop: bool = False,
) -> None:
    """Run McLoop, capture screenshots, compare, notify, and record history.

    When *skip_mcloop* is ``True`` McLoop is not invoked (used when resuming
    after an interruption that occurred after McLoop already completed).
    """
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
    clear_in_progress()
    print("Phase appended to duplo.json history.")

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
    ref_dir = Path("screenshots") / "reference"
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Duplicate any app by pointing at its website")
    subparsers = parser.add_subparsers(dest="command")
    init_parser = subparsers.add_parser(
        "init",
        help="Initialise a new duplication from a product URL",
    )
    init_parser.add_argument("url", help="Product URL to duplicate")
    subparsers.add_parser(
        "run",
        help="Run the current phase",
    )
    next_parser = subparsers.add_parser(
        "next",
        help="Generate and run the next phase",
    )
    next_parser.add_argument(
        "--feedback-file",
        metavar="FILE",
        default=None,
        help="Path to a plain-text file containing feedback (default: interactive input)",
    )
    return parser.parse_args()
