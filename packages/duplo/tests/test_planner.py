"""Tests for duplo.planner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import re

import pytest

from bob_tools.planfile import Plan
from bob_tools.planfile.renderer import render_plan

from duplo.extractor import Feature
from duplo.planner import (
    CanonicalH1OrdinalError,
    CompletedTask,
    _NEXT_PHASE_SYSTEM,
    _PHASE_SYSTEM,
    _PLAN_FILENAME,
    _detect_next_phase_number,
    _ensure_h1_heading,
    _escape_mcloop_tags,
    _strip_bugs_section,
    _strip_fences,
    _strip_trailing_commentary,
    append_test_tasks,
    generate_next_phase_plan,
    generate_phase_plan,
    parse_completed_tasks,
    save_plan,
    validate_h1_ordinal_sequence,
)
from duplo.questioner import BuildPreferences


def _sample_features() -> list[Feature]:
    return [
        Feature(name="User auth", description="Sign up and log in.", category="core"),
        Feature(name="Dashboard", description="Overview of activity.", category="ui"),
    ]


def _sample_prefs() -> BuildPreferences:
    return BuildPreferences(
        platform="web",
        language="Python/FastAPI",
        constraints=["PostgreSQL only"],
        preferences=["Use pytest"],
    )


def _canonical_body(
    phase_id: str = "phase_001",
    title: str = "Core Auth",
    *,
    extra_tasks: tuple[str, ...] = (),
    phase_prose: str = "",
) -> str:
    """Return a canonical Slice C synthesizer body the council pipeline
    accepts as input to :func:`typed_plan_from_synthesizer_text`.

    The runtime owns ``phase_id``; tests pass the same id the runtime
    will compute (``phase_001`` against an empty PLAN.md) so the
    constructed-mode validator sees the body the synthesizer is
    contractually required to emit. ``phase_prose`` lives below the
    phase header so :func:`bob_tools.planfile.parser.parse_plan`
    attaches it to ``Phase.prose`` (the planfile-level ``preamble``
    field is reserved for content above the first phase, which only
    appears when there is a ``# <project>`` H1).
    """
    lines: list[str] = []
    lines.append(f"## Phase {phase_id}: {title}")
    lines.append("")
    if phase_prose:
        lines.extend(phase_prose.rstrip("\n").split("\n"))
        lines.append("")
    lines.append("- [ ] Set up project structure")
    for extra in extra_tasks:
        lines.append(extra)
    lines.append("")
    return "\n".join(lines)


def _plan_to_text(plan: Plan) -> str:
    """Render a typed :class:`Plan` to PLAN.md bytes via the planfile
    renderer so tests can grep for canonical-form structural markers
    (``## Phase phase_NNN:``, ``<!-- phase_id: ... -->``, task lines).
    """
    return render_plan(plan)


def _walk_with_children(task):
    """Yield ``task`` and its nested children (depth-first), used by
    typed-plan assertions that compare against pre-T-000186 flat
    grep-style checks."""
    yield task
    for child in task.children:
        yield from _walk_with_children(child)


_SAMPLE_PLAN = _canonical_body()


class TestGeneratePhasePlan:
    def test_returns_typed_plan(self):
        """After T-000186, generate_phase_plan returns a typed
        :class:`bob_tools.planfile.Plan` value, not a markdown string.
        The synthesizer body (``## Phase phase_001: <title>`` with task
        checkboxes) is parsed, rebuilt as a constructed plan, and
        validated end-to-end via
        :func:`duplo.council.typed_plan_from_synthesizer_text`.
        """
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert isinstance(result, Plan)
        assert len(result.phases) == 1
        phase = result.phases[0]
        assert phase.phase_id == "phase_001"
        assert phase.title == "Core Auth"
        assert any(
            "Set up project structure" in t.text for t in phase.tasks
        )

    def test_passes_source_url_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://acme.io",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "https://acme.io" in prompt

    def test_passes_features_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "User auth" in prompt
        assert "Dashboard" in prompt

    def test_passes_preferences_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "Python/FastAPI" in prompt
        assert "PostgreSQL only" in prompt

    def test_passes_platform_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "web" in prompt

    def test_handles_empty_features(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                [],
                _sample_prefs(),
            )
        assert isinstance(result, Plan)

    def test_handles_empty_constraints_and_preferences(self):
        prefs = BuildPreferences(platform="cli", language="Go", constraints=[], preferences=[])
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                prefs,
            )
        prompt = mock_query.call_args[0][0]
        assert "(none)" in prompt
        assert isinstance(result, Plan)

    def test_spec_text_injected_into_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                spec_text="Build a calculator app.",
            )
        prompt = mock_query.call_args[0][0]
        assert "Build a calculator app." in prompt
        assert "authoritative" in prompt.lower()

    def test_spec_text_empty_not_in_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                spec_text="",
            )
        prompt = mock_query.call_args[0][0]
        assert "Product specification" not in prompt

    def test_prior_phases_files_listed_in_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                prior_phases_files=["Package.swift", "Sources/App/App.swift"],
            )
        prompt = mock_query.call_args[0][0]
        assert "Files already created in earlier phases" in prompt
        assert "do NOT recreate" in prompt
        assert "- Package.swift" in prompt
        assert "- Sources/App/App.swift" in prompt

    def test_prior_phases_files_empty_omitted(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                prior_phases_files=[],
            )
        prompt = mock_query.call_args[0][0]
        assert "Files already created in earlier phases" not in prompt

    def test_prior_phases_files_none_omitted(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "Files already created in earlier phases" not in prompt

    def test_includes_issues_in_prompt(self):
        phase = {
            "phase": 2,
            "title": "Polish",
            "goal": "Fix known issues",
            "features": ["Dashboard"],
            "test": "All issues resolved",
            "issues": ["Sidebar overlaps on mobile", "Login timeout too short"],
        }
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
            )
        prompt = mock_query.call_args[0][0]
        assert "Sidebar overlaps on mobile" in prompt
        assert "Login timeout too short" in prompt
        assert "Known issues to fix" in prompt

    def test_no_issues_block_when_empty(self):
        phase = {
            "phase": 2,
            "title": "Polish",
            "goal": "Add features",
            "features": ["Dashboard"],
            "test": "",
            "issues": [],
        }
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
            )
        prompt = mock_query.call_args[0][0]
        assert "Known issues to fix" not in prompt

    def test_no_issues_block_when_no_phase(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "Known issues to fix" not in prompt

    def test_phase_number_overrides_phase_dict(self):
        phase = {
            "phase": 0,
            "title": "Core",
            "goal": "Build core",
            "features": ["Auth"],
            "test": "",
        }
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
                phase_number=3,
            )
        prompt = mock_query.call_args[0][0]
        assert "Phase 3:" in prompt
        assert "Phase 0:" not in prompt

    def test_phase_number_used_without_phase_dict(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase_number=5,
            )
        prompt = mock_query.call_args[0][0]
        assert "Phase 5:" in prompt

    def test_phase_number_defaults_to_phase_dict(self):
        phase = {
            "phase": 2,
            "title": "Polish",
            "goal": "Polish it",
            "features": ["Dashboard"],
            "test": "",
        }
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
            )
        prompt = mock_query.call_args[0][0]
        assert "Phase 2:" in prompt


class TestGeneratePhasePlanH1Heading:
    """Verify generate_phase_plan() returns a typed Plan whose first
    phase carries the runtime-computed ``phase_NNN`` id.

    Pre-T-000186, this class pinned a markdown ``# <project> — Phase N:
    <title>`` H1 envelope on the returned string. After T-000186, the
    return value is a structured :class:`bob_tools.planfile.Plan`; the
    runtime owns phase identity and the synthesizer must emit a body
    whose ``## Phase phase_NNN:`` header matches the supplied id.
    These tests now cover the structural contract that replaced the
    string-envelope checks.
    """

    def test_returned_value_is_typed_plan_with_canonical_phase_id(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert isinstance(result, Plan)
        assert result.phases
        assert result.phases[0].phase_id == "phase_001"

    def test_synthesizer_must_emit_runtime_phase_id(self):
        """When the synthesizer skips the runtime-supplied
        ``phase_NNN`` header, :func:`generate_phase_plan` raises
        :class:`bob_tools.planfile.PlanValidationError` rather than
        silently rendering its own ordinal. The runtime owns phase
        identity; the synthesizer must honor it.
        """
        from bob_tools.planfile import PlanValidationError

        no_phase_header = (
            "Some preamble describing the phase.\n\n- [ ] Build thing\n"
        )
        with patch("duplo.planner.query", return_value=no_phase_header):
            with pytest.raises(PlanValidationError):
                generate_phase_plan(
                    "https://example.com",
                    _sample_features(),
                    _sample_prefs(),
                    project_name="Numi",
                    phase_number=0,
                )

    def test_synthesizer_using_wrong_phase_id_raises(self):
        """The synthesizer emits ``## Phase phase_007: Wrong`` while
        the runtime (against an empty target_dir) expects ``phase_001``.
        :func:`typed_plan_from_synthesizer_text` raises rather than
        accept the mismatch.
        """
        from bob_tools.planfile import PlanValidationError

        wrong_id_body = _canonical_body(
            phase_id="phase_007", title="Wrong"
        )
        with patch("duplo.planner.query", return_value=wrong_id_body):
            with pytest.raises(PlanValidationError):
                generate_phase_plan(
                    "https://example.com",
                    _sample_features(),
                    _sample_prefs(),
                    project_name="Different",
                )

    def test_canonical_body_round_trips_through_renderer(self):
        """The typed Plan returned by :func:`generate_phase_plan`
        renders to a canonical PLAN.md whose phase header line is
        the planfile renderer's ``## Phase N: <title>`` form (with
        an accompanying ``<!-- phase_id: phase_NNN -->`` comment),
        not the retired ``# <project> — Phase N: <title>`` H1
        envelope.
        """
        body = _canonical_body(phase_id="phase_001", title="Advanced")
        with patch("duplo.planner.query", return_value=body):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                project_name="Numi",
            )
        text = _plan_to_text(result)
        assert "## Phase 1: Advanced" in text
        assert "<!-- phase_id: phase_001 -->" in text
        # The legacy H1 envelope is gone.
        assert "# Numi — Phase " not in text

    def test_synthesizer_phase_prose_preserved_on_phase(self):
        """Prose the synthesizer emits between the ``## Phase
        phase_NNN:`` header and the first task lands on
        :attr:`Phase.prose` so no body content is silently lost. The
        Plan-level ``preamble`` only fills when the body has a
        ``# <project>`` H1 above the first phase, which the
        synthesizer must not author (the runtime owns the project
        envelope).
        """
        body = _canonical_body(
            phase_id="phase_001",
            title="Advanced",
            phase_prose="Python/SwiftUI calculator app.",
        )
        with patch("duplo.planner.query", return_value=body):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                project_name="Numi",
            )
        assert isinstance(result, Plan)
        assert result.phases[0].phase_id == "phase_001"
        assert "Python/SwiftUI calculator app" in result.phases[0].prose

    def test_phase_dict_drives_runtime_title(self):
        """The roadmap-supplied phase title appears verbatim in the
        synthesizer prompt; the synthesizer body controls the typed
        Plan's phase title (which the renderer emits in
        ``## Phase N: <title>``).
        """
        body = _canonical_body(phase_id="phase_001", title="Integrations")
        phase = {
            "phase": 5,
            "title": "Integrations",
            "goal": "Wire it up",
            "features": [],
            "test": "",
        }
        with patch("duplo.planner.query", return_value=body):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
                project_name="Widget",
            )
        assert isinstance(result, Plan)
        assert result.phases[0].title == "Integrations"


class TestEnsureH1Heading:
    """Strip-and-render contract.

    Phase ordinals in the outer ``# <project> — Phase N: <title>`` H1
    are execution metadata owned by Duplo's roadmap state, not the
    synthesizer. _ensure_h1_heading strips ANY model-authored
    ``# X — Phase N: ...`` H1 from the body and renders the canonical
    H1 from (project_name, phase_num, phase_title). This is the
    durable fix for the "synthesizer guesses the phase ordinal,
    gets it wrong" anti-pattern. Codex framing: model emits phase
    content; Duplo wraps it in the deterministic envelope.
    """

    def test_overrides_synthesizer_h1_with_canonical(self):
        """Synthesizer emits an H1 with project name 'App' and ordinal
        1; Duplo's roadmap state says project 'X' and ordinal 1. The
        canonical H1 wins regardless of what the synthesizer wrote."""
        result = _ensure_h1_heading(
            "\n\n# App — Phase 1: Core\n", "X", 1, "Core"
        )
        assert result == "# X — Phase 1: Core\n"

    def test_overrides_when_synthesizer_uses_wrong_ordinal(self):
        """Synthesizer emits 'Phase 7' when Duplo's roadmap says
        Phase 2. The canonical H1 (Phase 2) overwrites the wrong
        ordinal — the bug fix."""
        body = "# Widget — Phase 7: WrongOrdinal\n\n- [ ] Real task\n"
        result = _ensure_h1_heading(body, "Widget", 2, "Polish")
        assert result.startswith("# Widget — Phase 2: Polish\n")
        assert "Phase 7" not in result
        assert "WrongOrdinal" not in result
        assert "- [ ] Real task" in result

    def test_strips_multiple_phase_h1s(self):
        """Synthesizer emits multiple stray phase H1s; all are
        stripped, only Duplo's canonical H1 remains."""
        body = (
            "# Foo — Phase 1: Stray one\n"
            "\n"
            "# Bar — Phase 5: Stray two\n"
            "\n"
            "- [ ] Real task\n"
        )
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        phase_h1_count = sum(
            1 for line in result.splitlines() if " — Phase " in line and line.startswith("# ")
        )
        assert phase_h1_count == 1
        assert result.startswith("# Real — Phase 3: Real Title\n")
        assert "- [ ] Real task" in result
        assert "Stray one" not in result
        assert "Stray two" not in result

    # ----------- Broader strip regex (clarification #2) -----------

    def test_strips_phase_h1_with_hyphen_separator(self):
        """Synthesizer uses ASCII hyphen-minus instead of em-dash."""
        body = "# project - Phase 1: Hyphen sep\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        assert "Hyphen sep" not in result
        assert result.startswith("# Real — Phase 3: Real Title")

    def test_strips_phase_h1_with_en_dash(self):
        """Synthesizer uses en-dash instead of em-dash."""
        body = "# project – Phase 1: En dash\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        assert "En dash" not in result
        assert result.startswith("# Real — Phase 3: Real Title")

    def test_strips_phase_h1_without_separator(self):
        """Synthesizer omits the separator entirely."""
        body = "# project Phase 1: No sep\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        assert "No sep" not in result
        assert result.startswith("# Real — Phase 3: Real Title")

    def test_strips_phase_h1_without_project_prefix(self):
        """Synthesizer drops the project name and writes a bare
        ``# Phase N: ...`` heading."""
        body = "# Phase 1: Bare phase\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        assert "Bare phase" not in result
        assert result.startswith("# Real — Phase 3: Real Title")

    def test_strips_phase_h1_with_lowercase_phase(self):
        """Synthesizer writes ``phase`` in lowercase. Strip is
        case-insensitive."""
        body = "# project — phase 1: Lowercase\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        assert "Lowercase" not in result
        assert result.startswith("# Real — Phase 3: Real Title")

    def test_strips_phase_h1_with_uppercase_phase(self):
        """All-caps PHASE."""
        body = "# project — PHASE 1: Uppercase\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        assert "Uppercase" not in result
        assert result.startswith("# Real — Phase 3: Real Title")

    def test_strips_phase_h1_with_extra_whitespace(self):
        """Extra whitespace around the digit and colon."""
        body = "# project — Phase   1   :   Whitespace\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        assert "Whitespace" not in result
        assert result.startswith("# Real — Phase 3: Real Title")

    def test_does_not_strip_h2_phase_headings(self):
        """The inner ``## Phase phase_NNN:`` semantic header MUST
        survive the strip; it's the phase_id boundary that mcloop's
        Slice C parser anchors on. Strip is anchored at ``# ``
        (single hash), not ``## ``."""
        body = (
            "## Phase phase_001: Inner header\n"
            "\n"
            "- [ ] Task\n"
        )
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        assert "## Phase phase_001:" in result, (
            "the inner Slice C semantic header must be preserved"
        )
        assert result.startswith("# Real — Phase 3: Real Title")

    def test_does_not_strip_unrelated_h1_text(self):
        """An H1 line that mentions 'phase' but doesn't match the
        ``Phase \\d+:`` shape (e.g., 'phases of work') is content,
        not a phase H1, and must not be stripped."""
        body = "# Phases of work overview\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "Real", 3, "Real Title")
        # The non-phase H1 stays in the body (with Duplo's canonical
        # prepended on top).
        assert "Phases of work overview" in result
        assert result.startswith("# Real — Phase 3: Real Title")

    def test_prepends_when_no_heading(self):
        result = _ensure_h1_heading("plain text\n", "Widget", 2, "Polish")
        assert result.startswith("# Widget — Phase 2: Polish\n\nplain text")

    def test_prepends_when_h2_only(self):
        result = _ensure_h1_heading("## sub\n\n- [ ] x", "App", 0, "Scaffold")
        assert result.startswith("# App — Phase 0: Scaffold\n\n## sub")

    def test_empty_content_produces_heading_only(self):
        assert _ensure_h1_heading("", "App", 0, "Scaffold") == "# App — Phase 0: Scaffold\n"

    def test_empty_project_name_uses_fallback(self):
        result = _ensure_h1_heading("- [ ] Task", "", 1, "Core")
        assert result.startswith("# App — Phase 1: Core")

    def test_hash_without_space_is_not_h1(self):
        result = _ensure_h1_heading("#foo\n", "App", 1, "Core")
        assert result.startswith("# App — Phase 1: Core\n\n#foo")

    def test_empty_h1_line_is_not_accepted(self):
        result = _ensure_h1_heading("# \n- [ ] Task", "App", 1, "Core")
        assert result.startswith("# App — Phase 1: Core")

    def test_strips_preamble_before_h1(self):
        """LLM meta-commentary before the H1 is discarded, the H1
        is also discarded (strip-and-render), and Duplo's canonical
        H1 prepends. Previously this test pinned that the model's
        original H1 survived; the new contract says it doesn't."""
        content = (
            "The PLAN.md content is ready. Here it is for you to append to PLAN.md:\n"
            "\n"
            "---\n"
            "\n"
            "# Numi — Phase 4: Advanced\n"
            "\n"
            "- [ ] First task\n"
        )
        result = _ensure_h1_heading(content, "Numi", 4, "Advanced")
        assert result.startswith("# Numi — Phase 4: Advanced")
        assert "The PLAN.md content is ready" not in result
        assert "---" not in result
        # Exactly one phase H1 line in the result.
        phase_h1_count = sum(
            1
            for line in result.splitlines()
            if line.startswith("# ") and " — Phase " in line
        )
        assert phase_h1_count == 1

    def test_strips_preamble_with_separator_only(self):
        content = "---\n\n# App — Phase 2: Core\n\n- [ ] Task"
        result = _ensure_h1_heading(content, "Ignored", 99, "Ignored")
        # Old behavior kept the model's H1 verbatim. New behavior
        # strips it and renders Duplo's canonical envelope.
        assert result == "# Ignored — Phase 99: Ignored\n\n- [ ] Task"
        assert "App — Phase 2" not in result


class TestPhaseSystemPromptAnnotations:
    def test_system_prompt_requires_feat_annotation(self):
        assert '[feat: "Feature Name"]' in _PHASE_SYSTEM

    def test_system_prompt_requires_multi_feature_annotation(self):
        assert "comma-separated" in _PHASE_SYSTEM

    def test_system_prompt_requires_fix_annotation(self):
        assert '[fix: "description"]' in _PHASE_SYSTEM

    def test_system_prompt_no_annotation_for_scaffolding(self):
        assert "no annotation" in _PHASE_SYSTEM.lower()

    def test_system_prompt_orders_fixes_before_dependent_features(self):
        assert "fix tasks before new feature work" in _PHASE_SYSTEM.lower()

    def test_system_prompt_shows_feat_example_in_format(self):
        assert '[feat: "User authentication"]' in _PHASE_SYSTEM

    def test_system_prompt_heading_format(self):
        assert "# <AppName> — Phase N: <Title>" in _PHASE_SYSTEM

    def test_system_prompt_shows_fix_example_in_format(self):
        assert '[fix: "email format not checked"]' in _PHASE_SYSTEM

    def test_system_prompt_forbids_platform_boilerplate_paragraph(self):
        # The directive moved from "H1 phase heading" to the canonical
        # Slice C ``## Phase phase_NNN:`` header per T-000186; the
        # platform-boilerplate prohibition itself stays.
        assert (
            "Do NOT include a platform, language, prerequisites, or\n"
            "  build-system description paragraph at the top of the phase.\n"
            "  That information is written once in the PLAN.md project\n"
            "  header and must not be repeated per phase."
        ) in _PHASE_SYSTEM
        assert "## Phase phase_NNN:" in _PHASE_SYSTEM
        assert "go directly to task checkboxes" in _PHASE_SYSTEM

    def test_system_prompt_reserves_user_for_human_only_checks(self):
        assert "Reserve [USER] only for genuinely human-only checks" in _PHASE_SYSTEM
        assert "runnable verification command, test, or script must never" in _PHASE_SYSTEM
        assert "[AUTO:run_cli] task" in _PHASE_SYSTEM
        assert "McLoop will pause only on true [USER] tasks" in _PHASE_SYSTEM


class TestNextPhaseSystemPromptAnnotations:
    def test_system_prompt_requires_feat_annotation(self):
        assert '[feat: "Feature Name"]' in _NEXT_PHASE_SYSTEM

    def test_system_prompt_requires_multi_feature_annotation(self):
        assert "comma-separated" in _NEXT_PHASE_SYSTEM

    def test_system_prompt_requires_fix_annotation(self):
        assert '[fix: "description"]' in _NEXT_PHASE_SYSTEM

    def test_system_prompt_no_annotation_for_scaffolding(self):
        assert "no annotation" in _NEXT_PHASE_SYSTEM.lower()

    def test_system_prompt_reserves_user_for_human_only_checks(self):
        assert "Reserve [USER] only for genuinely human-only checks" in _NEXT_PHASE_SYSTEM
        assert "Runnable verification must be expressed" in _NEXT_PHASE_SYSTEM
        assert "[AUTO:run_cli] step" in _NEXT_PHASE_SYSTEM


_SAMPLE_CURRENT_PLAN = "# Phase 1: Core Auth\n\n## Objective\nMinimal app."

_ANNOTATED_PHASE_PLAN = """\
## Phase phase_001: Core

- [ ] Set up project structure and build system
- [ ] Add user login form [feat: "User auth"]
  - [ ] Create login page template
  - [ ] Wire up authentication backend [feat: "User auth"]
- [ ] Build activity overview [feat: "Dashboard"]
- [ ] Fix email validation on signup [fix: "email format not checked"]
"""

_ANNOTATED_NEXT_PLAN = """\
# Phase 2: Search

## Objective
Add full-text search across the application.

## Implementation steps
1. Set up search index infrastructure
2. Add search bar component [feat: "Full-text search"]
3. Implement result ranking [feat: "Full-text search", "Relevance scoring"]
4. Fix broken layout on mobile [fix: "sidebar overlaps content on small screens"]
"""

_ANNOTATION_RE = re.compile(r"\[(feat|fix):\s*\"[^\"]+\"(?:,\s*\"[^\"]+\")*\]")


class TestPlanAnnotationOutput:
    """Verify that generated typed Plans carry [feat:] / [fix:]
    annotations on their tasks.

    Pre-T-000186 these tests grepped a markdown string for annotation
    syntax. After T-000186, ``generate_phase_plan`` returns a typed
    :class:`bob_tools.planfile.Plan` whose tasks carry annotations as
    structured ``(key, value)`` pairs on ``Task.annotations``. The
    intent — that synthesizer-emitted ``[feat: ...]`` and ``[fix:
    ...]`` survive end-to-end — is preserved; the assertion shape is
    structural, not stringly.
    """

    @staticmethod
    def _walk_all_tasks(plan):
        for phase in plan.phases:
            for task in phase.tasks:
                yield from _walk_with_children(task)
            for sub in phase.subsections:
                for task in sub.tasks:
                    yield from _walk_with_children(task)

    def test_phase_plan_contains_feat_annotations(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_PHASE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert isinstance(result, Plan)
        feat_values = [
            value
            for task in self._walk_all_tasks(result)
            for key, value in task.annotations
            if key == "feat"
        ]
        assert feat_values

    def test_phase_plan_contains_fix_annotations(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_PHASE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert isinstance(result, Plan)
        fix_values = [
            value
            for task in self._walk_all_tasks(result)
            for key, value in task.annotations
            if key == "fix"
        ]
        assert fix_values

    def test_phase_plan_annotations_on_task_lines(self):
        """The typed-plan equivalent: every annotation lives on a Task,
        not on prose. Rendered output places annotations only on
        ``- [ ] ...`` lines because the renderer emits them as part of
        the task body — that property is preserved by construction in
        the typed model.
        """
        with patch("duplo.planner.query", return_value=_ANNOTATED_PHASE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        text = _plan_to_text(result)
        for line in text.splitlines():
            if _ANNOTATION_RE.search(line):
                stripped = line.lstrip()
                assert stripped.startswith("- [ ]") or stripped.startswith(
                    "- [x]"
                )

    def test_next_phase_plan_contains_feat_annotations(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_NEXT_PLAN):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Add search.")
        feat_matches = re.findall(r'\[feat: "[^"]+"\]', result)
        assert len(feat_matches) >= 1

    def test_next_phase_plan_contains_fix_annotations(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_NEXT_PLAN):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Add search.")
        fix_matches = re.findall(r'\[fix: "[^"]+"\]', result)
        assert len(fix_matches) >= 1

    def test_next_phase_plan_multi_feature_annotation(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_NEXT_PLAN):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Add search.")
        multi = re.findall(r'\[feat: "[^"]+",\s*"[^"]+"\]', result)
        assert len(multi) >= 1

    def test_scaffolding_lines_have_no_annotation(self):
        with patch("duplo.planner.query", return_value=_ANNOTATED_PHASE_PLAN):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        # The "Set up project structure ..." scaffolding task carries
        # no [feat:] / [fix:] annotation in the typed plan.
        scaffold_tasks = [
            t
            for t in self._walk_all_tasks(result)
            if "project structure" in t.text
        ]
        assert scaffold_tasks, "scaffolding task must survive parsing"
        for task in scaffold_tasks:
            assert task.annotations == ()


class TestDetectNextPhaseNumber:
    def test_extracts_phase_number(self):
        plan = "# Phase 1: Core Auth\n\n## Objective\nMinimal app."
        assert _detect_next_phase_number(plan) == 2

    def test_extracts_higher_phase_number(self):
        plan = "# Phase 3: Dashboard\n\n## Objective\nAdd dashboard."
        assert _detect_next_phase_number(plan) == 4

    def test_defaults_to_two_when_no_phase_heading(self):
        assert _detect_next_phase_number("No heading here.") == 2

    def test_case_insensitive(self):
        assert _detect_next_phase_number("# phase 2: Foo") == 3

    def test_prefixed_heading(self):
        plan = "# McWhisper — Phase 3: Dashboard\n\n## Objective\nAdd dashboard."
        assert _detect_next_phase_number(plan) == 4

    def test_stage_heading(self):
        plan = "# Stage 1: Core\n\n## Objective\nMinimal app."
        assert _detect_next_phase_number(plan) == 2

    def test_stage_higher_number(self):
        plan = "## Stage 2: Features\n\n- [ ] Add search"
        assert _detect_next_phase_number(plan) == 3

    def test_prefixed_stage_heading(self):
        plan = "# MyApp — Stage 4: Polish\n\n## Objective\nFinal pass."
        assert _detect_next_phase_number(plan) == 5


_SAMPLE_NEXT_PLAN = "# Phase 2: Search\n\n## Objective\nAdd search."


class TestGenerateNextPhasePlan:
    def test_returns_string(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Add search feature.")
        assert isinstance(result, str)
        assert result == _SAMPLE_NEXT_PLAN

    def test_passes_current_plan_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        prompt = mock_query.call_args[0][0]
        assert "Phase 1: Core Auth" in prompt

    def test_passes_feedback_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "Needs dark mode.")
        prompt = mock_query.call_args[0][0]
        assert "Needs dark mode." in prompt

    def test_passes_issues_text_to_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback", "- Layout broken")
        prompt = mock_query.call_args[0][0]
        assert "Layout broken" in prompt

    def test_next_phase_number_in_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        prompt = mock_query.call_args[0][0]
        assert "Phase 2" in prompt

    def test_no_issues_text_shows_no_issues_message(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        prompt = mock_query.call_args[0][0]
        assert "No visual issues reported" in prompt

    def test_empty_issues_text_shows_no_issues_message(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback", "")
        prompt = mock_query.call_args[0][0]
        assert "No visual issues reported" in prompt


_PLATFORM_ADDENDUM = (
    "\n## Platform-specific rules (from duplo platform knowledge)\n"
    "\n- Use Swift Package Manager for dependencies\n"
)


class TestPlatformAddendum:
    def test_phase_plan_appends_addendum_to_system_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                platform_addendum=_PLATFORM_ADDENDUM,
            )
        system = mock_query.call_args.kwargs["system"]
        assert _PHASE_SYSTEM in system
        assert _PLATFORM_ADDENDUM in system

    def test_phase_plan_empty_addendum_leaves_system_unchanged(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                platform_addendum="",
            )
        system = mock_query.call_args.kwargs["system"]
        assert system == _PHASE_SYSTEM

    def test_phase_plan_default_has_no_addendum(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_PLAN) as mock_query:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        system = mock_query.call_args.kwargs["system"]
        assert system == _PHASE_SYSTEM

    def test_next_phase_plan_appends_addendum_to_system_prompt(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(
                _SAMPLE_CURRENT_PLAN,
                "feedback",
                platform_addendum=_PLATFORM_ADDENDUM,
            )
        system = mock_query.call_args.kwargs["system"]
        assert _NEXT_PHASE_SYSTEM in system
        assert _PLATFORM_ADDENDUM in system

    def test_next_phase_plan_empty_addendum_leaves_system_unchanged(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(
                _SAMPLE_CURRENT_PLAN,
                "feedback",
                platform_addendum="",
            )
        system = mock_query.call_args.kwargs["system"]
        assert system == _NEXT_PHASE_SYSTEM

    def test_next_phase_plan_default_has_no_addendum(self):
        with patch("duplo.planner.query", return_value=_SAMPLE_NEXT_PLAN) as mock_query:
            generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        system = mock_query.call_args.kwargs["system"]
        assert system == _NEXT_PHASE_SYSTEM


class TestAppendTestTasks:
    def test_appends_tasks_to_plan(self):
        plan = "# Phase 1\n- [ ] Build core"
        tasks = ["- [ ] Wire up tests", "  - [ ] Replace stub"]
        result = append_test_tasks(plan, tasks)
        assert "Build core" in result
        assert result == (
            "# Phase 1\n- [ ] Wire up tests\n  - [ ] Replace stub\n- [ ] Build core\n"
        )

    def test_returns_plan_unchanged_when_no_tasks(self):
        plan = "# Phase 1\n- [ ] Build core\n"
        assert append_test_tasks(plan, []) == plan


class TestSavePlan:
    def test_writes_file(self, tmp_path: Path):
        content = "# Phase 1\n"
        path = save_plan(content, target_dir=tmp_path)
        assert path.name == _PLAN_FILENAME
        text = path.read_text(encoding="utf-8")
        assert "# Phase 1" in text
        # duplo must never emit a ## Bugs section.
        assert "## Bugs" not in text

    def test_returns_absolute_path(self, tmp_path: Path):
        path = save_plan("# Plan", target_dir=tmp_path)
        assert path.is_absolute()

    def test_appends_to_existing_file(self, tmp_path: Path):
        """``save_plan`` reads the existing PLAN.md, merges the new
        Plan via :func:`_merge_existing_plan`, and writes the combined
        result back. After T-000186 both the on-disk and inbound texts
        must be valid PLAN.md (the typed-plan boundary rejects ad-hoc
        prose).
        """
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(
            "## Phase phase_001: Existing\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [x] T-000001: Done task\n"
            "- [ ] T-000002: Open task\n",
            encoding="utf-8",
        )
        save_plan(
            "## Phase phase_002: Added\n\n- [ ] New task\n",
            target_dir=tmp_path,
        )
        text = plan_path.read_text(encoding="utf-8")
        assert "Done task" in text
        assert "Open task" in text
        assert "New task" in text

    def test_append_preserves_existing_content_exactly(self, tmp_path: Path):
        plan_path = tmp_path / _PLAN_FILENAME
        original = (
            "## Phase phase_001: First phase\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [x] T-000001: First\n"
            "- [ ] T-000002: Second\n"
        )
        plan_path.write_text(original, encoding="utf-8")
        save_plan(
            "## Phase phase_002: Added\n\n- [ ] Third\n",
            target_dir=tmp_path,
        )
        text = plan_path.read_text(encoding="utf-8")
        # The existing phase header, both tasks, and the appended
        # task all survive the merge.
        assert "First phase" in text
        assert "- [x] T-000001: First" in text
        assert "- [ ] T-000002: Second" in text
        assert text.rstrip().endswith("Third")

    def test_append_separates_phases_with_blank_line(self, tmp_path: Path):
        """The canonical renderer separates phase blocks with a single
        blank line — the typed equivalent of pre-T-000186's
        ``\\n\\n- [ ] New\\n`` assertion.
        """
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(
            "## Phase phase_001: Existing\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: Existing\n",
            encoding="utf-8",
        )
        save_plan(
            "## Phase phase_002: Added\n\n- [ ] New\n",
            target_dir=tmp_path,
        )
        text = plan_path.read_text(encoding="utf-8")
        # Two phase H2 headers in the output, in order.
        h2_lines = [ln for ln in text.splitlines() if ln.startswith("## Phase ")]
        assert len(h2_lines) == 2
        # The phase-id comment for the new phase is present, separating
        # the second phase from the first.
        assert "<!-- phase_id: phase_002 -->" in text

    def test_default_target_dir_is_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.chdir(tmp_path)
        path = save_plan("# Plan")
        assert path.parent == tmp_path.resolve()


class TestStripValidateRegexSplit:
    """Defect 1 / Defect 3: strip and validate use SEPARATE regexes.

    Strip is permissive (false positives benign — Duplo prepends
    canonical anyway). Validate is strict (only the canonical envelope
    Duplo renders counts as a phase H1; prose like
    `# Background: Phase 1 introduced filtering` is content, not
    envelope). The two regex constants encode different intents.
    """

    PROSE_H1 = "# Background: Phase 1 introduced filtering\n"

    def test_strip_catches_prose_h1_false_positive(self):
        """Synthesizer wrote a prose H1 mid-body that mentions
        ``Phase N``. Strip removes it (acceptable false positive
        because Duplo prepends the canonical envelope anyway)."""
        body = (
            "## Phase phase_001: real header\n\n"
            f"{self.PROSE_H1}"
            "\n- [ ] Real task\n"
        )
        result = _ensure_h1_heading(body, "App", 1, "Core")
        assert "Background" not in result
        assert "## Phase phase_001:" in result
        assert "- [ ] Real task" in result
        assert result.startswith("# App — Phase 1: Core")

    def test_validator_does_not_count_prose_h1_false_positive(self):
        """The same prose H1, if it ended up in PLAN.md somehow
        (e.g., a body that did NOT pass through Duplo's strip),
        does NOT count as a phase ordinal in validation. The
        canonical-envelope-only validator regex excludes it."""
        text = (
            "# App — Phase 0: Real\n\n"
            "## Phase phase_001: real semantic header\n\n"
            f"{self.PROSE_H1}"
            "\n- [ ] task\n\n"
            "# App — Phase 1: Real\n"
        )
        # Despite the prose H1 mentioning "Phase 1", the validator
        # only counts the two canonical envelope H1s [0, 1] which
        # are contiguous.
        validate_h1_ordinal_sequence(text)


class TestValidateH1OrdinalSequence:
    """H1 phase ordinal sequence must be contiguous and monotonic.

    Codex's broader framing: Duplo owns the deterministic envelope
    and validates the final markdown mcloop will consume. The
    validator catches the case where strip-and-render renders the
    wrong sequence (e.g., a Duplo-side bug in roadmap_phase_ordinal
    bookkeeping), as a fail-closed backstop.
    """

    def test_passes_with_no_h1_headings(self):
        # Pre-canonical scaffold writes have no phase H1 yet.
        validate_h1_ordinal_sequence("- [ ] Bare task without an H1\n")

    def test_passes_on_zero_indexed_sequence(self):
        text = (
            "# App — Phase 0: Scaffold\n\n- [ ] x\n\n"
            "# App — Phase 1: Core\n\n- [ ] y\n\n"
            "# App — Phase 2: Polish\n\n- [ ] z\n"
        )
        validate_h1_ordinal_sequence(text)

    def test_passes_on_one_indexed_sequence(self):
        text = (
            "# App — Phase 1: Scaffold\n\n- [ ] x\n\n"
            "# App — Phase 2: Core\n\n- [ ] y\n\n"
            "# App — Phase 3: Polish\n\n- [ ] z\n"
        )
        validate_h1_ordinal_sequence(text)

    def test_passes_on_single_phase(self):
        validate_h1_ordinal_sequence("# App — Phase 0: Scaffold\n\n- [ ] x\n")

    def test_raises_on_duplicate_ordinal(self):
        """The canonical failure case from tonight's bug. Phase 3
        appears twice; mcloop's parser refuses to load the file."""
        text = (
            "# App — Phase 0: A\n"
            "# App — Phase 1: B\n"
            "# App — Phase 3: C\n"
            "# App — Phase 3: D\n"
            "# App — Phase 4: E\n"
        )
        with pytest.raises(CanonicalH1OrdinalError) as ei:
            validate_h1_ordinal_sequence(text)
        msg = str(ei.value)
        assert "[0, 1, 3, 3, 4]" in msg
        assert "[0, 1, 2, 3, 4]" in msg

    def test_raises_on_gap_skip_ordinal(self):
        """Even without a duplicate, gap-skip is invalid: a phase
        ordinal that skips a value indicates one was lost
        somewhere in the rendering pipeline."""
        text = (
            "# App — Phase 0: A\n"
            "# App — Phase 1: B\n"
            "# App — Phase 3: C\n"
            "# App — Phase 4: D\n"
        )
        with pytest.raises(CanonicalH1OrdinalError) as ei:
            validate_h1_ordinal_sequence(text)
        msg = str(ei.value)
        assert "[0, 1, 3, 4]" in msg
        assert "[0, 1, 2, 3]" in msg

    def test_raises_on_out_of_order_ordinal(self):
        text = (
            "# App — Phase 0: A\n"
            "# App — Phase 2: C\n"
            "# App — Phase 1: B\n"
        )
        with pytest.raises(CanonicalH1OrdinalError) as ei:
            validate_h1_ordinal_sequence(text)
        msg = str(ei.value)
        assert "[0, 2, 1]" in msg

    def test_passes_when_starting_ordinal_is_nonzero(self):
        """Sequence [3, 4, 5] is valid: contiguous and monotonic
        starting from 3. Used when validating a partial PLAN.md
        slice that doesn't include the earliest phases."""
        validate_h1_ordinal_sequence(
            "# App — Phase 3: A\n# App — Phase 4: B\n# App — Phase 5: C\n"
        )

    def test_ignores_non_phase_h1s(self):
        """A plain ``# Heading`` line is not a phase H1 and does
        not participate in the ordinal-sequence check."""
        text = (
            "# Some other heading\n\n"
            "# App — Phase 0: A\n"
            "# App — Phase 1: B\n"
        )
        validate_h1_ordinal_sequence(text)

    # --------- Defect 2: expected_ordinals source-of-truth check --

    def test_validator_with_expected_passes_on_match(self):
        text = (
            "# App — Phase 0: A\n# App — Phase 1: B\n# App — Phase 2: C\n"
        )
        validate_h1_ordinal_sequence(text, expected_ordinals=[0, 1, 2])

    def test_validator_with_expected_fails_on_mismatch(self):
        """Plan has ordinals [0, 1, 2, 4] but Duplo's roadmap state
        emitted [0, 1, 2, 3]. Source-of-truth match fails. Error
        names BOTH observed and expected sequences."""
        text = (
            "# App — Phase 0: A\n# App — Phase 1: B\n"
            "# App — Phase 2: C\n# App — Phase 4: D\n"
        )
        with pytest.raises(CanonicalH1OrdinalError) as ei:
            validate_h1_ordinal_sequence(
                text, expected_ordinals=[0, 1, 2, 3]
            )
        msg = str(ei.value)
        assert "[0, 1, 2, 4]" in msg
        assert "[0, 1, 2, 3]" in msg
        assert "source-of-truth" in msg.lower()

    def test_validator_with_expected_fails_on_missing_phase(self):
        """Plan has only [0, 1] but roadmap emitted [0, 1, 2].
        Source-of-truth match fails (missing phase 2)."""
        text = "# App — Phase 0: A\n# App — Phase 1: B\n"
        with pytest.raises(CanonicalH1OrdinalError):
            validate_h1_ordinal_sequence(
                text, expected_ordinals=[0, 1, 2]
            )

    def test_validator_with_expected_fails_on_extra_phase(self):
        """Plan has [0, 1, 2] but roadmap emitted [0, 1].
        Source-of-truth match fails (extra phase)."""
        text = (
            "# App — Phase 0: A\n# App — Phase 1: B\n# App — Phase 2: C\n"
        )
        with pytest.raises(CanonicalH1OrdinalError):
            validate_h1_ordinal_sequence(
                text, expected_ordinals=[0, 1]
            )

    def test_validator_with_expected_fails_on_wrong_starting_ordinal(self):
        """Roadmap emitted [3, 4, 5] (Phase 0/1/2 already complete
        in a prior run); plan was rendered with [0, 1, 2]. Wrong
        starting ordinal."""
        text = (
            "# App — Phase 0: A\n# App — Phase 1: B\n# App — Phase 2: C\n"
        )
        with pytest.raises(CanonicalH1OrdinalError):
            validate_h1_ordinal_sequence(
                text, expected_ordinals=[3, 4, 5]
            )

    def test_validator_without_expected_falls_back_to_contiguity(self):
        """Backward-compatible: callers that don't provide
        expected_ordinals get the internal-contiguity check
        (passes on [0, 1, 2], fails on [0, 1, 3])."""
        good = "# App — Phase 0: A\n# App — Phase 1: B\n# App — Phase 2: C\n"
        validate_h1_ordinal_sequence(good)  # no expected_ordinals
        bad = "# App — Phase 0: A\n# App — Phase 1: B\n# App — Phase 3: D\n"
        with pytest.raises(CanonicalH1OrdinalError):
            validate_h1_ordinal_sequence(bad)


class TestStripIsSupersetOfMcloopParser:
    """Defect 5: Duplo's strip regex must be a SUPERSET of mcloop's
    checklist.py STAGE_RE
    (^#+\\s+.*?\\b(?:stage|phase)\\s+(\\d+)\\b, IGNORECASE).
    Whatever mcloop matches as a phase/stage header, Duplo MUST
    also recognize and strip — otherwise an unstripped wrong H1
    survives the body, Duplo prepends its canonical H1, mcloop
    sees both and fires duplicate-Phase.
    """

    def test_strip_removes_h1_phase_no_colon(self):
        """`# Phase 3 Glob filtering` — no colon after digit.
        Codex's example #1."""
        body = "# Phase 3 Glob filtering\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "App", 1, "Core")
        assert "Glob filtering" not in result
        assert "Phase 3" not in result
        assert result.startswith("# App — Phase 1: Core")

    def test_strip_removes_h2_phase(self):
        """`## Phase 3` — H2, no colon. Codex's example #2."""
        body = "## Phase 3\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "App", 1, "Core")
        # The H2 must be stripped because mcloop would parse it
        # as a stage header.
        assert "## Phase 3" not in result
        assert result.startswith("# App — Phase 1: Core")

    def test_strip_removes_h3_stage(self):
        """`### Stage 5: Cleanup` — H3, "Stage" keyword."""
        body = "### Stage 5: Cleanup\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "App", 1, "Core")
        assert "Stage 5" not in result
        assert "Cleanup" not in result
        assert result.startswith("# App — Phase 1: Core")

    def test_strip_removes_lowercase_stage(self):
        """`# stage 4 — Foo` — lowercase 'stage' keyword."""
        body = "# stage 4 — Foo\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "App", 1, "Core")
        assert "stage 4" not in result
        assert "Foo" not in result
        assert result.startswith("# App — Phase 1: Core")

    def test_strip_removes_phase_with_no_title(self):
        """`# Phase 7` — bare. mcloop would parse as stage 7."""
        body = "# Phase 7\n\n- [ ] Task\n"
        result = _ensure_h1_heading(body, "App", 1, "Core")
        # The bare phase header is stripped.
        result_lines = result.splitlines()
        # The only "Phase" line is Duplo's canonical envelope.
        phase_lines = [
            ln
            for ln in result_lines
            if "Phase" in ln and ln.startswith("# ")
        ]
        assert len(phase_lines) == 1
        assert phase_lines[0].startswith("# App — Phase 1: Core")

    def test_strip_preserves_slice_c_semantic_header(self):
        """The Slice C semantic header `## Phase phase_NNN: title`
        MUST survive. The `phase_001` token has no whitespace
        between "phase" and the digit (the underscore breaks the
        `\\bphase\\s+\\d+\\b` pattern), so Slice C headers are
        invisible to mcloop's STAGE_RE and to Duplo's strip."""
        body = (
            "## Phase phase_003: Glob filtering\n\n- [ ] Task\n"
        )
        result = _ensure_h1_heading(body, "App", 1, "Core")
        assert "## Phase phase_003: Glob filtering" in result
        assert result.startswith("# App — Phase 1: Core")


class TestSavePlanAcceptsExpectedOrdinals:
    """Save path: phases land canonically with monotonic 1..N ordinals.

    Pre-T-000186 the ``expected_h1_ordinals`` argument compared an
    accumulated list of ``# X — Phase N: <title>`` ordinals against a
    caller-supplied source-of-truth list. After T-000186 the
    typed-planfile renderer always emits ``## Phase 1: <title>``,
    ``## Phase 2: ...`` in contiguous monotonic order (the merge step
    in :func:`_merge_existing_plan` renumbers any duplicates), so the
    same intent is now enforced structurally by the canonical
    contract. These tests pin that intent against the new form.
    """

    def test_save_plan_renders_contiguous_ordinals_after_append(
        self, tmp_path: Path
    ):
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(
            "## Phase phase_001: A\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: a\n",
            encoding="utf-8",
        )
        save_plan(
            "## Phase phase_002: B\n\n- [ ] b\n",
            target_dir=tmp_path,
            expected_h1_ordinals=[1, 2],
        )
        text = plan_path.read_text(encoding="utf-8")
        h2_lines = [
            ln for ln in text.splitlines() if ln.startswith("## Phase ")
        ]
        assert h2_lines == ["## Phase 1: A", "## Phase 2: B"]

    def test_save_plan_renumbers_duplicate_inbound_ordinals(
        self, tmp_path: Path
    ):
        """The inbound Plan independently arrives with ordinal=1
        (each synthesis is self-contained); the merge step in
        :func:`_merge_existing_plan` renumbers so the final on-disk
        sequence is contiguous 1..N. The original behavior
        ``test_save_plan_raises_on_expected_mismatch`` pinned —
        rejecting non-monotonic accumulated ordinals before write —
        is now enforced by construction, not by post-hoc validation.
        """
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(
            "## Phase phase_001: A\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: a\n",
            encoding="utf-8",
        )
        # The inbound body uses phase_002 (per
        # ``compute_required_phase_id(highest+1)``); the renumber
        # step gives the final file H2 line ``## Phase 2: ...``,
        # not a stale ``## Phase 1: ...`` duplicate.
        save_plan(
            "## Phase phase_002: B\n\n- [ ] b\n", target_dir=tmp_path
        )
        text = plan_path.read_text(encoding="utf-8")
        h2_lines = [
            ln for ln in text.splitlines() if ln.startswith("## Phase ")
        ]
        assert h2_lines == ["## Phase 1: A", "## Phase 2: B"]


class TestStripAndRenderEndToEnd:
    """End-to-end: runtime owns phase identity; bad-id synthesizer
    body raises rather than landing in PLAN.md.

    Pre-T-000186 this class pinned the strip-and-render contract:
    even if the synthesizer fabricated a wrong ``# X — Phase N:`` H1,
    duplo would strip it and prepend its canonical H1 with the roadmap-
    supplied ordinal. After T-000186 the runtime computes the required
    ``phase_NNN`` id from the existing PLAN.md (``highest + 1``) and
    :func:`typed_plan_from_synthesizer_text` raises when the
    synthesizer's body uses a different id. The test pins that
    contract; the on-disk PLAN.md stays untouched.
    """

    def test_synthesizer_wrong_phase_id_raises_end_to_end(
        self, tmp_path: Path
    ) -> None:
        from bob_tools.planfile import PlanValidationError

        # Seed PLAN.md with phases 1 and 2 (the canonical 1-indexed
        # form the planfile renderer emits). The next required
        # phase_id is ``phase_003``.
        seed = (
            "## Phase phase_001: Scaffold\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: Phase 1 task\n"
            "\n"
            "## Phase phase_002: Core\n"
            "<!-- phase_id: phase_002 -->\n"
            "\n"
            "- [ ] T-000002: Phase 2 task\n"
        )
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(seed, encoding="utf-8")

        # Synthesizer body uses the wrong phase_id; runtime expects
        # ``phase_003`` (compute_required_phase_id reads the seed
        # above and returns ``phase_003``).
        wrong_body = (
            "## Phase phase_007: Wrong\n"
            "\n"
            "- [ ] Phase real task\n"
        )

        phase = {
            "phase": 2,
            "title": "Polish",
            "goal": "scope to polish",
            "features": [],
            "test": "",
        }
        with patch("duplo.planner.query", return_value=wrong_body):
            with pytest.raises(PlanValidationError) as exc_info:
                generate_phase_plan(
                    "https://example.com",
                    _sample_features(),
                    _sample_prefs(),
                    phase=phase,
                    project_name="App",
                    target_dir=tmp_path,
                )
        # The error message names both the supplied and required ids
        # so the synthesizer's misbehavior is auditable.
        assert "phase_007" in str(exc_info.value)
        assert "phase_003" in str(exc_info.value)

        # PLAN.md on disk is untouched.
        assert plan_path.read_text(encoding="utf-8") == seed

    def test_synthesizer_correct_phase_id_lands_canonical(
        self, tmp_path: Path
    ) -> None:
        """Companion test: the synthesizer DOES use the
        runtime-required ``phase_003`` id; the body lands with the
        canonical contiguous-ordinal contract intact.
        """
        seed = (
            "## Phase phase_001: Scaffold\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: Phase 1 task\n"
            "\n"
            "## Phase phase_002: Core\n"
            "<!-- phase_id: phase_002 -->\n"
            "\n"
            "- [ ] T-000002: Phase 2 task\n"
        )
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(seed, encoding="utf-8")

        right_body = (
            "## Phase phase_003: Polish\n"
            "\n"
            "- [ ] Phase 3 real task\n"
        )
        phase = {
            "phase": 2,
            "title": "Polish",
            "goal": "scope to polish",
            "features": [],
            "test": "",
        }
        with patch("duplo.planner.query", return_value=right_body):
            content = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase=phase,
                project_name="App",
                target_dir=tmp_path,
            )
        assert isinstance(content, Plan)
        assert content.phases[0].phase_id == "phase_003"

        save_plan(content, target_dir=tmp_path)

        final = plan_path.read_text(encoding="utf-8")
        # The accumulated PLAN.md now has three phases with monotonic
        # 1..3 ordinals from the canonical renderer.
        h2_lines = [
            ln for ln in final.splitlines() if ln.startswith("## Phase ")
        ]
        assert h2_lines == [
            "## Phase 1: Scaffold",
            "## Phase 2: Core",
            "## Phase 3: Polish",
        ]
        assert "Phase 3 real task" in final





class TestSavePlanH1OrdinalValidation:
    """save_plan's accumulated PLAN.md remains canonical across appends.

    Pre-T-000186 the validator extracted ordinals from
    ``# <project> — Phase N:`` H1 lines and required contiguous,
    monotonic 0/1-indexed sequences. After T-000186 the canonical
    PLAN.md uses ``## Phase N:`` headers and the typed-Plan merge
    pipeline guarantees contiguous 1..N ordinals by construction
    (the renumber step in :func:`_merge_existing_plan`). Tests now
    pin the structural contract that replaced the regex-driven check.
    """

    def test_save_plan_passes_on_valid_sequence(self, tmp_path: Path):
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(
            "## Phase phase_001: A\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: a\n",
            encoding="utf-8",
        )
        save_plan(
            "## Phase phase_002: B\n\n- [ ] b\n", target_dir=tmp_path
        )
        text = plan_path.read_text(encoding="utf-8")
        assert "## Phase 1: A" in text
        assert "## Phase 2: B" in text

    def test_save_plan_runtime_supplies_next_phase_id_not_a_duplicate(
        self, tmp_path: Path
    ):
        """Pre-T-000186 a duplicate-ordinal append raised
        :class:`CanonicalH1OrdinalError` at save-time. Post-T-000186
        duplicate phase_ids cannot arrive through
        :func:`generate_phase_plan`: the runtime computes
        ``compute_required_phase_id`` from the existing PLAN.md
        (``highest + 1``) and
        :func:`typed_plan_from_synthesizer_text` raises
        :class:`bob_tools.planfile.PlanValidationError` when the
        synthesizer's body uses any other id. The duplicate-id
        defense moved from save-time to author-time, but the intent —
        "duplicate phase ordinals cannot reach PLAN.md via Duplo" —
        is preserved.
        """
        from bob_tools.planfile import PlanValidationError

        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(
            "## Phase phase_001: A\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: a\n",
            encoding="utf-8",
        )

        wrong_body = (
            "## Phase phase_001: Dup\n"
            "\n"
            "- [ ] dup\n"
        )
        with patch("duplo.planner.query", return_value=wrong_body):
            with pytest.raises(PlanValidationError):
                generate_phase_plan(
                    "https://example.com",
                    _sample_features(),
                    _sample_prefs(),
                    project_name="App",
                    target_dir=tmp_path,
                )
        # PLAN.md is unchanged (the synthesizer body never reached
        # save_plan).
        assert "Dup" not in plan_path.read_text(encoding="utf-8")

    def test_save_plan_renumbers_gap_in_inbound_ordinal(
        self, tmp_path: Path
    ):
        """Pre-T-000186 a gap-skipped append (``Phase 0`` then
        ``Phase 2``) raised :class:`CanonicalH1OrdinalError`. After
        T-000186 phase ordinals are renumbered at merge time to be
        contiguous 1..N, so gap-skipping in the rendered output is
        impossible by construction; the inbound ``phase_002`` id is
        preserved (it does not collide with the existing
        ``phase_001``).
        """
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(
            "## Phase phase_001: A\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: a\n",
            encoding="utf-8",
        )
        save_plan(
            "## Phase phase_002: Skipped\n\n- [ ] x\n",
            target_dir=tmp_path,
        )
        text = plan_path.read_text(encoding="utf-8")
        # Both H2 lines render with contiguous 1..2 ordinals.
        h2_lines = [
            ln for ln in text.splitlines() if ln.startswith("## Phase ")
        ]
        assert h2_lines == ["## Phase 1: A", "## Phase 2: Skipped"]

    def test_save_plan_passes_when_no_h1_phases_present(self, tmp_path: Path):
        # Pre-canonical scaffold content has no phase header; the
        # planfile parser accepts a preamble-only Plan and save_plan
        # writes successfully.
        save_plan("- [ ] task without H1\n", target_dir=tmp_path)
        path = tmp_path / _PLAN_FILENAME
        assert path.exists()


class TestParseCompletedTasks:
    def test_empty_content(self):
        assert parse_completed_tasks("") == []

    def test_no_checked_items(self):
        plan = "# Phase 1\n- [ ] Not done\n- [ ] Also not done\n"
        assert parse_completed_tasks(plan) == []

    def test_basic_checked_items(self):
        plan = "- [x] Set up project\n- [x] Add login form\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 2
        assert tasks[0].text == "Set up project"
        assert tasks[1].text == "Add login form"

    def test_uppercase_x(self):
        plan = "- [X] Done task\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].text == "Done task"

    def test_mixed_checked_and_unchecked(self):
        plan = "- [x] Done\n- [ ] Not done\n- [x] Also done\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 2
        assert tasks[0].text == "Done"
        assert tasks[1].text == "Also done"

    def test_feat_annotation(self):
        plan = '- [x] Add login form [feat: "User auth"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].text == "Add login form"
        assert tasks[0].features == ["User auth"]
        assert tasks[0].fixes == []

    def test_multi_feat_annotation(self):
        plan = '- [x] Add recording [feat: "Push-to-talk", "Keyboard shortcuts"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].features == ["Push-to-talk", "Keyboard shortcuts"]

    def test_fix_annotation(self):
        plan = '- [x] Fix email check [fix: "email format not validated"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].text == "Fix email check"
        assert tasks[0].fixes == ["email format not validated"]
        assert tasks[0].features == []

    def test_no_annotation(self):
        plan = "- [x] Set up project structure\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].features == []
        assert tasks[0].fixes == []

    def test_indented_subtask(self):
        plan = "- [x] Main task\n  - [x] Subtask one\n    - [x] Deep subtask\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 3
        assert tasks[0].indent == 0
        assert tasks[1].indent == 2
        assert tasks[2].indent == 4

    def test_skips_non_task_lines(self):
        plan = (
            "# Phase 1: Core\n"
            "\n"
            "## Objective\n"
            "Build the core.\n"
            "\n"
            "- [x] First task\n"
            "Some text.\n"
            "- [x] Second task\n"
        )
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 2

    def test_full_plan(self):
        plan = """\
# MyApp

Web app built with Python/FastAPI.

- [x] Set up project structure and build system
- [x] Add user login form [feat: "User auth"]
  - [x] Create login page template
  - [x] Wire up auth backend [feat: "User auth"]
- [x] Build activity overview [feat: "Dashboard"]
- [x] Fix email validation [fix: "email format not checked"]
"""
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 6
        assert tasks[0].text == "Set up project structure and build system"
        assert tasks[0].features == []
        assert tasks[1].features == ["User auth"]
        assert tasks[4].features == ["Dashboard"]
        assert tasks[5].fixes == ["email format not checked"]

    def test_returns_completed_task_dataclass(self):
        plan = "- [x] Task one\n"
        tasks = parse_completed_tasks(plan)
        assert isinstance(tasks[0], CompletedTask)

    def test_multi_fix_annotation(self):
        plan = '- [x] Fix layout bugs [fix: "sidebar overlap", "footer gap"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].text == "Fix layout bugs"
        assert tasks[0].fixes == ["sidebar overlap", "footer gap"]
        assert tasks[0].features == []

    def test_annotation_like_text_midline_not_parsed(self):
        plan = '- [x] Update [feat: "old"] handler to use new API\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        # The regex only matches annotations at end of line, so mid-line
        # bracket text is kept as part of the task description.
        assert tasks[0].text == 'Update [feat: "old"] handler to use new API'
        assert tasks[0].features == []
        assert tasks[0].fixes == []

    def test_annotation_with_extra_spaces(self):
        plan = '- [x] Task with spacing [feat:  "Spaced feature"]\n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].features == ["Spaced feature"]

    def test_all_lines_annotated(self):
        plan = (
            '- [x] Add login [feat: "Auth"]\n'
            '- [x] Add dashboard [feat: "Dashboard"]\n'
            '- [x] Fix crash [fix: "null pointer on empty input"]\n'
        )
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 3
        assert all(t.features or t.fixes for t in tasks)
        assert tasks[0].features == ["Auth"]
        assert tasks[1].features == ["Dashboard"]
        assert tasks[2].fixes == ["null pointer on empty input"]

    def test_all_lines_unannotated(self):
        plan = "- [x] Set up project structure\n- [x] Configure CI pipeline\n- [x] Add README\n"
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 3
        assert all(t.features == [] and t.fixes == [] for t in tasks)

    def test_mixed_feat_fix_and_bare(self):
        plan = (
            "- [x] Scaffold project\n"
            '- [x] Add search [feat: "Full-text search"]\n'
            "- [x] Refactor utils\n"
            '- [x] Fix timeout [fix: "request hangs after 30s"]\n'
            '- [x] Add export [feat: "CSV export", "PDF export"]\n'
        )
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 5
        assert tasks[0].features == [] and tasks[0].fixes == []
        assert tasks[1].features == ["Full-text search"]
        assert tasks[2].features == [] and tasks[2].fixes == []
        assert tasks[3].fixes == ["request hangs after 30s"]
        assert tasks[4].features == ["CSV export", "PDF export"]

    def test_indented_subtask_with_annotation(self):
        plan = (
            '- [x] Build auth module [feat: "Auth"]\n'
            '  - [x] Add password hashing [feat: "Auth"]\n'
            "  - [x] Write migration script\n"
        )
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 3
        assert tasks[0].indent == 0 and tasks[0].features == ["Auth"]
        assert tasks[1].indent == 2 and tasks[1].features == ["Auth"]
        assert tasks[2].indent == 2 and tasks[2].features == []

    def test_trailing_whitespace_after_annotation(self):
        plan = '- [x] Add feature [feat: "Foo"]   \n'
        tasks = parse_completed_tasks(plan)
        assert len(tasks) == 1
        assert tasks[0].features == ["Foo"]
        assert tasks[0].text == "Add feature"


class TestStripBugsSection:
    """Tests for _strip_bugs_section()."""

    def test_no_bugs_heading_unchanged(self):
        content = (
            "# MyApp — Phase 1: Core\n\nBuild the app.\n\n- [ ] Set up project\n- [ ] Add login\n"
        )
        result = _strip_bugs_section(content)
        assert "## Bugs" not in result
        assert "- [ ] Set up project" in result
        assert "- [ ] Add login" in result

    def test_strips_empty_bugs_heading(self):
        content = "# MyApp — Phase 1: Core\n\n- [ ] Task\n\n## Bugs\n"
        result = _strip_bugs_section(content)
        assert "## Bugs" not in result
        assert "- [ ] Task" in result

    def test_strips_bugs_heading_and_keeps_tasks(self):
        content = "# MyApp — Phase 1: Core\n\n## Bugs\n\n- [ ] Set up project\n- [ ] Add login\n"
        result = _strip_bugs_section(content)
        assert "## Bugs" not in result
        # Tasks that were under the LLM's ## Bugs are preserved.
        assert "- [ ] Set up project" in result
        assert "- [ ] Add login" in result

    def test_preserves_other_content(self):
        content = "# MyApp — Phase 1: Core\n\nDescription.\n\n- [ ] First\n- [ ] Second\n"
        result = _strip_bugs_section(content)
        assert "- [ ] First" in result
        assert "- [ ] Second" in result
        assert "# MyApp — Phase 1: Core" in result
        assert "Description." in result


class TestSavePlanNeverEmitsBugsSection:
    """save_plan output must never contain a ## Bugs section."""

    def test_first_write_does_not_inject_bugs_section(self, tmp_path):
        content = "# MyApp — Phase 1: Core\n\nBuild the app.\n\n- [ ] Set up project"
        save_plan(content, target_dir=tmp_path)
        result = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert "## Bugs" not in result

    def test_append_does_not_inject_bugs(self, tmp_path):
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text("# Phase 1\n\n- [ ] Existing\n", encoding="utf-8")
        save_plan("- [ ] New task", target_dir=tmp_path)
        result = plan_path.read_text(encoding="utf-8")
        assert "## Bugs" not in result

    def test_llm_bugs_heading_stripped_on_first_write(self, tmp_path):
        content = "# MyApp — Phase 1: Core\n\n## Bugs\n\n- [ ] Set up project\n- [ ] Add login\n"
        save_plan(content, target_dir=tmp_path)
        result = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert "## Bugs" not in result
        # Tasks survive the strip.
        assert "- [ ] Set up project" in result
        assert "- [ ] Add login" in result

    def test_llm_bugs_heading_stripped_on_append(self, tmp_path):
        """An LLM-authored ``## Bugs`` heading is stripped by
        :func:`_strip_bugs_section` before the body is parsed; the
        tasks under it are folded into the inbound phase content.

        Pre-T-000186 the inbound block was a bare ``## Bugs`` plus
        loose tasks; under the typed-Plan boundary the inbound text
        must include a ``## Phase phase_NNN: <title>`` header (the
        runtime computes the id; here ``phase_002`` because the
        existing on-disk PLAN.md already has ``phase_001``).
        """
        plan_path = tmp_path / _PLAN_FILENAME
        plan_path.write_text(
            "## Phase phase_001: Existing\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: Existing\n",
            encoding="utf-8",
        )
        appended = (
            "## Phase phase_002: Added\n"
            "\n"
            "## Bugs\n"
            "\n"
            "- [ ] New task\n"
        )
        save_plan(appended, target_dir=tmp_path)
        result = plan_path.read_text(encoding="utf-8")
        assert "## Bugs" not in result
        assert "New task" in result
        assert "- [ ] T-000001: Existing" in result


class TestPlanStructureForMcloop:
    """Verify that save_plan produces PLAN.md with correct structure for mcloop.

    Mcloop treats tasks under the H1 phase heading as feature work.
    duplo-generated PLAN.md must never contain a ``## Bugs`` section;
    that is an mcloop-internal convention added at runtime.
    """

    # Realistic LLM output (post-T-000186 canonical Slice C form):
    # feature tasks under the ``## Phase phase_NNN:`` header, no
    # ``## Bugs`` heading anywhere.
    _LLM_GOOD = (
        "## Phase phase_001: Core\n"
        "\n"
        "Python/FastAPI web app with PostgreSQL.\n"
        "\n"
        '- [ ] Set up project structure [feat: "User auth"]\n'
        '- [ ] Add login form [feat: "User auth"]\n'
        '- [ ] Build dashboard [feat: "Dashboard"]\n'
    )

    # Broken LLM output: feature tasks placed under ## Bugs (an
    # mcloop-internal section that duplo must never emit).
    _LLM_BAD = (
        "## Phase phase_001: Core\n"
        "\n"
        "Python/FastAPI web app with PostgreSQL.\n"
        "\n"
        "## Bugs\n"
        "\n"
        '- [ ] Set up project structure [feat: "User auth"]\n'
        '- [ ] Add login form [feat: "User auth"]\n'
        '- [ ] Build dashboard [feat: "Dashboard"]\n'
    )

    def _feature_section_tasks(self, text: str) -> list[str]:
        """Return task lines that appear after the phase header.

        Post-T-000186 the canonical PLAN.md uses ``## Phase N: <title>``
        as the phase boundary (with a ``<!-- phase_id: ... -->``
        comment beneath); pre-T-000186 it was ``# <project> — Phase N:
        <title>``. The helper anchors on the new boundary.
        """
        lines = text.splitlines()
        past_phase = False
        tasks: list[str] = []
        for line in lines:
            if line.startswith("## Phase ") or line.startswith("## Stage "):
                past_phase = True
                continue
            if past_phase and line.lstrip().startswith("- ["):
                tasks.append(line)
        return tasks

    def test_good_llm_output_preserves_feature_tasks(self, tmp_path):
        save_plan(self._LLM_GOOD, target_dir=tmp_path)
        text = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert len(self._feature_section_tasks(text)) == 3
        assert "## Bugs" not in text

    def test_bad_llm_output_has_bugs_heading_stripped(self, tmp_path):
        """When LLM includes ## Bugs, save_plan strips the heading and
        keeps the tasks that were under it."""
        save_plan(self._LLM_BAD, target_dir=tmp_path)
        text = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert "## Bugs" not in text
        assert len(self._feature_section_tasks(text)) == 3

    def test_parse_completed_tasks_sees_feature_work(self, tmp_path):
        """After save_plan, checked tasks are parsed as feature work."""
        save_plan(self._LLM_GOOD, target_dir=tmp_path)
        text = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        # Simulate mcloop checking off tasks.
        checked = text.replace("- [ ]", "- [x]")
        tasks = parse_completed_tasks(checked)
        assert len(tasks) == 3
        feat_names = [n for t in tasks for n in t.features]
        assert "User auth" in feat_names
        assert "Dashboard" in feat_names
        # None are parsed as fixes (bugs).
        assert all(t.fixes == [] for t in tasks)

    def test_save_plan_output_never_contains_bugs_section(self, tmp_path):
        """Regression: duplo must never emit ``## Bugs`` via save_plan,
        whether the content lacks the heading, has an empty one, or has
        tasks placed beneath it."""
        inputs = [
            self._LLM_GOOD,
            self._LLM_BAD,
            "# Phase 1\n\n- [ ] Task\n\n## Bugs\n",
            "# Phase 1\n\n- [ ] Task\n",
        ]
        for i, content in enumerate(inputs):
            subdir = tmp_path / f"case_{i}"
            subdir.mkdir()
            save_plan(content, target_dir=subdir)
            text = (subdir / _PLAN_FILENAME).read_text(encoding="utf-8")
            assert "## Bugs" not in text, f"case {i} leaked ## Bugs"


class TestStripTrailingCommentary:
    """Tests for _strip_trailing_commentary() and its integration with
    generate_phase_plan() when the LLM wraps the plan in code fences AND
    adds meta-commentary after the closing fence.
    """

    def test_truncates_after_last_task_with_fence_and_commentary(self):
        """End-to-end: a fenced synthesizer body with trailing
        meta-commentary still parses cleanly under the typed-Plan
        boundary. Pre-T-000186 the assertion was that the returned
        markdown string ended at ``- [ ] Third task\\n``; post-T-000186
        the boundary returns a typed :class:`bob_tools.planfile.Plan`
        whose tasks survive verbatim and whose rendered output
        contains none of the fence / commentary noise.
        """
        llm_output = (
            "```markdown\n"
            "## Phase phase_001: Core\n"
            "\n"
            "- [ ] First task\n"
            "- [ ] Second task\n"
            "- [ ] Third task\n"
            "```\n"
            "\n"
            "---\n"
            "\n"
            "**Structure:** The plan has three tasks.\n"
            "\n"
            "Want me to write it?\n"
        )
        with patch("duplo.planner.query", return_value=llm_output):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                project_name="MyApp",
                phase_number=1,
            )
        assert isinstance(result, Plan)
        text = _plan_to_text(result)
        # Three task lines survive in document order.
        task_lines = [
            ln for ln in text.splitlines() if ln.lstrip().startswith("- [ ]")
        ]
        assert any("First task" in ln for ln in task_lines)
        assert any("Second task" in ln for ln in task_lines)
        assert any("Third task" in ln for ln in task_lines)
        # The fence and trailing commentary are gone.
        assert "```" not in text
        assert "---" not in text
        assert "**Structure:**" not in text
        assert "Want me to write it?" not in text

    def test_keeps_content_unchanged_when_no_trailing_garbage(self):
        content = "# Phase 1: Core\n\n- [ ] Task one\n- [ ] Task two\n"
        assert _strip_trailing_commentary(content).endswith("- [ ] Task two\n")

    def test_truncates_after_indented_subtask(self):
        content = (
            "# Phase 1\n"
            "\n"
            "- [ ] Parent\n"
            "  - [ ] Nested subtask\n"
            "\n"
            "Trailing prose that should be dropped.\n"
        )
        result = _strip_trailing_commentary(content)
        assert result.endswith("  - [ ] Nested subtask\n")
        assert "Trailing prose" not in result

    def test_no_task_lines_returns_content_unchanged(self):
        content = "# Phase 1\n\nNo tasks here.\n"
        assert _strip_trailing_commentary(content) == content

    def test_preserves_input_when_last_task_is_final_line(self):
        # When there is no trailing commentary to strip, input is preserved
        # verbatim -- including the absence of a trailing newline. This
        # prevents unintended reformatting of already-clean LLM output.
        content = "# Phase 1\n- [ ] Task"
        assert _strip_trailing_commentary(content) == content

    def test_strips_trailing_fence_and_qa_commentary_from_real_llm_output(self):
        # Reproduces the exact failure mode from BUGS.md: fenced plan with
        # trailing "---", bold "**Structure:**" summary, and "Want me to
        # write it?" prose -- _strip_fences cannot remove the fence because
        # _FENCE_RE anchors the closing fence at \Z, so this function must.
        content = (
            "# MyApp — Phase 1: Core\n"
            "\n"
            "- [ ] Scaffold project\n"
            "- [ ] Add main window\n"
            "- [ ] Wire up entry point\n"
            "```\n"
            "\n"
            "---\n"
            "\n"
            "**Structure:** three tasks covering scaffold, window, entry.\n"
            "\n"
            "Want me to write it?\n"
        )
        result = _strip_trailing_commentary(content)
        assert result.endswith("- [ ] Wire up entry point\n")
        assert "```" not in result
        assert "---" not in result
        assert "**Structure:**" not in result
        assert "Want me to write it?" not in result


class TestEscapeMcloopTags:
    """Tests for _escape_mcloop_tags() and its integration with save_plan()."""

    def test_helper_escapes_mid_sentence_user_token(self):
        line = "- [ ] Add a [USER] confirmation step before destructive actions\n"
        result = _escape_mcloop_tags(line)
        assert "[USER]" not in result
        assert "(USER)" in result

    def test_helper_preserves_leading_directive(self):
        line = "- [ ] [USER] Run ./run.sh and confirm the window appears\n"
        assert _escape_mcloop_tags(line) == line

    def test_helper_preserves_leading_batch_but_escapes_later(self):
        line = '- [ ] [BATCH] Wire auth and log [USER] events [feat: "Auth"]\n'
        result = _escape_mcloop_tags(line)
        assert result.startswith("- [ ] [BATCH] ")
        assert "(USER)" in result
        assert "[USER]" not in result

    def test_helper_leaves_non_task_lines_alone(self):
        content = "# Phase 1\n\nThis prose mentions [USER] but is not a task.\n"
        assert _escape_mcloop_tags(content) == content

    def test_helper_handles_indented_subtask(self):
        line = "  - [ ] Handle [AUTO] scheduling edge case\n"
        result = _escape_mcloop_tags(line)
        assert result == "  - [ ] Handle (AUTO) scheduling edge case\n"

    def test_save_plan_escapes_mid_sentence_user_token(self, tmp_path):
        content = (
            "# MyApp — Phase 1: Core\n"
            "\n"
            "- [ ] Prompt the user with a [USER] confirmation before deleting data\n"
        )
        save_plan(content, target_dir=tmp_path)
        written = (tmp_path / _PLAN_FILENAME).read_text(encoding="utf-8")
        assert "[USER]" not in written
        assert "(USER)" in written


class TestStripFences:
    """Tests for _strip_fences() removing LLM code-fence wrapping."""

    def test_strips_markdown_fence(self):
        wrapped = "```markdown\n# Phase 1: Core\n\n- [ ] Task\n```"
        assert _strip_fences(wrapped) == "# Phase 1: Core\n\n- [ ] Task"

    def test_strips_bare_fence(self):
        wrapped = "```\n# Phase 1: Core\n\n- [ ] Task\n```"
        assert _strip_fences(wrapped) == "# Phase 1: Core\n\n- [ ] Task"

    def test_strips_md_fence(self):
        wrapped = "```md\n# Phase 1: Core\n```"
        assert _strip_fences(wrapped) == "# Phase 1: Core"

    def test_no_fence_unchanged(self):
        plain = "# Phase 1: Core\n\n- [ ] Task"
        assert _strip_fences(plain) == plain

    def test_inner_fences_preserved(self):
        content = "# Phase 1\n\n```python\nprint('hi')\n```\n\n- [ ] Task"
        assert _strip_fences(content) == content

    def test_strips_tilde_fence(self):
        wrapped = "~~~markdown\n# Phase 1: Core\n\n- [ ] Task\n~~~"
        assert _strip_fences(wrapped) == "# Phase 1: Core\n\n- [ ] Task"

    def test_strips_bare_tilde_fence(self):
        wrapped = "~~~\n# Phase 1: Core\n~~~"
        assert _strip_fences(wrapped) == "# Phase 1: Core"

    def test_leading_trailing_whitespace(self):
        wrapped = "  ```markdown\n# Phase 1\n```  "
        assert _strip_fences(wrapped) == "# Phase 1"

    def test_generate_phase_plan_strips_fences(self):
        """generate_phase_plan strips an outer code fence from the
        synthesizer's body before parsing it into a typed Plan.
        After T-000186 the return is a typed Plan, not markdown, so
        the assertion is on the parsed phase header and surviving
        task.
        """
        fenced = (
            "```markdown\n"
            "## Phase phase_001: Core\n"
            "\n"
            "- [ ] Task\n"
            "```"
        )
        with patch("duplo.planner.query", return_value=fenced):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                project_name="MyApp",
            )
        assert isinstance(result, Plan)
        text = _plan_to_text(result)
        assert "```" not in text
        assert "## Phase 1: Core" in text
        assert any(
            "Task" in t.text for t in result.phases[0].tasks
        )

    def test_generate_next_phase_plan_strips_fences(self):
        fenced = "```markdown\n# Phase 2: Search\n\n- [ ] Task\n```"
        with patch("duplo.planner.query", return_value=fenced):
            result = generate_next_phase_plan(_SAMPLE_CURRENT_PLAN, "feedback")
        assert not result.startswith("```")
        assert result.startswith("# Phase 2")
