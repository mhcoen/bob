"""Tests for duplo.roadmap."""

from __future__ import annotations

from unittest.mock import patch

from duplo.extractor import Feature
from duplo.questioner import BuildPreferences
from duplo.roadmap import _parse_roadmap, format_roadmap, generate_roadmap


def _sample_features() -> list[Feature]:
    return [
        Feature(name="Arithmetic", description="Basic math.", category="core"),
        Feature(name="Variables", description="Named values.", category="core"),
    ]


def _sample_prefs() -> BuildPreferences:
    return BuildPreferences(
        platform="desktop",
        language="Swift/SwiftUI",
        constraints=["macOS 14+"],
        preferences=["No external deps"],
    )


_SAMPLE_ROADMAP = '[{"phase": 0, "title": "Scaffold", "goal": "Empty window", "features": [], "test": "Window opens"}]'


class TestParseRoadmap:
    def test_parses_valid_json(self):
        roadmap = _parse_roadmap(_SAMPLE_ROADMAP)
        assert len(roadmap) == 1
        assert roadmap[0]["title"] == "Scaffold"

    def test_strips_fences(self):
        raw = f"```json\n{_SAMPLE_ROADMAP}\n```"
        roadmap = _parse_roadmap(raw)
        assert len(roadmap) == 1

    def test_returns_empty_on_bad_json(self):
        assert _parse_roadmap("not json") == []

    def test_returns_empty_on_non_array(self):
        assert _parse_roadmap('{"phase": 0}') == []


class TestFormatRoadmap:
    def test_formats_phase(self):
        roadmap = [
            {"phase": 0, "title": "Scaffold", "goal": "Window", "test": "Opens", "features": []}
        ]
        text = format_roadmap(roadmap)
        assert "Phase 0: Scaffold" in text
        assert "Window" in text


class TestGenerateRoadmap:
    def test_returns_roadmap_list(self):
        with patch("duplo.roadmap.query", return_value=_SAMPLE_ROADMAP):
            result = generate_roadmap(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        assert isinstance(result, list)
        assert len(result) == 1

    def test_passes_features_to_prompt(self):
        with patch("duplo.roadmap.query", return_value=_SAMPLE_ROADMAP) as mock_query:
            generate_roadmap(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "Arithmetic" in prompt
        assert "Variables" in prompt

    def test_passes_preferences_to_prompt(self):
        with patch("duplo.roadmap.query", return_value=_SAMPLE_ROADMAP) as mock_query:
            generate_roadmap(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
            )
        prompt = mock_query.call_args[0][0]
        assert "Swift/SwiftUI" in prompt
        assert "macOS 14+" in prompt

    def test_spec_text_injected_into_prompt(self):
        with patch("duplo.roadmap.query", return_value=_SAMPLE_ROADMAP) as mock_query:
            generate_roadmap(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                spec_text="Build a macOS calculator.",
            )
        prompt = mock_query.call_args[0][0]
        assert "Build a macOS calculator." in prompt
        assert "authoritative" in prompt.lower()

    def test_spec_text_empty_not_in_prompt(self):
        with patch("duplo.roadmap.query", return_value=_SAMPLE_ROADMAP) as mock_query:
            generate_roadmap(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                spec_text="",
            )
        prompt = mock_query.call_args[0][0]
        assert "Product specification" not in prompt

    def test_completion_history_in_prompt(self):
        history = [{"phase": "Phase 1", "features": ["Arithmetic"]}]
        with patch("duplo.roadmap.query", return_value=_SAMPLE_ROADMAP) as mock_query:
            generate_roadmap(
                "https://example.com",
                _sample_features(),
                _sample_prefs(),
                completion_history=history,
            )
        prompt = mock_query.call_args[0][0]
        assert "Arithmetic" in prompt
        assert "completed" in prompt.lower()
