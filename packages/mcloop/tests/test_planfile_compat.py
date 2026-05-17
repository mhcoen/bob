"""Operation-level parity tests for the additive planfile compatibility shim."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from bob_tools.planfile import load, migrate, save

from mcloop import _planfile_compat as shim
from mcloop import checklist

ROOT = Path(__file__).resolve().parents[1]
REAL_PLANS = (ROOT / "PLAN.md", ROOT / "PLAN.EXAMPLE.md")


def _flatten(tasks):
    result = []

    def visit(task_list):
        for task in task_list:
            result.append(task)
            visit(task.children)

    visit(tasks)
    return result


def _signature(task):
    return (
        task.text,
        task.checked,
        task.failed,
        task.line_number,
        task.indent_level,
        task.stage,
        len(task.children),
        tuple(task.eliminated),
    )


def _status_by_text(tasks):
    return [(task.text, task.checked, task.failed) for task in _flatten(tasks)]


def _copy_plan(tmp_path: Path, source: Path, name: str) -> Path:
    target = tmp_path / name
    shutil.copyfile(source, target)
    return target


def _replace_first(text: str, old: str, new: str) -> str:
    replaced = text.replace(old, new, 1)
    assert replaced != text
    return replaced


def _make_first_done_task_unchecked(path: Path) -> None:
    path.write_text(_replace_first(path.read_text(), "- [x]", "- [ ]"))


def _make_first_done_task_failed(path: Path) -> None:
    path.write_text(_replace_first(path.read_text(), "- [x]", "- [!]"))


def _save_migrated(path: Path) -> None:
    save(path, migrate(load(path)))


def test_parse_selection_counts_and_parent_shape_match_real_plans() -> None:
    for source in REAL_PLANS:
        legacy_tasks = checklist.parse(source)
        shim_tasks = shim.parse(source)

        assert [_signature(task) for task in _flatten(shim_tasks)] == [
            _signature(task) for task in _flatten(legacy_tasks)
        ]
        assert shim.find_next(shim_tasks) is None
        assert checklist.find_next(legacy_tasks) is None
        assert shim.count_unchecked(shim_tasks) == checklist.count_unchecked(legacy_tasks)
        assert shim.has_unchecked_bugs(shim_tasks) == checklist.has_unchecked_bugs(legacy_tasks)

        for legacy_task, shim_task in zip(_flatten(legacy_tasks), _flatten(shim_tasks)):
            assert (shim.find_parent(shim_tasks, shim_task) is None) == (
                checklist.find_parent(legacy_tasks, legacy_task) is None
            )


def test_classification_matches_or_is_allowed_checked_prose_divergence() -> None:
    """§2(d): shim uses leading tags; checklist still substring-matches BATCH/AUTO."""
    allowed = []
    for source in REAL_PLANS:
        for legacy_task, shim_task in zip(
            _flatten(checklist.parse(source)), _flatten(shim.parse(source))
        ):
            legacy_flags = (
                checklist.is_user_task(legacy_task),
                checklist.is_auto_task(legacy_task),
                checklist.is_batch_task(legacy_task),
            )
            shim_flags = (
                shim.is_user_task(shim_task),
                shim.is_auto_task(shim_task),
                shim.is_batch_task(shim_task),
            )
            if legacy_flags == shim_flags:
                continue
            # Current source contains checked prose mentions of [BATCH].
            # They are the deliberate §2(d) accepted-doc divergence and are
            # non-actionable in these fixtures, so scheduling remains identical.
            assert legacy_task.checked
            assert not shim_task.failed
            assert legacy_flags[:2] == shim_flags[:2]
            assert legacy_flags[2] is True and shim_flags[2] is False
            allowed.append(legacy_task.text)

    assert allowed == [
        'Add `"batch": false` config key. When false, `run_loop` ignores '
        "`[BATCH]` tags and runs all children individually",
        "Test: [BATCH] parent with multiple children runs one session, all children checked off",
        "Add `--stop-after-one` CLI flag. When set, mcloop runs exactly one "
        "checkable leaf task and then exits. If the next task is part of a "
        "`[BATCH]` parent, the batching logic must be bypassed for that "
        "single task: run only the one task in its own session, commit it "
        "normally, then exit. Do not run the rest of the batch",
    ]


def test_check_off_matches_checklist_status_on_migrated_real_plan_copy(tmp_path: Path) -> None:
    legacy_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "legacy-checkoff.md")
    shim_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "shim-checkoff.md")
    _make_first_done_task_unchecked(legacy_path)
    _make_first_done_task_unchecked(shim_path)
    _save_migrated(shim_path)

    legacy_task = checklist.find_next(checklist.parse(legacy_path))
    shim_task = shim.find_next(shim.parse(shim_path))
    assert legacy_task is not None
    assert shim_task is not None
    assert legacy_task.text == shim_task.text

    checklist.check_off(legacy_path, legacy_task)
    shim.check_off(shim_path, shim_task)

    assert _status_by_text(shim.parse(shim_path)) == _status_by_text(checklist.parse(legacy_path))


def test_mark_failed_matches_checklist_status_on_migrated_real_plan_copy(
    tmp_path: Path,
) -> None:
    legacy_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "legacy-fail.md")
    shim_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "shim-fail.md")
    _make_first_done_task_unchecked(legacy_path)
    _make_first_done_task_unchecked(shim_path)
    _save_migrated(shim_path)

    legacy_task = checklist.find_next(checklist.parse(legacy_path))
    shim_task = shim.find_next(shim.parse(shim_path))
    assert legacy_task is not None
    assert shim_task is not None
    assert legacy_task.text == shim_task.text

    checklist.mark_failed(legacy_path, legacy_task)
    shim.mark_failed(shim_path, shim_task)

    assert _status_by_text(shim.parse(shim_path)) == _status_by_text(checklist.parse(legacy_path))


def test_clear_failed_markers_matches_checklist_on_real_plan_copy(tmp_path: Path) -> None:
    legacy_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "legacy-clear.md")
    shim_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "shim-clear.md")
    _make_first_done_task_failed(legacy_path)
    _make_first_done_task_failed(shim_path)

    assert checklist.clear_failed_markers(legacy_path) == 1
    assert shim.clear_failed_markers(shim_path) == 1

    assert _status_by_text(shim.parse(shim_path)) == _status_by_text(checklist.parse(legacy_path))


def test_batch_children_match_on_real_batch_block(tmp_path: Path) -> None:
    legacy_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "legacy-batch.md")
    shim_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "shim-batch.md")
    for path in (legacy_path, shim_path):
        text = path.read_text()
        text = text.replace(
            "- [x] [BATCH] Add reviewer module",
            "- [ ] [BATCH] Add reviewer module",
            1,
        )
        text = text.replace(
            "   - [x] Create `ReviewFinding`",
            "   - [ ] Create `ReviewFinding`",
            1,
        )
        text = text.replace(
            "   - [x] Create `ReviewRequest`",
            "   - [ ] Create `ReviewRequest`",
            1,
        )
        path.write_text(text)

    legacy_parent = next(
        task for task in _flatten(checklist.parse(legacy_path)) if checklist.is_batch_task(task)
    )
    shim_parent = next(
        task for task in _flatten(shim.parse(shim_path)) if shim.is_batch_task(task)
    )

    assert [task.text for task in shim.get_batch_children(shim_parent)] == [
        task.text for task in checklist.get_batch_children(legacy_parent)
    ]
    assert (
        shim.find_next(shim.parse(shim_path)).text
        == checklist.find_next(checklist.parse(legacy_path)).text
    )


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


def test_purge_completed_bugs_removes_done_bug_entries_atomically(tmp_path: Path) -> None:
    path = tmp_path / "BUGS.md"
    path.write_text("## Bugs\n\n- [x] Fixed crash\n- [ ] Open crash\n")
    shim.purge_completed_bugs(path)
    text = path.read_text()
    assert "Fixed crash" not in text
    assert "Open crash" in text


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


def test_no_unexpected_runtime_import_of_planfile_compat() -> None:
    for path in (ROOT / "mcloop").glob("*.py"):
        if path.name == "_planfile_compat.py":
            continue
        assert "_planfile_compat" not in path.read_text()


def test_no_unchecked_prose_mention_classification_divergence() -> None:
    """Deliberate §2(d) divergence is currently checked-only in real fixtures."""
    prose_tag = re.compile(r"\[(?:BATCH|AUTO:[^\]]+)\]")
    for source in REAL_PLANS:
        for legacy_task, shim_task in zip(
            _flatten(checklist.parse(source)), _flatten(shim.parse(source))
        ):
            if prose_tag.search(legacy_task.text) and (
                checklist.is_batch_task(legacy_task) != shim.is_batch_task(shim_task)
                or checklist.is_auto_task(legacy_task) != shim.is_auto_task(shim_task)
            ):
                assert legacy_task.checked
