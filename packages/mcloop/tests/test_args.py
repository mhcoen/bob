"""Unit tests for CLI argument parsing."""

from unittest.mock import patch

import pytest

from mcloop.main import _parse_args


def _parse(*argv):
    with patch("sys.argv", ["mcloop", *argv]):
        return _parse_args()


def test_defaults():
    args = _parse()
    assert args.file == "PLAN.md"
    assert args.dry_run is False
    assert args.max_retries == 3
    assert args.model is None
    assert args.command is None


def test_file_flag():
    args = _parse("--file", "tasks.md")
    assert args.file == "tasks.md"


def test_dry_run_flag():
    args = _parse("--dry-run")
    assert args.dry_run is True


def test_max_retries_flag():
    args = _parse("--max-retries", "5")
    assert args.max_retries == 5


def test_model_flag():
    args = _parse("--model", "opus")
    assert args.model == "opus"


def test_sync_subcommand():
    args = _parse("sync")
    assert args.command == "sync"


def test_sync_subcommand_with_file():
    args = _parse("--file", "custom.md", "sync")
    assert args.command == "sync"
    assert args.file == "custom.md"


def test_audit_subcommand():
    args = _parse("audit")
    assert args.command == "audit"


def test_audit_subcommand_with_file():
    args = _parse("--file", "custom.md", "audit")
    assert args.command == "audit"
    assert args.file == "custom.md"


def test_no_subcommand_command_is_none():
    args = _parse("--dry-run")
    assert args.command is None


def test_invalid_subcommand_exits():
    with pytest.raises(SystemExit):
        _parse("bogus")
