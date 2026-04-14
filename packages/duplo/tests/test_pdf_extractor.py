"""Tests for duplo.pdf_extractor."""

from __future__ import annotations

from pathlib import Path

from duplo.pdf_extractor import docs_text_extractor, extract_pdf_text
from duplo.spec_reader import ReferenceEntry


def _make_pdf(text: str = "Hello World") -> bytes:
    """Return minimal PDF bytes containing *text* on one page."""
    stream = f"BT /F1 12 Tf 100 700 Td ({text}) Tj ET"
    stream_len = len(stream)
    return (
        f"%PDF-1.4\n"
        f"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n\n"
        f"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n\n"
        f"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]\n"
        f"   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
        f"endobj\n\n"
        f"4 0 obj\n<< /Length {stream_len} >>\nstream\n{stream}\nendstream\n"
        f"endobj\n\n"
        f"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        f"endobj\n\n"
        f"xref\n0 6\ntrailer\n<< /Size 6 /Root 1 0 R >>\n"
        f"startxref\n0\n%%EOF"
    ).encode()


class TestExtractPdfText:
    def test_single_pdf(self, tmp_path: Path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(_make_pdf("Feature overview"))
        result = extract_pdf_text([pdf])
        assert "Feature overview" in result
        assert "doc.pdf" in result

    def test_multiple_pdfs(self, tmp_path: Path):
        pdf1 = tmp_path / "first.pdf"
        pdf2 = tmp_path / "second.pdf"
        pdf1.write_bytes(_make_pdf("Alpha feature"))
        pdf2.write_bytes(_make_pdf("Beta feature"))
        result = extract_pdf_text([pdf1, pdf2])
        assert "Alpha feature" in result
        assert "Beta feature" in result
        assert "first.pdf" in result
        assert "second.pdf" in result

    def test_empty_list(self):
        assert extract_pdf_text([]) == ""

    def test_unreadable_pdf_skipped(self, tmp_path: Path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf at all")
        good = tmp_path / "good.pdf"
        good.write_bytes(_make_pdf("Valid content"))
        result = extract_pdf_text([bad, good])
        assert "Valid content" in result

    def test_missing_file_skipped(self, tmp_path: Path):
        missing = tmp_path / "gone.pdf"
        result = extract_pdf_text([missing])
        assert result == ""

    def test_pdf_with_no_text(self, tmp_path: Path):
        blank = tmp_path / "blank.pdf"
        # Minimal valid PDF with no text content stream
        blank.write_bytes(
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R "
            b"/MediaBox [0 0 612 792] >>\nendobj\n"
            b"xref\n0 4\ntrailer\n<< /Size 4 /Root 1 0 R >>\n"
            b"startxref\n0\n%%EOF"
        )
        result = extract_pdf_text([blank])
        assert result == ""


def _ref(path: Path, roles: list[str] | None = None) -> ReferenceEntry:
    return ReferenceEntry(path=path, roles=roles or ["docs"])


class TestDocsTextExtractor:
    def test_txt_file_read_directly(self, tmp_path: Path):
        txt = tmp_path / "notes.txt"
        txt.write_text("Some notes here", encoding="utf-8")
        result = docs_text_extractor([_ref(txt)])
        assert "notes.txt" in result
        assert "Some notes here" in result

    def test_md_file_read_directly(self, tmp_path: Path):
        md = tmp_path / "readme.md"
        md.write_text("# Title\nContent", encoding="utf-8")
        result = docs_text_extractor([_ref(md)])
        assert "readme.md" in result
        assert "# Title\nContent" in result

    def test_pdf_routed_to_extract_pdf_text(self, tmp_path: Path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(_make_pdf("PDF content here"))
        result = docs_text_extractor([_ref(pdf)])
        assert "doc.pdf" in result
        assert "PDF content here" in result

    def test_mixed_extensions(self, tmp_path: Path):
        pdf = tmp_path / "spec.pdf"
        pdf.write_bytes(_make_pdf("From PDF"))
        txt = tmp_path / "notes.txt"
        txt.write_text("From TXT", encoding="utf-8")
        md = tmp_path / "guide.md"
        md.write_text("From MD", encoding="utf-8")
        entries = [_ref(pdf), _ref(txt), _ref(md)]
        result = docs_text_extractor(entries)
        assert "From PDF" in result
        assert "From TXT" in result
        assert "From MD" in result
        assert "spec.pdf" in result
        assert "notes.txt" in result
        assert "guide.md" in result

    def test_empty_list(self):
        assert docs_text_extractor([]) == ""

    def test_unknown_extension_skipped(self, tmp_path: Path):
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG fake")
        result = docs_text_extractor([_ref(img)])
        assert result == ""

    def test_missing_file_skipped(self, tmp_path: Path):
        missing = tmp_path / "gone.txt"
        result = docs_text_extractor([_ref(missing)])
        assert result == ""

    def test_unreadable_pdf_skipped(self, tmp_path: Path):
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        good = tmp_path / "good.txt"
        good.write_text("Good text", encoding="utf-8")
        result = docs_text_extractor([_ref(bad), _ref(good)])
        assert "Good text" in result

    def test_header_per_file(self, tmp_path: Path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("AAA", encoding="utf-8")
        b.write_text("BBB", encoding="utf-8")
        result = docs_text_extractor([_ref(a), _ref(b)])
        assert "=== a.txt ===" in result
        assert "=== b.txt ===" in result
