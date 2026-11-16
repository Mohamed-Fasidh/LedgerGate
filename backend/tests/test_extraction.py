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


@pytest.mark.parametrize(
    ("text", "expected_currency", "expected_amount"),
    [
        ("Invoice Total: $1,250.75", "USD", 1250.75),
        ("Grand Total: USD 2,000", "USD", 2000.0),
        ("Amount Due: €845.20", "EUR", 845.2),
        ("Total: GBP 99.99", "GBP", 99.99),
        ("Grand Total: 1,250.75 USD", "USD", 1250.75),
        ("Amount Due: 845.20 EUR", "EUR", 845.2),
        ("Total: 99.99 GBP", "GBP", 99.99),
    ],
)
def test_extracts_supported_currency_symbols_and_codes(text, expected_currency, expected_amount):
    result = parse_text(text)
    assert result["amount"] == expected_amount
    assert result["currency"] == expected_currency
    assert result["confidence"]["amount"] == "regex_candidate"


def test_amount_without_currency_marker_keeps_inr_default():
    result = parse_text("Grand Total: 650.00")
    assert result["amount"] == 650.0
    assert result["currency"] == "INR"


def test_ocr_page_text_is_joined_with_real_newlines():
    import ast
    from pathlib import Path

    source = Path(document_extraction.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    joins = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "ocr_text" for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "join"
    ]
    assert len(joins) == 1
    assert isinstance(joins[0].value.func.value, ast.Constant)
    assert joins[0].value.func.value.value == "\n"
