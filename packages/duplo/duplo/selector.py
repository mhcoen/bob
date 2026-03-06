"""Present extracted features to the user and ask which to include."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from duplo.extractor import Feature

_CATEGORY_ORDER = ["core", "ui", "integrations", "api", "security", "other"]


def select_features(
    features: list[Feature],
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> list[Feature]:
    """Display *features* and interactively ask the user which to include.

    Accepts selections like:
      - "all" or blank  – include everything
      - "none"          – include nothing
      - "1,3,5"         – comma-separated numbers
      - "1-4,7"         – ranges and individual numbers mixed

    Returns the list of selected :class:`Feature` objects.
    """
    if not features:
        print_fn("No features to select.")
        return []

    _print_features(features, print_fn)

    print_fn("")
    print_fn("Enter the numbers of the features to include.")
    print_fn('  Examples: "all", "none", "1,3,5", "1-4,7"')
    raw = input_fn("Selection [all]: ").strip()

    if not raw or raw.lower() == "all":
        selected = list(features)
    elif raw.lower() == "none":
        selected = []
    else:
        indices = _parse_selection(raw, len(features))
        selected = [features[i] for i in sorted(indices)]

    print_fn(f"\n{len(selected)} of {len(features)} feature(s) selected.")
    return selected


def _print_features(features: list[Feature], print_fn: Callable[[str], None]) -> None:
    by_category: dict[str, list[tuple[int, Feature]]] = defaultdict(list)
    for idx, feature in enumerate(features, start=1):
        cat = feature.category if feature.category in _CATEGORY_ORDER else "other"
        by_category[cat].append((idx, feature))

    for cat in _CATEGORY_ORDER:
        if cat not in by_category:
            continue
        print_fn(f"\n[{cat.upper()}]")
        for idx, feature in by_category[cat]:
            print_fn(f"  {idx:>3}. {feature.name}")
            print_fn(f"       {feature.description}")


def _parse_selection(raw: str, count: int) -> set[int]:
    """Parse a selection string into a set of 0-based indices.

    Accepts comma-separated tokens; each token is either a single number or
    a range like "2-5". Numbers outside [1, count] are silently ignored.
    """
    indices: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            parts = token.split("-", 1)
            try:
                start = int(parts[0].strip())
                end = int(parts[1].strip())
            except ValueError:
                continue
            for n in range(start, end + 1):
                if 1 <= n <= count:
                    indices.add(n - 1)
        else:
            try:
                n = int(token)
            except ValueError:
                continue
            if 1 <= n <= count:
                indices.add(n - 1)
    return indices
