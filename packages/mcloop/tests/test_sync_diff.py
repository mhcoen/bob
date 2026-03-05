"""Tests for sync diff and confirmation logic."""


from mcloop.main import _confirm_sync_changes, _show_diff


def test_show_diff_includes_added_lines(capsys):
    original = "- [ ] task one\n"
    proposed = "- [ ] task one\n- [x] task two\n"
    _show_diff(original, proposed)
    out = capsys.readouterr().out
    assert "+- [x] task two" in out


def test_show_diff_includes_removed_lines(capsys):
    original = "line a\nline b\n"
    proposed = "line a\n"
    _show_diff(original, proposed)
    out = capsys.readouterr().out
    assert "-line b" in out


def test_show_diff_uses_filename(capsys):
    _show_diff("a\n", "b\n", "MY_PLAN.md")
    out = capsys.readouterr().out
    assert "MY_PLAN.md" in out


def test_show_diff_empty_when_identical(capsys):
    _show_diff("same\n", "same\n")
    out = capsys.readouterr().out
    assert out == ""


def test_confirm_sync_changes_no_diff_returns_true(tmp_path, capsys):
    plan = tmp_path / "PLAN.md"
    result = _confirm_sync_changes(plan, "same\n", "same\n")
    assert result is True
    out = capsys.readouterr().out
    assert "No changes" in out


def test_confirm_sync_changes_accepted(tmp_path, capsys):
    plan = tmp_path / "PLAN.md"
    result = _confirm_sync_changes(plan, "old\n", "new\n", _input=lambda _: "y")
    assert result is True


def test_confirm_sync_changes_rejected(tmp_path, capsys):
    plan = tmp_path / "PLAN.md"
    result = _confirm_sync_changes(plan, "old\n", "new\n", _input=lambda _: "n")
    assert result is False


def test_confirm_sync_changes_default_rejects(tmp_path, capsys):
    plan = tmp_path / "PLAN.md"
    result = _confirm_sync_changes(plan, "old\n", "new\n", _input=lambda _: "")
    assert result is False


def test_confirm_sync_changes_shows_diff(tmp_path, capsys):
    plan = tmp_path / "PLAN.md"
    _confirm_sync_changes(plan, "old\n", "new\n", _input=lambda _: "n")
    out = capsys.readouterr().out
    assert "-old" in out
    assert "+new" in out


def test_confirm_sync_changes_uses_checklist_name(tmp_path, capsys):
    plan = tmp_path / "TASKS.md"
    _confirm_sync_changes(plan, "a\n", "b\n", _input=lambda _: "n")
    out = capsys.readouterr().out
    assert "TASKS.md" in out
