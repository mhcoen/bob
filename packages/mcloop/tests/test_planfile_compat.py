"""Shim-only regression tests for ``mcloop._planfile_compat``.

Originally this module's job was parity coverage — locking the shim's
behavior to ``mcloop.checklist`` across the de-split cutover. Once D1
deletes ``mcloop/checklist.py``, the parity comparison machinery
retires with it. What remains in this file is the four shim-only
behavioral tests: properties of the shim's own contract (flag-tag
backing, purge atomicity, ID requirement on mutations, the
parse-description checkbox-first edge case) that are not asserted
anywhere else in the test surface — including ``bob-tools``'s
planfile parity suite, which tests planfile-level behavior rather
than the mcloop wrapper.
"""

from __future__ import annotations

import dataclasses
import shutil
from pathlib import Path

import pytest
from bob_tools.planfile.model import PlanSyntaxError
from plan_fixtures import canonical_plan_text

from mcloop import _planfile_compat as shim


def test_auto_user_helpers_are_planfile_tag_backed(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(
        "# Demo\n\n"
        "## Stage 1: Core\n\n"
        "- [ ] [USER] Inspect app\n"
        "  Keep this line.\n"
        "- [ ] [AUTO:run_cli] Smoke test the verifier by running `./verify.sh --fast`\n"
    )
    tasks = shim.parse(path)

    user, auto = tasks[0], tasks[1]
    assert shim.is_user_task(user)
    assert shim.user_task_instructions(user) == "Inspect app\n  Keep this line."
    assert shim.is_auto_task(auto)
    assert shim.parse_auto_task(auto) == ("run_cli", "./verify.sh --fast")


def test_parse_preserves_structured_annotations() -> None:
    scratch = Path(".scratch/tests/planfile-compat-annotations")
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    path = scratch / "PLAN.md"
    path.write_text(
        "# Demo\n\n"
        "## Stage 1: Core\n\n"
        "- [ ] T-000001: Run scoped proof [accept: command-exit: true]\n"
    )

    task = shim.parse(path)[0]

    assert task.annotations == (("accept", "command-exit: true"),)


def test_parse_auto_run_cli_extracts_only_backtick_command(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(
        "# Demo\n\n"
        "## Stage 1: Core\n\n"
        "- [ ] [AUTO:run_cli] Verify the build still works by running `make build`\n"
    )
    auto = shim.parse(path)[0]
    # Only the backtick-quoted command runs, never the surrounding prose.
    assert shim.parse_auto_task(auto) == ("run_cli", "make build")


def test_parse_auto_run_cli_without_backticks_returns_error(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(
        "# Demo\n\n## Stage 1: Core\n\n- [ ] [AUTO:run_cli] Make sure the app launches cleanly\n"
    )
    auto = shim.parse(path)[0]
    action, message = shim.parse_auto_task(auto)
    # No command to run: fail clearly instead of shelling out the prose.
    assert action == "error"
    assert "no backtick-delimited command" in message


def test_parse_auto_run_cli_bare_single_token_command_runs_as_is(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text("# Demo\n\n## Stage 1: Core\n\n- [ ] [AUTO:run_cli] pytest\n")
    auto = shim.parse(path)[0]
    # A bare command with no backticks and no surrounding prose runs verbatim.
    assert shim.parse_auto_task(auto) == ("run_cli", "pytest")


def test_parse_auto_run_cli_bare_path_command_runs_as_is(tmp_path: Path) -> None:
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\n")
    path = tmp_path / "PLAN.md"
    path.write_text(f"# Demo\n\n## Stage 1: Core\n\n- [ ] [AUTO:run_cli] {script} --fast\n")
    auto = shim.parse(path)[0]
    # First token resolves to an existing script path, so the whole args runs.
    assert shim.parse_auto_task(auto) == ("run_cli", f"{script} --fast")


def test_parse_auto_run_cli_multiple_backticks_returns_error(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(
        "# Demo\n\n"
        "## Stage 1: Core\n\n"
        "- [ ] [AUTO:run_cli] Run `make build` and then `make test`\n"
    )
    auto = shim.parse(path)[0]
    action, message = shim.parse_auto_task(auto)
    assert action == "error"
    assert "multiple backtick-delimited commands" in message


def test_parse_auto_non_run_cli_action_is_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text("# Demo\n\n## Stage 1: Core\n\n- [ ] [AUTO:run_gui] open -a Foo | Foo\n")
    auto = shim.parse(path)[0]
    # Backtick extraction must not touch other automated actions.
    assert shim.parse_auto_task(auto) == ("run_gui", "open -a Foo | Foo")


def test_run_cli_all_three_arg_shapes_regression(tmp_path: Path) -> None:
    """Regression: ``parse_auto_task`` handles all three run_cli arg shapes.

    (1) prose with a backtick-quoted command runs exactly that command;
    (2) a bare path/command with no backticks runs as-is;
    (3) prose with no extractable command errors with a clear message.

    The per-shape tests above each assert one case in isolation; this locks
    all three together so a future change cannot fix one shape by breaking
    another (the exact failure mode that produced T-000003/T-000004).
    """
    script = tmp_path / "verify.sh"
    script.write_text("#!/bin/sh\n")
    path = tmp_path / "PLAN.md"
    path.write_text(
        "# Demo\n\n"
        "## Stage 1: Core\n\n"
        "- [ ] [AUTO:run_cli] Run `make build` to confirm the build\n"
        "- [ ] [AUTO:run_cli] pytest\n"
        f"- [ ] [AUTO:run_cli] {script} --fast\n"
        "- [ ] [AUTO:run_cli] Make sure the app launches cleanly\n"
    )
    backtick, bare_token, bare_path, prose = shim.parse(path)

    # (1) Prose with a backtick-quoted command: only that command runs.
    assert shim.parse_auto_task(backtick) == ("run_cli", "make build")
    # (2) Bare command/path with no backticks: runs verbatim.
    assert shim.parse_auto_task(bare_token) == ("run_cli", "pytest")
    assert shim.parse_auto_task(bare_path) == ("run_cli", f"{script} --fast")
    # (3) Prose with no extractable command: clear error, never shell the prose.
    action, message = shim.parse_auto_task(prose)
    assert action == "error"
    assert "no backtick-delimited command" in message


def test_parse_description_empty_when_first_line_is_checkbox(tmp_path: Path) -> None:
    path = tmp_path / "tasks.md"
    path.write_text("- [ ] First task\n- [ ] Second task\n")
    assert shim.parse_description(path) == ""


def test_purge_completed_bugs_removes_done_bug_entries_atomically(tmp_path: Path) -> None:
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [x] Fixed crash\n- [ ] Open crash\n")
    shim.purge_completed_bugs(path)
    text = path.read_text()
    assert "Fixed crash" not in text
    assert "Open crash" in text
    resolved = tmp_path / "BUGS-resolved.md"
    assert resolved.exists()
    resolved_text = resolved.read_text()
    assert "- [x] Fixed crash" in resolved_text
    assert "Open crash" not in resolved_text


def test_purge_completed_bugs_appends_verbatim_to_resolved_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [x] First fixed\n- [ ] Still open\n- [x] Second fixed\n")
    shim.purge_completed_bugs(path)
    resolved = tmp_path / "BUGS-resolved.md"
    resolved_text = resolved.read_text()
    assert resolved_text.startswith("## Resolved Bugs\n\n")
    assert "- [x] First fixed\n" in resolved_text
    assert "- [x] Second fixed\n" in resolved_text
    assert "Still open" not in resolved_text

    path.write_text("## Bugs\n\n- [x] Third fixed\n- [ ] Still open\n")
    shim.purge_completed_bugs(path)
    resolved_text_2 = resolved.read_text()
    assert resolved_text_2.startswith(resolved_text)
    assert "- [x] Third fixed\n" in resolved_text_2


def test_purge_completed_bugs_no_done_entries_does_not_create_resolved_file(
    tmp_path: Path,
) -> None:
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [ ] Still open\n")
    shim.purge_completed_bugs(path)
    assert not (tmp_path / "BUGS-resolved.md").exists()


def test_check_off_completes_idless_task_by_source_position(tmp_path: Path) -> None:
    """Id-less tasks are completed positionally, mirroring mark_failed /
    reset_task. (This replaces the retired contract that check_off rejects
    id-less tasks — it was the last mutator without the positional branch,
    which crashed bug-only mode completing a loose BUGS.md entry.)"""
    path = tmp_path / "PLAN.md"
    path.write_text("# Demo\n\n## Stage 1: Core\n\n- [ ] No id yet\n")
    task = shim.find_next(shim.parse(path))
    assert task is not None
    assert task.task_id is None

    shim.check_off(path, task)

    assert path.read_text() == "# Demo\n\n## Stage 1: Core\n\n- [x] No id yet\n"


def test_mark_failed_marks_idless_bugs_task_by_source_position(tmp_path: Path) -> None:
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [ ] Fix crash in loose bug queue\n")
    task = shim.find_next(shim.parse(path))

    assert task is not None
    assert task.task_id is None

    shim.mark_failed(path, task)

    assert path.read_text() == "## Bugs\n\n- [!] Fix crash in loose bug queue\n"


def test_reset_task_flips_failed_task_back_to_pending_by_id(tmp_path: Path) -> None:
    """A [!]-failed migrated task is reset to [ ] and becomes selectable again."""
    path = tmp_path / "PLAN.md"
    path.write_text(
        canonical_plan_text("## Stage 1: Core\n\n- [ ] First task\n- [ ] Second task\n")
    )
    first = shim.parse(path)[0]
    assert first.task_id is not None
    shim.mark_failed(path, first)

    failed = next(t for t in shim.parse(path) if t.failed)
    # A failed task is skipped by the scheduler, so the next task is the other one.
    assert shim.find_next(shim.parse(path)).text == "Second task"

    shim.reset_task(path, failed)

    reset = shim.parse(path)[0]
    assert not reset.failed
    assert not reset.checked
    # Now runnable again: it is the first actionable task once more.
    assert shim.find_next(shim.parse(path)).text == "First task"


def test_reset_task_resets_idless_bugs_task_by_source_position(tmp_path: Path) -> None:
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [!] Fix crash in loose bug queue\n")
    failed = next(t for t in shim.parse(path) if t.failed)
    assert failed.task_id is None

    shim.reset_task(path, failed)

    assert path.read_text() == "## Bugs\n\n- [ ] Fix crash in loose bug queue\n"


def test_reset_task_is_idempotent_on_pending_task(tmp_path: Path) -> None:
    """Resetting a task that is already pending is a no-op, not an error."""
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [ ] Already pending\n")
    task = shim.find_next(shim.parse(path))
    assert task is not None

    shim.reset_task(path, task)

    assert path.read_text() == "## Bugs\n\n- [ ] Already pending\n"


def test_check_off_completes_idless_bugs_task_by_source_position(tmp_path: Path) -> None:
    path = tmp_path / "BUGS.md"
    path.write_text(
        "## Bugs\n\n- [ ] Fix crash in loose bug queue\n- [ ] Another open bug\n"
    )
    task = shim.find_next(shim.parse(path))
    assert task is not None
    assert task.task_id is None

    shim.check_off(path, task)

    assert path.read_text() == (
        "## Bugs\n\n- [x] Fix crash in loose bug queue\n- [ ] Another open bug\n"
    )


def test_check_off_is_idempotent_on_completed_idless_task(tmp_path: Path) -> None:
    """Completing an already-[x] id-less task is a no-op, not an error."""
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [ ] Fix crash in loose bug queue\n")
    task = shim.find_next(shim.parse(path))
    assert task is not None
    shim.check_off(path, task)
    after_first = path.read_text()

    shim.check_off(path, task)

    assert path.read_text() == after_first


def test_check_off_raises_when_idless_task_not_locatable(tmp_path: Path) -> None:
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [ ] Fix crash in loose bug queue\n")
    task = shim.find_next(shim.parse(path))
    assert task is not None
    ghost = dataclasses.replace(task, text="text that matches no checkbox line")

    with pytest.raises(ValueError, match="could not locate id-less task"):
        shim.check_off(path, ghost)


def test_check_off_by_id_still_routes_through_planfile(tmp_path: Path) -> None:
    """A task WITH an id keeps the migrated planfile.complete_task path."""
    path = tmp_path / "PLAN.md"
    path.write_text(canonical_plan_text("## Stage 1: Core\n\n- [ ] First task\n"))
    task = shim.parse(path)[0]
    assert task.task_id is not None

    shim.check_off(path, task)

    done = shim.parse(path)[0]
    assert done.checked
    assert not done.failed


def test_check_off_then_purge_archives_idless_magic_lined_bug(tmp_path: Path) -> None:
    """End-to-end run_loop:2747 regression: bug-only mode completes the
    id-less bug entry (still carrying a stray magic line) positionally, then
    the end-of-run purge archives it and leaves BUGS.md clean."""
    path = tmp_path / "BUGS.md"
    path.write_text(
        "<!-- bob-plan-format: 1 -->\n"
        "\n"
        "## Bugs\n"
        "- [ ] Fix issue reported during task 11.8 (see observation below):\n"
        "```\n"
        "extract a corpus of 1 file containing two sentences of 10 and 20 words\n"
        "```\n"
    )
    task = shim.find_next(shim.parse(path))
    assert task is not None
    assert task.task_id is None

    shim.check_off(path, task)  # crashed at run_loop:2747 before the id-less branch

    assert "- [x] Fix issue reported during task 11.8" in path.read_text()

    shim.purge_completed_bugs(path)

    remaining = path.read_text()
    assert "Fix issue reported during task 11.8" not in remaining
    assert "<!-- bob-plan-format:" not in remaining
    resolved = (tmp_path / shim.RESOLVED_BUGS_FILENAME).read_text()
    assert "Fix issue reported during task 11.8" in resolved


def test_reset_task_leaves_other_failed_markers_intact(tmp_path: Path) -> None:
    """Resetting one failed task does not touch a sibling's [!] marker."""
    path = tmp_path / "PLAN.md"
    path.write_text(
        canonical_plan_text("## Stage 1: Core\n\n- [ ] First task\n- [ ] Second task\n")
    )
    tasks = shim.parse(path)
    shim.mark_failed(path, tasks[0])
    shim.mark_failed(path, tasks[1])

    first_failed = next(t for t in shim.parse(path) if t.failed and t.text == "First task")
    shim.reset_task(path, first_failed)

    after = {t.text: t for t in shim.parse(path)}
    assert not after["First task"].failed
    assert after["Second task"].failed


def test_reset_makes_previously_failed_task_runnable_again(tmp_path: Path) -> None:
    """Regression: a [!]-failed task the scheduler skips becomes selectable
    again once reset to pending.

    A failed task is a hard stop for ``find_next`` — it is permanently
    skipped, so the loop would never retry it even after its blocking
    condition (a missing mapped test, an absent waiver, a stale baseline)
    is cleared. Resetting it must restore runnability: it is the task
    ``find_next`` selects once more. Covers the id-less loose BUGS.md path,
    where runnability (not just the rewritten marker) is the contract.
    """
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [!] Fix the blocked bug\n- [ ] Later bug\n")

    # While failed, the scheduler skips it and selects the next pending task.
    failed = next(t for t in shim.parse(path) if t.failed)
    assert failed.task_id is None
    assert shim.find_next(shim.parse(path)).text == "Later bug"

    shim.reset_task(path, failed)

    # Now pending again and first in line: runnable, not permanently skipped.
    selected = shim.find_next(shim.parse(path))
    assert selected.text == "Fix the blocked bug"
    assert not selected.failed
    assert not selected.checked


def test_parse_bugs_md_tolerates_idless_entry_with_magic_line(tmp_path: Path) -> None:
    """Regression: a magic-lined BUGS.md (loose queue) with an id-less bug
    entry plus a fenced block — exactly what the bug-filer wrote in the 11.8
    crash — must parse without raising. The entry surfaces as an unchecked
    ``task_id=None`` Task so the run-summary/scheduler count paths work."""
    path = tmp_path / "BUGS.md"
    path.write_text(
        "<!-- bob-plan-format: 1 -->\n"
        "\n"
        "## Bugs\n"
        "- [ ] Fix issue reported during task 11.8 (see observation below):\n"
        "```\n"
        "extract a corpus of 1 file containing two sentences of 10 and 20 words\n"
        "```\n"
    )
    tasks = shim.parse(path)
    unchecked = [t for t in tasks if not t.checked]
    assert len(unchecked) == 1
    assert unchecked[0].task_id is None
    assert (
        unchecked[0].text
        == "Fix issue reported during task 11.8 (see observation below):"
    )


def test_parse_plan_md_stays_strict_on_idless_entry(tmp_path: Path) -> None:
    """The BUGS.md tolerance must NOT weaken PLAN.md: a magic-lined PLAN.md
    with an id-less checkbox still raises (the magic line is only stripped for
    the BUGS.md bug-queue read path, keyed on the filename)."""
    path = tmp_path / "PLAN.md"
    path.write_text(
        "<!-- bob-plan-format: 1 -->\n# P\n## Stage 1: Core\n- [ ] no id here\n"
    )
    with pytest.raises(PlanSyntaxError):
        shim.parse(path)


def test_purge_completed_bugs_strips_magic_line_from_idless_queue(
    tmp_path: Path,
) -> None:
    """Change 2 root fix: purging a magic-lined id-less BUGS.md must NOT raise
    and must rewrite the file WITHOUT the magic line, so subsequent strict
    readers (the direct parse_plan callers) become moot."""
    path = tmp_path / "BUGS.md"
    path.write_text(
        "<!-- bob-plan-format: 1 -->\n"
        "\n"
        "## Bugs\n"
        "- [ ] Fix issue reported during task 11.8 (see observation below):\n"
        "```\n"
        "extract a corpus of 1 file containing two sentences of 10 and 20 words\n"
        "```\n"
    )
    shim.purge_completed_bugs(path)  # must not raise
    text = path.read_text()
    assert "<!-- bob-plan-format:" not in text  # magic line dropped
    # The unresolved (TODO) bug entry is preserved.
    assert "Fix issue reported during task 11.8" in text
    # And the rewritten file now parses cleanly as a loose queue.
    tasks = shim.parse(path)
    assert any(not t.checked and t.task_id is None for t in tasks)
