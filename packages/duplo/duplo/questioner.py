"""Ask the user about platform, language, constraints, and preferences."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

_PLATFORMS = ["web", "mobile-ios", "mobile-android", "desktop", "cli", "api", "other"]


@dataclass
class BuildPreferences:
    platform: str
    language: str
    constraints: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)


def ask_preferences(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> BuildPreferences:
    """Interactively ask the user about build preferences.

    Collects platform, language/stack, constraints, and open-ended preferences.
    Returns a :class:`BuildPreferences` dataclass with the answers.
    """
    print_fn("")
    print_fn("=== Build Preferences ===")
    print_fn("")

    platform = _ask_platform(input_fn, print_fn)
    language = _ask_language(input_fn, print_fn)
    constraints = _ask_list(
        "Any constraints? (e.g. existing DB, cloud provider, performance SLA)",
        "Constraint",
        input_fn,
        print_fn,
    )
    preferences = _ask_list(
        "Any other preferences? (e.g. testing approach, architecture style, CI/CD)",
        "Preference",
        input_fn,
        print_fn,
    )

    prefs = BuildPreferences(
        platform=platform,
        language=language,
        constraints=constraints,
        preferences=preferences,
    )

    _print_summary(prefs, print_fn)
    return prefs


def _ask_platform(
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
) -> str:
    options = ", ".join(_PLATFORMS)
    print_fn(f"Target platform [{options}]:")
    while True:
        raw = input_fn("  Platform: ").strip().lower()
        if not raw:
            print_fn("  Please enter a platform.")
            continue
        # Accept any prefix match or exact value; fall back to raw if no match
        match = next((p for p in _PLATFORMS if p.startswith(raw)), None)
        if match:
            return match
        # Accept free-form if user types something not in the list
        return raw


def _ask_language(
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
) -> str:
    print_fn("Primary language / stack (e.g. Python/FastAPI, TypeScript/React):")
    while True:
        raw = input_fn("  Language/stack: ").strip()
        if raw:
            return raw
        print_fn("  Please enter a language or stack.")


def _ask_list(
    prompt: str,
    item_label: str,
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
) -> list[str]:
    """Collect zero or more free-form items from the user.

    The user enters one item per line; a blank line finishes input.
    """
    print_fn(prompt)
    print_fn(f"  (Enter one {item_label.lower()} per line; blank line when done)")
    items: list[str] = []
    while True:
        raw = input_fn(f"  {item_label} {len(items) + 1}: ").strip()
        if not raw:
            break
        items.append(raw)
    return items


def _print_summary(prefs: BuildPreferences, print_fn: Callable[[str], None]) -> None:
    print_fn("")
    print_fn("--- Preferences summary ---")
    print_fn(f"  Platform   : {prefs.platform}")
    print_fn(f"  Language   : {prefs.language}")
    if prefs.constraints:
        print_fn("  Constraints:")
        for c in prefs.constraints:
            print_fn(f"    - {c}")
    else:
        print_fn("  Constraints: none")
    if prefs.preferences:
        print_fn("  Preferences:")
        for p in prefs.preferences:
            print_fn(f"    - {p}")
    else:
        print_fn("  Preferences: none")
