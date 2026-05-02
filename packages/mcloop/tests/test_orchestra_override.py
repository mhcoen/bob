"""Tests for the project-local Orchestra override banner and ack flow.

Covers ``mcloop.orchestra_override`` (fingerprint, ack file IO, banner
content), the run-time banner emission in ``mcloop.code_edit``, the
``ack-orchestra-override`` subcommand handler in ``mcloop.main``, and
the install-time override detection in ``mcloop.install_cmd``. The
existing ``tests/test_code_edit_wrapper.py`` covers the legacy
substring assertions on the run-time path; this file owns the
ack-aware behavior and the banner content contract.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from mcloop.code_edit import _select_backend
from mcloop.install_cmd import _check_orchestra_override
from mcloop.orchestra_override import (
    ACK_FILENAME,
    ack_path,
    banner_lines,
    banner_text,
    fingerprint,
    is_acknowledged,
    project_orchestra_config_path,
    read_ack,
    write_ack,
)

# --------------------------------------------------------------------
# Test fixtures
# --------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_banner_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run-time banner is latched at module scope so a single
    process emits it once. Tests reset the latch so each case starts
    in a clean state, otherwise an earlier test's emission would
    suppress every later test's expected emission."""
    monkeypatch.setattr(
        "mcloop.code_edit._PROJECT_OVERRIDE_NOTE_EMITTED", False
    )


@pytest.fixture(autouse=True)
def _isolate_orchestra_global_config(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    """Point ``orchestra.config.global_config_path`` at an empty tmp
    directory so the developer's real ``~/.orchestra/config.json`` does
    not influence test outcomes. Mirrors the fixture in
    ``tests/test_code_edit_wrapper.py``."""
    isolated_global = (
        tmp_path_factory.mktemp("isolated-orchestra-override") / "config.json"
    )
    monkeypatch.setattr(
        "orchestra.config.global_config_path", lambda: isolated_global
    )
    return isolated_global


def _make_project_with_override(
    tmp_path: Path, contents: dict[str, Any] | str
) -> tuple[Path, Path]:
    """Create ``tmp_path/project/.orchestra/config.json`` with
    ``contents``. Returns (project_dir, config_path)."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config_dir = project_dir / ".orchestra"
    config_dir.mkdir()
    config_path = config_dir / "config.json"
    if isinstance(contents, str):
        config_path.write_text(contents)
    else:
        config_path.write_text(json.dumps(contents))
    return project_dir, config_path


# --------------------------------------------------------------------
# orchestra_override module unit tests
# --------------------------------------------------------------------


def test_fingerprint_is_sha256_of_config_bytes(tmp_path: Path) -> None:
    """fingerprint() must hash the file's exact bytes so an edit
    produces a different digest. The recorded ack relies on this."""
    project_dir, config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    expected = hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert fingerprint(config_path) == expected


def test_ack_path_under_dot_mcloop_with_canonical_filename(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    assert ack_path(project_dir) == project_dir / ".mcloop" / ACK_FILENAME


def test_write_then_read_ack_round_trips(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    written = write_ack(project_dir, "deadbeef")
    assert written == ack_path(project_dir)
    assert read_ack(project_dir) == "deadbeef"


def test_read_ack_returns_none_when_missing(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    assert read_ack(project_dir) is None


def test_read_ack_returns_none_when_empty(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    (project_dir / ".mcloop").mkdir(parents=True)
    (project_dir / ".mcloop" / ACK_FILENAME).write_text("")
    assert read_ack(project_dir) is None


def test_is_acknowledged_false_without_config(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    fake_config = project_dir / ".orchestra" / "config.json"
    assert not is_acknowledged(project_dir, fake_config)


def test_is_acknowledged_true_when_fingerprint_matches(tmp_path: Path) -> None:
    project_dir, config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    write_ack(project_dir, fingerprint(config_path))
    assert is_acknowledged(project_dir, config_path)


def test_is_acknowledged_false_after_config_edit(tmp_path: Path) -> None:
    project_dir, config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    write_ack(project_dir, fingerprint(config_path))
    # Edit the config so the fingerprint changes.
    config_path.write_text(
        json.dumps({"workflows": {"code_edit": {"pattern": "single"}}})
    )
    assert not is_acknowledged(project_dir, config_path)


def test_banner_lines_contain_required_substrings(tmp_path: Path) -> None:
    """The banner must carry the four user-facing pieces: the alert
    label, the absolute config path, the override-warning sentence,
    and the ack-subcommand hint. Pinning these substrings catches
    accidental edits that drop any of the four."""
    _project_dir, config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    text = banner_text(config_path)
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" in text
    assert str(config_path.resolve()) in text
    assert "overrides ~/.orchestra/config.json" in text
    assert "mcloop ack-orchestra-override" in text
    # The banner is bracketed by a horizontal rule so it stands out
    # in long logs. Pin both edges.
    lines = banner_lines(config_path)
    rule = "=" * 60
    assert lines[0] == rule
    assert lines[-1] == rule


def test_project_orchestra_config_path_helper(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    expected = project_dir / ".orchestra" / "config.json"
    assert project_orchestra_config_path(project_dir) == expected


# --------------------------------------------------------------------
# Run-time banner: ack suppression, edit-then-refire
# --------------------------------------------------------------------


def test_banner_fires_when_override_present_and_no_ack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No ack file means the banner fires on the first call. Latched
    after, but the first emission lands."""
    project_dir, _config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    _select_backend(project_dir)
    err = capsys.readouterr().err
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" in err
    assert "mcloop ack-orchestra-override" in err


def test_banner_silenced_when_ack_matches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A matching ack fingerprint suppresses the banner entirely."""
    project_dir, config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    write_ack(project_dir, fingerprint(config_path))
    _select_backend(project_dir)
    err = capsys.readouterr().err
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" not in err


def test_banner_returns_after_config_edit_invalidates_ack(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Editing the config after acknowledging it changes the
    fingerprint; the banner returns until the user re-acks."""
    project_dir, config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    write_ack(project_dir, fingerprint(config_path))
    # First call: ack matches, banner silent.
    _select_backend(project_dir)
    capsys.readouterr()  # discard

    # Edit the local config, simulating the user reaching for it
    # without re-acking. Reset the latch as if a new mcloop run
    # started.
    config_path.write_text(
        json.dumps({"workflows": {"code_edit": {"pattern": "single"}}})
    )
    monkeypatch.setattr(
        "mcloop.code_edit._PROJECT_OVERRIDE_NOTE_EMITTED", False
    )
    _select_backend(project_dir)
    err = capsys.readouterr().err
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" in err


def test_banner_does_not_fire_when_no_override_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    _select_backend(project_dir)
    err = capsys.readouterr().err
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" not in err


# --------------------------------------------------------------------
# ack-orchestra-override subcommand handler
# --------------------------------------------------------------------


def test_ack_subcommand_writes_fingerprint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The handler writes a fresh ack file with the current
    fingerprint and prints a confirmation line."""
    project_dir, config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    expected = fingerprint(config_path)

    from mcloop.main import _cmd_ack_orchestra_override

    _cmd_ack_orchestra_override(project_dir)
    out = capsys.readouterr().out

    written = ack_path(project_dir)
    assert written.is_file()
    assert written.read_text(encoding="utf-8").strip() == expected
    assert "Acknowledged" in out
    assert str(config_path) in out


def test_ack_subcommand_exits_when_no_override_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No project-local override file means there is nothing to
    acknowledge; the handler must exit non-zero with a clear error
    so a user typing the command in the wrong project sees what's
    wrong."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    from mcloop.main import _cmd_ack_orchestra_override

    with pytest.raises(SystemExit) as exc_info:
        _cmd_ack_orchestra_override(project_dir)
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "no project-local" in err
    assert "nothing to acknowledge" in err


def test_ack_then_run_silences_banner_end_to_end(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round-trip: acknowledge, then call _select_backend on a fresh
    process latch and assert the banner is silent. This is the
    primary user flow."""
    project_dir, _config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )

    from mcloop.main import _cmd_ack_orchestra_override

    _cmd_ack_orchestra_override(project_dir)
    capsys.readouterr()  # discard ack confirmation
    monkeypatch.setattr(
        "mcloop.code_edit._PROJECT_OVERRIDE_NOTE_EMITTED", False
    )

    _select_backend(project_dir)
    err = capsys.readouterr().err
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" not in err


# --------------------------------------------------------------------
# Install-time check
# --------------------------------------------------------------------


def test_install_check_emits_banner_when_override_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``_check_orchestra_override`` prints the banner to stdout (the
    install summary surface) and returns a summary entry."""
    project_dir, _config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    result = _check_orchestra_override(project_dir, dry_run=False)
    out = capsys.readouterr().out
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" in out
    assert "mcloop ack-orchestra-override" in out
    assert result is not None
    component, status = result
    assert component == "Orchestra override"
    assert "detected" in status
    assert "ack-orchestra-override" in status


def test_install_check_silent_when_acknowledged(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When the user has already acknowledged the override, the
    install flow reports it as acknowledged and does not re-emit the
    banner."""
    project_dir, config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    write_ack(project_dir, fingerprint(config_path))
    result = _check_orchestra_override(project_dir, dry_run=False)
    out = capsys.readouterr().out
    assert "[orchestra] PROJECT-LOCAL OVERRIDE DETECTED" not in out
    assert result is not None
    component, status = result
    assert component == "Orchestra override"
    assert status == "acknowledged"


def test_install_check_returns_none_without_override(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    assert _check_orchestra_override(project_dir, dry_run=False) is None


def test_install_check_dry_run_does_not_write_ack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--dry-run`` install must not write the ack file. The banner
    still surfaces so the user sees what install would surface, but
    no filesystem change escapes."""
    project_dir, _config_path = _make_project_with_override(
        tmp_path, {"workflows": {"code_edit": {"pattern": "direct"}}}
    )
    result = _check_orchestra_override(project_dir, dry_run=True)
    capsys.readouterr()
    assert not ack_path(project_dir).exists()
    assert result is not None
    _component, status = result
    assert "(dry run)" in status
