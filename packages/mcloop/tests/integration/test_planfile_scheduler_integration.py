"""Integration tests for the scheduler's PLAN.md I/O via the
``_planfile_compat`` shim — real file roundtrips, no mocks.

These tests use canonical PLAN.md fixtures (``## Stage N:`` headers with
``T-NNNNNN:`` task ids) because the shim's mutations are backed by
``bob_tools.planfile`` typed mutations, which require migrated task ids.
"""

import pytest

from mcloop._planfile_compat import check_off, find_next, mark_failed, parse


def _canonical_plan(*lines: str) -> str:
    body = "\n".join(lines)
    return f"# Demo\n\n## Stage 1: Tasks\n\n{body}\n"


@pytest.mark.integration
def test_parse_check_off_roundtrip(tmp_path):
    md = tmp_path / "PLAN.md"
    md.write_text(
        _canonical_plan(
            "- [ ] T-000001: First task",
            "- [ ] T-000002: Second task",
        )
    )

    tasks = parse(md)
    first = find_next(tasks)
    assert first is not None
    assert first.text == "First task"

    check_off(md, first)

    content = md.read_text()
    assert "- [x] T-000001: First task" in content
    assert "- [ ] T-000002: Second task" in content

    tasks = parse(md)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Second task"


@pytest.mark.integration
def test_check_off_all_tasks(tmp_path):
    md = tmp_path / "PLAN.md"
    md.write_text(
        _canonical_plan(
            "- [ ] T-000001: A",
            "- [ ] T-000002: B",
            "- [ ] T-000003: C",
        )
    )

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
    md = tmp_path / "PLAN.md"
    md.write_text(
        _canonical_plan(
            "- [ ] T-000001: Failing task",
            "- [ ] T-000002: Other task",
        )
    )

    tasks = parse(md)
    task = find_next(tasks)
    assert task is not None

    mark_failed(md, task)

    content = md.read_text()
    assert "- [!] T-000001: Failing task" in content
    assert "- [ ] T-000002: Other task" in content

    tasks = parse(md)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Other task"


@pytest.mark.integration
def test_parent_auto_checked_when_children_done(tmp_path):
    md = tmp_path / "PLAN.md"
    md.write_text(
        _canonical_plan(
            "- [ ] T-000001: Parent",
            "  - [ ] T-000002: Child A",
            "  - [ ] T-000003: Child B",
        )
    )

    tasks = parse(md)
    child_a = find_next(tasks)
    assert child_a is not None
    assert child_a.text == "Child A"
    check_off(md, child_a)

    content = md.read_text()
    assert "- [ ] T-000001: Parent" in content

    tasks = parse(md)
    child_b = find_next(tasks)
    assert child_b is not None
    assert child_b.text == "Child B"
    check_off(md, child_b)

    content = md.read_text()
    assert "- [x] T-000001: Parent" in content
    assert "- [x] T-000002: Child A" in content
    assert "- [x] T-000003: Child B" in content


@pytest.mark.integration
def test_skips_already_checked_items(tmp_path):
    md = tmp_path / "PLAN.md"
    md.write_text(
        _canonical_plan(
            "- [x] T-000001: Done",
            "- [ ] T-000002: Todo",
        )
    )

    tasks = parse(md)
    task = find_next(tasks)
    assert task is not None
    assert task.text == "Todo"


@pytest.mark.integration
def test_preserves_file_content_around_checkboxes(tmp_path):
    original = (
        "# My Plan\n\n"
        "Some description here.\n\n"
        "## Stage 1: Work\n\n"
        "- [ ] T-000001: First task\n"
        "- [ ] T-000002: Second task\n"
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
    assert "- [x] T-000001: First task" in content
    assert "- [ ] T-000002: Second task" in content
