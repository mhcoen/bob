"""Tests for duplo.scanner."""

from __future__ import annotations

from pathlib import Path

from duplo.scanner import ScanResult, scan_directory, scan_files


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

    def test_extracts_urls_from_non_text_files(self, tmp_path: Path):
        (tmp_path / "config.json").write_text('{"url": "https://product.example.com/api"}')
        result = scan_directory(tmp_path)
        assert "https://product.example.com/api" in result.urls

    def test_extracts_urls_from_html_files(self, tmp_path: Path):
        (tmp_path / "page.html").write_text('<a href="https://docs.example.com">Docs</a>')
        result = scan_directory(tmp_path)
        assert "https://docs.example.com" in result.urls

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


class TestRelevance:
    def test_small_image_flagged_irrelevant(self, tmp_path: Path):
        (tmp_path / "tiny.png").write_bytes(b"P" * 100)
        result = scan_directory(tmp_path)
        assert len(result.relevance) == 1
        assert not result.relevance[0].relevant
        assert "small" in result.relevance[0].reason

    def test_large_image_is_relevant(self, tmp_path: Path):
        (tmp_path / "screenshot.png").write_bytes(b"P" * 2000)
        result = scan_directory(tmp_path)
        assert len(result.relevance) == 1
        assert result.relevance[0].relevant

    def test_empty_pdf_flagged_irrelevant(self, tmp_path: Path):
        (tmp_path / "empty.pdf").write_bytes(b"")
        result = scan_directory(tmp_path)
        rel = [r for r in result.relevance if r.category == "pdf"]
        assert len(rel) == 1
        assert not rel[0].relevant

    def test_nonempty_pdf_is_relevant(self, tmp_path: Path):
        (tmp_path / "spec.pdf").write_bytes(b"%PDF" * 100)
        result = scan_directory(tmp_path)
        rel = [r for r in result.relevance if r.category == "pdf"]
        assert len(rel) == 1
        assert rel[0].relevant

    def test_empty_text_file_flagged_irrelevant(self, tmp_path: Path):
        (tmp_path / "empty.txt").write_text("   \n  ")
        result = scan_directory(tmp_path)
        rel = [r for r in result.relevance if r.category == "text"]
        assert len(rel) == 1
        assert not rel[0].relevant
        assert "empty" in rel[0].reason

    def test_short_text_file_flagged_irrelevant(self, tmp_path: Path):
        (tmp_path / "short.txt").write_text("hi")
        result = scan_directory(tmp_path)
        rel = [r for r in result.relevance if r.category == "text"]
        assert len(rel) == 1
        assert not rel[0].relevant
        assert "short" in rel[0].reason

    def test_substantive_text_is_relevant(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text(
            "This is a product spec with enough detail to be useful."
        )
        result = scan_directory(tmp_path)
        rel = [r for r in result.relevance if r.category == "text"]
        assert len(rel) == 1
        assert rel[0].relevant

    def test_url_source_tracked_for_non_text_files(self, tmp_path: Path):
        (tmp_path / "links.yaml").write_text("url: https://example.com/product")
        result = scan_directory(tmp_path)
        rel = [r for r in result.relevance if r.category == "url_source"]
        assert len(rel) == 1
        assert rel[0].relevant
        assert "URLs" in rel[0].reason

    def test_empty_video_flagged_irrelevant(self, tmp_path: Path):
        (tmp_path / "empty.mp4").write_bytes(b"")
        result = scan_directory(tmp_path)
        rel = [r for r in result.relevance if r.category == "video"]
        assert len(rel) == 1
        assert not rel[0].relevant
        assert "empty" in rel[0].reason

    def test_nonempty_video_is_relevant(self, tmp_path: Path):
        (tmp_path / "demo.mp4").write_bytes(b"\x00" * 5000)
        result = scan_directory(tmp_path)
        rel = [r for r in result.relevance if r.category == "video"]
        assert len(rel) == 1
        assert rel[0].relevant

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

    def test_assesses_relevance(self, tmp_path: Path):
        tiny = tmp_path / "tiny.png"
        tiny.write_bytes(b"P" * 100)
        result = scan_files([tiny])
        assert len(result.relevance) == 1
        assert not result.relevance[0].relevant
