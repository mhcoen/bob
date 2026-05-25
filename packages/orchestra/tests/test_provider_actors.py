"""Tests for the provider-routing actor bindings.

Covers the kimi/deepseek bindings introduced in the F2.5 actor-registry
workstream: env construction (base_url, auth token, model slug,
CLAUDE_CONFIG_DIR), API-key isolation (no ANTHROPIC_API_KEY leak), and
retry-on-throttle behavior against Cloudflare 403/429 markers.

The fail-fast credential check is also exercised: instantiating a
provider-routing adapter with the auth_token_env unset raises
``ProviderCredentialError`` before any subprocess can spawn.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from orchestra.adapters._subprocess import (
    apply_provider_env,
    build_session_env,
)
from orchestra.adapters.claude_code_text import (
    ClaudeCodeTextAdapter,
    ProviderCredentialError,
    _looks_throttled,
)

# --------------------------------------------------------------------
# apply_provider_env: claude_config_dir + use_slug_model
# --------------------------------------------------------------------


def test_apply_provider_env_kimi_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test-moonshot-1234567890")
    env: dict[str, str] = {}
    apply_provider_env(
        env,
        "kimi-k2.6",
        {
            "base_url": "https://api.moonshot.ai/anthropic/",
            "auth_token_env": "MOONSHOT_API_KEY",
            "claude_config_dir": "~/.claude-kimi",
            "use_slug_model": False,
        },
    )
    assert env["ANTHROPIC_BASE_URL"] == "https://api.moonshot.ai/anthropic/"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-moonshot-1234567890"
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["ANTHROPIC_MODEL"] == "kimi-k2.6"
    assert "moonshotai/" not in env["ANTHROPIC_MODEL"]
    assert env["CLAUDE_CONFIG_DIR"] == os.path.expanduser("~/.claude-kimi")


def test_apply_provider_env_deepseek_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-deepseek-9876")
    env: dict[str, str] = {}
    apply_provider_env(
        env,
        "deepseek-v4-pro",
        {
            "base_url": "https://api.deepseek.com/anthropic",
            "auth_token_env": "DEEPSEEK_API_KEY",
            "claude_config_dir": "~/.claude-deepseek",
            "use_slug_model": False,
        },
    )
    assert env["ANTHROPIC_BASE_URL"] == "https://api.deepseek.com/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test-deepseek-9876"
    assert env["ANTHROPIC_MODEL"] == "deepseek-v4-pro"
    assert "deepseek/" not in env["ANTHROPIC_MODEL"]
    assert env["CLAUDE_CONFIG_DIR"] == os.path.expanduser("~/.claude-deepseek")


def test_apply_provider_env_default_openrouter_still_uses_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default path (no executor config) still slug-prefixes for OpenRouter."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    env: dict[str, str] = {}
    apply_provider_env(env, "kimi-k2.6", None)
    assert env["ANTHROPIC_MODEL"] == "moonshotai/kimi-k2.6"
    assert "CLAUDE_CONFIG_DIR" not in env


def test_apply_provider_env_anthropic_native_is_noop() -> None:
    """Native Anthropic models pass through untouched."""
    env: dict[str, str] = {}
    apply_provider_env(env, "sonnet", {"base_url": "https://api.moonshot.ai/anthropic/"})
    assert "ANTHROPIC_BASE_URL" not in env


# --------------------------------------------------------------------
# build_session_env: ANTHROPIC_API_KEY isolation
# --------------------------------------------------------------------


def test_build_session_env_strips_anthropic_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ANTHROPIC_API_KEY in parent env must NOT reach a kimi subprocess.

    PASSTHROUGH_VARS does not include ANTHROPIC_API_KEY, so the strip
    is automatic; apply_provider_env then sets it to empty string as
    belt-and-suspenders. Verify both layers.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-be-stripped")
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-moon-test")
    env = build_session_env(
        cli="claude",
        model="kimi-k2.6",
        executor_config={
            "base_url": "https://api.moonshot.ai/anthropic/",
            "auth_token_env": "MOONSHOT_API_KEY",
            "claude_config_dir": "~/.claude-kimi",
            "use_slug_model": False,
        },
    )
    # Empty string (set by apply_provider_env) — never the parent value.
    assert env.get("ANTHROPIC_API_KEY") == ""
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-moon-test"
    assert "sk-ant-should-be-stripped" not in env.values()


# --------------------------------------------------------------------
# Adapter init: fail-fast credential check
# --------------------------------------------------------------------


def test_kimi_adapter_init_fails_when_moonshot_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    with pytest.raises(ProviderCredentialError, match="MOONSHOT_API_KEY"):
        ClaudeCodeTextAdapter(
            default_model="kimi-k2.6",
            provider_config={
                "base_url": "https://api.moonshot.ai/anthropic/",
                "auth_token_env": "MOONSHOT_API_KEY",
                "claude_config_dir": "~/.claude-kimi",
                "use_slug_model": False,
            },
        )


def test_deepseek_adapter_init_fails_when_deepseek_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ProviderCredentialError, match="DEEPSEEK_API_KEY"):
        ClaudeCodeTextAdapter(
            default_model="deepseek-v4-pro",
            provider_config={
                "base_url": "https://api.deepseek.com/anthropic",
                "auth_token_env": "DEEPSEEK_API_KEY",
                "claude_config_dir": "~/.claude-deepseek",
                "use_slug_model": False,
            },
        )


def test_default_adapter_init_no_credential_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default claude_code_text adapter (no provider_config) does NOT
    require any provider credential; it relies on the user's claude
    login state. Fail-fast applies only to direct-provider bindings."""
    monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Should not raise.
    ClaudeCodeTextAdapter(default_model="sonnet")


# --------------------------------------------------------------------
# _looks_throttled marker matching
# --------------------------------------------------------------------


def test_looks_throttled_matches_403() -> None:
    assert _looks_throttled("Error: status 403 Forbidden")
    assert _looks_throttled("HTTP 403 Forbidden")


def test_looks_throttled_matches_429() -> None:
    assert _looks_throttled("Error: 429 Too Many Requests")
    assert _looks_throttled("status 429")


def test_looks_throttled_matches_rate_limit() -> None:
    assert _looks_throttled("rate limit exceeded")
    assert _looks_throttled("rate-limit hit")
    assert _looks_throttled("rate_limit reached")


def test_looks_throttled_misses_normal_output() -> None:
    assert not _looks_throttled("Hello world, here is your response.")
    assert not _looks_throttled("")
    assert not _looks_throttled("404 Not Found")


# --------------------------------------------------------------------
# Retry-on-throttle: integration with run_session
# --------------------------------------------------------------------


def test_retry_on_throttle_succeeds_on_second_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First call returns a 403 marker, second succeeds; final result
    is the second call's success."""
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
    adapter = ClaudeCodeTextAdapter(
        default_model="kimi-k2.6",
        provider_config={
            "base_url": "https://api.moonshot.ai/anthropic/",
            "auth_token_env": "MOONSHOT_API_KEY",
            "claude_config_dir": "~/.claude-kimi",
            "use_slug_model": False,
        },
        retry_on_throttle=True,
        max_retries=2,
        initial_backoff_s=0.0,
    )
    calls: list[int] = []

    def fake_run_session(*args: object, **kwargs: object) -> tuple[str, int]:
        calls.append(1)
        if len(calls) == 1:
            return "API error: status 403 Forbidden\n", 1
        return '{"type":"text","text":"ok"}\n', 0

    with mock.patch(
        "orchestra.adapters.claude_code_text.run_session", fake_run_session
    ):
        from pathlib import Path
        out, exit_code = adapter._run_with_optional_retry(
            ["claude", "-p"],
            Path("/tmp"),
            env={},
            timeout=60,
            stdin_bytes=b"hello",
        )
    assert len(calls) == 2
    assert exit_code == 0
    assert "ok" in out


def test_retry_on_throttle_gives_up_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All attempts return throttle markers; final output is the last
    failure (not a synthesized success)."""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    adapter = ClaudeCodeTextAdapter(
        default_model="deepseek-v4-pro",
        provider_config={
            "base_url": "https://api.deepseek.com/anthropic",
            "auth_token_env": "DEEPSEEK_API_KEY",
            "claude_config_dir": "~/.claude-deepseek",
            "use_slug_model": False,
        },
        retry_on_throttle=True,
        max_retries=3,
        initial_backoff_s=0.0,
    )
    calls: list[int] = []

    def fake_run_session(*args: object, **kwargs: object) -> tuple[str, int]:
        calls.append(1)
        return "Error: 429 too many requests\n", 1

    with mock.patch(
        "orchestra.adapters.claude_code_text.run_session", fake_run_session
    ):
        from pathlib import Path
        out, exit_code = adapter._run_with_optional_retry(
            ["claude", "-p"],
            Path("/tmp"),
            env={},
            timeout=60,
            stdin_bytes=b"hello",
        )
    # Initial call + max_retries retries = 4 total attempts.
    assert len(calls) == 1 + 3
    assert exit_code == 1
    assert "429" in out


def test_retry_disabled_passes_through_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default adapter (retry_on_throttle=False) does not retry."""
    adapter = ClaudeCodeTextAdapter(default_model="sonnet")
    calls: list[int] = []

    def fake_run_session(*args: object, **kwargs: object) -> tuple[str, int]:
        calls.append(1)
        return "API error: status 403 Forbidden\n", 1

    with mock.patch(
        "orchestra.adapters.claude_code_text.run_session", fake_run_session
    ):
        from pathlib import Path
        out, exit_code = adapter._run_with_optional_retry(
            ["claude", "-p"],
            Path("/tmp"),
            env={},
            timeout=60,
            stdin_bytes=b"hello",
        )
    assert len(calls) == 1
    assert exit_code == 1


def test_retry_returns_immediately_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """First-call success means no retry, even with retry_on_throttle."""
    monkeypatch.setenv("MOONSHOT_API_KEY", "sk-test")
    adapter = ClaudeCodeTextAdapter(
        default_model="kimi-k2.6",
        provider_config={
            "base_url": "https://api.moonshot.ai/anthropic/",
            "auth_token_env": "MOONSHOT_API_KEY",
            "claude_config_dir": "~/.claude-kimi",
            "use_slug_model": False,
        },
        retry_on_throttle=True,
        max_retries=3,
        initial_backoff_s=0.0,
    )
    calls: list[int] = []

    def fake_run_session(*args: object, **kwargs: object) -> tuple[str, int]:
        calls.append(1)
        return '{"type":"text","text":"first try"}\n', 0

    with mock.patch(
        "orchestra.adapters.claude_code_text.run_session", fake_run_session
    ):
        from pathlib import Path
        out, exit_code = adapter._run_with_optional_retry(
            ["claude", "-p"],
            Path("/tmp"),
            env={},
            timeout=60,
            stdin_bytes=b"hello",
        )
    assert len(calls) == 1
    assert exit_code == 0
