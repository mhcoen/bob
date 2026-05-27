from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from mcloop import runner


@pytest.fixture(autouse=True)
def _reset_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    runner._reset_subscription_preflight_for_tests()
    monkeypatch.setattr("mcloop.install_cmd._load_mcloop_config", lambda: {})


def test_subscription_preflight_fails_on_not_logged_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}

    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            1,
            stdout='{"type":"system","subtype":"init"}\n',
            stderr="Not logged in · Please run /login\n",
        )

    monkeypatch.setattr("mcloop.runner.subprocess.run", _fake_run)

    with pytest.raises(runner.SubscriptionPreflightError) as excinfo:
        runner.ensure_subscription_preflight(
            cli="claude",
            model="opus",
            env=env,
            cwd=tmp_path,
        )

    assert excinfo.value.exit_code == runner.SUBSCRIPTION_PREFLIGHT_EXIT_CODE
    message = str(excinfo.value)
    assert "Not logged in" in message
    assert "Please run /login" in message
    assert "Exit code: 1" in message


def test_subscription_preflight_accepts_valid_stream_result_and_caches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    calls = 0

    def _fake_run(*args, **kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout=(
                '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n'
                '{"type":"result","subtype":"success","is_error":false}\n'
            ),
            stderr="",
        )

    monkeypatch.setattr("mcloop.runner.subprocess.run", _fake_run)

    runner.ensure_subscription_preflight(
        cli="claude",
        model="opus",
        env=env,
        cwd=tmp_path,
    )
    runner.ensure_subscription_preflight(
        cli="claude",
        model="opus",
        env=env,
        cwd=tmp_path,
    )

    assert calls == 1


def test_subscription_preflight_rejects_exit_zero_without_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}

    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout='{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}\n',
            stderr="",
        )

    monkeypatch.setattr("mcloop.runner.subprocess.run", _fake_run)

    with pytest.raises(runner.SubscriptionPreflightError) as excinfo:
        runner.ensure_subscription_preflight(
            cli="claude",
            model="opus",
            env=env,
            cwd=tmp_path,
        )

    assert "No stream-json result was produced" in str(excinfo.value)


def test_subscription_preflight_rejects_error_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}

    def _fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args[0],
            0,
            stdout='{"type":"result","subtype":"error","is_error":true}\n',
            stderr="",
        )

    monkeypatch.setattr("mcloop.runner.subprocess.run", _fake_run)

    with pytest.raises(runner.SubscriptionPreflightError):
        runner.ensure_subscription_preflight(
            cli="claude",
            model="opus",
            env=env,
            cwd=tmp_path,
        )


@pytest.mark.parametrize(
    ("cli", "model", "env_extra", "config"),
    [
        ("codex", "gpt-5.4", {}, {}),
        ("claude", "opus", {"ANTHROPIC_API_KEY": "sk-test"}, {"billing": "api"}),
        (
            "claude",
            "opus",
            {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"},
            {"billing": "openrouter"},
        ),
        (
            "claude",
            "kimi-k2.6",
            {"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"},
            {},
        ),
    ],
)
def test_subscription_preflight_skips_non_subscription_claude_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cli: str,
    model: str,
    env_extra: dict[str, str],
    config: dict[str, str],
) -> None:
    monkeypatch.setattr("mcloop.install_cmd._load_mcloop_config", lambda: config)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    env.update(env_extra)

    runner.ensure_subscription_preflight(
        cli=cli,
        model=model,
        env=env,
        cwd=tmp_path,
    )


def test_decode_subprocess_output_handles_bytes() -> None:
    assert runner._decode_subprocess_output(None) == ""
    assert runner._decode_subprocess_output("hello") == "hello"
    assert runner._decode_subprocess_output(b"hello") == "hello"
    # Undecodable bytes must not crash; errors="replace" is the contract.
    assert runner._decode_subprocess_output(b"a\xffb") == "a�b"


def test_preflight_timeout_with_bytes_stdout_raises_preflight_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}

    def _fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=args[0],
            timeout=runner.SUBSCRIPTION_PREFLIGHT_TIMEOUT,
            output=b"partial stdout\n",
            stderr=b"partial stderr\n",
        )

    monkeypatch.setattr("mcloop.runner.subprocess.run", _fake_run)

    with pytest.raises(runner.SubscriptionPreflightError) as excinfo:
        runner.ensure_subscription_preflight(
            cli="claude",
            model="opus",
            env=env,
            cwd=tmp_path,
        )

    assert "timed out" in str(excinfo.value)
    assert "partial stdout" in excinfo.value.output
    assert "partial stderr" in excinfo.value.output
