"""Write platform scaffold artifacts to the target project.

Called from the pipeline before Phase 0 plan generation.  Writes
files from :attr:`PlatformProfile.scaffold_files` and appends
:attr:`PlatformProfile.gitignore_entries` to ``.gitignore``.

All writes are idempotent: existing files are not overwritten
(the profile is the initial source of truth; once the file
exists, the developer owns it).

Template variables expanded in BOTH file paths and content:

  - ``{project_name}``: the PyPI distribution name (may contain
    hyphens; legal in PEP 503).
  - ``{package_name}``: the Python identifier form (hyphens
    replaced with underscores). MUST be used everywhere a Python
    module identifier is required: package directory names,
    ``[project.scripts]`` value module paths,
    ``[tool.setuptools.packages.find].include``, and
    ``run.sh``'s ``python -m`` invocation.

Splitting these is what prevents the canonical-mode consistency
validator and the Python-identifier validator from firing on
freshly-scaffolded projects whose distribution name contains a
hyphen.
"""

from __future__ import annotations

import stat
from pathlib import Path

from duplo.platforms.schema import PlatformProfile


def project_name_to_package_name(project_name: str) -> str:
    """Return the Python-identifier form of a distribution name.

    Replaces hyphens with underscores. PyPI distribution names are
    PEP 503-normalized to allow hyphens, dots, and case variations;
    Python module identifiers (PEP 8 package-and-module-names)
    require letters, digits, and underscores only.

    Examples:
        ``fswatch-run-smoke`` -> ``fswatch_run_smoke``
        ``my-cool-cli``       -> ``my_cool_cli``
        ``already_clean``     -> ``already_clean``
    """
    return project_name.replace("-", "_")


def write_scaffold(
    profiles: list[PlatformProfile],
    project_name: str,
    *,
    target_dir: Path | str = ".",
) -> list[Path]:
    """Write scaffold artifacts for *profiles* into *target_dir*.

    For each profile:

    1. Writes each :class:`ScaffoldFile` to *target_dir*,
       expanding ``{project_name}`` and ``{package_name}`` in BOTH
       the file path AND its content.  Files that already exist
       are **skipped** (not overwritten).

    2. Appends any ``gitignore_entries`` to *target_dir*/.gitignore
       that are not already present.

    Args:
        profiles: Resolved platform profiles (may be empty).
        project_name: Project name for template expansion (PyPI
            distribution name; may contain hyphens).
        target_dir: Project root directory.

    Returns:
        List of paths that were written (not paths that were
        skipped because they already existed).
    """
    root = Path(target_dir).resolve()
    written: list[Path] = []

    package_name = project_name_to_package_name(project_name)

    def _expand(s: str) -> str:
        return s.replace("{project_name}", project_name).replace("{package_name}", package_name)

    for profile in profiles:
        for sf in profile.scaffold_files:
            expanded_path = _expand(sf.path)
            dest = root / expanded_path
            if dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            content = _expand(sf.content)
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
        new_entries = [entry for entry in profile.gitignore_entries if entry not in existing_lines]
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
        "Phase 0 tasks should USE these files (e.g. 'Run ./run.sh to verify'), not recreate them."
    )
    lines.append("")
    return "\n".join(lines)
