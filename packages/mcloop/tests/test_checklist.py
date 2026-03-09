"""Tests for loop.checklist."""

from mcloop.checklist import (
    check_off,
    find_next,
    is_auto_task,
    is_user_task,
    mark_failed,
    parse,
    parse_auto_task,
    parse_description,
    user_task_instructions,
)

SAMPLE = """\
- [ ] Add user authentication
- [ ] Set up database migrations
  - [ ] Create users table
  - [ ] Create sessions table
- [ ] Write API endpoint for login
- [x] Initialize project structure
"""

NESTED_ALL_DONE = """\
- [ ] Set up database migrations
  - [x] Create users table
  - [x] Create sessions table
"""


def test_parse_basic(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text(SAMPLE)
    tasks = parse(f)

    assert len(tasks) == 4
    assert tasks[0].text == "Add user authentication"
    assert not tasks[0].checked
    assert tasks[3].text == "Initialize project structure"
    assert tasks[3].checked


def test_parse_nested(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text(SAMPLE)
    tasks = parse(f)

    parent = tasks[1]
    assert parent.text == "Set up database migrations"
    assert len(parent.children) == 2
    assert parent.children[0].text == "Create users table"
    assert parent.children[1].text == "Create sessions table"


def test_find_next_returns_first_unchecked_leaf(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text(SAMPLE)
    tasks = parse(f)

    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Add user authentication"


def test_find_next_prefers_children(tmp_path):
    md = """\
- [ ] Parent
  - [ ] Child 1
  - [ ] Child 2
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Child 1"


def test_find_next_parent_when_children_done(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text(NESTED_ALL_DONE)
    tasks = parse(f)

    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Set up database migrations"


def test_find_next_none_when_all_done(tmp_path):
    md = "- [x] Done\n- [x] Also done\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert find_next(tasks) is None


def test_check_off(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text(SAMPLE)
    tasks = parse(f)
    first = find_next(tasks)

    check_off(f, first)

    tasks2 = parse(f)
    assert tasks2[0].checked
    assert tasks2[0].text == "Add user authentication"


def test_check_off_auto_checks_parent(tmp_path):
    md = """\
- [ ] Parent
  - [x] Child 1
  - [ ] Child 2
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Check off Child 2
    child2 = tasks[0].children[1]
    check_off(f, child2)

    tasks2 = parse(f)
    assert tasks2[0].checked  # parent auto-checked
    assert tasks2[0].children[0].checked
    assert tasks2[0].children[1].checked


def test_parse_failed_marker(tmp_path):
    md = "- [!] Broken task\n- [ ] Next task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert tasks[0].failed
    assert not tasks[0].checked
    assert not tasks[1].failed


def test_find_next_skips_failed(tmp_path):
    md = "- [!] Broken task\n- [ ] Next task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Next task"


def test_find_next_none_when_all_failed_or_done(tmp_path):
    md = "- [!] Broken\n- [x] Done\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert find_next(tasks) is None


def test_mark_failed(tmp_path):
    md = "- [ ] Will fail\n- [ ] Other\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    mark_failed(f, tasks[0])

    tasks2 = parse(f)
    assert tasks2[0].failed
    assert not tasks2[1].failed
    assert "- [!] Will fail" in f.read_text()


def test_parse_description(tmp_path):
    md = """\
# My Project

Build a REST API for managing widgets.
Use Flask and SQLite.

- [ ] Set up project structure
- [ ] Add widget CRUD endpoints
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)

    desc = parse_description(f)
    assert "Build a REST API" in desc
    assert "Flask and SQLite" in desc
    assert "- [ ]" not in desc


def test_parse_description_empty(tmp_path):
    md = "- [ ] First task\n- [ ] Second task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)

    assert parse_description(f) == ""


def test_parse_uppercase_x(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text("- [X] Done with uppercase\n- [ ] Not done\n")
    tasks = parse(f)
    assert tasks[0].checked
    assert not tasks[1].checked


def test_parse_empty_file(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text("")
    tasks = parse(f)
    assert tasks == []


def test_parse_no_checkboxes(tmp_path):
    f = tmp_path / "tasks.md"
    f.write_text("# Project\n\nJust some text, no tasks.\n")
    tasks = parse(f)
    assert tasks == []


def test_find_next_empty_list():
    assert find_next([]) is None


def test_deep_nesting(tmp_path):
    md = """\
- [ ] Level 0
  - [ ] Level 1
    - [ ] Level 2
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert len(tasks) == 1
    assert len(tasks[0].children) == 1
    assert len(tasks[0].children[0].children) == 1
    assert tasks[0].children[0].children[0].text == "Level 2"

    nxt = find_next(tasks)
    assert nxt.text == "Level 2"


def test_check_off_deep_auto_checks_all_parents(tmp_path):
    md = """\
- [ ] L0
  - [ ] L1
    - [ ] L2
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    leaf = tasks[0].children[0].children[0]
    check_off(f, leaf)

    tasks2 = parse(f)
    assert tasks2[0].checked
    assert tasks2[0].children[0].checked
    assert tasks2[0].children[0].children[0].checked


def test_mixed_checked_and_unchecked_children(tmp_path):
    md = """\
- [ ] Parent
  - [x] Done child
  - [ ] Undone child
  - [!] Failed child
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    nxt = find_next(tasks)
    assert nxt.text == "Undone child"


def test_multiple_roots_mixed(tmp_path):
    md = """\
- [x] Root 1
- [!] Root 2
- [ ] Root 3
  - [x] Child A
  - [ ] Child B
- [ ] Root 4
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert len(tasks) == 4
    nxt = find_next(tasks)
    assert nxt.text == "Child B"


def test_mark_failed_checked_task(tmp_path):
    """mark_failed handles tasks that Claude Code already checked off."""
    md = "- [x] Already checked\n- [ ] Other\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    mark_failed(f, tasks[0])

    tasks2 = parse(f)
    assert tasks2[0].failed
    assert not tasks2[0].checked
    assert "- [!] Already checked" in f.read_text()


def test_mark_failed_preserves_other_tasks(tmp_path):
    md = "- [ ] Task A\n- [ ] Task B\n- [ ] Task C\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    mark_failed(f, tasks[1])

    tasks2 = parse(f)
    assert not tasks2[0].failed
    assert tasks2[1].failed
    assert not tasks2[2].failed
    assert not tasks2[0].checked
    assert not tasks2[2].checked


def test_check_off_does_not_auto_check_parent_with_failed_child(tmp_path):
    md = """\
- [ ] Parent
  - [!] Failed child
  - [ ] Good child
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    good_child = tasks[0].children[1]
    check_off(f, good_child)

    tasks2 = parse(f)
    assert not tasks2[0].checked  # parent should NOT auto-check
    assert tasks2[0].children[1].checked


def test_is_user_task_with_tag(tmp_path):
    md = "- [ ] [USER] Launch the app and check the menu bar\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert is_user_task(tasks[0])


def test_is_user_task_without_tag(tmp_path):
    md = "- [ ] Fix the crash on startup\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert not is_user_task(tasks[0])


def test_is_user_task_tag_mid_text(tmp_path):
    md = "- [ ] Verify [USER] the window appears\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert is_user_task(tasks[0])


def test_user_task_instructions_strips_tag(tmp_path):
    md = "- [ ] [USER] Launch the app and check if the icon appears\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert user_task_instructions(tasks[0]) == ("Launch the app and check if the icon appears")


def test_is_auto_task_with_tag(tmp_path):
    md = "- [ ] [AUTO:run_cli] ./my_app --flag\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert is_auto_task(tasks[0])


def test_is_auto_task_without_tag(tmp_path):
    md = "- [ ] Fix the crash on startup\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert not is_auto_task(tasks[0])


def test_is_auto_task_not_user_task(tmp_path):
    md = "- [ ] [AUTO:run_cli] ./my_app\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert is_auto_task(tasks[0])
    assert not is_user_task(tasks[0])


def test_parse_auto_task_run_cli(tmp_path):
    md = "- [ ] [AUTO:run_cli] ./my_app --flag\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    action, args = parse_auto_task(tasks[0])
    assert action == "run_cli"
    assert args == "./my_app --flag"


def test_parse_auto_task_run_gui(tmp_path):
    md = "- [ ] [AUTO:run_gui] open .build/debug/MyApp | MyApp\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    action, args = parse_auto_task(tasks[0])
    assert action == "run_gui"
    assert args == "open .build/debug/MyApp | MyApp"


def test_parse_auto_task_window_exists(tmp_path):
    md = "- [ ] [AUTO:window_exists] MyApp\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    action, args = parse_auto_task(tasks[0])
    assert action == "window_exists"
    assert args == "MyApp"


def test_parse_auto_task_no_tag(tmp_path):
    md = "- [ ] Normal task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    action, args = parse_auto_task(tasks[0])
    assert action == ""
    assert args == ""
