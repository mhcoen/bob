"""Unit tests for the profile registry."""

from __future__ import annotations

import pytest

from orchestra.errors import RegistryConflict
from orchestra.registry.registry import ProfileRegistry, with_core


def test_with_core_has_inline_types_and_backings():
    reg = with_core()
    for t in ("text", "json", "messages", "prompt", "schema", "document"):
        assert t in reg.artifact_types
    for backing in ("model", "human", "shell"):
        assert backing in reg.actor_backings


def test_with_core_has_identity_text_parser():
    reg = with_core()
    parsers = reg.parsers_for(backing="model", artifact_types=("text",))
    assert any(p.name == "identity_text" for p in parsers)
    assert reg.parsers_for(backing="shell", artifact_types=("text",)) == []
    assert reg.parsers_for(backing="model", artifact_types=("json",)) == []


def test_double_registration_conflicts():
    reg = ProfileRegistry()
    reg.register_artifact_type("custom")
    with pytest.raises(RegistryConflict):
        reg.register_artifact_type("custom")


def test_adapter_for_constructs_an_adapter():
    reg = with_core()
    adapter = reg.adapter_for("model")
    desc = adapter.describe()
    assert desc["backing"] == "model"
    assert desc["kind"] == "mock"


def test_adapter_for_caches_instance_per_backing():
    """Adapters are constructed once per backing and reused."""
    reg = with_core()
    a1 = reg.adapter_for("model")
    a2 = reg.adapter_for("model")
    assert a1 is a2
    h1 = reg.adapter_for("human")
    assert h1 is not a1
    h2 = reg.adapter_for("human")
    assert h1 is h2


def test_adapter_for_caches_a_none_returning_factory():
    """A factory that returns ``None`` (or any falsy instance) must be
    invoked exactly once and its result cached. A truthiness-based
    cache check would treat ``None`` as a permanent miss and re-invoke
    the factory on every call, violating the one-instance-per-backing
    invariant."""
    reg = ProfileRegistry()
    calls = {"n": 0}

    def factory():
        calls["n"] += 1
        return None

    reg.register_actor_backing("nullable", factory)
    first = reg.adapter_for("nullable")
    second = reg.adapter_for("nullable")
    assert first is None
    assert second is None
    assert calls["n"] == 1


def test_register_actor_backing_invalidates_cache():
    reg = ProfileRegistry()

    class A:
        def describe(self) -> dict:
            return {"v": 1}

    class B:
        def describe(self) -> dict:
            return {"v": 2}

    reg.register_actor_backing("k", A)
    a1 = reg.adapter_for("k")
    assert a1.describe()["v"] == 1
    # Re-registration of the same backing is a conflict, not a swap.
    with pytest.raises(RegistryConflict):
        reg.register_actor_backing("k", B)


def test_bare_codex_identifier_resolves_to_a_chatgpt_account_model():
    """The bare ``codex`` identifier must not resolve to gpt-5-codex.
    ChatGPT-account Codex rejects that model with a 400, so anything
    defaulting through the identifier table would break ecosystem-wide.
    The default is the model the account-tier CLI serves."""
    reg = with_core()
    ident = reg.model_identifiers["codex"]
    assert ident.adapter == "codex_text"
    assert ident.model == "gpt-5.5"
    assert ident.model != "gpt-5-codex"


def test_gpt_5_codex_remains_selectable_as_an_explicit_identifier():
    """Accounts whose Codex access does serve gpt-5-codex can still
    select it, but only by naming it explicitly. It is opt-in, never
    the value behind the bare ``codex`` identifier."""
    reg = with_core()
    ident = reg.model_identifiers["gpt-5-codex"]
    assert ident.adapter == "codex_text"
    assert ident.model == "gpt-5-codex"
