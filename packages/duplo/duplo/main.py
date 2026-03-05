"""Duplo CLI entry point."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    args = _parse_args()
    if args.command == "run":
        print("duplo run: not yet implemented")
    elif args.command == "next":
        print("duplo next: not yet implemented")
    elif args.url:
        print(f"duplo init: {args.url}")
        print("not yet implemented")
    else:
        print("Usage: duplo <url> | duplo run | duplo next")
        sys.exit(1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Duplicate any app by pointing at its website"
    )
    parser.add_argument(
        "url", nargs="?", default=None,
        help="Product URL to duplicate",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "run", help="Run the current phase",
    )
    subparsers.add_parser(
        "next", help="Generate and run the next phase",
    )
    return parser.parse_args()
