"""Integration tests for checklist: real file I/O roundtrips."""

import pytest

from mcloop.checklist import check_off, find_next, mark_failed, parse


@pytest.mark.integration
def test_parse_check_off_roundtrip(tmp_path):
    """parse → find_next → check_off produces a correctly updated file."""
    md = tmp_path / "PLAN.md"
    md.write_text("- [ ] First task\n- [ ] Second task\n")

    tasks = parse(md)
    first = find_next(tasks)
    assert first is not None
    assert first.text == "First task"

    check_off(md, first)

    content = md.read_text()
    assert "- [x] First task" in content
    assert "- [ ] Second task" in content

    # Parse again: next task should now be Second task
    tasks = parse(md)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Second task"


@pytest.mark.integration
def test_check_off_all_tasks(tmp_path):
    """Checking off all tasks leaves no unchecked items."""
    md = tmp_path / "PLAN.md"
    md.write_text("- [ ] A\n- [ ] B\n- [ ] C\n")

    for _ in range(3):
        tasks = parse(md)
        task = find_next(tasks)
        assert task is not None
        check_off(md, task)

    tasks = parse(md)
    assert find_next(tasks) is None
    assert md.read_text().count("- [x]") == 3


@pytest.mark.integration
def test_mark_failed_roundtrip(tmp_path):
    """mark_failed rewrites - [ ] to - [!] on disk."""
    md = tmp_path / "PLAN.md"
    md.write_text("- [ ] Failing task\n- [ ] Other task\n")

    tasks = parse(md)
    task = find_next(tasks)
    assert task is not None

    mark_failed(md, task)

    content = md.read_text()
    assert "- [!] Failing task" in content
    assert "- [ ] Other task" in content

    # Failed task should not be returned by find_next
    tasks = parse(md)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Other task"


@pytest.mark.integration
def test_parent_auto_checked_when_children_done(tmp_path):
    """Parent is auto-checked when all children are checked off."""
    md = tmp_path / "PLAN.md"
    md.write_text("- [ ] Parent\n  - [ ] Child A\n  - [ ] Child B\n")

    # Check off Child A
    tasks = parse(md)
    child_a = find_next(tasks)
    assert child_a is not None
    assert child_a.text == "Child A"
    check_off(md, child_a)

    # Parent should still be unchecked
    content = md.read_text()
    assert "- [ ] Parent" in content

    # Check off Child B
    tasks = parse(md)
    child_b = find_next(tasks)
    assert child_b is not None
    assert child_b.text == "Child B"
    check_off(md, child_b)

    # Parent should now be auto-checked
    content = md.read_text()
    assert "- [x] Parent" in content
    assert "- [x] Child A" in content
    assert "- [x] Child B" in content


@pytest.mark.integration
def test_skips_already_checked_items(tmp_path):
    """find_next skips items already marked [x]."""
    md = tmp_path / "PLAN.md"
    md.write_text("- [x] Done\n- [ ] Todo\n")

    tasks = parse(md)
    task = find_next(tasks)
    assert task is not None
    assert task.text == "Todo"


@pytest.mark.integration
def test_preserves_file_content_around_checkboxes(tmp_path):
    """check_off preserves prose, headers, and other markdown around checkboxes."""
    original = (
        "# My Plan\n\n"
        "Some description here.\n\n"
        "- [ ] First task\n"
        "- [ ] Second task\n\n"
        "## Notes\n\n"
        "Footer text.\n"
    )
    md = tmp_path / "PLAN.md"
    md.write_text(original)

    tasks = parse(md)
    task = find_next(tasks)
    assert task is not None
    check_off(md, task)

    content = md.read_text()
    assert "# My Plan" in content
    assert "Some description here." in content
    assert "## Notes" in content
    assert "Footer text." in content
    assert "- [x] First task" in content
    assert "- [ ] Second task" in content
