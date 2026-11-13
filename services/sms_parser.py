"""Bank SMS / notification message parser.

Parses Turkish bank SMS templates and returns a dict that can be used to
pre-fill a Transaction record:

    {
        "bank":      "Ziraat Bankası",
        "amount":    1234.56,
        "date":      date(2025, 6, 1),
        "card_last4": "1234",
        "merchant":  "BİM",
        "category":  "Market",
        "account":   "VHS-1234",
        "raw":       "Ziraat Bankasi ... 1.234,56 TL ... Bim Market ..."
    }

The parser is intentionally conservative — if it cannot identify a known
bank template it still returns a dict with `bank: None` so the caller can
decide whether to fall back to the OCR / manual flow.
"""
import re
from datetime import date
from typing import Dict, Optional

# Turkish bank SMS templates (case-insensitive, flexible whitespace).
# Each entry: (compiled regex, bank name).  The regex may capture a named
# "merchant" group; it will NOT capture the amount — that's done separately.
_BANK_PATTERNS = [
    (re.compile(r"ziraat\s*banka", re.I), "Ziraat Bankası"),
    (re.compile(r"garanti\s*(?:banka|g\s*?b)", re.I), "Garanti Bankası"),
    (re.compile(r"iş\s*banka", re.I), "İş Bankası"),
    (re.compile(r"yapı\s*kredi", re.I), "Yapı Kredi"),
    (re.compile(r"akbank", re.I), "Akbank"),
    (re.compile(r"halk\s*banka", re.I), "Halkbank"),
]

# Amount regex — captures Turkish-format numbers: 1.234,56 or 234,56 or 500
# Supports multiple thousands groups: 12.345.678,90
# The regex is deliberately greedy so "1.500" is captured as the full string
# "1.500" rather than "1" + "500".
_AMOUNT_RE = re.compile(r"\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:,\d{1,2})?|\d+")


def _to_float(num: str) -> float:
    """Parse a possibly Turkish-formatted number: 1.250,50 → 1250.50.

    Rules:
      * "1.500"      → 1500        ('.' is thousands, no ',' present)
      * "1.234,56"   → 1234.56    ('.' thousands, ',' decimal)
      * "234,56"     → 234.56     (no '.', ',' decimal)
      * "1250"       → 1250.0     (no separators)
    """
    s = num.strip()
    has_thousands_dot = re.search(r"\.\d{1,3}$", s) and '.' in s and ',' not in s
    has_mixed = '.' in s and ',' in s
    if has_mixed:
        # e.g. 1.234,56 → 1234.56
        s = s.replace('.', '').replace(',', '.')
    elif has_thousands_dot:
        # e.g. 1.500 → 1500 (a dot followed by 1-3 digits is a thousands sep)
        s = s.replace('.', '')
    elif ',' in s:
        # e.g. 234,56 → 234.56
        s = s.replace(',', '.')
    return float(s)


# Common Turkish merchant/category keywords.
_MERCHANT_CATEGORY_MAP = {
    "market": ["bim", "a101", "sok", "migros", "seyir", "market"],
    "bills": ["fatura", "elektrik", "su", "gaz", "internet", "abon"],
    "fuel": ["akaryakit", "benzin", "motorin", "petrol", "istasyon", "otok"],
    "debt": ["kredi", "taksit", "borc", "ödünç"],
    "transport": ["metro", "otobus", "taksi", "taxi", "ulasim", "kart"],
    "health": ["eczane", "pharmacy", "hastane", "doktor"],
    "entertainment": ["sinema", "konser", "oyun", "mutfak"],
}


def _guess_category(merchant: Optional[str], bank: Optional[str]) -> str:
    text = f"{merchant or ''} {bank or ''}".lower()
    for category, keywords in _MERCHANT_CATEGORY_MAP.items():
        for kw in keywords:
            if kw in text:
                return category.title()
    return "Market"


def _find_date(text: str) -> Optional[date]:
    """Extract a date (DD.MM.YYYY or YYYY-MM-DD) from the SMS body."""
    m = re.search(r"\b(\d{2})[./](\d{2})[./](\d{4})\b", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def _find_last4(text: str) -> Optional[str]:
    """Find the last-4 card number — the first 4-digit group that isn't a date."""
    # Look for patterns like "Kart: 1234" or "kartınız 1234" or standalone 4-digit after bank name.
    m = re.search(r"kart(?:ınız|ınızı)?\s*:?\s*(\d{4})\b", text, re.I)
    if m:
        return m.group(1)
    # Fallback: any 4-digit number not in a date pattern.
    for m in re.finditer(r"\b(\d{4})\b", text):
        num = m.group(1)
        if num not in ('1900', '1901', '1902', '1903', '1904', '1905',
                       '1906', '1907', '1908', '1909', '2000', '2001',
                       '2002', '2003', '2004', '2005', '2006', '2007',
                       '2008', '2009', '2010', '2011', '2012', '2013',
                       '2014', '2015', '2016', '2017', '2018', '2019',
                       '2020', '2021', '2022', '2023', '2024', '2025',
                       '2026', '2027', '2028', '2029'):
            return num
    return None


def _find_amount(text: str) -> Optional[float]:
    """Find the largest amount in the text, handling Turkish separators.

    Numbers that look like dates (DD.MM.YYYY) are excluded so the date
    does not pollute the amount search.
    """
    candidates = []
    # Remove date tokens first so they can't be picked up as amounts.
    cleaned = re.sub(r"\d{2}[./]\d{2}[./]\d{4}", "", text)
    for m in _AMOUNT_RE.finditer(cleaned):
        try:
            val = _to_float(m.group(0))
        except ValueError:
            continue
        # Ignore implausibly large numbers that are clearly not amounts.
        if val > 10_000_000:
            continue
        candidates.append(val)
    return max(candidates) if candidates else None


def _find_merchant(message: str, bank_name: Optional[str]) -> Optional[str]:
    """Extract the merchant / store name that follows the bank mention."""
    if bank_name:
        m = re.search(
            re.escape(bank_name) + r"[^.\n]*(?:mağaza|magaza|market|işyeri|merchant)\s*:\s*"
            r"([A-ZÇĞİÖŞÜa-zçğıöşü0-9 ]+?)(?=\s*[.,]|\s*$|\s+kart)",
            message, re.I)
        if m:
            return m.group(1).strip()
    # Look for the last named entity before "TL" or at end of line
    m = re.search(r"mağaza\s*:\s*([^\n.,]+)", message, re.I)
    if m:
        return m.group(1).strip()
    return None


def parse_bank_sms(message: str) -> Dict:
    """Parse a Turkish bank SMS / push-notification message.

    Returns a dict with keys: bank, amount, date, card_last4, merchant,
    category, account, raw.  All non-matching fields are None / empty.
    """
    if not message:
        return {}

    bank_name: Optional[str] = None
    for pattern, name in _BANK_PATTERNS:
        if pattern.search(message):
            bank_name = name
            break

    return {
        "bank": bank_name,
        "amount": _find_amount(message),
        "date": _find_date(message),
        "card_last4": _find_last4(message),
        "merchant": _find_merchant(message, bank_name),
        "category": _guess_category(_find_merchant(message, bank_name), bank_name),
        "account": "",
        "raw": message.strip()[:500],
    }