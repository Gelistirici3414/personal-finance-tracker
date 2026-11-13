"""Tests for the OCR + bank-SMS parsers (pure-function unit tests)."""
from datetime import date
from services.ocr import extract_price, extract_discount, parse_receipt_text
from services.sms_parser import parse_bank_sms


def test_extract_price_total_keyword():
    text = "Toplam Tutar: 1.250,50 ₺\nMarket Alışverişi"
    assert extract_price(text) == 1250.50


def test_extract_price_standalone_line():
    text = "Süt 1L\n45,90\nEkmek\n10,00"
    assert extract_price(text) == 45.90


def test_extract_discount_pct_symbol():
    assert extract_discount("%30 indirim") == 30.0
    assert extract_discount("30% indirim") == 30.0
    assert extract_discount("İndirim %12,5") == 12.5


def test_parse_receipt_text_bundle():
    text = "Total: 150,75 ₺\n%20 indirim"
    result = parse_receipt_text(text)
    assert result['price'] == 150.75
    assert result['discount_pct'] == 20.0


def test_parse_receipt_empty_text():
    assert parse_receipt_text('') == {'price': None, 'discount_pct': None}


def test_parse_bank_sms_ziraat():
    msg = ("Ziraat Bankası kredi kartınızdan 1.234,56 TL harcama yapılmıştır. "
           "Kartınız: 1234, Mağaza: BİM")
    result = parse_bank_sms(msg)
    assert result['bank'] == 'Ziraat Bankası'
    assert result['amount'] == 1234.56
    assert result['card_last4'] == '1234'
    assert result['merchant'] is not None
    assert result['category']  # always a string


def test_parse_bank_sms_garanti_no_merchant():
    msg = "Garanti Bankası kredi kartınızdan 500 TL harcama yapılmıştır."
    result = parse_bank_sms(msg)
    assert result['bank'] == 'Garanti Bankası'
    assert result['amount'] == 500.0
    assert result['category'] == 'Market'


def test_parse_bank_sms_with_date():
    msg = ("İş Bankası 25.06.2025 tarihli kart hareketi: "
           "250,00 TL — Petrol Ofisi")
    result = parse_bank_sms(msg)
    assert result['date'] == date(2025, 6, 25)
    assert result['amount'] == 250.0


def test_parse_bank_sms_unknown_bank_still_extracts_amount():
    msg = "Kartınızdan 75,50 TL harcama yapılmıştır."
    result = parse_bank_sms(msg)
    assert result['bank'] is None
    assert result['amount'] == 75.50