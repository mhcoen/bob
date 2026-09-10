"""Model selection at the native Claude subprocess boundary."""

import pytest

from orchestra.adapters.claude_code_agent import ClaudeCodeAgentAdapter
from orchestra.adapters.claude_code_text import ClaudeCodeTextAdapter
from orchestra.registry.registry import with_core
from orchestra.spine import InvocationRequest


@pytest.mark.parametrize("adapter_type", [ClaudeCodeTextAdapter, ClaudeCodeAgentAdapter])
@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        (None, "claude-fable-5-1[1m]"),
        ("fable", "claude-fable-5-1[1m]"),
        ("fable[1m]", "claude-fable-5-1[1m]"),
        ("sonnet", "sonnet"),
        ("opus", "opus"),
        ("claude-opus-4-7", "claude-opus-4-7"),
        ("z-ai/glm-5.3-flash", "z-ai/glm-5.3-flash"),
    ],
)
def test_prepared_model_matches_subprocess_selection(
    adapter_type, selected, expected, tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr("orchestra.adapters._subprocess._load_mcloop_config", lambda: {})
    monkeypatch.setattr("orchestra.adapters._subprocess.load_role_config", lambda role: None)
    request = InvocationRequest(
        state_id="selection",
        attempt=1,
        actor_binding={"model": selected},
        reads={},
        external_inputs={"project_dir": str(tmp_path)},
        prompt_artifact="Reply OK",
        schema=None,
        backing_options={},
        timeout_ms=1000,
    )
    prepared = adapter_type().prepare(request)
    command = prepared.inner["cmd"]
    assert command[command.index("--model") + 1] == expected
    assert prepared.summary["model"] == expected
    if selected == "z-ai/glm-5.3-flash":
        assert prepared.inner["env"]["ANTHROPIC_BASE_URL"] == "https://openrouter.ai/api"
    else:
        assert "ANTHROPIC_BASE_URL" not in prepared.inner["env"]


def test_fable_registry_binding_pins_version():
    binding = with_core().model_identifiers["fable"]
    assert binding.adapter == "claude_code_text"
    assert binding.model == "claude-fable-5-1[1m]"


def test_provider_binding_keeps_its_own_default():
    adapter = ClaudeCodeTextAdapter(provider_config={"base_url": "https://provider.invalid"})
    assert "--model" not in adapter._build_command(None)
