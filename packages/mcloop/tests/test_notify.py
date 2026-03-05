"""Tests for loop.notify."""

from unittest.mock import patch

from mcloop.notify import _escape_applescript, notify


def test_escape_applescript():
    assert _escape_applescript('say "hi"') == 'say \\"hi\\"'
    assert _escape_applescript("back\\slash") == "back\\\\slash"


@patch("mcloop.notify._send_telegram")
@patch("mcloop.notify._send_imessage")
def test_notify_calls_both(mock_imsg, mock_tg):
    notify("test message")
    mock_tg.assert_called_once()
    mock_imsg.assert_called_once()


@patch("mcloop.notify._send_telegram")
@patch("mcloop.notify._send_imessage")
def test_notify_error_prefix(mock_imsg, mock_tg):
    notify("bad thing", level="error")
    call_text = mock_tg.call_args[0][0]
    assert "ERROR:" in call_text


@patch("mcloop.notify.subprocess.run")
@patch("mcloop.notify._IMESSAGE_ID", "mhcoen@gmail.com")
def test_imessage_uses_chat_id_format(mock_run):
    from mcloop.notify import _send_imessage

    _send_imessage("hello")
    mock_run.assert_called_once()
    script = mock_run.call_args[0][0][2]  # osascript -e <script>
    assert 'to chat id "any;-;mhcoen@gmail.com"' in script
    assert "hello" in script


@patch("mcloop.notify.subprocess.run")
@patch("mcloop.notify._IMESSAGE_ID", "")
def test_imessage_skips_when_no_id(mock_run):
    from mcloop.notify import _send_imessage

    _send_imessage("hello")
    mock_run.assert_not_called()


@patch("mcloop.notify.urllib.request.urlopen")
@patch("mcloop.notify._BOT_TOKEN", "fake-token")
@patch("mcloop.notify._CHAT_ID", "12345")
def test_telegram_sends_to_correct_url(mock_urlopen):
    from mcloop.notify import _send_telegram

    _send_telegram("hello")
    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    assert "fake-token" in req.full_url
    assert b"12345" in req.data
    assert b"hello" in req.data


@patch("mcloop.notify.urllib.request.urlopen")
@patch("mcloop.notify._BOT_TOKEN", "")
@patch("mcloop.notify._CHAT_ID", "12345")
def test_telegram_skips_when_no_token(mock_urlopen):
    from mcloop.notify import _send_telegram

    _send_telegram("hello")
    mock_urlopen.assert_not_called()


@patch("mcloop.notify._send_telegram")
@patch("mcloop.notify._send_imessage")
def test_notify_warning_prefix(mock_imsg, mock_tg):
    notify("low disk", level="warning")
    tg_text = mock_tg.call_args[0][0]
    assert "Warning:" in tg_text
    imsg_text = mock_imsg.call_args[0][0]
    assert "Warning:" in imsg_text


@patch("mcloop.notify._send_telegram")
@patch("mcloop.notify._send_imessage")
def test_notify_info_no_prefix(mock_imsg, mock_tg):
    notify("task done", level="info")
    tg_text = mock_tg.call_args[0][0]
    assert "*Loop* task done" == tg_text
    imsg_text = mock_imsg.call_args[0][0]
    assert "Loop: task done" == imsg_text
