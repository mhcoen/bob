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

import io
import json
import re
from pathlib import Path

import pytest

from bob_tools.planfile.cli import (
    EXIT_INVALID_PLAN,
    EXIT_OK,
    EXIT_OTHER,
    EXIT_TASK_NOT_FOUND,
    _print_parse_error,
    main,
)
from bob_tools.planfile.model import PlanSyntaxError

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

MARKER_BEARING_MIXED_IDS_PLAN = """<!-- bob-plan-format: 1 -->

# Marker Fixture

## Stage 1: Bootstrap
<!-- phase_id: phase_001 -->

- [ ] T-000005: already has an id
- [ ] bare todo
- [x] bare done task
"""

PHASELESS_PLAN = """# Phaseless Fixture

- [ ] stray task
"""

# A completed task whose tail carries a fenced code block. The parser
# captures those four fence/output lines (plus the trailing blank) as
# the task's ``trailing_lines`` (lossless retention). ``fmt`` used to
# crash here: it re-rendered through the constructed-mode validator,
# which rejects any ``trailing_lines`` as a construction-API violation,
# so a plan using the documented trailing-line capture could not be
# fmt'd (BUGS.md T-000010).
TRAILING_BLOCK_PLAN = """<!-- bob-plan-format: 1 -->

# Trailing Block Fixture

## Stage 1: Bootstrap
<!-- phase_id: phase_001 -->

- [x] T-000001: ran the linter
  ```
  ruff output
  here
  ```

- [ ] T-000002: next task
"""

# The exact fenced block the parser captured as trailing lines; ``fmt``
# must preserve it byte-for-byte.
TRAILING_BLOCK_FENCE = "  ```\n  ruff output\n  here\n  ```\n"

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

    def test_migrates_bare_checkboxes_beside_id_bearing_ones_under_marker(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """fmt assigns ids to bare checkboxes regardless of the marker.

        A magic-lined file may legitimately carry a mix of already-id'd
        and still-bare checkboxes (a partially migrated plan). fmt must
        migrate the bare ones — assigning ids is what fmt is for —
        rather than take the strict-validate path the marker would
        otherwise force, which rejected bare checkboxes with "expected
        task id after checkbox marker". This pins the migrate-vs-validate
        trigger in ``cmd_fmt`` at the partial-migration boundary the
        single-scenario test above does not cover.
        """
        path = tmp_path / "PLAN.md"
        path.write_text(MARKER_BEARING_MIXED_IDS_PLAN)
        rc = main(["fmt", str(path)])
        captured = capsys.readouterr()
        assert rc == EXIT_OK
        # The failure signature from the bug report must never appear.
        assert "expected task id" not in captured.err
        text = path.read_text()
        # Existing id is preserved; the two bare checkboxes get fresh
        # ids continuing past the max in use, not an error.
        assert "T-000005: already has an id" in text
        assert "T-000006: bare todo" in text
        assert "T-000007: bare done task" in text

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

    def test_preserves_task_trailing_code_block(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """fmt on a plan whose completed task carries a trailing fenced
        code block succeeds and preserves the block byte-for-byte.

        Regression for BUGS.md T-000010: fmt re-rendered through the
        constructed-mode validator, which rejects the ``trailing_lines``
        the parser legitimately captured from disk, so any plan using the
        documented lossless trailing-line capture crashed with
        "trailing_lines must be empty on constructed tasks".
        """
        path = tmp_path / "PLAN.md"
        path.write_text(TRAILING_BLOCK_PLAN)
        rc = main(["fmt", str(path)])
        captured = capsys.readouterr()
        assert rc == EXIT_OK
        assert "trailing_lines" not in captured.err
        text = path.read_text()
        # The captured fenced block survives verbatim.
        assert TRAILING_BLOCK_FENCE in text
        # ids were still assigned to the surrounding tasks.
        assert "T-000001: ran the linter" in text
        assert "T-000002: next task" in text
        # fmt stays idempotent on the trailing-line-bearing file.
        rc2 = main(["fmt", str(path)])
        assert rc2 == EXIT_OK
        assert path.read_text() == text


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


class TestErrorFilename:
    """Parse-error diagnostics must name the file actually being processed.

    Regression for the ``bob-plan fmt BUGS.md`` bug: the error read
    "PLAN.md invalid at line N" regardless of the input argument,
    because a path-less :class:`PlanSyntaxError` falls back to the
    hardcoded "PLAN.md" in ``__str__``. The CLI now binds the real path
    before formatting, so the reported filename always matches the input.
    """

    @pytest.mark.parametrize("command", ["validate", "next", "fmt"])
    def test_reported_path_matches_input_argument(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
    ) -> None:
        path = tmp_path / "BUGS.md"
        path.write_text(INVALID_PLAN)
        rc = main([command, str(path)])
        captured = capsys.readouterr()
        assert rc == EXIT_INVALID_PLAN
        assert "BUGS.md invalid" in captured.err
        assert "PLAN.md" not in captured.err

    def test_binds_cli_path_onto_pathless_error(self) -> None:
        # A PlanSyntaxError from a nested re-parse can arrive with
        # ``path is None``; the CLI must stamp the file it was handed
        # rather than let ``__str__`` fall back to "PLAN.md".
        exc = PlanSyntaxError("boom", 127, 1, None)
        buf = io.StringIO()
        _print_parse_error(exc, buf, Path("BUGS.md"))
        assert "BUGS.md invalid at line 127" in buf.getvalue()
        assert "PLAN.md" not in buf.getvalue()

    def test_does_not_override_existing_path(self) -> None:
        # When the exception already carries a path, the CLI must not
        # clobber it with the argument passed for the None case.
        exc = PlanSyntaxError("boom", 5, 1, Path("SOURCE.md"))
        buf = io.StringIO()
        _print_parse_error(exc, buf, Path("BUGS.md"))
        assert "SOURCE.md invalid at line 5" in buf.getvalue()
        assert "BUGS.md" not in buf.getvalue()


class TestTrailingContentSurvivesRuntime:
    """done/fail must not destroy content fmt preserves.

    Regression: the runtime preflight rejected ``trailing_lines`` and
    destructively migrated any fmt-canonical file carrying them, so the
    very next ``bob-plan done`` deleted the user's trailing prose or
    fenced blocks from disk.
    """

    def test_done_preserves_trailing_prose_without_migration(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        path = tmp_path / "PLAN.md"
        path.write_text(
            "# Demo\n\n## Stage 1: Core\n\n"
            "- [x] finished thing\n"
            "  Verified by running the harness.\n\n"
            "- [ ] next thing\n",
            encoding="utf-8",
        )
        assert main(["fmt", str(path)]) == EXIT_OK
        text = path.read_text()
        assert "Verified by running the harness." in text
        match = re.search(r"- \[ \] (T-\d+):", text)
        assert match is not None
        assert main(["done", str(path), match.group(1)]) == EXIT_OK
        err = capsys.readouterr().err
        assert "migrating legacy" not in err
        assert "Verified by running the harness." in path.read_text()


class TestNonUtf8Input:
    """A non-UTF-8 plan file exits with a diagnostic, never a traceback.

    Regression: every CLI read path caught OSError only, so a Latin-1
    or binary PLAN.md raised UnicodeDecodeError straight through main()
    -- an uncaught traceback where mcloop and shell scripts expect a
    clean nonzero exit.
    """

    @pytest.mark.parametrize(
        "argv",
        [
            ["validate"],
            ["fmt"],
            ["next"],
            ["done", "--", "T-000001"],
            ["fail", "--", "T-000001", "--reason", "x"],
        ],
        ids=["validate", "fmt", "next", "done", "fail"],
    )
    def test_latin1_file_is_a_diagnostic_not_a_traceback(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        argv: list[str],
    ) -> None:
        bad = tmp_path / "PLAN.md"
        bad.write_bytes("## Bugs\n- [ ] caf\xe9\n".encode("latin-1"))
        cmd, rest = argv[0], argv[1:]
        rest = [a for a in rest if a != "--"]
        code = main([cmd, str(bad), *rest])
        assert code == EXIT_OTHER
        err = capsys.readouterr().err
        assert "error reading" in err
        assert "Traceback" not in err
