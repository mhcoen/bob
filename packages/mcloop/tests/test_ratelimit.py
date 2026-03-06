"""Tests for loop.ratelimit."""

import time
from unittest.mock import patch

from mcloop.ratelimit import (
    SESSION_LIMIT_POLL,
    RateLimitState,
    get_available_cli,
    is_rate_limited,
    is_session_limited,
    wait_for_reset,
)


def test_is_rate_limited_detects_patterns():
    assert is_rate_limited("Error: rate limit exceeded", 1)
    assert is_rate_limited("HTTP 429 Too Many Requests", 1)
    assert is_rate_limited("usage limit reached", 1)


def test_is_rate_limited_case_insensitive():
    assert is_rate_limited("RATE LIMIT exceeded", 1)
    assert is_rate_limited("Quota Exceeded", 1)


def test_is_rate_limited_detects_all_patterns():
    assert is_rate_limited("rate_limit_error", 1)
    assert is_rate_limited("too many requests", 1)
    assert is_rate_limited("quota exceeded", 1)
    assert is_rate_limited("capacity reached", 1)


def test_is_rate_limited_ignores_success():
    assert not is_rate_limited("rate limit", 0)


def test_is_rate_limited_no_match():
    assert not is_rate_limited("some other error", 1)


def test_rate_limit_state():
    state = RateLimitState()
    assert not state.is_limited("claude")

    state.mark_limited("claude", cooldown=1)
    assert state.is_limited("claude")

    time.sleep(1.1)
    assert not state.is_limited("claude")


def test_seconds_until_reset_none_when_clear():
    state = RateLimitState()
    assert state.seconds_until_reset() is None


def test_seconds_until_reset_returns_minimum():
    state = RateLimitState()
    state.mark_limited("claude", cooldown=100)
    state.mark_limited("codex", cooldown=50)

    secs = state.seconds_until_reset()
    assert secs is not None
    assert 40 < secs <= 50


def test_get_available_cli_fallover():
    state = RateLimitState()
    assert get_available_cli(state) == "claude"

    state.mark_limited("claude")
    assert get_available_cli(state) == "codex"

    state.mark_limited("codex")
    assert get_available_cli(state) is None


def test_get_available_cli_respects_enabled_clis():
    state = RateLimitState()
    assert get_available_cli(state, enabled_clis=("claude",)) == "claude"

    state.mark_limited("claude")
    # codex not in enabled_clis, so returns None
    assert get_available_cli(state, enabled_clis=("claude",)) is None


def test_get_available_cli_preferred():
    state = RateLimitState()
    assert get_available_cli(state, preferred="codex") == "codex"


def test_wait_for_reset_returns_when_available():
    state = RateLimitState()
    state.mark_limited("claude", cooldown=0)  # already expired

    with patch("mcloop.ratelimit.time.sleep"):
        cli = wait_for_reset(state)
    assert cli == "claude"


def test_wait_for_reset_respects_enabled_clis():
    state = RateLimitState()
    state.mark_limited("claude", cooldown=0)

    with patch("mcloop.ratelimit.time.sleep"):
        cli = wait_for_reset(state, enabled_clis=("claude",))
    assert cli == "claude"


def test_session_limit_poll_constant():
    assert SESSION_LIMIT_POLL == 600


def test_is_session_limited_detects_patterns():
    assert is_session_limited("credit balance is too low", 1)
    assert is_session_limited("you've hit your limit", 1)
    assert is_session_limited("exceeded your plan limits", 1)
    assert is_session_limited("usage cap reached", 1)


def test_is_session_limited_ignores_success():
    assert not is_session_limited("credit balance is too low", 0)


def test_is_session_limited_no_match():
    assert not is_session_limited("some other error", 1)


def test_wait_for_reset_calls_notify():
    state = RateLimitState()
    state.mark_limited("claude", cooldown=0)
    notifications = []

    def fake_notify(msg, level="info"):
        notifications.append((msg, level))

    with patch("mcloop.ratelimit.time.sleep"):
        wait_for_reset(state, notify_fn=fake_notify)

    assert any("Pausing" in msg for msg, _ in notifications)
    assert any("Resuming" in msg for msg, _ in notifications)
