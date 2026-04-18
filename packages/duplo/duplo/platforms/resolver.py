"""Resolve BuildPreferences to matching PlatformProfile(s).

The resolver scores each registered profile against the preferences
and returns the best match(es).  A profile must match on BOTH
platform AND language to be considered; preference matches are a
bonus that breaks ties.

Importing this module triggers discovery of all platform knowledge
modules (so their ``register()`` calls run).
"""

from __future__ import annotations

from duplo.platforms.schema import PlatformProfile, all_profiles
from duplo.questioner import BuildPreferences

# Side-effect imports: ensure every platform module has registered
# its profile before resolve_profiles() is called.
import duplo.platforms.macos.swiftui_spm as _  # noqa: F401
import duplo.platforms.macos.python_cli as _  # noqa: F401


def resolve_profiles(prefs: BuildPreferences) -> list[PlatformProfile]:
    """Return matching profiles for *prefs*, best-match first.

    Matching algorithm:

    1. **Platform match (required).**  At least one entry in
       ``profile.match_platform`` must appear as a case-insensitive
       substring in ``prefs.platform``.  Score: +2.

    2. **Language match (required).**  At least one entry in
       ``profile.match_language`` must appear as a case-insensitive
       substring in ``prefs.language``.  Score: +2.

    3. **Preference bonus (optional).**  For each entry in
       ``profile.match_any_preference`` that appears as a
       case-insensitive substring in *any* element of
       ``prefs.preferences``, score +1.

    Profiles that fail either required match are discarded.
    Remaining profiles are sorted by descending score.  Ties are
    broken by ``profile.id`` for determinism.

    Returns an empty list if no profile matches (backward-compatible
    with the pre-platform-knowledge behavior: no injection).
    """
    platform_lower = prefs.platform.lower()
    language_lower = prefs.language.lower()
    preferences_lower = [p.lower() for p in prefs.preferences]

    scored: list[tuple[int, str, PlatformProfile]] = []

    for profile in all_profiles():
        # Required: platform match.
        platform_hit = any(mp in platform_lower for mp in profile.match_platform)
        if not platform_hit:
            continue

        # Required: language match.
        language_hit = any(ml in language_lower for ml in profile.match_language)
        if not language_hit:
            continue

        score = 4  # base score for matching both required criteria

        # Bonus: preference matches.
        for mp in profile.match_any_preference:
            if any(mp in p for p in preferences_lower):
                score += 1

        scored.append((score, profile.id, profile))

    # Sort: highest score first, then alphabetical id for determinism.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [profile for _, _, profile in scored]
