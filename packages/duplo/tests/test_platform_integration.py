"""End-to-end integration test for the platform knowledge flow.

Starts from a SPEC.md that declares a SwiftUI architecture and runs
duplo's CLI entry point against a pre-populated ``.duplo/duplo.json``.
The run must thread a single declaration all the way through to:

- resolver:  ``macos-swiftui-spm`` profile is selected
- planner:   the system prompt addendum contains the profile's rules
- scaffold:  ``run.sh`` is written to disk
- CLAUDE.md: the platform rules section is present
- gitignore: the profile's entries (``.build/`` and ``*.app/``) are added

Real LLM calls are avoided by priming ``.duplo/duplo.json`` with the
cached preferences and the architecture hash that matches the SPEC's
``## Architecture`` body, so ``_load_preferences`` uses the cache.
Only ``generate_phase_plan`` is mocked, which lets the test inspect
the ``platform_addendum`` argument the planner would have received.
"""

from __future__ import annotations

import dataclasses
import json
from unittest.mock import patch

from duplo.build_prefs import architecture_hash
from duplo.main import main
from duplo.platforms.resolver import resolve_profiles
from duplo.questioner import BuildPreferences


_SPEC_ARCHITECTURE = "Swift 5.9, SwiftUI, Swift Package Manager. Target macOS 14+."

_SPEC_MD = (
    "# Integration Test App\n"
    "\n"
    "## Purpose\n"
    "\n"
    "A tiny SwiftUI calculator app used to exercise the platform "
    "knowledge pipeline end to end.\n"
    "\n"
    "## Architecture\n"
    "\n"
    f"{_SPEC_ARCHITECTURE}\n"
)

_SWIFTUI_PREFS = BuildPreferences(
    platform="macos",
    language="swift/swiftui",
    constraints=[],
    preferences=["build: Swift Package Manager"],
)


def _write_fixture(tmp_path) -> None:
    """Drop SPEC.md and ``.duplo/duplo.json`` into *tmp_path*.

    The duplo.json preferences and architecture_hash are pre-populated
    so ``_load_preferences`` returns the cached BuildPreferences without
    invoking the LLM extractor.  The roadmap has a single phase whose
    features list is empty so no user selection is required.
    """
    (tmp_path / "SPEC.md").write_text(_SPEC_MD, encoding="utf-8")

    duplo_dir = tmp_path / ".duplo"
    duplo_dir.mkdir()

    data = {
        "source_url": "",
        "features": [],
        "preferences": [dataclasses.asdict(_SWIFTUI_PREFS)],
        "architecture_hash": architecture_hash(_SPEC_ARCHITECTURE),
        "roadmap": [
            {
                "phase": 0,
                "title": "Core",
                "goal": "Minimal window",
                "features": [],
                "test": "Window opens",
            },
        ],
        "current_phase": 0,
    }
    (duplo_dir / "duplo.json").write_text(json.dumps(data), encoding="utf-8")


class TestPlatformKnowledgeFlow:
    def test_resolve_profiles_selects_swiftui_spm(self):
        """The SwiftUI/SPM preferences resolve to the swiftui_spm profile."""
        profiles = resolve_profiles(_SWIFTUI_PREFS)
        assert profiles, "no profile matched SwiftUI/SPM preferences"
        assert profiles[0].id == "macos-swiftui-spm"

    def test_full_pipeline_threads_platform_knowledge(self, tmp_path, monkeypatch, capsys):
        """Run main() against a SwiftUI SPEC and verify every downstream artifact.

        Asserts, in one pass:

        - ``generate_phase_plan`` is called with a ``platform_addendum``
          that contains the swiftui_spm planner rules and a reference
          to the scaffolded ``run.sh``.
        - ``run.sh`` exists on disk in the project root.
        - ``CLAUDE.md`` contains the platform rules heading and at
          least one of the profile's CLAUDE.md rules.
        - ``.gitignore`` contains ``.build/`` and ``*.app/``.
        """
        _write_fixture(tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("sys.argv", ["duplo"])
        monkeypatch.setattr("duplo.main._check_migration", lambda target_dir: None)

        with patch(
            "duplo.pipeline.generate_phase_plan",
            return_value="# Integration Test App - Phase 1: Core\n- [ ] task\n",
        ) as mock_gen:
            main()

        # Drain captured output so it does not bleed into other tests.
        capsys.readouterr()

        mock_gen.assert_called_once()
        addendum = mock_gen.call_args.kwargs["platform_addendum"]
        assert "macOS + SwiftUI + Swift Package Manager" in addendum, (
            "planner addendum missing swiftui_spm display name"
        )
        assert "./run.sh" in addendum, "planner addendum missing the run.sh usage rule"
        assert "run.sh" in addendum and "MUST NOT be recreated" in addendum, (
            "planner addendum missing the scaffold notice"
        )

        run_sh = tmp_path / "run.sh"
        assert run_sh.is_file(), "scaffold write_scaffold did not create run.sh"

        claude_md = tmp_path / "CLAUDE.md"
        assert claude_md.is_file(), "write_claude_md did not create CLAUDE.md"
        claude_text = claude_md.read_text(encoding="utf-8")
        assert "## Platform rules" in claude_text
        assert "macOS + SwiftUI + Swift Package Manager" in claude_text
        # At least one rule body — NEVER run .build/debug/<name> directly.
        assert ".build/debug" in claude_text

        gitignore = tmp_path / ".gitignore"
        assert gitignore.is_file(), "scaffold did not write .gitignore"
        gitignore_lines = gitignore.read_text(encoding="utf-8").splitlines()
        assert ".build/" in gitignore_lines
        assert "*.app/" in gitignore_lines
