"""Tests for the ``bob-plan`` CLI in :mod:`bob_tools.planfile.cli`.

Exercises each subcommand (``validate``, ``next``, ``fmt``, ``done``,
``fail``) for:

* the success path (correct exit code 0 and the documented stdout
  shape),
* parse and validation failure (exit code 1) — bad PLAN.md is
  surfaced through ``cmd_validate`` and the validate-first guard on
  every other subcommand,
* task-not-found (exit code 2) for ``done`` / ``fail`` referencing an
  unknown task id,
* JSON output shape for ``done`` and ``fail``: a list of
  ``Settlement`` dicts, with the kind and task id readable straight
  out of the parsed JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bob_tools.planfile.cli import (
    EXIT_INVALID_PLAN,
    EXIT_OK,
    EXIT_TASK_NOT_FOUND,
    main,
)

STRICT_PLAN = """<!-- bob-plan-format: 1 -->

# Strict CLI Fixture

## Stage 1: Bootstrap
<!-- phase_id: phase_001 -->

- [ ] T-000001: first task
- [ ] T-000002: second task
"""

MCLOOP_CANONICAL_NON_CONSTRUCTED_PLAN = """# McLoop Canonical Fixture

## Stage 1: Bootstrap
<!-- phase_id: phase_001 -->

First paragraph. Second paragraph.

- [ ] T-000001: first task
- [ ] T-000002: second task
"""

COMPAT_PLAN = """# Compat CLI Fixture

## Stage 1: Bootstrap

- [ ] first task
- [ ] second task
"""

MARKER_BEARING_ID_LESS_PLAN = """<!-- bob-plan-format: 1 -->

# Marker Fixture

## Stage 1: Bootstrap

- [ ] first task
- [ ] second task
"""

PHASELESS_PLAN = """# Phaseless Fixture

- [ ] stray task
"""

INVALID_PLAN = """# Broken Plan

# Broken Plan

## Stage 1: Bootstrap

- [ ] only task
"""


@pytest.fixture
def strict_plan_path(tmp_path: Path) -> Path:
    path = tmp_path / "PLAN.md"
    path.write_text(STRICT_PLAN)
    return path


@pytest.fixture
def mcloop_canonical_non_constructed_path(tmp_path: Path) -> Path:
    path = tmp_path / "PLAN.md"
    path.write_text(MCLOOP_CANONICAL_NON_CONSTRUCTED_PLAN)
    return path


@pytest.fixture
def compat_plan_path(tmp_path: Path) -> Path:
    path = tmp_path / "PLAN.md"
    path.write_text(COMPAT_PLAN)
    return path


@pytest.fixture
def invalid_plan_path(tmp_path: Path) -> Path:
    path = tmp_path / "PLAN.md"
    path.write_text(INVALID_PLAN)
    return path


class TestValidate:
    def test_success(
        self, strict_plan_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["validate", str(strict_plan_path)])
        captured = capsys.readouterr()
        assert rc == EXIT_OK
        assert "OK" in captured.out

    def test_parse_failure_exits_one(
        self, invalid_plan_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["validate", str(invalid_plan_path)])
        captured = capsys.readouterr()
        assert rc == EXIT_INVALID_PLAN
        assert captured.err  # error message routed to stderr

    def test_compat_plan_is_accepted(
        self, compat_plan_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["validate", str(compat_plan_path)])
        captured = capsys.readouterr()
        assert rc == EXIT_OK
        assert "OK" in captured.out


class TestNext:
    def test_prints_first_actionable(
        self, strict_plan_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["next", str(strict_plan_path)])
        captured = capsys.readouterr()
        assert rc == EXIT_OK
        assert captured.out.strip() == "T-000001: first task"

    def test_invalid_plan_exits_one(
        self, invalid_plan_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["next", str(invalid_plan_path)])
        captured = capsys.readouterr()
        assert rc == EXIT_INVALID_PLAN
        assert captured.err


class TestFmt:
    def test_assigns_task_ids_to_compat_plan(self, compat_plan_path: Path) -> None:
        rc = main(["fmt", str(compat_plan_path)])
        assert rc == EXIT_OK
        text = compat_plan_path.read_text()
        assert "T-000001" in text
        assert "T-000002" in text
        # phase-id comment was added (phase_id_source was "none" pre-migrate)
        assert "<!-- phase_id:" in text

    def test_idempotent_on_strict_plan(self, strict_plan_path: Path) -> None:
        rc = main(["fmt", str(strict_plan_path)])
        assert rc == EXIT_OK
        once = strict_plan_path.read_text()
        rc2 = main(["fmt", str(strict_plan_path)])
        assert rc2 == EXIT_OK
        twice = strict_plan_path.read_text()
        assert once == twice

    def test_assigns_task_ids_on_marker_bearing_plan(self, tmp_path: Path) -> None:
        """A magic-lined file with id-less tasks is fmt's core repair case.

        ``load`` would reject it (the magic line force-enables strict
        parsing, which requires ids), so fmt must parse leniently; this
        used to exit 1 with "expected task id" and left no tool path to
        canonicalize such a file.
        """
        path = tmp_path / "PLAN.md"
        path.write_text(MARKER_BEARING_ID_LESS_PLAN)
        rc = main(["fmt", str(path)])
        assert rc == EXIT_OK
        text = path.read_text()
        assert "T-000001" in text
        assert "T-000002" in text
        assert "<!-- bob-plan-format: 1 -->" in text

    def test_refuses_to_drop_checkboxes_outside_phase_heading(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The parser drops checkboxes that sit outside any Stage/Phase
        heading; fmt must refuse rather than silently rewrite the file
        without them."""
        path = tmp_path / "PLAN.md"
        path.write_text(PHASELESS_PLAN)
        rc = main(["fmt", str(path)])
        captured = capsys.readouterr()
        assert rc == EXIT_INVALID_PLAN
        assert "refusing to format" in captured.err
        assert path.read_text() == PHASELESS_PLAN  # byte-preserved


class TestDone:
    def test_marks_task_done_and_emits_settlement(
        self, strict_plan_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["done", str(strict_plan_path), "T-000001"])
        captured = capsys.readouterr()
        assert rc == EXIT_OK
        settlements = json.loads(captured.out)
        assert isinstance(settlements, list)
        assert len(settlements) == 1
        assert settlements[0]["task_id"] == "T-000001"
        assert settlements[0]["kind"] == "commit_landed"
        assert "[x] T-000001" in strict_plan_path.read_text()

    def test_marks_mcloop_canonical_non_constructed_plan_done(
        self,
        mcloop_canonical_non_constructed_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(
            [
                "done",
                str(mcloop_canonical_non_constructed_path),
                "T-000001",
            ]
        )
        captured = capsys.readouterr()
        assert rc == EXIT_OK
        settlements = json.loads(captured.out)
        assert settlements[0]["task_id"] == "T-000001"
        text = mcloop_canonical_non_constructed_path.read_text()
        assert "[x] T-000001" in text
        assert "Second paragraph." in text
        # The runtime preflight migrates the legacy plan to constructed
        # form (magic line + ids + ordinals) on first touch before the
        # mutation, so the saved file now carries the magic line.
        assert "<!-- bob-plan-format: 1 -->" in text

    def test_unknown_task_exits_two(
        self, strict_plan_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(["done", str(strict_plan_path), "T-999999"])
        captured = capsys.readouterr()
        assert rc == EXIT_TASK_NOT_FOUND
        assert captured.err


class TestFail:
    def test_marks_task_failed_with_reason(
        self, strict_plan_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(
            [
                "fail",
                str(strict_plan_path),
                "T-000002",
                "--reason",
                "tests failed",
            ]
        )
        captured = capsys.readouterr()
        assert rc == EXIT_OK
        settlements = json.loads(captured.out)
        assert len(settlements) == 1
        assert settlements[0]["task_id"] == "T-000002"
        assert settlements[0]["kind"] == "test_failed"
        assert settlements[0]["summary"] == "tests failed"
        assert "[!] T-000002" in strict_plan_path.read_text()

    def test_marks_mcloop_canonical_non_constructed_plan_failed(
        self,
        mcloop_canonical_non_constructed_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        rc = main(
            [
                "fail",
                str(mcloop_canonical_non_constructed_path),
                "T-000002",
                "--reason",
                "tests failed",
            ]
        )
        captured = capsys.readouterr()
        assert rc == EXIT_OK
        settlements = json.loads(captured.out)
        assert settlements[0]["task_id"] == "T-000002"
        assert settlements[0]["kind"] == "test_failed"
        text = mcloop_canonical_non_constructed_path.read_text()
        assert "[!] T-000002" in text
        assert "Second paragraph." in text
        # Runtime preflight migrated the legacy plan to constructed form
        # on first touch, so the magic line is now present.
        assert "<!-- bob-plan-format: 1 -->" in text

    def test_unknown_task_exits_two(
        self, strict_plan_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        rc = main(
            [
                "fail",
                str(strict_plan_path),
                "T-999999",
                "--reason",
                "missing",
            ]
        )
        captured = capsys.readouterr()
        assert rc == EXIT_TASK_NOT_FOUND
        assert captured.err
