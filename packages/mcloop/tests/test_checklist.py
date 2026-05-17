"""Tests for loop.checklist."""

from mcloop.checklist import (
    PlanCorruptionError,
    check_off,
    clear_failed_markers,
    find_next,
    find_parent,
    get_batch_children,
    get_eliminated,
    has_unchecked_bugs,
    is_auto_task,
    is_batch_task,
    is_user_task,
    mark_failed,
    parse,
    parse_auto_task,
    parse_description,
    purge_completed_bugs,
    task_label,
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


def test_find_next_blocks_siblings_after_failed_subtask(tmp_path):
    """A failed subtask blocks later siblings under the same parent."""
    md = """\
- [ ] Parent
   - [!] Failed child
   - [ ] Next child
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    nxt = find_next(tasks)
    # Parent can't complete with a failed child, and the sibling
    # after the failed child is blocked. Nothing is actionable.
    assert nxt is None


def test_find_next_skips_root_with_failed_child(tmp_path):
    """A root task with a failed child is skipped; later roots still run."""
    md = """\
- [ ] Parent A
   - [!] Broken
   - [ ] Blocked
- [ ] Parent B
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Parent B"


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


def test_is_user_task_tag_mid_text_is_prose(tmp_path):
    md = "- [ ] Verify [USER] the window appears\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert not is_user_task(tasks[0])


def test_is_user_task_bug_description_mentions_user_tag_not_user_task(tmp_path):
    md = "- [ ] Fix `[USER]` task body loss at parse time\n"
    f = tmp_path / "bugs.md"
    f.write_text(md)
    tasks = parse(f)
    assert not is_user_task(tasks[0])


def test_user_task_instructions_strips_tag(tmp_path):
    md = "- [ ] [USER] Launch the app and check if the icon appears\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert user_task_instructions(tasks[0]) == ("Launch the app and check if the icon appears")


def test_user_task_body_collected_until_next_checkbox(tmp_path):
    md = (
        "- [ ] [USER] Run the diagnostic\n"
        "  Open a terminal and run:\n"
        "\n"
        "  $ ./diagnose --verbose\n"
        "\n"
        "  Report the final exit line.\n"
        "- [ ] Followup task\n"
    )
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert tasks[0].body == (
        "  Open a terminal and run:\n\n  $ ./diagnose --verbose\n\n  Report the final exit line."
    )
    assert tasks[1].body == ""


def test_user_task_body_stops_at_heading(tmp_path):
    md = (
        "## Stage 1\n"
        "- [ ] [USER] Verify the UI\n"
        "  Look at the menu bar.\n"
        "## Stage 2\n"
        "- [ ] Other work\n"
    )
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert tasks[0].body == "  Look at the menu bar."


def test_user_task_body_empty_for_non_user_task(tmp_path):
    md = "- [ ] Normal task\n  Some prose that would otherwise drop\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert tasks[0].body == ""


def test_user_task_instructions_includes_body(tmp_path):
    md = "- [ ] [USER] Step one\n  Step two on its own line.\n\n  $ run-me\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    instructions = user_task_instructions(tasks[0])
    assert instructions == ("Step one\n  Step two on its own line.\n\n  $ run-me")
    assert "\n" in instructions


def test_user_task_multiline_round_trip_to_banner(tmp_path):
    from mcloop import formatting

    md = (
        "- [ ] [USER] Confirm the install\n"
        "  Open a terminal and run:\n"
        "\n"
        "  $ ./installer.sh --check\n"
        "\n"
        "  Then report the last line of output.\n"
    )
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    instructions = user_task_instructions(tasks[0])
    # Newlines survive parse -> user_task_instructions.
    assert instructions.count("\n") >= 4
    assert "Confirm the install" in instructions
    assert "./installer.sh --check" in instructions
    banner = formatting.user_banner("1", instructions)
    # Each meaningful body line appears verbatim in the banner output.
    assert "Confirm the install" in banner
    assert "Open a terminal and run:" in banner
    assert "$ ./installer.sh --check" in banner
    assert "Then report the last line of output." in banner
    # Newlines preserved, not collapsed to a single line.
    assert "Confirm the install\n" in banner


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


def test_find_next_bugs_before_features(tmp_path):
    """Bug tasks have absolute priority over feature tasks."""
    md = "## Stage 1: Core\n- [ ] Feature task\n## Bugs\n- [ ] Fix crash\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Fix crash"


def test_find_next_bugs_before_features_no_stages(tmp_path):
    """Bug priority works in plans without stage headers."""
    md = "- [ ] Feature A\n- [ ] Feature B\n## Bugs\n- [ ] Fix segfault\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Fix segfault"


def test_find_next_features_when_bugs_checked(tmp_path):
    """Feature tasks returned when all bugs are checked off."""
    md = "## Bugs\n- [x] Fixed crash\n## Stage 1: Core\n- [ ] Add feature\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Add feature"


def test_find_next_features_when_bugs_failed(tmp_path):
    """Failed bugs are skipped, feature tasks returned if no unchecked bugs."""
    md = "## Bugs\n- [!] Unresolvable\n## Stage 1: Core\n- [ ] Add feature\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Add feature"


def test_find_next_bug_nested_children(tmp_path):
    """Bug tasks with children return the first unchecked child."""
    md = (
        "## Bugs\n"
        "- [ ] Fix crash group\n"
        "  - [x] Investigate cause\n"
        "  - [ ] Apply fix\n"
        "## Stage 1: Core\n"
        "- [ ] Add feature\n"
    )
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Apply fix"


def test_find_next_no_bugs_section_returns_feature(tmp_path):
    """Without a ## Bugs section, find_next returns first feature task."""
    md = "## Stage 1: Core\n- [ ] Feature A\n- [ ] Feature B\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Feature A"


def test_find_next_empty_bugs_section_returns_feature(tmp_path):
    """An empty ## Bugs section (no tasks) returns feature tasks."""
    md = "## Bugs\n\n## Stage 1: Core\n- [ ] Feature A\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Feature A"


def test_find_next_multiple_bugs_returns_first(tmp_path):
    """With multiple unchecked bugs, returns the first one."""
    md = "## Stage 1: Core\n- [ ] Feature\n## Bugs\n- [ ] Bug A\n- [ ] Bug B\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    nxt = find_next(tasks)
    assert nxt is not None
    assert nxt.text == "Bug A"


def test_has_unchecked_bugs_true(tmp_path):
    md = "## Bugs\n- [ ] Fix crash\n## Stage 1: Core\n- [ ] Add feature\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert has_unchecked_bugs(tasks)


def test_has_unchecked_bugs_false_all_checked(tmp_path):
    md = "## Bugs\n- [x] Fix crash\n## Stage 1: Core\n- [ ] Add feature\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert not has_unchecked_bugs(tasks)


def test_has_unchecked_bugs_false_no_bugs_section(tmp_path):
    md = "## Stage 1: Core\n- [ ] Add feature\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert not has_unchecked_bugs(tasks)


def test_has_unchecked_bugs_false_failed(tmp_path):
    md = "## Bugs\n- [!] Fix crash\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert not has_unchecked_bugs(tasks)


# ── [RULEDOUT] parsing ──


def test_parse_ruledout_attaches_to_parent(tmp_path):
    """[RULEDOUT] lines attach to the nearest parent task by indentation."""
    md = "- [ ] Fix crash\n  [RULEDOUT] tried restarting\n- [ ] Other task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert len(tasks) == 2
    assert tasks[0].eliminated == ["[RULEDOUT] tried restarting"]
    assert tasks[1].eliminated == []


def test_parse_ruledout_nested_task(tmp_path):
    """[RULEDOUT] at deeper indent attaches to the nested parent."""
    md = "- [ ] Parent\n  - [ ] Child\n    [RULEDOUT] didn't work\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert tasks[0].eliminated == []
    assert tasks[0].children[0].eliminated == ["[RULEDOUT] didn't work"]


def test_parse_ruledout_top_level_attaches_to_last_root(tmp_path):
    """Top-level [RULEDOUT] (no indent) attaches to most recent root task."""
    md = "- [ ] First task\n- [ ] Second task\n[RULEDOUT] top level approach\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert tasks[0].eliminated == []
    assert tasks[1].eliminated == ["[RULEDOUT] top level approach"]


def test_parse_ruledout_multiple_entries(tmp_path):
    """Multiple [RULEDOUT] lines accumulate on the same task."""
    md = "- [ ] Fix bug\n  [RULEDOUT] approach A\n  [RULEDOUT] approach B\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert len(tasks[0].eliminated) == 2
    assert "[RULEDOUT] approach A" in tasks[0].eliminated
    assert "[RULEDOUT] approach B" in tasks[0].eliminated


def test_parse_ruledout_no_tasks_ignored(tmp_path):
    """[RULEDOUT] before any tasks is silently ignored."""
    md = "[RULEDOUT] orphan\n- [ ] Task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert len(tasks) == 1
    assert tasks[0].eliminated == []


def test_get_eliminated_target_task(tmp_path):
    """get_eliminated returns entries from the target task."""
    md = "- [ ] Fix crash\n  [RULEDOUT] tried restart\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    result = get_eliminated(tasks, tasks[0])
    assert result == ["[RULEDOUT] tried restart"]


def test_get_eliminated_collects_ancestors(tmp_path):
    """get_eliminated collects entries from ancestors along the path."""
    md = (
        "- [ ] Parent\n"
        "  [RULEDOUT] parent approach\n"
        "  - [ ] Child\n"
        "    [RULEDOUT] child approach\n"
    )
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    child = tasks[0].children[0]
    result = get_eliminated(tasks, child)
    assert "[RULEDOUT] parent approach" in result
    assert "[RULEDOUT] child approach" in result


def test_get_eliminated_not_found(tmp_path):
    """get_eliminated returns empty list when target not in tree."""
    from mcloop.checklist import Task as CTask

    md = "- [ ] Task A\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    fake = CTask(
        text="nonexistent",
        checked=False,
        failed=False,
        line_number=99,
        indent_level=0,
    )
    assert get_eliminated(tasks, fake) == []


# ── [BATCH] support ──


def test_is_batch_task_true(tmp_path):
    md = "- [ ] [BATCH] Build all components\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert is_batch_task(tasks[0])


def test_is_batch_task_false(tmp_path):
    md = "- [ ] Build all components\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert not is_batch_task(tasks[0])


def test_get_batch_children_returns_unchecked(tmp_path):
    md = "- [ ] [BATCH] Parent\n  - [x] Done child\n  - [ ] Child A\n  - [ ] Child B\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    batch = get_batch_children(tasks[0])
    assert len(batch) == 2
    assert batch[0].text == "Child A"
    assert batch[1].text == "Child B"


def test_get_batch_children_stops_at_failed_after_collected(tmp_path):
    """A failed child stops collection once non-failed children exist."""
    md = (
        "- [ ] [BATCH] Parent\n"
        "  - [x] Done child\n"
        "  - [ ] Child A\n"
        "  - [ ] Child B\n"
        "  - [!] Failed child\n"
        "  - [ ] Child C\n"
    )
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    batch = get_batch_children(tasks[0])
    assert len(batch) == 2
    assert batch[0].text == "Child A"
    assert batch[1].text == "Child B"


def test_get_batch_children_skips_leading_failed(tmp_path):
    """Failed children before any collected child are skipped."""
    md = (
        "- [ ] [BATCH] Parent\n"
        "  - [!] Failed first\n"
        "  - [!] Failed second\n"
        "  - [ ] Child A\n"
        "  - [ ] Child B\n"
    )
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    batch = get_batch_children(tasks[0])
    assert len(batch) == 2
    assert batch[0].text == "Child A"
    assert batch[1].text == "Child B"


def test_get_batch_children_stops_at_user_task(tmp_path):
    md = "- [ ] [BATCH] Parent\n  - [ ] Child A\n  - [ ] [USER] Check the app\n  - [ ] Child B\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    batch = get_batch_children(tasks[0])
    assert len(batch) == 1
    assert batch[0].text == "Child A"


def test_get_batch_children_stops_at_auto_task(tmp_path):
    md = "- [ ] [BATCH] Parent\n  - [ ] Child A\n  - [ ] [AUTO:run_cli] ./app\n  - [ ] Child B\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    batch = get_batch_children(tasks[0])
    assert len(batch) == 1
    assert batch[0].text == "Child A"


def test_get_batch_children_empty_when_all_done(tmp_path):
    md = "- [ ] [BATCH] Parent\n  - [x] Done A\n  - [x] Done B\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    batch = get_batch_children(tasks[0])
    assert batch == []


def test_get_batch_children_empty_when_all_failed(tmp_path):
    """All children failed and none were collected — returns empty."""
    md = "- [ ] [BATCH] Parent\n  - [!] Failed A\n  - [!] Failed B\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    batch = get_batch_children(tasks[0])
    assert batch == []


def test_get_batch_children_failed_immediately_after_first(tmp_path):
    """Failed child right after first collected child yields single-item batch."""
    md = "- [ ] [BATCH] Parent\n  - [ ] Child A\n  - [!] Failed child\n  - [ ] Child B\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    batch = get_batch_children(tasks[0])
    assert len(batch) == 1
    assert batch[0].text == "Child A"


def test_get_batch_children_zero_when_failed_after_done(tmp_path):
    """Parent [done, failed, pending, pending] collects zero — failed blocks pending siblings."""
    md = (
        "- [ ] [BATCH] Parent\n"
        "  - [x] Done child\n"
        "  - [!] Failed child\n"
        "  - [ ] Pending A\n"
        "  - [ ] Pending B\n"
    )
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    batch = get_batch_children(tasks[0])
    assert batch == []


def test_find_parent_returns_parent(tmp_path):
    md = "- [ ] Parent\n  - [ ] Child\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    child = tasks[0].children[0]
    parent = find_parent(tasks, child)
    assert parent is tasks[0]


def test_find_parent_returns_none_for_root(tmp_path):
    md = "- [ ] Root task\n- [ ] Another root\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert find_parent(tasks, tasks[0]) is None
    assert find_parent(tasks, tasks[1]) is None


def test_task_label_flat(tmp_path):
    md = "- [ ] First\n- [ ] Second\n- [ ] Third\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert task_label(tasks, tasks[0]) == "1"
    assert task_label(tasks, tasks[1]) == "2"
    assert task_label(tasks, tasks[2]) == "3"


def test_task_label_with_subtasks(tmp_path):
    md = "- [ ] Parent\n  - [ ] Child one\n  - [ ] Child two\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert task_label(tasks, tasks[0]) == "1"
    assert task_label(tasks, tasks[0].children[0]) == "1.1"
    assert task_label(tasks, tasks[0].children[1]) == "1.2"


def test_task_label_with_stages(tmp_path):
    md = "## Stage 2: Setup\n- [ ] First task\n- [ ] Second task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)
    assert task_label(tasks, tasks[0]) == "2.1"
    assert task_label(tasks, tasks[1]) == "2.2"


# ── line_number as primary key ──


def test_check_off_duplicate_text_uses_line_number(tmp_path):
    """With duplicate task texts, check_off targets the correct one by line_number."""
    md = "- [ ] Run tests\n- [ ] Run tests\n- [ ] Run tests\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Check off the second duplicate (line 1)
    check_off(f, tasks[1])

    tasks2 = parse(f)
    assert not tasks2[0].checked
    assert tasks2[1].checked
    assert not tasks2[2].checked


def test_mark_failed_duplicate_text_uses_line_number(tmp_path):
    """With duplicate task texts, mark_failed targets the correct one by line_number."""
    md = "- [ ] Deploy\n- [ ] Deploy\n- [ ] Deploy\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Fail the third duplicate (line 2)
    mark_failed(f, tasks[2])

    tasks2 = parse(f)
    assert not tasks2[0].failed
    assert not tasks2[1].failed
    assert tasks2[2].failed


def test_check_off_stale_line_number_falls_back_to_text(tmp_path):
    """When line_number is stale (file modified), falls back to text match."""
    md = "- [ ] First task\n- [ ] Second task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Simulate file modification: insert a line at the top, shifting everything
    f.write_text("# Added header\n- [ ] First task\n- [ ] Second task\n")

    # task.line_number is 0 but "First task" is now on line 1
    check_off(f, tasks[0])

    tasks2 = parse(f)
    assert tasks2[0].checked
    assert tasks2[0].text == "First task"


def test_mark_failed_stale_line_number_falls_back_to_text(tmp_path):
    """When line_number is stale, mark_failed falls back to text match."""
    md = "- [ ] Alpha\n- [ ] Beta\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Shift lines by inserting a header
    f.write_text("# Header\n- [ ] Alpha\n- [ ] Beta\n")

    mark_failed(f, tasks[1])

    tasks2 = parse(f)
    assert not tasks2[0].failed
    assert tasks2[1].failed


def test_check_off_line_number_preferred_over_earlier_text_match(tmp_path):
    """line_number is used even when an earlier line has the same text."""
    md = "- [x] Run tests\n- [ ] Run tests\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # tasks[1] has line_number=1 and text "Run tests"
    # Line 0 also has text "Run tests" but is already checked
    # line_number should take priority and target line 1
    check_off(f, tasks[1])

    content = f.read_text()
    assert content.count("[x] Run tests") == 2


def test_fallback_validates_indent_level(tmp_path):
    """Fallback text match skips tasks with different indent levels."""
    md = "- [ ] Build\n  - [ ] Build\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Simulate stale line_number: insert header to shift lines
    f.write_text("# Header\n- [ ] Build\n  - [ ] Build\n")

    # tasks[0] has indent_level=0; fallback should match the root "Build", not the child
    check_off(f, tasks[0])

    tasks2 = parse(f)
    assert tasks2[0].checked  # root Build
    assert not tasks2[0].children[0].checked  # child Build untouched


def test_fallback_validates_stage(tmp_path):
    """Fallback text match skips tasks in a different stage."""
    md = "## Stage 1: Setup\n- [ ] Deploy\n## Stage 2: Launch\n- [ ] Deploy\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Both tasks have text "Deploy" but different stages
    # Simulate stale line_number by inserting a line
    f.write_text("# Title\n## Stage 1: Setup\n- [ ] Deploy\n## Stage 2: Launch\n- [ ] Deploy\n")

    # tasks[1] is in Stage 2; fallback should match the Stage 2 "Deploy"
    check_off(f, tasks[1])

    tasks2 = parse(f)
    assert not tasks2[0].checked  # Stage 1 Deploy untouched
    assert tasks2[1].checked  # Stage 2 Deploy checked


def test_mark_failed_fallback_validates_indent_level(tmp_path):
    """mark_failed fallback skips tasks with different indent levels."""
    md = "- [ ] Test\n  - [ ] Test\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Shift lines
    f.write_text("# Header\n- [ ] Test\n  - [ ] Test\n")

    # Fail the child (indent_level=2), not the root
    mark_failed(f, tasks[0].children[0])

    tasks2 = parse(f)
    assert not tasks2[0].failed  # root Test
    assert tasks2[0].children[0].failed  # child Test


def test_mark_failed_fallback_validates_stage(tmp_path):
    """mark_failed fallback skips tasks in a different stage."""
    md = "## Stage 1: A\n- [ ] Run\n## Stage 2: B\n- [ ] Run\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    f.write_text("# Title\n## Stage 1: A\n- [ ] Run\n## Stage 2: B\n- [ ] Run\n")

    mark_failed(f, tasks[0])

    tasks2 = parse(f)
    assert tasks2[0].failed  # Stage 1 Run
    assert not tasks2[1].failed  # Stage 2 Run


def test_fallback_raises_when_no_match_with_validation(tmp_path):
    """Fallback raises IndexError when text matches but indent/stage don't."""
    from mcloop.checklist import Task as CTask

    md = "- [ ] Only task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)

    # Create a task with matching text but wrong indent and stage
    fake = CTask(
        text="Only task",
        checked=False,
        failed=False,
        line_number=99,  # stale
        indent_level=4,  # wrong indent
        stage="Stage 5: Missing",  # wrong stage
    )
    import pytest

    with pytest.raises(IndexError, match="no text match"):
        from mcloop.checklist import _find_task_line

        lines = f.read_text().splitlines()
        _find_task_line(lines, fake)


def test_fallback_nearest_match_check_off(tmp_path):
    """Fallback picks the match nearest to the original line_number, not the first."""
    from mcloop.checklist import Task as CTask
    from mcloop.checklist import _find_task_line

    # 5 identical Deploys after 10 header lines (candidates at lines 10-14)
    content = "# H\n" * 10 + "- [ ] Deploy\n" * 5
    f = tmp_path / "tasks.md"
    f.write_text(content)
    lines = f.read_text().splitlines()

    # Stale line_number=13: nearest candidate should be line 13, not line 10 (first)
    task = CTask(
        text="Deploy",
        checked=False,
        failed=False,
        line_number=13,
        indent_level=0,
        stage="",
    )
    result = _find_task_line(lines, task)
    assert result == 13


def test_fallback_nearest_match_not_first(tmp_path):
    """When the nearest match is not the first candidate, it is still chosen."""
    from mcloop.checklist import Task as CTask
    from mcloop.checklist import _find_task_line

    # 3 identical tasks with headers shifting them
    content = "- [x] Done\n- [ ] Work\n- [x] Done\n- [ ] Work\n- [x] Done\n- [ ] Work\n"
    f = tmp_path / "plan.md"
    f.write_text(content)
    lines = content.splitlines()

    # Target the last "Work" (originally at line 5), stale line_number=50
    fake = CTask(
        text="Work",
        checked=False,
        failed=False,
        line_number=50,  # stale, far past end
        indent_level=0,
        stage="",
    )
    result = _find_task_line(lines, fake)
    # Unchecked "Work" at lines 1, 3, 5. Nearest to 50 is 5.
    assert result == 5


def test_fallback_nearest_checked_when_no_unchecked(tmp_path):
    """When all matches are checked, nearest checked match is returned."""
    from mcloop.checklist import Task as CTask
    from mcloop.checklist import _find_task_line

    content = "- [x] Task\n- [x] Task\n- [x] Task\n"
    f = tmp_path / "plan.md"
    f.write_text(content)
    lines = content.splitlines()

    fake = CTask(
        text="Task",
        checked=False,
        failed=False,
        line_number=2,  # stale
        indent_level=0,
        stage="",
    )
    result = _find_task_line(lines, fake)
    assert result == 2  # nearest checked match


def test_auto_check_parents_duplicate_text_different_stages(tmp_path):
    """_auto_check_parents only checks off the parent whose children are all done."""
    md = """\
## Stage 1: First
- [ ] Setup
  - [x] Child A
  - [ ] Child B

## Stage 2: Second
- [ ] Setup
  - [ ] Child C
  - [ ] Child D
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Check off Child B in Stage 1 — should auto-check Stage 1 parent only
    child_b = tasks[0].children[1]
    assert child_b.text == "Child B"
    check_off(f, child_b)

    tasks2 = parse(f)
    assert tasks2[0].checked, "Stage 1 parent should be auto-checked"
    assert not tasks2[1].checked, "Stage 2 parent should NOT be auto-checked"


def test_auto_check_parents_duplicate_text_different_indent(tmp_path):
    """_auto_check_parents handles parents with identical text at different indent levels."""
    md = """\
- [ ] Outer
  - [ ] Setup
    - [x] Deep A
    - [ ] Deep B
  - [x] Other child
- [ ] Setup
  - [x] Top A
  - [ ] Top B
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Check off Deep B (nested under Outer > Setup)
    deep_b = tasks[0].children[0].children[1]
    assert deep_b.text == "Deep B"
    check_off(f, deep_b)

    tasks2 = parse(f)
    # Inner "Setup" should be auto-checked (both Deep A and Deep B done)
    assert tasks2[0].children[0].checked, "Inner Setup should be auto-checked"
    # Outer "Outer" should also be auto-checked (Setup + Other child both done)
    assert tasks2[0].checked, "Outer should be auto-checked"
    # Top-level "Setup" should NOT be auto-checked (Top B still unchecked)
    assert not tasks2[1].checked, "Top-level Setup should NOT be auto-checked"


def test_check_off_identical_text_different_indent_levels(tmp_path):
    """check_off targets the correct task when identical text appears at different indents."""
    md = """\
- [ ] Build
  - [ ] Build
  - [ ] Other child
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Both tasks have text "Build" but at different indent levels.
    # check_off the child (indent_level=2) using line_number identity.
    child = tasks[0].children[0]
    assert child.text == "Build"
    check_off(f, child)

    tasks2 = parse(f)
    # Root should remain unchecked (it still has "Other child" unchecked)
    assert not tasks2[0].checked, "Root 'Build' should remain unchecked"
    assert tasks2[0].children[0].checked, "Child 'Build' should be checked off"
    assert not tasks2[0].children[1].checked, "Other child should remain unchecked"


def test_check_off_identical_text_different_indent_targets_root(tmp_path):
    """check_off targets the root task when identical text exists as a child."""
    md = """\
- [ ] Build
- [ ] Build
  - [ ] Sub-task
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # check_off the first root (indent_level=0, no children)
    assert tasks[0].text == "Build"
    assert tasks[0].indent_level == 0
    check_off(f, tasks[0])

    tasks2 = parse(f)
    assert tasks2[0].checked, "First root 'Build' should be checked off"
    assert not tasks2[1].checked, "Second root 'Build' should remain unchecked"


def test_mark_failed_identical_text_different_stages(tmp_path):
    """mark_failed targets the correct task when identical text appears in different stages."""
    md = """\
## Stage 1: Alpha
- [ ] Deploy service

## Stage 2: Beta
- [ ] Deploy service
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Both tasks have text "Deploy service" but in different stages.
    # mark_failed the Stage 2 task using line_number identity.
    assert tasks[1].text == "Deploy service"
    assert tasks[1].stage == "Stage 2: Beta"
    mark_failed(f, tasks[1])

    tasks2 = parse(f)
    assert not tasks2[0].failed, "Stage 1 'Deploy service' should not be failed"
    assert tasks2[1].failed, "Stage 2 'Deploy service' should be marked failed"


def test_mark_failed_identical_text_different_stages_targets_first(tmp_path):
    """mark_failed targets Stage 1 task when identical text exists in Stage 2."""
    md = """\
## Stage 1: Alpha
- [ ] Deploy service

## Stage 2: Beta
- [ ] Deploy service
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # mark_failed the Stage 1 task
    assert tasks[0].text == "Deploy service"
    assert tasks[0].stage == "Stage 1: Alpha"
    mark_failed(f, tasks[0])

    tasks2 = parse(f)
    assert tasks2[0].failed, "Stage 1 'Deploy service' should be marked failed"
    assert not tasks2[1].failed, "Stage 2 'Deploy service' should not be failed"


# ── identical tasks at same indent/stage with shifted lines ──


def test_check_off_identical_same_indent_stage_targets_nearest(tmp_path):
    """Two identical tasks at same indent and stage; check_off targets nearest after shift."""
    md = """\
## Stage 1: Build
- [ ] Run tests
- [ ] Run tests
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Both tasks are "Run tests" at indent 0 in the same stage
    assert tasks[0].text == "Run tests"
    assert tasks[1].text == "Run tests"
    assert tasks[0].stage == tasks[1].stage

    # Insert 3 lines at the top, shifting everything down by 3
    shifted = "# Header\n# Line 2\n# Line 3\n" + md
    f.write_text(shifted)

    # tasks[1] had line_number=2; now the tasks are at lines 4 and 5.
    # Fallback should pick line 5 (nearest to original line 2+3=5? no,
    # nearest to stored line_number=2). Lines 4 and 5 are candidates;
    # line 4 is nearest to 2.
    # But we want to check off the SECOND task specifically.
    # tasks[1].line_number was 2; after shift, nearest unchecked to 2 is line 4.
    # So check_off will target line 4 (the first "Run tests" in the shifted file).
    # To test the second task, check off tasks[0] first, then tasks[1].
    check_off(f, tasks[0])
    result = parse(f)
    assert result[0].checked, "First 'Run tests' should be checked"
    assert not result[1].checked, "Second 'Run tests' should still be unchecked"

    # Now check off the second one
    check_off(f, tasks[1])
    result2 = parse(f)
    assert result2[0].checked, "First 'Run tests' should still be checked"
    assert result2[1].checked, "Second 'Run tests' should now be checked"


def test_check_off_shifted_file_nearest_to_original_line(tmp_path):
    """Inserted lines shift tasks; check_off picks the candidate nearest to original line."""
    from mcloop.checklist import _find_task_line

    md = """\
- [ ] Alpha
- [ ] Alpha
- [ ] Alpha
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # Original line numbers: 0, 1, 2
    assert tasks[0].line_number == 0
    assert tasks[1].line_number == 1
    assert tasks[2].line_number == 2

    # Insert 5 lines at the top, tasks shift to lines 5, 6, 7
    shifted = "# H\n" * 5 + md
    f.write_text(shifted)
    lines = f.read_text().splitlines()

    # tasks[2] had line_number=2; candidates are at 5, 6, 7.
    # Nearest to 2 is 5.
    result = _find_task_line(lines, tasks[2])
    assert result == 5

    # tasks[0] had line_number=0; nearest to 0 is also 5.
    result0 = _find_task_line(lines, tasks[0])
    assert result0 == 5

    # tasks[1] had line_number=1; nearest to 1 is also 5.
    result1 = _find_task_line(lines, tasks[1])
    assert result1 == 5

    # After checking off line 5, tasks[1] should fall back to line 6
    lines[5] = lines[5].replace("- [ ]", "- [x]", 1)
    result1b = _find_task_line(lines, tasks[1])
    # Unchecked candidates are now 6, 7. Nearest to 1 is 6.
    assert result1b == 6


def test_check_off_two_identical_shifted_targets_closest(tmp_path):
    """Two identical tasks at same indent/stage, file shifted; check_off picks closest."""
    md = """\
## Stage 1: Deploy
- [ ] Ship it
- [x] Done step
- [ ] Ship it
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    # "Ship it" at lines 1 and 3, same stage, same indent
    ship1 = tasks[0]  # line 1
    ship2 = tasks[2]  # line 3
    assert ship1.text == "Ship it"
    assert ship2.text == "Ship it"
    assert ship1.line_number == 1
    assert ship2.line_number == 3

    # Insert 10 distinct header lines at the top (tasks shift to 11, 12, 13).
    # Distinct titles avoid tripping the structural sanity check; the
    # test's intent is line-shift behavior, not header content.
    shifted = "".join(f"# Header {i}\n" for i in range(10)) + md
    f.write_text(shifted)

    # check_off ship2 (original line 3) — candidates at 11 and 13 (unchecked).
    # Nearest to 3 is 11.  But that's also nearest to ship1 (line 1).
    # After checking off ship2, the unchecked at 11 gets checked.
    check_off(f, ship2)
    result = parse(f)

    # One of the "Ship it" tasks should be checked
    ship_tasks = [t for t in result if t.text == "Ship it"]
    assert sum(1 for t in ship_tasks if t.checked) == 1

    # Check off ship1 — remaining unchecked "Ship it" should be checked
    check_off(f, ship1)
    result2 = parse(f)
    ship_tasks2 = [t for t in result2 if t.text == "Ship it"]
    assert all(t.checked for t in ship_tasks2), "Both 'Ship it' tasks should be checked"


def test_mark_failed_identical_same_stage_shifted(tmp_path):
    """mark_failed with identical tasks at same indent/stage after file shift."""
    md = """\
## Stage 1: Test
- [ ] Verify output
- [ ] Verify output
"""
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert tasks[0].text == "Verify output"
    assert tasks[1].text == "Verify output"

    # Insert lines to shift content
    shifted = "# Added\n# More\n" + md
    f.write_text(shifted)

    # Mark first task as failed — nearest to line 1 among candidates at 3, 4
    mark_failed(f, tasks[0])
    result = parse(f)
    verify_tasks = [t for t in result if t.text == "Verify output"]
    assert verify_tasks[0].failed, "First 'Verify output' should be failed"
    assert not verify_tasks[1].failed, "Second 'Verify output' should not be failed"


# ── section headers with H1/H2 + Stage/Phase ──


def test_parse_h1_phase_header_recognized(tmp_path):
    """An H1 header containing 'Phase N' is recognized as a section header."""
    md = "# Duplo - Phase 1: Bootstrapping\n- [ ] First task\n- [ ] Second task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert len(tasks) == 2
    assert tasks[0].stage == "Duplo - Phase 1: Bootstrapping"
    assert tasks[1].stage == "Duplo - Phase 1: Bootstrapping"


def test_parse_h1_stage_header_recognized(tmp_path):
    """An H1 header containing 'Stage N' is recognized as a section header."""
    md = "# Big Project Stage 3: Refactor\n- [ ] Task A\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert tasks[0].stage == "Big Project Stage 3: Refactor"


def test_parse_h3_phase_header_recognized(tmp_path):
    """An H3 header containing 'Phase N' is also recognized (# or more)."""
    md = "### Subsection Phase 2: Details\n- [ ] A task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert tasks[0].stage == "Subsection Phase 2: Details"


def test_parse_header_without_stage_or_phase_not_section(tmp_path):
    """An H1 without 'Stage' or 'Phase' is NOT treated as a section header."""
    md = "# My Project\n- [ ] Task A\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert tasks[0].stage == ""


def test_parse_phase_without_number_not_section(tmp_path):
    """An H1 mentioning 'Phase' but without a number is NOT a section header."""
    md = "# Planning Phase Considerations\n- [ ] Task A\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert tasks[0].stage == ""


def test_task_label_with_h1_phase(tmp_path):
    """task_label extracts the phase number from an H1 'Phase N' header."""
    md = "# Duplo - Phase 1: Bootstrapping\n- [ ] First task\n- [ ] Second task\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert task_label(tasks, tasks[0]) == "1.1"
    assert task_label(tasks, tasks[1]) == "1.2"


def test_task_label_phase_mid_title(tmp_path):
    """Phase/Stage number extracted when the word appears mid-title, not at start."""
    md = "## Frontend Stage 7: Polish\n- [ ] Tweak padding\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert task_label(tasks, tasks[0]) == "7.1"


def test_parse_h1_bugs_header_recognized(tmp_path):
    """An H1 'Bugs' header is recognized (any heading level)."""
    md = "# Bugs\n- [ ] Fix crash\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert tasks[0].stage == "Bugs"


def test_has_unchecked_bugs_with_h1_bugs_header(tmp_path):
    """has_unchecked_bugs works with an H1 Bugs header."""
    md = "# Bugs\n- [ ] Fix crash\n"
    f = tmp_path / "tasks.md"
    f.write_text(md)
    tasks = parse(f)

    assert has_unchecked_bugs(tasks)


# ── purge_completed_bugs ──


def test_purge_completed_bugs_basic(tmp_path):
    """purge_completed_bugs removes checked items from BUGS.md."""
    md = "## Bugs\n- [x] Fixed crash A\n- [x] Fixed crash B\n- [ ] Unfixed bug\n"
    f = tmp_path / "BUGS.md"
    f.write_text(md)
    purge_completed_bugs(f)

    result = f.read_text()
    assert "Fixed crash A" not in result
    assert "Fixed crash B" not in result
    assert "Unfixed bug" in result
    assert "## Bugs" in result


def test_purge_completed_bugs_all_done(tmp_path):
    """When all bugs are fixed, only the header remains."""
    md = "## Bugs\n\n- [x] Fixed bug 1\n- [x] Fixed bug 2\n"
    f = tmp_path / "BUGS.md"
    f.write_text(md)
    purge_completed_bugs(f)

    result = f.read_text()
    assert "## Bugs" in result
    assert "Fixed bug" not in result


def test_purge_completed_bugs_no_checked(tmp_path):
    """purge_completed_bugs is a no-op when nothing is checked."""
    md = "## Bugs\n\n- [ ] Open bug A\n- [ ] Open bug B\n"
    f = tmp_path / "BUGS.md"
    f.write_text(md)
    before = f.read_text()
    purge_completed_bugs(f)
    after = f.read_text()
    # Content preserved (trailing newline normalization is OK)
    assert "Open bug A" in after
    assert "Open bug B" in after
    assert before.rstrip() == after.rstrip()


def test_purge_completed_bugs_keeps_prose(tmp_path):
    """Non-checkbox lines (prose, blank lines) in BUGS.md are kept."""
    md = "## Bugs\n\nThese are notes about bugs.\n- [x] Fixed one\n- [ ] Open one\n"
    f = tmp_path / "BUGS.md"
    f.write_text(md)
    purge_completed_bugs(f)

    result = f.read_text()
    assert "These are notes about bugs." in result
    assert "Fixed one" not in result
    assert "Open one" in result


def test_purge_completed_bugs_with_subtasks(tmp_path):
    """Checked subtasks under a checked parent are all removed."""
    md = (
        "## Bugs\n"
        "- [x] Fix crash group\n"
        "   - [x] Fix part A\n"
        "   - [x] Fix part B\n"
        "- [ ] Unfixed bug\n"
    )
    f = tmp_path / "BUGS.md"
    f.write_text(md)
    purge_completed_bugs(f)

    result = f.read_text()
    assert "Fix crash group" not in result
    assert "Fix part A" not in result
    assert "Fix part B" not in result
    assert "Unfixed bug" in result


# ── structural sanity check (PlanCorruptionError) ──


def test_parse_clean_plan_passes_sanity_check(tmp_path):
    """A well-formed multi-phase plan with one Bugs section parses normally."""
    md = (
        "# Duplo\n"
        "\n"
        "Project description here.\n"
        "\n"
        "## Bugs\n"
        "\n"
        "# Phase 1: Bootstrapping\n"
        "- [x] Setup\n"
        "# Phase 2: Build\n"
        "- [ ] Implement\n"
    )
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    # Should not raise.
    tasks = parse(f)
    assert any(t.text == "Implement" for t in tasks)


def test_parse_detects_duplicate_top_header(tmp_path):
    """Two `# Duplo` headers (the canonical corruption signature) are flagged."""
    import pytest

    md = (
        "# Duplo\n"
        "Some intro.\n"
        "# Phase 1: Setup\n"
        "- [x] Done\n"
        "# Duplo\n"  # duplicated top header partway through
        "More content.\n"
        "- [ ] Stranded task\n"
    )
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    with pytest.raises(PlanCorruptionError) as exc_info:
        parse(f)
    msg = str(exc_info.value)
    assert "duplicate top-level heading" in msg
    assert "# Duplo" in msg
    # Both line numbers (1-indexed) should appear.
    assert "1" in msg and "5" in msg


def test_parse_detects_multiple_bugs_sections(tmp_path):
    """Two `## Bugs` sections in the same file are flagged."""
    import pytest

    md = (
        "## Bugs\n"
        "- [ ] First bug\n"
        "# Phase 1: Setup\n"
        "- [x] Done\n"
        "## Bugs\n"  # duplicated bugs section
        "- [ ] Second bug\n"
    )
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    with pytest.raises(PlanCorruptionError) as exc_info:
        parse(f)
    msg = str(exc_info.value)
    assert "multiple Bugs sections" in msg


def test_parse_detects_duplicate_phase_number(tmp_path):
    """Two stage headers with the same Phase number are flagged."""
    import pytest

    md = (
        "# Phase 1: First attempt\n"
        "- [x] Done\n"
        "# Phase 2: Real work\n"
        "- [x] Also done\n"
        "# Phase 2: Second attempt at phase 2\n"  # duplicate Phase 2
        "- [ ] Conflicted\n"
    )
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    with pytest.raises(PlanCorruptionError) as exc_info:
        parse(f)
    msg = str(exc_info.value)
    assert "duplicate Phase/Stage 2" in msg


def test_parse_detects_duplicate_stage_number_mixed_levels(tmp_path):
    """Duplicate stage number at different heading levels still flagged."""
    import pytest

    md = (
        "## Stage 1: Build\n"
        "- [x] Done\n"
        "### Stage 1: Polish\n"  # different heading level, same Stage number
        "- [ ] Pending\n"
    )
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    with pytest.raises(PlanCorruptionError) as exc_info:
        parse(f)
    assert "duplicate Phase/Stage 1" in str(exc_info.value)


def test_parse_reports_all_anomalies_in_one_error(tmp_path):
    """All structural problems are reported in a single exception."""
    import pytest

    md = (
        "# Duplo\n"
        "## Bugs\n"
        "# Phase 1: A\n"
        "- [ ] T\n"
        "# Duplo\n"  # duplicate H1
        "## Bugs\n"  # duplicate Bugs
        "# Phase 1: B\n"  # duplicate Phase 1
    )
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    with pytest.raises(PlanCorruptionError) as exc_info:
        parse(f)
    msg = str(exc_info.value)
    assert "duplicate top-level heading" in msg
    assert "multiple Bugs sections" in msg
    assert "duplicate Phase/Stage 1" in msg


def test_parse_check_structure_false_skips_check(tmp_path):
    """check_structure=False bypasses the sanity check entirely."""
    md = (
        "# Duplo\n"
        "# Duplo\n"  # duplicate that would otherwise raise
        "- [ ] Task\n"
    )
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    # Should not raise; should parse the task.
    tasks = parse(f, check_structure=False)
    assert len(tasks) == 1
    assert tasks[0].text == "Task"


def test_parse_phase_header_does_not_count_as_h1_duplicate(tmp_path):
    """`# Phase 1` and `# Phase 2` must not be flagged as duplicate H1s.

    They are both H1 headings but with different titles, AND they are
    classified as stage headers (not plain H1s) by the sanity check.
    Two phase headers with different numbers are normal structure.
    """
    md = "# Phase 1: First\n- [x] Done\n# Phase 2: Second\n- [ ] Pending\n"
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    # Should not raise.
    tasks = parse(f)
    assert len(tasks) == 2


def test_parse_single_bugs_section_passes(tmp_path):
    """Exactly one `## Bugs` section is normal and must pass."""
    md = "## Bugs\n- [ ] Open\n# Phase 1: Work\n- [ ] Task\n"
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    tasks = parse(f)
    # Both sections produce tasks.
    assert any(t.stage == "Bugs" for t in tasks)
    assert any("Phase 1" in t.stage for t in tasks)


def test_parse_error_message_includes_path(tmp_path):
    """PlanCorruptionError message names the offending file."""
    import pytest

    md = "# X\n# X\n"
    f = tmp_path / "PLAN.md"
    f.write_text(md)
    with pytest.raises(PlanCorruptionError) as exc_info:
        parse(f)
    assert str(f) in str(exc_info.value)


def test_clear_failed_markers_flips_bang_to_space(tmp_path):
    f = tmp_path / "plan.md"
    f.write_text("- [ ] A\n- [!] B\n- [x] C\n- [!] D\n")
    n = clear_failed_markers(f)
    assert n == 2
    assert f.read_text() == "- [ ] A\n- [ ] B\n- [x] C\n- [ ] D\n"


def test_clear_failed_markers_preserves_indentation(tmp_path):
    f = tmp_path / "plan.md"
    f.write_text("- [ ] Parent\n  - [!] Child\n    - [!] Grandchild\n")
    n = clear_failed_markers(f)
    assert n == 2
    assert f.read_text() == "- [ ] Parent\n  - [ ] Child\n    - [ ] Grandchild\n"


def test_clear_failed_markers_returns_zero_on_missing_file(tmp_path):
    missing = tmp_path / "does_not_exist.md"
    assert clear_failed_markers(missing) == 0


def test_clear_failed_markers_does_not_corrupt_non_checkbox_lines(tmp_path):
    """A non-checkbox line that happens to contain the literal "- [!]"
    sequence (e.g. inside prose or a code block) must not be rewritten.
    """
    f = tmp_path / "plan.md"
    original = (
        "- [!] real failed task\n"
        "Sample diff line: `- [!]` should stay verbatim.\n"
        "Note: avoid `- [!]` in templates.\n"
        "  - [!] indented real failed task\n"
    )
    f.write_text(original)
    n = clear_failed_markers(f)
    assert n == 2  # only the two real checkbox lines flipped
    result = f.read_text()
    assert result.splitlines()[0] == "- [ ] real failed task"
    assert result.splitlines()[1] == "Sample diff line: `- [!]` should stay verbatim."
    assert result.splitlines()[2] == "Note: avoid `- [!]` in templates."
    assert result.splitlines()[3] == "  - [ ] indented real failed task"


def test_clear_failed_markers_no_op_preserves_mtime(tmp_path):
    f = tmp_path / "plan.md"
    f.write_text("- [ ] A\n- [x] B\n")
    mtime_before = f.stat().st_mtime_ns
    # Forge a later mtime to detect whether a write occurred.
    import os

    os.utime(f, ns=(mtime_before, mtime_before))
    n = clear_failed_markers(f)
    assert n == 0
    # File should not have been rewritten since nothing changed.
    assert f.stat().st_mtime_ns == mtime_before
