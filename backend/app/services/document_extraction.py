"""Conservative document extraction: parsed fields are suggestions requiring human confirmation."""
import re
from pathlib import Path
import math

MAX_EXTRACTED_TEXT_CHARS = 2_000_000
MAX_PDF_PAGES = 50
OCR_SCALE = 2
MAX_OCR_PAGE_PIXELS = 12_000_000
MAX_OCR_TOTAL_PIXELS = 48_000_000

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
    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
        raise ValueError("Extracted text exceeds the 2 MB processing limit")
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


def _ocr_pixel_count(page) -> int:
    """Estimate rendered pixels at the fixed OCR scale before allocating an image."""
    rect = page.rect
    width = math.ceil(float(rect.width) * OCR_SCALE)
    height = math.ceil(float(rect.height) * OCR_SCALE)
    if width <= 0 or height <= 0:
        raise ValueError("PDF contains a page with invalid dimensions")
    pixels = width * height
    if pixels > MAX_OCR_PAGE_PIXELS:
        raise ValueError("PDF page exceeds the OCR image-size limit")
    return pixels


def extract_file(path: str, suffix: str) -> tuple[str, dict]:
    p = Path(path)
    if suffix == ".txt":
        with p.open("r", encoding="utf-8", errors="replace") as source:
            text = source.read(MAX_EXTRACTED_TEXT_CHARS + 1)
        if len(text) > MAX_EXTRACTED_TEXT_CHARS:
            raise ValueError("Text file exceeds the 2 MB processing limit")
    elif suffix == ".pdf":
        import fitz
        with fitz.open(path) as doc:
            if doc.page_count > MAX_PDF_PAGES:
                raise ValueError("PDF exceeds the 50-page processing limit")
            page_text = []
            total_chars = 0
            for page in doc:
                chunk = page.get_text("text")
                total_chars += len(chunk)
                if total_chars > MAX_EXTRACTED_TEXT_CHARS:
                    raise ValueError("PDF extracted text exceeds the 2 MB processing limit")
                page_text.append(chunk)
            text = "\n".join(page_text)

            if len(text.strip()) < 20:
                # Check every page before OCR allocates rendered image buffers.
                total_ocr_pixels = sum(_ocr_pixel_count(page) for page in doc)
                if total_ocr_pixels > MAX_OCR_TOTAL_PIXELS:
                    raise ValueError("PDF exceeds the total OCR image-size limit")
                # OCR is optional; unavailable OCR dependencies leave extraction
                # as an unconfirmed empty candidate rather than blocking upload.
                try:
                    import pytesseract
                    from PIL import Image
                    import io
                    chunks = []
                    for page in doc:
                        pix = page.get_pixmap(matrix=fitz.Matrix(OCR_SCALE, OCR_SCALE), alpha=False)
                        chunks.append(pytesseract.image_to_string(Image.open(io.BytesIO(pix.tobytes("png")))))
                    text = "\n".join(chunks)
                    if len(text) > MAX_EXTRACTED_TEXT_CHARS:
                        raise ValueError("OCR text exceeds the 2 MB processing limit")
                except ImportError:
                    pass
    else:
        raise ValueError("Unsupported file type")
    return text, parse_text(text)
