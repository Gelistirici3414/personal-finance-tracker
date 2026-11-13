"""Tests for the market-catalog scraper (mocked HTTP + OCR)."""
from unittest.mock import patch
from models import db, PromoDeal
from services import scrapers


def test_save_promo_deals_persists(monkeypatch, app, user):
    """save_promo_deals persists a batch of deals without crashing."""
    with app.app_context():
        created = scrapers.save_promo_deals([
            {'market': 'BIM', 'product': 'Süt 1L', 'price': 32.5,
             'discount_pct': 15.0, 'source_page': 'https://example.com/bim'},
            {'market': 'A101', 'product': 'Ekmek', 'price': 10.0,
             'discount_pct': None, 'source_page': 'https://example.com/a101'},
        ])
        assert len(created) == 2
        assert PromoDeal.query.filter_by(product='Süt 1L').first().price == 32.5
        # Roll back so we don't pollute later tests.
        db.session.rollback()


def test_scrape_market_returns_empty_on_fetch_failure(monkeypatch, app, user):
    """When _fetch_html returns None (network down) scrape_market yields []."""
    with app.app_context():
        with patch.object(scrapers, '_fetch_html', return_value=None):
            result = scrapers.scrape_market('BIM')
        assert result == []
        # No PromoDeal rows created by this call.
        assert PromoDeal.query.count() == 0


def test_parse_html_deals_structured_card(app, user):
    """data-* attribute cards are preferred over text fallback."""
    html = '''
    <div data-product="Yarım Fiyat Yağ" data-price="89,90" data-discount="50">
      <img src="https://cdn.example.com/yag.jpg">
    </div>
    '''
    deals = scrapers._parse_html_deals('BIM', html, 'https://x')
    assert len(deals) == 1
    assert deals[0]['product'] == 'Yarım Fiyat Yağ'
    assert deals[0]['price'] == 89.9
    assert deals[0]['discount_pct'] == 50.0
    assert deals[0]['image_url'].startswith('https://cdn.example.com/')


def test_parse_html_deals_text_fallback(app, user):
    """When no data-* cards exist, fall back to price-tag text."""
    html = '''
    <div class="product-card">
        <span class="title">Makarna Kampanyası</span>
        <span class="price">2. ürün 10,00₺</span>
    </div>
    '''
    deals = scrapers._parse_html_deals('BIM', html, 'https://x')
    assert len(deals) >= 1
    assert any('Makarna' in d['product'] for d in deals)


def test_scrape_all_markets_maps_each_market(app, user, monkeypatch):
    """scrape_all_markets returns one count per configured market."""
    with patch.object(scrapers, 'scrape_market', return_value=[]) as m:
        results = scrapers.scrape_all_markets()
    assert set(results.keys()) == set(scrapers.CATALOG_URLS.keys())
    assert all(v == 0 for v in results.values())
    assert m.call_count == len(scrapers.CATALOG_URLS)