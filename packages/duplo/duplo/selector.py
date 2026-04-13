"""Present extracted features to the user and ask which to include."""

from __future__ import annotations

from collections import defaultdict
from typing import Callable

from duplo.extractor import Feature

_CATEGORY_ORDER = ["core", "ui", "integrations", "api", "security", "other"]


def select_features(
    features: list[Feature],
    *,
    recommended: list[str] | None = None,
    phase_label: str | None = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> list[Feature]:
    """Display *features* and interactively ask the user which to include.

    When *recommended* is provided (a list of feature names from the
    roadmap's next phase), those features are marked with ``*`` in the
    display and used as the default selection instead of "all".

    When *phase_label* is also provided (e.g. ``"Phase 2"``), the
    recommendation line includes the phase name and the numbered
    feature indices for quick reference.

    Accepts selections like:
      - "all"           – include everything
      - blank           – include recommended (or all if none)
      - "none"          – include nothing
      - "1,3,5"         – comma-separated numbers
      - "1-4,7"         – ranges and individual numbers mixed

    Returns the list of selected :class:`Feature` objects.
    """
    if not features:
        print_fn("No features to select.")
        return []

    rec_set = set(recommended) if recommended else set()
    _print_features(features, print_fn, recommended=rec_set)

    rec_indices = _recommended_indices(features, rec_set)
    if rec_indices is not None:
        default_label = ",".join(str(i + 1) for i in rec_indices)
        default_hint = default_label
    else:
        default_hint = "all"

    print_fn("")
    print_fn("Enter the numbers of the features to include.")
    print_fn('  Examples: "all", "none", "1,3,5", "1-4,7"')
    if rec_indices is not None:
        nums = ", ".join(str(i + 1) for i in rec_indices)
        if phase_label:
            print_fn(f"  * = Recommended for {phase_label}: {nums}")
        else:
            print_fn(f"  * = recommended by roadmap: {nums}")
    raw = input_fn(f"Selection [{default_hint}]: ").strip()

    if raw.lower() == "all":
        selected = list(features)
    elif raw.lower() == "none":
        selected = []
    elif not raw:
        # Empty input: use recommended if available, otherwise all.
        if rec_indices is not None:
            selected = [features[i] for i in rec_indices]
        else:
            selected = list(features)
    else:
        indices = _parse_selection(raw, len(features))
        selected = [features[i] for i in sorted(indices)]

    print_fn(f"\n{len(selected)} of {len(features)} feature(s) selected.")
    return selected


def _recommended_indices(features: list[Feature], rec_set: set[str]) -> list[int] | None:
    """Return sorted 0-based indices of features in *rec_set*, or None."""
    if not rec_set:
        return None
    indices = [i for i, f in enumerate(features) if f.name in rec_set]
    return indices if indices else None


def _print_features(
    features: list[Feature],
    print_fn: Callable[[str], None],
    *,
    recommended: set[str] | None = None,
) -> None:
    rec_set = recommended or set()
    by_category: dict[str, list[tuple[int, Feature]]] = defaultdict(list)
    for idx, feature in enumerate(features, start=1):
        cat = feature.category if feature.category in _CATEGORY_ORDER else "other"
        by_category[cat].append((idx, feature))

    for cat in _CATEGORY_ORDER:
        if cat not in by_category:
            continue
        print_fn(f"\n[{cat.upper()}]")
        for idx, feature in by_category[cat]:
            marker = " *" if feature.name in rec_set else ""
            print_fn(f"  {idx:>3}. {feature.name}{marker}")
            print_fn(f"       {feature.description}")


def select_issues(
    issues: list[dict],
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> list[dict]:
    """Display open *issues* and interactively ask which to address.

    Only issues with ``status == "open"`` (or no ``status`` field) are
    shown.  Uses the same numbered selection pattern as
    :func:`select_features`.

    Accepts selections like:
      - "all"           – include everything
      - blank           – include nothing (skip issues)
      - "none"          – include nothing
      - "1,3,5"         – comma-separated numbers
      - "1-4,7"         – ranges and individual numbers mixed

    Returns the list of selected issue dicts.  Blank input selects all
    open issues (matching the ``[all]`` prompt default).
    """
    open_issues = [iss for iss in issues if iss.get("status", "open") == "open"]
    if not open_issues:
        return []

    print_fn("\nOpen issues:")
    for idx, iss in enumerate(open_issues, start=1):
        severity = iss.get("severity", iss.get("source", ""))
        label = f" [{severity}]" if severity else ""
        print_fn(f"  {idx:>3}. {iss['description']}{label}")

    print_fn("")
    print_fn("Which issues should be addressed in this phase?")
    print_fn('  Examples: "all", "none", "1,3", "1-3"')
    raw = input_fn("Issues [all]: ").strip()

    if not raw or raw.lower() == "all":
        selected = list(open_issues)
    elif raw.lower() == "none":
        selected = []
    else:
        indices = _parse_selection(raw, len(open_issues))
        selected = [open_issues[i] for i in sorted(indices)]

    print_fn(f"\n{len(selected)} of {len(open_issues)} issue(s) selected.")
    return selected


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
            for n in range(min(start, end), max(start, end) + 1):
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
