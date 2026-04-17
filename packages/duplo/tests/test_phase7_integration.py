"""Phase 7 end-to-end integration tests for the cleanup phase.

Each test constructs a tmpdir fixture, runs duplo's CLI entry point
programmatically, and asserts on the resulting output and filesystem
state. The legacy ``_first_run`` path was deleted in Phase 7.2.1, so
the fresh-directory flow now must exit cleanly with a message telling
the user to run ``duplo init`` — no interactive prompts, no directory
creation, no LLM calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from duplo.main import main


def _snapshot_dir(path: Path) -> set[Path]:
    """Return the set of all paths (files + directories) under *path*."""
    return {p.relative_to(path) for p in path.rglob("*")}


def _fail_on_input(*_args, **_kwargs):
    """Replacement for ``builtins.input`` that fails the test if called.

    The fresh-directory flow must not prompt the user — prompting was
    a ``_first_run`` behavior that was removed in Phase 7.2.1. Any
    attempt to call ``input`` indicates a regression.
    """
    raise AssertionError("interactive prompt attempted in fresh-directory flow")


class TestFreshDirectoryWithoutInitPrintsMessage:
    """Per CURRENT_PLAN.md § 'Automated integration tests':
    ``test_fresh_directory_without_init_prints_message``.
    """

    def test_fresh_directory_prints_init_message_exits_zero_no_side_effects(
        self, tmp_path, monkeypatch, capsys
    ):
        """Run duplo (no subcommand) in a completely empty tmpdir.

        Asserts:
        - A message directing the user to run ``duplo init`` is printed.
        - The process exits 0 (not 1) — a fresh directory is not an error.
        - No interactive prompt is attempted (no ``_first_run`` behavior).
        - No directories or files are created in the tmpdir (no
          ``_first_run`` directory-creation behavior).
        """
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        # Migration gate is exercised in its own integration test below;
        # here we only care about the post-migration dispatch behavior.
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)
        monkeypatch.setattr("builtins.input", _fail_on_input)

        before = _snapshot_dir(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "duplo init" in captured.out

        after = _snapshot_dir(tmp_path)
        assert after == before, (
            "fresh-directory flow must not create any files or directories; "
            f"new entries: {sorted(after - before)}"
        )
