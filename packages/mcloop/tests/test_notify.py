"""Tests for mcloop.notify."""

from unittest.mock import patch

from mcloop.notify import _escape_applescript, _load_env, notify


def test_escape_applescript():
    assert _escape_applescript('say "hi"') == 'say \\"hi\\"'
    assert _escape_applescript("back\\slash") == "back\\\\slash"


@patch("mcloop.notify._send_telegram")
@patch.dict("os.environ", {"MCLOOP_IMESSAGE": ""}, clear=False)
def test_notify_defaults_to_telegram(mock_tg):
    notify("test message")
    mock_tg.assert_called_once()


@patch("mcloop.notify._send_imessage")
@patch.dict("os.environ", {"MCLOOP_IMESSAGE": "1"}, clear=False)
def test_notify_imessage_when_configured(mock_imsg):
    notify("test message")
    mock_imsg.assert_called_once()


@patch("mcloop.notify._send_telegram")
@patch.dict("os.environ", {"MCLOOP_IMESSAGE": ""}, clear=False)
def test_notify_error_prefix(mock_tg):
    notify("bad thing", level="error")
    call_text = mock_tg.call_args[0][0]
    assert "ERROR:" in call_text


@patch("mcloop.notify._send_telegram")
@patch.dict("os.environ", {"MCLOOP_IMESSAGE": ""}, clear=False)
def test_notify_warning_prefix(mock_tg):
    notify("low disk", level="warning")
    tg_text = mock_tg.call_args[0][0]
    assert "Warning:" in tg_text


@patch("mcloop.notify._send_telegram")
@patch.dict("os.environ", {"MCLOOP_IMESSAGE": ""}, clear=False)
def test_notify_info_no_prefix(mock_tg):
    notify("task done", level="info")
    assert mock_tg.call_args[0][0] == "*McLoop* task done"


@patch("mcloop.notify._send_imessage")
@patch.dict("os.environ", {"MCLOOP_IMESSAGE": "1"}, clear=False)
def test_notify_imessage_info_no_prefix(mock_imsg):
    notify("task done", level="info")
    assert mock_imsg.call_args[0][0] == "McLoop: task done"


@patch("mcloop.notify._send_imessage")
@patch("mcloop.notify._send_telegram")
@patch.dict("os.environ", {"MCLOOP_IMESSAGE": ""}, clear=False)
def test_telegram_mode_does_not_send_imessage(mock_tg, mock_imsg):
    notify("test")
    mock_imsg.assert_not_called()


@patch("mcloop.notify._send_telegram")
@patch("mcloop.notify._send_imessage")
@patch.dict("os.environ", {"MCLOOP_IMESSAGE": "1"}, clear=False)
def test_imessage_mode_does_not_send_telegram(mock_imsg, mock_tg):
    notify("test")
    mock_tg.assert_not_called()


@patch("mcloop.notify.subprocess.run")
@patch(
    "mcloop.notify._get_config",
    return_value=("", "", "mhcoen@gmail.com"),
)
def test_imessage_uses_chat_id_format(mock_config, mock_run, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from mcloop.notify import _send_imessage

    _send_imessage("hello")
    mock_run.assert_called_once()
    script = mock_run.call_args[0][0][2]  # osascript -e <script>
    assert 'to chat id "any;-;mhcoen@gmail.com"' in script
    assert "hello" in script


@patch("mcloop.notify.subprocess.run")
@patch("mcloop.notify._get_config", return_value=("", "", ""))
def test_imessage_skips_when_no_id(mock_config, mock_run):
    from mcloop.notify import _send_imessage

    _send_imessage("hello")
    mock_run.assert_not_called()


@patch("mcloop.notify.urllib.request.urlopen")
@patch(
    "mcloop.notify._get_config",
    return_value=("fake-token", "12345", ""),
)
def test_telegram_sends_to_correct_url(mock_config, mock_urlopen, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from mcloop.notify import _send_telegram

    _send_telegram("hello")
    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    assert "fake-token" in req.full_url
    assert b"12345" in req.data
    assert b"hello" in req.data


@patch("mcloop.notify.urllib.request.urlopen")
@patch("mcloop.notify._get_config", return_value=("", "12345", ""))
def test_telegram_skips_when_no_token(mock_config, mock_urlopen):
    from mcloop.notify import _send_telegram

    _send_telegram("hello")
    mock_urlopen.assert_not_called()


def test_load_env_parses_key_values(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("KEY1=value1\nKEY2=value2\n# comment\n\nKEY3=val=ue3\n")
    with patch("mcloop.notify.ENV_FILE", env_file):
        vals = _load_env()
    assert vals == {"KEY1": "value1", "KEY2": "value2", "KEY3": "val=ue3"}


def test_load_env_missing_file(tmp_path):
    with patch("mcloop.notify.ENV_FILE", tmp_path / "nonexistent"):
        vals = _load_env()
    assert vals == {}


def test_escape_applescript_empty():
    assert _escape_applescript("") == ""


def test_escape_applescript_no_special():
    assert _escape_applescript("hello world") == "hello world"
