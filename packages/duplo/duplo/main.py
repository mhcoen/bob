"""Duplo CLI entry point."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from duplo.extractor import extract_features
from duplo.fetcher import fetch_site
from duplo.questioner import ask_preferences
from duplo.screenshotter import save_reference_screenshots
from duplo.selector import select_features

_SECTION_URL_RE = re.compile(r"^=== (.+?) ===$", re.MULTILINE)


def main() -> None:
    args = _parse_args()
    if args.command == "run":
        print("duplo run: not yet implemented")
    elif args.command == "next":
        print("duplo next: not yet implemented")
    elif args.command == "init":
        print(f"Fetching {args.url} …")
        text = fetch_site(args.url)
        print(text)
        print("\nExtracting features …")
        features = extract_features(text)
        if features:
            print(f"\nFound {len(features)} feature(s).")
            features = select_features(features)
        else:
            print("No features extracted.")

        ask_preferences()

        urls = _SECTION_URL_RE.findall(text)
        if urls:
            output_dir = Path("screenshots")
            print(f"\nSaving reference screenshots to {output_dir}/ …")
            saved = save_reference_screenshots(urls, output_dir)
            print(f"Saved {len(saved)} screenshot(s).")
    else:
        print("Usage: duplo init <url> | duplo run | duplo next")
        sys.exit(1)


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
