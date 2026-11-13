"""Promotions / market-deals routes.

Pages:
    GET /promotions            → list recent scraped deals (filters: market, search)
    GET /api/promotions        → JSON version of the same

Background:
    POST /api/promotions/refresh  → trigger an immediate scrape cycle
                                    (auth: any logged-in user; rate-limited in prod)
"""
from flask import Blueprint, render_template, request, jsonify, session
from models import PromoDeal
from services.scrapers import get_recent_deals, scrape_all_markets, CATALOG_URLS
from services.scheduler import is_running

promo_bp = Blueprint('promo', __name__)


@promo_bp.route('/promotions')
def promotions():
    uid = session.get('user_id')
    market = request.args.get('market')
    search = request.args.get('search', '').strip().lower()
    hours = int(request.args.get('hours', 168))  # default 7 days

    deals = get_recent_deals(market=market, hours=hours, limit=500)
    if search:
        deals = [d for d in deals
                 if search in (d.product or '').lower()
                 or search in (d.market or '').lower()
                 or search in (d.ocr_text or '').lower()]

    markets = list(CATALOG_URLS.keys())
    return render_template('promotions.html', deals=deals, markets=markets,
                           active_market=market or '', search=search,
                           is_scraping=is_running())


@promo_bp.route('/api/promotions', methods=['GET'])
def api_promotions():
    if not session.get('user_id'):
        return jsonify(error='authentication required'), 401
    market = request.args.get('market')
    search = request.args.get('search', '').strip().lower()
    hours = int(request.args.get('hours', 168))

    deals = get_recent_deals(market=market, hours=hours, limit=500)
    if search:
        deals = [d for d in deals
                 if search in (d.product or '').lower()
                 or search in (d.market or '').lower()]
    return jsonify([
        {'id': d.id, 'market': d.market, 'product': d.product,
         'price': d.price, 'discount_pct': d.discount_pct,
         'image_url': d.image_url, 'scraped_at': d.scraped_at.isoformat() if d.scraped_at else None}
        for d in deals
    ])


@promo_bp.route('/api/promotions/refresh', methods=['POST'])
def api_refresh():
    if not session.get('user_id'):
        return jsonify(error='authentication required'), 401
    # Fire an in-process scrape now (blocking, but fast because it reuses
    # the shared connection pool).  The scheduler is a separate process
    # that will also run periodically.
    results = scrape_all_markets()
    return jsonify(status='ok', scraped=results), 200