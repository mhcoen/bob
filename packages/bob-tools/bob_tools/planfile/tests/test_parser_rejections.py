"""Malformed-input rejection coverage (Codex pile-5 acceptance gap).

Stage 2.7.1 in PLAN.md lists eight rejection conditions: the three
structural anomalies caught by ``_check_structural_sanity`` (duplicate
H1, multiple Bugs sections, duplicate phase/stage ordinals) and five
tag-level malformations (annotations with an unclosed bracket, a
missing colon, or an empty value; action tags missing the colon or the
action name).

The structural anomalies do raise in compat mode and are exercised in
:class:`TestStructuralCorruptionRejection` with a minimal failing
fixture per case, asserting the user-facing error substring and the
1-based source line of the first offending heading.

The tag-level malformations do NOT raise in compat mode: every such
pattern is treated as prose so legacy hand-written PLAN.md files keep
parsing through the compat parser. The ``[feat: ]`` empty-value case is
in fact accepted as a *valid* annotation because the grammar only
requires whitespace after the colon (the value after it may be empty).
Strict mode in Stage 3 is the right place to add these rejections;
:class:`TestCompatModeToleratesMalformedTags` pins the current lenient
behavior so a silent regression in compat mode shows up in the diff,
and the gap between the task description and the parser implementation
is recorded in NOTES.md [2.7.1-2.7.2].
"""

from __future__ import annotations

import pytest

from bob_tools.planfile.model import PlanSyntaxError
from bob_tools.planfile.parser import parse_plan


class TestStructuralCorruptionRejection:
    """Each structural anomaly raises with a quotable message and line."""

    @pytest.mark.parametrize(
        ("text", "expected_substring", "expected_line"),
        [
            pytest.param(
                "# Same\n## Stage 1: A\n- [ ] x\n# Same\n",
                "duplicate top-level heading '# Same'",
                1,
                id="duplicate_h1",
            ),
            pytest.param(
                "# P\n## Stage 1: A\n- [ ] x\n## Bugs\n- [ ] one\n## Bugs\n- [ ] two\n",
                "multiple Bugs sections",
                4,
                id="multiple_bugs_sections",
            ),
            pytest.param(
                "# P\n## Stage 1: A\n- [ ] x\n## Stage 1: B\n- [ ] y\n",
                "duplicate Phase/Stage 1",
                2,
                id="duplicate_phase_ordinals",
            ),
            pytest.param(
                # A ledger-form phase lands at positional ordinal 1 and a
                # bare-digit stage header explicitly claims ordinal 1: the
                # collision only shows up once the positional ordinal is
                # modelled, so this pins the mixed-form regression.
                "# P\n## Phase phase_001: A\n- [ ] x\n## Stage 1: B\n- [ ] y\n",
                "duplicate Phase/Stage 1",
                2,
                id="duplicate_ordinals_mixed_heading_forms",
            ),
        ],
    )
    def test_rejection_raises_with_message_and_line(
        self,
        text: str,
        expected_substring: str,
        expected_line: int,
    ) -> None:
        with pytest.raises(PlanSyntaxError) as exc_info:
            parse_plan(text)
        err = exc_info.value
        assert expected_substring in err.message, (
            f"expected substring {expected_substring!r} in error message; "
            f"got {err.message!r}"
        )
        assert err.line == expected_line, (
            f"expected line {expected_line}; got {err.line}"
        )

    def test_mixed_forms_with_distinct_ordinals_parse(self) -> None:
        """A ledger-form phase (ordinal 1) followed by ``## Stage 2`` is
        legitimate: the positional-ordinal check must not fire when the
        assigned ordinals differ across heading forms."""
        text = "# P\n## Phase phase_001: A\n- [ ] x\n## Stage 2: B\n- [ ] y\n"
        plan = parse_plan(text)
        assert [p.ordinal for p in plan.phases] == [1, 2]


class TestCompatModeToleratesMalformedTags:
    """Compat-mode lenience pinning for the five tag-level cases.

    Each fixture is the minimal task body that the task description
    lists as a rejection condition. Compat mode currently leaves the
    malformed bracket text inside the task description (or, for
    ``[feat: ]``, captures it as an annotation with empty value),
    matching mcloop's tolerant behavior. Stage 3 strict mode is the
    right home for the rejections; the assertion here is only that no
    :class:`PlanSyntaxError` is raised today.
    """

    @pytest.mark.parametrize(
        "text",
        [
            pytest.param(
                '# P\n## Stage 1: A\n- [ ] hello [feat: "x"\n',
                id="annotation_unclosed_bracket",
            ),
            pytest.param(
                '# P\n## Stage 1: A\n- [ ] hello [feat "x"]\n',
                id="annotation_missing_colon",
            ),
            pytest.param(
                "# P\n## Stage 1: A\n- [ ] hello [feat: ]\n",
                id="annotation_empty_value",
            ),
            pytest.param(
                "# P\n## Stage 1: A\n- [ ] [AUTO] do thing\n",
                id="action_tag_without_colon",
            ),
            pytest.param(
                "# P\n## Stage 1: A\n- [ ] [AUTO:] do thing\n",
                id="action_tag_empty_action_name",
            ),
        ],
    )
    def test_malformed_tag_does_not_raise_in_compat_mode(self, text: str) -> None:
        parse_plan(text)
