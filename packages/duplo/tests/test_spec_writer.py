"""Tests for duplo.spec_writer."""

import dataclasses
import json
from pathlib import Path

import pytest

from duplo.spec_reader import (
    BehaviorContract,
    DesignBlock,
    ProductSpec,
    ReferenceEntry,
    SourceEntry,
    _parse_spec,
)
from duplo.spec_writer import (
    DraftInputs,
    _draft_from_inputs,
    _infer_url_role,
    _propose_file_role,
    append_references,
    append_sources,
    format_spec,
    update_design_autogen,
)
from duplo.claude_cli import ClaudeCliError


class TestAppendSources:
    """Tests for append_sources."""

    def test_append_single_entry(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(
            url="https://other.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert "- https://other.com" in result
        assert "  role: docs" in result
        assert "  scrape: shallow" in result
        # Original entry still present.
        assert "- https://example.com" in result

    def test_append_multiple_entries(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entries = [
            SourceEntry(url="https://a.com", role="docs", scrape="shallow"),
            SourceEntry(url="https://b.com", role="docs", scrape="none"),
        ]
        result = append_sources(spec, entries)
        assert "- https://a.com" in result
        assert "- https://b.com" in result

    def test_dedup_existing_canonical_url(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(
            url="https://example.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert result == spec

    def test_dedup_trailing_slash(self):
        """Canonicalization strips trailing slash — dedup catches it."""
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(
            url="https://example.com/",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert result == spec

    def test_dedup_case_insensitive_host(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(
            url="https://EXAMPLE.COM",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert result == spec

    def test_idempotent_double_call(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        entry = SourceEntry(url="https://new.com", role="docs", scrape="shallow")
        first = append_sources(spec, [entry])
        second = append_sources(first, [entry])
        assert first == second

    def test_empty_new_entries_returns_unchanged(self):
        spec = "## Sources\n\n- https://example.com\n  role: product-reference\n  scrape: deep\n"
        result = append_sources(spec, [])
        assert result == spec

    def test_missing_sources_section_created(self):
        spec = "## Purpose\n\nBuild a calculator.\n"
        entry = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        result = append_sources(spec, [entry])
        assert "## Sources" in result
        assert "- https://example.com" in result
        assert "  role: product-reference" in result

    def test_missing_sources_placed_after_architecture(self):
        spec = "## Purpose\n\nBuild a calculator.\n\n## Architecture\n\nSwiftUI app.\n"
        entry = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        result = append_sources(spec, [entry])
        # Sources should come after Architecture.
        arch_pos = result.index("## Architecture")
        sources_pos = result.index("## Sources")
        assert sources_pos > arch_pos

    def test_flags_discovered_true(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://found.com",
            role="docs",
            scrape="shallow",
            discovered=True,
        )
        result = append_sources(spec, [entry])
        assert "  discovered: true" in result

    def test_flags_proposed_true(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://suggested.com",
            role="product-reference",
            scrape="deep",
            proposed=True,
        )
        result = append_sources(spec, [entry])
        assert "  proposed: true" in result

    def test_flags_both_proposed_and_discovered(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://both.com",
            role="docs",
            scrape="none",
            proposed=True,
            discovered=True,
        )
        result = append_sources(spec, [entry])
        assert "  proposed: true" in result
        assert "  discovered: true" in result

    def test_no_flags_when_false(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://clean.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        assert "proposed" not in result
        assert "discovered" not in result

    def test_notes_included(self):
        spec = "## Sources\n\n"
        entry = SourceEntry(
            url="https://noted.com",
            role="docs",
            scrape="shallow",
            notes="main docs site",
        )
        result = append_sources(spec, [entry])
        assert "  notes: main docs site" in result

    def test_dedup_against_proposed_existing(self):
        """Dedup ignores flags — even a proposed entry blocks duplicates."""
        spec = (
            "## Sources\n\n"
            "- https://example.com\n"
            "  role: product-reference\n"
            "  scrape: deep\n"
            "  proposed: true\n"
        )
        entry = SourceEntry(
            url="https://example.com",
            role="product-reference",
            scrape="deep",
        )
        result = append_sources(spec, [entry])
        assert result == spec

    def test_missing_sources_at_end_without_architecture(self):
        spec = "## Purpose\n\nBuild a calculator.\n"
        entry = SourceEntry(
            url="https://example.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        # Purpose content should come before Sources.
        purpose_pos = result.index("## Purpose")
        sources_pos = result.index("## Sources")
        assert sources_pos > purpose_pos

    def test_sources_after_architecture_before_next_section(self):
        spec = (
            "## Purpose\n\nBuild a calculator.\n\n"
            "## Architecture\n\nSwiftUI app.\n\n"
            "## Design\n\nMinimal.\n"
        )
        entry = SourceEntry(
            url="https://example.com",
            role="docs",
            scrape="shallow",
        )
        result = append_sources(spec, [entry])
        arch_pos = result.index("## Architecture")
        sources_pos = result.index("## Sources")
        design_pos = result.index("## Design")
        assert arch_pos < sources_pos < design_pos


class TestAppendReferences:
    """Tests for append_references."""

    def test_append_single_entry(self):
        spec = "## References\n\n- ref/a.png\n  role: visual-target\n"
        entry = ReferenceEntry(path=Path("ref/b.png"), roles=["visual-target"])
        result = append_references(spec, [entry])
        assert "- ref/b.png" in result
        assert "  role: visual-target" in result
        # Original entry still present.
        assert "- ref/a.png" in result

    def test_append_multiple_entries(self):
        spec = "## References\n\n- ref/a.png\n  role: visual-target\n"
        entries = [
            ReferenceEntry(path=Path("ref/b.png"), roles=["visual-target"]),
            ReferenceEntry(path=Path("ref/c.pdf"), roles=["docs"]),
        ]
        result = append_references(spec, entries)
        assert "- ref/b.png" in result
        assert "- ref/c.pdf" in result

    def test_dedup_existing_path(self):
        spec = "## References\n\n- ref/a.png\n  role: visual-target\n"
        entry = ReferenceEntry(path=Path("ref/a.png"), roles=["docs"])
        result = append_references(spec, [entry])
        assert result == spec

    def test_dedup_is_path_only_ignores_role(self):
        """Same path with a different role still deduplicates."""
        spec = "## References\n\n- ref/a.png\n  role: visual-target\n"
        entry = ReferenceEntry(
            path=Path("ref/a.png"),
            roles=["behavioral-target", "docs"],
        )
        result = append_references(spec, [entry])
        assert result == spec

    def test_dedup_trailing_slash(self):
        spec = "## References\n\n- ref/dir\n  role: docs\n"
        entry = ReferenceEntry(path=Path("ref/dir/"), roles=["docs"])
        result = append_references(spec, [entry])
        assert result == spec

    def test_idempotent_double_call(self):
        spec = "## References\n\n- ref/a.png\n  role: visual-target\n"
        entry = ReferenceEntry(path=Path("ref/new.png"), roles=["visual-target"])
        first = append_references(spec, [entry])
        second = append_references(first, [entry])
        assert first == second

    def test_empty_new_entries_returns_unchanged(self):
        spec = "## References\n\n- ref/a.png\n  role: visual-target\n"
        result = append_references(spec, [])
        assert result == spec

    def test_missing_references_section_created(self):
        spec = "## Purpose\n\nBuild a calculator.\n"
        entry = ReferenceEntry(path=Path("ref/a.png"), roles=["visual-target"])
        result = append_references(spec, [entry])
        assert "## References" in result
        assert "- ref/a.png" in result
        assert "  role: visual-target" in result

    def test_missing_references_placed_after_sources(self):
        spec = (
            "## Purpose\n\nBuild it.\n\n"
            "## Sources\n\n- https://example.com\n  role: docs\n  scrape: shallow\n"
        )
        entry = ReferenceEntry(path=Path("ref/a.png"), roles=["visual-target"])
        result = append_references(spec, [entry])
        sources_pos = result.index("## Sources")
        references_pos = result.index("## References")
        assert references_pos > sources_pos

    def test_missing_references_placed_after_purpose_when_no_sources(self):
        spec = "## Purpose\n\nBuild a calculator.\n\n## Architecture\n\nSwiftUI.\n"
        entry = ReferenceEntry(path=Path("ref/a.png"), roles=["visual-target"])
        result = append_references(spec, [entry])
        purpose_pos = result.index("## Purpose")
        references_pos = result.index("## References")
        arch_pos = result.index("## Architecture")
        assert purpose_pos < references_pos < arch_pos

    def test_missing_references_at_end_when_no_purpose_or_sources(self):
        spec = "## Architecture\n\nSwiftUI.\n"
        entry = ReferenceEntry(path=Path("ref/a.png"), roles=["visual-target"])
        result = append_references(spec, [entry])
        assert "## References" in result
        assert result.index("## Architecture") < result.index("## References")

    def test_proposed_flag_written(self):
        spec = "## References\n\n"
        entry = ReferenceEntry(
            path=Path("ref/new.png"),
            roles=["visual-target"],
            proposed=True,
        )
        result = append_references(spec, [entry])
        assert "  proposed: true" in result

    def test_no_proposed_when_false(self):
        spec = "## References\n\n"
        entry = ReferenceEntry(path=Path("ref/a.png"), roles=["visual-target"])
        result = append_references(spec, [entry])
        assert "proposed" not in result

    def test_multiple_roles_serialized_comma_separated(self):
        spec = "## References\n\n"
        entry = ReferenceEntry(
            path=Path("ref/a.png"),
            roles=["visual-target", "behavioral-target"],
        )
        result = append_references(spec, [entry])
        assert "  role: visual-target, behavioral-target" in result

    def test_notes_included(self):
        spec = "## References\n\n"
        entry = ReferenceEntry(
            path=Path("ref/a.png"),
            roles=["visual-target"],
            notes="main hero shot",
        )
        result = append_references(spec, [entry])
        assert "  notes: main hero shot" in result


class TestUpdateDesignAutogen:
    """Tests for update_design_autogen."""

    def test_empty_design_gets_autogen_block(self):
        spec = "## Purpose\n\nCalc.\n\n## Design\n\n"
        result = update_design_autogen(spec, "Colors: blue")
        assert "<!-- BEGIN AUTO-GENERATED" in result
        assert "Colors: blue" in result
        assert "<!-- END AUTO-GENERATED -->" in result

    def test_existing_user_prose_preserved(self):
        spec = "## Design\n\nKeep it minimal and clean.\n"
        result = update_design_autogen(spec, "Colors: red")
        assert "Keep it minimal and clean." in result
        assert "Colors: red" in result
        # User prose should come before the autogen block.
        prose_pos = result.index("Keep it minimal")
        auto_pos = result.index("BEGIN AUTO-GENERATED")
        assert prose_pos < auto_pos

    def test_existing_autogen_nonempty_not_replaced(self):
        """Write-once: non-empty autogen block is preserved."""
        spec = (
            "## Design\n\n"
            "<!-- BEGIN AUTO-GENERATED design-requirements -->\n"
            "Old content\n"
            "<!-- END AUTO-GENERATED -->\n"
        )
        result = update_design_autogen(spec, "New content")
        assert "Old content" in result
        assert "New content" not in result

    def test_existing_autogen_empty_is_replaced(self):
        """Empty autogen block allows regeneration."""
        spec = "## Design\n\n<!-- BEGIN AUTO-GENERATED -->\n\n<!-- END AUTO-GENERATED -->\n"
        result = update_design_autogen(spec, "Fresh content")
        assert "Fresh content" in result

    def test_missing_design_section_created(self):
        spec = "## Purpose\n\nBuild a calculator.\n"
        result = update_design_autogen(spec, "Fonts: sans-serif")
        assert "## Design" in result
        assert "Fonts: sans-serif" in result

    def test_round_trip_through_parser(self):
        """Output parses back to spec.design.auto_generated == body."""
        spec = "## Purpose\n\nCalc.\n\n## Design\n\nUser notes.\n"
        body = "Colors: blue\nFonts: monospace"
        result = update_design_autogen(spec, body)
        parsed = _parse_spec(result)
        assert parsed.design.auto_generated == body

    def test_missing_design_placed_after_sources(self):
        spec = (
            "## Architecture\n\nSwift.\n\n"
            "## Sources\n\n"
            "- https://example.com\n"
            "  role: product-reference\n"
            "  scrape: deep\n"
        )
        result = update_design_autogen(spec, "Layout: grid")
        sources_pos = result.index("## Sources")
        design_pos = result.index("## Design")
        assert design_pos > sources_pos

    def test_missing_design_placed_after_architecture_no_sources(self):
        spec = "## Architecture\n\nSwift.\n"
        result = update_design_autogen(spec, "Layout: grid")
        arch_pos = result.index("## Architecture")
        design_pos = result.index("## Design")
        assert design_pos > arch_pos

    def test_missing_design_at_end_without_arch_or_sources(self):
        spec = "## Purpose\n\nCalc.\n"
        result = update_design_autogen(spec, "Layout: grid")
        purpose_pos = result.index("## Purpose")
        design_pos = result.index("## Design")
        assert design_pos > purpose_pos

    def test_autogen_block_with_whitespace_only_is_replaced(self):
        """Block containing only whitespace counts as empty."""
        spec = "## Design\n\n<!-- BEGIN AUTO-GENERATED -->\n   \n<!-- END AUTO-GENERATED -->\n"
        result = update_design_autogen(spec, "New stuff")
        assert "New stuff" in result

    def test_preserves_content_after_design_section(self):
        spec = "## Design\n\nProse.\n\n## References\n\n- ref/img.png\n  role: visual-target\n"
        result = update_design_autogen(spec, "Colors: green")
        assert "## References" in result
        assert "ref/img.png" in result
        assert "Colors: green" in result


class TestFormatSpec:
    """Tests for format_spec."""

    def test_empty_spec_has_top_matter(self):
        result = format_spec(ProductSpec())
        assert result.startswith("# SPEC\n")
        assert "How the pieces fit together:" in result

    def test_empty_spec_has_fill_in_on_required_sections(self):
        result = format_spec(ProductSpec())
        assert "<FILL IN: one or two sentences describing what you're building>" in result
        assert "<FILL IN: language, framework, platform, constraints>" in result

    def test_required_fill_in_markers_match_template(self):
        """FILL IN markers for empty Purpose/Architecture must match SPEC-template.md."""
        template = (Path(__file__).resolve().parent.parent / "SPEC-template.md").read_text()
        result = format_spec(ProductSpec())
        # Pull each required section's body out of the template and
        # confirm format_spec emits the exact same FILL IN line.
        for heading in ("## Purpose", "## Architecture"):
            t_start = template.index(heading) + len(heading)
            t_end = template.index("## ", t_start)
            template_marker = next(
                line.strip()
                for line in template[t_start:t_end].splitlines()
                if line.strip().startswith("<FILL IN")
            )
            assert template_marker in result

    def test_empty_spec_has_comment_hints_on_optional_sections(self):
        result = format_spec(ProductSpec())
        assert "URLs duplo should scrape" in result
        assert "Files in ref/" in result
        assert "Optional if ## References has visual-target files." in result
        assert "Optional. Overrides for include/exclude." in result
        assert "Input → output pairs become verification tasks." in result
        assert "Optional. Free-form context for duplo." in result

    def test_optional_comment_hints_match_template(self):
        """Leading comment hint for empty optional sections must come from SPEC-template.md verbatim."""
        import re as _re

        template = (Path(__file__).resolve().parent.parent / "SPEC-template.md").read_text()
        result = format_spec(ProductSpec())
        # For each optional section, pull the first <!-- ... --> block
        # from the template's section body and confirm format_spec emits it.
        for heading in ("## Design", "## Scope", "## Behavior", "## Notes"):
            t_start = template.index(f"{heading}\n") + len(heading)
            next_hdr = _re.search(r"^## ", template[t_start:], _re.MULTILINE)
            t_end = t_start + next_hdr.start() if next_hdr else len(template)
            body = template[t_start:t_end]
            open_at = body.index("<!--")
            close_at = body.index("-->", open_at) + len("-->")
            first_comment = body[open_at:close_at]
            assert first_comment in result, f"{heading} comment hint not found in output"

    def test_empty_spec_does_not_have_fill_in_on_optional_sections(self):
        result = format_spec(ProductSpec())
        # Only Purpose and Architecture get FILL IN markers. The top
        # matter references "<FILL IN>" literally too, so slice it off
        # before counting section-level markers.
        body = result.split("-->", 1)[1]
        assert body.count("<FILL IN") == 2

    def test_section_order_is_canonical(self):
        result = format_spec(ProductSpec())
        # The top matter comment mentions "## Sources" and "## References"
        # verbatim, so skip past it before searching for the actual
        # section headings.
        body = result.split("-->", 1)[1]
        positions = [
            body.index("## Purpose"),
            body.index("## Sources"),
            body.index("## References"),
            body.index("## Architecture"),
            body.index("## Design"),
            body.index("## Scope"),
            body.index("## Behavior"),
            body.index("## Notes"),
        ]
        assert positions == sorted(positions)

    def test_filled_purpose_has_content_no_fill_in(self):
        spec = ProductSpec(purpose="A text calculator for macOS.")
        result = format_spec(spec)
        assert "A text calculator for macOS." in result
        # The Purpose FILL IN marker should be gone.
        assert "<FILL IN: one or two sentences" not in result

    def test_filled_architecture_has_content_no_fill_in(self):
        spec = ProductSpec(architecture="SwiftUI on macOS 14+.")
        result = format_spec(spec)
        assert "SwiftUI on macOS 14+." in result
        assert "<FILL IN: language, framework" not in result

    def test_sources_rendered_with_flags(self):
        spec = ProductSpec(
            sources=[
                SourceEntry(
                    url="https://numi.app",
                    role="product-reference",
                    scrape="deep",
                    notes="main site",
                ),
                SourceEntry(
                    url="https://example.com",
                    role="docs",
                    scrape="shallow",
                    proposed=True,
                ),
                SourceEntry(
                    url="https://crawled.com",
                    role="docs",
                    scrape="none",
                    discovered=True,
                ),
            ]
        )
        result = format_spec(spec)
        assert "- https://numi.app" in result
        assert "  role: product-reference" in result
        assert "  scrape: deep" in result
        assert "  notes: main site" in result
        assert "  proposed: true" in result
        assert "  discovered: true" in result
        # No Sources comment hint when sources are present.
        assert "URLs duplo should scrape" not in result

    def test_sources_entries_separated_by_blank_line(self):
        spec = ProductSpec(
            sources=[
                SourceEntry(url="https://a.com", role="docs", scrape="none"),
                SourceEntry(url="https://b.com", role="docs", scrape="none"),
            ]
        )
        result = format_spec(spec)
        # Two entries, separated by a blank line.
        assert "- https://a.com\n  role: docs\n  scrape: none\n\n- https://b.com" in result

    def test_references_rendered_with_multiple_roles(self):
        spec = ProductSpec(
            references=[
                ReferenceEntry(
                    path=Path("ref/main.png"),
                    roles=["visual-target", "docs"],
                    notes="primary view",
                    proposed=True,
                ),
            ]
        )
        result = format_spec(spec)
        assert "- ref/main.png" in result
        assert "  role: visual-target, docs" in result
        assert "  notes: primary view" in result
        assert "  proposed: true" in result
        assert "Files in ref/" not in result

    def test_references_entries_separated_by_blank_line(self):
        spec = ProductSpec(
            references=[
                ReferenceEntry(path=Path("ref/a.png"), roles=["visual-target"]),
                ReferenceEntry(path=Path("ref/b.png"), roles=["docs"]),
            ]
        )
        result = format_spec(spec)
        assert "- ref/a.png\n  role: visual-target\n\n- ref/b.png" in result

    def test_design_with_user_prose_only(self):
        spec = ProductSpec(design=DesignBlock(user_prose="Calm, neutral palette."))
        result = format_spec(spec)
        assert "Calm, neutral palette." in result
        assert "Optional if ## References has visual-target files." not in result
        assert "BEGIN AUTO-GENERATED" not in result

    def test_design_with_autogenerated_only(self):
        spec = ProductSpec(design=DesignBlock(auto_generated="Colors: #fff on #000"))
        result = format_spec(spec)
        assert "BEGIN AUTO-GENERATED" in result
        assert "Colors: #fff on #000" in result
        assert "END AUTO-GENERATED" in result

    def test_design_with_both_prose_and_autogenerated(self):
        spec = ProductSpec(
            design=DesignBlock(
                user_prose="Follow the brand guide.",
                auto_generated="Extracted palette: teal on ivory.",
            )
        )
        result = format_spec(spec)
        # user_prose comes before the AUTO-GENERATED block.
        prose_pos = result.index("Follow the brand guide.")
        begin_pos = result.index("BEGIN AUTO-GENERATED")
        auto_pos = result.index("Extracted palette: teal on ivory.")
        assert prose_pos < begin_pos < auto_pos

    def test_scope_include_exclude_rendered(self):
        spec = ProductSpec(
            scope_include=["arithmetic", "unit conversion"],
            scope_exclude=["plugin API"],
        )
        result = format_spec(spec)
        assert "include:\n  - arithmetic\n  - unit conversion" in result
        assert "exclude:\n  - plugin API" in result
        assert "Optional. Overrides for include/exclude." not in result

    def test_behavior_contracts_rendered(self):
        spec = ProductSpec(
            behavior_contracts=[
                BehaviorContract(input="2 + 3", expected="5"),
                BehaviorContract(input="5 km in miles", expected="3.11 mi"),
            ]
        )
        result = format_spec(spec)
        assert "- `2 + 3` → `5`" in result
        assert "- `5 km in miles` → `3.11 mi`" in result
        assert "Input → output pairs become verification tasks." not in result

    def test_notes_rendered_verbatim(self):
        spec = ProductSpec(notes="Original description provided to duplo init:\n\nhello")
        result = format_spec(spec)
        assert "Original description provided to duplo init:\n\nhello" in result
        assert "Optional. Free-form context for duplo." not in result

    def test_empty_optional_sections_keep_comment_hint(self):
        # Purpose is filled but everything else is empty.
        spec = ProductSpec(purpose="A thing.", architecture="Some stack.")
        result = format_spec(spec)
        # Optional hints still present.
        assert "URLs duplo should scrape" in result
        assert "Files in ref/" in result
        assert "Optional. Free-form context for duplo." in result

    def test_output_ends_with_newline(self):
        assert format_spec(ProductSpec()).endswith("\n")

    def test_fully_populated_spec_serializes_all_sections(self):
        """Every section's content is present in the output when all fields are filled."""
        spec = ProductSpec(
            purpose="A text calculator for macOS.",
            architecture="SwiftUI on macOS 14+.",
            sources=[
                SourceEntry(
                    url="https://numi.app",
                    role="product-reference",
                    scrape="deep",
                    notes="main site",
                ),
            ],
            references=[
                ReferenceEntry(
                    path=Path("ref/main.png"),
                    roles=["visual-target"],
                    notes="primary view",
                ),
            ],
            design=DesignBlock(
                user_prose="Follow the brand guide.",
                auto_generated="Extracted palette: teal on ivory.",
            ),
            scope_include=["arithmetic"],
            scope_exclude=["plugin API"],
            behavior_contracts=[BehaviorContract(input="2 + 3", expected="5")],
            notes="Free-form context.",
        )
        result = format_spec(spec)
        # Required sections filled.
        assert "A text calculator for macOS." in result
        assert "SwiftUI on macOS 14+." in result
        # Sources.
        assert "- https://numi.app" in result
        assert "  role: product-reference" in result
        assert "  scrape: deep" in result
        assert "  notes: main site" in result
        # References.
        assert "- ref/main.png" in result
        assert "  role: visual-target" in result
        assert "  notes: primary view" in result
        # Design: user_prose before auto_generated.
        assert "Follow the brand guide." in result
        assert "BEGIN AUTO-GENERATED" in result
        assert "Extracted palette: teal on ivory." in result
        # Scope.
        assert "include:\n  - arithmetic" in result
        assert "exclude:\n  - plugin API" in result
        # Behavior.
        assert "- `2 + 3` → `5`" in result
        # Notes.
        assert "Free-form context." in result
        # No FILL IN markers remain after the top matter.
        body = result.split("-->", 1)[1]
        assert "<FILL IN" not in body
        # No optional-section comment hints when content is present.
        assert "URLs duplo should scrape" not in result
        assert "Files in ref/" not in result
        assert "Optional if ## References has visual-target files." not in result
        assert "Optional. Overrides for include/exclude." not in result
        assert "Input → output pairs become verification tasks." not in result
        assert "Optional. Free-form context for duplo." not in result

    def test_output_parses_back_to_a_productspec(self):
        """Smoke test: the output of format_spec is valid SPEC.md text."""
        spec = ProductSpec(
            purpose="A calculator.",
            architecture="SwiftUI.",
            sources=[SourceEntry(url="https://a.com", role="docs", scrape="none")],
            references=[
                ReferenceEntry(path=Path("ref/a.png"), roles=["visual-target"]),
            ],
            scope_include=["math"],
            scope_exclude=["plugins"],
            behavior_contracts=[BehaviorContract(input="1+1", expected="2")],
            notes="Some context.",
            design=DesignBlock(user_prose="Minimal."),
        )
        text = format_spec(spec)
        parsed = _parse_spec(text)
        assert parsed.purpose == "A calculator."
        assert parsed.architecture == "SwiftUI."
        assert len(parsed.sources) == 1
        assert parsed.sources[0].url == "https://a.com"
        assert len(parsed.references) == 1
        assert parsed.references[0].path == Path("ref/a.png")
        assert parsed.scope_include == ["math"]
        assert parsed.scope_exclude == ["plugins"]
        assert len(parsed.behavior_contracts) == 1
        assert parsed.behavior_contracts[0].input == "1+1"
        assert parsed.notes == "Some context."
        assert parsed.design.user_prose == "Minimal."


# --------------------------------------------------------------------------
# Round-trip property tests
# --------------------------------------------------------------------------
#
# Property: parse(format_spec(spec)) == spec for all surviving fields.
# Per DRAFTER-design.md section "Round-trip testing".

_ROUND_TRIP_EXCLUDED_FIELDS = {
    # Listed in DRAFTER-design.md:
    "raw",
    "dropped_sources",
    "dropped_references",
    # Additional derived fields that do not survive round-tripping: scope
    # and behavior hold the raw section body, which the parser always
    # populates from the serialized content regardless of the original's
    # value. The fill_in_* flags are parser-set and depend on whether
    # <FILL IN> markers appear in the serialized body.
    "scope",
    "behavior",
    "fill_in_purpose",
    "fill_in_architecture",
    "fill_in_design",
}


def _design_blocks_equal_for_round_trip(a: DesignBlock, b: DesignBlock) -> bool:
    """Compare DesignBlock semantic fields (ignores parser-only has_fill_in_marker)."""
    return a.user_prose == b.user_prose and a.auto_generated == b.auto_generated


def _spec_equal_for_round_trip(a: ProductSpec, b: ProductSpec) -> bool:
    """Compare ProductSpecs ignoring fields that do not survive round-tripping."""
    for f in dataclasses.fields(ProductSpec):
        if f.name in _ROUND_TRIP_EXCLUDED_FIELDS:
            continue
        av = getattr(a, f.name)
        bv = getattr(b, f.name)
        if f.name == "design":
            if not _design_blocks_equal_for_round_trip(av, bv):
                return False
        else:
            if av != bv:
                return False
    return True


def _well_formed_sources_and_refs() -> tuple[list[SourceEntry], list[ReferenceEntry]]:
    """A pair of non-empty Sources/References lists.

    Used to avoid the parser picking up example entries from the HTML
    comment hints that format_spec emits when these sections are empty.
    """
    return (
        [SourceEntry(url="https://baseline.example", role="docs", scrape="none")],
        [ReferenceEntry(path=Path("ref/baseline.png"), roles=["visual-target"])],
    )


def _fixture_empty() -> ProductSpec:
    """Truly empty spec. Does NOT round-trip cleanly: see NOTES.md [6.3.1].
    Used only by ``test_empty_spec_round_trip_pins_documented_behavior`` —
    excluded from the standard round-trip parametrization.
    """
    return ProductSpec()


def _fixture_minimal() -> ProductSpec:
    """Minimally filled spec: required sections populated plus one entry each
    in sections whose comment hints would otherwise be re-parsed as content.
    """
    sources, refs = _well_formed_sources_and_refs()
    return ProductSpec(
        purpose="A minimal calculator.",
        architecture="Python 3.11 CLI.",
        sources=sources,
        references=refs,
        scope_include=["math"],
        behavior_contracts=[BehaviorContract(input="1+1", expected="2")],
    )


def _fixture_all_sections_filled() -> ProductSpec:
    return ProductSpec(
        purpose="A full-featured calculator for macOS.",
        architecture="SwiftUI targeting macOS 14+.",
        sources=[
            SourceEntry(
                url="https://numi.app",
                role="product-reference",
                scrape="deep",
                notes="main site",
            ),
            SourceEntry(
                url="https://docs.example",
                role="docs",
                scrape="shallow",
            ),
        ],
        references=[
            ReferenceEntry(
                path=Path("ref/main.png"),
                roles=["visual-target"],
                notes="primary view",
            ),
            ReferenceEntry(
                path=Path("ref/demo.mp4"),
                roles=["behavioral-target"],
            ),
        ],
        design=DesignBlock(
            user_prose="Follow the brand guide.",
            auto_generated="Extracted palette: teal on ivory.",
        ),
        scope_include=["arithmetic", "unit conversion"],
        scope_exclude=["plugin API"],
        behavior_contracts=[
            BehaviorContract(input="2 + 3", expected="5"),
            BehaviorContract(input="5 km in miles", expected="3.11 mi"),
        ],
        notes="Free-form context for duplo.",
    )


def _fixture_mixed_filled_empty() -> ProductSpec:
    """Required sections plus Sources/References/Scope/Behavior filled;
    Design and Notes left empty.
    """
    sources, refs = _well_formed_sources_and_refs()
    return ProductSpec(
        purpose="A middleweight spec.",
        architecture="Language agnostic.",
        sources=sources,
        references=refs,
        scope_include=["feature-a"],
        behavior_contracts=[BehaviorContract(input="x", expected="y")],
    )


def _fixture_sources_with_flags() -> ProductSpec:
    _, refs = _well_formed_sources_and_refs()
    return ProductSpec(
        purpose="Sources flag variants.",
        architecture="Agnostic.",
        sources=[
            SourceEntry(url="https://plain.example", role="docs", scrape="none"),
            SourceEntry(
                url="https://proposed.example",
                role="docs",
                scrape="shallow",
                proposed=True,
            ),
            SourceEntry(
                url="https://discovered.example",
                role="docs",
                scrape="none",
                discovered=True,
            ),
            SourceEntry(
                url="https://counter.example",
                role="counter-example",
                scrape="none",
                notes="avoid this layout",
            ),
        ],
        references=refs,
        scope_include=["x"],
        behavior_contracts=[BehaviorContract(input="a", expected="b")],
    )


def _fixture_references_multi_role_and_proposed() -> ProductSpec:
    sources, _ = _well_formed_sources_and_refs()
    return ProductSpec(
        purpose="Reference variants.",
        architecture="Agnostic.",
        sources=sources,
        references=[
            ReferenceEntry(
                path=Path("ref/main.png"),
                roles=["visual-target", "docs"],
                notes="primary + docs",
                proposed=True,
            ),
            ReferenceEntry(
                path=Path("ref/spec.pdf"),
                roles=["docs"],
            ),
        ],
        scope_include=["x"],
        behavior_contracts=[BehaviorContract(input="a", expected="b")],
    )


def _fixture_design_prose_and_autogen() -> ProductSpec:
    sources, refs = _well_formed_sources_and_refs()
    return ProductSpec(
        purpose="Design variants.",
        architecture="Agnostic.",
        sources=sources,
        references=refs,
        design=DesignBlock(
            user_prose="Neutral palette; generous whitespace.",
            auto_generated="Colors: #111 on #fafafa. Font: Inter.",
        ),
        scope_include=["x"],
        behavior_contracts=[BehaviorContract(input="a", expected="b")],
    )


def _fixture_scope_include_and_exclude() -> ProductSpec:
    sources, refs = _well_formed_sources_and_refs()
    return ProductSpec(
        purpose="Scope variants.",
        architecture="Agnostic.",
        sources=sources,
        references=refs,
        scope_include=["arithmetic", "variables", "unit conversion"],
        scope_exclude=["plugin API", "scripting"],
        behavior_contracts=[BehaviorContract(input="a", expected="b")],
    )


def _fixture_multiple_behavior_contracts() -> ProductSpec:
    sources, refs = _well_formed_sources_and_refs()
    return ProductSpec(
        purpose="Behavior variants.",
        architecture="Agnostic.",
        sources=sources,
        references=refs,
        scope_include=["x"],
        behavior_contracts=[
            BehaviorContract(input="2 + 3", expected="5"),
            BehaviorContract(input="10 * 4", expected="40"),
            BehaviorContract(input="5 km in miles", expected="3.11 mi"),
        ],
    )


_ROUND_TRIP_FIXTURES: list[tuple[str, ProductSpec]] = [
    ("minimal", _fixture_minimal()),
    ("all_sections_filled", _fixture_all_sections_filled()),
    ("mixed_filled_empty", _fixture_mixed_filled_empty()),
    ("sources_with_flags", _fixture_sources_with_flags()),
    ("references_multi_role_and_proposed", _fixture_references_multi_role_and_proposed()),
    ("design_prose_and_autogen", _fixture_design_prose_and_autogen()),
    ("scope_include_and_exclude", _fixture_scope_include_and_exclude()),
    ("multiple_behavior_contracts", _fixture_multiple_behavior_contracts()),
]


class TestRoundTrip:
    """parse(format_spec(spec)) == spec for all surviving fields."""

    @pytest.mark.parametrize(
        "spec",
        [f[1] for f in _ROUND_TRIP_FIXTURES],
        ids=[f[0] for f in _ROUND_TRIP_FIXTURES],
    )
    def test_round_trip_preserves_surviving_fields(self, spec: ProductSpec):
        serialized = format_spec(spec)
        parsed = _parse_spec(serialized)
        assert _spec_equal_for_round_trip(parsed, spec), (
            f"round-trip mismatch; parsed=\n{parsed}\n\nexpected=\n{spec}"
        )

    def test_comparator_excludes_raw_dropped_sources_dropped_references(self):
        """The comparator ignores raw, dropped_sources, and dropped_references."""
        a = ProductSpec(
            purpose="x",
            architecture="y",
            raw="one",
            dropped_sources=[SourceEntry(url="https://bad", role="", scrape="")],
            dropped_references=[ReferenceEntry(path=Path("ref/bad"), roles=[])],
        )
        b = ProductSpec(
            purpose="x",
            architecture="y",
            raw="different",
            dropped_sources=[],
            dropped_references=[],
        )
        assert _spec_equal_for_round_trip(a, b)

    def test_comparator_detects_difference_in_surviving_field(self):
        a = ProductSpec(purpose="x", architecture="y")
        b = ProductSpec(purpose="x", architecture="z")
        assert not _spec_equal_for_round_trip(a, b)

    def test_empty_spec_round_trip_pins_documented_behavior(self):
        """Truly empty ProductSpec does not round-trip identically: the
        parser picks up example content from format_spec's comment hints,
        and FILL IN markers survive as Purpose/Architecture text. Pins
        the current behavior documented in NOTES.md [6.3.1]; if the
        parser is later updated to comment-strip those section bodies,
        this test will fail loudly to flag the behavior change.
        """
        spec = _fixture_empty()
        serialized = format_spec(spec)
        parsed = _parse_spec(serialized)
        assert "FILL IN" in parsed.purpose
        assert "FILL IN" in parsed.architecture
        assert parsed.fill_in_purpose is True
        assert parsed.fill_in_architecture is True
        assert parsed.sources, "parser currently picks up example SourceEntry from comment hints"
        assert parsed.references, (
            "parser currently picks up example ReferenceEntry from comment hints"
        )
        assert parsed.scope_include, (
            "parser currently picks up example scope items from comment hints"
        )
        assert parsed.behavior_contracts, (
            "parser currently picks up example behavior contracts from comment hints"
        )
        assert not _spec_equal_for_round_trip(parsed, spec)

    def test_dropped_fields_round_trip_as_empty(self):
        """Documenting the asymmetry per DRAFTER-design.md § "Round-trip testing":
        ``dropped_*`` are parser-only (entries the parser rejected at read time)
        and ``format_spec`` does not serialize them. A round-tripped spec
        therefore always has empty ``dropped_*`` lists regardless of what the
        original contained — populated ``dropped_*`` on the source side and
        empty ``dropped_*`` on the parsed side compare equal under
        ``_spec_equal_for_round_trip``.
        """
        spec = _fixture_all_sections_filled()
        spec = dataclasses.replace(
            spec,
            dropped_sources=[
                SourceEntry(url="https://no-role.example", role="", scrape=""),
                SourceEntry(url="https://bad-scrape.example", role="docs", scrape="wobble"),
            ],
            dropped_references=[
                ReferenceEntry(path=Path("ref/no-role.png"), roles=[]),
                ReferenceEntry(path=Path("ref/bad-role.png"), roles=["bogus-role"]),
            ],
        )
        assert spec.dropped_sources, "precondition: source has populated dropped_sources"
        assert spec.dropped_references, "precondition: source has populated dropped_references"
        serialized = format_spec(spec)
        parsed = _parse_spec(serialized)
        # Asymmetry: source had entries; parsed side is always empty.
        assert parsed.dropped_sources == []
        assert parsed.dropped_references == []
        # And the comparator treats that as equal for round-trip purposes.
        assert _spec_equal_for_round_trip(parsed, spec)

    def test_dropped_fields_asymmetry_on_minimal_spec(self):
        """Isolates the asymmetry from other section content: even when the
        only difference between the source spec and an otherwise-identical
        spec is populated ``dropped_*``, format_spec's output is identical
        and the parser produces empty ``dropped_*`` on the round-trip.
        """
        base = _fixture_minimal()
        with_dropped = dataclasses.replace(
            base,
            dropped_sources=[SourceEntry(url="https://dropped.example", role="", scrape="")],
            dropped_references=[ReferenceEntry(path=Path("ref/dropped.png"), roles=[])],
        )
        # format_spec ignores dropped_* — the two specs serialize identically.
        assert format_spec(base) == format_spec(with_dropped)
        parsed = _parse_spec(format_spec(with_dropped))
        assert parsed.dropped_sources == []
        assert parsed.dropped_references == []


class TestInferUrlRole:
    """Tests for ``_infer_url_role`` (regex-based role inference)."""

    def test_like_returns_product_reference(self):
        assert _infer_url_role("like numi at https://numi.app") == "product-reference"

    def test_such_as_returns_product_reference(self):
        assert _infer_url_role("a calculator such as https://numi.app") == "product-reference"

    def test_inspired_by_returns_product_reference(self):
        assert _infer_url_role("inspired by https://numi.app") == "product-reference"

    def test_see_also_returns_docs(self):
        assert _infer_url_role("see also https://example.com/spec") == "docs"

    def test_for_reference_returns_docs(self):
        assert _infer_url_role("https://example.com/spec for reference") == "docs"

    def test_not_like_returns_counter_example(self):
        assert _infer_url_role("not like https://bad.example") == "counter-example"

    def test_unlike_returns_counter_example(self):
        assert _infer_url_role("unlike https://bad.example") == "counter-example"

    def test_avoid_returns_counter_example(self):
        assert _infer_url_role("avoid https://bad.example") == "counter-example"

    def test_default_when_no_pattern_matches(self):
        assert _infer_url_role("check out https://numi.app today") == "product-reference"

    def test_empty_context_returns_default(self):
        assert _infer_url_role("") == "product-reference"

    def test_case_insensitive_like(self):
        assert _infer_url_role("LIKE https://numi.app") == "product-reference"

    def test_case_insensitive_see_also(self):
        assert _infer_url_role("See Also https://example.com") == "docs"

    def test_case_insensitive_unlike(self):
        assert _infer_url_role("UNLIKE https://bad.example") == "counter-example"

    def test_first_match_wins_not_like_before_like(self):
        # ``not like`` starts at 0, plain ``like`` starts at 4 — earliest wins.
        assert _infer_url_role("not like https://bad.example") == "counter-example"

    def test_first_match_wins_earlier_like_beats_later_unlike(self):
        # Two patterns in one context: ``like`` at 0 beats ``unlike`` later.
        assert _infer_url_role("like this, unlike https://x") == "product-reference"

    def test_word_boundary_dislike_does_not_match_like(self):
        # ``like`` inside ``dislike`` must not trigger product-reference.
        assert _infer_url_role("I dislike https://bad.example") == "product-reference"


class TestProposeFileRole:
    """Tests for ``_propose_file_role`` (Vision-based role inference)."""

    def test_image_triggers_vision_and_parses_json(self, monkeypatch, tmp_path):
        calls: list[tuple] = []

        def fake_query(prompt, image_paths, **kwargs):
            calls.append((prompt, list(image_paths)))
            return '{"description": "A dashboard UI", "role": "visual-target"}'

        monkeypatch.setattr("duplo.spec_writer.query_with_images", fake_query)
        path = tmp_path / "dashboard.png"
        path.write_bytes(b"")

        description, role = _propose_file_role(path)

        assert description == "A dashboard UI"
        assert role == "visual-target"
        assert len(calls) == 1
        assert calls[0][1] == [path]

    def test_image_vision_prompt_uses_enum_roles(self, monkeypatch, tmp_path):
        captured_prompt: list[str] = []

        def fake_query(prompt, image_paths, **kwargs):
            captured_prompt.append(prompt)
            return '{"description": "x", "role": "docs"}'

        monkeypatch.setattr("duplo.spec_writer.query_with_images", fake_query)
        path = tmp_path / "diagram.webp"
        path.write_bytes(b"")

        _propose_file_role(path)

        prompt = captured_prompt[0]
        for role in ("visual-target", "behavioral-target", "docs", "counter-example", "ignore"):
            assert role in prompt

    def test_jpg_and_jpeg_treated_as_images(self, monkeypatch, tmp_path):
        call_count = [0]

        def fake_query(prompt, image_paths, **kwargs):
            call_count[0] += 1
            return '{"description": "x", "role": "visual-target"}'

        monkeypatch.setattr("duplo.spec_writer.query_with_images", fake_query)
        for ext in (".jpg", ".jpeg", ".gif"):
            p = tmp_path / f"img{ext}"
            p.write_bytes(b"")
            _, role = _propose_file_role(p)
            assert role == "visual-target"
        assert call_count[0] == 3

    def test_pdf_defaults_to_docs_without_vision(self, monkeypatch, tmp_path):
        def fail_query(*args, **kwargs):
            raise AssertionError("Vision must not be called for PDFs")

        monkeypatch.setattr("duplo.spec_writer.query_with_images", fail_query)
        path = tmp_path / "spec.pdf"
        path.write_bytes(b"")

        description, role = _propose_file_role(path)
        assert description == ""
        assert role == "docs"

    def test_text_defaults_to_docs(self, monkeypatch, tmp_path):
        def fail_query(*args, **kwargs):
            raise AssertionError("Vision must not be called for text")

        monkeypatch.setattr("duplo.spec_writer.query_with_images", fail_query)
        for ext in (".txt", ".md"):
            p = tmp_path / f"readme{ext}"
            p.write_text("hi")
            description, role = _propose_file_role(p)
            assert description == ""
            assert role == "docs"

    def test_video_defaults_to_behavioral_target(self, monkeypatch, tmp_path):
        def fail_query(*args, **kwargs):
            raise AssertionError("Vision must not be called for video")

        monkeypatch.setattr("duplo.spec_writer.query_with_images", fail_query)
        for ext in (".mp4", ".mov", ".webm", ".avi"):
            p = tmp_path / f"clip{ext}"
            p.write_bytes(b"")
            description, role = _propose_file_role(p)
            assert description == ""
            assert role == "behavioral-target"

    def test_unknown_extension_defaults_to_ignore_with_diagnostic(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "mystery.xyz"
        path.write_bytes(b"")

        _, role = _propose_file_role(path)
        assert role == "ignore"
        errors_log = tmp_path / ".duplo" / "errors.jsonl"
        assert errors_log.exists()
        assert "unknown extension" in errors_log.read_text()

    def test_llm_failure_after_retries_falls_back_to_ignore(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        attempts = [0]

        def always_fail(prompt, image_paths, **kwargs):
            attempts[0] += 1
            raise ClaudeCliError("boom")

        sleeps: list[float] = []
        monkeypatch.setattr("duplo.spec_writer.query_with_images", always_fail)
        monkeypatch.setattr("duplo.spec_writer.time.sleep", lambda s: sleeps.append(s))
        path = tmp_path / "x.png"
        path.write_bytes(b"")

        description, role = _propose_file_role(path)
        assert description == ""
        assert role == "ignore"
        # 1 initial attempt + 2 retries = 3 calls.
        assert attempts[0] == 3
        # Backoff slept between attempts (not after the last failure).
        assert len(sleeps) == 2
        assert (tmp_path / ".duplo" / "errors.jsonl").exists()

    def test_llm_retries_then_succeeds(self, monkeypatch, tmp_path):
        attempts = [0]

        def flaky(prompt, image_paths, **kwargs):
            attempts[0] += 1
            if attempts[0] < 3:
                raise ClaudeCliError("transient")
            return '{"description": "ok", "role": "visual-target"}'

        monkeypatch.setattr("duplo.spec_writer.query_with_images", flaky)
        monkeypatch.setattr("duplo.spec_writer.time.sleep", lambda s: None)
        path = tmp_path / "x.png"
        path.write_bytes(b"")

        description, role = _propose_file_role(path)
        assert description == "ok"
        assert role == "visual-target"
        assert attempts[0] == 3

    def test_json_parse_error_falls_back_to_ignore_with_diagnostic(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "duplo.spec_writer.query_with_images",
            lambda prompt, image_paths, **kwargs: "not-json-at-all",
        )
        path = tmp_path / "x.png"
        path.write_bytes(b"")

        description, role = _propose_file_role(path)
        assert description == ""
        assert role == "ignore"
        assert (tmp_path / ".duplo" / "errors.jsonl").exists()

    def test_invalid_role_in_response_falls_back_to_ignore(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "duplo.spec_writer.query_with_images",
            lambda prompt, image_paths, **kwargs: ('{"description": "A logo", "role": "mascot"}'),
        )
        path = tmp_path / "x.png"
        path.write_bytes(b"")

        description, role = _propose_file_role(path)
        # Description preserved even when role is invalid.
        assert description == "A logo"
        assert role == "ignore"
        assert (tmp_path / ".duplo" / "errors.jsonl").exists()

    def test_vision_json_wrapped_in_prose(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "duplo.spec_writer.query_with_images",
            lambda prompt, image_paths, **kwargs: (
                'Here is the analysis:\n{"description": "UI", "role": "visual-target"}'
            ),
        )
        path = tmp_path / "x.png"
        path.write_bytes(b"")

        description, role = _propose_file_role(path)
        assert description == "UI"
        assert role == "visual-target"


class TestDraftInputs:
    """Tests for the ``DraftInputs`` dataclass."""

    def test_all_fields_default_to_empty(self):
        inputs = DraftInputs()
        assert inputs.url is None
        assert inputs.url_scrape is None
        assert inputs.description is None
        assert inputs.existing_ref_files == []
        assert inputs.vision_proposals == {}

    def test_construct_with_all_fields(self, tmp_path):
        p = tmp_path / "ref" / "x.png"
        inputs = DraftInputs(
            url="https://numi.app",
            url_scrape="scraped text",
            description="like numi",
            existing_ref_files=[p],
            vision_proposals={p: "visual-target"},
        )
        assert inputs.url == "https://numi.app"
        assert inputs.url_scrape == "scraped text"
        assert inputs.description == "like numi"
        assert inputs.existing_ref_files == [p]
        assert inputs.vision_proposals == {p: "visual-target"}

    def test_independent_default_lists_between_instances(self):
        """Mutable defaults must not leak across instances."""
        a = DraftInputs()
        a.existing_ref_files.append(Path("ref/x.png"))
        a.vision_proposals[Path("ref/y.png")] = "docs"
        b = DraftInputs()
        assert b.existing_ref_files == []
        assert b.vision_proposals == {}


class TestDraftFromInputs:
    """Tests for ``_draft_from_inputs`` (the single LLM call)."""

    def _mock_query(self, monkeypatch, response: str) -> list[str]:
        """Replace ``duplo.spec_writer.query`` with a capturing stub."""
        captured: list[str] = []

        def fake_query(prompt, **kwargs):
            captured.append(prompt)
            return response

        monkeypatch.setattr("duplo.spec_writer.query", fake_query)
        return captured

    def test_url_only_fills_purpose_leaves_architecture_null(self, monkeypatch):
        self._mock_query(
            monkeypatch,
            json.dumps(
                {
                    "purpose": "A calculator like Numi.",
                    "architecture": None,
                    "design": None,
                    "behavior_contracts": [],
                    "scope_include": [],
                    "scope_exclude": [],
                }
            ),
        )
        inputs = DraftInputs(url="https://numi.app", url_scrape="Numi is a calculator.")

        spec = _draft_from_inputs(inputs)

        assert spec.purpose == "A calculator like Numi."
        assert spec.architecture == ""
        assert spec.design.user_prose == ""
        assert spec.behavior_contracts == []
        assert spec.scope_include == []
        assert spec.scope_exclude == []

    def test_prose_with_stack_fills_architecture(self, monkeypatch):
        self._mock_query(
            monkeypatch,
            json.dumps(
                {
                    "purpose": "A text calculator.",
                    "architecture": "SwiftUI on macOS 14+.",
                    "design": "Monospaced, minimal chrome.",
                    "behavior_contracts": [{"input": "2 + 3", "expected": "5"}],
                    "scope_include": ["Unit conversion"],
                    "scope_exclude": ["Plugin API"],
                }
            ),
        )
        inputs = DraftInputs(description="Build a SwiftUI text calculator like Numi.")

        spec = _draft_from_inputs(inputs)

        assert spec.purpose == "A text calculator."
        assert spec.architecture == "SwiftUI on macOS 14+."
        assert spec.design.user_prose == "Monospaced, minimal chrome."
        assert len(spec.behavior_contracts) == 1
        assert spec.behavior_contracts[0].input == "2 + 3"
        assert spec.behavior_contracts[0].expected == "5"
        assert spec.scope_include == ["Unit conversion"]
        assert spec.scope_exclude == ["Plugin API"]

    def test_prose_without_stack_leaves_architecture_null(self, monkeypatch):
        """When the LLM returns null for architecture, spec.architecture stays empty."""
        self._mock_query(
            monkeypatch,
            json.dumps(
                {
                    "purpose": "A calculator.",
                    "architecture": None,
                    "design": None,
                    "behavior_contracts": [],
                    "scope_include": [],
                    "scope_exclude": [],
                }
            ),
        )
        inputs = DraftInputs(description="Build a calculator. Nothing else.")

        spec = _draft_from_inputs(inputs)

        assert spec.architecture == ""

    def test_both_url_and_prose_are_included_in_prompt(self, monkeypatch):
        captured = self._mock_query(
            monkeypatch,
            '{"purpose": null, "architecture": null, "design": null, '
            '"behavior_contracts": [], "scope_include": [], "scope_exclude": []}',
        )
        inputs = DraftInputs(
            url="https://numi.app",
            url_scrape="SCRAPE_BODY",
            description="PROSE_BODY",
        )

        _draft_from_inputs(inputs)

        prompt = captured[0]
        assert "https://numi.app" in prompt
        assert "SCRAPE_BODY" in prompt
        assert "PROSE_BODY" in prompt

    def test_url_and_prose_merged_response_flows_through(self, monkeypatch):
        """Per INIT-design.md § `duplo init <url> --from-description`:
        URL scrape provides purpose, prose provides design/architecture,
        and prose wins on conflicts. ``_draft_from_inputs`` delegates the
        actual merge to the LLM; this test pins that both inputs reach
        the prompt AND that the merged response flows through to the
        returned ProductSpec intact (so the prose-winning design/arch the
        LLM chose are what callers see).
        """
        captured = self._mock_query(
            monkeypatch,
            json.dumps(
                {
                    # From URL scrape: product identity / base purpose.
                    "purpose": "A text calculator like Numi.",
                    # From prose: explicit stack statement.
                    "architecture": "SwiftUI on macOS 14+.",
                    # Conflict: scrape suggests light theme, prose says
                    # dark — prose wins.
                    "design": "Dark theme, monospaced typography.",
                    "behavior_contracts": [],
                    "scope_include": [],
                    "scope_exclude": [],
                }
            ),
        )
        inputs = DraftInputs(
            url="https://numi.app",
            url_scrape="Numi is a text calculator. Light theme by default.",
            description=("Build a SwiftUI text calculator like Numi, but with a dark theme."),
        )

        spec = _draft_from_inputs(inputs)

        prompt = captured[0]
        assert "Numi is a text calculator" in prompt
        assert "dark theme" in prompt
        # Merged values flow through verbatim.
        assert spec.purpose == "A text calculator like Numi."
        assert spec.architecture == "SwiftUI on macOS 14+."
        assert spec.design.user_prose == "Dark theme, monospaced typography."

    def test_prompt_lists_all_schema_fields(self, monkeypatch):
        captured = self._mock_query(
            monkeypatch,
            '{"purpose": null, "architecture": null, "design": null, '
            '"behavior_contracts": [], "scope_include": [], "scope_exclude": []}',
        )
        _draft_from_inputs(DraftInputs(description="x"))

        prompt = captured[0]
        for field_name in (
            "purpose",
            "architecture",
            "design",
            "behavior_contracts",
            "scope_include",
            "scope_exclude",
        ):
            assert field_name in prompt

    def test_prompt_does_not_request_notes(self, monkeypatch):
        captured = self._mock_query(
            monkeypatch,
            '{"purpose": null, "architecture": null, "design": null, '
            '"behavior_contracts": [], "scope_include": [], "scope_exclude": []}',
        )
        _draft_from_inputs(DraftInputs(description="x"))

        prompt = captured[0]
        # "notes" must not appear as a schema field.
        assert "- notes" not in prompt
        assert "notes:" not in prompt

    def test_prompt_forbids_inferring_architecture_from_url_scrape(self, monkeypatch):
        """Per DRAFTER-design.md and INIT-design.md: architecture is filled
        ONLY when description prose explicitly states a stack/platform/language;
        URL scrapes do NOT inform architecture."""
        captured = self._mock_query(
            monkeypatch,
            '{"purpose": null, "architecture": null, "design": null, '
            '"behavior_contracts": [], "scope_include": [], "scope_exclude": []}',
        )
        _draft_from_inputs(
            DraftInputs(
                url="https://numi.app",
                url_scrape="Numi is a macOS calculator written in Swift.",
                description="Build a calculator.",
            )
        )

        prompt = captured[0].lower()
        # Architecture is gated on description prose only.
        assert "only if" in prompt
        assert "description prose" in prompt
        # URL scrapes / product identity must not inform architecture.
        assert "do not infer architecture" in prompt
        assert "scraped product pages" in prompt
        assert "product identity" in prompt

    def test_neither_url_nor_prose_yields_empty_product_spec(self, monkeypatch):
        self._mock_query(
            monkeypatch,
            json.dumps(
                {
                    "purpose": None,
                    "architecture": None,
                    "design": None,
                    "behavior_contracts": [],
                    "scope_include": [],
                    "scope_exclude": [],
                }
            ),
        )
        spec = _draft_from_inputs(DraftInputs())

        assert spec.purpose == ""
        assert spec.architecture == ""
        assert spec.design.user_prose == ""
        assert spec.behavior_contracts == []

    def test_llm_failure_after_retries_returns_empty_spec(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        attempts = [0]

        def always_fail(prompt, **kwargs):
            attempts[0] += 1
            raise ClaudeCliError("transport error")

        sleeps: list[float] = []
        monkeypatch.setattr("duplo.spec_writer.query", always_fail)
        monkeypatch.setattr("duplo.spec_writer.time.sleep", lambda s: sleeps.append(s))

        spec = _draft_from_inputs(DraftInputs(description="x"))

        # Fallback to an empty ProductSpec.
        assert spec.purpose == ""
        assert spec.architecture == ""
        # Initial attempt + 2 retries = 3 calls.
        assert attempts[0] == 3
        # Slept between attempts, not after the final failure.
        assert len(sleeps) == 2
        assert (tmp_path / ".duplo" / "errors.jsonl").exists()

    def test_malformed_json_retries_then_falls_back(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        attempts = [0]

        def bad_json(prompt, **kwargs):
            attempts[0] += 1
            return "not-json-at-all"

        monkeypatch.setattr("duplo.spec_writer.query", bad_json)
        monkeypatch.setattr("duplo.spec_writer.time.sleep", lambda s: None)

        spec = _draft_from_inputs(DraftInputs(description="x"))

        assert spec.purpose == ""
        assert attempts[0] == 3
        assert (tmp_path / ".duplo" / "errors.jsonl").exists()

    def test_transient_failure_then_success(self, monkeypatch):
        attempts = [0]

        def flaky(prompt, **kwargs):
            attempts[0] += 1
            if attempts[0] < 3:
                raise ClaudeCliError("transient")
            return json.dumps(
                {
                    "purpose": "OK",
                    "architecture": None,
                    "design": None,
                    "behavior_contracts": [],
                    "scope_include": [],
                    "scope_exclude": [],
                }
            )

        monkeypatch.setattr("duplo.spec_writer.query", flaky)
        monkeypatch.setattr("duplo.spec_writer.time.sleep", lambda s: None)

        spec = _draft_from_inputs(DraftInputs(description="x"))

        assert spec.purpose == "OK"
        assert attempts[0] == 3

    def test_all_nulls_produces_template_like_spec(self, monkeypatch):
        """LLM-null-everywhere ⇒ format_spec(spec) shows FILL IN markers."""
        self._mock_query(
            monkeypatch,
            json.dumps(
                {
                    "purpose": None,
                    "architecture": None,
                    "design": None,
                    "behavior_contracts": [],
                    "scope_include": [],
                    "scope_exclude": [],
                }
            ),
        )
        spec = _draft_from_inputs(DraftInputs(description="x"))
        text = format_spec(spec)
        assert "<FILL IN: one or two sentences" in text
        assert "<FILL IN: language, framework" in text

    def test_json_wrapped_in_code_fence_is_parsed(self, monkeypatch):
        self._mock_query(
            monkeypatch,
            "```json\n"
            + json.dumps(
                {
                    "purpose": "Fenced.",
                    "architecture": None,
                    "design": None,
                    "behavior_contracts": [],
                    "scope_include": [],
                    "scope_exclude": [],
                }
            )
            + "\n```",
        )
        spec = _draft_from_inputs(DraftInputs(description="x"))
        assert spec.purpose == "Fenced."

    def test_response_that_is_not_a_json_object_falls_back(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        # A bare array parses but isn't a dict — treat as fallback.
        self._mock_query(monkeypatch, "[1, 2, 3]")

        spec = _draft_from_inputs(DraftInputs(description="x"))
        assert spec.purpose == ""
        assert (tmp_path / ".duplo" / "errors.jsonl").exists()

    def test_behavior_contracts_with_missing_fields_are_dropped(self, monkeypatch):
        self._mock_query(
            monkeypatch,
            json.dumps(
                {
                    "purpose": "OK",
                    "architecture": None,
                    "design": None,
                    "behavior_contracts": [
                        {"input": "2 + 3", "expected": "5"},
                        {"input": "", "expected": "drop me"},
                        {"input": "no expected"},
                        "not a dict",
                    ],
                    "scope_include": [],
                    "scope_exclude": [],
                }
            ),
        )
        spec = _draft_from_inputs(DraftInputs(description="x"))
        assert len(spec.behavior_contracts) == 1
        assert spec.behavior_contracts[0].input == "2 + 3"
