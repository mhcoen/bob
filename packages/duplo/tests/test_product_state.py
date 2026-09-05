"""Product identity recovery must preserve evidence instead of reinitializing."""

import json
import os

import pytest

from bob_tools.json_state import StateError
from duplo.saver import PRODUCT_JSON, derive_app_name, load_product, save_product


@pytest.mark.parametrize("operation", ["load", "save", "derive"])
@pytest.mark.parametrize(
    "raw",
    [
        b'{"product_name":',
        b"\xff",
        b"[]",
        b'{"schema_version": 2}',
        b'{"schema_version": true}',
        b'{"product_name": null}',
    ],
)
def test_invalid_identity_is_preserved(tmp_path, operation, raw):
    path = tmp_path / PRODUCT_JSON
    path.parent.mkdir()
    path.write_bytes(raw)
    with pytest.raises(StateError, match="preserved"):
        if operation == "load":
            load_product(target_dir=tmp_path)
        elif operation == "save":
            save_product("Replacement", "", target_dir=tmp_path)
        else:
            derive_app_name(None, tmp_path)
    assert path.read_bytes() == raw


def test_legacy_read_does_not_migrate_but_save_does(tmp_path):
    path = tmp_path / PRODUCT_JSON
    path.parent.mkdir()
    raw = b'{"product_name":"Old", "app_name":"Custom", "extra":{"x":1}}'
    path.write_bytes(raw)
    assert load_product(target_dir=tmp_path) == ("Old", "")
    assert path.read_bytes() == raw
    save_product("New", "https://example.com", target_dir=tmp_path)
    assert json.loads(path.read_text()) == {
        "schema_version": 1,
        "product_name": "New",
        "source_url": "https://example.com",
        "app_name": "Custom",
        "extra": {"x": 1},
    }
    assert derive_app_name(None, tmp_path) == "Custom"


def test_interrupted_product_save_keeps_identity(tmp_path, monkeypatch):
    path = save_product("Original", "", target_dir=tmp_path)
    raw = path.read_bytes()

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="replace failed"):
        save_product("New", "", target_dir=tmp_path)
    assert path.read_bytes() == raw
    assert load_product(target_dir=tmp_path) == ("Original", "")


def test_corrupt_duplo_fallback_does_not_initialize_identity(tmp_path):
    path = tmp_path / ".duplo" / "duplo.json"
    path.parent.mkdir()
    path.write_text("{")
    with pytest.raises(StateError):
        derive_app_name(None, tmp_path)
    assert not (tmp_path / PRODUCT_JSON).exists()
