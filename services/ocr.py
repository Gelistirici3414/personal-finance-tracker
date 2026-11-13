"""OCR / Vision service for receipt and brochure analysis.

Provides two backends:
  1. pytesseract + Pillow  (local, needs the tesseract binary installed)
  2. A rule-based text heuristic that extracts price + discount from plain text

The service is always available; it falls back to the heuristic if
pytesseract is not installed, so the app works out-of-the-box.
"""
import logging
import re
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Cache pytesseract at import time so every call is cheap.
try:
    import pytesseract
    from PIL import Image
    _TESSERACT = True
except Exception:  # pragma: no cover - optional dependency
    pytesseract = None
    Image = None
    _TESSERACT = False


PRICE_PATTERNS = [
    # "₺" prefix or suffix, decimal or comma separated
    re.compile(r"(?P<price>\d{1,7}(?:[.,]\d{1,2})?)\s*(?:TL|₺|T\.L\.?)", re.I),
    re.compile(r"(?:TL|₺|T\.L\.?)\s*(?P<price>\d{1,7}(?:[.,]\d{1,2})?)"),
]
DISCOUNT_PATTERNS = [
    re.compile(r"%\s*(?P<pct>\d{1,2}(?:[.,]\d+)?)", re.I),
    re.compile(r"(?P<pct>\d{1,2}(?:[.,]\d+)?)\s*%", re.I),
]
AMOUNT_TOTAL_KEYWORDS = re.compile(
    r"(?i)\b(total|tutar|genel\s*toplam|grand\s*total|toptlam|toptlaml)\b"
)


def image_to_text(image_bytes: bytes, filename: str = "receipt") -> str:
    """Run OCR on raw image bytes and return the extracted text.

    Falls back to an empty string when tesseract is unavailable so callers
    can decide whether to use the heuristic parser instead.
    """
    if not _TESSERACT:
        logger.debug("pytesseract not available; returning empty text")
        return ""
    try:
        import io
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img)
        return text or ""
    except Exception:
        logger.exception("OCR failed on %s", filename)
        return ""


def pdf_to_text(pdf_bytes: bytes) -> str:
    """Extract plain text from a PDF (uses PyPDF2 if installed)."""
    try:
        import PyPDF2
        import io
        reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        return ""


def _to_float(num: str) -> float:
    """Parse a possibly Turkish-formatted number: 1.250,50 → 1250.50."""
    s = num.strip()
    # If both '.' and ',' are present, '.' is thousands and ',' is decimal.
    if '.' in s and ',' in s:
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    return float(s)


def extract_price(text: str) -> Optional[float]:
    """Pull the largest labelled total price from OCR text.

    Preference order:
      1. A line containing a total keyword → largest number on that line.
      2. The largest standalone number on its own line.
      3. The largest number with a ₺/TL suffix.

    Handles Turkish thousands separators (e.g. "1.250,50" → 1250.50).
    """
    if not text:
        return None
    candidates = []
    for line in text.splitlines():
        if AMOUNT_TOTAL_KEYWORDS.search(line):
            nums = re.findall(r"\d+(?:[.,]\d+)*", line)
            if nums:
                candidates.append(max(_to_float(n) for n in nums))
    if candidates:
        return max(candidates)

    for pat in PRICE_PATTERNS:
        for m in pat.finditer(text):
            candidates.append(_to_float(m.group("price")))
    if candidates:
        return max(candidates)

    standalone = [
        _to_float(n)
        for n in re.findall(r"^\s*\d+(?:[.,]\d+)*\s*$", text, re.M)
    ]
    if standalone:
        return max(standalone)
    return None


def extract_discount(text: str) -> Optional[float]:
    """Return the lowest discount percentage found, e.g. 30.5."""
    if not text:
        return None
    pcts = []
    for pat in DISCOUNT_PATTERNS:
        for m in pat.finditer(text):
            try:
                pcts.append(_to_float(m.group("pct")))
            except ValueError:
                continue
    return min(pcts) if pcts else None


def parse_receipt_text(text: str) -> Dict[str, Optional[float]]:
    """Bundle the extracted fields into a single dict for callers.

    Returns:
        {"price": Optional[float], "discount_pct": Optional[float]}
    """
    return {
        "price": extract_price(text),
        "discount_pct": extract_discount(text),
    }