"""BuildPreferences dataclass.

The interactive-prompt helpers (``ask_preferences``, ``_ask_platform``,
``_ask_language``, ``_ask_list``, ``_print_summary``, ``_PLATFORMS``)
were removed in Phase 7.4.4 as dead code — the pipeline now derives
:class:`BuildPreferences` from ``spec.architecture`` via
:func:`duplo.build_prefs.parse_build_preferences`, and the last
interactive caller (``_first_run``) was deleted in Phase 7.2.1.

The ``BuildPreferences`` dataclass itself is still live: 12 importers
across ``duplo/`` and ``tests/`` reference it. A future task
(CURRENT_PLAN.md § "BuildPreferences migration") will relocate the
dataclass to ``duplo/build_prefs.py`` and retarget callers; until then
this module exists solely to host the type.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BuildPreferences:
    platform: str
    language: str
    constraints: list[str] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)
