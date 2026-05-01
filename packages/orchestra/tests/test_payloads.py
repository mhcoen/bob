"""Unit tests for ``orchestra.payloads``.

The module is load-bearing for both the live executor's
``_write_payload`` and the replay path's envelope hydration. The
end-to-end tests in ``test_e2e.py`` and ``test_transforms.py``
exercise the integration but do not pin every edge case in
isolation. These unit tests fix the on-disk format and the
strip-internal semantics so regressions are caught before they
reach an end-to-end fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestra.payloads import load_payload, strip_internal, write_payload

# --------------------------------------------------------------------
# strip_internal
# --------------------------------------------------------------------


def test_strip_internal_empty_payload() -> None:
    assert strip_internal({}) == {}


def test_strip_internal_only_internal_keys() -> None:
    payload = {"_a": 1, "_b": "x", "_nested": {"k": "v"}}
    assert strip_internal(payload) == {}


def test_strip_internal_no_internal_keys() -> None:
    payload = {"output": "hello", "tokens": 12, "verdict": None}
    assert strip_internal(payload) == payload


def test_strip_internal_mixed_keys() -> None:
    payload = {
        "output": "hi",
        "_declared_writes": [{"name": "response"}],
        "tokens": 5,
        "_internal": True,
    }
    assert strip_internal(payload) == {"output": "hi", "tokens": 5}


def test_strip_internal_strips_only_top_level() -> None:
    """Pin the documented semantics: nested internal-looking keys
    are NOT stripped. The function operates on the top-level dict
    only. If a future change expands to recurse into nested dicts,
    this test pins that the current behavior is intentional and
    must be revisited explicitly."""
    payload = {
        "output": "hi",
        "nested": {
            "_internal_in_nested": "stays",
            "value": 1,
        },
        "_top_level_internal": "removed",
    }
    out = strip_internal(payload)
    assert "_top_level_internal" not in out
    assert "nested" in out
    assert out["nested"] == {"_internal_in_nested": "stays", "value": 1}


def test_strip_internal_returns_a_new_dict() -> None:
    """``strip_internal`` must not mutate its input. The returned
    object is independent of the input mapping so callers can keep
    or discard either side without surprise."""
    payload = {"a": 1, "_b": 2}
    out = strip_internal(payload)
    assert out == {"a": 1}
    assert payload == {"a": 1, "_b": 2}


# --------------------------------------------------------------------
# write_payload + load_payload round-trip
# --------------------------------------------------------------------


def _round_trip(tmp_path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    payloads_dir = tmp_path / "payloads"
    ref = write_payload(payloads_dir, "test-run", 7, payload)
    return load_payload(tmp_path, ref)


def test_round_trip_empty_payload(tmp_path: Path) -> None:
    assert _round_trip(tmp_path, {}) == {}


def test_round_trip_primitive_values(tmp_path: Path) -> None:
    payload = {
        "output": "hello",
        "verdict": "approve",
        "tokens_in": 12,
        "tokens_out": 7,
        "cost_usd": 0.0,
        "transcript_ref": None,
    }
    assert _round_trip(tmp_path, payload) == payload


def test_round_trip_nested_dict(tmp_path: Path) -> None:
    payload = {
        "output": "structured",
        "fields": {"a": 1, "b": {"c": "deep"}},
        "aggregate": {"pass_count": 3, "fail_count": 0},
    }
    assert _round_trip(tmp_path, payload) == payload


def test_round_trip_non_ascii_strings(tmp_path: Path) -> None:
    payload = {
        "output": "café résumé naïve",
        "fields": {"ϕ": "ψ", "状态": "成功"},
    }
    assert _round_trip(tmp_path, payload) == payload


def test_write_payload_strips_internal_keys_on_disk(tmp_path: Path) -> None:
    payloads_dir = tmp_path / "payloads"
    payload = {"output": "hi", "_declared_writes": [{"x": 1}]}
    ref = write_payload(payloads_dir, "test-run", 3, payload)
    on_disk = json.loads((tmp_path / ref).read_text(encoding="utf-8"))
    assert on_disk == {"output": "hi"}


# --------------------------------------------------------------------
# write_payload on-disk format
# --------------------------------------------------------------------


def test_write_payload_returns_relative_ref(tmp_path: Path) -> None:
    payloads_dir = tmp_path / "payloads"
    ref = write_payload(payloads_dir, "abc-run", 42, {"k": "v"})
    assert ref == "payloads/abc-run-42.json"


def test_write_payload_creates_payloads_dir_if_missing(tmp_path: Path) -> None:
    payloads_dir = tmp_path / "payloads"
    assert not payloads_dir.exists()
    write_payload(payloads_dir, "run-1", 1, {"k": "v"})
    assert payloads_dir.is_dir()


def test_write_payload_uses_sort_keys_true(tmp_path: Path) -> None:
    """The on-disk format is canonical: keys appear in lexicographic
    order regardless of insertion order. This pin matters because
    determinism tests downstream rely on the encoded bytes being
    stable across Python's dict ordering."""
    payloads_dir = tmp_path / "payloads"
    payload = {"zeta": 1, "alpha": 2, "mu": 3}
    ref = write_payload(payloads_dir, "run-sort", 1, payload)
    raw = (tmp_path / ref).read_text(encoding="utf-8")
    # Strip the trailing newline for the JSON-only comparison.
    assert raw.endswith("\n")
    body = raw[:-1]
    assert body == json.dumps(payload, sort_keys=True, ensure_ascii=False)
    # And not the unsorted form (only meaningful when keys differ in
    # order from sorted, which is the case for our fixture).
    assert body != json.dumps(payload, sort_keys=False, ensure_ascii=False)


def test_write_payload_uses_ensure_ascii_false(tmp_path: Path) -> None:
    """Non-ASCII characters land as raw UTF-8 in the on-disk file,
    not as ``\\uXXXX`` escape sequences. Pin this so the format
    stays stable for tooling that grep the payload files by hand."""
    payloads_dir = tmp_path / "payloads"
    payload = {"output": "café"}
    ref = write_payload(payloads_dir, "run-utf8", 1, payload)
    raw_bytes = (tmp_path / ref).read_bytes()
    # Raw UTF-8 emits the multi-byte sequence for é (0xC3 0xA9).
    assert b"caf\xc3\xa9" in raw_bytes
    # Ascii-escape form would have emitted é instead.
    assert b"\\u00e9" not in raw_bytes


def test_write_payload_trailing_newline(tmp_path: Path) -> None:
    payloads_dir = tmp_path / "payloads"
    write_payload(payloads_dir, "run-nl", 1, {"k": "v"})
    raw = (payloads_dir / "run-nl-1.json").read_text(encoding="utf-8")
    assert raw.endswith("\n")
    # Exactly one trailing newline, not two or zero.
    assert not raw.endswith("\n\n")


# --------------------------------------------------------------------
# load_payload edge cases
# --------------------------------------------------------------------


def test_load_payload_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    """A cancelled invocation never wrote its payload, so the
    referenced file does not exist. Replay must treat this as an
    empty payload rather than crashing."""
    assert load_payload(tmp_path, "payloads/does-not-exist.json") == {}


def test_load_payload_non_dict_contents_returns_empty_dict(
    tmp_path: Path,
) -> None:
    """A corrupt payload file (e.g., a JSON list or a scalar at the
    top level) is treated as empty. Hydration consumers expect
    a dict shape; returning a non-dict would propagate a type
    error far from the source."""
    payloads_dir = tmp_path / "payloads"
    payloads_dir.mkdir()
    (payloads_dir / "weird.json").write_text("[1, 2, 3]\n", encoding="utf-8")
    assert load_payload(tmp_path, "payloads/weird.json") == {}
