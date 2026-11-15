"""Background web scraper for Turkish market weekly catalogs.

Design principles
-----------------
* **Real data only.**  No mock / sample offers are ever injected.  When a
  market cannot be scraped (blocked, DNS down, JS-only SPA) the scheduler
  simply records 0 new deals and the UI shows an honest
  "anlık indirim verisi alınamadı" warning instead of fake products.
* **Bot-protection aware.**  A rotating pool of realistic desktop
  User-Agents plus full browser-style header sets (Sec-Fetch-*, sec-ch-ua,
  Accept, Accept-Language, Referer) are used on every request.  Each market
  keeps a session that re-rolls its user agent between runs.
* **Multi-strategy parsing, per market.**
    1. JSON-LD Product/Offer blocks
    2. Structured data-* product attributes
    3. Market-specific DOM selectors (BİM priceArea, ŞOK data-testid boxes,
       A101/Migros generic product-card heuristics)
    4. Generic "text contains a Turkish price token" fallback

Targets (override per-market via env CATALOG_URL_<MARKET>):
    BIM     https://www.bim.com.tr/Categories/100/aktuel-urunler.aspx?top=1
    A101    https://www.a101.com.tr/
    SOK     https://www.sok.com.tr/
    MIGROS  https://www.migros.com.tr/

Every network call is wrapped so a failure never crashes the scheduler.
"""
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

try:
    import requests
    from bs4 import BeautifulSoup
    _HTTP_OK = True
except Exception:  # pragma: no cover - dependencies missing
    requests = None
    BeautifulSoup = None
    _HTTP_OK = False

from models import db, PromoDeal, save_promo_deals
from services.ocr import extract_discount, _to_float

logger = logging.getLogger(__name__)

CATALOG_URLS = {
    "BIM": os.environ.get(
        "CATALOG_URL_BIM",
        "https://www.bim.com.tr/Categories/100/aktuel-urunler.aspx?top=1"),
    "A101": os.environ.get("CATALOG_URL_A101", "https://www.a101.com.tr/"),
    "SOK": os.environ.get("CATALOG_URL_SOK", "https://www.sok.com.tr/"),
    "MIGROS": os.environ.get("CATALOG_URL_MIGROS", "https://www.migros.com.tr/"),
}
TIMEOUT = 25
_MAX_DEALS_PER_MARKET = 200
_MIN_PRODUCTS_WITH_PRICE = 5

# Realistic desktop user-agent pool, rotated per request to mitigate simple
# bot detection (constant UA fingerprinting).  Tuple = (UA, Accept, Lang).
_USER_AGENTS = [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "tr-TR,tr;q=0.9,en;q=0.7"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "tr-TR,tr;q=0.9,en;q=0.6"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
     "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "tr-TR,tr;q=0.8,en-US;q=0.6,en;q=0.4"),
]
_SEC_CH_UA = ('"Chromium";v="125"; "Google Chrome";v="125"; '
              '"Not.A/Brand";v="24"')

_sessions: Dict[str, "requests.Session"] = {}


def _browser_headers(market: str) -> Dict[str, str]:
    """Build a realistic, per-call-rotated set of browser-like headers."""
    ua, accept, accept_lang = random.choice(_USER_AGENTS)
    base = CATALOG_URLS.get(market.upper(), "").rsplit("/", 1)[0]
    return {
        "User-Agent": ua,
        "Accept": accept,
        "Accept-Language": accept_lang,
        "Referer": base + "/",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": _SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "Connection": "keep-alive",
    }


def _session(market: str) -> Optional["requests.Session"]:
    if not _HTTP_OK:
        return None
    key = market.upper()
    if key not in _sessions:
        s = requests.Session()
        s.headers.update(_browser_headers(key))
        _sessions[key] = s
    return _sessions[key]


def _fetch_html(url: str, market: str) -> Optional[str]:
    """GET a catalog page with rotated browser-like headers.

    A fresh UA/header set is generated on every call (fingerprint rotation);
    the session's connection pool keeps keep-alive behaviour.  A 401/403
    triggers one automatic retry with a new header set plus a short
    randomised backoff.
    """
    sess = _session(market)
    if sess is None:
        logger.warning("requests/bs4 not available; cannot fetch %s", url)
        return None
    sess.headers.update(_browser_headers(market))
    try:
        resp = sess.get(url, timeout=TIMEOUT, allow_redirects=True)
        if resp.status_code in (401, 403):
            logger.warning("%s catalog returned HTTP %s (bot protection); "
                           "retrying with a fresh header set", market,
                           resp.status_code)
            sess.headers.update(_browser_headers(market))
            time.sleep(random.uniform(1.5, 3.0))
            resp = sess.get(url, timeout=TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() in ("iso-8859-1",
                                                          "latin-1"):
            resp.encoding = "utf-8"
        return resp.text
    except Exception:
        logger.exception("Failed to fetch catalog %s (%s)", market, url)
        return None


def _clean_text(s: Optional[str]) -> str:
    """Normalise scraped text: collapse whitespace, fix common entities."""
    if not s:
        return ""
    s = (s.replace("\u00a0", " ")
           .replace("\u2022", " ")
           .replace("&#x27;", "'")
           .replace("&amp;", "&"))
    return " ".join(s.split())


def _parse_price_tokens(text: str) -> Optional[float]:
    """Parse Turkish price notation: '289,00₺', '1.250,50 TL', '10,00'.

    Preference:
      1. A number directly followed by a currency marker (₺/TL) — the price.
      2. A bare decimal number in Turkish notation ('89,90', '1.250,50').
    Whole numbers without a marker are ignored (they may be sizes/quantities,
    e.g. 'yüzde 20' or '1 kg').
    """
    cleaned = _clean_text(text)
    if not cleaned:
        return None
    candidates: List[float] = []
    for m in re.finditer(r"(\d+(?:[.,]\d+)*)(?=\s*(?:₺|TL\b))",
                         cleaned, re.I):
        try:
            candidates.append(_to_float(m.group(1)))
        except Exception:
            continue
    if not candidates:
        # Fallback: Turkish decimal notation ('89,90' / '289,00').  Strip
        # spaces that text extraction may have inserted between the
        # integer and decimal parts ('289, 00' → '289,00').
        for m in re.finditer(r"\d+(?:\s*[.,]\s*\d+)+", cleaned):
            try:
                candidates.append(_to_float(re.sub(r"\s+", "", m.group(0))))
            except Exception:
                continue
    if not candidates:
        return None
    best = None
    for v in candidates:
        if 0 < v <= 100000 and (best is None or v > best):
            best = v
    return best


def _discount_from_texts(*texts: Optional[str]) -> Optional[float]:
    for t in texts:
        d = extract_discount(t or "")
        if d is not None:
            return d
    return None


def _deals_from_jsonld(market: str, soup: "BeautifulSoup",
                       source_url: str) -> List[Dict]:
    """Strategy 1: structured JSON-LD Product/Offer data (most reliable)."""
    deals: List[Dict] = []
    seen = set()
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.get_text().strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        stack = list(nodes)
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            ntype = node.get("@type", "")
            graph = node.get("@graph") or []
            if isinstance(graph, list):
                stack.extend(graph)
            if ntype in ("Product", "Offer", "ItemList") or \
                    isinstance(node.get("offers"), (dict, list)):
                offers = node.get("offers")
                if isinstance(offers, list):
                    stack.extend(offers)
                if ntype == "Product":
                    name = _clean_text(
                        node.get("name") or node.get("description") or "")
                    img = node.get("image")
                    if isinstance(img, list):
                        img = img[0] if img else None
                    off = node.get("offers") or {}
                    price = None
                    if isinstance(off, dict):
                        raw_price = str(off.get("price")
                                        or off.get("lowPrice") or "").strip()
                        try:
                            # JSON-LD prices are plain decimal strings
                            # ('120,50' / '30') — no currency marker needed.
                            v = _to_float(raw_price) if raw_price else None
                            if v is not None and 0 < v <= 100000:
                                price = v
                        except Exception:
                            price = None
                    if name:
                        key = (name, price)
                        if key in seen:
                            continue
                        seen.add(key)
                        deals.append({
                            "market": market, "product": name[:200],
                            "price": price, "discount_pct": None,
                            "image_url": img, "ocr_text": None,
                            "source_page": source_url})
    return deals


def _deals_from_data_attrs(market: str, soup: "BeautifulSoup",
                           source_url: str) -> List[Dict]:
    """Strategy 2: generic data-product / data-price attributes."""
    deals: List[Dict] = []
    for card in soup.select("[data-product][data-price]"):
        product = _clean_text(card.get("data-product"))
        price = _parse_price_tokens(card.get("data-price") or "")
        img = card.select_one("img")
        if product:
            deals.append({
                "market": market, "product": product[:200], "price": price,
                "discount_pct": _discount_from_texts(
                    card.get("data-discount")),
                "image_url": img.get("src") if img else None,
                "ocr_text": None, "source_page": source_url})
    return deals


def _deals_bim(market: str, soup: "BeautifulSoup",
               source_url: str) -> List[Dict]:
    """Strategy 3a: BİM DOM — div.product cards with a priceArea block.

    Real price markup:  <div class="text quantify">289,</div>
                        <div class="kusurArea"><span class="number">00</span></div>
                        <span class="curr">₺</span>
    The three parts are extracted separately and reassembled as
    '289,00₺' so the Turkish price parser sees one contiguous token.
    """
    deals: List[Dict] = []
    for card in soup.select("div.product"):
        title = card.select_one("h2.title")
        subtitle = card.select_one("h2.subTitle")
        name_parts = [p.get_text(strip=True)
                      for p in (title, subtitle)
                      if p and p.get_text(strip=True)]
        product = _clean_text(" ".join(name_parts))
        price_area = card.select_one(".priceArea")
        price = None
        if price_area is not None:
            whole_el = price_area.select_one(".text.quantify, .quantify")
            frac_el = price_area.select_one(".number")
            whole = _clean_text(whole_el.get_text(strip=True)) if whole_el else ""
            frac = _clean_text(frac_el.get_text(strip=True)) if frac_el else ""
            if whole:
                # Reassemble '289,' + '00' + '₺' into '289,00₺'.
                whole = whole.rstrip(",")
                token = whole + ("." + frac if frac else "") + "₺"
                price = _parse_price_tokens(token)
            if price is None:
                price = _parse_price_tokens(price_area.get_text(" ", strip=True))
        disc_el = card.select_one("[class*=discount]")
        img = card.select_one("img")
        if not product or price is None:
            continue
        deals.append({
            "market": market, "product": product[:200], "price": price,
            "discount_pct": _discount_from_texts(
                disc_el.get_text(" ", strip=True) if disc_el else None),
            "image_url": (img.get("src") or img.get("data-src")) if img else None,
            "ocr_text": None, "source_page": source_url})
    return deals


def _deals_sok(market: str, soup: "BeautifulSoup",
               source_url: str) -> List[Dict]:
    """Strategy 3b: ŞOK DOM — CProductCard modules, data-testid prices."""
    deals: List[Dict] = []
    seen = set()
    price_els = soup.select('[data-testid="price"], '
                            '[data-testid="discountedPrice"]')
    for el in price_els:
        card = el.find_parent(class_=lambda c: c and "productcard" in c.lower())
        if card is None:
            card = el.find_parent("a") or el.find_parent("div")
        if card is None:
            continue
        title = (card.select_one('[class*="CProductCard-module_title"]')
                 or card.find("h2") or card.find("h3"))
        product = _clean_text(title.get_text(" ", strip=True)) if title \
            else ""
        img = card.select_one('img[src*="product-thumb"], img')
        disc_el = card.select_one('[data-testid="discountedPrice"]')
        price_el = card.select_one('[data-testid="price"]')
        use_el = disc_el or price_el
        price = _parse_price_tokens(
            use_el.get_text(" ", strip=True)) if use_el else None
        discount = None
        if disc_el and price_el and disc_el is not price_el:
            old = _parse_price_tokens(price_el.get_text(" ", strip=True))
            new = _parse_price_tokens(disc_el.get_text(" ", strip=True))
            if old and new and old > new:
                discount = round((old - new) / old * 100.0, 1)
        if not product and img is not None:
            product = _clean_text(img.get("alt"))
        if not product or price is None:
            continue
        key = (product, price)
        if key in seen:
            continue
        seen.add(key)
        deals.append({
            "market": market, "product": product[:200], "price": price,
            "discount_pct": discount,
            "image_url": (img.get("src") or img.get("data-src")) if img else None,
            "ocr_text": None, "source_page": source_url})
    return deals


def _deals_generic_cards(market: str, soup: "BeautifulSoup",
                         source_url: str) -> List[Dict]:
    """Strategy 3c: A101 / Migros — heuristic product-card detection.

    Looks for repeated card-like containers that carry a Turkish price
    token plus a plausible product name.  Used for markets without a
    stable DOM contract or whose content is server-rendered sparsely
    (Migros is an SPA — this rarely yields data, which is why the UI
    must show an honest warning).
    """
    deals: List[Dict] = []
    seen = set()
    containers = soup.select(
        'a[href*="product"], div[class*="product-card"], '
        'div[class*="productCard"], li[class*="product"]')
    if not containers:
        containers = soup.select('[class*="card"], [class*="Card"]')
    for card in containers:
        text_all = card.get_text(" ", strip=True)
        price = _parse_price_tokens(text_all)
        if price is None or price > 100000:
            continue
        name_el = (card.find("h2") or card.find("h3") or card.find(
            class_=lambda c: c and any(
                k in c.lower() for k in ("name", "title", "product", "urun"))))
        product = _clean_text(name_el.get_text(" ", strip=True)) if name_el \
            else ""
        if len(product) < 2:
            cands = [s.strip() for s in card.find_all(
                string=lambda s: s and "₺" not in s and "TL" not in s)
                if len(s.strip()) > 2]
            product = _clean_text(max(cands, key=len)) if cands else ""
        if len(product) < 2 or product.isdigit():
            continue
        if (product, price) in seen:
            continue
        seen.add((product, price))
        img = card.select_one("img")
        deals.append({
            "market": market, "product": product[:200], "price": price,
            "discount_pct": _discount_from_texts(text_all[:400]),
            "image_url": (img.get("src") or img.get("data-src")) if img else None,
            "ocr_text": None, "source_page": source_url})
    return deals


def _deals_price_text_fallback(market: str, soup: "BeautifulSoup",
                               source_url: str) -> List[Dict]:
    """Strategy 4: last resort — card-ish divs containing a real ₺ token."""
    deals: List[Dict] = []
    seen = set()
    for text_el in soup.find_all(string=lambda s: s and "₺" in s):
        parent = text_el.parent
        if not parent:
            continue
        card = parent.find_parent(
            "div", class_=lambda c: c and ("card" in c.lower()
                                           or "urun" in c.lower()
                                           or "product" in c.lower()))
        if card is None:
            card = parent
        p_el = card.find(class_=lambda c: c and ("name" in c.lower()
                         or "title" in c.lower()))
        product = _clean_text(p_el.get_text(" ", strip=True)) if p_el else ""
        price_text = _clean_text(text_el.get_text())
        price = _parse_price_tokens(price_text)
        if not product and price is None:
            continue
        key = (product, price)
        if key in seen:
            continue
        seen.add(key)
        img = card.select_one("img")
        deals.append({
            "market": market,
            "product": (product or f"{market} ürün (etiket okuma)")[:200],
            "price": price,
            "discount_pct": extract_discount(price_text),
            "image_url": (img.get("src") or img.get("data-src")) if img else None,
            "ocr_text": price_text,
            "source_page": source_url})
    return deals


def _dedupe_deals(deals: List[Dict]) -> List[Dict]:
    out: List[Dict] = []
    seen = set()
    for d in deals:
        key = (_clean_text(d.get("product") or "")[:80],
               d.get("price"), d.get("image_url"))
        if key in seen or not key[0]:
            continue
        seen.add(key)
        out.append(d)
    return out[:_MAX_DEALS_PER_MARKET]


def _parse_html_deals(market: str, html: str, source_url: str) -> List[Dict]:
    """Run the parse strategies in priority order for *market*."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")

    deals = _deals_from_jsonld(market, soup, source_url)
    if len([d for d in deals if d.get("price")]) >= _MIN_PRODUCTS_WITH_PRICE:
        return _dedupe_deals(deals)

    deals += _deals_from_data_attrs(market, soup, source_url)

    if market.upper() == "BIM":
        deals += _deals_bim(market, soup, source_url)
    elif market.upper() == "SOK":
        deals += _deals_sok(market, soup, source_url)
    else:
        deals += _deals_generic_cards(market, soup, source_url)

    priced = [d for d in deals if d.get("price") is not None]
    if len(priced) >= _MIN_PRODUCTS_WITH_PRICE:
        return _dedupe_deals(deals)

    deals += _deals_price_text_fallback(market, soup, source_url)
    return _dedupe_deals(deals)


def scrape_market(market: str) -> List[PromoDeal]:
    """Scrape a single catalog and persist; returns created PromoDeals.

    Never raises — a failed market yields an empty list so the scheduler
    can continue with the remaining markets.  Returns ONLY real, scraped
    products (deals without a parsed price are dropped); on any failure
    the result is simply empty and the UI shows an honest warning.
    """
    key = market.upper()
    url = CATALOG_URLS.get(key)
    if not url:
        logger.info("No catalog URL configured for %s; skipping", market)
        return []
    html = _fetch_html(url, key)
    deals = _parse_html_deals(key, html, url)
    deals = [d for d in deals if d.get("price") is not None]
    if not deals:
        logger.info(
            "No real deals parsed for %s (URL: %s) — page likely blocked, "
            "JS-rendered, or layout-changed. UI will show an honest "
            "'veri alınamadı' warning instead of fake data.", key, url)
        return []
    try:
        return save_promo_deals(deals)
    except Exception:
        logger.exception("Failed to persist %s deals; rolled back", key)
        return []


def scrape_all_markets() -> Dict[str, int]:
    results: Dict[str, int] = {}
    for market in CATALOG_URLS:
        results[market] = len(scrape_market(market))
        time.sleep(random.uniform(0.5, 1.2))  # be polite between markets
    return results


def get_recent_deals(market: Optional[str] = None, hours: int = 72,
                    limit: int = 100) -> List[PromoDeal]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    q = PromoDeal.query.filter(PromoDeal.scraped_at >= cutoff) \
                      .order_by(PromoDeal.scraped_at.desc())
    if market:
        q = q.filter(PromoDeal.market == market.upper())
    return q.limit(limit).all()


def get_last_scraped_per_market() -> Dict[str, Optional[datetime]]:
    """Return the most recent scrape timestamp per market (or None)."""
    out: Dict[str, Optional[datetime]] = {}
    for mkt in CATALOG_URLS:
        row = PromoDeal.query.filter(PromoDeal.market == mkt.upper()) \
                            .order_by(PromoDeal.scraped_at.desc()).first()
        out[mkt.upper()] = row.scraped_at if row else None
    return out


def _main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape market catalogs")
    parser.add_argument("--market", default=None,
                        help="BIM, A101, SOK, or MIGROS")
    parser.add_argument("--all", action="store_true",
                       help="Scrape all configured markets")
    args = parser.parse_args()
    from app import create_app
    with create_app().app_context():
        if args.market:
            print(scrape_market(args.market))
        elif args.all:
            print(scrape_all_markets())
        else:
            parser.print_help()


if __name__ == "__main__":
    _main()
