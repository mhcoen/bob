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
        assert plan == body.strip()

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


class TestCanonicalPlanFormatValidator:
    def test_passes_on_valid_plan(self) -> None:
        body = (
            "## Phase phase_001: Setup\n\n"
            "- [ ] Initialize package\n"
            "- [ ] Add smoke test\n\n"
            "## Phase phase_002: Tests\n\n"
            "- [ ] Add unit tests\n"
        )
        # No raise.
        council._validate_canonical_plan_markdown(body)

    def test_passes_on_single_phase_single_task(self) -> None:
        body = "## Phase phase_001: Bring up scaffold\n\n- [ ] do the thing\n"
        council._validate_canonical_plan_markdown(body)

    def test_rejects_zero_tasks_total(self) -> None:
        body = (
            "## Phase phase_001: Setup\n\n"
            "Some narrative prose, no tasks.\n\n"
            "## Phase phase_002: Tests\n\n"
            "More prose without a checklist.\n"
        )
        with pytest.raises(
            council.CanonicalPlanFormatError, match="zero `- \\[ \\]` task lines"
        ):
            council._validate_canonical_plan_markdown(body)

    def test_rejects_phase_with_no_tasks(self) -> None:
        body = (
            "## Phase phase_001: Setup\n\n"
            "- [ ] Initialize package\n\n"
            "## Phase phase_002: Tests\n\n"
            "Narrative prose, no tasks for this phase.\n"
        )
        with pytest.raises(
            council.CanonicalPlanFormatError, match="phase_002"
        ):
            council._validate_canonical_plan_markdown(body)

    def test_rejects_no_phase_headers(self) -> None:
        body = (
            "# fswatch-run\n\n"
            "Pure prose project description with - [ ] dummy task.\n"
        )
        with pytest.raises(
            council.CanonicalPlanFormatError, match="no `## Phase phase_NNN"
        ):
            council._validate_canonical_plan_markdown(body)

    def test_rejects_old_pre_slice_c_header_form(self) -> None:
        # Pre-Slice C plans used `# Phase 1: ...`; the strict validator
        # treats those as no-phase-header (they fail the regex).
        body = "# Phase 1: legacy form\n\n- [ ] task\n"
        with pytest.raises(
            council.CanonicalPlanFormatError, match="no `## Phase phase_NNN"
        ):
            council._validate_canonical_plan_markdown(body)

    def test_atomicity_validation_failure_does_not_write_plan(
        self, tmp_path: Path
    ) -> None:
        """The author_phase_plan caller writes PLAN.md from the
        returned text. A CanonicalPlanFormatError raise means
        author_phase_plan never returned, so the caller never
        wrote PLAN.md. This test verifies the contract by
        asserting the raise propagates and PLAN.md does not
        appear in the project dir.
        """
        bad_plan = (
            "## Phase phase_001: Setup\n\n"
            "Narrative prose with no checklist tasks.\n"
        )
        result = _StubResult(plan_text=bad_plan)
        with _patch_run_workflow(result):
            with pytest.raises(council.CanonicalPlanFormatError):
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
        monkeypatch.setenv("DUPLO_USE_COUNCIL", "1")
        monkeypatch.delenv("DUPLO_NO_COUNCIL", raising=False)
        with (
            patch("duplo.planner.query") as mock_query,
            patch(
                "duplo.planner.council.author_phase_plan",
                return_value="# Council Phase 1",
            ) as mock_council,
        ):
            result = generate_phase_plan(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert mock_council.call_count == 1
        assert mock_query.call_count == 0
        assert "Council Phase 1" in result

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
