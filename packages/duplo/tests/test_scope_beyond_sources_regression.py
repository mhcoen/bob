"""Regression: scope beyond sources is never dropped.

A ``## Scope`` include item that is mentioned in no scraped Source must
still survive the whole pipeline: it has to become a feature record
(extractor) *and* be allocated to a build phase that builds it
(roadmap). This ties together the two reconciliation paths so the
guarantee cannot regress in either module independently.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from duplo.extractor import extract_features
from duplo.questioner import BuildPreferences
from duplo.roadmap import generate_roadmap
from duplo.spec_reader import read_spec

# A SPEC whose Scope include list names a feature ("Offline mode") that
# appears in no scraped Source. The scraped product text below mentions
# only arithmetic, so the LLM has no reason to surface offline support.
_SPEC_MD = """\
# Product

## Purpose

A calculator.

## Scope

include:
  - Offline mode
"""

# Scraped Source text deliberately omits any mention of offline mode.
_SCRAPED = "The product evaluates arithmetic expressions and shows the result."

# What the LLM would return: extraction and roadmap both drop the
# unmentioned include item, exactly as in the original bug.
_LLM_FEATURES = json.dumps(
    [{"name": "Arithmetic", "description": "Evaluates expressions.", "category": "core"}]
)
_LLM_ROADMAP = json.dumps(
    [
        {"phase": 0, "title": "Scaffold", "goal": "Window", "features": [], "test": "Opens"},
        {
            "phase": 1,
            "title": "Core",
            "goal": "Arithmetic",
            "features": ["Arithmetic"],
            "test": "1+1=2",
        },
    ]
)


def _prefs() -> BuildPreferences:
    return BuildPreferences(
        platform="desktop",
        language="Swift/SwiftUI",
        constraints=["macOS 14+"],
        preferences=["No external deps"],
    )


def test_scope_beyond_sources_yields_feature_and_build_phase(tmp_path):
    (tmp_path / "SPEC.md").write_text(_SPEC_MD, encoding="utf-8")
    spec = read_spec(target_dir=tmp_path)

    assert spec is not None
    # Sanity: the include item is present in scope but absent from sources.
    assert spec.scope_include == ["Offline mode"]
    assert "offline" not in _SCRAPED.lower()

    # 1) The include item must surface as a feature record even though no
    #    scraped Source mentions it and the LLM did not extract it.
    with patch("duplo.extractor.query", return_value=_LLM_FEATURES):
        features = extract_features(_SCRAPED, scope_include=spec.scope_include)
    assert any(f.name == "Offline mode" for f in features)

    # 2) The same item must be allocated to a real build phase (not the
    #    scaffold phase 0, which generates no feature tasks), even though
    #    the LLM roadmap omitted it.
    with patch("duplo.roadmap.query", return_value=_LLM_ROADMAP):
        roadmap = generate_roadmap(
            _SCRAPED,
            features,
            _prefs(),
            scope_include=spec.scope_include,
        )
    built = {
        name for phase in roadmap if phase.get("phase") != 0 for name in phase.get("features", [])
    }
    assert "Offline mode" in built
