"""Tests for duplo.scanner."""

from __future__ import annotations

from pathlib import Path

from duplo.scanner import ScanResult, scan_directory


class TestScanDirectory:
    def test_finds_images(self, tmp_path: Path):
        (tmp_path / "screenshot.png").write_bytes(b"PNG")
        (tmp_path / "photo.jpg").write_bytes(b"JPG")
        (tmp_path / "icon.gif").write_bytes(b"GIF")
        result = scan_directory(tmp_path)
        assert len(result.images) == 3

    def test_finds_pdfs(self, tmp_path: Path):
        (tmp_path / "spec.pdf").write_bytes(b"%PDF")
        result = scan_directory(tmp_path)
        assert len(result.pdfs) == 1

    def test_finds_text_files(self, tmp_path: Path):
        (tmp_path / "notes.txt").write_text("some notes")
        (tmp_path / "readme.md").write_text("# readme")
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
        (tmp_path / "a.txt").write_text("https://example.com")
        (tmp_path / "b.txt").write_text("https://example.com")
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
