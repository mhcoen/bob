"""Duplo CLI entry point."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from duplo.appshot import capture_appshot
from duplo.comparator import compare_screenshots
from duplo.issuer import generate_issue_list, save_issue_list
from duplo.extractor import Feature, extract_features
from duplo.fetcher import fetch_site
from duplo.initializer import create_project_dir, project_name_from_url
from duplo.planner import generate_phase_plan, save_plan
from duplo.questioner import BuildPreferences, ask_preferences
from duplo.runner import run_mcloop
from duplo.saver import save_selections, write_claude_md
from duplo.screenshotter import save_reference_screenshots
from duplo.selector import select_features

_SECTION_URL_RE = re.compile(r"^=== (.+?) ===$", re.MULTILINE)
_DUPLO_JSON = "duplo.json"


def main() -> None:
    args = _parse_args()
    if args.command == "run":
        _cmd_run()
    elif args.command == "next":
        print("duplo next: not yet implemented")
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


def _cmd_run() -> None:
    duplo_path = Path(_DUPLO_JSON)
    if not duplo_path.exists():
        print(f"Error: {_DUPLO_JSON} not found. Run 'duplo init <url>' first.")
        sys.exit(1)

    data = json.loads(duplo_path.read_text(encoding="utf-8"))
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

    print("\nRunning McLoop …")
    exit_code = run_mcloop(".")
    if exit_code != 0:
        print(f"McLoop exited with code {exit_code}")
        sys.exit(exit_code)
    print("McLoop complete.")

    app_name = data.get("app_name", "")
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
    subparsers.add_parser(
        "next",
        help="Generate and run the next phase",
    )
    return parser.parse_args()
