from app.services.document_extraction import parse_text

def test_extract_invoice_fields_as_candidates():
    result=parse_text("Invoice No: LG-1001\\nInvoice Date: 2026-11-14\\nGrand Total: INR 65,000.00")
    assert result["invoice_number"]=="LG-1001"
    assert result["amount"]==65000.0
    assert result["currency"]=="INR"
    assert result["invoice_date"]=="2026-11-14"
    assert result["confidence"]["amount"]=="regex_candidate"
