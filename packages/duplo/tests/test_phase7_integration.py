"""Phase 7 end-to-end integration tests for the cleanup phase.

Each test constructs a tmpdir fixture, runs duplo's CLI entry point
programmatically, and asserts on the resulting output and filesystem
state. The legacy ``_first_run`` path was deleted in Phase 7.2.1, so
the fresh-directory flow now must exit cleanly with a message telling
the user to run ``duplo init`` — no interactive prompts, no directory
creation, no LLM calls.
"""

from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

import pytest

import duplo
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


class TestOldProjectStillBlockedByMigration:
    """Per CURRENT_PLAN.md § 'Automated integration tests':
    ``test_old_project_still_blocked_by_migration``.

    An old-format project — one with ``.duplo/duplo.json`` but no
    new-format ``SPEC.md`` — must still hit the migration gate
    (``duplo.migration._check_migration``) and exit with a message
    telling the user how to migrate. Phase 7 cleanup removed
    ``_first_run`` but must NOT have weakened the migration gate.
    """

    def test_old_project_hits_migration_gate(self, tmp_path, monkeypatch, capsys):
        """Run duplo (no subcommand) against an old-format project.

        Fixture: ``.duplo/duplo.json`` present, ``SPEC.md`` absent.
        This is the shape of a project created by the pre-redesign
        duplo (which wrote ``duplo.json`` as its state file but never
        authored ``SPEC.md``).

        Asserts:
        - The migration message is printed to stdout.
        - The message references ``duplo init`` as the recommended path.
        - The process exits 1 (migration needed is a hard stop).
        """
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "duplo.json").write_text("{}\n", encoding="utf-8")
        assert not (tmp_path / "SPEC.md").exists()

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("builtins.input", _fail_on_input)

        with pytest.raises(SystemExit) as exc_info:
            main()

        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "This project predates the SPEC.md / ref/ redesign." in captured.out
        assert "duplo init" in captured.out


class TestNoDeadImportsRemain:
    """Per CURRENT_PLAN.md § 'Automated integration tests':
    ``test_no_dead_imports_remain``.

    Smoke test that walks the ``duplo`` package and imports every
    submodule. Catches stale ``from duplo.X import Y`` statements where
    ``X`` was deleted by Phase 7 cleanup or where ``Y`` was removed
    from a surviving module. Individual deletion tests only check the
    modules they touch; this test sweeps the whole package so a dead
    import anywhere in the tree still fails loudly.
    """

    def test_every_duplo_submodule_imports_cleanly(self):
        """Import every module under ``duplo/`` via ``importlib``.

        Uses ``pkgutil.iter_modules`` on ``duplo.__path__`` to enumerate
        submodules — this is resilient to renames, so we do not need to
        hand-maintain a list. Any ``ImportError`` (stale import of a
        deleted module) or ``AttributeError`` raised at import time
        (stale ``from`` of a deleted symbol) fails the test.
        """
        failures: list[str] = []
        for module_info in pkgutil.iter_modules(duplo.__path__):
            name = f"duplo.{module_info.name}"
            try:
                importlib.import_module(name)
            except (ImportError, AttributeError) as exc:
                failures.append(f"{name}: {type(exc).__name__}: {exc}")

        assert not failures, (
            "one or more duplo submodules failed to import; a deleted "
            "module or symbol is still referenced somewhere:\n  " + "\n  ".join(failures)
        )
