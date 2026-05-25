"""Tests for duplo.platforms.scaffold."""

from __future__ import annotations

import stat
from pathlib import Path

from duplo.platforms.scaffold import format_scaffold_notice, write_scaffold
from duplo.platforms.schema import PlatformProfile, ScaffoldFile


def _profile(
    *,
    files: list[ScaffoldFile] | None = None,
    gitignore: list[str] | None = None,
    profile_id: str = "test-profile",
    display_name: str = "Test Profile",
) -> PlatformProfile:
    return PlatformProfile(
        id=profile_id,
        display_name=display_name,
        scaffold_files=files or [],
        gitignore_entries=gitignore or [],
    )


_RUN_SH_BODY = "#!/bin/bash\necho running {project_name}\n"


class TestWriteScaffold:
    def test_creates_run_sh_with_content_and_executable_bit(self, tmp_path: Path):
        profile = _profile(
            files=[ScaffoldFile(path="run.sh", content=_RUN_SH_BODY, executable=True)]
        )
        written = write_scaffold([profile], "MyApp", target_dir=tmp_path)

        run_sh = tmp_path / "run.sh"
        assert run_sh in written
        assert run_sh.exists()
        assert run_sh.read_text(encoding="utf-8") == "#!/bin/bash\necho running MyApp\n"
        mode = run_sh.stat().st_mode
        assert mode & stat.S_IXUSR, "owner execute bit not set"
        assert mode & stat.S_IXGRP, "group execute bit not set"

    def test_does_not_overwrite_existing_files(self, tmp_path: Path):
        run_sh = tmp_path / "run.sh"
        run_sh.write_text("original user content\n", encoding="utf-8")
        original_mode = run_sh.stat().st_mode

        profile = _profile(
            files=[ScaffoldFile(path="run.sh", content=_RUN_SH_BODY, executable=True)]
        )
        written = write_scaffold([profile], "MyApp", target_dir=tmp_path)

        assert run_sh not in written
        assert run_sh.read_text(encoding="utf-8") == "original user content\n"
        # Executable bit not forced on an already-existing file.
        assert run_sh.stat().st_mode == original_mode

    def test_gitignore_entries_appended_without_duplication(self, tmp_path: Path):
        gitignore = tmp_path / ".gitignore"
        gitignore.write_text(".build/\n.DS_Store\n", encoding="utf-8")

        profile = _profile(gitignore=[".build/", ".DS_Store", "*.app/", ".swiftpm/"])
        write_scaffold([profile], "MyApp", target_dir=tmp_path)

        content = gitignore.read_text(encoding="utf-8")
        lines = [line for line in content.splitlines() if line and not line.startswith("#")]
        # Each meaningful entry appears exactly once.
        for entry in [".build/", ".DS_Store", "*.app/", ".swiftpm/"]:
            assert lines.count(entry) == 1, f"{entry} duplicated in {lines}"

    def test_gitignore_created_when_missing(self, tmp_path: Path):
        profile = _profile(gitignore=["build/", "*.log"])
        write_scaffold([profile], "MyApp", target_dir=tmp_path)

        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text(encoding="utf-8")
        assert "build/" in content
        assert "*.log" in content

    def test_no_gitignore_write_when_all_entries_present(self, tmp_path: Path):
        gitignore = tmp_path / ".gitignore"
        original = "build/\n*.log\n"
        gitignore.write_text(original, encoding="utf-8")

        profile = _profile(gitignore=["build/", "*.log"])
        written = write_scaffold([profile], "MyApp", target_dir=tmp_path)

        assert gitignore not in written
        assert gitignore.read_text(encoding="utf-8") == original

    def test_empty_profiles_is_noop(self, tmp_path: Path):
        written = write_scaffold([], "MyApp", target_dir=tmp_path)
        assert written == []
        assert list(tmp_path.iterdir()) == []


class TestFormatScaffoldNotice:
    def test_empty_when_nothing_written(self):
        assert format_scaffold_notice([]) == ""

    def test_lists_relative_paths(self, tmp_path: Path):
        p = tmp_path / "run.sh"
        p.write_text("x", encoding="utf-8")
        notice = format_scaffold_notice([p], target_dir=tmp_path)
        assert "run.sh" in notice
        assert "MUST NOT" in notice
