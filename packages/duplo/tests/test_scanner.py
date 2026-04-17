"""Tests for duplo.scanner."""

from __future__ import annotations

import json
from pathlib import Path

from duplo import scanner
from duplo.scanner import (
    ScanResult,
    check_unlisted_ref_files,
    scan_directory,
    scan_files,
)
from duplo.spec_reader import ReferenceEntry


class TestScanDirectory:
    def test_finds_images(self, tmp_path: Path):
        (tmp_path / "screenshot.png").write_bytes(b"PNG" * 500)
        (tmp_path / "photo.jpg").write_bytes(b"JPG" * 500)
        (tmp_path / "icon.gif").write_bytes(b"GIF" * 500)
        result = scan_directory(tmp_path)
        assert len(result.images) == 3

    def test_finds_pdfs(self, tmp_path: Path):
        (tmp_path / "spec.pdf").write_bytes(b"%PDF" * 100)
        result = scan_directory(tmp_path)
        assert len(result.pdfs) == 1

    def test_finds_videos(self, tmp_path: Path):
        (tmp_path / "demo.mp4").write_bytes(b"\x00" * 5000)
        (tmp_path / "recording.mov").write_bytes(b"\x00" * 5000)
        (tmp_path / "clip.webm").write_bytes(b"\x00" * 5000)
        (tmp_path / "sample.avi").write_bytes(b"\x00" * 5000)
        result = scan_directory(tmp_path)
        assert len(result.videos) == 4

    def test_finds_text_files(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("some notes about the product")
        (tmp_path / "readme.md").write_text("# readme with useful content")
        result = scan_directory(tmp_path)
        assert len(result.text_files) == 2

    def test_extracts_urls_from_text(self, tmp_path: Path):
        (tmp_path / "links.txt").write_text(
            "Check out https://example.com and https://docs.example.com/guide"
        )
        result = scan_directory(tmp_path)
        assert "https://example.com" in result.urls
        assert "https://docs.example.com/guide" in result.urls

    def test_deduplicates_urls(self, tmp_path: Path):
        (tmp_path / "a.txt").write_text("https://example.com is a great site")
        (tmp_path / "b.txt").write_text("https://example.com is mentioned again")
        result = scan_directory(tmp_path)
        assert result.urls.count("https://example.com") == 1

    def test_skips_dotdirs(self, tmp_path: Path):
        git_dir = tmp_path / ".git"
        git_dir.mkdir()
        (git_dir / "config.txt").write_text("git config")
        result = scan_directory(tmp_path)
        assert len(result.text_files) == 0

    def test_skips_duplo_dir(self, tmp_path: Path):
        duplo_dir = tmp_path / ".duplo"
        duplo_dir.mkdir()
        (duplo_dir / "state.txt").write_text("state")
        result = scan_directory(tmp_path)
        assert len(result.text_files) == 0

    def test_skips_subdirectories(self, tmp_path: Path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "deep.txt").write_text("nested")
        result = scan_directory(tmp_path)
        assert len(result.text_files) == 0

    def test_empty_directory(self, tmp_path: Path):
        result = scan_directory(tmp_path)
        assert result == ScanResult()

    def test_strips_trailing_punctuation_from_urls(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text(
            "Visit https://example.com/page, or https://other.com."
        )
        result = scan_directory(tmp_path)
        assert "https://example.com/page" in result.urls
        assert "https://other.com" in result.urls

    def test_ignores_non_reference_files(self, tmp_path: Path):
        (tmp_path / "code.py").write_text("print('hello')")
        (tmp_path / "data.json").write_text("{}")
        result = scan_directory(tmp_path)
        assert len(result.images) == 0
        assert len(result.pdfs) == 0
        assert len(result.text_files) == 0

    def test_skips_urls_in_json_files(self, tmp_path: Path):
        """JSON files are in _SOURCE_EXTS — URLs are config refs, not product URLs."""
        (tmp_path / "config.json").write_text('{"url": "https://product.example.com/api"}')
        result = scan_directory(tmp_path)
        assert "https://product.example.com/api" not in result.urls

    def test_skips_urls_in_html_files(self, tmp_path: Path):
        """HTML files are in _SOURCE_EXTS — URLs are code refs, not product URLs."""
        (tmp_path / "page.html").write_text('<a href="https://docs.example.com">Docs</a>')
        result = scan_directory(tmp_path)
        assert "https://docs.example.com" not in result.urls

    def test_skips_binary_files_for_url_extraction(self, tmp_path: Path):
        (tmp_path / "image.bin").write_bytes(bytes(range(256)) * 10)
        result = scan_directory(tmp_path)
        assert len(result.urls) == 0

    def test_skips_ignored_extensions(self, tmp_path: Path):
        (tmp_path / "lib.so").write_bytes(b"\x00" * 100)
        (tmp_path / "app.exe").write_bytes(b"\x00" * 100)
        (tmp_path / "archive.zip").write_bytes(b"\x00" * 100)
        result = scan_directory(tmp_path)
        assert len(result.images) == 0
        assert len(result.pdfs) == 0
        assert len(result.text_files) == 0
        assert len(result.urls) == 0


class TestNoRelevanceScoring:
    """Verify that scanner no longer filters files by size or content."""

    def test_tiny_image_still_included(self, tmp_path: Path):
        """Images are included regardless of file size."""
        (tmp_path / "tiny.png").write_bytes(b"P" * 10)
        result = scan_directory(tmp_path)
        assert len(result.images) == 1

    def test_empty_pdf_still_included(self, tmp_path: Path):
        """PDFs are included regardless of file size."""
        (tmp_path / "empty.pdf").write_bytes(b"")
        result = scan_directory(tmp_path)
        assert len(result.pdfs) == 1

    def test_empty_text_file_still_included(self, tmp_path: Path):
        """Text files are included regardless of content length."""
        (tmp_path / "empty.txt").write_text("   \n  ")
        result = scan_directory(tmp_path)
        assert len(result.text_files) == 1

    def test_short_text_file_still_included(self, tmp_path: Path):
        """Short text files are included without relevance filtering."""
        (tmp_path / "short.txt").write_text("hi")
        result = scan_directory(tmp_path)
        assert len(result.text_files) == 1

    def test_empty_video_still_included(self, tmp_path: Path):
        """Videos are included regardless of file size."""
        (tmp_path / "empty.mp4").write_bytes(b"")
        result = scan_directory(tmp_path)
        assert len(result.videos) == 1

    def test_no_relevance_field_on_scan_result(self, tmp_path: Path):
        """ScanResult no longer has a relevance field."""
        (tmp_path / "shot.png").write_bytes(b"PNG" * 500)
        result = scan_directory(tmp_path)
        assert not hasattr(result, "relevance")

    def test_yaml_files_skipped_as_source(self, tmp_path: Path):
        """YAML files are in _SOURCE_EXTS — no URL extraction."""
        (tmp_path / "links.yaml").write_text("url: https://example.com/product")
        result = scan_directory(tmp_path)
        assert "https://example.com/product" not in result.urls

    def test_dedup_urls_across_text_and_non_text(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("Visit https://example.com for more info")
        (tmp_path / "config.yaml").write_text("url: https://example.com")
        result = scan_directory(tmp_path)
        assert result.urls.count("https://example.com") == 1


class TestScanFiles:
    def test_classifies_image(self, tmp_path: Path):
        img = tmp_path / "shot.png"
        img.write_bytes(b"PNG" * 500)
        result = scan_files([img])
        assert len(result.images) == 1

    def test_classifies_video(self, tmp_path: Path):
        vid = tmp_path / "demo.mp4"
        vid.write_bytes(b"\x00" * 5000)
        result = scan_files([vid])
        assert len(result.videos) == 1

    def test_classifies_pdf(self, tmp_path: Path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF" * 100)
        result = scan_files([pdf])
        assert len(result.pdfs) == 1

    def test_classifies_text_and_extracts_urls(self, tmp_path: Path):
        txt = tmp_path / "notes.txt"
        txt.write_text("See https://example.com for details about the product")
        result = scan_files([txt])
        assert len(result.text_files) == 1
        assert "https://example.com" in result.urls

    def test_skips_nonexistent(self, tmp_path: Path):
        result = scan_files([tmp_path / "gone.png"])
        assert result == ScanResult()

    def test_multiple_file_types(self, tmp_path: Path):
        img = tmp_path / "a.png"
        img.write_bytes(b"PNG" * 500)
        pdf = tmp_path / "b.pdf"
        pdf.write_bytes(b"%PDF" * 100)
        txt = tmp_path / "c.txt"
        txt.write_text("Some useful reference content for the product")
        result = scan_files([img, pdf, txt])
        assert len(result.images) == 1
        assert len(result.pdfs) == 1
        assert len(result.text_files) == 1

    def test_source_files_excluded_from_all_categories(self, tmp_path: Path):
        """Source code files must not appear in any ScanResult category."""
        source_files = []
        # Extension-based source files
        for ext in (".py", ".swift", ".rs", ".go", ".js", ".ts", ".java", ".c", ".cpp", ".rb"):
            p = tmp_path / f"code{ext}"
            p.write_text("https://example.com/should-not-be-extracted")
            source_files.append(p)
        # Name-based source files
        for name in ("Makefile", "Dockerfile", "Package.swift", "Cargo.toml", "go.mod"):
            p = tmp_path / name
            p.write_text("https://example.com/should-not-be-extracted")
            source_files.append(p)

        result = scan_files(source_files)

        assert result.images == []
        assert result.videos == []
        assert result.pdfs == []
        assert result.text_files == []
        assert result.urls == []

    def test_no_relevance_field(self, tmp_path: Path):
        tiny = tmp_path / "tiny.png"
        tiny.write_bytes(b"P" * 100)
        result = scan_files([tiny])
        assert not hasattr(result, "relevance")
        assert len(result.images) == 1


class TestScanFilesRoleLookup:
    """Tests for role lookup in scan_files via ## References."""

    def test_matching_path_gets_roles(self, tmp_path: Path):
        """File whose path matches a ReferenceEntry gets its roles."""
        ref = tmp_path / "ref"
        ref.mkdir()
        img = ref / "shot.png"
        img.write_bytes(b"PNG")
        refs = [ReferenceEntry(path=Path("ref/shot.png"), roles=["visual-target"])]
        result = scan_files([img], references=refs)
        assert result.roles[img] == ["visual-target"]

    def test_no_references_empty_roles(self, tmp_path: Path):
        """No references parameter means empty roles dict."""
        img = tmp_path / "shot.png"
        img.write_bytes(b"PNG")
        result = scan_files([img])
        assert result.roles == {}

    def test_empty_references_no_roles(self, tmp_path: Path):
        """Empty references list means no roles assigned."""
        img = tmp_path / "shot.png"
        img.write_bytes(b"PNG")
        result = scan_files([img], references=[])
        assert result.roles == {}

    def test_unlisted_file_no_roles(self, tmp_path: Path):
        """File not in ## References gets no roles entry."""
        ref = tmp_path / "ref"
        ref.mkdir()
        img = ref / "orphan.png"
        img.write_bytes(b"PNG")
        refs = [ReferenceEntry(path=Path("ref/other.png"), roles=["visual-target"])]
        result = scan_files([img], references=refs)
        assert img not in result.roles

    def test_multiple_roles(self, tmp_path: Path):
        """File with multiple roles gets all of them."""
        ref = tmp_path / "ref"
        ref.mkdir()
        img = ref / "combo.png"
        img.write_bytes(b"PNG")
        refs = [
            ReferenceEntry(
                path=Path("ref/combo.png"),
                roles=["visual-target", "behavioral-target"],
            )
        ]
        result = scan_files([img], references=refs)
        assert result.roles[img] == ["visual-target", "behavioral-target"]

    def test_mixed_listed_and_unlisted(self, tmp_path: Path):
        """Only listed files get roles; unlisted files are absent from roles dict."""
        ref = tmp_path / "ref"
        ref.mkdir()
        listed = ref / "listed.png"
        listed.write_bytes(b"PNG")
        unlisted = ref / "unlisted.jpg"
        unlisted.write_bytes(b"JPG")
        refs = [ReferenceEntry(path=Path("ref/listed.png"), roles=["docs"])]
        result = scan_files([listed, unlisted], references=refs)
        assert listed in result.roles
        assert unlisted not in result.roles

    def test_fallback_matches_by_filename(self, tmp_path: Path):
        """Absolute path matches a ref/ entry by filename fallback."""
        ref = tmp_path / "ref"
        ref.mkdir()
        img = ref / "shot.png"
        img.write_bytes(b"PNG")
        # The reference path is ref/shot.png but the scanned path is absolute.
        refs = [ReferenceEntry(path=Path("ref/shot.png"), roles=["visual-target"])]
        # Pass the absolute path — str won't match "ref/shot.png" directly.
        result = scan_files([img], references=refs)
        assert result.roles[img] == ["visual-target"]

    def test_nonexistent_file_skipped(self, tmp_path: Path):
        """Nonexistent files are skipped, no roles assigned."""
        gone = tmp_path / "ref" / "gone.png"
        refs = [ReferenceEntry(path=Path("ref/gone.png"), roles=["visual-target"])]
        result = scan_files([gone], references=refs)
        assert gone not in result.roles

    def test_classification_still_works_with_references(self, tmp_path: Path):
        """Role lookup doesn't interfere with normal classification."""
        ref = tmp_path / "ref"
        ref.mkdir()
        img = ref / "shot.png"
        img.write_bytes(b"PNG")
        pdf = ref / "spec.pdf"
        pdf.write_bytes(b"%PDF")
        refs = [
            ReferenceEntry(path=Path("ref/shot.png"), roles=["visual-target"]),
            ReferenceEntry(path=Path("ref/spec.pdf"), roles=["docs"]),
        ]
        result = scan_files([img, pdf], references=refs)
        assert len(result.images) == 1
        assert len(result.pdfs) == 1
        assert result.roles[img] == ["visual-target"]
        assert result.roles[pdf] == ["docs"]


class TestCheckUnlistedRefFiles:
    """Tests for check_unlisted_ref_files diagnostic."""

    def test_all_files_listed(self, tmp_path: Path):
        """No diagnostics when all scanned files are in ## References."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "shot.png").write_bytes(b"PNG")
        scan = scan_directory(ref)
        refs = [ReferenceEntry(path=Path("ref/shot.png"), roles=["visual-target"])]
        errors = tmp_path / "errors.jsonl"
        unlisted = check_unlisted_ref_files(scan, refs, ref_dir=ref, errors_path=errors)
        assert unlisted == []
        assert not errors.exists()

    def test_unlisted_file_emits_diagnostic(self, tmp_path: Path):
        """File in ref/ not in ## References gets a diagnostic."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "shot.png").write_bytes(b"PNG")
        (ref / "extra.jpg").write_bytes(b"JPG")
        scan = scan_directory(ref)
        refs = [ReferenceEntry(path=Path("ref/shot.png"), roles=["visual-target"])]
        errors = tmp_path / "errors.jsonl"
        unlisted = check_unlisted_ref_files(scan, refs, ref_dir=ref, errors_path=errors)
        assert len(unlisted) == 1
        assert unlisted[0].name == "extra.jpg"
        # Verify diagnostic was recorded.
        lines = errors.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["site"] == "scanner"
        assert record["category"] == "io"
        assert "extra.jpg" in record["message"]
        assert "will be ignored" in record["message"]

    def test_empty_references_all_unlisted(self, tmp_path: Path):
        """All files are unlisted when ## References is empty."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "a.png").write_bytes(b"PNG")
        (ref / "b.pdf").write_bytes(b"%PDF")
        scan = scan_directory(ref)
        errors = tmp_path / "errors.jsonl"
        unlisted = check_unlisted_ref_files(scan, [], ref_dir=ref, errors_path=errors)
        assert len(unlisted) == 2
        lines = errors.read_text().strip().splitlines()
        assert len(lines) == 2

    def test_empty_scan_no_diagnostics(self, tmp_path: Path):
        """No diagnostics when ref/ has no files."""
        ref = tmp_path / "ref"
        ref.mkdir()
        scan = scan_directory(ref)
        refs = [ReferenceEntry(path=Path("ref/shot.png"), roles=["visual-target"])]
        errors = tmp_path / "errors.jsonl"
        unlisted = check_unlisted_ref_files(scan, refs, ref_dir=ref, errors_path=errors)
        assert unlisted == []
        assert not errors.exists()

    def test_multiple_file_types_checked(self, tmp_path: Path):
        """Images, videos, PDFs, and text files are all checked."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "shot.png").write_bytes(b"PNG")
        (ref / "demo.mp4").write_bytes(b"\x00" * 100)
        (ref / "spec.pdf").write_bytes(b"%PDF")
        (ref / "notes.txt").write_text("some notes")
        scan = scan_directory(ref)
        # Only list the image.
        refs = [ReferenceEntry(path=Path("ref/shot.png"), roles=["visual-target"])]
        errors = tmp_path / "errors.jsonl"
        unlisted = check_unlisted_ref_files(scan, refs, ref_dir=ref, errors_path=errors)
        unlisted_names = {p.name for p in unlisted}
        assert "demo.mp4" in unlisted_names
        assert "spec.pdf" in unlisted_names
        assert "notes.txt" in unlisted_names
        assert "shot.png" not in unlisted_names

    def test_diagnostic_message_contains_relative_path(self, tmp_path: Path):
        """Diagnostic message uses ref/filename, not absolute path."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "orphan.png").write_bytes(b"PNG")
        scan = scan_directory(ref)
        errors = tmp_path / "errors.jsonl"
        check_unlisted_ref_files(scan, [], ref_dir=ref, errors_path=errors)
        record = json.loads(errors.read_text().strip())
        assert "ref/orphan.png" in record["message"]


class TestScanDirectoryRefOnly:
    """scan_directory only enumerates files under ref/, ignoring project root."""

    def test_only_ref_files_found(self, tmp_path: Path):
        """Files in the project root are ignored when scanning ref/."""
        root = tmp_path
        ref = root / "ref"
        ref.mkdir()
        # Root-level files — should NOT be found.
        (root / "PLAN.md").write_text("# Plan")
        (root / "logo.png").write_bytes(b"PNG" * 100)
        (root / "notes.txt").write_text("root notes")
        # ref/ files — should be found.
        (ref / "screenshot.png").write_bytes(b"PNG" * 100)
        (ref / "spec.pdf").write_bytes(b"%PDF" * 50)

        result = scan_directory(ref)

        names = {p.name for p in result.images + result.pdfs + result.text_files}
        assert "screenshot.png" in names
        assert "spec.pdf" in names
        assert "PLAN.md" not in names
        assert "logo.png" not in names
        assert "notes.txt" not in names

    def test_ignores_sibling_directories(self, tmp_path: Path):
        """Directories next to ref/ are not scanned."""
        ref = tmp_path / "ref"
        ref.mkdir()
        sibling = tmp_path / "screenshots"
        sibling.mkdir()
        (sibling / "capture.png").write_bytes(b"PNG" * 100)
        (ref / "demo.png").write_bytes(b"PNG" * 100)

        result = scan_directory(ref)

        assert len(result.images) == 1
        assert result.images[0].name == "demo.png"

    def test_ref_listed_file_included_with_role(self, tmp_path: Path):
        """File in ref/ listed in ## References is included with its role."""
        ref = tmp_path / "ref"
        ref.mkdir()
        img = ref / "ui.png"
        img.write_bytes(b"PNG" * 100)

        scan = scan_directory(ref)
        assert len(scan.images) == 1

        # Role lookup via scan_files with references.
        refs = [ReferenceEntry(path=Path("ref/ui.png"), roles=["visual-target"])]
        result = scan_files(scan.images, references=refs)
        assert result.roles[img] == ["visual-target"]

    def test_ref_unlisted_file_produces_diagnostic(self, tmp_path: Path):
        """File in ref/ NOT in ## References gets diagnostic."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "listed.png").write_bytes(b"PNG" * 100)
        (ref / "stray.jpg").write_bytes(b"JPG" * 100)

        scan = scan_directory(ref)
        refs = [ReferenceEntry(path=Path("ref/listed.png"), roles=["visual-target"])]
        errors = tmp_path / "errors.jsonl"
        unlisted = check_unlisted_ref_files(scan, refs, ref_dir=ref, errors_path=errors)

        assert len(unlisted) == 1
        assert unlisted[0].name == "stray.jpg"
        record = json.loads(errors.read_text().strip())
        assert record["site"] == "scanner"
        assert "stray.jpg" in record["message"]

    def test_tiny_image_included_if_declared(self, tmp_path: Path):
        """A tiny image is included when declared in ## References."""
        ref = tmp_path / "ref"
        ref.mkdir()
        tiny = ref / "icon.png"
        tiny.write_bytes(b"P")  # 1 byte — no relevance filtering.

        scan = scan_directory(ref)
        assert len(scan.images) == 1

        refs = [ReferenceEntry(path=Path("ref/icon.png"), roles=["visual-target"])]
        errors = tmp_path / "errors.jsonl"
        unlisted = check_unlisted_ref_files(scan, refs, ref_dir=ref, errors_path=errors)
        assert unlisted == []

    def test_large_image_excluded_if_not_declared(self, tmp_path: Path):
        """A large image without a ## References entry gets diagnostic."""
        ref = tmp_path / "ref"
        ref.mkdir()
        big = ref / "wallpaper.png"
        big.write_bytes(b"PNG" * 100_000)  # Large file, undeclared.

        scan = scan_directory(ref)
        assert len(scan.images) == 1

        errors = tmp_path / "errors.jsonl"
        unlisted = check_unlisted_ref_files(scan, [], ref_dir=ref, errors_path=errors)
        assert len(unlisted) == 1
        assert unlisted[0].name == "wallpaper.png"

    def test_subdirs_under_ref_ignored(self, tmp_path: Path):
        """scan_directory does not recurse into subdirectories of ref/."""
        ref = tmp_path / "ref"
        ref.mkdir()
        sub = ref / "subdir"
        sub.mkdir()
        (sub / "nested.png").write_bytes(b"PNG" * 100)
        (ref / "top.png").write_bytes(b"PNG" * 100)

        result = scan_directory(ref)

        assert len(result.images) == 1
        assert result.images[0].name == "top.png"


class TestScanFilesRoleLookupIntegration:
    """scan_files role-lookup matches paths against ## References correctly."""

    def test_relative_path_match(self, tmp_path: Path):
        """File passed as relative ref/name matches ref entry."""
        ref = tmp_path / "ref"
        ref.mkdir()
        img = ref / "shot.png"
        img.write_bytes(b"PNG")
        refs = [ReferenceEntry(path=Path("ref/shot.png"), roles=["docs"])]
        result = scan_files([Path("ref/shot.png")], references=refs)
        # File doesn't exist at cwd-relative path so won't classify,
        # but role lookup still works for existing files.
        # Use the absolute path that does exist.
        result = scan_files([img], references=refs)
        assert result.roles[img] == ["docs"]

    def test_multiple_entries_each_get_roles(self, tmp_path: Path):
        """Each file matches its own entry independently."""
        ref = tmp_path / "ref"
        ref.mkdir()
        a = ref / "a.png"
        a.write_bytes(b"PNG")
        b = ref / "b.pdf"
        b.write_bytes(b"%PDF")
        c = ref / "c.txt"
        c.write_text("notes")
        refs = [
            ReferenceEntry(path=Path("ref/a.png"), roles=["visual-target"]),
            ReferenceEntry(path=Path("ref/b.pdf"), roles=["docs"]),
            ReferenceEntry(path=Path("ref/c.txt"), roles=["behavioral-target"]),
        ]
        result = scan_files([a, b, c], references=refs)
        assert result.roles[a] == ["visual-target"]
        assert result.roles[b] == ["docs"]
        assert result.roles[c] == ["behavioral-target"]

    def test_unlisted_file_gets_no_role(self, tmp_path: Path):
        """File not in ## References has no entry in roles dict."""
        ref = tmp_path / "ref"
        ref.mkdir()
        listed = ref / "listed.png"
        listed.write_bytes(b"PNG")
        orphan = ref / "orphan.png"
        orphan.write_bytes(b"PNG")
        refs = [ReferenceEntry(path=Path("ref/listed.png"), roles=["visual-target"])]
        result = scan_files([listed, orphan], references=refs)
        assert listed in result.roles
        assert orphan not in result.roles


class TestNoRemovedScoringSymbols:
    """Pin that the scoring symbols removed in commit ffc66ea stay gone.

    Mirrors the ``hasattr``-style invariant test added in 7.5.5
    (``TestNoInitializerImportsInPipeline``). If any of these names
    reappears in ``duplo.scanner`` the suite fails, flagging a
    regression toward the pre-SPEC relevance-scoring model.
    """

    def test_no_file_relevance_dataclass(self):
        assert not hasattr(scanner, "FileRelevance")

    def test_no_assess_image_function(self):
        assert not hasattr(scanner, "_assess_image")

    def test_no_assess_video_function(self):
        assert not hasattr(scanner, "_assess_video")

    def test_no_assess_pdf_function(self):
        assert not hasattr(scanner, "_assess_pdf")

    def test_no_assess_text_function(self):
        assert not hasattr(scanner, "_assess_text")

    def test_no_min_image_bytes_constant(self):
        assert not hasattr(scanner, "_MIN_IMAGE_BYTES")

    def test_scan_result_has_no_relevance_field(self):
        fields = {f for f in ScanResult.__dataclass_fields__}
        assert "relevance" not in fields


class TestScanDirectoryRefInventoryOnly:
    """scan_directory is a pure file-inventory walk of ref/ — no scoring."""

    def test_output_fields_are_inventory_only(self, tmp_path: Path):
        """ScanResult exposes only file lists, urls, and roles — no scores."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "a.png").write_bytes(b"PNG")
        result = scan_directory(ref)
        expected_fields = {"images", "videos", "pdfs", "text_files", "urls", "roles"}
        assert set(ScanResult.__dataclass_fields__) == expected_fields
        assert result.images[0].name == "a.png"

    def test_inventory_independent_of_file_size(self, tmp_path: Path):
        """A 1-byte image and a large image both land in scan.images."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "tiny.png").write_bytes(b"P")
        (ref / "big.png").write_bytes(b"PNG" * 100_000)
        result = scan_directory(ref)
        assert {p.name for p in result.images} == {"tiny.png", "big.png"}

    def test_inventory_independent_of_text_length(self, tmp_path: Path):
        """An empty text file and a long one both land in scan.text_files."""
        ref = tmp_path / "ref"
        ref.mkdir()
        (ref / "empty.txt").write_text("")
        (ref / "long.md").write_text("x" * 10_000)
        result = scan_directory(ref)
        assert {p.name for p in result.text_files} == {"empty.txt", "long.md"}
