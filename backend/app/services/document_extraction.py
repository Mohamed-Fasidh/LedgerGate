"""Conservative document extraction with bounded PDF parsing.

Extracted fields are suggestions only and always require human confirmation.
"""
import re
from pathlib import Path

AMOUNT_PATTERNS = [
    r"(?:grand\s+total|invoice\s+total|total\s+amount|amount\s+due|total)\s*[:\-]?\s*(?:₹|INR\s*)?([\d,]+(?:\.\d{1,2})?)",
    r"(?:₹|INR)\s*([\d,]+(?:\.\d{1,2})?)",
]
INVOICE_PATTERNS = [
    r"(?:invoice\s*(?:no|number|#))\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-\/]+)",
]
DATE_PATTERNS = [
    r"(?:invoice\s*date|date)\s*[:\-]?\s*(\d{1,2}[\/\-]\d{1,2}[\/\-]\d{2,4}|\d{4}-\d{2}-\d{2})",
]


def parse_text(text: str) -> dict:
    normalized = text.replace("\u00a0", " ")
    result = {"invoice_number": None, "amount": None, "currency": "INR", "invoice_date": None, "confidence": {}}
    for pat in INVOICE_PATTERNS:
        match = re.search(pat, normalized, re.I)
        if match:
            result["invoice_number"] = match.group(1).strip()
            result["confidence"]["invoice_number"] = "regex_candidate"
            break
    for pat in AMOUNT_PATTERNS:
        matches = list(re.finditer(pat, normalized, re.I))
        if matches:
            raw = matches[-1].group(1).replace(",", "")
            try:
                result["amount"] = round(float(raw), 2)
                result["confidence"]["amount"] = "regex_candidate"
                if "₹" in normalized or re.search(r"\bINR\b", normalized, re.I):
                    result["currency"] = "INR"
                elif "$" in normalized:
                    result["currency"] = "USD"
                break
            except ValueError:
                pass
    for pat in DATE_PATTERNS:
        match = re.search(pat, normalized, re.I)
        if match:
            result["invoice_date"] = match.group(1)
            result["confidence"]["invoice_date"] = "regex_candidate"
            break
    return result


def extract_file(path: str, suffix: str, max_pdf_pages: int = 100) -> tuple[str, dict]:
    """Extract bounded text from UTF-8 TXT or non-encrypted PDF documents."""
    file_path = Path(path)
    if suffix == ".txt":
        raw = file_path.read_bytes()
        if b"\x00" in raw:
            raise ValueError("Text file contains binary data")
        text = raw.decode("utf-8-sig", errors="strict")
    elif suffix == ".pdf":
        import fitz

        with fitz.open(path) as doc:
            if doc.needs_pass:
                raise ValueError("Encrypted PDFs are not accepted")
            if doc.page_count > max_pdf_pages:
                raise ValueError(f"PDF exceeds the {max_pdf_pages}-page limit")
            pages = list(doc)
            text = "\n".join(page.get_text("text") for page in pages)
            if len(text.strip()) < 20:
                # OCR is optional and remains bounded by max_pdf_pages.
                try:
                    import io
                    import pytesseract
                    from PIL import Image

                    chunks = []
                    for page in pages:
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                        chunks.append(pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png")))))
                    text = "\n".join(chunks)
                except Exception:
                    pass
    else:
        raise ValueError("Unsupported file type")
    return text, parse_text(text)
