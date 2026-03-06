"""Duplo CLI entry point."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

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

        saved = save_selections(args.url, features, prefs, target_dir=project_dir)
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
