"""Unit tests for ``orchestra.executor.executor._extract_last_json_object``.

The extractor is the runtime's parse-tolerance contract for
schema-backed model output. Real LLM CLIs wrap the model's JSON
answer in non-JSON content (codex banner + prompt-echo + reasoning
+ "tokens used" footer; claude markdown ```json fence with prose
preamble). The extractor scans for balanced top-level ``{...}``
spans, respects JSON string and escape boundaries, and returns the
first that ``json.loads`` cleanly when walked from last to first.

The extractor is schema-agnostic: it returns parsed JSON or raises
``_JsonExtractError``. Schema validation is the runtime's
responsibility and is not retried here.
"""

from __future__ import annotations

import pytest

from orchestra.executor.executor import (
    _extract_last_json_object,
    _JsonExtractError,
)

_CODEX_BANNER = """\
Reading prompt from stdin...
OpenAI Codex v0.128.0 (research preview)
--------
workdir: /tmp/whatever
model: gpt-5.5
provider: openai
approval: never
sandbox: read-only
reasoning effort: high
reasoning summaries: none
session id: 019dfb11-9c40-7163-9c11-cfe1b63e9cc8
--------
user
You are the judge. Read the user's question, the proposal, and the
reviewer's critique. Decide whether the proposal is acceptable.
"""

_CODEX_FOOTER = """
tokens used
5,033
"""


def test_extract_clean_json():
    """A bare JSON object parses without any wrapping."""
    text = '{"decision": "accept", "feedback": "fine"}'
    out = _extract_last_json_object(text)
    assert out == {"decision": "accept", "feedback": "fine"}


def test_extract_codex_transcript_with_banner_and_footer():
    """Codex's actual output shape: banner + prompt-echo + reasoning
    + footer surrounding the model's final JSON."""
    body = '{"decision":"iterate","feedback":"item 4 unmet: replace leveraging."}'
    text = _CODEX_BANNER + "\nThe judge's reasoning goes here.\n" + body + _CODEX_FOOTER
    out = _extract_last_json_object(text)
    assert out == {
        "decision": "iterate",
        "feedback": "item 4 unmet: replace leveraging.",
    }


def test_extract_claude_markdown_fence_with_preamble():
    """Claude's actual output shape: prose preamble + ```json fence
    + JSON body + closing fence."""
    text = """\
Both proposer and reviewer agree on the core defect. Here is the verdict:

```json
{
  "decision": "implement",
  "feedback": "fix variance formula and add n<2 guard",
  "fix_instructions": "1. Change / n to / (n-1). 2. Add ValueError for n<2."
}
```
"""
    out = _extract_last_json_object(text)
    assert out["decision"] == "implement"
    assert "fix variance" in out["feedback"]
    assert "Change / n to / (n-1)" in out["fix_instructions"]


def test_extract_multiple_balanced_blocks_takes_last():
    """When the text contains multiple balanced JSON objects, the
    extractor returns the LAST one (which in real LLM output is the
    model's actual answer; earlier blocks are typically reasoning
    examples or prior-turn echoes)."""
    text = """\
Here is an example shape: {"decision": "example", "feedback": "ignore me"}
And here is the actual answer:
{"decision": "accept", "feedback": "the real answer"}
"""
    out = _extract_last_json_object(text)
    assert out == {"decision": "accept", "feedback": "the real answer"}


def test_extract_with_trailing_garbage():
    """A balanced object followed by non-JSON garbage on the same
    or following lines is still extracted; the closing brace
    correctly terminates the span."""
    text = '{"decision": "accept", "feedback": "ok"}\nsome trailing text\nmore garbage'
    out = _extract_last_json_object(text)
    assert out == {"decision": "accept", "feedback": "ok"}


def test_extract_with_string_containing_braces():
    """Brace characters inside a JSON string value do not affect the
    scanner's depth tracking. The whole top-level object is captured
    correctly even when its string values contain JSON-like fragments."""
    text = (
        '{"decision": "iterate", '
        '"feedback": "the prior verdict was {decision: iterate} again"}'
    )
    out = _extract_last_json_object(text)
    assert out["decision"] == "iterate"
    assert "{decision: iterate}" in out["feedback"]


def test_extract_with_escaped_quotes_inside_strings():
    """Backslash-escaped quotes inside a JSON string do not terminate
    the string. The scanner correctly continues until the unescaped
    closing quote."""
    text = r'{"decision": "accept", "feedback": "uses \"leveraging\" instead of using"}'
    out = _extract_last_json_object(text)
    assert out["decision"] == "accept"
    assert 'uses "leveraging" instead of using' in out["feedback"]


def test_extract_no_json_returns_parse_error():
    """A purely prose output with no ``{`` character at all raises
    _JsonExtractError with the no-balanced-object message."""
    text = "Sorry, I cannot generate JSON for this request."
    with pytest.raises(_JsonExtractError) as exc:
        _extract_last_json_object(text)
    assert "no balanced JSON object found" in str(exc.value)


def test_extract_empty_output_returns_parse_error():
    """Empty model output is treated as no-balanced-object; the
    schema_validation log record fires with the same parse_error
    outcome."""
    with pytest.raises(_JsonExtractError) as exc:
        _extract_last_json_object("")
    assert "no balanced JSON object found" in str(exc.value)


def test_extract_partial_json_returns_parse_error():
    """An unmatched ``{`` with no closing ``}`` produces no balanced
    span. The extractor raises with the no-balanced-object message,
    distinguishing 'partial output' from 'malformed JSON'."""
    text = '{"decision": "accept", "feedback": "this is incomplete'
    with pytest.raises(_JsonExtractError) as exc:
        _extract_last_json_object(text)
    assert "no balanced JSON object found" in str(exc.value)


def test_extract_nested_fences():
    """Nested markdown fences and code blocks do not affect the
    extractor; the brace scanner is agnostic to fences. The last
    balanced JSON object wins regardless of fence nesting."""
    text = """\
Here is some explanation:

```
inner code block: not JSON, just illustration
```

And the verdict:

```json
{
  "decision": "rereview",
  "feedback": "look at section 3 again"
}
```
"""
    out = _extract_last_json_object(text)
    assert out == {
        "decision": "rereview",
        "feedback": "look at section 3 again",
    }


def test_extract_returns_last_valid_json_even_when_schema_invalid():
    """Schema-agnostic invariant: the extractor returns the LAST
    balanced span that parses as JSON, even if the parsed object
    fails downstream schema validation. The extractor never retries
    earlier candidates after a schema rejection — that decision
    belongs to the schema layer.

    Pinned via this test: the extractor sees two balanced JSON
    objects, the LAST one is well-formed but does not match a
    hypothetical schema (no `decision` key). The extractor still
    returns the last, NOT the first (which here happens to look
    schema-compatible). Schema validation in production code is then
    free to report schema_error against the returned object; it must
    not silently fall back to earlier candidates.
    """
    text = """\
Here is a plausible-looking placeholder:
{"decision": "accept", "feedback": "schema-compatible placeholder"}

But the actual final response:
{"unrelated_key": "value", "another": 42}
"""
    out = _extract_last_json_object(text)
    # The LAST balanced span. Schema-incompatible but parseable.
    assert out == {"unrelated_key": "value", "another": 42}
    # Explicitly NOT the earlier schema-compatible block.
    assert "decision" not in out
