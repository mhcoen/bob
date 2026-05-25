"""Tests for :mod:`bob_tools.planfile.plan_artifact`.

The sanitizer gates the LLM-emitted reauthor response before it
reaches :func:`bob_tools.planfile.parse_plan`. These tests pin:

  - Pass-through behavior on clean text and on non-verdict fenced
    blocks (Python, bash, malformed JSON, JSON objects whose shape
    does not look like a verdict).
  - Extraction of a single trailing fenced ``json`` block decoding to
    a verdict-shaped object.
  - Rejection of the two contract violations the original duplo
    sanitizer was built to surface: a verdict-shaped fenced block
    sitting mid-body, and multiple verdict-shaped blocks (ambiguous
    "which one is the trailing verdict").

The behavior contract was ported verbatim from the duplo
``plan_document`` sanitizer that this module replaces; the duplo
tests at ``duplo/tests/test_plan_document.py`` continue to exist
against the legacy module until that module is deleted, so the
behavior is double-pinned during the transition.
"""

from __future__ import annotations

import textwrap

import pytest

from bob_tools.planfile import PlanArtifactRejected, sanitize_plan_artifact


class TestPassThrough:
    def test_clean_text_passes_through_with_no_extraction(self) -> None:
        text = "# proj — Phase 0: T\n## Phase phase_001: s\nbody\n"
        plan_text, extracted = sanitize_plan_artifact(text)
        assert plan_text == text
        assert extracted is None

    def test_non_verdict_json_passes_through(self) -> None:
        text = textwrap.dedent(
            """\
            example config:

            ```json
            {"name": "fswatch", "version": "0.1"}
            ```
            """
        )
        plan_text, extracted = sanitize_plan_artifact(text)
        assert plan_text == text
        assert extracted is None

    def test_fenced_python_passes_through(self) -> None:
        text = textwrap.dedent(
            """\
            ```python
            def f(): pass
            ```
            """
        )
        plan_text, extracted = sanitize_plan_artifact(text)
        assert plan_text == text
        assert extracted is None

    def test_fenced_bash_passes_through(self) -> None:
        text = "```bash\nls -la\n```\n"
        plan_text, extracted = sanitize_plan_artifact(text)
        assert plan_text == text
        assert extracted is None

    def test_malformed_json_passes_through(self) -> None:
        text = "```json\nnot { valid json\n```\n"
        plan_text, extracted = sanitize_plan_artifact(text)
        assert plan_text == text
        assert extracted is None


class TestTrailingVerdictExtraction:
    def test_trailing_verdict_extracted(self) -> None:
        text = textwrap.dedent(
            """\
            # proj — Phase 0: T
            ## Phase phase_001: s

            body content

            ```json
            {"decision": "accept", "feedback": "ok"}
            ```
            """
        )
        plan_text, extracted = sanitize_plan_artifact(text)
        assert extracted == {"decision": "accept", "feedback": "ok"}
        assert "## Phase phase_001: s" in plan_text
        assert "body content" in plan_text
        assert "```json" not in plan_text
        assert "decision" not in plan_text

    def test_trailing_verdict_with_lineage_key_extracted(self) -> None:
        text = textwrap.dedent(
            """\
            body
            ```json
            {"lineage": {"phases": []}}
            ```
            """
        )
        plan_text, extracted = sanitize_plan_artifact(text)
        assert extracted == {"lineage": {"phases": []}}
        assert "```json" not in plan_text

    def test_non_verdict_block_then_trailing_verdict(self) -> None:
        text = textwrap.dedent(
            """\
            ```json
            {"name": "fswatch"}
            ```

            ```json
            {"decision": "accept"}
            ```
            """
        )
        plan_text, extracted = sanitize_plan_artifact(text)
        assert extracted == {"decision": "accept"}
        assert '{"name": "fswatch"}' in plan_text
        assert '"decision": "accept"' not in plan_text


class TestRejection:
    def test_mid_body_verdict_rejects(self) -> None:
        text = textwrap.dedent(
            """\
            body before

            ```json
            {"decision": "accept"}
            ```

            body after
            """
        )
        with pytest.raises(PlanArtifactRejected, match=r"NOT the\s+trailing"):
            sanitize_plan_artifact(text)

    def test_multiple_verdict_blocks_reject(self) -> None:
        text = textwrap.dedent(
            """\
            ```json
            {"decision": "accept"}
            ```

            ```json
            {"lineage": {"phases": []}}
            ```
            """
        )
        with pytest.raises(PlanArtifactRejected, match="2 fenced 'json' blocks"):
            sanitize_plan_artifact(text)
