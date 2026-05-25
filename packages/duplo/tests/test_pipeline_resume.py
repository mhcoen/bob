"""Tests for the partial-PLAN.md resume path in pipeline._subsequent_run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bob_tools.planfile import (
    Phase,
    Plan,
    make_task,
)

from duplo import pipeline
from duplo.claude_cli import ClaudeCliError
from duplo.hasher import HashDiff


def _typed_phase(
    phase_id: str, ordinal: int, title: str, *, done: bool = False
) -> Phase:
    """Build a typed :class:`Phase` matching the canonical renderer output.

    After T-000186 the planfile renderer is the source of truth for
    PLAN.md bytes; phases land as ``## Phase N: <title>`` headers with
    a ``<!-- phase_id: phase_NNN -->`` comment line. The resume tests
    assert against the renderer's output rather than the retired
    ``# <project> -- Phase N: <title>`` H1 envelope.
    """
    from bob_tools.planfile.model import TaskStatus

    task = make_task(
        "do work",
        status=TaskStatus.DONE if done else TaskStatus.TODO,
    )
    return Phase(
        phase_id=phase_id,
        phase_id_source="explicit_comment",
        ordinal=ordinal,
        keyword="Phase",
        title=title,
        prose="",
        subsections=(),
        tasks=(task,),
        line_number=0,
    )


def _typed_plan(project: str, phases: tuple[Phase, ...]) -> str:
    """Render a typed multi-phase Plan to PLAN.md bytes via the planfile
    renderer so the resulting text matches what
    :func:`bob_tools.planfile.save` actually writes to disk.
    """
    from bob_tools.planfile import migrate, validate_plan
    from bob_tools.planfile.renderer import render_plan

    plan = Plan(
        magic_version=1,
        project_title=project,
        preamble="Project header.",
        phases=phases,
        bugs=None,
        source_path=None,
    )
    plan = migrate(plan)
    validate_plan(plan, constructed=True)
    return render_plan(plan)


class TestObservedPhaseCount:
    """Pure unit tests for pipeline._observed_phase_count.

    After T-000186 the resume bookkeeping counts the canonical H2 phase
    headers the planfile renderer emits (``## Phase phase_NNN: <title>``
    plus a ``<!-- phase_id: phase_NNN -->`` comment line) instead of
    the retired ``# <project> -- Phase N: <title>`` H1 envelope.
    """

    def test_counts_canonical_h2_phase_headers(self) -> None:
        text = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "# MyApp\n"
            "\n"
            "Intro prose.\n"
            "\n"
            "## Phase phase_001: Scaffold\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: task\n"
            "\n"
            "## Phase phase_002: Features\n"
            "<!-- phase_id: phase_002 -->\n"
            "\n"
            "- [ ] T-000002: another\n"
            "\n"
            "## Notes about Phase 99 from long ago\n"
        )
        # The two ``## Phase phase_NNN: <title>`` headers count; the
        # ``## Notes about Phase 99 ...`` H2 (without a keyword-first
        # ``Phase|Stage`` token) does not.
        assert pipeline._observed_phase_count(text) == 2

    def test_zero_on_header_only(self) -> None:
        text = (
            "<!-- bob-plan-format: 1 -->\n"
            "\n"
            "# MyApp\n"
            "\n"
            "Just a project header and some prose, no phase envelopes here.\n"
            "\n"
            "## Notes about Phase 1 introduced filtering\n"
        )
        assert pipeline._observed_phase_count(text) == 0

    def test_counts_stage_headers_too(self) -> None:
        # mcloop also recognizes the ``Stage`` keyword as a phase
        # heading; the resume counter mirrors that.
        text = (
            "## Stage 1: Foundations\n"
            "<!-- phase_id: phase_001 -->\n"
            "\n"
            "- [ ] T-000001: task\n"
        )
        assert pipeline._observed_phase_count(text) == 1


# Sentinel returned by stubs that swallow all positional/keyword args.
def _noop(*args, **kwargs):
    return None


def _resume_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub every helper _subsequent_run walks through before State 2."""
    monkeypatch.setattr(pipeline, "read_spec", lambda *a, **kw: None)
    monkeypatch.setattr(pipeline, "validate_for_run", _noop)
    monkeypatch.setattr(pipeline, "_print_status", _noop)
    monkeypatch.setattr(pipeline, "_print_summary", _noop)
    monkeypatch.setattr(pipeline, "save_hashes", _noop)
    monkeypatch.setattr(pipeline, "load_hashes", lambda *a, **kw: {})
    monkeypatch.setattr(pipeline, "compute_hashes", lambda *a, **kw: {})
    monkeypatch.setattr(pipeline, "diff_hashes", lambda *a, **kw: HashDiff())
    monkeypatch.setattr(pipeline, "_plan_is_complete", lambda *a, **kw: False)
    monkeypatch.setattr(
        pipeline, "_plan_has_unchecked_tasks", lambda *a, **kw: False
    )
    monkeypatch.setattr(
        pipeline, "_detect_and_append_gaps", lambda *a, **kw: (0, 0, 0, 0)
    )
    monkeypatch.setattr(
        pipeline, "_rescrape_product_url", lambda *a, **kw: (0, 0, "")
    )
    monkeypatch.setattr(pipeline, "_load_preferences", lambda *a, **kw: [])
    monkeypatch.setattr(
        pipeline, "_resolve_platform_profiles", lambda *a, **kw: []
    )
    monkeypatch.setattr(pipeline, "_announce_profiles", _noop)
    monkeypatch.setattr(pipeline, "write_scaffold", lambda *a, **kw: [])
    monkeypatch.setattr(
        pipeline, "format_scaffold_notice", lambda *a, **kw: ""
    )
    monkeypatch.setattr(pipeline, "write_claude_md", _noop)
    monkeypatch.setattr(pipeline, "format_local_overrides", lambda *a, **kw: "")
    monkeypatch.setattr(
        pipeline, "format_planner_system_addendum", lambda *a, **kw: ""
    )


def _write_state(tmp_path: Path, *, roadmap: list[dict], plan_text: str) -> None:
    """Write a minimal .duplo/duplo.json + PLAN.md in *tmp_path*."""
    duplo_dir = tmp_path / ".duplo"
    duplo_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "source_url": "https://example.com",
        "app_name": "MyApp",
        "features": [],
        "preferences": {
            "platform": "web",
            "language": "Python",
            "constraints": [],
            "preferences": [],
        },
        "roadmap": roadmap,
        "phases": [],
        "current_phase": 0,
    }
    (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "PLAN.md").write_text(plan_text, encoding="utf-8")


class TestResumePath:
    """Integration tests for the State 2 resume branch."""

    _ROADMAP_4 = [
        {"phase": i, "title": f"P{i}", "goal": "g", "features": [], "test": "ok"}
        for i in range(4)
    ]
    _ROADMAP_3 = [
        {"phase": i, "title": f"P{i}", "goal": "g", "features": [], "test": "ok"}
        for i in range(3)
    ]

    @staticmethod
    def _partial_plan(num_phases: int) -> str:
        phases = tuple(
            _typed_phase(
                f"phase_{i + 1:03d}", i + 1, f"P{i}", done=True
            )
            for i in range(num_phases)
        )
        return _typed_plan("MyApp", phases)

    def test_exits_75_on_partial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_state(
            tmp_path,
            roadmap=self._ROADMAP_4,
            plan_text=self._partial_plan(2),
        )
        monkeypatch.chdir(tmp_path)
        _resume_patches(monkeypatch)

        call_count = {"n": 0}

        def boom(*args, **kwargs):
            call_count["n"] += 1
            raise ClaudeCliError("claude CLI timed out after 600 seconds")

        monkeypatch.setattr(pipeline, "generate_phase_plan", boom)

        with pytest.raises(SystemExit) as exc_info:
            pipeline._subsequent_run()
        assert exc_info.value.code == 75

        # Only the first resumed phase (index 2) was attempted before the
        # ClaudeCliError stopped the loop.
        assert call_count["n"] == 1

        # PLAN.md on disk still has exactly the two phase headers it
        # started with — the failing generate did not write.
        plan_after = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert pipeline._observed_phase_count(plan_after) == 2

    def test_completes_when_no_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_state(
            tmp_path,
            roadmap=self._ROADMAP_3,
            plan_text=self._partial_plan(1),
        )
        monkeypatch.chdir(tmp_path)
        _resume_patches(monkeypatch)

        def fake_generate(*args, **kwargs):
            """Return a typed :class:`Plan` mirroring what
            :func:`duplo.council.typed_plan_from_synthesizer_text`
            produces for a single fresh phase: one Phase carrying the
            runtime-computed ``phase_NNN`` id and a parser-style
            ``ordinal=1`` (each synthesis is self-contained; final
            on-disk ordinals are owned by the merge/save layer).
            """
            from pathlib import Path as _Path

            from duplo import council

            phase_num = kwargs["phase_number"]
            phase_title = kwargs["phase"].get("title", "")
            required_phase_id = council.compute_required_phase_id(
                _Path("PLAN.md")
            )
            phase = _typed_phase(required_phase_id, 1, phase_title)
            # phase_num is unused for the typed contract; the runtime
            # owns ordinal numbering. The reference is retained so
            # the test still asserts the loop iterated each roadmap
            # entry.
            del phase_num
            return Plan(
                magic_version=1,
                project_title="",
                preamble="",
                phases=(phase,),
                bugs=None,
                source_path=None,
            )

        monkeypatch.setattr(pipeline, "generate_phase_plan", fake_generate)

        # No SystemExit on success.
        pipeline._subsequent_run()

        plan_after = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert pipeline._observed_phase_count(plan_after) == 3
