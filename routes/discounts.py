from flask import Blueprint, render_template, request

discounts_bp = Blueprint('discounts', __name__)

# Mock "scraped" promotional deals for major Turkish markets.
MARKET_DEALS = [
    # BİM
    {"market": "BİM", "product": "Yarım Değerine Yağ", "discount": "%50", "price": "89,90₺", "brochure": "Yarın Yine Yarım Fiyat"},
    {"market": "BİM", "product": "Toz Şeker 5kg", "discount": "250₺'ye", "price": "250,00₺", "brochure": "Sıkça Aranan"},
    {"market": "BİM", "product": "Makarna Kampanyası", "discount": "2. 10₺", "price": "10,00₺ /2. ürün", "brochure": "Sıkça Aranan"},
    {"market": "BİM", "product": "Deterjan 2x1", "discount": "1 Al 1 Kazan", "price": "-", "brochure": "Sıkça Aranan"},
    # A101
    {"market": "A101", "product": "Süt İşleri Çeşitli", "discount": "%30", "price": "Fiyat etiketinde", "brochure": "A101 Kataloğu"},
    {"market": "A101", "product": "Kartofel Çeşitleri", "discount": "%40", "price": "Fiyat etiketinde", "brochure": "Sezon Fiyatı"},
    {"market": "A101", "product": "Etiyem Kampanyası", "discount": "İkincisi %50", "price": "-", "brochure": "A101 Kataloğu"},
    # ŞOK
    {"market": "ŞOK", "product": "Yerli Domates", "discount": "300₺'ye 1kg", "price": "300,00₺", "brochure": "Şok Kampanya"},
    {"market": "ŞOK", "product": "Beyaz Eşya Dönüşümü", "discount": "%20", "price": "Fiyat etiketinde", "brochure": "Broşür"},
    {"market": "ŞOK", "product": "Grip ve Soğuk Algınlığı", "discount": "%35", "price": "Fiyat etiketinde", "brochure": "Şok Kampanya"},
    # Migros
    {"market": "Migros", "product": "Migros Hediye Çeki", "discount": "200₺ çeki", "price": "-", "brochure": "Migros Sanal Market"},
    {"market": "Migros", "product": "Kahvaltılıklar", "discount": "%25", "price": "Fiyat etiketinde", "brochure": "Migros Kataloğu"},
    {"market": "Migros", "product": "Kartına Özgü 100₺ Bonus", "discount": "100₺", "price": "-", "brochure": "Migros Kart"},
]


@discounts_bp.route('/discounts')
def discounts():
    market = request.args.get('market')
    search = request.args.get('search', '').strip().lower()

    deals = MARKET_DEALS
    if market:
        deals = [d for d in deals if d['market'] == market]
    if search:
        deals = [d for d in deals if search in d['product'].lower() or search in d['market'].lower()]

    markets = ['BİM', 'A101', 'ŞOK', 'Migros']
    return render_template('discounts.html', deals=deals, markets=markets,
                           active_market=market or '', search=search)