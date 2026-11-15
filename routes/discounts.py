"""Discounts & market deals — unified page serving REAL, live-scraped
offers for BİM, A101, ŞOK and Migros weekly catalogues.

Endpoints:
    GET  /discounts              → HTML list with filters (market, search)
    GET  /api/discounts          → JSON list of recent deals
    POST /api/discounts/refresh  → trigger an immediate scrape cycle

The scraper runs in a background worker (run_scheduler.py).  This module
queries PromoDeal from the database and shows ONLY real scraped data.
When no recent data is available the page shows an honest
"Anlık indirim verisi alınamadı" warning — never fake/mock products.
"""
import logging
from flask import Blueprint, render_template, request, jsonify, session
from models import PromoDeal
from services.scrapers import (get_recent_deals, scrape_all_markets,
                               CATALOG_URLS, get_last_scraped_per_market)
from services.scheduler import is_running

logger = logging.getLogger(__name__)

discounts_bp = Blueprint('discounts', __name__)


@discounts_bp.route('/discounts')
def discounts():
    market = request.args.get('market')
    search = request.args.get('search', '').strip().lower()
    hours = int(request.args.get('hours', 168))

    # Real scraped data only — mock offers are intentionally NOT injected.
    deals = get_recent_deals(market=market, hours=hours, limit=500)
    if search:
        deals = [d for d in deals
                 if search in (d.product or '').lower()
                 or search in (getattr(d, 'market', '') or '').lower()
                 or search in (getattr(d, 'ocr_text', '') or '').lower()]

    markets = list(CATALOG_URLS.keys())
    last_scraped = get_last_scraped_per_market()
    return render_template('discounts.html', deals=deals, markets=markets,
                           active_market=market or '', search=search,
                           is_scraping=is_running(),
                           last_scraped=last_scraped)


def _deal_to_dict(d):
    """Normalise a PromoDeal ORM row into a JSON-safe shape."""
    scraped = getattr(d, 'scraped_at', None)
    return {'id': getattr(d, 'id', None),
            'market': getattr(d, 'market', '') or '',
            'product': getattr(d, 'product', '') or '',
            'price': getattr(d, 'price', None),
            'discount_pct': getattr(d, 'discount_pct', None),
            'image_url': getattr(d, 'image_url', None),
            'scraped_at': scraped.isoformat() if scraped else None}


@discounts_bp.route('/api/discounts', methods=['GET'])
def api_discounts():
    if not session.get('user_id'):
        return jsonify(error='authentication required'), 401
    market = request.args.get('market')
    search = request.args.get('search', '').strip().lower()
    hours = int(request.args.get('hours', 168))

    # Real data only — an empty catalogue returns an empty JSON list.
    deals = get_recent_deals(market=market, hours=hours, limit=500)
    if search:
        deals = [d for d in deals
                 if search in (getattr(d, 'product', '') or '').lower()
                 or search in (getattr(d, 'market', '') or '').lower()]

    return jsonify([_deal_to_dict(d) for d in deals])


@discounts_bp.route('/api/discounts/refresh', methods=['POST'])
def api_refresh():
    if not session.get('user_id'):
        return jsonify(error='authentication required'), 401
    try:
        results = scrape_all_markets()
        total = sum(int(n) for n in results.values())
        if total == 0:
            # Honest status: nothing could be fetched — the page will show
            # the "veri alınamadı" warning instead of fake products.
            return jsonify(status='unavailable',
                           message=('Anlık indirim verisi alınamadı, '
                                    'lütfen tekrar deneyin.'),
                           scraped=results), 200
        return jsonify(status='ok', scraped=results), 200
    except Exception:
        logger.exception("Scrape failed during manual refresh")
        return jsonify(status='error',
                       message='Yenileme hatası; mevcut liste korunuyor.',
                       error='scrape failed'), 500


