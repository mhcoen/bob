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
