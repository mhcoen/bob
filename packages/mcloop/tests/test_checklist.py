"""Tests for loop.checklist."""

from loop.checklist import check_off, find_next, mark_failed, parse, parse_description

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
