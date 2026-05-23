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

from pathlib import Path

from mcloop import _planfile_compat as shim


def test_auto_user_helpers_are_planfile_tag_backed(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text(
        "# Demo\n\n"
        "## Stage 1: Core\n\n"
        "- [ ] [USER] Inspect app\n"
        "  Keep this line.\n"
        "- [ ] [AUTO:run_cli] ./verify.sh --fast\n"
    )
    tasks = shim.parse(path)

    user, auto = tasks[0], tasks[1]
    assert shim.is_user_task(user)
    assert shim.user_task_instructions(user) == "Inspect app\n  Keep this line."
    assert shim.is_auto_task(auto)
    assert shim.parse_auto_task(auto) == ("run_cli", "./verify.sh --fast")


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


def test_mutation_requires_migrated_task_ids(tmp_path: Path) -> None:
    path = tmp_path / "PLAN.md"
    path.write_text("# Demo\n\n## Stage 1: Core\n\n- [ ] No id yet\n")
    task = shim.find_next(shim.parse(path))
    assert task is not None
    assert task.task_id is None
    try:
        shim.check_off(path, task)
    except ValueError as exc:
        assert "requires migrated PLAN.md task ids" in str(exc)
    else:
        raise AssertionError("check_off accepted an ID-less task")
