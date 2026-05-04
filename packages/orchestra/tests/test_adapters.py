"""Unit tests for the slice-1 mock adapters.

The adapters are deterministic by design; these tests confirm the
payload shapes match what the executor's parsers and outcome-derivation
expect, and that the mock-specific configuration knobs work.
"""

from __future__ import annotations

import os

import pytest

from orchestra.adapters.mock_human import MockHumanAdapter
from orchestra.adapters.mock_model import MockModelAdapter
from orchestra.adapters.mock_shell import MockShellAdapter
from orchestra.errors import AdapterError
from orchestra.spine import InvocationRequest


def _request(
    *,
    state_id: str = "s",
    actor_binding: dict | None = None,
    backing_options: dict | None = None,
    prompt: str | None = None,
) -> InvocationRequest:
    return InvocationRequest(
        state_id=state_id,
        attempt=1,
        actor_binding=actor_binding or {"kind": "model"},
        reads={},
        external_inputs={},
        prompt_artifact=prompt,
        schema=None,
        backing_options=backing_options or {},
        timeout_ms=None,
    )


# --------------------------------------------------------------------
# Mock model adapter
# --------------------------------------------------------------------


def test_mock_model_default_response_echoes_prompt():
    adapter = MockModelAdapter()
    prepared = adapter.prepare(_request(prompt="hello"))
    payload = adapter.invoke(prepared)
    assert payload["output"].startswith("[mock-llm response to:")
    assert "hello" in payload["output"]
    assert payload["verdict"] is None
    assert payload["fields"] == {}
    assert payload["tokens_in"] == len("hello")
    assert payload["cost_usd"] is None
    assert payload["transcript_ref"] is None


def test_mock_model_constructor_override():
    adapter = MockModelAdapter(response="OVERRIDE")
    prepared = adapter.prepare(_request(prompt="hi"))
    assert adapter.invoke(prepared)["output"] == "OVERRIDE"


def test_mock_model_env_override(monkeypatch):
    monkeypatch.setenv("ORCHESTRA_MOCK_MODEL_RESPONSE", "ENV-RESPONSE")
    adapter = MockModelAdapter()
    prepared = adapter.prepare(_request(prompt="hi"))
    assert adapter.invoke(prepared)["output"] == "ENV-RESPONSE"


def test_mock_model_describe_metadata():
    desc = MockModelAdapter().describe()
    assert desc["backing"] == "model"
    assert desc["kind"] == "mock"
    assert desc["supports_cancel"] is False


# --------------------------------------------------------------------
# Mock human adapter
# --------------------------------------------------------------------


def test_mock_human_instance_script_consumed_in_order():
    MockHumanAdapter.clear_shared_script()
    adapter = MockHumanAdapter(script=["accept", "reject"])
    req = _request(
        actor_binding={"kind": "human"},
        backing_options={"options": ["accept", "reject"]},
    )
    p = adapter.prepare(req)
    assert adapter.invoke(p)["chosen"] == "accept"
    p2 = adapter.prepare(req)
    assert adapter.invoke(p2)["chosen"] == "reject"


def test_mock_human_shared_script_used_when_instance_empty():
    MockHumanAdapter.clear_shared_script()
    MockHumanAdapter.set_shared_script(["accept"])
    adapter = MockHumanAdapter()
    req = _request(
        actor_binding={"kind": "human"},
        backing_options={"options": ["accept", "reject"]},
    )
    p = adapter.prepare(req)
    assert adapter.invoke(p)["chosen"] == "accept"
    MockHumanAdapter.clear_shared_script()


def test_mock_human_raises_when_script_exhausted():
    MockHumanAdapter.clear_shared_script()
    if "ORCHESTRA_MOCK_HUMAN_SCRIPT" in os.environ:
        del os.environ["ORCHESTRA_MOCK_HUMAN_SCRIPT"]
    adapter = MockHumanAdapter()
    req = _request(
        actor_binding={"kind": "human"},
        backing_options={"options": ["accept", "reject"]},
    )
    p = adapter.prepare(req)
    with pytest.raises(AdapterError):
        adapter.invoke(p)


def test_mock_human_invalid_choice_raises():
    MockHumanAdapter.clear_shared_script()
    adapter = MockHumanAdapter(script=["definitely-not-an-option"])
    req = _request(
        actor_binding={"kind": "human"},
        backing_options={"options": ["accept", "reject"]},
    )
    p = adapter.prepare(req)
    with pytest.raises(AdapterError):
        adapter.invoke(p)


def test_mock_human_no_options_raises_at_prepare():
    adapter = MockHumanAdapter()
    req = _request(actor_binding={"kind": "human"}, backing_options={})
    with pytest.raises(AdapterError):
        adapter.prepare(req)


# --------------------------------------------------------------------
# Mock shell adapter
# --------------------------------------------------------------------


def test_mock_shell_runs_block_executes_each_command():
    adapter = MockShellAdapter(
        response_table={"echo a": (0, "a\n", ""), "echo b": (0, "b\n", "")}
    )
    req = _request(
        actor_binding={"kind": "shell"},
        backing_options={"runs": ["echo a", "echo b"]},
    )
    p = adapter.prepare(req)
    payload = adapter.invoke(p)
    assert payload["aggregate"]["pass_count"] == 2
    assert payload["aggregate"]["fail_count"] == 0
    assert payload["aggregate"]["skipped_count"] == 0
    assert [c["command"] for c in payload["commands"]] == ["echo a", "echo b"]
    assert all(c["exit_code"] == 0 for c in payload["commands"])


def test_mock_shell_short_circuits_on_failure_by_default():
    adapter = MockShellAdapter(
        response_table={"first": (0, "", ""), "fail": (1, "", "boom"), "third": (0, "", "")}
    )
    req = _request(
        actor_binding={"kind": "shell"},
        backing_options={"runs": ["first", "fail", "third"]},
    )
    payload = adapter.invoke(adapter.prepare(req))
    agg = payload["aggregate"]
    assert agg["pass_count"] == 1
    assert agg["fail_count"] == 1
    assert agg["skipped_count"] == 1
    assert payload["commands"][2]["skipped"] is True


def test_mock_shell_continue_on_fail_does_not_short_circuit():
    adapter = MockShellAdapter(
        response_table={"first": (0, "", ""), "fail": (1, "", ""), "third": (0, "", "")}
    )
    req = _request(
        actor_binding={"kind": "shell"},
        backing_options={
            "runs": ["first", "fail", "third"],
            "continue_on_fail": True,
        },
    )
    payload = adapter.invoke(adapter.prepare(req))
    agg = payload["aggregate"]
    assert agg["pass_count"] == 2
    assert agg["fail_count"] == 1
    assert agg["skipped_count"] == 0


def test_mock_shell_no_command_or_runs_raises():
    adapter = MockShellAdapter()
    req = _request(actor_binding={"kind": "shell"}, backing_options={})
    with pytest.raises(AdapterError):
        adapter.prepare(req)


def test_mock_shell_command_form():
    adapter = MockShellAdapter(response_table={"echo hi": (0, "hi\n", "")})
    req = _request(
        actor_binding={"kind": "shell"},
        backing_options={"command": "echo hi"},
    )
    payload = adapter.invoke(adapter.prepare(req))
    assert payload["commands"][0]["command"] == "echo hi"
    assert payload["aggregate"]["pass_count"] == 1


# --------------------------------------------------------------------
# extract_final_text: stream-json -> assistant text
# --------------------------------------------------------------------


def test_extract_final_text_returns_result_field() -> None:
    """The canonical happy path: Claude Code emits a stream of
    delta events followed by a result record, and the helper pulls
    the result.result string out as the final assistant text."""
    import json

    from orchestra.adapters._subprocess import extract_final_text

    expected = "I'm doing well, thanks. Ready when you are. What would you like to work on?"
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "I"},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {
                        "type": "text_delta",
                        "text": (
                            "'m doing well, thanks. Ready when you are. "
                            "What would you like to work on?"
                        ),
                    },
                },
            }
        ),
        json.dumps({"type": "stream_event", "event": {"type": "message_stop"}}),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": expected,
            }
        ),
    ]
    transcript = "\n".join(lines) + "\n"
    assert extract_final_text(transcript) == expected


def test_extract_final_text_uses_last_result_when_multiple() -> None:
    """If for some reason multiple result records appear, the last
    one wins so a clean retry's summary supersedes a prior failure."""
    import json

    from orchestra.adapters._subprocess import extract_final_text

    lines = [
        json.dumps({"type": "result", "subtype": "error", "result": "first try"}),
        json.dumps({"type": "result", "subtype": "success", "result": "final"}),
    ]
    assert extract_final_text("\n".join(lines)) == "final"


def test_extract_final_text_falls_back_to_text_deltas() -> None:
    """When the stream lacks a result record (subprocess crashed
    mid-stream), concatenate every text_delta in order."""
    import json

    from orchestra.adapters._subprocess import extract_final_text

    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Hello, "},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "world"},
                },
            }
        ),
    ]
    assert extract_final_text("\n".join(lines)) == "Hello, world"


def test_extract_final_text_handles_top_level_content_block_delta() -> None:
    """Some Claude Code versions emit content_block_delta at the top
    level instead of wrapping in stream_event. Both shapes should
    yield the same text on the fallback path."""
    import json

    from orchestra.adapters._subprocess import extract_final_text

    lines = [
        json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "foo"},
            }
        ),
        json.dumps(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "bar"},
            }
        ),
    ]
    assert extract_final_text("\n".join(lines)) == "foobar"


def test_extract_final_text_returns_raw_when_not_jsonl() -> None:
    """A subprocess that crashed before emitting any JSON (e.g.
    printed a Python traceback) returns the raw output unchanged so
    the user can still see what went wrong."""
    from orchestra.adapters._subprocess import extract_final_text

    raw = "Traceback (most recent call last):\n  File ...\n"
    assert extract_final_text(raw) == raw


def test_extract_final_text_handles_empty_input() -> None:
    from orchestra.adapters._subprocess import extract_final_text

    assert extract_final_text("") == ""


def test_extract_final_text_falls_through_when_result_is_empty_string() -> None:
    """Some vendor builds of the inner CLI (kimi via moonshot/Parasail
    has been observed) emit a final ``result`` record where the
    ``result`` field is the empty string while the actual assistant
    text was emitted only through ``content_block_delta`` events.
    Treat empty ``result.result`` as ``no result`` and fall through
    to the delta fallback so the answer is recovered."""
    import json

    from orchestra.adapters._subprocess import extract_final_text

    lines = [
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Yes. "},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "Sleep is good."},
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
            }
        ),
    ]
    assert extract_final_text("\n".join(lines)) == "Yes. Sleep is good."


def test_empty_result_falls_through_to_deltas() -> None:
    """Real-shape regression for run a4deee595138 (kimi-k2.6 via
    moonshotai/Parasail). The proposer subprocess emits a thinking
    content block at index 0, an answer text block at index 1 with
    content_block_delta events carrying the actual answer, a
    redacted_thinking block at index 2, and a final result record
    with result == "". The previous helper returned "" because rule 1
    accepted any string-typed result.result; now the empty result
    falls through to rule 2 and the deltas at index 1 are
    concatenated as the answer."""
    import json

    from orchestra.adapters._subprocess import extract_final_text

    answer_text = (
        " Yes. Go to sleep early. Adequate rest is more valuable "
        "than whatever you would get done while tired."
    )
    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        # Thinking block at index 0 (the model's reasoning, not part
        # of the answer). Some providers stream this as text_delta on
        # the thinking block. The current helper accepts every
        # text_delta delta in order; this test fixture only emits
        # text_delta on the answer block at index 1 so the resolver
        # picks up the answer text.
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "thinking"},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "content_block_stop", "index": 0},
            }
        ),
        # Answer text block at index 1.
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "text"},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": answer_text},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "content_block_stop", "index": 1},
            }
        ),
        # Redacted thinking at index 2 (no text_delta emitted).
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "index": 2,
                    "content_block": {"type": "redacted_thinking"},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {"type": "content_block_stop", "index": 2},
            }
        ),
        # Final result record with empty result. This is the kimi
        # quirk: result.result is "" while the actual answer lives in
        # the deltas above.
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "stop_reason": "end_turn",
            }
        ),
    ]
    transcript = "\n".join(lines) + "\n"
    assert extract_final_text(transcript) == answer_text


def test_empty_result_with_no_deltas_falls_through_to_raw() -> None:
    """When the final result.result is empty AND no
    content_block_delta events emitted any text, the resolver falls
    through to rule 3 (raw passthrough). Confirms the empty-result-
    treated-as-missing behavior is consistent across both fallback
    rules."""
    import json

    from orchestra.adapters._subprocess import extract_final_text

    lines = [
        json.dumps({"type": "system", "subtype": "init"}),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "",
                "stop_reason": "end_turn",
            }
        ),
    ]
    transcript = "\n".join(lines) + "\n"
    # No deltas to fall back to. Rule 3 returns the raw input so the
    # caller still has something to inspect.
    assert extract_final_text(transcript) == transcript


def test_extract_final_text_ignores_non_text_deltas() -> None:
    """Tool-use deltas should not pollute the final text."""
    import json

    from orchestra.adapters._subprocess import extract_final_text

    lines = [
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "delta": {"type": "input_json_delta", "partial_json": "{}"},
                },
            }
        ),
        json.dumps({"type": "result", "subtype": "success", "result": "ok"}),
    ]
    assert extract_final_text("\n".join(lines)) == "ok"


# --------------------------------------------------------------------
# workspace_mutation contract (PRJI prerequisite)
# --------------------------------------------------------------------


def test_workspace_mutation_classification_for_shipped_adapters():
    """Every shipped adapter declares workspace_mutation in describe()
    per design/iteration-and-implementation-workflows.md. The PRJI
    workflow's config validation rule reads this field to enforce the
    "implementer is the only mutator" invariant.
    """
    from orchestra.adapters.claude_code_agent import ClaudeCodeAgentAdapter
    from orchestra.adapters.claude_code_text import ClaudeCodeTextAdapter
    from orchestra.adapters.codex_agent import CodexAgentAdapter
    from orchestra.adapters.codex_text import CodexTextAdapter
    from orchestra.adapters.mock_human import MockHumanAdapter
    from orchestra.adapters.mock_model import MockModelAdapter
    from orchestra.adapters.mock_shell import MockShellAdapter

    expected = {
        ClaudeCodeAgentAdapter: "mutating",
        ClaudeCodeTextAdapter: "text_only",
        CodexAgentAdapter: "mutating",
        CodexTextAdapter: "text_only",
        MockModelAdapter: "text_only",
        MockHumanAdapter: "text_only",
        MockShellAdapter: "text_only",
    }
    for cls, expected_value in expected.items():
        desc = cls().describe()
        assert "workspace_mutation" in desc, (
            f"{cls.__name__}.describe() missing 'workspace_mutation'"
        )
        actual = desc["workspace_mutation"]
        assert actual == expected_value, (
            f"{cls.__name__} declared {actual!r}, "
            f"expected {expected_value!r}"
        )
