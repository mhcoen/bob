"""Tests for _preflight_chain tier demotion.

A tier that fails subscription preflight must be demoted (dropped from the
chain for this run) rather than aborting the whole run, as long as at least
one tier remains usable. The run aborts only when every tier fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcloop.main import ChainEntry, _preflight_chain
from mcloop.runner import SubscriptionPreflightError


@pytest.fixture(autouse=True)
def _stub_runner_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralise the env/command builders so only preflight decides usability."""
    import mcloop.runner as runner

    monkeypatch.setattr(runner, "_build_session_env", lambda cli: {})
    monkeypatch.setattr(
        runner,
        "_build_command",
        lambda *args, **kwargs: ["probe"],
    )


@pytest.fixture(autouse=True)
def notify_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Capture (message, level) for every notify() the preflight emits.

    Autouse so no test in this module ever reaches the real Telegram path.
    """
    import mcloop.main as main

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main,
        "notify",
        lambda message, level="info": calls.append((message, level)),
    )
    return calls


def _make_preflight(failing_models: set[str]):
    """Build an ensure_subscription_preflight stub that fails for given models."""

    def _fake(*, cli: str, model: str | None, env: dict, cwd: Path) -> None:
        if model in failing_models:
            raise SubscriptionPreflightError(
                f"{cli} subscription preflight failed before starting a task: {model} unavailable",
                output="raw probe output",
            )

    return _fake


def test_demotes_failing_tier_and_proceeds_on_survivor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Tier 2 fails preflight, tier 1 passes -> proceed on tier 1, warn, no raise."""
    import mcloop.runner as runner

    monkeypatch.setattr(
        runner,
        "ensure_subscription_preflight",
        _make_preflight({"gpt-5-codex"}),
    )
    chain = [
        ChainEntry(cli="claude", model="opus"),
        ChainEntry(cli="codex", model="gpt-5-codex"),
        ChainEntry(cli="claude", model="kimi-k2.6"),
    ]

    usable = _preflight_chain(chain, Path("/tmp"))

    assert [e.model for e in usable] == ["opus", "kimi-k2.6"]
    out = capsys.readouterr().out
    assert "Skipping chain tier 2 (codex/gpt-5-codex)" in out
    assert "preflight failed" in out


def test_all_tiers_fail_raises_listing_every_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every tier fails preflight -> SubscriptionPreflightError listing all."""
    import mcloop.runner as runner

    monkeypatch.setattr(
        runner,
        "ensure_subscription_preflight",
        _make_preflight({"opus", "gpt-5-codex", "kimi-k2.6"}),
    )
    chain = [
        ChainEntry(cli="claude", model="opus"),
        ChainEntry(cli="codex", model="gpt-5-codex"),
        ChainEntry(cli="claude", model="kimi-k2.6"),
    ]

    with pytest.raises(SubscriptionPreflightError) as excinfo:
        _preflight_chain(chain, Path("/tmp"))

    message = str(excinfo.value)
    assert "No usable model chain tier" in message
    assert "tier 1 (claude/opus)" in message
    assert "tier 2 (codex/gpt-5-codex)" in message
    assert "tier 3 (claude/kimi-k2.6)" in message
    # Aggregated raw output from the failing probes is preserved.
    assert "raw probe output" in excinfo.value.output


def test_surviving_tiers_preserve_chain_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The middle tier fails; survivors keep their original relative order."""
    import mcloop.runner as runner

    monkeypatch.setattr(
        runner,
        "ensure_subscription_preflight",
        _make_preflight({"middle"}),
    )
    chain = [
        ChainEntry(cli="claude", model="first"),
        ChainEntry(cli="codex", model="middle"),
        ChainEntry(cli="claude", model="last"),
    ]

    usable = _preflight_chain(chain, Path("/tmp"))

    assert [(e.cli, e.model) for e in usable] == [
        ("claude", "first"),
        ("claude", "last"),
    ]


def test_demotion_fires_warning_notify_naming_skipped_and_survivors(
    monkeypatch: pytest.MonkeyPatch,
    notify_calls: list[tuple[str, str]],
) -> None:
    """A demoted tier emits a warning-level notify naming skip + survivors."""
    import mcloop.runner as runner

    monkeypatch.setattr(
        runner,
        "ensure_subscription_preflight",
        _make_preflight({"gpt-5-codex"}),
    )
    chain = [
        ChainEntry(cli="claude", model="opus"),
        ChainEntry(cli="codex", model="gpt-5-codex"),
        ChainEntry(cli="claude", model="kimi-k2.6"),
    ]

    _preflight_chain(chain, Path("/tmp"))

    warnings = [msg for msg, level in notify_calls if level == "warning"]
    assert len(warnings) == 1
    assert "skipping model tier 2 (codex/gpt-5-codex)" in warnings[0]
    assert "Running on: opus, kimi-k2.6" in warnings[0]


def test_all_tiers_fail_fires_error_notify(
    monkeypatch: pytest.MonkeyPatch,
    notify_calls: list[tuple[str, str]],
) -> None:
    """The hard-stop path emits an error-level notify before raising."""
    import mcloop.runner as runner

    monkeypatch.setattr(
        runner,
        "ensure_subscription_preflight",
        _make_preflight({"opus", "gpt-5-codex"}),
    )
    chain = [
        ChainEntry(cli="claude", model="opus"),
        ChainEntry(cli="codex", model="gpt-5-codex"),
    ]

    with pytest.raises(SubscriptionPreflightError):
        _preflight_chain(chain, Path("/tmp"))

    errors = [msg for msg, level in notify_calls if level == "error"]
    assert len(errors) == 1
    assert "all model chain tiers failed preflight" in errors[0]


def test_all_tiers_pass_returns_full_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No failures -> the chain is returned unchanged in order."""
    import mcloop.runner as runner

    monkeypatch.setattr(
        runner,
        "ensure_subscription_preflight",
        _make_preflight(set()),
    )
    chain = [
        ChainEntry(cli="claude", model="opus"),
        ChainEntry(cli="codex", model="gpt-5-codex"),
    ]

    usable = _preflight_chain(chain, Path("/tmp"))

    assert usable == chain
