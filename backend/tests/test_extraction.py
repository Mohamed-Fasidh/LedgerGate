from pathlib import Path

import fitz
import pytest

from app.services.document_extraction import extract_file, parse_text


def test_extract_invoice_fields_as_candidates():
    result = parse_text("Invoice No: LG-1001\\nInvoice Date: 2026-11-14\\nGrand Total: INR 65,000.00")
    assert result["invoice_number"] == "LG-1001"
    assert result["amount"] == 65000.0
    assert result["currency"] == "INR"
    assert result["invoice_date"] == "2026-11-14"
    assert result["confidence"]["amount"] == "regex_candidate"


def test_txt_extraction_requires_valid_utf8(tmp_path: Path):
    path = tmp_path / "bad.txt"
    path.write_bytes(b"Invoice\\xff")
    with pytest.raises(UnicodeDecodeError):
        extract_file(str(path), ".txt")


def test_txt_extraction_rejects_binary_content(tmp_path: Path):
    path = tmp_path / "binary.txt"
    path.write_bytes(b"Invoice\\x00data")
    with pytest.raises(ValueError, match="binary"):
        extract_file(str(path), ".txt")


def test_pdf_extraction_enforces_page_limit(tmp_path: Path):
    path = tmp_path / "many-pages.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(path)
    doc.close()
    with pytest.raises(ValueError, match="page limit"):
        extract_file(str(path), ".pdf", max_pdf_pages=1)


def test_pdf_extraction_rejects_encrypted_pdf(tmp_path: Path):
    path = tmp_path / "encrypted.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(path, encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    doc.close()
    with pytest.raises(ValueError, match="Encrypted"):
        extract_file(str(path), ".pdf")
