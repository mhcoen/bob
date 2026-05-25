"""Tests for ``duplo.council`` and the council branch in
``duplo.planner.generate_phase_plan``.

The Orchestra side is exercised end-to-end against scripted model
adapters in ``orchestra/tests/test_workflows_council.py``. These
tests cover the Duplo-side bridge:

  - Enable/disable env vars.
  - The author_phase_plan happy path: builds the right inputs,
    receives the synthesizer's plan from a mocked run_workflow,
    writes the audit dir.
  - The author_phase_plan unhappy paths: terminal != "done",
    missing plan artifact, council config-resolution edge cases.
  - Fallback role bindings are loaded when no project config.
  - generate_phase_plan routes through council when enabled and
    through the legacy query path when not.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from duplo import council
from duplo.extractor import Feature
from duplo.planner import generate_phase_plan
from duplo.questioner import BuildPreferences


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def _sample_features() -> list[Feature]:
    return [
        Feature(name="User auth", description="Sign up and log in.", category="core"),
    ]


def _sample_prefs() -> BuildPreferences:
    return BuildPreferences(
        platform="web", language="Python", constraints=[], preferences=[]
    )


class _StubArtifactView:
    def __init__(self, value: Any) -> None:
        self.value = value


class _StubResult:
    def __init__(
        self,
        *,
        run_id: str = "test-run-1",
        terminal: str = "done",
        plan_text: str = (
            "## Phase phase_001: Core Auth\n\n- [ ] Set up.\n"
        ),
        verdict: dict[str, Any] | None = None,
        proposals: dict[str, str] | None = None,
        brief: str = "COUNCIL BRIEF: how to author phase 1.",
    ) -> None:
        self.run_id = run_id
        self.terminal = terminal
        self.log_path = Path("/tmp/council-test/log.jsonl")
        self.envelope = None
        verdict_value = verdict or {
            "decision": "accept",
            "feedback": "convergent",
            "agreements": ["minimal scaffold first"],
            "disagreements": [],
            "rejected_options": [],
        }
        proposals = proposals or {
            "proposal_code": "code's proposal text",
            "proposal_codex": "codex's proposal text",
            "proposal_kimi": "kimi's proposal text",
            "proposal_deepseek": "deepseek's proposal text",
        }
        self.artifacts: dict[str, _StubArtifactView] = {
            "council_brief": _StubArtifactView(brief),
            "plan": _StubArtifactView(plan_text),
            "judge_verdict": _StubArtifactView(verdict_value),
            "judge_decision": _StubArtifactView(verdict_value["decision"]),
            "judge_feedback": _StubArtifactView(verdict_value["feedback"]),
        }
        for name, text in proposals.items():
            self.artifacts[name] = _StubArtifactView(text)


def _patch_run_workflow(result: Any, captured: dict[str, Any] | None = None):
    """Patch ``orchestra.run_workflow`` to return ``result``.

    ``captured`` (if provided) gets keyword-args from the
    invocation written into it for assertions.
    """

    def _fake(*args: Any, **kwargs: Any) -> Any:
        if captured is not None:
            captured["args"] = args
            captured["kwargs"] = kwargs
        return result

    return patch("orchestra.run_workflow", side_effect=_fake)


# --------------------------------------------------------------------
# Enable/disable env logic
# --------------------------------------------------------------------


class TestIsEnabled:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("DUPLO_USE_COUNCIL", raising=False)
        monkeypatch.delenv("DUPLO_NO_COUNCIL", raising=False)
        assert council.is_enabled() is False

    def test_enable_via_env(self, monkeypatch):
        monkeypatch.setenv("DUPLO_USE_COUNCIL", "1")
        monkeypatch.delenv("DUPLO_NO_COUNCIL", raising=False)
        assert council.is_enabled() is True

    def test_explicit_disable_overrides_enable(self, monkeypatch):
        monkeypatch.setenv("DUPLO_USE_COUNCIL", "1")
        monkeypatch.setenv("DUPLO_NO_COUNCIL", "1")
        assert council.is_enabled() is False

    def test_truthy_variants(self, monkeypatch):
        for value in ("1", "true", "TRUE", "yes", "on", "  YES  "):
            monkeypatch.setenv("DUPLO_USE_COUNCIL", value)
            monkeypatch.delenv("DUPLO_NO_COUNCIL", raising=False)
            assert council.is_enabled() is True, value

    def test_falsy_variants(self, monkeypatch):
        for value in ("", "0", "false", "no", "off"):
            monkeypatch.setenv("DUPLO_USE_COUNCIL", value)
            monkeypatch.delenv("DUPLO_NO_COUNCIL", raising=False)
            assert council.is_enabled() is False, value

    def test_set_enabled_toggles_envs(self, monkeypatch):
        monkeypatch.delenv("DUPLO_USE_COUNCIL", raising=False)
        monkeypatch.delenv("DUPLO_NO_COUNCIL", raising=False)
        council.set_enabled(True)
        assert os.environ.get("DUPLO_USE_COUNCIL") == "1"
        assert "DUPLO_NO_COUNCIL" not in os.environ
        council.set_enabled(False)
        assert os.environ.get("DUPLO_NO_COUNCIL") == "1"
        assert "DUPLO_USE_COUNCIL" not in os.environ


# --------------------------------------------------------------------
# author_phase_plan: happy path
# --------------------------------------------------------------------


class TestAuthorPhasePlan:
    def test_returns_synthesizer_plan_text(self, tmp_path, monkeypatch):
        """T-000186 / Stage 18: ``author_phase_plan`` now returns a
        typed :class:`bob_tools.planfile.Plan` (built by
        :func:`typed_plan_from_synthesizer_text` from the synthesizer's
        body), not a markdown string. Pin the structural fields the
        upstream caller relies on.
        """
        from bob_tools.planfile import Plan

        monkeypatch.chdir(tmp_path)
        body = (
            "## Phase phase_001: synthesized plan body\n\n"
            "- [ ] do the thing\n"
        )
        result = _StubResult(plan_text=body)
        with _patch_run_workflow(result):
            plan = council.author_phase_plan(
                prompt="reference material body",
                system="planner system directive",
                phase_num=1,
                project_dir=tmp_path,
            )
        assert isinstance(plan, Plan)
        assert [phase.phase_id for phase in plan.phases] == ["phase_001"]
        assert plan.phases[0].title == "synthesized plan body"
        assert [task.text for task in plan.phases[0].tasks] == [
            "do the thing"
        ]

    def test_passes_canonical_question_for_phase_num(self, tmp_path):
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(), captured=captured):
            council.author_phase_plan(
                prompt="prompt",
                system="system",
                phase_num=3,
                project_dir=tmp_path,
            )
        inputs = captured["args"][1]
        assert inputs["question"] == (
            "Author the Phase 3 plan from the reference material above."
        )

    def test_state_includes_prompt_and_system(self, tmp_path):
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(), captured=captured):
            council.author_phase_plan(
                prompt="REFERENCE-MARKER",
                system="SYSTEM-MARKER",
                phase_num=1,
                project_dir=tmp_path,
            )
        inputs = captured["args"][1]
        assert "REFERENCE-MARKER" in inputs["state"]
        assert "SYSTEM-MARKER" in inputs["state"]

    def test_ledger_and_design_inputs_are_empty_strings(self, tmp_path):
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(), captured=captured):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=1, project_dir=tmp_path
            )
        inputs = captured["args"][1]
        assert inputs["ledger_slice"] == ""
        assert inputs["design_context"] == ""

    def test_invokes_council_four_canonical_workflow_by_name(self, tmp_path):
        # Canonical-mode plan authoring routes through the
        # workflow-split name landed in orchestra ee44ba5; reauthor
        # mode routes through council_four_reauthor instead.
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(), captured=captured):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=1, project_dir=tmp_path
            )
        assert captured["args"][0] == "council_four_canonical"

    def test_data_root_under_audits_council(self, tmp_path):
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(), captured=captured):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=1, project_dir=tmp_path
            )
        data_root = captured["kwargs"]["data_root"]
        assert tmp_path / ".duplo" / "audits" / "council" / "_runs" == data_root

    def test_audit_dir_written_after_success(self, tmp_path):
        result = _StubResult(run_id="r-42")
        with _patch_run_workflow(result):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=1, project_dir=tmp_path
            )
        audit_dir = tmp_path / ".duplo" / "audits" / "council" / "r-42"
        assert audit_dir.is_dir()
        for name in (
            "brief.md",
            "proposal_code.md",
            "proposal_codex.md",
            "proposal_kimi.md",
            "proposal_deepseek.md",
            "plan.md",
            "verdict.json",
            "run_meta.json",
        ):
            assert (audit_dir / name).exists(), name

    def test_audit_run_meta_records_terminal_and_question(self, tmp_path):
        result = _StubResult(run_id="r-7")
        with _patch_run_workflow(result):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=2, project_dir=tmp_path
            )
        meta_path = (
            tmp_path / ".duplo" / "audits" / "council" / "r-7" / "run_meta.json"
        )
        meta = json.loads(meta_path.read_text())
        assert meta["run_id"] == "r-7"
        assert meta["terminal"] == "done"
        assert "Phase 2 plan" in meta["question"]

    def test_prints_council_notice_to_stderr(self, tmp_path, capsys):
        with _patch_run_workflow(_StubResult()):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=1, project_dir=tmp_path
            )
        captured = capsys.readouterr()
        assert "council mode" in captured.err
        assert "6 LLM calls" in captured.err


# --------------------------------------------------------------------
# author_phase_plan: unhappy paths
# --------------------------------------------------------------------


class TestAuthorPhasePlanFailureModes:
    def test_terminal_not_done_raises(self, tmp_path):
        bad = _StubResult(
            terminal="stop",
            verdict={
                "decision": "reframe",
                "feedback": "proposals split too widely",
                "agreements": [],
                "disagreements": [],
                "rejected_options": [],
            },
        )
        with _patch_run_workflow(bad):
            with pytest.raises(council.CouncilError, match="reframe"):
                council.author_phase_plan(
                    prompt="p",
                    system="s",
                    phase_num=1,
                    project_dir=tmp_path,
                )

    def test_missing_plan_artifact_raises(self, tmp_path):
        result = _StubResult()
        del result.artifacts["plan"]
        with _patch_run_workflow(result):
            with pytest.raises(council.CouncilError, match="no 'plan' artifact"):
                council.author_phase_plan(
                    prompt="p",
                    system="s",
                    phase_num=1,
                    project_dir=tmp_path,
                )

    def test_empty_plan_raises(self, tmp_path):
        result = _StubResult(plan_text="   \n   ")
        with _patch_run_workflow(result):
            with pytest.raises(council.CouncilError, match="empty"):
                council.author_phase_plan(
                    prompt="p",
                    system="s",
                    phase_num=1,
                    project_dir=tmp_path,
                )

    def test_run_workflow_raises_wrapped_in_council_error(self, tmp_path):
        with patch(
            "orchestra.run_workflow",
            side_effect=RuntimeError("orchestra exploded"),
        ):
            with pytest.raises(council.CouncilError, match="orchestra exploded"):
                council.author_phase_plan(
                    prompt="p",
                    system="s",
                    phase_num=1,
                    project_dir=tmp_path,
                )


# --------------------------------------------------------------------
# Canonical-mode markdown format validator (Slice D regression)
# --------------------------------------------------------------------


class TestComputeRequiredPhaseId:
    """Duplo computes required_phase_id deterministically from the
    existing PLAN.md (highest phase_NNN + 1, zero-padded, NOT the
    smallest gap). Codex's safe rule; see the
    "Per-call model authority is the wrong ownership boundary"
    section in orchestra/design/synthesizer-output-contract.md.
    """

    def test_no_plan_md_returns_phase_001(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "PLAN.md"
        assert council.compute_required_phase_id(plan_path) == "phase_001"

    def test_one_prior_phase_returns_phase_002(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("## Phase phase_001: First\n\n- [ ] x\n")
        assert council.compute_required_phase_id(plan_path) == "phase_002"

    def test_four_prior_phases_returns_phase_005(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(
            "## Phase phase_001: A\n\n- [ ] a\n\n"
            "## Phase phase_002: B\n\n- [ ] b\n\n"
            "## Phase phase_003: C\n\n- [ ] c\n\n"
            "## Phase phase_004: D\n\n- [ ] d\n"
        )
        assert council.compute_required_phase_id(plan_path) == "phase_005"

    def test_gap_uses_highest_plus_one_not_gap(self, tmp_path: Path) -> None:
        # PLAN.md contains phase_001 and phase_003. Required is
        # phase_004 (highest + 1), NOT the gap at phase_002. Codex's
        # explicit rule: gap-filling would let stale lineage state
        # coexist with new entries under the same identifier.
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(
            "## Phase phase_001: A\n\n- [ ] a\n\n"
            "## Phase phase_003: C\n\n- [ ] c\n"
        )
        assert council.compute_required_phase_id(plan_path) == "phase_004"

    def test_non_strict_phase_ids_ignored(self, tmp_path: Path) -> None:
        # Phase ids that don't match the strict phase_NNN form do not
        # contribute to the highest-plus-one calculation.
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text(
            "## Phase phase_007: ignored-numeric-too-small\n\n- [ ] a\n\n"
            "## Phase legacy_thing: not-strict\n\n- [ ] b\n"
        )
        # Strict ids: phase_007. Non-strict: legacy_thing (skipped).
        # Result: phase_008.
        assert council.compute_required_phase_id(plan_path) == "phase_008"

    def test_zero_padding_three_digits(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("## Phase phase_007: First\n\n- [ ] x\n")
        # +1 = 8, formatted as phase_008.
        assert council.compute_required_phase_id(plan_path) == "phase_008"


class TestRequiredPhaseIdInjection:
    """author_phase_plan computes required_phase_id and threads it
    into the council inputs alongside state / question / ledger_slice
    / design_context. Pinned so a future refactor cannot drop the
    field silently."""

    def test_inputs_dict_carries_required_phase_id(
        self, tmp_path: Path
    ) -> None:
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(), captured=captured):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=1, project_dir=tmp_path
            )
        inputs = captured["args"][1]
        assert "required_phase_id" in inputs
        assert inputs["required_phase_id"] == "phase_001"

    def test_required_phase_id_increments_with_existing_plan(
        self, tmp_path: Path
    ) -> None:
        plan_path = tmp_path / "PLAN.md"
        plan_path.write_text("## Phase phase_001: First\n\n- [ ] x\n")

        # _StubResult default returns phase_001 — but the validator
        # now requires phase_002 (since PLAN.md already has phase_001).
        # Provide a stub plan that satisfies the new constraint.
        body = (
            "## Phase phase_002: Second\n\n- [ ] do the thing\n"
        )
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(plan_text=body), captured=captured):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=2, project_dir=tmp_path
            )
        inputs = captured["args"][1]
        assert inputs["required_phase_id"] == "phase_002"


class TestCanonicalPlanFormatValidator:
    """Replacement for the removed ``_validate_canonical_plan_markdown``
    layer.

    Stage 18 moved canonical-mode plan validation into bob_tools.planfile
    (``parse_plan`` + ``validate_plan(constructed=True)`` +
    ``assert_mcloop_canonical``) and Duplo's
    :func:`council.typed_plan_from_synthesizer_text` drives it. These
    tests pin the typed-behavior surface: what the typed conversion
    accepts versus rejects, with the same fixture shapes the old
    regex-based ``_validate_canonical_plan_markdown`` tests used so the
    coverage scope is preserved.
    """

    def test_passes_on_valid_plan(self) -> None:
        body = (
            "## Phase phase_001: Setup\n\n"
            "- [ ] Initialize package\n"
            "- [ ] Add smoke test\n\n"
            "## Phase phase_002: Tests\n\n"
            "- [ ] Add unit tests\n"
        )
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase_001"
        )
        assert [phase.phase_id for phase in plan.phases] == [
            "phase_001",
            "phase_002",
        ]

    def test_passes_with_required_phase_id_match(self) -> None:
        body = "## Phase phase_003: Third\n\n- [ ] task\n"
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase_003"
        )
        assert [phase.phase_id for phase in plan.phases] == ["phase_003"]

    def test_check5_rejects_required_phase_id_mismatch(self) -> None:
        from bob_tools.planfile import PlanValidationError

        body = "## Phase phase_001: First\n\n- [ ] task\n"
        with pytest.raises(
            PlanValidationError,
            match=r"required_phase_id 'phase_002' not present",
        ):
            council.typed_plan_from_synthesizer_text(
                body, required_phase_id="phase_002"
            )

    def test_check5_passes_when_required_id_present_among_multiple(
        self,
    ) -> None:
        # Plan body has multiple phases; required_phase_id need only be
        # one of them (the synthesizer's authoring of THIS invocation's
        # new phase is the load-bearing one).
        body = (
            "## Phase phase_002: Second\n\n- [ ] a\n\n"
            "## Phase phase_003: Third\n\n- [ ] b\n"
        )
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase_002"
        )
        assert "phase_002" in [phase.phase_id for phase in plan.phases]

    def test_non_strict_phase_id_is_accepted_now(self) -> None:
        """The old regex-based ``_validate_canonical_plan_markdown``
        rejected non-strict ``phase_id`` suffixes (``phase1``,
        ``phase_1``). The typed validator does not enforce a specific
        suffix shape; that was a duplo-side defensive check, not an
        mcloop input requirement. Either id parses fine via
        :func:`parse_plan` because it is a single identifier word
        following the ``## Phase`` keyword.
        """
        body = "## Phase phase1: First\n\n- [ ] task\n"
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase1"
        )
        assert [phase.phase_id for phase in plan.phases] == ["phase1"]

    def test_short_phase_id_suffix_is_accepted_now(self) -> None:
        """Companion to ``test_non_strict_phase_id_is_accepted_now``:
        the typed validator accepts ``phase_1`` too. Strict three-digit
        zero-padded suffixes are a duplo runtime CONVENTION (what
        ``compute_required_phase_id`` emits), not a validation rule.
        """
        body = "## Phase phase_1: First\n\n- [ ] task\n"
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase_1"
        )
        assert [phase.phase_id for phase in plan.phases] == ["phase_1"]

    def test_check4_rejects_duplicate_phase_id(self) -> None:
        from bob_tools.planfile import PlanValidationError

        body = (
            "## Phase phase_001: First\n\n- [ ] a\n\n"
            "## Phase phase_001: Duplicate\n\n- [ ] b\n"
        )
        with pytest.raises(
            PlanValidationError,
            match="duplicate phase_id",
        ):
            council.typed_plan_from_synthesizer_text(
                body, required_phase_id="phase_001"
            )

    def test_check4_names_all_duplicates(self) -> None:
        from bob_tools.planfile import PlanValidationError

        body = (
            "## Phase phase_001: A\n\n- [ ] a\n\n"
            "## Phase phase_001: B\n\n- [ ] b\n\n"
            "## Phase phase_002: C\n\n- [ ] c\n\n"
            "## Phase phase_002: D\n\n- [ ] d\n"
        )
        # The typed validator surfaces every duplicate id pair, in
        # whichever order ``validate_plan`` walks the phases.
        with pytest.raises(PlanValidationError) as excinfo:
            council.typed_plan_from_synthesizer_text(
                body, required_phase_id="phase_001"
            )
        message = str(excinfo.value)
        assert "phase_001" in message
        assert "phase_002" in message

    def test_out_of_order_phase_ids_accepted_now(self) -> None:
        """The old regex validator rejected non-monotonic phase ids;
        the typed validator does not. mcloop does not require monotonic
        ordering — phase identity is per-phase, and ordering is the
        responsibility of duplo's runtime envelope (which authors
        ``compute_required_phase_id`` from existing PLAN.md state).
        Out-of-order ids in a freshly synthesized body would still be
        caught when duplo passes the body through
        :func:`assert_mcloop_canonical` in production, only via the
        ordinal-monotonicity check the renderer enforces; here the
        synthesizer-side check no longer fires.
        """
        body = (
            "## Phase phase_001: A\n\n- [ ] a\n\n"
            "## Phase phase_003: C\n\n- [ ] c\n\n"
            "## Phase phase_002: B\n\n- [ ] b\n"
        )
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase_001"
        )
        assert [phase.phase_id for phase in plan.phases] == [
            "phase_001",
            "phase_003",
            "phase_002",
        ]

    def test_strictly_increasing_phase_ids_accepted(self) -> None:
        body = (
            "## Phase phase_001: A\n\n- [ ] a\n\n"
            "## Phase phase_002: B\n\n- [ ] b\n\n"
            "## Phase phase_005: C\n\n- [ ] c\n"
        )
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase_001"
        )
        assert [phase.phase_id for phase in plan.phases] == [
            "phase_001",
            "phase_002",
            "phase_005",
        ]

    def test_passes_on_single_phase_single_task(self) -> None:
        body = "## Phase phase_001: Bring up scaffold\n\n- [ ] do the thing\n"
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase_001"
        )
        assert len(plan.phases) == 1
        assert len(plan.phases[0].tasks) == 1

    def test_empty_phase_bodies_accepted_now(self) -> None:
        """The old validator required every phase to carry at least one
        ``- [ ]`` task line; the typed validator does not. A phase whose
        body is pure prose now parses to a Phase with empty ``tasks``.
        The Plan still validates because nothing in
        ``validate_plan(constructed=True)`` or
        :func:`assert_mcloop_canonical` requires a minimum task count.
        Duplo's prompt template still asks for ``5-15 checklist items
        per phase``; that is a quality target, not a contract.
        """
        body = (
            "## Phase phase_001: Setup\n\n"
            "Some narrative prose, no tasks.\n\n"
            "## Phase phase_002: Tests\n\n"
            "More prose without a checklist.\n"
        )
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase_001"
        )
        assert all(len(phase.tasks) == 0 for phase in plan.phases)

    def test_no_phase_headers_rejected_via_required_id_check(self) -> None:
        """When the body has no ``## Phase`` headers at all, parsing
        succeeds with an empty ``phases`` tuple. The typed validator
        then catches the absence via the explicit
        ``required_phase_id not in actual_phase_ids`` check.
        """
        from bob_tools.planfile import PlanValidationError

        body = "# fswatch-run\n\nPure prose project description.\n"
        with pytest.raises(
            PlanValidationError,
            match="required_phase_id 'phase_001' not present",
        ):
            council.typed_plan_from_synthesizer_text(
                body, required_phase_id="phase_001"
            )

    def test_pre_slice_c_header_form_gets_auto_id(self) -> None:
        """Pre-Slice C plans used ``# Phase 1: ...``: a single-hash
        header with the legacy ordinal scheme. The typed parser
        accepts that as a phase header without an explicit phase_id;
        :func:`migrate` then auto-assigns ``phase_001`` to fill the
        gap. This is the migration-friendly behavior: legacy bodies
        survive the typed boundary as long as the auto-assigned id
        matches the caller-supplied ``required_phase_id``.
        """
        body = "# Phase 1: legacy form\n\n- [ ] task\n"
        plan = council.typed_plan_from_synthesizer_text(
            body, required_phase_id="phase_001"
        )
        assert [phase.phase_id for phase in plan.phases] == ["phase_001"]

    def test_atomicity_validation_failure_does_not_write_plan(
        self, tmp_path: Path
    ) -> None:
        """When the synthesizer's body cannot be turned into a valid
        typed Plan (here, the body has no phase headers so the
        ``required_phase_id`` check fires), ``author_phase_plan``
        raises ``PlanValidationError`` instead of returning. The
        upstream caller (``planner.save_plan``) never receives the
        Plan, so PLAN.md is never written. This pins the atomicity
        invariant under the new typed boundary.
        """
        from bob_tools.planfile import PlanValidationError

        bad_plan = (
            "# Just a project header, no phase headers.\n\n"
            "Narrative prose with no checklist tasks.\n"
        )
        result = _StubResult(plan_text=bad_plan)
        with _patch_run_workflow(result):
            with pytest.raises(PlanValidationError):
                council.author_phase_plan(
                    prompt="p",
                    system="s",
                    phase_num=1,
                    project_dir=tmp_path,
                )
        # PLAN.md is written by the upstream caller (planner.save_plan);
        # author_phase_plan does not touch the file. The raise
        # propagates and PLAN.md remains absent.
        assert not (tmp_path / "PLAN.md").exists()


# --------------------------------------------------------------------
# Config resolution
# --------------------------------------------------------------------


class TestConfigResolution:
    def test_falls_back_when_no_project_config(self, tmp_path):
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(), captured=captured):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=1, project_dir=tmp_path
            )
        cfg = captured["args"][2]
        for role in (
            "framer",
            "proposer_code",
            "proposer_codex",
            "proposer_kimi",
            "proposer_deepseek",
            "synthesizer",
        ):
            assert role in cfg.roles, role
        assert "council_four_canonical" in cfg.workflows
        assert (
            cfg.workflows["council_four_canonical"].pattern
            == "council_four_canonical"
        )
        assert "council_four_reauthor" in cfg.workflows
        assert (
            cfg.workflows["council_four_reauthor"].pattern
            == "council_four_reauthor"
        )

    def test_fallback_proposers_pairwise_distinct(self, tmp_path):
        """Cross-model diversity at the proposer layer is what the
        council fan-out is for; Orchestra's ``_validate_council_four``
        still enforces it. The synthesizer is allowed to share a model
        string with a proposer (see council-actor-bindings.md in the
        Orchestra tree); that constraint is gone.
        """
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(), captured=captured):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=1, project_dir=tmp_path
            )
        cfg = captured["args"][2]
        proposers = (
            "proposer_code",
            "proposer_codex",
            "proposer_kimi",
            "proposer_deepseek",
        )
        seen: dict[tuple[str, str | None], str] = {}
        for role in proposers:
            b = cfg.roles[role]
            key = (b.adapter, b.model)
            assert key not in seen, (
                f"proposer {role} collides with {seen[key]} on {key}"
            )
            seen[key] = role

    def test_uses_project_config_when_six_council_roles_present(
        self, tmp_path
    ):
        orchestra_dir = tmp_path / ".orchestra"
        orchestra_dir.mkdir()
        config = {
            "roles": {
                "framer": {"adapter": "claude_code_text", "model": "haiku"},
                "proposer_code": {
                    "adapter": "claude_code_text",
                    "model": "sonnet",
                },
                "proposer_codex": {
                    "adapter": "codex_text",
                    "model": "gpt-5.5",
                },
                "proposer_kimi": {
                    "adapter": "claude_code_text_kimi",
                    "model": "kimi-k2.6",
                },
                "proposer_deepseek": {
                    "adapter": "claude_code_text_deepseek",
                    "model": "deepseek-v4-pro",
                },
                "synthesizer": {
                    "adapter": "claude_code_text",
                    "model": "opus",
                },
                "extra_role": {
                    "adapter": "claude_code_text",
                    "model": "sonnet",
                },
            },
            "workflows": {
                "council_four_canonical": {"pattern": "council_four_canonical"},
                "council_four_reauthor": {"pattern": "council_four_reauthor"},
            },
        }
        (orchestra_dir / "config.json").write_text(json.dumps(config))
        captured: dict[str, Any] = {}
        with _patch_run_workflow(_StubResult(), captured=captured):
            council.author_phase_plan(
                prompt="p", system="s", phase_num=1, project_dir=tmp_path
            )
        cfg = captured["args"][2]
        # Project config wins -> the extra_role survives.
        assert "extra_role" in cfg.roles


# --------------------------------------------------------------------
# Planner integration
# --------------------------------------------------------------------


class TestPlannerCouncilBranch:
    def test_planner_uses_query_when_council_disabled(self, monkeypatch):
        monkeypatch.delenv("DUPLO_USE_COUNCIL", raising=False)
        monkeypatch.delenv("DUPLO_NO_COUNCIL", raising=False)
        with (
            patch(
                "duplo.planner.query", return_value="# Phase 1: legacy"
            ) as mock_query,
            patch(
                "duplo.planner.council.author_phase_plan"
            ) as mock_council,
        ):
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert mock_query.call_count == 1
        assert mock_council.call_count == 0

    def test_planner_uses_council_when_enabled(self, monkeypatch):
        """Confirm the council branch is exercised when the env var is
        set. The council's H1 phase heading gets stripped-and-rendered
        per the new envelope contract; assert against the council's
        body content (which survives the strip) rather than against
        the council's H1 (which Duplo overwrites)."""
        monkeypatch.setenv("DUPLO_USE_COUNCIL", "1")
        monkeypatch.delenv("DUPLO_NO_COUNCIL", raising=False)
        with (
            patch("duplo.planner.query") as mock_query,
            patch(
                "duplo.planner.council.author_phase_plan",
                return_value=(
                    "# Council Phase 1\n\n- [ ] task-from-council\n"
                ),
            ) as mock_council,
        ):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert mock_council.call_count == 1
        assert mock_query.call_count == 0
        # The council's H1 is stripped; Duplo prepends its canonical.
        # The council's body content survives.
        assert "- [ ] task-from-council" in result

    def test_planner_council_receives_phase_num(self, monkeypatch):
        monkeypatch.setenv("DUPLO_USE_COUNCIL", "1")
        monkeypatch.delenv("DUPLO_NO_COUNCIL", raising=False)
        with patch(
            "duplo.planner.council.author_phase_plan",
            return_value="# X",
        ) as mock_council:
            generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                phase={"phase": 4, "title": "T", "goal": "g"},
            )
        kwargs = mock_council.call_args.kwargs
        assert kwargs["phase_num"] == 4


# --------------------------------------------------------------------
# duplo init writes .orchestra/config.json
# --------------------------------------------------------------------


class TestInitWritesOrchestraConfig:
    def test_no_args_init_creates_orchestra_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from duplo import init as init_mod
        import argparse as ap

        ns = ap.Namespace(
            url=None,
            from_description=None,
            deep=False,
            force=False,
            command="init",
        )
        init_mod.run_init(ns)
        cfg_path = tmp_path / ".orchestra" / "config.json"
        assert cfg_path.exists()
        cfg = json.loads(cfg_path.read_text())
        assert set(cfg["roles"].keys()) == {
            "framer",
            "proposer_code",
            "proposer_codex",
            "proposer_kimi",
            "proposer_deepseek",
            "synthesizer",
        }
        assert (
            cfg["workflows"]["council_four_canonical"]["pattern"]
            == "council_four_canonical"
        )
        assert (
            cfg["workflows"]["council_four_reauthor"]["pattern"]
            == "council_four_reauthor"
        )

    def test_init_does_not_overwrite_existing_orchestra_config(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        orchestra_dir = tmp_path / ".orchestra"
        orchestra_dir.mkdir()
        existing = {"roles": {"editor": {"adapter": "claude_code_agent"}}}
        cfg_path = orchestra_dir / "config.json"
        cfg_path.write_text(json.dumps(existing))
        from duplo import init as init_mod
        import argparse as ap

        ns = ap.Namespace(
            url=None,
            from_description=None,
            deep=False,
            force=False,
            command="init",
        )
        init_mod.run_init(ns)
        assert json.loads(cfg_path.read_text()) == existing
