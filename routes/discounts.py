"""Discounts & market deals — unified page serving both scraped (real-time)
and mock (fallback) offers for BİM, A101, ŞOK and Migros weekly catalogues.

Endpoints:
    GET  /discounts              → HTML list with filters (market, search)
    GET  /api/discounts          → JSON list of recent deals
    POST /api/discounts/refresh  → trigger an immediate scrape cycle

The scraper runs in a background worker (run_scheduler.py).  This module
queries PromoDeal from the database; if none are found it falls back to
hard-coded mock deals so the UI is never empty.
"""
import logging
from flask import Blueprint, render_template, request, jsonify, session
from models import PromoDeal
from services.scrapers import get_recent_deals, scrape_all_markets, CATALOG_URLS
from services.scheduler import is_running

logger = logging.getLogger(__name__)

discounts_bp = Blueprint('discounts', __name__)

# Fallback mock data when no scraped deals are available.
MARKET_DEALS = [
    {"market": "BİM",   "product": "Yarım Değerine Yağ",          "discount_pct": 50.0,  "price": 89.90,  "brochure": "Yarın Yine Yarım Fiyat"},
    {"market": "BİM",   "product": "Toz Şeker 5kg",                "discount_pct": None,  "price": 250.00, "brochure": "Sıkça Aranan"},
    {"market": "BİM",   "product": "Makarna Kampanyası",           "discount_pct": None,  "price": 10.00,  "brochure": "Sıkça Aranan", "note": "2. ürün"},
    {"market": "BİM",   "product": "Deterjan 2x1",                 "discount_pct": None,  "price": None,   "brochure": "Sıkça Aranan", "note": "1 Al 1 Kazan"},
    {"market": "A101",  "product": "Süt İşleri Çeşitli",           "discount_pct": 30.0,  "price": None,   "brochure": "A101 Kataloğu"},
    {"market": "A101",  "product": "Kartofel Çeşitleri",           "discount_pct": 40.0,  "price": None,   "brochure": "Sezon Fiyatı"},
    {"market": "A101",  "product": "Etiyem Kampanyası",            "discount_pct": 50.0,  "price": None,   "brochure": "A101 Kataloğu", "note": "İkincisi %50"},
    {"market": "ŞOK",   "product": "Yerli Domates",                "discount_pct": None,  "price": 300.00, "brochure": "Şok Kampanya", "note": "1kg"},
    {"market": "ŞOK",   "product": "Beyaz Eşya Dönüşümü",          "discount_pct": 20.0,  "price": None,   "brochure": "Broşür"},
    {"market": "ŞOK",   "product": "Grip ve Soğuk Algınlığı",      "discount_pct": 35.0,  "price": None,   "brochure": "Şok Kampanya"},
    {"market": "Migros","product": "Migros Hediye Çeki",           "discount_pct": None,  "price": 200.00, "brochure": "Migros Sanal Market"},
    {"market": "Migros","product": "Kahvaltılıklar",               "discount_pct": 25.0,  "price": None,   "brochure": "Migros Kataloğu"},
    {"market": "Migros","product": "Kartına Özgü 100₺ Bonus",     "discount_pct": None,  "price": 100.00, "brochure": "Migros Kart"},
]


def _format_mock(deal):
    """Convert a mock deal dict into the same shape as a PromoDeal ORM row."""
    return {
        'id': None,
        'market': deal['market'],
        'product': deal['product'],
        'price': deal.get('price'),
        'discount_pct': deal.get('discount_pct'),
        'image_url': None,
        'scraped_at': None,
        'source_page': deal.get('brochure'),
    }


@discounts_bp.route('/discounts')
def discounts():
    uid = session.get('user_id')
    market = request.args.get('market')
    search = request.args.get('search', '').strip().lower()
    hours = int(request.args.get('hours', 168))

    # Load real scraped deals first.
    deals = get_recent_deals(market=market, hours=hours, limit=500)
    # Fall back to mock data when DB is empty or user is not logged in.
    if not deals and not uid:
        deals = [_format_mock(d) for d in MARKET_DEALS]

    if search:
        deals = [d for d in deals
                 if search in (d.product or '').lower()
                 or search in (getattr(d, 'market', '') or '').lower()
                 or search in (getattr(d, 'ocr_text', '') or '').lower()]

    markets = list(CATALOG_URLS.keys())
    return render_template('discounts.html', deals=deals, markets=markets,
                           active_market=market or '', search=search,
                           is_scraping=is_running())


@discounts_bp.route('/api/discounts', methods=['GET'])
def api_discounts():
    if not session.get('user_id'):
        return jsonify(error='authentication required'), 401
    market = request.args.get('market')
    search = request.args.get('search', '').strip().lower()
    hours = int(request.args.get('hours', 168))

    deals = get_recent_deals(market=market, hours=hours, limit=500)
    if not deals:
        deals = [_format_mock(d) for d in MARKET_DEALS]
    if search:
        deals = [d for d in deals
                 if search in (d.product or '').lower()
                 or search in (getattr(d, 'market', '') or '').lower()]

    return jsonify([
        {'id': getattr(d, 'id', None), 'market': getattr(d, 'market', ''),
         'product': getattr(d, 'product', ''), 'price': getattr(d, 'price', None),
         'discount_pct': getattr(d, 'discount_pct', None),
         'image_url': getattr(d, 'image_url', None),
         'scraped_at': getattr(d, 'scraped_at', None).isoformat() if getattr(d, 'scraped_at', None) else None}
        for d in deals
    ])


@discounts_bp.route('/api/discounts/refresh', methods=['POST'])
def api_refresh():
    if not session.get('user_id'):
        return jsonify(error='authentication required'), 401
    try:
        results = scrape_all_markets()
        return jsonify(status='ok', scraped=results), 200
    except Exception:
        logger.exception("Scrape failed during manual refresh")
        return jsonify(error='scrape failed'), 500