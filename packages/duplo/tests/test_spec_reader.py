"""Tests for duplo.spec_reader."""

from __future__ import annotations

import json
from pathlib import Path

from duplo.spec_reader import (
    BehaviorContract,
    ProductSpec,
    _parse_contracts,
    _parse_spec,
    _split_sections,
    format_contracts_as_verification,
    format_scope_override_prompt,
    format_spec_for_prompt,
    read_spec,
)


class TestReadSpec:
    def test_returns_none_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert read_spec() is None

    def test_returns_none_when_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "SPEC.md").write_text("")
        assert read_spec() is None

    def test_returns_none_when_whitespace_only(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "SPEC.md").write_text("   \n\n  \n")
        assert read_spec() is None

    def test_reads_simple_spec(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "SPEC.md").write_text("A text calculator for macOS.")
        spec = read_spec()
        assert spec is not None
        assert "text calculator" in spec.raw

    def test_reads_from_target_dir(self, tmp_path):
        (tmp_path / "SPEC.md").write_text("Build a widget.")
        spec = read_spec(target_dir=tmp_path)
        assert spec is not None
        assert "widget" in spec.raw


class TestSplitSections:
    def test_no_headings(self):
        sections = _split_sections("Just some text.\nMore text.")
        assert "" in sections
        assert "Just some text." in sections[""]

    def test_single_heading(self):
        sections = _split_sections("## Purpose\nA calculator app.")
        assert "Purpose" in sections
        assert "calculator" in sections["Purpose"]

    def test_multiple_headings(self):
        text = "## Purpose\nA calc.\n## Scope\nInclude math.\n## Design\nDark theme."
        sections = _split_sections(text)
        assert "Purpose" in sections
        assert "Scope" in sections
        assert "Design" in sections
        assert "calc" in sections["Purpose"]
        assert "math" in sections["Scope"]
        assert "Dark" in sections["Design"]

    def test_h1_and_h3_headings(self):
        text = "# Main\nIntro.\n### Detail\nBody."
        sections = _split_sections(text)
        assert "Main" in sections
        assert "Detail" in sections

    def test_preamble_before_first_heading(self):
        text = "Preamble text.\n## Purpose\nDetails."
        sections = _split_sections(text)
        assert "Preamble" in sections[""]
        assert "Details" in sections["Purpose"]


class TestParseSpec:
    def test_purpose(self):
        spec = _parse_spec("## Purpose\nA macOS text calculator.")
        assert "macOS text calculator" in spec.purpose

    def test_scope_include_exclude(self):
        text = (
            "## Scope\n"
            "- include: currency conversion, unit conversion\n"
            "- exclude: JavaScript plugin API, CLI tool\n"
        )
        spec = _parse_spec(text)
        assert "currency conversion" in spec.scope_include
        assert "unit conversion" in spec.scope_include
        assert "JavaScript plugin API" in spec.scope_exclude
        assert "CLI tool" in spec.scope_exclude

    def test_scope_alternative_keywords(self):
        text = (
            "## Scope\n"
            "- want: dark mode\n"
            "- skip: telemetry\n"
            "- add: undo support\n"
            "- omit: analytics\n"
        )
        spec = _parse_spec(text)
        assert "dark mode" in spec.scope_include
        assert "undo support" in spec.scope_include
        assert "telemetry" in spec.scope_exclude
        assert "analytics" in spec.scope_exclude

    def test_behavior_contracts(self):
        text = (
            "## Behavior\n"
            "- `2+3` → `5`\n"
            "- `Price: $7 × 4` should produce `$28`\n"
            "- `5 km in miles` expect `3.11 mi`\n"
        )
        spec = _parse_spec(text)
        assert len(spec.behavior_contracts) == 3
        assert spec.behavior_contracts[0].input == "2+3"
        assert spec.behavior_contracts[0].expected == "5"
        assert spec.behavior_contracts[1].input == "Price: $7 × 4"
        assert spec.behavior_contracts[1].expected == "$28"
        assert spec.behavior_contracts[2].input == "5 km in miles"
        assert spec.behavior_contracts[2].expected == "3.11 mi"

    def test_behavior_arrow_variants(self):
        text = (
            "## Behavior\n"
            "- `a` -> `1`\n"
            "- `b` => `2`\n"
            "- `c` → `3`\n"
            "- `d` should be `4`\n"
            "- `e` should show `5`\n"
            "- `f` should return `6`\n"
        )
        spec = _parse_spec(text)
        assert len(spec.behavior_contracts) == 6
        for i, expected in enumerate(["1", "2", "3", "4", "5", "6"]):
            assert spec.behavior_contracts[i].expected == expected

    def test_british_spelling(self):
        spec = _parse_spec("## Behaviour\n- `1+1` → `2`\n")
        assert len(spec.behavior_contracts) == 1

    def test_architecture(self):
        spec = _parse_spec("## Architecture\nSwiftUI, MVVM, no external deps.")
        assert "SwiftUI" in spec.architecture
        assert "MVVM" in spec.architecture

    def test_design(self):
        spec = _parse_spec("## Design\nDark theme, monospace font, #2b2b2b background.")
        assert "#2b2b2b" in spec.design

    def test_references(self):
        spec = _parse_spec(
            "## References\n"
            "- numi.app demo video is the visual ground truth\n"
            "- github wiki is the behavioral ground truth\n"
        )
        assert "visual ground truth" in spec.references

    def test_raw_always_present(self):
        text = "Just free-form guidance, no headings."
        spec = _parse_spec(text)
        assert spec.raw == text

    def test_unrecognised_headings_preserved_in_raw(self):
        text = "## Custom Section\nSome custom guidance."
        spec = _parse_spec(text)
        assert "Custom Section" in spec.raw
        assert "custom guidance" in spec.raw

    def test_full_spec(self):
        text = (
            "# Numi Clone\n\n"
            "## Purpose\n"
            "A macOS text calculator inspired by numi.app.\n\n"
            "## Scope\n"
            "- include: arithmetic, unit conversion, variables\n"
            "- exclude: JavaScript plugin API, Alfred integration\n\n"
            "## Behavior\n"
            "- `2+3` → `5`\n"
            "- `Price: $7 × 4` → `$28`\n\n"
            "## Architecture\n"
            "Swift, SwiftUI, SPM, macOS 14+. No external dependencies.\n\n"
            "## Design\n"
            "Dark theme (#2b2b2b), monospace font, yellow-green results.\n\n"
            "## References\n"
            "- The demo video at numi.app is the visual ground truth.\n"
        )
        spec = _parse_spec(text)
        assert "macOS text calculator" in spec.purpose
        assert "arithmetic" in spec.scope_include
        assert "JavaScript plugin API" in spec.scope_exclude
        assert len(spec.behavior_contracts) == 2
        assert "SwiftUI" in spec.architecture
        assert "#2b2b2b" in spec.design
        assert "visual ground truth" in spec.references


class TestParseContracts:
    def test_empty_text(self):
        assert _parse_contracts("") == []

    def test_no_backtick_pairs(self):
        assert _parse_contracts("just some text without code") == []

    def test_single_contract(self):
        contracts = _parse_contracts("`2+3` → `5`")
        assert len(contracts) == 1
        assert contracts[0].input == "2+3"
        assert contracts[0].expected == "5"

    def test_multiple_contracts(self):
        text = "- `a` → `1`\n- `b` → `2`\n"
        contracts = _parse_contracts(text)
        assert len(contracts) == 2


class TestFormatSpecForPrompt:
    def test_wraps_raw_text(self):
        spec = ProductSpec(raw="Build a calculator.")
        result = format_spec_for_prompt(spec)
        assert "PRODUCT SPECIFICATION" in result
        assert "authoritative" in result
        assert "Build a calculator." in result


class TestFormatScopeOverridePrompt:
    def test_empty_when_no_overrides(self):
        spec = ProductSpec()
        assert format_scope_override_prompt(spec) == ""

    def test_include_only(self):
        spec = ProductSpec(scope_include=["variables", "percentages"])
        result = format_scope_override_prompt(spec)
        assert "REQUIRES" in result
        assert "variables" in result
        assert "percentages" in result

    def test_exclude_only(self):
        spec = ProductSpec(scope_exclude=["CLI tool"])
        result = format_scope_override_prompt(spec)
        assert "EXCLUDED" in result
        assert "CLI tool" in result

    def test_both(self):
        spec = ProductSpec(
            scope_include=["math"],
            scope_exclude=["plugins"],
        )
        result = format_scope_override_prompt(spec)
        assert "REQUIRES" in result
        assert "EXCLUDED" in result


class TestFormatContractsAsVerification:
    def test_empty_when_no_contracts(self):
        spec = ProductSpec()
        assert format_contracts_as_verification(spec) == ""

    def test_generates_tasks(self):
        spec = ProductSpec(
            behavior_contracts=[
                BehaviorContract(input="2+3", expected="5"),
                BehaviorContract(input="10 km in miles", expected="6.21 mi"),
            ]
        )
        result = format_contracts_as_verification(spec)
        assert "## Functional verification from product spec" in result
        assert "- [ ] Verify: type `2+3`, expect result `5`" in result
        assert "- [ ] Verify: type `10 km in miles`, expect result `6.21 mi`" in result
        assert "SPEC.md" in result
