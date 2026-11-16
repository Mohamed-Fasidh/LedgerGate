from app.services.document_extraction import parse_text

def test_extract_invoice_fields_as_candidates():
    result=parse_text("Invoice No: LG-1001\\nInvoice Date: 2026-11-14\\nGrand Total: INR 65,000.00")
    assert result["invoice_number"]=="LG-1001"
    assert result["amount"]==65000.0
    assert result["currency"]=="INR"
    assert result["invoice_date"]=="2026-11-14"
    assert result["confidence"]["amount"]=="regex_candidate"


import pytest
from app.services import document_extraction


def test_text_file_extraction_enforces_text_limit(tmp_path):
    path = tmp_path / "large.txt"
    path.write_text("x" * (document_extraction.MAX_EXTRACTED_TEXT_CHARS + 1), encoding="utf-8")

    with pytest.raises(ValueError, match="2 MB processing limit"):
        document_extraction.extract_file(str(path), ".txt")


def test_parser_rejects_oversized_extracted_text():
    with pytest.raises(ValueError, match="2 MB processing limit"):
        document_extraction.parse_text("x" * (document_extraction.MAX_EXTRACTED_TEXT_CHARS + 1))


def test_ocr_pixel_estimate_rejects_oversized_page():
    class Rect:
        width = 5000
        height = 5000

    class Page:
        rect = Rect()

    with pytest.raises(ValueError, match="OCR image-size limit"):
        document_extraction._ocr_pixel_count(Page())


def test_ocr_pixel_estimate_accepts_normal_page():
    class Rect:
        width = 612
        height = 792

    class Page:
        rect = Rect()

    assert document_extraction._ocr_pixel_count(Page()) == 1224 * 1584
