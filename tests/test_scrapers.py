"""Tests for the real-data market-catalog scraper (mocked HTTP).

No mock/fake offer data exists anywhere in the pipeline: when a fetch
fails the scraper must return [] and the discounts page must show the
honest "anlık indirim verisi alınamadı" warning.
"""
from unittest.mock import patch
from models import db, PromoDeal
from services import scrapers


def test_save_promo_deals_persists(app, user):
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
        db.session.rollback()


def test_scrape_market_returns_empty_on_fetch_failure(app, user):
    """When _fetch_html returns None (network down) scrape_market yields
    [] — never fake products."""
    with app.app_context():
        with patch.object(scrapers, '_fetch_html', return_value=None):
            result = scrapers.scrape_market('BIM')
        assert result == []
        assert PromoDeal.query.count() == 0


def test_scrape_market_drops_priceless_deals(app, user):
    """Deals without a parsed price are discarded (real data only)."""
    with app.app_context():
        with patch.object(scrapers, '_fetch_html',
                          return_value='no prices here'):
            result = scrapers.scrape_market('SOK')
        assert result == []


def test_parse_bim_cards(app, user):
    """BİM DOM (div.product + priceArea) parses Turkish '289,00' prices.

    Real page layout: <h2.subTitle> = brand, <h2.title> = product name;
    priceArea contains '289,' + kusurArea '00' + curr '₺'.
    """
    html = '''
    <div class="product">
      <a href="/aktuel-urunler/dana-kangal-sucuk-coskun-400-g/aktuel.aspx">
        <img src="https://cdn1.bim.com.tr/uploads/aktuel-urunler/x.jpg" />
      </a>
      <div class="descArea">
        <h2 class="subTitle">COŞKUN</h2>
        <h2 class="title">DANA KANGAL SUCUK 400 G</h2>
      </div>
      <div class="priceArea">
        <a href="#"><div class="text quantify">289,</div>
        <div class="kusurArea"><span class="number">00</span></div>
        <span class="curr">₺</span></a>
      </div>
    </div>
    <div class="product">
      <div class="descArea">
        <h2 class="subTitle">ATMOSFER</h2>
        <h2 class="title">AYÇİÇEĞİ YAĞI 1L</h2>
      </div>
      <div class="priceArea">
        <a href="#"><div class="text quantify">125,</div>
        <div class="kusurArea"><span class="number">50</span></div>
        <span class="curr">₺</span></a>
      </div>
    </div>
    '''
    deals = [d for d in scrapers._parse_html_deals(
        'BIM', html, 'https://bim.com.tr/x') if d['price'] is not None]
    assert len(deals) == 2, deals
    by_product = {d['product']: d for d in deals}
    kangal = by_product.get('DANA KANGAL SUCUK 400 G COŞKUN')
    assert kangal is not None, by_product
    assert kangal['price'] == 289.0
    assert kangal['image_url'] and 'aktuel-urunler' in kangal['image_url']
    ayicik = by_product.get('AYÇİÇEĞİ YAĞI 1L ATMOSFER')
    assert ayicik is not None, by_product
    assert ayicik['price'] == 125.5


def test_parse_sok_cards(app, user):
    """ŞOK DOM (data-testid price boxes) parses discounted prices."""
    html = '''
    <div class="CProductCard-module_wrap__xyz">
      <a>
        <img class="CProductCard-module_image__abc"
             src="/x.jpg" alt="product-thumb"/>
        <div class="CProductCard-module_infoContainer__F8uxY">
          <h2 class="CProductCard-module_title__u8bMW">
            Dp Fıstık Sütü 200 Ml</h2>
          <div class="CPriceBox-module_cPriceBox__1OWBR">
            <div class="CPriceBox-module_price__bYk-c" data-testid="price">
              189,00₺</div>
            <span class="CPriceBox-module_discountedPrice__15Ffw"
                  data-testid="discountedPrice">109,00₺</span>
          </div>
        </div>
      </a>
    </div>
    '''
    deals = scrapers._parse_html_deals('SOK', html, 'https://sok.com.tr/')
    assert len(deals) >= 1
    d = deals[0]
    assert d['product'].startswith('Dp Fıstık')
    assert d['price'] == 109.0          # discounted price wins
    assert d['discount_pct'] == round((189.0 - 109.0) / 189.0 * 100, 1)


def test_parse_generic_cards(app, user):
    """Generic card heuristic (A101/Migros) extracts named priced cards."""
    html = '''
    <div class="product-card">
      <h3 class="title">Makarna Kampanyası</h3>
      <span class="price">10,00₺</span>
    </div>
    <div class="product-card">
      <h3 class="title">Ayçiçeği Yağı 1L</h3>
      <span class="price">180,50₺</span>
    </div>
    <div class="product-card">
      <h3 class="title">Kekik 100 G</h3>
      <span class="price">45,00₺</span>
    </div>
    '''
    deals = [d for d in scrapers._parse_html_deals(
        'A101', html, 'https://a101.com.tr/') if d['price'] is not None]
    assert len(deals) >= 3, deals
    assert any('Makarna' in d['product'] for d in deals)


def test_parse_jsonld(app, user):
    """JSON-LD Product blocks are parsed with Turkish price strings."""
    html = '''
    <script type="application/ld+json">
    [{"@type": "Product", "name": "Süper Ürün 1L",
      "image": "https://i/x.jpg",
      "offers": {"@type": "Offer", "price": "120,50",
                 "priceCurrency": "TRY"}},
     {"@type": "Product", "name": "Süper Ürün 2",
      "offers": {"@type": "Offer", "price": "30",
                 "priceCurrency": "TRY"}}]
    </script>
    '''
    deals = [d for d in scrapers._parse_html_deals(
        'MIGROS', html, 'https://migros.com.tr/') if d['price'] is not None]
    products = {d['product']: d['price'] for d in deals}
    assert products.get('Süper Ürün 1L') == 120.5
    assert products.get('Süper Ürün 2') == 30.0


def test_parse_empty_when_no_real_data(app, user):
    """A page with no recognisable product data yields no deals (never fake)."""
    html = '<html><body><div class="banner">Haftanın fırsatları</div></body></html>'
    assert scrapers._parse_html_deals(
        'MIGROS', html, 'https://migros.com.tr/') == []


def test_parse_price_tokens_turkish(app, user):
    assert scrapers._parse_price_tokens('289,00₺') == 289.0
    assert scrapers._parse_price_tokens('1.250,50 TL') == 1250.5
    assert scrapers._parse_price_tokens('150,00') == 150.0
    assert scrapers._parse_price_tokens('yüzde 20 indirim') is None
    assert scrapers._parse_price_tokens('') is None


def test_browser_headers_rotate(app, user):
    """Headers carry the full browser fingerprint set; UA rotates."""
    seen_uas = set()
    for _ in range(12):
        h = scrapers._browser_headers('BIM')
        assert 'Sec-Fetch-Mode' in h and 'sec-ch-ua' in h
        assert h.get('Referer', '').startswith('https://')
        seen_uas.add(h['User-Agent'])
    assert len(seen_uas) >= 2, "user-agent pool must rotate"


def test_scrape_all_markets_maps_each_market(app, user):
    """scrape_all_markets returns one count per configured market."""
    with patch.object(scrapers, 'scrape_market', return_value=[]) as m, \
         patch.object(scrapers.time, 'sleep'):
        results = scrapers.scrape_all_markets()
    assert set(results.keys()) == set(scrapers.CATALOG_URLS.keys())
    assert all(v == 0 for v in results.values())
    assert m.call_count == len(scrapers.CATALOG_URLS)

