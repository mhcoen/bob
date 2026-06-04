"""Integration coverage for phase ids through generation + save."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from duplo import pipeline
from duplo.questioner import BuildPreferences


def _repo_scratch_project() -> Path:
    root = Path(__file__).resolve().parents[1]
    return root / ".scratch" / "tests" / f"phase-id-generation-{uuid.uuid4().hex}"


def test_generation_loop_persists_unique_sequential_phase_ids(monkeypatch) -> None:
    project_dir = _repo_scratch_project()
    shutil.rmtree(project_dir, ignore_errors=True)
    project_dir.mkdir(parents=True)
    monkeypatch.chdir(project_dir)

    roadmap = [
        {"phase": 0, "title": "Scaffold", "goal": "start", "features": []},
        {"phase": 1, "title": "Core", "goal": "build", "features": []},
        {"phase": 2, "title": "Polish", "goal": "finish", "features": []},
    ]

    def duplicate_phase_body(*_args, phase: dict, **_kwargs) -> str:
        title = str(phase["title"])
        return f"## Phase phase_001: {title}\n\n- [ ] Build {title.lower()}\n"

    monkeypatch.setattr(pipeline, "generate_phase_plan", duplicate_phase_body)
    monkeypatch.setattr(pipeline, "_git_commit_artifact", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "load_frame_descriptions", lambda: {})

    try:
        saved, total = pipeline._run_phase_generation_loop(
            roadmap=roadmap,
            start_idx=0,
            phases_completed=0,
            source_url="",
            features=[],
            preferences=[
                BuildPreferences(
                    platform="cli",
                    language="Python",
                    constraints=[],
                    preferences=[],
                )
            ],
            spec=None,
            spec_prompt="",
            platform_addendum="",
            prior_phases_files=[],
            project_name="Phase Id App",
        )

        assert (saved, total) == (3, 3)
        plan_text = (project_dir / "PLAN.md").read_text(encoding="utf-8")
        assert plan_text.count("<!-- phase_id:") == 3
        assert "<!-- phase_id: phase_001 -->" in plan_text
        assert "<!-- phase_id: phase_002 -->" in plan_text
        assert "<!-- phase_id: phase_003 -->" in plan_text
    finally:
        monkeypatch.chdir(Path(__file__).resolve().parents[1])
        shutil.rmtree(project_dir, ignore_errors=True)
