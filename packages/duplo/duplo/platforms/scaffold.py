"""Write platform scaffold artifacts to the target project.

Called from the pipeline before Phase 0 plan generation.  Writes
files from :attr:`PlatformProfile.scaffold_files` and appends
:attr:`PlatformProfile.gitignore_entries` to ``.gitignore``.

All writes are idempotent: existing files are not overwritten
(the profile is the initial source of truth; once the file
exists, the developer owns it).
"""

from __future__ import annotations

import stat
from pathlib import Path

from duplo.platforms.schema import PlatformProfile


def write_scaffold(
    profiles: list[PlatformProfile],
    project_name: str,
    *,
    target_dir: Path | str = ".",
) -> list[Path]:
    """Write scaffold artifacts for *profiles* into *target_dir*.

    For each profile:

    1. Writes each :class:`ScaffoldFile` to *target_dir*,
       expanding ``{project_name}`` in content.  Files that
       already exist are **skipped** (not overwritten).

    2. Appends any ``gitignore_entries`` to *target_dir*/.gitignore
       that are not already present.

    Args:
        profiles: Resolved platform profiles (may be empty).
        project_name: Project name for template expansion.
        target_dir: Project root directory.

    Returns:
        List of paths that were written (not paths that were
        skipped because they already existed).
    """
    root = Path(target_dir).resolve()
    written: list[Path] = []

    for profile in profiles:
        for sf in profile.scaffold_files:
            dest = root / sf.path
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = sf.content.replace("{project_name}", project_name)
            dest.write_text(content, encoding="utf-8")
            if sf.executable:
                dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
            written.append(dest)

        # Append gitignore entries.
        gitignore_path = root / ".gitignore"
        existing_lines: set[str] = set()
        if gitignore_path.exists():
            existing_lines = {
                line.rstrip("\n")
                for line in gitignore_path.read_text(encoding="utf-8").splitlines()
            }
        new_entries = [
            entry
            for entry in profile.gitignore_entries
            if entry not in existing_lines
        ]
        if new_entries:
            with gitignore_path.open("a", encoding="utf-8") as f:
                # Ensure we start on a new line.
                if existing_lines:
                    content_so_far = gitignore_path.read_text(encoding="utf-8")
                    if content_so_far and not content_so_far.endswith("\n"):
                        f.write("\n")
                f.write(f"# Platform: {profile.display_name}\n")
                for entry in new_entries:
                    f.write(entry + "\n")
            written.append(gitignore_path)

    return written


def format_scaffold_notice(written: list[Path], target_dir: Path | str = ".") -> str:
    """Format a notice for the planner about pre-generated scaffold files.

    Returns a string suitable for appending to the planner system
    prompt.  Returns empty string if nothing was written.
    """
    if not written:
        return ""
    root = Path(target_dir).resolve()
    rel_paths = []
    for p in written:
        try:
            rel_paths.append(str(p.relative_to(root)))
        except ValueError:
            rel_paths.append(str(p))

    lines = [
        "",
        "## Pre-generated scaffold artifacts",
        "",
        "The following files have already been created by duplo and "
        "MUST NOT be recreated or overwritten by plan tasks:",
    ]
    for rp in rel_paths:
        lines.append(f"- {rp}")
    lines.append("")
    lines.append(
        "Phase 0 tasks should USE these files (e.g. 'Run ./run.sh to "
        "verify'), not recreate them."
    )
    lines.append("")
    return "\n".join(lines)
