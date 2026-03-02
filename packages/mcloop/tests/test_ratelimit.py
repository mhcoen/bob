"""Tests for loop.ratelimit."""

import time

from loop.ratelimit import RateLimitState, get_available_cli, is_rate_limited


def test_is_rate_limited_detects_patterns():
    assert is_rate_limited("Error: rate limit exceeded", 1)
    assert is_rate_limited("HTTP 429 Too Many Requests", 1)
    assert is_rate_limited("usage limit reached", 1)


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


def test_get_available_cli_fallover():
    state = RateLimitState()
    assert get_available_cli(state) == "claude"

    state.mark_limited("claude")
    assert get_available_cli(state) == "codex"

    state.mark_limited("codex")
    assert get_available_cli(state) is None
