"""Conservative document extraction: parsed fields are suggestions requiring human confirmation."""
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
        m = re.search(pat, normalized, re.I)
        if m:
            result["invoice_number"] = m.group(1).strip()
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
        m = re.search(pat, normalized, re.I)
        if m:
            result["invoice_date"] = m.group(1)
            result["confidence"]["invoice_date"] = "regex_candidate"
            break
    return result

def extract_file(path: str, suffix: str) -> tuple[str, dict]:
    p = Path(path)
    if suffix == ".txt":
        text = p.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".pdf":
        import fitz
        doc = fitz.open(path)
        text = "\n".join(page.get_text("text") for page in doc)
        if len(text.strip()) < 20:
            # Optional OCR: requires pytesseract and system tesseract executable.
            try:
                import pytesseract
                from PIL import Image
                import io
                chunks = []
                for page in doc:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                    chunks.append(pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png")))))
                text = "\n".join(chunks)
            except Exception:
                pass
    else:
        raise ValueError("Unsupported file type")
    return text, parse_text(text)
