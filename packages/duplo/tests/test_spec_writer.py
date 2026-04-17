"""Tests for duplo.spec_writer."""

from pathlib import Path

from duplo.spec_reader import (
    BehaviorContract,
    DesignBlock,
    ProductSpec,
    ReferenceEntry,
    SourceEntry,
    _parse_spec,
)
from duplo.spec_writer import append_sources, format_spec, update_design_autogen


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
        assert "- include: arithmetic, unit conversion" in result
        assert "- exclude: plugin API" in result
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
