"""Background web scraper for Turkish market weekly catalogs.

Targets (override via env CATALOG_URL_<MARKET>):
    BIM    https://www.bim.com.tr/aktuel
    A101   https://www.a101.com.tr/kataloglar
    SOK    https://www.sokmarketer.com/tr/aktuel
    MIGROS https://www.migroskurumsal.com/aktuel-firsatlar

Uses requests + BeautifulSoup.  Structured data-* attributes are preferred
over OCR; image-only cards fall back to `services.ocr.extract_price`.
Every network call is wrapped so a failure never crashes the scheduler.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

try:
    import requests
    from bs4 import BeautifulSoup
    _HTTP_OK = True
except Exception:
    requests = None
    BeautifulSoup = None
    _HTTP_OK = False

from models import db, PromoDeal, save_promo_deals
from services.ocr import extract_price, extract_discount

logger = logging.getLogger(__name__)

CATALOG_URLS = {
    "BIM": os.environ.get("CATALOG_URL_BIM", "https://www.bim.com.tr/aktuel"),
    "A101": os.environ.get("CATALOG_URL_A101", "https://www.a101.com.tr/kataloglar"),
    "SOK": os.environ.get("CATALOG_URL_SOK", "https://www.sokmarketer.com/tr/aktuel"),
    "MIGROS": os.environ.get("CATALOG_URL_MIGROS",
                             "https://www.migroskurumsal.com/aktuel-firsatlar"),
}
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
}
TIMEOUT = 20


def _fetch_html(url: str) -> Optional[str]:
    if not _HTTP_OK:
        logger.warning("requests/bs4 not available; cannot fetch %s", url)
        return None
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.text
    except Exception:
        logger.exception("Failed to fetch catalog %s", url)
        return None


def _parse_html_deals(market: str, html: str, source_url: str) -> List[Dict]:
    deals: List[Dict] = []
    if not html:
        return deals
    soup = BeautifulSoup(html, "html.parser")
    for card in soup.select("[data-product][data-price]"):
        product = (card.get("data-product") or "").strip()
        price_raw = (card.get("data-price") or "").strip().replace("₺", "").replace(",", ".")
        price = None
        try:
            price = float(price_raw) if price_raw else None
        except ValueError:
            pass
        discount = None
        try:
            discount = float((card.get("data-discount") or "").strip())
        except ValueError:
            pass
        img = card.select_one("img")
        if product:
            deals.append({"market": market, "product": product, "price": price,
                          "discount_pct": discount,
                          "image_url": img.get("src") if img else None,
                          "ocr_text": None, "source_page": source_url})
    if not deals:
        for text_el in soup.find_all(string=lambda s: s and "₺" in s):
            parent = text_el.parent
            if not parent:
                continue
            card = parent.find_parent("div", class_=lambda c: c and "card" in c.lower())
            p_el = card.find(class_=lambda c: c and ("name" in c.lower()
                     or "title" in c.lower())) if card else None
            product = p_el.get_text(strip=True) if p_el else ""
            price_text = text_el.get_text(strip=True)
            deals.append({"market": market,
                          "product": product or f"{market} ürün (otomatik)",
                          "price": extract_price(price_text),
                          "discount_pct": extract_discount(price_text),
                          "image_url": (card.find("img").get("src") if card and card.find("img") else None),
                          "ocr_text": price_text, "source_page": source_url})
    return deals


def scrape_market(market: str) -> List[PromoDeal]:
    """Scrape a single catalog and persist; returns created PromoDeals.

    Never raises — a failed market yields an empty list so the scheduler
    can continue with the remaining markets.
    """
    url = CATALOG_URLS.get(market.upper())
    if not url:
        logger.info("No catalog URL configured for %s; skipping", market)
        return []
    html = _fetch_html(url)
    deals = _parse_html_deals(market.upper(), html, url)
    if not deals:
        logger.info("No deals parsed for %s (URL: %s)", market, url)
        return []
    try:
        return save_promo_deals(deals)
    except Exception:
        logger.exception("Failed to persist %s deals; rolled back", market)
        return []


def scrape_all_markets() -> Dict[str, int]:
    results: Dict[str, int] = {}
    for market in CATALOG_URLS:
        results[market] = len(scrape_market(market))
    return results


def get_recent_deals(market: Optional[str] = None, hours: int = 72,
                     limit: int = 100) -> List[PromoDeal]:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    q = PromoDeal.query.filter(PromoDeal.scraped_at >= cutoff) \
                      .order_by(PromoDeal.scraped_at.desc())
    if market:
        q = q.filter(PromoDeal.market == market.upper())
    return q.limit(limit).all()


def _main():
    import argparse
    parser = argparse.ArgumentParser(description="Scrape market catalogs")
    parser.add_argument("--market", default=None, help="BIM, A101, SOK, or MIGROS")
    parser.add_argument("--all", action="store_true", help="Scrape all configured markets")
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