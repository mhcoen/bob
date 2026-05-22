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


def test_classification_matches_checklist_on_real_plans() -> None:
    """Shim classification must match checklist on every real-plan task.

    The shim's primary classifier is the typed planfile field
    (``flag_tags`` / ``action_tag``); the secondary fallback is
    checklist's text-substring rule, applied when the typed field is
    absent. Together those two paths reproduce checklist's behavior
    exactly — including on prose-mention BATCH tasks in real plans
    that don't carry a typed flag.

    The §2(d) DONE prose-mention concern (scheduler picking up a
    spurious BATCH parent) is not reintroduced because all such tasks
    are guaranteed ``[x]`` per the freeze invariant; the scheduler
    skips checked tasks regardless of their classification.
    """
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
            assert legacy_flags == shim_flags, (
                f"classification divergence on {legacy_task.text!r}: "
                f"checklist={legacy_flags} shim={shim_flags}"
            )


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


def _uncheck_first(text: str, task_body: str) -> str:
    """Flip the first ``- [x] <task_body>`` line to ``- [ ] <task_body>``.

    Tolerates an optional ``T-NNNNNN: `` prefix between the checkbox and
    the task body so the substitution survives B1 canonicalization
    (which adds canonical task ids to every task line in the real
    PLAN.md). Without this tolerance, ``str.replace`` against the
    pre-canonical literal silently no-ops on the post-canonical file
    and the downstream parser sees nothing unchecked.
    """
    pattern = re.compile(
        r"^(?P<indent>\s*)- \[x\] (?P<id>T-\d{6}: )?" + re.escape(task_body),
        re.MULTILINE,
    )
    return pattern.sub(r"\g<indent>- [ ] \g<id>" + task_body, text, count=1)


def test_batch_children_match_on_real_batch_block(tmp_path: Path) -> None:
    legacy_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "legacy-batch.md")
    shim_path = _copy_plan(tmp_path, ROOT / "PLAN.md", "shim-batch.md")
    for path in (legacy_path, shim_path):
        text = path.read_text()
        text = _uncheck_first(text, "[BATCH] Add reviewer module")
        text = _uncheck_first(text, "Create `ReviewFinding`")
        text = _uncheck_first(text, "Create `ReviewRequest`")
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


def test_classifier_text_fallback_matches_checklist_on_hand_built_tasks() -> None:
    """Hand-built ``Task`` objects (bypassing parse_plan) must classify
    identically to checklist's substring rules. Test fixtures across
    the mcloop suite build ``Task(text, checked, failed, depth,
    lineno)`` directly and rely on text-based classification — the
    shim must preserve that for behavior-preservation at cutover.
    """
    auto_task = checklist.Task("[AUTO:run_cli] python -m pytest", True, False, 0, 0)
    user_task = checklist.Task("[USER] Inspect output", False, False, 0, 0)
    batch_task = checklist.Task("[BATCH] Add reviewer", False, False, 0, 0)
    plain_task = checklist.Task("Just a task", False, False, 0, 0)

    # The shim must classify hand-built (typed-field-unset) Tasks the
    # same way checklist does — checklist matches on text substring,
    # so the shim's text-fallback must agree.
    assert shim.is_auto_task(auto_task) == checklist.is_auto_task(auto_task)
    assert shim.is_user_task(user_task) == checklist.is_user_task(user_task)
    assert shim.is_batch_task(batch_task) == checklist.is_batch_task(batch_task)
    assert shim.is_auto_task(plain_task) == checklist.is_auto_task(plain_task)
    assert shim.is_user_task(plain_task) == checklist.is_user_task(plain_task)
    assert shim.is_batch_task(plain_task) == checklist.is_batch_task(plain_task)

    # All three positive cases must classify as the expected kind.
    assert shim.is_auto_task(auto_task)
    assert shim.is_user_task(user_task)
    assert shim.is_batch_task(batch_task)


def test_parse_description_extracts_prose_before_first_checkbox_matches_checklist(
    tmp_path: Path,
) -> None:
    """The shim must reproduce checklist.parse_description exactly so the
    runtime's project-blurb extraction (mcloop/main.py:850) survives D1's
    deletion of mcloop/checklist.py.
    """
    md = (
        "# My Project\n\n"
        "Build a REST API for managing widgets.\n"
        "Use Flask and SQLite.\n\n"
        "- [ ] Set up project structure\n"
        "- [ ] Add widget CRUD endpoints\n"
    )
    path = tmp_path / "tasks.md"
    path.write_text(md)
    assert shim.parse_description(path) == checklist.parse_description(path)


def test_parse_description_empty_when_first_line_is_checkbox(tmp_path: Path) -> None:
    path = tmp_path / "tasks.md"
    path.write_text("- [ ] First task\n- [ ] Second task\n")
    assert shim.parse_description(path) == ""
    assert shim.parse_description(path) == checklist.parse_description(path)


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
