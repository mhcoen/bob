"""Tests for loop.notify."""

from unittest.mock import patch

from loop.notify import _escape_applescript, notify


def test_escape_applescript():
    assert _escape_applescript('say "hi"') == 'say \\"hi\\"'
    assert _escape_applescript("back\\slash") == "back\\\\slash"


@patch("loop.notify._send_telegram")
@patch("loop.notify._send_imessage")
def test_notify_calls_both(mock_imsg, mock_tg):
    notify("test message")
    mock_tg.assert_called_once()
    mock_imsg.assert_called_once()


@patch("loop.notify._send_telegram")
@patch("loop.notify._send_imessage")
def test_notify_error_prefix(mock_imsg, mock_tg):
    notify("bad thing", level="error")
    call_text = mock_tg.call_args[0][0]
    assert "ERROR:" in call_text
