"""Tests for the partial-PLAN.md resume path in pipeline._subsequent_run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from duplo import pipeline
from duplo.claude_cli import ClaudeCliError
from duplo.hasher import HashDiff


def _envelope(project: str, phase_num: int, title: str, body: str = "") -> str:
    """Render a canonical-envelope phase plan that save_plan will accept."""
    body = body or "- [ ] do work\n"
    return f"# {project} — Phase {phase_num}: {title}\n\n{body}"


class TestObservedPhaseCount:
    """Pure unit tests for pipeline._observed_phase_count."""

    def test_counts_only_canonical_envelopes(self) -> None:
        text = (
            "# MyApp\n"
            "\n"
            "Intro prose.\n"
            "\n"
            "# MyApp — Phase 0: Scaffold\n"
            "- [ ] task\n"
            "\n"
            "# MyApp — Phase 1: Features\n"
            "- [ ] another\n"
            "\n"
            "# Background: Phase 99 was a long time ago\n"
        )
        assert pipeline._observed_phase_count(text) == 2

    def test_zero_on_header_only(self) -> None:
        text = (
            "# MyApp\n"
            "\n"
            "Just a project header and some prose, no phase envelopes here.\n"
            "\n"
            "# Background: Phase 1 introduced filtering\n"
        )
        assert pipeline._observed_phase_count(text) == 0


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

    _PROJECT_HEADER = "# MyApp\n\nProject header.\n"
    _PARTIAL_PLAN_2 = (
        _PROJECT_HEADER
        + "\n"
        + _envelope("MyApp", 0, "P0", "- [x] scaffold\n")
        + "\n"
        + _envelope("MyApp", 1, "P1", "- [x] core\n")
    )
    _PARTIAL_PLAN_1 = (
        _PROJECT_HEADER
        + "\n"
        + _envelope("MyApp", 0, "P0", "- [x] scaffold\n")
    )

    def test_exits_75_on_partial(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_state(
            tmp_path,
            roadmap=self._ROADMAP_4,
            plan_text=self._PARTIAL_PLAN_2,
        )
        monkeypatch.chdir(tmp_path)
        _resume_patches(monkeypatch)

        call_count = {"n": 0}

        def boom(*args, **kwargs) -> str:
            call_count["n"] += 1
            raise ClaudeCliError("claude CLI timed out after 600 seconds")

        monkeypatch.setattr(pipeline, "generate_phase_plan", boom)

        with pytest.raises(SystemExit) as exc_info:
            pipeline._subsequent_run()
        assert exc_info.value.code == 75

        # Only the first resumed phase (index 2) was attempted before the
        # ClaudeCliError stopped the loop.
        assert call_count["n"] == 1

        # PLAN.md on disk still has exactly the two envelopes it started
        # with — the failing generate did not write.
        plan_after = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert pipeline._observed_phase_count(plan_after) == 2

    def test_completes_when_no_failures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_state(
            tmp_path,
            roadmap=self._ROADMAP_3,
            plan_text=self._PARTIAL_PLAN_1,
        )
        monkeypatch.chdir(tmp_path)
        _resume_patches(monkeypatch)

        def fake_generate(*args, **kwargs) -> str:
            phase_num = kwargs["phase_number"]
            phase_title = kwargs["phase"].get("title", "")
            return _envelope("MyApp", phase_num, phase_title)

        monkeypatch.setattr(pipeline, "generate_phase_plan", fake_generate)

        # No SystemExit on success.
        pipeline._subsequent_run()

        plan_after = (tmp_path / "PLAN.md").read_text(encoding="utf-8")
        assert pipeline._observed_phase_count(plan_after) == 3
