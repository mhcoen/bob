"""Tests for duplo.spec_reader."""

from __future__ import annotations


from pathlib import Path

import json

from duplo.spec_reader import (
    BehaviorContract,
    DesignBlock,
    ProductSpec,
    ReferenceEntry,
    SourceEntry,
    _FIELD_LINE,
    _AUTOGEN_RE,
    _FILL_IN_RE,
    _HTML_COMMENT_RE,
    _KNOWN_SECTIONS,
    _REFERENCE_ENTRY_START_BARE,
    _REFERENCE_ENTRY_START_QUOTED,
    _SOURCE_ENTRY_START,
    _VALID_REFERENCE_ROLES,
    _VALID_SCRAPE_VALUES,
    _VALID_SOURCE_ROLES,
    _parse_contracts,
    _parse_design_block,
    _parse_reference_entries,
    _parse_source_entries,
    _parse_spec,
    _split_sections,
    _strip_comments,
    _validate_reference_entries,
    _validate_source_entries,
    format_behavioral_references,
    format_contracts_as_verification,
    format_counter_examples,
    format_design_for_prompt,
    format_doc_references,
    format_scope_override_prompt,
    format_spec_for_prompt,
    format_visual_references,
    read_spec,
    scrapeable_sources,
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
        assert "#2b2b2b" in spec.design.user_prose

    def test_design_is_design_block(self):
        spec = _parse_spec(
            "## Design\n"
            "User notes here.\n\n"
            "<!-- BEGIN AUTO-GENERATED design-requirements -->\n"
            "Generated content.\n"
            "<!-- END AUTO-GENERATED -->\n"
        )
        assert spec.design.user_prose == "User notes here."
        assert spec.design.auto_generated == "Generated content."
        assert spec.design.has_fill_in_marker is False

    def test_design_default_is_empty_design_block(self):
        spec = _parse_spec("## Purpose\nJust purpose, no design.")
        assert spec.design.user_prose == ""
        assert spec.design.auto_generated == ""
        assert spec.design.has_fill_in_marker is False

    def test_references_structured(self):
        spec = _parse_spec("## References\n- ref/demo.mp4\n  role: visual-target\n")
        assert len(spec.references) == 1
        assert spec.references[0].roles == ["visual-target"]

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
        assert "#2b2b2b" in spec.design.user_prose
        assert spec.references == []


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
    def test_includes_purpose(self):
        spec = ProductSpec(purpose="Build a calculator.")
        result = format_spec_for_prompt(spec)
        assert "PRODUCT SPECIFICATION" in result
        assert "authoritative" in result
        assert "## Purpose" in result
        assert "Build a calculator." in result

    def test_empty_spec_returns_empty(self):
        spec = ProductSpec()
        assert format_spec_for_prompt(spec) == ""

    def test_includes_all_simple_sections(self):
        spec = ProductSpec(
            purpose="A calculator",
            scope="Desktop only",
            behavior="`1+1` → `2`",
            architecture="Python + Qt",
            notes="Ship by Friday",
        )
        result = format_spec_for_prompt(spec)
        assert "## Purpose" in result
        assert "## Scope" in result
        assert "## Behavior" in result
        assert "## Architecture" in result
        assert "## Notes" in result
        assert "A calculator" in result
        assert "Desktop only" in result
        assert "Python + Qt" in result
        assert "Ship by Friday" in result

    def test_includes_design(self):
        spec = ProductSpec(
            design=DesignBlock(user_prose="Dark theme", auto_generated="colors: #000"),
        )
        result = format_spec_for_prompt(spec)
        assert "## Design" in result
        assert "Dark theme" in result
        assert "colors: #000" in result

    def test_includes_safe_sources(self):
        spec = ProductSpec(
            sources=[
                SourceEntry(
                    url="https://example.com",
                    role="product-reference",
                    scrape="deep",
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "## Sources" in result
        assert "https://example.com" in result

    def test_excludes_proposed_sources(self):
        spec = ProductSpec(
            purpose="Test",
            sources=[
                SourceEntry(
                    url="https://proposed.example.com",
                    role="docs",
                    scrape="deep",
                    proposed=True,
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "https://proposed.example.com" not in result

    def test_excludes_discovered_sources(self):
        spec = ProductSpec(
            purpose="Test",
            sources=[
                SourceEntry(
                    url="https://discovered.example.com",
                    role="docs",
                    scrape="shallow",
                    discovered=True,
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "https://discovered.example.com" not in result

    def test_excludes_counter_example_sources(self):
        spec = ProductSpec(
            purpose="Test",
            sources=[
                SourceEntry(
                    url="https://counter.example.com",
                    role="counter-example",
                    scrape="none",
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "https://counter.example.com" not in result

    def test_includes_safe_references(self):
        spec = ProductSpec(
            references=[
                ReferenceEntry(
                    path=Path("ref/screenshot.png"),
                    roles=["visual-target"],
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "## References" in result
        assert "ref/screenshot.png" in result

    def test_excludes_proposed_references(self):
        spec = ProductSpec(
            purpose="Test",
            references=[
                ReferenceEntry(
                    path=Path("ref/proposed.png"),
                    roles=["visual-target"],
                    proposed=True,
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "ref/proposed.png" not in result

    def test_excludes_counter_example_references(self):
        spec = ProductSpec(
            purpose="Test",
            references=[
                ReferenceEntry(
                    path=Path("ref/counter.png"),
                    roles=["counter-example"],
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "ref/counter.png" not in result

    def test_excludes_ignore_references(self):
        spec = ProductSpec(
            purpose="Test",
            references=[
                ReferenceEntry(
                    path=Path("ref/ignored.png"),
                    roles=["ignore"],
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "ref/ignored.png" not in result

    def test_source_scrape_none_omitted(self):
        spec = ProductSpec(
            sources=[
                SourceEntry(
                    url="https://example.com",
                    role="product-reference",
                    scrape="none",
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "scrape:" not in result

    def test_source_notes_included(self):
        spec = ProductSpec(
            sources=[
                SourceEntry(
                    url="https://example.com",
                    role="docs",
                    scrape="none",
                    notes="Main API docs",
                ),
            ],
        )
        result = format_spec_for_prompt(spec)
        assert "Main API docs" in result

    def test_does_not_use_raw(self):
        spec = ProductSpec(
            raw="SECRET RAW TEXT",
            purpose="Safe purpose",
        )
        result = format_spec_for_prompt(spec)
        assert "SECRET RAW TEXT" not in result
        assert "Safe purpose" in result

    def test_prompt_injection_invariant(self):
        """Highest-stakes test: proposed, discovered, and counter-example
        entries must NEVER appear in the prompt output."""
        spec = ProductSpec(
            purpose="A real product",
            sources=[
                SourceEntry(
                    url="https://safe.example.com",
                    role="product-reference",
                    scrape="deep",
                ),
                SourceEntry(
                    url="https://proposed-source.evil.com",
                    role="docs",
                    scrape="deep",
                    notes="PROPOSED_SOURCE_MARKER",
                    proposed=True,
                ),
                SourceEntry(
                    url="https://discovered-source.evil.com",
                    role="docs",
                    scrape="shallow",
                    notes="DISCOVERED_SOURCE_MARKER",
                    discovered=True,
                ),
                SourceEntry(
                    url="https://counter-source.evil.com",
                    role="counter-example",
                    scrape="none",
                    notes="COUNTER_SOURCE_MARKER",
                ),
            ],
            references=[
                ReferenceEntry(
                    path=Path("ref/safe.png"),
                    roles=["visual-target"],
                ),
                ReferenceEntry(
                    path=Path("ref/proposed-ref.png"),
                    roles=["visual-target"],
                    notes="PROPOSED_REF_MARKER",
                    proposed=True,
                ),
                ReferenceEntry(
                    path=Path("ref/counter-ref.png"),
                    roles=["counter-example"],
                    notes="COUNTER_REF_MARKER",
                ),
                ReferenceEntry(
                    path=Path("ref/ignored-ref.png"),
                    roles=["ignore"],
                    notes="IGNORED_REF_MARKER",
                ),
            ],
        )
        result = format_spec_for_prompt(spec)

        # Safe entries ARE present.
        assert "https://safe.example.com" in result
        assert "ref/safe.png" in result

        # Proposed sources excluded.
        assert "proposed-source.evil.com" not in result
        assert "PROPOSED_SOURCE_MARKER" not in result

        # Discovered sources excluded.
        assert "discovered-source.evil.com" not in result
        assert "DISCOVERED_SOURCE_MARKER" not in result

        # Counter-example sources excluded.
        assert "counter-source.evil.com" not in result
        assert "COUNTER_SOURCE_MARKER" not in result

        # Proposed references excluded.
        assert "proposed-ref.png" not in result
        assert "PROPOSED_REF_MARKER" not in result

        # Counter-example references excluded.
        assert "counter-ref.png" not in result
        assert "COUNTER_REF_MARKER" not in result

        # Ignored references excluded.
        assert "ignored-ref.png" not in result
        assert "IGNORED_REF_MARKER" not in result


class TestFormatSpecVerbatimSections:
    """Verify user-authored sections appear verbatim in prompt output."""

    def test_purpose_verbatim(self):
        text = "Build a calculator.\n\nIt should handle decimals."
        spec = ProductSpec(purpose=text)
        result = format_spec_for_prompt(spec)
        assert f"## Purpose\n\n{text}" in result

    def test_architecture_verbatim(self):
        text = "Python 3.11+\n- Use Qt for GUI\n- SQLite for storage"
        spec = ProductSpec(architecture=text)
        result = format_spec_for_prompt(spec)
        assert f"## Architecture\n\n{text}" in result

    def test_scope_verbatim(self):
        text = "- include: basic arithmetic\n- exclude: graphing"
        spec = ProductSpec(scope=text)
        result = format_spec_for_prompt(spec)
        assert f"## Scope\n\n{text}" in result

    def test_behavior_verbatim(self):
        text = "`2+2` → `4`\n`10/3` → `3.333`"
        spec = ProductSpec(behavior=text)
        result = format_spec_for_prompt(spec)
        assert f"## Behavior\n\n{text}" in result

    def test_notes_verbatim(self):
        text = "Ship by Friday.\nNo external APIs."
        spec = ProductSpec(notes=text)
        result = format_spec_for_prompt(spec)
        assert f"## Notes\n\n{text}" in result

    def test_design_user_prose_verbatim(self):
        prose = "Dark theme with rounded corners.\nUse system font."
        spec = ProductSpec(design=DesignBlock(user_prose=prose))
        result = format_spec_for_prompt(spec)
        assert f"## Design\n\n{prose}" in result

    def test_design_user_prose_first_when_both_present(self):
        prose = "Minimal, clean look"
        auto = "colors: #fff, #000"
        spec = ProductSpec(
            design=DesignBlock(user_prose=prose, auto_generated=auto),
        )
        result = format_spec_for_prompt(spec)
        assert f"## Design\n\n{prose}\n\n---\n\n{auto}" in result

    def test_all_sections_verbatim_order(self):
        spec = ProductSpec(
            purpose="Purpose text",
            scope="Scope text",
            behavior="Behavior text",
            architecture="Architecture text",
            design=DesignBlock(user_prose="Design text"),
            notes="Notes text",
        )
        result = format_spec_for_prompt(spec)
        # All sections present with exact content.
        for heading, text in [
            ("Purpose", "Purpose text"),
            ("Scope", "Scope text"),
            ("Behavior", "Behavior text"),
            ("Architecture", "Architecture text"),
            ("Design", "Design text"),
            ("Notes", "Notes text"),
        ]:
            assert f"## {heading}\n\n{text}" in result
        # Purpose comes before Notes (ordering check).
        assert result.index("## Purpose") < result.index("## Notes")

    def test_multiline_with_special_chars_preserved(self):
        purpose = (
            "Build a **Markdown** parser.\n"
            "\n"
            "Support:\n"
            "- `code blocks`\n"
            "- <html> tags\n"
            '- "quoted strings"'
        )
        spec = ProductSpec(purpose=purpose)
        result = format_spec_for_prompt(spec)
        assert f"## Purpose\n\n{purpose}" in result


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


class TestSourceEntryDefaults:
    def test_required_fields(self):
        entry = SourceEntry(url="https://example.com", role="docs", scrape="deep")
        assert entry.url == "https://example.com"
        assert entry.role == "docs"
        assert entry.scrape == "deep"

    def test_optional_defaults(self):
        entry = SourceEntry(url="https://example.com", role="docs", scrape="none")
        assert entry.notes == ""
        assert entry.proposed is False
        assert entry.discovered is False

    def test_all_fields(self):
        entry = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="shallow",
            notes="main site",
            proposed=True,
            discovered=True,
        )
        assert entry.notes == "main site"
        assert entry.proposed is True
        assert entry.discovered is True


class TestReferenceEntryDefaults:
    def test_required_fields(self):
        entry = ReferenceEntry(path=Path("ref/demo.mp4"))
        assert entry.path == Path("ref/demo.mp4")

    def test_optional_defaults(self):
        entry = ReferenceEntry(path=Path("ref/screenshot.png"))
        assert entry.roles == []
        assert entry.notes == ""
        assert entry.proposed is False

    def test_roles_is_list(self):
        entry = ReferenceEntry(
            path=Path("ref/demo.mp4"),
            roles=["visual-target", "behavioral-target"],
        )
        assert len(entry.roles) == 2
        assert "visual-target" in entry.roles
        assert "behavioral-target" in entry.roles

    def test_all_fields(self):
        entry = ReferenceEntry(
            path=Path("ref/doc.pdf"),
            roles=["docs"],
            notes="API reference",
            proposed=True,
        )
        assert entry.notes == "API reference"
        assert entry.proposed is True


class TestDesignBlockDefaults:
    def test_all_defaults(self):
        block = DesignBlock()
        assert block.user_prose == ""
        assert block.auto_generated == ""
        assert block.has_fill_in_marker is False

    def test_with_values(self):
        block = DesignBlock(
            user_prose="Dark theme",
            auto_generated="colors: #2b2b2b",
            has_fill_in_marker=True,
        )
        assert block.user_prose == "Dark theme"
        assert block.auto_generated == "colors: #2b2b2b"
        assert block.has_fill_in_marker is True


class TestStripComments:
    def test_removes_single_line_comment(self):
        text = "before <!-- comment --> after"
        assert _strip_comments(text) == "before  after"

    def test_removes_multi_line_comment(self):
        text = "before\n<!-- multi\nline\ncomment -->\nafter"
        assert _strip_comments(text) == "before\n\nafter"

    def test_leaves_non_comment_content_intact(self):
        text = "no comments here\njust regular text"
        assert _strip_comments(text) == text

    def test_removes_multiple_comments(self):
        text = "a <!-- x --> b <!-- y --> c"
        assert _strip_comments(text) == "a  b  c"

    def test_empty_comment(self):
        text = "before <!----> after"
        assert _strip_comments(text) == "before  after"

    def test_comment_with_fill_in_marker(self):
        text = "Purpose\n<!-- <FILL IN: describe> -->\nReal content"
        result = _strip_comments(text)
        assert "<FILL IN" not in result
        assert "Real content" in result

    def test_html_comment_re_matches_dotall(self):
        text = "<!-- spans\nmultiple\nlines -->"
        assert _HTML_COMMENT_RE.fullmatch(text) is not None


class TestFillInRegex:
    def test_matches_basic_marker(self):
        assert _FILL_IN_RE.search("<FILL IN>") is not None

    def test_matches_with_hint_text(self):
        assert _FILL_IN_RE.search("<FILL IN: describe your product>") is not None

    def test_matches_extra_whitespace(self):
        assert _FILL_IN_RE.search("<FILL  IN>") is not None

    def test_no_match_without_marker(self):
        assert _FILL_IN_RE.search("just regular text") is None

    def test_no_match_partial(self):
        assert _FILL_IN_RE.search("<FILL>") is None


class TestFillInPurpose:
    def test_marker_sets_flag(self):
        spec = _parse_spec("## Purpose\n<FILL IN: describe>\n")
        assert spec.fill_in_purpose is True

    def test_marker_in_comment_does_not_set_flag(self):
        spec = _parse_spec("## Purpose\n<!-- <FILL IN: describe> -->\nReal purpose.\n")
        assert spec.fill_in_purpose is False

    def test_absent_marker_keeps_flag_false(self):
        spec = _parse_spec("## Purpose\nA macOS text calculator.\n")
        assert spec.fill_in_purpose is False


class TestFillInArchitecture:
    def test_marker_sets_flag(self):
        spec = _parse_spec("## Architecture\n<FILL IN>\n")
        assert spec.fill_in_architecture is True

    def test_marker_in_comment_does_not_set_flag(self):
        spec = _parse_spec("## Architecture\n<!-- <FILL IN> -->\nSwiftUI, MVVM.\n")
        assert spec.fill_in_architecture is False

    def test_absent_marker_keeps_flag_false(self):
        spec = _parse_spec("## Architecture\nSwift, SwiftUI.\n")
        assert spec.fill_in_architecture is False


class TestFillInDesign:
    def test_marker_and_no_visual_target_sets_flag(self):
        text = (
            "## Design\n<FILL IN: describe visual style>\n"
            "## References\n- ref/doc.pdf\n  role: docs\n"
        )
        spec = _parse_spec(text)
        assert spec.fill_in_design is True

    def test_marker_but_visual_target_present_keeps_flag_false(self):
        text = "## Design\n<FILL IN>\n## References\n- ref/demo.mp4\n  role: visual-target\n"
        spec = _parse_spec(text)
        assert spec.fill_in_design is False

    def test_no_marker_keeps_flag_false(self):
        text = "## Design\nDark theme, monospace font.\n"
        spec = _parse_spec(text)
        assert spec.fill_in_design is False

    def test_marker_in_comment_keeps_flag_false(self):
        text = "## Design\n<!-- <FILL IN> -->\nDark theme.\n## References\nNo visual targets.\n"
        spec = _parse_spec(text)
        assert spec.fill_in_design is False

    def test_no_references_section_with_marker_sets_flag(self):
        spec = _parse_spec("## Design\n<FILL IN>\n")
        assert spec.fill_in_design is True


class TestSourceEntryStart:
    def test_matches_http_url(self):
        m = _SOURCE_ENTRY_START.match("- http://example.com")
        assert m is not None
        assert m.group(1) == "http://example.com"

    def test_matches_https_url(self):
        m = _SOURCE_ENTRY_START.match("- https://example.com/docs")
        assert m is not None
        assert m.group(1) == "https://example.com/docs"

    def test_matches_with_trailing_whitespace(self):
        m = _SOURCE_ENTRY_START.match("- https://example.com   ")
        assert m is not None
        assert m.group(1) == "https://example.com"

    def test_no_match_without_dash(self):
        assert _SOURCE_ENTRY_START.match("https://example.com") is None

    def test_no_match_without_url(self):
        assert _SOURCE_ENTRY_START.match("- just some text") is None

    def test_no_match_ftp_url(self):
        assert _SOURCE_ENTRY_START.match("- ftp://example.com") is None

    def test_no_match_indented_line(self):
        assert _SOURCE_ENTRY_START.match("  - https://example.com") is None

    def test_no_match_url_with_trailing_text(self):
        assert _SOURCE_ENTRY_START.match("- https://example.com extra") is None

    def test_matches_url_with_path_and_query(self):
        m = _SOURCE_ENTRY_START.match("- https://example.com/a/b?q=1&r=2#frag")
        assert m is not None
        assert m.group(1) == "https://example.com/a/b?q=1&r=2#frag"


class TestFieldLine:
    def test_matches_two_space_indent(self):
        m = _FIELD_LINE.match("  role: product-reference")
        assert m is not None
        assert m.group(1) == "role"
        assert m.group(2) == "product-reference"

    def test_matches_four_space_indent(self):
        m = _FIELD_LINE.match("    scrape: deep")
        assert m is not None
        assert m.group(1) == "scrape"
        assert m.group(2) == "deep"

    def test_matches_empty_value(self):
        m = _FIELD_LINE.match("  notes:")
        assert m is not None
        assert m.group(1) == "notes"
        assert m.group(2) == ""

    def test_matches_value_with_spaces(self):
        m = _FIELD_LINE.match("  notes: some free text here")
        assert m is not None
        assert m.group(1) == "notes"
        assert m.group(2) == "some free text here"

    def test_matches_boolean_value(self):
        m = _FIELD_LINE.match("  proposed: true")
        assert m is not None
        assert m.group(1) == "proposed"
        assert m.group(2) == "true"

    def test_no_match_unindented(self):
        assert _FIELD_LINE.match("role: product-reference") is None

    def test_no_match_single_space_indent(self):
        assert _FIELD_LINE.match(" role: product-reference") is None

    def test_no_match_list_item(self):
        assert _FIELD_LINE.match("- https://example.com") is None

    def test_no_match_no_colon(self):
        assert _FIELD_LINE.match("  just indented text") is None

    def test_matches_tab_indent(self):
        m = _FIELD_LINE.match("\t\trole: docs")
        assert m is not None  # \s matches tabs too
        assert m.group(1) == "role"

    def test_matches_large_indent(self):
        m = _FIELD_LINE.match("      notes: continuation text")
        assert m is not None
        assert m.group(1) == "notes"
        assert m.group(2) == "continuation text"


class TestParseSourceEntries:
    def test_single_entry_all_fields(self):
        body = (
            "- https://example.com\n"
            "  role: product-reference\n"
            "  scrape: deep\n"
            "  notes: main site\n"
            "  proposed: true\n"
            "  discovered: true\n"
        )
        entries = _parse_source_entries(body)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.url == "https://example.com"
        assert entry.role == "product-reference"
        assert entry.scrape == "deep"
        assert entry.notes == "main site"
        assert entry.proposed is True
        assert entry.discovered is True

    def test_single_entry_minimal_fields(self):
        body = "- https://example.com\n  role: docs\n  scrape: shallow\n"
        entries = _parse_source_entries(body)
        assert len(entries) == 1
        assert entries[0].url == "https://example.com"
        assert entries[0].role == "docs"
        assert entries[0].scrape == "shallow"
        assert entries[0].notes == ""
        assert entries[0].proposed is False
        assert entries[0].discovered is False

    def test_multiple_entries(self):
        body = (
            "- https://example.com\n"
            "  role: product-reference\n"
            "  scrape: deep\n"
            "\n"
            "- https://docs.example.com\n"
            "  role: docs\n"
            "  scrape: shallow\n"
        )
        entries = _parse_source_entries(body)
        assert len(entries) == 2
        assert entries[0].url == "https://example.com"
        assert entries[0].role == "product-reference"
        assert entries[1].url == "https://docs.example.com"
        assert entries[1].role == "docs"

    def test_entries_separated_by_new_entry_no_blank_line(self):
        body = (
            "- https://a.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "- https://b.com\n"
            "  role: docs\n"
            "  scrape: deep\n"
        )
        entries = _parse_source_entries(body)
        assert len(entries) == 2
        assert entries[0].url == "https://a.com"
        assert entries[1].url == "https://b.com"

    def test_multiline_notes(self):
        body = (
            "- https://example.com\n"
            "  role: product-reference\n"
            "  scrape: deep\n"
            "  notes: first line\n"
            "    second line\n"
            "    third line\n"
        )
        entries = _parse_source_entries(body)
        assert len(entries) == 1
        assert entries[0].notes == "first line\nsecond line\nthird line"

    def test_multiline_notes_stops_at_next_field(self):
        body = (
            "- https://example.com\n"
            "  role: docs\n"
            "  notes: line one\n"
            "    line two\n"
            "  scrape: deep\n"
        )
        entries = _parse_source_entries(body)
        assert len(entries) == 1
        assert entries[0].notes == "line one\nline two"
        assert entries[0].scrape == "deep"

    def test_multiline_notes_stops_at_blank_line(self):
        body = (
            "- https://example.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "  notes: first\n"
            "    second\n"
            "\n"
            "- https://other.com\n"
            "  role: docs\n"
            "  scrape: none\n"
        )
        entries = _parse_source_entries(body)
        assert len(entries) == 2
        assert entries[0].notes == "first\nsecond"

    def test_empty_body(self):
        assert _parse_source_entries("") == []

    def test_body_with_no_entries(self):
        body = "Some prose about sources.\nNo actual list items."
        assert _parse_source_entries(body) == []

    def test_missing_optional_fields_default(self):
        body = "- https://example.com\n  role: docs\n  scrape: none\n"
        entries = _parse_source_entries(body)
        assert entries[0].proposed is False
        assert entries[0].discovered is False
        assert entries[0].notes == ""

    def test_proposed_false_string(self):
        body = "- https://example.com\n  role: docs\n  scrape: none\n  proposed: false\n"
        entries = _parse_source_entries(body)
        assert entries[0].proposed is False

    def test_notes_empty_value(self):
        body = "- https://example.com\n  role: docs\n  scrape: none\n  notes:\n"
        entries = _parse_source_entries(body)
        assert entries[0].notes == ""

    def test_notes_continuation_requires_deeper_indent(self):
        body = (
            "- https://example.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "  notes: start\n"
            "  not a continuation\n"
        )
        # "  not a continuation" has same indent as "  notes:", so it's
        # not a continuation.  If it matches _FIELD_LINE it becomes a
        # new field; otherwise the entry ends.  Here "not" has no colon
        # after a word, so it doesn't match _FIELD_LINE and the entry
        # ends.
        entries = _parse_source_entries(body)
        assert len(entries) == 1
        assert entries[0].notes == "start"

    def test_multiline_notes_four_space_field_six_space_continuation(self):
        body = (
            "- https://example.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "    notes: deep start\n"
            "      continued\n"
        )
        entries = _parse_source_entries(body)
        assert entries[0].notes == "deep start\ncontinued"


class TestValidateSourceEntries:
    def _errors_path(self, tmp_path):
        return tmp_path / "errors.jsonl"

    def _read_errors(self, path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_valid_entry_passes(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://example.com",
                role="product-reference",
                scrape="deep",
            )
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 1
        assert result[0].url == "https://example.com"
        assert self._read_errors(ep) == []

    def test_invalid_url_dropped(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [SourceEntry(url="ftp://bad.com", role="docs", scrape="none")]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 0
        errors = self._read_errors(ep)
        assert len(errors) == 1
        assert "invalid URL" in errors[0]["message"]

    def test_empty_url_dropped(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [SourceEntry(url="", role="docs", scrape="none")]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 0

    def test_unknown_role_drops_entry(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://example.com",
                role="doc",
                scrape="deep",
            )
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 0
        errors = self._read_errors(ep)
        assert len(errors) == 1
        assert "unknown role" in errors[0]["message"]
        assert "'doc'" in errors[0]["message"]

    def test_empty_role_drops_entry(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [SourceEntry(url="https://example.com", role="", scrape="deep")]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 0

    def test_unknown_scrape_defaults_to_none(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://example.com",
                role="docs",
                scrape="full",
            )
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 1
        assert result[0].scrape == "none"
        errors = self._read_errors(ep)
        assert len(errors) == 1
        assert "unknown scrape" in errors[0]["message"]

    def test_empty_scrape_defaults_to_none(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [SourceEntry(url="https://example.com", role="docs", scrape="")]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 1
        assert result[0].scrape == "none"

    def test_both_proposed_and_discovered_accepted(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://example.com",
                role="docs",
                scrape="shallow",
                proposed=True,
                discovered=True,
            )
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 1
        assert result[0].proposed is True
        assert result[0].discovered is True
        assert self._read_errors(ep) == []

    def test_mixed_valid_and_invalid(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://good.com",
                role="docs",
                scrape="deep",
            ),
            SourceEntry(
                url="https://bad-role.com",
                role="doc",
                scrape="deep",
            ),
            SourceEntry(
                url="ftp://bad-url.com",
                role="docs",
                scrape="none",
            ),
            SourceEntry(
                url="https://bad-scrape.com",
                role="counter-example",
                scrape="everything",
            ),
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 2
        assert result[0].url == "https://good.com"
        assert result[1].url == "https://bad-scrape.com"
        assert result[1].scrape == "none"

    def test_all_valid_roles_accepted(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(url=f"https://{role}.com", role=role, scrape="none")
            for role in sorted(_VALID_SOURCE_ROLES)
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == len(_VALID_SOURCE_ROLES)

    def test_all_valid_scrape_values_accepted(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(url=f"https://{val}.com", role="docs", scrape=val)
            for val in sorted(_VALID_SCRAPE_VALUES)
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == len(_VALID_SCRAPE_VALUES)
        assert self._read_errors(ep) == []

    def test_counter_example_deep_scrape_overridden_to_none(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://example.com",
                role="counter-example",
                scrape="deep",
            )
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 1
        assert result[0].scrape == "none"
        errors = self._read_errors(ep)
        assert len(errors) == 1
        assert "counter-example" in errors[0]["message"]
        assert "overriding to 'none'" in errors[0]["message"]

    def test_counter_example_shallow_scrape_overridden_to_none(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://example.com",
                role="counter-example",
                scrape="shallow",
            )
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 1
        assert result[0].scrape == "none"
        errors = self._read_errors(ep)
        assert len(errors) == 1
        assert "counter-example" in errors[0]["message"]

    def test_counter_example_none_scrape_no_diagnostic(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://example.com",
                role="counter-example",
                scrape="none",
            )
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 1
        assert result[0].scrape == "none"
        assert self._read_errors(ep) == []

    def test_counter_example_preserves_other_fields(self, tmp_path):
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://example.com",
                role="counter-example",
                scrape="deep",
                notes="some notes",
                proposed=True,
                discovered=True,
            )
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 1
        assert result[0].scrape == "none"
        assert result[0].notes == "some notes"
        assert result[0].proposed is True
        assert result[0].discovered is True
        assert result[0].role == "counter-example"

    def test_non_counter_example_deep_scrape_no_override(self, tmp_path):
        """Only counter-example role triggers the scrape override."""
        ep = self._errors_path(tmp_path)
        entries = [
            SourceEntry(
                url="https://example.com",
                role="docs",
                scrape="deep",
            )
        ]
        result = _validate_source_entries(entries, errors_path=ep)
        assert len(result) == 1
        assert result[0].scrape == "deep"
        assert self._read_errors(ep) == []


class TestParseSourceEntriesValidation:
    """Integration: validation runs inside _parse_source_entries."""

    def test_invalid_role_dropped_during_parse(self, tmp_path):
        ep = tmp_path / "errors.jsonl"
        body = "- https://example.com\n  role: doc\n  scrape: deep\n"
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 0

    def test_unknown_scrape_defaults_during_parse(self, tmp_path):
        ep = tmp_path / "errors.jsonl"
        body = "- https://example.com\n  role: docs\n  scrape: mega\n"
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 1
        assert entries[0].scrape == "none"

    def test_valid_entries_pass_through(self, tmp_path):
        ep = tmp_path / "errors.jsonl"
        body = (
            "- https://a.com\n"
            "  role: product-reference\n"
            "  scrape: deep\n"
            "\n"
            "- https://b.com\n"
            "  role: docs\n"
            "  scrape: shallow\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 2


class TestDiagnosticRecordStructure:
    """Verify diagnostic records have correct site, category, and timestamp."""

    def _read_errors(self, path):
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def test_invalid_url_record_structure(self, tmp_path):
        ep = tmp_path / "errors.jsonl"
        entries = [SourceEntry(url="ftp://bad.com", role="docs", scrape="none")]
        _validate_source_entries(entries, errors_path=ep)
        errors = self._read_errors(ep)
        assert len(errors) == 1
        assert errors[0]["site"] == "spec_reader:_validate_source_entries"
        assert errors[0]["category"] == "io"
        assert "timestamp" in errors[0]

    def test_unknown_role_record_structure(self, tmp_path):
        ep = tmp_path / "errors.jsonl"
        entries = [SourceEntry(url="https://x.com", role="doc", scrape="deep")]
        _validate_source_entries(entries, errors_path=ep)
        errors = self._read_errors(ep)
        assert len(errors) == 1
        assert errors[0]["site"] == "spec_reader:_validate_source_entries"
        assert errors[0]["category"] == "io"
        assert "timestamp" in errors[0]

    def test_unknown_scrape_record_structure(self, tmp_path):
        ep = tmp_path / "errors.jsonl"
        entries = [SourceEntry(url="https://x.com", role="docs", scrape="full")]
        _validate_source_entries(entries, errors_path=ep)
        errors = self._read_errors(ep)
        assert len(errors) == 1
        assert errors[0]["site"] == "spec_reader:_validate_source_entries"
        assert errors[0]["category"] == "io"
        assert "timestamp" in errors[0]

    def test_counter_example_scrape_override_record_structure(self, tmp_path):
        ep = tmp_path / "errors.jsonl"
        entries = [
            SourceEntry(
                url="https://x.com",
                role="counter-example",
                scrape="deep",
            )
        ]
        _validate_source_entries(entries, errors_path=ep)
        errors = self._read_errors(ep)
        assert len(errors) == 1
        assert errors[0]["site"] == "spec_reader:_validate_source_entries"
        assert errors[0]["category"] == "io"
        assert "timestamp" in errors[0]
        assert "counter-example" in errors[0]["message"]

    def test_multiple_failures_each_recorded(self, tmp_path):
        ep = tmp_path / "errors.jsonl"
        entries = [
            SourceEntry(url="ftp://a.com", role="docs", scrape="none"),
            SourceEntry(url="https://b.com", role="bogus", scrape="deep"),
            SourceEntry(url="https://c.com", role="docs", scrape="mega"),
        ]
        _validate_source_entries(entries, errors_path=ep)
        errors = self._read_errors(ep)
        assert len(errors) == 3
        assert "invalid URL" in errors[0]["message"]
        assert "unknown role" in errors[1]["message"]
        assert "unknown scrape" in errors[2]["message"]

    def test_no_diagnostic_for_valid_entries(self, tmp_path):
        ep = tmp_path / "errors.jsonl"
        entries = [
            SourceEntry(
                url="https://example.com",
                role="product-reference",
                scrape="deep",
            )
        ]
        _validate_source_entries(entries, errors_path=ep)
        assert not ep.exists()

    def test_parse_source_entries_emits_diagnostics(self, tmp_path):
        """Integration: diagnostics emitted through the parse path."""
        ep = tmp_path / "errors.jsonl"
        body = (
            "- https://good.com\n"
            "  role: docs\n"
            "  scrape: deep\n"
            "\n"
            "- https://bad-role.com\n"
            "  role: doc\n"
            "  scrape: deep\n"
            "\n"
            "- https://bad-scrape.com\n"
            "  role: counter-example\n"
            "  scrape: mega\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 2
        errors = self._read_errors(ep)
        assert len(errors) == 2
        assert "unknown role" in errors[0]["message"]
        assert "unknown scrape" in errors[1]["message"]


class TestParseSourceEntriesCommentStripped:
    """Entries inside HTML comments must not be parsed as real entries.

    The caller is expected to strip comments before passing body to
    ``_parse_source_entries``.  These tests verify that the strip +
    parse pipeline correctly ignores commented-out entries.
    """

    def test_commented_entry_ignored(self, tmp_path):
        body = (
            "- https://real.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "\n"
            "<!-- - https://commented-out.com\n"
            "  role: product-reference\n"
            "  scrape: deep -->\n"
        )
        entries = _parse_source_entries(_strip_comments(body), errors_path=tmp_path / "e.jsonl")
        assert len(entries) == 1
        assert entries[0].url == "https://real.com"

    def test_all_entries_commented_out(self, tmp_path):
        body = "<!-- - https://a.com\n  role: docs\n  scrape: none -->\n"
        entries = _parse_source_entries(_strip_comments(body), errors_path=tmp_path / "e.jsonl")
        assert entries == []

    def test_inline_comment_around_field(self, tmp_path):
        body = (
            "- https://example.com\n  role: docs\n  scrape: shallow\n  <!-- notes: old note -->\n"
        )
        entries = _parse_source_entries(_strip_comments(body), errors_path=tmp_path / "e.jsonl")
        assert len(entries) == 1
        assert entries[0].notes == ""

    def test_real_entries_before_and_after_commented_block(self, tmp_path):
        body = (
            "- https://first.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "\n"
            "<!-- - https://hidden.com\n"
            "  role: docs\n"
            "  scrape: deep -->\n"
            "\n"
            "- https://last.com\n"
            "  role: counter-example\n"
            "  scrape: shallow\n"
        )
        entries = _parse_source_entries(_strip_comments(body), errors_path=tmp_path / "e.jsonl")
        assert len(entries) == 2
        assert entries[0].url == "https://first.com"
        assert entries[1].url == "https://last.com"


class TestParseSourceEntriesFieldCombinations:
    """Verify all field value combinations parse correctly."""

    def test_all_three_roles(self, tmp_path):
        ep = tmp_path / "e.jsonl"
        body = (
            "- https://a.com\n"
            "  role: product-reference\n"
            "  scrape: none\n"
            "\n"
            "- https://b.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "\n"
            "- https://c.com\n"
            "  role: counter-example\n"
            "  scrape: none\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 3
        assert entries[0].role == "product-reference"
        assert entries[1].role == "docs"
        assert entries[2].role == "counter-example"

    def test_all_three_scrape_values(self, tmp_path):
        ep = tmp_path / "e.jsonl"
        body = (
            "- https://a.com\n"
            "  role: docs\n"
            "  scrape: deep\n"
            "\n"
            "- https://b.com\n"
            "  role: docs\n"
            "  scrape: shallow\n"
            "\n"
            "- https://c.com\n"
            "  role: docs\n"
            "  scrape: none\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 3
        assert entries[0].scrape == "deep"
        assert entries[1].scrape == "shallow"
        assert entries[2].scrape == "none"

    def test_http_url_accepted(self, tmp_path):
        ep = tmp_path / "e.jsonl"
        body = "- http://example.com\n  role: docs\n  scrape: none\n"
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 1
        assert entries[0].url == "http://example.com"

    def test_discovered_true_proposed_false(self, tmp_path):
        ep = tmp_path / "e.jsonl"
        body = (
            "- https://example.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "  proposed: false\n"
            "  discovered: true\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert entries[0].proposed is False
        assert entries[0].discovered is True

    def test_proposed_true_discovered_false(self, tmp_path):
        ep = tmp_path / "e.jsonl"
        body = (
            "- https://example.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "  proposed: true\n"
            "  discovered: false\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert entries[0].proposed is True
        assert entries[0].discovered is False

    def test_notes_with_all_fields(self, tmp_path):
        ep = tmp_path / "e.jsonl"
        body = (
            "- https://example.com\n"
            "  role: product-reference\n"
            "  scrape: deep\n"
            "  notes: authoritative source\n"
            "  proposed: true\n"
            "  discovered: true\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 1
        assert entries[0].notes == "authoritative source"
        assert entries[0].proposed is True
        assert entries[0].discovered is True

    def test_many_entries(self, tmp_path):
        """Five entries in sequence, no blank lines between them."""
        ep = tmp_path / "e.jsonl"
        lines = []
        for i in range(5):
            lines.append(f"- https://site{i}.com\n")
            lines.append("  role: docs\n")
            lines.append("  scrape: none\n")
        body = "".join(lines)
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 5
        for i, entry in enumerate(entries):
            assert entry.url == f"https://site{i}.com"


class TestParseSourceEntriesMultilineNotes:
    """Detailed multi-line notes parsing."""

    def test_three_continuation_lines(self, tmp_path):
        ep = tmp_path / "e.jsonl"
        body = (
            "- https://example.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "  notes: line one\n"
            "    line two\n"
            "    line three\n"
            "    line four\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert entries[0].notes == "line one\nline two\nline three\nline four"

    def test_multiline_notes_between_entries(self, tmp_path):
        ep = tmp_path / "e.jsonl"
        body = (
            "- https://first.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "  notes: note A\n"
            "    continued A\n"
            "\n"
            "- https://second.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "  notes: note B\n"
            "    continued B\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert len(entries) == 2
        assert entries[0].notes == "note A\ncontinued A"
        assert entries[1].notes == "note B\ncontinued B"

    def test_multiline_notes_before_proposed(self, tmp_path):
        ep = tmp_path / "e.jsonl"
        body = (
            "- https://example.com\n"
            "  role: docs\n"
            "  scrape: none\n"
            "  notes: first\n"
            "    second\n"
            "  proposed: true\n"
        )
        entries = _parse_source_entries(body, errors_path=ep)
        assert entries[0].notes == "first\nsecond"
        assert entries[0].proposed is True


class TestKnownSections:
    """``_KNOWN_SECTIONS`` recognises all expected headings."""

    def test_sources_in_known_sections(self):
        assert "sources" in _KNOWN_SECTIONS

    def test_notes_in_known_sections(self):
        assert "notes" in _KNOWN_SECTIONS


class TestSpecNotes:
    """``spec.notes`` stores comment-stripped body from ``## Notes``."""

    def test_notes_stored(self):
        text = "## Notes\n\nSome project notes here.\n"
        spec = _parse_spec(text)
        assert spec.notes == "Some project notes here."

    def test_notes_comments_stripped(self):
        text = "## Notes\n\nVisible text.\n<!-- hidden -->\nMore visible.\n"
        spec = _parse_spec(text)
        assert "hidden" not in spec.notes
        assert "Visible text." in spec.notes
        assert "More visible." in spec.notes

    def test_notes_empty_when_absent(self):
        text = "## Purpose\n\nA calculator.\n"
        spec = _parse_spec(text)
        assert spec.notes == ""

    def test_notes_multiline_preserved_verbatim(self):
        text = "## Notes\n\nLine one.\nLine two.\n\nParagraph two.\n"
        spec = _parse_spec(text)
        assert spec.notes == "Line one.\nLine two.\n\nParagraph two."

    def test_notes_multiline_comment_stripped(self):
        text = "## Notes\n\nBefore comment.\n<!--\nMulti-line\ncomment\n-->\nAfter comment.\n"
        spec = _parse_spec(text)
        assert "Multi-line" not in spec.notes
        assert "Before comment." in spec.notes
        assert "After comment." in spec.notes


class TestReferenceEntryStartBare:
    def test_matches_simple_path(self):
        m = _REFERENCE_ENTRY_START_BARE.match("- ref/screenshot.png")
        assert m is not None
        assert m.group(1) == "ref/screenshot.png"

    def test_matches_path_with_spaces(self):
        m = _REFERENCE_ENTRY_START_BARE.match("- ref/Screen Shot 2025-10-12 at 14.30.png")
        assert m is not None
        assert m.group(1) == "ref/Screen Shot 2025-10-12 at 14.30.png"

    def test_matches_nested_path(self):
        m = _REFERENCE_ENTRY_START_BARE.match("- ref/docs/guide.pdf")
        assert m is not None
        assert m.group(1) == "ref/docs/guide.pdf"

    def test_strips_trailing_whitespace(self):
        m = _REFERENCE_ENTRY_START_BARE.match("- ref/image.png   ")
        assert m is not None
        assert m.group(1) == "ref/image.png"

    def test_no_match_without_dash(self):
        assert _REFERENCE_ENTRY_START_BARE.match("ref/image.png") is None

    def test_no_match_without_ref_prefix(self):
        assert _REFERENCE_ENTRY_START_BARE.match("- images/foo.png") is None

    def test_no_match_indented_line(self):
        assert _REFERENCE_ENTRY_START_BARE.match("  - ref/image.png") is None

    def test_no_match_bare_ref_slash_only(self):
        # "ref/" alone with nothing after is not a valid path.
        assert _REFERENCE_ENTRY_START_BARE.match("- ref/") is None

    def test_path_with_hyphens_and_dots(self):
        m = _REFERENCE_ENTRY_START_BARE.match("- ref/my-app-v2.3.screenshot.png")
        assert m is not None
        assert m.group(1) == "ref/my-app-v2.3.screenshot.png"


class TestReferenceEntryStartQuoted:
    def test_matches_quoted_path(self):
        m = _REFERENCE_ENTRY_START_QUOTED.match('- "ref/screenshot.png"')
        assert m is not None
        assert m.group(1) == "ref/screenshot.png"

    def test_matches_quoted_path_with_spaces(self):
        m = _REFERENCE_ENTRY_START_QUOTED.match('- "ref/Screen Shot 2025-10-12 at 14.30.png"')
        assert m is not None
        assert m.group(1) == "ref/Screen Shot 2025-10-12 at 14.30.png"

    def test_matches_quoted_path_with_special_chars(self):
        m = _REFERENCE_ENTRY_START_QUOTED.match('- "ref/file (copy).png"')
        assert m is not None
        assert m.group(1) == "ref/file (copy).png"

    def test_strips_trailing_whitespace(self):
        m = _REFERENCE_ENTRY_START_QUOTED.match('- "ref/image.png"   ')
        assert m is not None
        assert m.group(1) == "ref/image.png"

    def test_no_match_without_ref_prefix(self):
        assert _REFERENCE_ENTRY_START_QUOTED.match('- "images/foo.png"') is None

    def test_no_match_without_dash(self):
        assert _REFERENCE_ENTRY_START_QUOTED.match('"ref/image.png"') is None

    def test_no_match_indented_line(self):
        assert _REFERENCE_ENTRY_START_QUOTED.match('  - "ref/image.png"') is None

    def test_no_match_embedded_quote(self):
        # A literal `"` inside the path breaks the quoted form.
        assert _REFERENCE_ENTRY_START_QUOTED.match('- "ref/file"name.png"') is None

    def test_no_match_unquoted_path(self):
        assert _REFERENCE_ENTRY_START_QUOTED.match("- ref/image.png") is None

    def test_quoted_nested_path(self):
        m = _REFERENCE_ENTRY_START_QUOTED.match('- "ref/sub dir/deep/file.pdf"')
        assert m is not None
        assert m.group(1) == "ref/sub dir/deep/file.pdf"


class TestParseReferenceEntries:
    def test_single_entry_all_fields(self):
        body = "- ref/screenshot.png\n  role: visual-target\n  notes: main UI\n  proposed: true\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        entry = entries[0]
        assert entry.path == Path("ref/screenshot.png")
        assert entry.roles == ["visual-target"]
        assert entry.notes == "main UI"
        assert entry.proposed is True

    def test_single_entry_minimal(self):
        body = "- ref/demo.mp4\n  role: behavioral-target\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].path == Path("ref/demo.mp4")
        assert entries[0].roles == ["behavioral-target"]
        assert entries[0].notes == ""
        assert entries[0].proposed is False

    def test_multiple_entries(self):
        body = (
            "- ref/screenshot.png\n"
            "  role: visual-target\n"
            "\n"
            "- ref/demo.mp4\n"
            "  role: behavioral-target\n"
        )
        entries = _parse_reference_entries(body)
        assert len(entries) == 2
        assert entries[0].path == Path("ref/screenshot.png")
        assert entries[1].path == Path("ref/demo.mp4")

    def test_entries_separated_by_new_entry_no_blank_line(self):
        body = "- ref/a.png\n  role: visual-target\n- ref/b.png\n  role: docs\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 2
        assert entries[0].path == Path("ref/a.png")
        assert entries[1].path == Path("ref/b.png")

    def test_multiple_roles_comma_separated(self):
        body = "- ref/demo.mp4\n  role: behavioral-target, visual-target\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].roles == ["behavioral-target", "visual-target"]

    def test_quoted_path(self):
        body = '- "ref/Screen Shot 2025-10-12 at 14.30.png"\n  role: visual-target\n'
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].path == Path("ref/Screen Shot 2025-10-12 at 14.30.png")

    def test_bare_path_with_spaces(self):
        body = "- ref/Screen Shot 2025-10-12 at 14.30.png\n  role: visual-target\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].path == Path("ref/Screen Shot 2025-10-12 at 14.30.png")

    def test_multiline_notes(self):
        body = (
            "- ref/demo.mp4\n"
            "  role: behavioral-target\n"
            "  notes: first line\n"
            "    second line\n"
            "    third line\n"
        )
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].notes == "first line\nsecond line\nthird line"

    def test_multiline_notes_stops_at_next_field(self):
        body = "- ref/demo.mp4\n  notes: line one\n    line two\n  role: docs\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].notes == "line one\nline two"
        assert entries[0].roles == ["docs"]

    def test_empty_body(self):
        entries = _parse_reference_entries("")
        assert entries == []

    def test_body_with_no_entries(self):
        body = "Some prose about references.\nMore text.\n"
        entries = _parse_reference_entries(body)
        assert entries == []


class TestParseReferenceEntriesValidation:
    def test_unknown_role_dropped_from_list(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        body = "- ref/demo.mp4\n  role: behavioral-target, bogus-role\n"
        entries = _parse_reference_entries(body, errors_path=str(errors))
        assert len(entries) == 1
        assert entries[0].roles == ["behavioral-target"]
        log = errors.read_text()
        assert "bogus-role" in log

    def test_all_roles_unknown_defaults_to_ignore(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        body = "- ref/demo.mp4\n  role: bogus, also-bogus\n"
        entries = _parse_reference_entries(body, errors_path=str(errors))
        assert len(entries) == 1
        assert entries[0].roles == ["ignore"]

    def test_discovered_flag_emits_diagnostic(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        body = "- ref/demo.mp4\n  role: docs\n  discovered: true\n"
        entries = _parse_reference_entries(body, errors_path=str(errors))
        assert len(entries) == 1
        # discovered is ignored — not stored on ReferenceEntry.
        assert not hasattr(entries[0], "discovered")
        log = errors.read_text()
        assert "discovered" in log
        assert "not valid for References" in log

    def test_discovered_false_still_emits_diagnostic(self, tmp_path):
        """Any discovered: value triggers diagnostic, not just 'true'."""
        errors = tmp_path / "errors.jsonl"
        body = "- ref/demo.mp4\n  role: docs\n  discovered: false\n"
        entries = _parse_reference_entries(body, errors_path=str(errors))
        assert len(entries) == 1
        log = errors.read_text()
        assert "discovered" in log
        assert "not valid for References" in log

    def test_no_role_keeps_empty_roles(self):
        body = "- ref/demo.mp4\n  notes: no role given\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].roles == []

    def test_comment_stripped_entries_not_parsed(self):
        body = "<!-- - ref/hidden.png\n  role: visual-target -->\n"
        # The caller should strip comments before passing to the parser,
        # but the parser itself just sees raw lines. After comment
        # stripping the entry won't be present.
        stripped = _strip_comments(body)
        entries = _parse_reference_entries(stripped)
        assert entries == []


class TestParseReferenceEntriesMultipleRoles:
    def test_all_valid_roles_accepted(self):
        body = (
            "- ref/a.png\n"
            "  role: visual-target, behavioral-target, "
            "docs, counter-example, ignore\n"
        )
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert set(entries[0].roles) == _VALID_REFERENCE_ROLES

    def test_single_valid_role_among_invalids(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        body = "- ref/a.png\n  role: bad1, visual-target, bad2\n"
        entries = _parse_reference_entries(body, errors_path=str(errors))
        assert entries[0].roles == ["visual-target"]
        log = errors.read_text()
        assert "bad1" in log
        assert "bad2" in log

    def test_trailing_comma_ignored(self):
        body = "- ref/a.png\n  role: visual-target, behavioral-target,\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].roles == ["visual-target", "behavioral-target"]

    def test_no_spaces_around_comma(self):
        body = "- ref/a.png\n  role: visual-target,behavioral-target\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].roles == ["visual-target", "behavioral-target"]

    def test_extra_spaces_around_comma(self):
        body = "- ref/a.png\n  role: visual-target ,  behavioral-target\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].roles == ["visual-target", "behavioral-target"]

    def test_single_role_produces_list(self):
        body = "- ref/a.png\n  role: visual-target\n"
        entries = _parse_reference_entries(body)
        assert len(entries) == 1
        assert entries[0].roles == ["visual-target"]
        assert isinstance(entries[0].roles, list)


class TestValidateReferenceEntriesPathCheck:
    def test_path_under_ref_accepted(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        entries = [
            ReferenceEntry(path=Path("ref/screenshot.png"), roles=["visual-target"]),
        ]
        result = _validate_reference_entries(entries, errors_path=str(errors))
        assert len(result) == 1
        assert result[0].path == Path("ref/screenshot.png")
        assert not errors.exists()

    def test_path_not_under_ref_dropped(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        entries = [
            ReferenceEntry(path=Path("other/file.png"), roles=["visual-target"]),
        ]
        result = _validate_reference_entries(entries, errors_path=str(errors))
        assert result == []
        log = errors.read_text()
        assert "outside ref/" in log

    def test_absolute_path_dropped(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        entries = [
            ReferenceEntry(path=Path("/abs/ref/file.png"), roles=["docs"]),
        ]
        result = _validate_reference_entries(entries, errors_path=str(errors))
        assert result == []
        log = errors.read_text()
        assert "outside ref/" in log

    def test_mixed_valid_and_invalid_paths(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        entries = [
            ReferenceEntry(path=Path("ref/good.png"), roles=["visual-target"]),
            ReferenceEntry(path=Path("images/bad.png"), roles=["docs"]),
            ReferenceEntry(path=Path("ref/also-good.pdf"), roles=["docs"]),
        ]
        result = _validate_reference_entries(entries, errors_path=str(errors))
        assert len(result) == 2
        assert result[0].path == Path("ref/good.png")
        assert result[1].path == Path("ref/also-good.pdf")

    def test_path_validation_before_role_validation(self, tmp_path):
        """Entry with bad path is dropped even if roles are valid."""
        errors = tmp_path / "errors.jsonl"
        entries = [
            ReferenceEntry(path=Path("src/file.py"), roles=["visual-target"]),
        ]
        result = _validate_reference_entries(entries, errors_path=str(errors))
        assert result == []

    def test_all_roles_unknown_defaults_to_ignore_via_validate(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        entries = [
            ReferenceEntry(path=Path("ref/demo.mp4"), roles=["bogus", "nope"]),
        ]
        result = _validate_reference_entries(entries, errors_path=str(errors))
        assert len(result) == 1
        assert result[0].roles == ["ignore"]
        log = errors.read_text()
        assert "bogus" in log
        assert "nope" in log
        assert "defaulting to" in log

    def test_unknown_roles_dropped_keeps_valid(self, tmp_path):
        errors = tmp_path / "errors.jsonl"
        entries = [
            ReferenceEntry(
                path=Path("ref/a.png"),
                roles=["visual-target", "fake", "docs"],
            ),
        ]
        result = _validate_reference_entries(entries, errors_path=str(errors))
        assert result[0].roles == ["visual-target", "docs"]
        log = errors.read_text()
        assert "fake" in log


class TestReferencesMigration:
    """Old prose-form ## References parses to empty list with diagnostic."""

    def test_prose_references_parses_to_empty_list(self):
        text = (
            "## References\n"
            "- numi.app demo video is the visual ground truth\n"
            "- github wiki is the behavioral ground truth\n"
        )
        spec = _parse_spec(text)
        assert spec.references == []

    def test_prose_preserved_in_raw(self):
        text = "## References\n- numi.app demo video is the visual ground truth\n"
        spec = _parse_spec(text)
        assert "visual ground truth" in spec.raw

    def test_prose_references_emits_migration_diagnostic(self, tmp_path, monkeypatch):
        errors = tmp_path / "errors.jsonl"
        monkeypatch.setattr(
            "duplo.spec_reader.record_failure",
            lambda site, cat, msg, **kw: errors.write_text(
                json.dumps({"site": site, "category": cat, "message": msg}) + "\n"
            ),
        )
        text = "## References\n- numi.app demo video is the visual ground truth\n"
        _parse_spec(text)
        log = errors.read_text()
        assert "prose" in log
        assert "migrat" in log.lower()

    def test_empty_references_no_diagnostic(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "duplo.spec_reader.record_failure",
            lambda site, cat, msg, **kw: calls.append(msg),
        )
        text = "## References\n"
        _parse_spec(text)
        ref_calls = [c for c in calls if "References" in c or "references" in c]
        assert ref_calls == []

    def test_structured_references_no_diagnostic(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "duplo.spec_reader.record_failure",
            lambda site, cat, msg, **kw: calls.append(msg),
        )
        text = "## References\n- ref/demo.mp4\n  role: visual-target\n"
        _parse_spec(text)
        ref_calls = [c for c in calls if "prose" in c]
        assert ref_calls == []


class TestAutogenRe:
    """Tests for _AUTOGEN_RE regex matching BEGIN/END AUTO-GENERATED markers."""

    def test_matches_standard_block(self):
        body = (
            "Some prose.\n"
            "<!-- BEGIN AUTO-GENERATED -->\n"
            "auto content here\n"
            "<!-- END AUTO-GENERATED -->\n"
            "More prose."
        )
        m = _AUTOGEN_RE.search(body)
        assert m is not None
        assert m.group(1).strip() == "auto content here"

    def test_matches_block_with_extra_text_in_begin(self):
        body = (
            "<!-- BEGIN AUTO-GENERATED design-requirements -->\n"
            "colors: blue\n"
            "<!-- END AUTO-GENERATED -->"
        )
        m = _AUTOGEN_RE.search(body)
        assert m is not None
        assert "colors: blue" in m.group(1)

    def test_dotall_multiline_content(self):
        body = "<!-- BEGIN AUTO-GENERATED -->\nline one\n\nline three\n<!-- END AUTO-GENERATED -->"
        m = _AUTOGEN_RE.search(body)
        assert m is not None
        assert "line one" in m.group(1)
        assert "line three" in m.group(1)

    def test_no_match_without_end_marker(self):
        body = "<!-- BEGIN AUTO-GENERATED -->\nsome content\n"
        m = _AUTOGEN_RE.search(body)
        assert m is None

    def test_no_match_without_begin_marker(self):
        body = "some content\n<!-- END AUTO-GENERATED -->"
        m = _AUTOGEN_RE.search(body)
        assert m is None

    def test_no_match_on_plain_text(self):
        body = "Just regular design prose, no markers."
        m = _AUTOGEN_RE.search(body)
        assert m is None

    def test_flexible_whitespace_in_markers(self):
        body = "<!--  BEGIN AUTO-GENERATED  -->\ncontent\n<!--  END AUTO-GENERATED  -->"
        m = _AUTOGEN_RE.search(body)
        assert m is not None
        assert m.group(1).strip() == "content"

    def test_malformed_begin_no_match(self):
        body = "<!-- BEGINAUTO-GENERATED -->\ncontent\n<!-- END AUTO-GENERATED -->"
        m = _AUTOGEN_RE.search(body)
        assert m is None

    def test_captures_empty_block(self):
        body = "<!-- BEGIN AUTO-GENERATED --><!-- END AUTO-GENERATED -->"
        m = _AUTOGEN_RE.search(body)
        assert m is not None
        assert m.group(1) == ""


class TestParseDesignBlock:
    """Tests for _parse_design_block splitting body into DesignBlock."""

    def test_block_present_splits_user_prose_and_auto(self):
        body = (
            "User-written design notes.\n\n"
            "<!-- BEGIN AUTO-GENERATED -->\n"
            "colors: #2b2b2b\nfonts: Inter\n"
            "<!-- END AUTO-GENERATED -->\n"
        )
        block = _parse_design_block(body)
        assert block.user_prose == "User-written design notes."
        assert block.auto_generated == "colors: #2b2b2b\nfonts: Inter"

    def test_block_absent_all_goes_to_user_prose(self):
        body = "Just user prose, no auto block."
        block = _parse_design_block(body)
        assert block.user_prose == "Just user prose, no auto block."
        assert block.auto_generated == ""

    def test_malformed_markers_treated_as_no_block(self):
        body = "<!-- BEGINAUTO-GENERATED -->\ncontent\n<!-- END AUTO-GENERATED -->"
        block = _parse_design_block(body)
        # Malformed BEGIN marker means no match — all to user_prose.
        assert block.auto_generated == ""
        assert "content" in block.user_prose

    def test_html_comments_stripped_from_user_prose(self):
        body = (
            "Visible prose. <!-- hidden comment -->\n\n"
            "<!-- BEGIN AUTO-GENERATED -->\nauto\n<!-- END AUTO-GENERATED -->"
        )
        block = _parse_design_block(body)
        assert "hidden comment" not in block.user_prose
        assert "Visible prose." in block.user_prose
        assert block.auto_generated == "auto"

    def test_fill_in_marker_in_user_prose(self):
        body = "<FILL IN>\n\n<!-- BEGIN AUTO-GENERATED -->\nauto\n<!-- END AUTO-GENERATED -->"
        block = _parse_design_block(body)
        assert block.has_fill_in_marker is True

    def test_fill_in_marker_absent(self):
        body = (
            "Real design notes.\n\n"
            "<!-- BEGIN AUTO-GENERATED -->\nauto\n<!-- END AUTO-GENERATED -->"
        )
        block = _parse_design_block(body)
        assert block.has_fill_in_marker is False

    def test_fill_in_marker_in_comment_not_detected(self):
        body = (
            "<!-- <FILL IN> -->\nReal design.\n\n"
            "<!-- BEGIN AUTO-GENERATED -->\nauto\n<!-- END AUTO-GENERATED -->"
        )
        block = _parse_design_block(body)
        assert block.has_fill_in_marker is False

    def test_fill_in_marker_no_block(self):
        body = "<FILL IN>\nSome initial design."
        block = _parse_design_block(body)
        assert block.has_fill_in_marker is True
        assert block.auto_generated == ""

    def test_empty_body(self):
        block = _parse_design_block("")
        assert block.user_prose == ""
        assert block.auto_generated == ""
        assert block.has_fill_in_marker is False

    def test_only_auto_block_no_user_prose(self):
        body = "<!-- BEGIN AUTO-GENERATED -->\nauto only\n<!-- END AUTO-GENERATED -->"
        block = _parse_design_block(body)
        assert block.user_prose == ""
        assert block.auto_generated == "auto only"

    def test_block_absent_comments_stripped_from_user_prose(self):
        body = "Visible text. <!-- hidden --> More visible."
        block = _parse_design_block(body)
        assert block.user_prose == "Visible text.  More visible."
        assert block.auto_generated == ""

    def test_block_absent_multiline_body(self):
        body = "Line one.\n\nLine two.\nLine three."
        block = _parse_design_block(body)
        assert block.user_prose == "Line one.\n\nLine two.\nLine three."
        assert block.auto_generated == ""

    def test_block_absent_whitespace_only(self):
        block = _parse_design_block("   \n  \n  ")
        assert block.user_prose == ""
        assert block.auto_generated == ""
        assert block.has_fill_in_marker is False

    def test_block_absent_multiline_comment_stripped(self):
        body = "Before.\n<!--\nmulti\nline\n-->\nAfter."
        block = _parse_design_block(body)
        assert "multi" not in block.user_prose
        assert "Before." in block.user_prose
        assert "After." in block.user_prose
        assert block.auto_generated == ""

    def test_text_after_block_not_in_user_prose(self):
        body = (
            "Before.\n"
            "<!-- BEGIN AUTO-GENERATED -->\nauto\n<!-- END AUTO-GENERATED -->\n"
            "After the block."
        )
        block = _parse_design_block(body)
        assert block.user_prose == "Before."
        assert block.auto_generated == "auto"
        # Text after the block is neither user_prose nor auto_generated.
        assert "After" not in block.user_prose

    def test_begin_only_marker_treated_as_no_block(self):
        body = "User prose.\n<!-- BEGIN AUTO-GENERATED -->\norphaned content"
        block = _parse_design_block(body)
        # No END marker → regex doesn't match → all to user_prose.
        assert block.auto_generated == ""
        assert "User prose." in block.user_prose
        assert "orphaned content" in block.user_prose

    def test_end_only_marker_treated_as_no_block(self):
        body = "User prose.\norphaned content\n<!-- END AUTO-GENERATED -->"
        block = _parse_design_block(body)
        # No BEGIN marker → regex doesn't match → all to user_prose.
        assert block.auto_generated == ""
        assert "User prose." in block.user_prose
        assert "orphaned content" in block.user_prose

    def test_nested_markers_first_match_wins(self):
        body = (
            "Outer prose.\n"
            "<!-- BEGIN AUTO-GENERATED -->\n"
            "first auto\n"
            "<!-- BEGIN AUTO-GENERATED -->\n"
            "nested auto\n"
            "<!-- END AUTO-GENERATED -->\n"
            "between\n"
            "<!-- END AUTO-GENERATED -->"
        )
        block = _parse_design_block(body)
        # _AUTOGEN_RE is non-greedy (.*?) so it matches the first
        # BEGIN to the first END deterministically.
        assert block.user_prose == "Outer prose."
        assert "first auto" in block.auto_generated
        assert "nested auto" in block.auto_generated
        # The first END closes the match; content after is excluded.
        assert "between" not in block.auto_generated

    def test_repeated_markers_first_block_wins(self):
        body = (
            "Prose before.\n"
            "<!-- BEGIN AUTO-GENERATED -->\n"
            "block one\n"
            "<!-- END AUTO-GENERATED -->\n"
            "Middle text.\n"
            "<!-- BEGIN AUTO-GENERATED -->\n"
            "block two\n"
            "<!-- END AUTO-GENERATED -->"
        )
        block = _parse_design_block(body)
        # .search() returns the first match — second block is ignored.
        assert block.user_prose == "Prose before."
        assert block.auto_generated == "block one"
        assert "block two" not in block.auto_generated


class TestParseSpecSourcesIntegration:
    """Integration tests: _parse_spec wires ## Sources into spec.sources."""

    def test_sources_parsed_into_spec(self):
        text = "## Sources\n- https://example.com/docs\n  role: docs\n  scrape: deep\n"
        spec = _parse_spec(text)
        assert len(spec.sources) == 1
        assert spec.sources[0].url == "https://example.com/docs"
        assert spec.sources[0].role == "docs"
        assert spec.sources[0].scrape == "deep"

    def test_no_sources_section_gives_empty_list(self):
        text = "## Purpose\nSome purpose.\n"
        spec = _parse_spec(text)
        assert spec.sources == []

    def test_sources_default_on_new_spec(self):
        spec = ProductSpec()
        assert spec.sources == []


class TestFullyFilledSpec:
    """Parse a SPEC.md that exercises every section and field.

    Verifies that unchanged-type fields (purpose, architecture, scope,
    behavior) still populate correctly alongside the new structured
    fields (design as DesignBlock, references as list[ReferenceEntry],
    sources as list[SourceEntry], notes, fill_in_* flags).
    """

    FULL_SPEC = (
        "# CalcApp\n\n"
        "## Purpose\n"
        "A macOS text calculator inspired by numi.app.\n\n"
        "## Scope\n"
        "- include: arithmetic, unit conversion\n"
        "- exclude: JavaScript plugin API\n\n"
        "## Behavior\n"
        "- `2+3` → `5`\n"
        "- `10 km in miles` should produce `6.21 mi`\n\n"
        "## Architecture\n"
        "Swift, SwiftUI, SPM, macOS 14+. No external dependencies.\n\n"
        "## Design\n"
        "Dark theme, monospace font.\n\n"
        "<!-- BEGIN AUTO-GENERATED design-requirements -->\n"
        "colors: #2b2b2b, #a3be8c\n"
        "<!-- END AUTO-GENERATED -->\n\n"
        "## References\n"
        "- ref/demo.mp4\n"
        "  role: visual-target, behavioral-target\n"
        "  notes: product walkthrough\n"
        "- ref/api-docs.pdf\n"
        "  role: docs\n\n"
        "## Sources\n"
        "- https://numi.app\n"
        "  role: product-reference\n"
        "  scrape: deep\n"
        "  notes: main product site\n"
        "- https://docs.numi.app\n"
        "  role: docs\n"
        "  scrape: shallow\n\n"
        "## Notes\n"
        "Focus on core arithmetic first.\n"
    )

    def test_raw_contains_full_text(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert spec.raw == self.FULL_SPEC

    # --- Unchanged-type fields: purpose, architecture, scope, behavior ---

    def test_purpose_populated(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert "macOS text calculator" in spec.purpose

    def test_architecture_populated(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert "SwiftUI" in spec.architecture
        assert "SPM" in spec.architecture

    def test_scope_include(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert "arithmetic" in spec.scope_include
        assert "unit conversion" in spec.scope_include

    def test_scope_exclude(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert "JavaScript plugin API" in spec.scope_exclude

    def test_scope_raw(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert "include: arithmetic" in spec.scope

    def test_behavior_contracts(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert len(spec.behavior_contracts) == 2
        assert spec.behavior_contracts[0] == BehaviorContract(input="2+3", expected="5")
        assert spec.behavior_contracts[1] == BehaviorContract(
            input="10 km in miles", expected="6.21 mi"
        )

    def test_behavior_raw(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert "`2+3`" in spec.behavior

    # --- New structured fields ---

    def test_design_is_design_block(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert isinstance(spec.design, DesignBlock)

    def test_design_user_prose(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert "Dark theme" in spec.design.user_prose
        assert "monospace font" in spec.design.user_prose

    def test_design_auto_generated(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert "#2b2b2b" in spec.design.auto_generated
        assert "#a3be8c" in spec.design.auto_generated

    def test_design_no_fill_in_marker(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert spec.design.has_fill_in_marker is False

    def test_references_is_list(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert isinstance(spec.references, list)
        assert len(spec.references) == 2

    def test_references_first_entry(self):
        spec = _parse_spec(self.FULL_SPEC)
        ref = spec.references[0]
        assert isinstance(ref, ReferenceEntry)
        assert ref.path == Path("ref/demo.mp4")
        assert "visual-target" in ref.roles
        assert "behavioral-target" in ref.roles
        assert ref.notes == "product walkthrough"

    def test_references_second_entry(self):
        spec = _parse_spec(self.FULL_SPEC)
        ref = spec.references[1]
        assert ref.path == Path("ref/api-docs.pdf")
        assert ref.roles == ["docs"]

    def test_sources_populated(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert len(spec.sources) == 2
        assert spec.sources[0].url == "https://numi.app"
        assert spec.sources[0].role == "product-reference"
        assert spec.sources[0].scrape == "deep"
        assert spec.sources[0].notes == "main product site"

    def test_sources_second_entry(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert spec.sources[1].url == "https://docs.numi.app"
        assert spec.sources[1].role == "docs"
        assert spec.sources[1].scrape == "shallow"

    def test_notes_populated(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert "Focus on core arithmetic first" in spec.notes

    def test_fill_in_purpose_false(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert spec.fill_in_purpose is False

    def test_fill_in_architecture_false(self):
        spec = _parse_spec(self.FULL_SPEC)
        assert spec.fill_in_architecture is False

    def test_fill_in_design_false_with_visual_target(self):
        """fill_in_design is false because design has no <FILL IN> marker."""
        spec = _parse_spec(self.FULL_SPEC)
        assert spec.fill_in_design is False


class TestFullyFilledSpecWithFillInMarkers:
    """Variant with <FILL IN> markers to verify fill_in_* flags go True."""

    SPEC_WITH_MARKERS = (
        "## Purpose\n"
        "<FILL IN: describe purpose>\n\n"
        "## Architecture\n"
        "<FILL IN: describe architecture>\n\n"
        "## Design\n"
        "<FILL IN: describe design>\n\n"
        "## References\n"
        "- ref/doc.pdf\n"
        "  role: docs\n"
    )

    def test_fill_in_purpose_true(self):
        spec = _parse_spec(self.SPEC_WITH_MARKERS)
        assert spec.fill_in_purpose is True

    def test_fill_in_architecture_true(self):
        spec = _parse_spec(self.SPEC_WITH_MARKERS)
        assert spec.fill_in_architecture is True

    def test_fill_in_design_true_no_visual_target(self):
        """No visual-target in references, so fill_in_design is True."""
        spec = _parse_spec(self.SPEC_WITH_MARKERS)
        assert spec.fill_in_design is True

    def test_fill_in_design_false_with_visual_target(self):
        """Adding a visual-target reference suppresses fill_in_design."""
        text = (
            "## Design\n"
            "<FILL IN: describe design>\n\n"
            "## References\n"
            "- ref/demo.mp4\n"
            "  role: visual-target\n"
        )
        spec = _parse_spec(text)
        assert spec.design.has_fill_in_marker is True
        assert spec.fill_in_design is False


class TestFormatReferenceFilters:
    """Tests for format_visual_references, format_behavioral_references,
    format_doc_references, and format_counter_examples."""

    def _make_spec(self, refs: list[ReferenceEntry]) -> ProductSpec:
        return ProductSpec(references=refs)

    def _entry(
        self,
        name: str,
        roles: list[str],
        proposed: bool = False,
    ) -> ReferenceEntry:
        return ReferenceEntry(
            path=Path(f"ref/{name}"),
            roles=roles,
            proposed=proposed,
        )

    def test_visual_returns_visual_target(self):
        e = self._entry("a.png", ["visual-target"])
        spec = self._make_spec([e])
        assert format_visual_references(spec) == [e]

    def test_visual_excludes_proposed(self):
        e = self._entry("a.png", ["visual-target"], proposed=True)
        spec = self._make_spec([e])
        assert format_visual_references(spec) == []

    def test_behavioral_returns_behavioral_target(self):
        e = self._entry("demo.mp4", ["behavioral-target"])
        spec = self._make_spec([e])
        assert format_behavioral_references(spec) == [e]

    def test_behavioral_excludes_proposed(self):
        e = self._entry("demo.mp4", ["behavioral-target"], proposed=True)
        spec = self._make_spec([e])
        assert format_behavioral_references(spec) == []

    def test_docs_returns_docs(self):
        e = self._entry("readme.md", ["docs"])
        spec = self._make_spec([e])
        assert format_doc_references(spec) == [e]

    def test_docs_excludes_proposed(self):
        e = self._entry("readme.md", ["docs"], proposed=True)
        spec = self._make_spec([e])
        assert format_doc_references(spec) == []

    def test_counter_example_returns_counter(self):
        e = self._entry("bad.png", ["counter-example"])
        spec = self._make_spec([e])
        assert format_counter_examples(spec) == [e]

    def test_counter_example_excludes_proposed(self):
        e = self._entry("bad.png", ["counter-example"], proposed=True)
        spec = self._make_spec([e])
        assert format_counter_examples(spec) == []

    def test_dual_role_appears_in_both(self):
        e = self._entry("demo.mp4", ["visual-target", "behavioral-target"])
        spec = self._make_spec([e])
        assert format_visual_references(spec) == [e]
        assert format_behavioral_references(spec) == [e]

    def test_triple_role_appears_in_all_matching(self):
        e = self._entry("all.png", ["visual-target", "docs", "counter-example"])
        spec = self._make_spec([e])
        assert format_visual_references(spec) == [e]
        assert format_doc_references(spec) == [e]
        assert format_counter_examples(spec) == [e]
        assert format_behavioral_references(spec) == []

    def test_empty_references(self):
        spec = self._make_spec([])
        assert format_visual_references(spec) == []
        assert format_behavioral_references(spec) == []
        assert format_doc_references(spec) == []
        assert format_counter_examples(spec) == []

    def test_no_matching_role(self):
        e = self._entry("ignore.png", ["ignore"])
        spec = self._make_spec([e])
        assert format_visual_references(spec) == []
        assert format_behavioral_references(spec) == []
        assert format_doc_references(spec) == []
        assert format_counter_examples(spec) == []

    def test_mixed_proposed_and_confirmed(self):
        confirmed = self._entry("a.png", ["visual-target"])
        proposed = self._entry("b.png", ["visual-target"], proposed=True)
        spec = self._make_spec([confirmed, proposed])
        assert format_visual_references(spec) == [confirmed]


class TestScrapeable:
    """Tests for scrapeable_sources."""

    def _entry(
        self,
        url: str = "https://example.com",
        role: str = "product-reference",
        scrape: str = "deep",
        proposed: bool = False,
        discovered: bool = False,
    ) -> SourceEntry:
        return SourceEntry(
            url=url,
            role=role,
            scrape=scrape,
            proposed=proposed,
            discovered=discovered,
        )

    def _spec(self, sources: list[SourceEntry]) -> ProductSpec:
        return ProductSpec(sources=sources)

    def test_deep_scrape_included(self):
        e = self._entry(scrape="deep")
        assert scrapeable_sources(self._spec([e])) == [e]

    def test_shallow_scrape_included(self):
        e = self._entry(scrape="shallow")
        assert scrapeable_sources(self._spec([e])) == [e]

    def test_none_scrape_excluded(self):
        e = self._entry(scrape="none")
        assert scrapeable_sources(self._spec([e])) == []

    def test_discovered_excluded(self):
        e = self._entry(discovered=True)
        assert scrapeable_sources(self._spec([e])) == []

    def test_proposed_excluded(self):
        e = self._entry(proposed=True)
        assert scrapeable_sources(self._spec([e])) == []

    def test_counter_example_excluded(self):
        e = self._entry(role="counter-example")
        assert scrapeable_sources(self._spec([e])) == []

    def test_docs_role_included(self):
        e = self._entry(role="docs", scrape="deep")
        assert scrapeable_sources(self._spec([e])) == [e]

    def test_product_reference_role_included(self):
        e = self._entry(role="product-reference", scrape="shallow")
        assert scrapeable_sources(self._spec([e])) == [e]

    def test_empty_sources(self):
        assert scrapeable_sources(self._spec([])) == []

    def test_mixed_entries(self):
        keep_deep = self._entry(url="https://a.com", scrape="deep")
        keep_shallow = self._entry(url="https://b.com", scrape="shallow")
        skip_none = self._entry(url="https://c.com", scrape="none")
        skip_discovered = self._entry(url="https://d.com", discovered=True)
        skip_proposed = self._entry(url="https://e.com", proposed=True)
        skip_counter = self._entry(url="https://f.com", role="counter-example")
        spec = self._spec(
            [
                keep_deep,
                keep_shallow,
                skip_none,
                skip_discovered,
                skip_proposed,
                skip_counter,
            ]
        )
        assert scrapeable_sources(spec) == [keep_deep, keep_shallow]

    def test_all_conditions_must_hold(self):
        """Counter-example with deep scrape, not discovered, not proposed
        should still be excluded (role filter)."""
        e = self._entry(
            role="counter-example",
            scrape="deep",
            proposed=False,
            discovered=False,
        )
        assert scrapeable_sources(self._spec([e])) == []


class TestFormatDesignForPrompt:
    def test_both_present(self):
        spec = ProductSpec(
            design=DesignBlock(user_prose="User notes", auto_generated="Gen content")
        )
        result = format_design_for_prompt(spec)
        assert result == "User notes\n\n---\n\nGen content"

    def test_only_user_prose(self):
        spec = ProductSpec(design=DesignBlock(user_prose="User notes", auto_generated=""))
        assert format_design_for_prompt(spec) == "User notes"

    def test_only_auto_generated(self):
        spec = ProductSpec(design=DesignBlock(user_prose="", auto_generated="Gen content"))
        assert format_design_for_prompt(spec) == "Gen content"

    def test_neither_present(self):
        spec = ProductSpec(design=DesignBlock())
        assert format_design_for_prompt(spec) == ""

    def test_order_prose_before_auto(self):
        spec = ProductSpec(design=DesignBlock(user_prose="AAA", auto_generated="ZZZ"))
        result = format_design_for_prompt(spec)
        assert result.index("AAA") < result.index("ZZZ")

    def test_separator_between_sections(self):
        spec = ProductSpec(design=DesignBlock(user_prose="prose", auto_generated="auto"))
        result = format_design_for_prompt(spec)
        assert "\n\n---\n\n" in result
