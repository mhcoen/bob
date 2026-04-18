"""PlatformProfile dataclass and the global profile registry.

Each platform knowledge module (e.g. ``macos/swiftui_spm.py``)
calls :func:`register` at import time to add its profile.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScaffoldFile:
    """A file to be created during scaffold generation.

    Attributes:
        path: Relative path from project root (e.g. ``run.sh``).
        content: File content (may contain ``{project_name}``
            placeholder for template expansion).
        executable: Whether the file should be marked executable.
    """

    path: str
    content: str
    executable: bool = False


@dataclass(frozen=True)
class PlatformProfile:
    """Platform-specific operational knowledge for duplo's generators.

    All string lists use plain ASCII (no backticks, em dashes, or
    special characters) because their content may end up in git
    commit messages via PLAN.md task descriptions.

    Attributes:
        id: Short identifier, e.g. ``macos-swiftui-spm``.
        display_name: Human-readable label for logging.
        match_platform: Lowercase substrings to match against
            ``BuildPreferences.platform``.
        match_language: Lowercase substrings to match against
            ``BuildPreferences.language``.
        match_any_preference: Lowercase substrings; if ANY appears
            in any element of ``BuildPreferences.preferences``,
            the match score increases.
        planner_rules: Rules injected into the planner system prompt.
            Each rule is a single imperative sentence.
        claude_md_rules: Rules written to the CLAUDE.md
            ``## Platform rules`` section.  Each rule is a short
            paragraph aimed at Claude Code in ``-p`` mode.
        scaffold_files: Files written to disk before Phase 0 plan
            generation.  The planner is told they exist.
        prerequisites: Human-readable prerequisite descriptions,
            injected into Phase 0 preamble.
        failure_modes: Common mistakes the LLM makes on this
            platform.  Injected into both the planner prompt and
            CLAUDE.md as explicit warnings.
        bootstrap_steps: Shell commands or task descriptions for
            environment setup.  Become Phase 0 scaffold tasks.
        gitignore_entries: Lines to add to .gitignore.
    """

    id: str
    display_name: str

    # Matching criteria.
    match_platform: list[str] = field(default_factory=list)
    match_language: list[str] = field(default_factory=list)
    match_any_preference: list[str] = field(default_factory=list)

    # Planner injection.
    planner_rules: list[str] = field(default_factory=list)

    # CLAUDE.md rules.
    claude_md_rules: list[str] = field(default_factory=list)

    # Scaffold artifacts.
    scaffold_files: list[ScaffoldFile] = field(default_factory=list)

    # Environment prerequisites.
    prerequisites: list[str] = field(default_factory=list)

    # Common failure modes.
    failure_modes: list[str] = field(default_factory=list)

    # Bootstrap steps.
    bootstrap_steps: list[str] = field(default_factory=list)

    # Gitignore entries.
    gitignore_entries: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

_REGISTRY: list[PlatformProfile] = []


def register(profile: PlatformProfile) -> None:
    """Add *profile* to the global registry.

    Called at module import time by each platform knowledge module.
    """
    _REGISTRY.append(profile)


def all_profiles() -> list[PlatformProfile]:
    """Return a shallow copy of all registered profiles."""
    return list(_REGISTRY)
