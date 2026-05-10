"""Tests for duplo.plan_document.

The plan_document module owns the canonical PLAN.md structural
contract: H1 envelope + H2 phase header pairs as units, with the
renderer the only path that writes structural metadata. These tests
pin parser strictness, renderer determinism, sanitizer rejection
shape, and the structural-invariant validator.
"""

from __future__ import annotations

import textwrap

import pytest

from duplo.plan_document import (
    ParseError,
    PhaseUnit,
    Plan,
    PlanArtifactRejected,
    StructuralValidationError,
    parse_plan,
    render,
    sanitize_plan_artifact,
    units_by_id,
    validate_structure,
)


# ---------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------


class TestParsePlanHappyPath:
    def test_minimal_plan(self) -> None:
        text = (
            "# proj — Phase 0: Title\n"
            "## Phase phase_001: Sub\n"
            "\n"
            "body content\n"
        )
        plan = parse_plan(text)
        assert plan.project_name == "proj"
        assert plan.preamble == ""
        assert len(plan.units) == 1
        unit = plan.units[0]
        assert unit.h1_envelope == "Title"
        assert unit.phase_id == "phase_001"
        assert unit.h2_title == "Sub"
        assert unit.body == "\nbody content\n"

    def test_two_phases_with_preamble(self) -> None:
        text = (
            "# proj\n"
            "\n"
            "Project description across multiple\n"
            "lines.\n"
            "\n"
            "# proj — Phase 0: Scaffold\n"
            "## Phase phase_001: Set up\n"
            "\n"
            "- [ ] step 1\n"
            "\n"
            "# proj — Phase 1: Build\n"
            "## Phase phase_002: Implement\n"
            "\n"
            "- [ ] step 2\n"
        )
        plan = parse_plan(text)
        assert plan.project_name == "proj"
        # preamble is everything before the first H1 envelope (the
        # plain '# proj' is NOT an envelope because it lacks the
        # em-dash + Phase pattern).
        assert plan.preamble.startswith("# proj\n")
        assert "Project description" in plan.preamble
        assert len(plan.units) == 2
        assert plan.units[0].h1_envelope == "Scaffold"
        assert plan.units[0].phase_id == "phase_001"
        assert plan.units[0].h2_title == "Set up"
        assert "step 1" in plan.units[0].body
        assert plan.units[1].h1_envelope == "Build"
        assert plan.units[1].phase_id == "phase_002"
        assert plan.units[1].h2_title == "Implement"
        assert "step 2" in plan.units[1].body

    def test_no_envelope_h1_returns_empty_units(self) -> None:
        """A plan with no H1 envelope returns an empty units tuple
        and the entire text as preamble. This handles pre-canonical
        plans gracefully; the assembly path that calls parse_plan
        treats empty units as 'nothing to substitute'."""
        text = "# title\n\nsome body\n"
        plan = parse_plan(text)
        assert plan.units == ()
        assert plan.project_name == ""
        assert plan.preamble == text

    def test_round_trip_render_equals_parse(self) -> None:
        """render(parse(text)) == text for canonical input. Pinned so
        a future parser/renderer change can't silently lose
        information through round-trip."""
        text = (
            "# proj — Phase 0: Scaffold\n"
            "## Phase phase_001: Set up\n"
            "\n"
            "- [ ] step a\n"
            "- [ ] step b\n"
            "\n"
            "# proj — Phase 1: Build\n"
            "## Phase phase_002: Implement\n"
            "\n"
            "body for build\n"
        )
        assert render(parse_plan(text)) == text

    def test_round_trip_with_preamble(self) -> None:
        text = (
            "# fswatch-run-smoke\n"
            "\n"
            "Build fswatch-run, a small CLI...\n"
            "\n"
            "# fswatch-run-smoke — Phase 0: Scaffold\n"
            "## Phase phase_001: Wire entry point\n"
            "\n"
            "- [ ] x\n"
        )
        assert render(parse_plan(text)) == text


class TestParsePlanRejections:
    def test_h1_with_no_h2_raises(self) -> None:
        text = (
            "# proj — Phase 0: Title\n"
            "\n"
            "no H2 underneath\n"
        )
        with pytest.raises(ParseError, match="not followed by an H2"):
            parse_plan(text)

    def test_h1_with_multiple_h2s_raises(self) -> None:
        """The fswatch-run-smoke corruption shape: one H1 with
        several H2s underneath. parse_plan rejects this so the
        corruption can't sneak through assembly."""
        text = (
            "# proj — Phase 1: Multi\n"
            "## Phase phase_001: First\n"
            "body 1\n"
            "## Phase phase_002: Second\n"
            "body 2\n"
            "## Phase phase_003: Third\n"
            "body 3\n"
        )
        with pytest.raises(ParseError, match="3 H2 phase headers"):
            parse_plan(text)

    def test_h2_in_preamble_raises(self) -> None:
        """An H2 sitting before the first H1 envelope has no parent
        unit; reject rather than silently dropping it."""
        text = (
            "## Phase phase_001: Stray\n"
            "\n"
            "# proj — Phase 0: Title\n"
            "## Phase phase_002: Sub\n"
            "body\n"
        )
        with pytest.raises(ParseError, match="before the first H1 envelope"):
            parse_plan(text)

    def test_h2_in_text_with_no_h1_raises(self) -> None:
        text = "preamble only\n## Phase phase_001: Stray\nbody\n"
        with pytest.raises(ParseError, match="no preceding H1 envelope"):
            parse_plan(text)

    def test_intervening_text_between_h1_and_h2_raises(self) -> None:
        """Between H1 and H2 only whitespace is allowed. A council
        finding or other narrative there is the kind of mid-envelope
        bloat that has accumulated across reauthor passes; reject."""
        text = (
            "# proj — Phase 0: Title\n"
            "Verified: some council narrative\n"
            "## Phase phase_001: Sub\n"
            "\n"
            "body\n"
        )
        with pytest.raises(
            ParseError, match="non-whitespace content"
        ):
            parse_plan(text)

    def test_blank_lines_between_h1_and_h2_allowed(self) -> None:
        """Whitespace lines between H1 and H2 are tolerated; only
        non-whitespace content is rejected. Round-trip is preserved
        because render emits no blank line between H1 and H2 — that
        normalization is intentional."""
        text = (
            "# proj — Phase 0: Title\n"
            "\n"
            "\n"
            "## Phase phase_001: Sub\n"
            "body\n"
        )
        # parse_plan accepts.
        plan = parse_plan(text)
        assert plan.units[0].phase_id == "phase_001"
        # render emits the canonical no-blank-line form.
        assert render(plan) == (
            "# proj — Phase 0: Title\n"
            "## Phase phase_001: Sub\n"
            "body\n"
        )

    def test_mismatched_project_name_across_envelopes_raises(self) -> None:
        text = (
            "# proj — Phase 0: A\n"
            "## Phase phase_001: a\n"
            "\n"
            "# different — Phase 1: B\n"
            "## Phase phase_002: b\n"
            "\n"
        )
        with pytest.raises(
            ParseError, match="uses project name 'different'"
        ):
            parse_plan(text)


class TestParsePlanPhase0Codification:
    """Phase 0 follows the same H1+H2 contract as every other phase.

    Codify it: Phase 0 is NOT a special case. The synthesizer template
    emits the H1 envelope for Phase 0 just like any other phase, and
    parse_plan applies the same rules.
    """

    def test_phase_0_gets_h1_envelope_like_any_other(self) -> None:
        text = (
            "# proj — Phase 0: Scaffold\n"
            "## Phase phase_001: Set up\n"
            "\n"
            "- [ ] x\n"
        )
        plan = parse_plan(text)
        assert plan.units[0].h1_envelope == "Scaffold"

    def test_phase_0_without_h1_envelope_raises(self) -> None:
        """A bare H2 with no H1 envelope above it is rejected even
        for Phase 0."""
        text = "## Phase phase_001: Set up\n- [ ] x\n"
        with pytest.raises(ParseError, match="no preceding H1 envelope"):
            parse_plan(text)


# ---------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------


class TestRender:
    def test_render_assigns_ordinals_by_position(self) -> None:
        """A Plan whose units are reordered renumbers the H1
        ordinals to match the new positions. Substituting a unit
        thus automatically renumbers downstream H1s."""
        plan = Plan(
            project_name="proj",
            preamble="",
            units=(
                PhaseUnit(
                    h1_envelope="A",
                    phase_id="phase_001",
                    h2_title="alpha",
                    body="body a\n",
                ),
                PhaseUnit(
                    h1_envelope="B",
                    phase_id="phase_002",
                    h2_title="beta",
                    body="body b\n",
                ),
                PhaseUnit(
                    h1_envelope="C",
                    phase_id="phase_003",
                    h2_title="gamma",
                    body="body c\n",
                ),
            ),
        )
        out = render(plan)
        # Each unit's H1 carries its position as the ordinal.
        assert "Phase 0: A" in out
        assert "Phase 1: B" in out
        assert "Phase 2: C" in out

    def test_render_emits_preamble_verbatim(self) -> None:
        plan = Plan(
            project_name="proj",
            preamble="# proj\n\nproject description\n\n",
            units=(
                PhaseUnit(
                    h1_envelope="A",
                    phase_id="phase_001",
                    h2_title="alpha",
                    body="body\n",
                ),
            ),
        )
        out = render(plan)
        assert out.startswith("# proj\n\nproject description\n\n")

    def test_render_empty_units_emits_only_preamble(self) -> None:
        plan = Plan(
            project_name="",
            preamble="just preamble\nno units\n",
            units=(),
        )
        assert render(plan) == "just preamble\nno units\n"


# ---------------------------------------------------------------------
# Sanitizer
# ---------------------------------------------------------------------


class TestSanitizePlanArtifact:
    def test_clean_text_passes_through(self) -> None:
        text = "# proj — Phase 0: T\n## Phase phase_001: s\nbody\n"
        assert sanitize_plan_artifact(text) == text

    def test_verdict_with_decision_key_rejects(self) -> None:
        text = textwrap.dedent(
            """\
            # proj — Phase 0: T
            ## Phase phase_001: s

            body

            ```json
            {"decision": "accept", "feedback": "ok"}
            ```
            """
        )
        with pytest.raises(PlanArtifactRejected, match="decision"):
            sanitize_plan_artifact(text)

    def test_verdict_with_lineage_key_rejects(self) -> None:
        text = textwrap.dedent(
            """\
            ```json
            {"lineage": {"phases": []}}
            ```
            """
        )
        with pytest.raises(PlanArtifactRejected, match="lineage"):
            sanitize_plan_artifact(text)

    def test_non_verdict_json_passes_through(self) -> None:
        """A fenced JSON block with non-verdict shape is not the
        contract violation we're catching; pass it through."""
        text = textwrap.dedent(
            """\
            example config:

            ```json
            {"name": "fswatch", "version": "0.1"}
            ```
            """
        )
        assert sanitize_plan_artifact(text) == text

    def test_fenced_python_passes_through(self) -> None:
        text = textwrap.dedent(
            """\
            ```python
            def f(): pass
            ```
            """
        )
        assert sanitize_plan_artifact(text) == text

    def test_fenced_bash_passes_through(self) -> None:
        text = "```bash\nls -la\n```\n"
        assert sanitize_plan_artifact(text) == text

    def test_malformed_json_passes_through(self) -> None:
        """A fenced ``json`` block whose contents don't decode is
        ignored. The user can fix the typo; we won't reject what we
        can't classify."""
        text = "```json\nnot { valid json\n```\n"
        assert sanitize_plan_artifact(text) == text

    def test_multiple_blocks_first_violator_rejects(self) -> None:
        text = textwrap.dedent(
            """\
            ```json
            {"name": "ok"}
            ```

            ```json
            {"decision": "accept"}
            ```
            """
        )
        with pytest.raises(PlanArtifactRejected):
            sanitize_plan_artifact(text)


# ---------------------------------------------------------------------
# Structural validator
# ---------------------------------------------------------------------


class TestValidateStructure:
    def _basic_unit(
        self,
        phase_id: str = "phase_001",
        body: str = "ok body\n",
    ) -> PhaseUnit:
        return PhaseUnit(
            h1_envelope="Title",
            phase_id=phase_id,
            h2_title="sub",
            body=body,
        )

    def test_clean_plan_passes(self) -> None:
        plan = Plan(
            project_name="proj",
            preamble="",
            units=(self._basic_unit(),),
        )
        validate_structure(plan)  # no raise

    def test_duplicate_phase_id_raises(self) -> None:
        plan = Plan(
            project_name="proj",
            preamble="",
            units=(
                self._basic_unit(phase_id="phase_001"),
                self._basic_unit(phase_id="phase_001"),
            ),
        )
        with pytest.raises(StructuralValidationError, match="duplicate phase_id"):
            validate_structure(plan)

    def test_h1_in_unit_body_raises(self) -> None:
        """A unit body that contains an H1-shaped line means the H1
        envelope was misparsed as content somewhere upstream. Refuse
        to validate."""
        body = "ok line\n# stray H1 inside body\nmore content\n"
        plan = Plan(
            project_name="proj",
            preamble="",
            units=(self._basic_unit(body=body),),
        )
        with pytest.raises(
            StructuralValidationError, match="H1-shaped line"
        ):
            validate_structure(plan)

    def test_verdict_json_in_unit_body_raises(self) -> None:
        body = textwrap.dedent(
            """\
            body line

            ```json
            {"decision": "accept", "feedback": "x"}
            ```

            more body
            """
        )
        plan = Plan(
            project_name="proj",
            preamble="",
            units=(self._basic_unit(body=body),),
        )
        with pytest.raises(
            StructuralValidationError, match="verdict-shaped"
        ):
            validate_structure(plan)

    def test_accumulates_multiple_violations(self) -> None:
        """Like _validate_lineage_structural in reauthor.py and
        _validate_canonical_plan_markdown in council.py, errors
        accumulate so callers see the full picture."""
        body_with_h1 = "# stray H1\n"
        body_with_verdict = (
            "intro\n\n```json\n{\"lineage\": {}}\n```\n"
        )
        plan = Plan(
            project_name="proj",
            preamble="",
            units=(
                self._basic_unit(phase_id="phase_001", body=body_with_h1),
                self._basic_unit(phase_id="phase_001", body=body_with_verdict),
            ),
        )
        with pytest.raises(StructuralValidationError) as exc_info:
            validate_structure(plan)
        msg = str(exc_info.value)
        assert "duplicate phase_id" in msg
        assert "H1-shaped line" in msg
        assert "verdict-shaped" in msg

    def test_empty_units_passes(self) -> None:
        plan = Plan(project_name="", preamble="just preamble\n", units=())
        validate_structure(plan)


# ---------------------------------------------------------------------
# units_by_id helper
# ---------------------------------------------------------------------


class TestUnitsById:
    def test_indexes_by_phase_id(self) -> None:
        units = (
            PhaseUnit(h1_envelope="A", phase_id="phase_001", h2_title="a", body=""),
            PhaseUnit(h1_envelope="B", phase_id="phase_002", h2_title="b", body=""),
        )
        index = units_by_id(units)
        assert set(index.keys()) == {"phase_001", "phase_002"}

    def test_duplicate_id_raises(self) -> None:
        units = (
            PhaseUnit(h1_envelope="A", phase_id="phase_001", h2_title="a", body=""),
            PhaseUnit(h1_envelope="B", phase_id="phase_001", h2_title="b", body=""),
        )
        with pytest.raises(StructuralValidationError, match="duplicate"):
            units_by_id(units)


# ---------------------------------------------------------------------
# Regression: the fswatch-run-smoke corruption shape
# ---------------------------------------------------------------------


class TestFswatchRunSmokeCorruptionRegression:
    """The fswatch-run-smoke fixture's PLAN.md corruption shape:
    one H1 envelope (Phase 1) under which sit four H2 sections plus
    two embedded fenced ``json`` verdict blocks. The new parser
    rejects this shape at parse time, so preserve-by-default cannot
    re-emit the corruption verbatim across reauthor passes."""

    def _corruption_plan_text(self) -> str:
        return textwrap.dedent(
            """\
            # fswatch-run-smoke

            Project description.

            # fswatch-run-smoke — Phase 0: Scaffold
            ## Phase phase_001: Set up

            - [x] done

            # fswatch-run-smoke — Phase 1: Watch and run
            ## Phase phase_018: First subsection

            - [ ] task

            ```json
            {"decision": "accept", "lineage": {"phases": []}}
            ```

            ## Phase phase_015: Second subsection

            - [ ] task

            ## Phase phase_019: Third subsection

            ```json
            {"lineage": {"phases": []}}
            ```

            ## Phase phase_017: Fourth subsection

            - [ ] task
            """
        )

    def test_parse_raises_on_multiple_h2_under_one_h1(self) -> None:
        with pytest.raises(ParseError, match="H2 phase headers"):
            parse_plan(self._corruption_plan_text())

    def test_sanitize_rejects_embedded_verdict_in_corrupt_plan(self) -> None:
        """Even before parse, sanitize_plan_artifact catches the
        embedded verdict JSON. Reauthor calls sanitize first, so the
        rejection surfaces with the named reason rather than a
        downstream parse error."""
        with pytest.raises(PlanArtifactRejected):
            sanitize_plan_artifact(self._corruption_plan_text())
