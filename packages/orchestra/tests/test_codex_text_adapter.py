"""Unit tests for ``CodexTextAdapter``.

Mirrors ``tests/test_adapters.py`` and ``tests/test_subprocess_adapters.py``
in shape: prepare() output structure, command construction, env wiring,
verdict mapping, and the passthrough invariant that Codex stdout is
returned as ``output`` unchanged (no stream-json extraction).

Subprocess invocation is mocked. No live ``codex exec`` is required to
run this file. A separate live-shaped test is gated behind a skipif so
CI on machines without the Codex CLI does not fail.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from orchestra.adapters import codex_text as codex_text_mod
from orchestra.adapters.codex_text import (
    CodexTextAdapter,
    _verdict_for_exit_code,
    register,
)
from orchestra.spine import InvocationRequest


def _request(
    *,
    state_id: str = "s",
    actor_binding: dict[str, Any] | None = None,
    backing_options: dict[str, Any] | None = None,
    external_inputs: dict[str, Any] | None = None,
    prompt: str | None = None,
    timeout_ms: int | None = None,
) -> InvocationRequest:
    return InvocationRequest(
        state_id=state_id,
        attempt=1,
        actor_binding=actor_binding or {"kind": "model"},
        reads={},
        external_inputs=external_inputs or {},
        prompt_artifact=prompt,
        schema=None,
        backing_options=backing_options or {},
        timeout_ms=timeout_ms,
    )


# --------------------------------------------------------------------
# Command construction
# --------------------------------------------------------------------


def test_build_command_minimal_no_model_no_prompt() -> None:
    adapter = CodexTextAdapter()
    cmd = adapter._build_command(prompt="", model=None)
    assert cmd == ["codex", "exec", "--full-auto"]


def test_build_command_with_model_and_prompt() -> None:
    adapter = CodexTextAdapter()
    cmd = adapter._build_command(prompt="hello", model="gpt-5-codex")
    assert cmd == [
        "codex",
        "exec",
        "--full-auto",
        "--model",
        "gpt-5-codex",
        "hello",
    ]


def test_build_command_model_only() -> None:
    adapter = CodexTextAdapter()
    cmd = adapter._build_command(prompt="", model="gpt-5-codex")
    assert cmd == [
        "codex",
        "exec",
        "--full-auto",
        "--model",
        "gpt-5-codex",
    ]


def test_build_command_prompt_only() -> None:
    adapter = CodexTextAdapter()
    cmd = adapter._build_command(prompt="hi", model=None)
    assert cmd == ["codex", "exec", "--full-auto", "hi"]


# --------------------------------------------------------------------
# prepare() shape
# --------------------------------------------------------------------


def test_prepare_summary_carries_kind_adapter_cli_command(
    tmp_path: Path,
) -> None:
    adapter = CodexTextAdapter(default_model="gpt-5-codex-mini")
    req = _request(
        prompt="say hi",
        external_inputs={
            "project_dir": str(tmp_path),
            "log_dir": str(tmp_path / "logs"),
            "task_label": "smoke",
        },
    )
    prepared = adapter.prepare(req)
    assert prepared.summary["kind"] == "model"
    assert prepared.summary["adapter"] == "codex_text"
    assert prepared.summary["cli"] == "codex"
    assert prepared.summary["model"] == "gpt-5-codex-mini"
    assert prepared.summary["command"] == [
        "codex",
        "exec",
        "--full-auto",
        "--model",
        "gpt-5-codex-mini",
        "say hi",
    ]
    assert prepared.summary["cwd"] == str(tmp_path)
    assert prepared.summary["log_dir"] == str(tmp_path / "logs")
    assert prepared.summary["prompt_chars"] == len("say hi")
    assert prepared.summary["prompt_preview"] == "say hi"


def test_prepare_inner_carries_cmd_env_cwd_log_dir(tmp_path: Path) -> None:
    adapter = CodexTextAdapter()
    req = _request(
        prompt="x",
        external_inputs={
            "project_dir": str(tmp_path),
            "log_dir": str(tmp_path / "l"),
            "task_label": "label",
        },
    )
    prepared = adapter.prepare(req)
    inner = prepared.inner
    assert inner["cmd"][0] == "codex"
    assert inner["cwd"] == tmp_path
    assert inner["log_dir"] == tmp_path / "l"
    assert inner["task_label"] == "label"
    # Env is built via build_session_env. PATH always passes through.
    assert "PATH" in inner["env"]


def test_prepare_backing_options_override_external_inputs(
    tmp_path: Path,
) -> None:
    """``backing_options`` wins over ``external_inputs`` on overlap."""
    adapter = CodexTextAdapter()
    other = tmp_path / "elsewhere"
    other.mkdir()
    req = _request(
        prompt="x",
        external_inputs={"project_dir": str(tmp_path)},
        backing_options={"project_dir": str(other)},
    )
    prepared = adapter.prepare(req)
    assert prepared.inner["cwd"] == other


def test_prepare_model_resolution_order() -> None:
    """``backing_options.model_override`` > ``default_model`` > ``actor_binding.model``."""
    a = CodexTextAdapter(default_model="default-m")
    # model_override wins
    p = a.prepare(
        _request(
            actor_binding={"kind": "model", "model": "binding-m"},
            backing_options={"model_override": "override-m"},
        )
    )
    assert p.summary["model"] == "override-m"
    # default beats binding
    p = a.prepare(
        _request(
            actor_binding={"kind": "model", "model": "binding-m"},
        )
    )
    assert p.summary["model"] == "default-m"
    # binding when no defaults
    plain = CodexTextAdapter()
    p = plain.prepare(
        _request(actor_binding={"kind": "model", "model": "binding-m"})
    )
    assert p.summary["model"] == "binding-m"


def test_prepare_timeout_from_request_overrides_default() -> None:
    a = CodexTextAdapter(default_timeout_s=42)
    p = a.prepare(_request(timeout_ms=5000))
    assert p.summary["timeout_s"] == 5
    assert p.inner["timeout_s"] == 5


def test_prepare_default_timeout_when_not_set() -> None:
    a = CodexTextAdapter(default_timeout_s=42)
    p = a.prepare(_request())
    assert p.summary["timeout_s"] == 42


# --------------------------------------------------------------------
# invoke() passthrough behavior
# --------------------------------------------------------------------


def test_invoke_returns_stdout_unchanged_no_stream_json_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex emits final text on stdout. The adapter must NOT call
    ``extract_final_text`` on it. A stream-json-shaped string fed
    through invoke() should reach the caller unchanged."""
    fake_stdout = (
        '{"type": "result", "subtype": "success", "result": "would-be-extracted"}'
    )
    monkeypatch.setattr(
        codex_text_mod,
        "run_session",
        lambda cmd, cwd, env, timeout, silent: (fake_stdout, 0),
    )
    monkeypatch.setattr(
        codex_text_mod,
        "write_log",
        lambda log_dir, task_label, cmd, output, exit_code: tmp_path / "log",
    )
    adapter = CodexTextAdapter()
    prepared = adapter.prepare(
        _request(prompt="x", external_inputs={"project_dir": str(tmp_path)})
    )
    payload = adapter.invoke(prepared)
    # Output is the raw fake_stdout, not "would-be-extracted".
    assert payload["output"] == fake_stdout


def test_invoke_returns_complete_verdict_on_zero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_text_mod,
        "run_session",
        lambda cmd, cwd, env, timeout, silent: ("done.", 0),
    )
    monkeypatch.setattr(
        codex_text_mod,
        "write_log",
        lambda log_dir, task_label, cmd, output, exit_code: tmp_path / "log",
    )
    adapter = CodexTextAdapter()
    prepared = adapter.prepare(_request(external_inputs={"project_dir": str(tmp_path)}))
    payload = adapter.invoke(prepared)
    assert payload["verdict"] == "complete"
    assert payload["fields"]["exit_code"] == 0
    assert payload["fields"]["log_path"] == str(tmp_path / "log")
    assert payload["transcript_ref"] == str(tmp_path / "log")


def test_invoke_returns_timeout_verdict_on_minus_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_text_mod,
        "run_session",
        lambda cmd, cwd, env, timeout, silent: ("partial", -2),
    )
    monkeypatch.setattr(
        codex_text_mod,
        "write_log",
        lambda log_dir, task_label, cmd, output, exit_code: tmp_path / "log",
    )
    adapter = CodexTextAdapter()
    prepared = adapter.prepare(_request(external_inputs={"project_dir": str(tmp_path)}))
    payload = adapter.invoke(prepared)
    assert payload["verdict"] == "timeout"
    assert payload["fields"]["exit_code"] == -2


def test_invoke_returns_error_verdict_on_other_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_text_mod,
        "run_session",
        lambda cmd, cwd, env, timeout, silent: ("oops", 7),
    )
    monkeypatch.setattr(
        codex_text_mod,
        "write_log",
        lambda log_dir, task_label, cmd, output, exit_code: tmp_path / "log",
    )
    adapter = CodexTextAdapter()
    prepared = adapter.prepare(_request(external_inputs={"project_dir": str(tmp_path)}))
    payload = adapter.invoke(prepared)
    assert payload["verdict"] == "error"
    assert payload["fields"]["exit_code"] == 7


# --------------------------------------------------------------------
# verdict mapping
# --------------------------------------------------------------------


def test_verdict_for_exit_code_mapping() -> None:
    assert _verdict_for_exit_code(0) == "complete"
    assert _verdict_for_exit_code(-2) == "timeout"
    assert _verdict_for_exit_code(1) == "error"
    assert _verdict_for_exit_code(130) == "error"


# --------------------------------------------------------------------
# describe() and class attributes
# --------------------------------------------------------------------


def test_describe_metadata() -> None:
    desc = CodexTextAdapter().describe()
    assert desc["backing"] == "codex_text"
    assert desc["kind"] == "subprocess"
    assert desc["cli"] == "codex"
    assert desc["supports_cancel"] is False
    assert desc["reports_cost"] is False
    # Codex output is final text, not a stream-json transcript.
    assert desc["supports_streaming"] is False


def test_class_attributes() -> None:
    assert CodexTextAdapter.backing == "codex_text"
    assert CodexTextAdapter.manages_own_timeout is True


def test_cancel_is_noop() -> None:
    a = CodexTextAdapter()
    p = a.prepare(_request())
    # cancel returns None and does not raise.
    assert a.cancel(p) is None


# --------------------------------------------------------------------
# register()
# --------------------------------------------------------------------


def test_register_idempotent() -> None:
    class _FakeRegistry:
        def __init__(self) -> None:
            self.actor_backings: dict[str, Any] = {}

        def register_actor_backing(self, name: str, factory: Any) -> None:
            if name in self.actor_backings:
                raise RuntimeError(f"duplicate {name}")
            self.actor_backings[name] = factory

    reg = _FakeRegistry()
    register(reg)
    assert "codex_text" in reg.actor_backings
    # Second call must be a no-op, not raise.
    register(reg)
    assert "codex_text" in reg.actor_backings


def test_register_factory_constructs_adapter_with_default_model() -> None:
    class _FakeRegistry:
        def __init__(self) -> None:
            self.actor_backings: dict[str, Any] = {}

        def register_actor_backing(self, name: str, factory: Any) -> None:
            self.actor_backings[name] = factory

    reg = _FakeRegistry()
    register(reg, default_model="gpt-5-codex")
    instance = reg.actor_backings["codex_text"]()
    assert isinstance(instance, CodexTextAdapter)
    assert instance._default_model == "gpt-5-codex"


# --------------------------------------------------------------------
# Live smoke (skipped when codex CLI is absent)
# --------------------------------------------------------------------


@pytest.mark.skipif(
    shutil.which("codex") is None or os.environ.get("ORCHESTRA_LIVE_CODEX") != "1",
    reason="live Codex test requires codex on PATH and ORCHESTRA_LIVE_CODEX=1",
)
def test_live_codex_text_smoke(tmp_path: Path) -> None:
    """Live invocation: run a trivial prompt through Codex.

    Skipped automatically when the user does not have the Codex CLI
    installed. When present, this asserts the wiring (env, command,
    log file) actually produces a zero exit and non-empty output.
    """
    adapter = CodexTextAdapter()
    req = _request(
        prompt="reply with the single word: ok",
        external_inputs={
            "project_dir": str(tmp_path),
            "log_dir": str(tmp_path / "logs"),
            "task_label": "live-smoke",
        },
    )
    prepared = adapter.prepare(req)
    payload = adapter.invoke(prepared)
    assert payload["verdict"] == "complete"
    assert payload["output"]
    assert Path(payload["fields"]["log_path"]).exists()
