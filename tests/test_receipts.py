"""Tests for receipt upload + SMS commit endpoints (OCR mocked)."""
import io
from urllib.parse import quote
from unittest.mock import patch
from models import db, Account, Transaction


def _make_test_account(client, app, user, name='Default'):
    with app.app_context():
        a = Account(user_id=user.id, name=name, type='Cash',
                    initial_balance=1000.0, current_balance=1000.0)
        db.session.add(a)
        db.session.commit()
        db.session.refresh(a)
        return a


def test_receipt_upload_creates_transaction(client, app, user):
    """Mock OCR returns a known price; upload must create a Transaction."""
    acc = _make_test_account(client, app, user)
    fake_ocr_text = "Toplam: 250,00 ₺\nMarket"

    with patch('routes.receipts.image_to_text', return_value=fake_ocr_text), \
         patch('routes.receipts._OCR_OK', True):
        r = client.post('/receipts/upload',
                        data={'account_id': str(acc.id),
                              'receipt_file': (io.BytesIO(b'img'), 'test.png')},
                        content_type='multipart/form-data')
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body['ok'] is True
    assert body['amount'] == 250.0

    with app.app_context():
        txn = Transaction.query.filter_by(user_id=user.id).first()
        assert txn is not None
        assert txn.amount == 250.0


def test_receipt_upload_no_price_returns_422(client, app, user):
    """When OCR finds no price, the endpoint returns 422 with parsed text."""
    acc = _make_test_account(client, app, user)
    with patch('routes.receipts.image_to_text', return_value=''), \
         patch('routes.receipts._OCR_OK', True):
        r = client.post('/receipts/upload',
                        data={'account_id': str(acc.id),
                              'receipt_file': (io.BytesIO(b'img'), 'empty.png')},
                        content_type='multipart/form-data')
    assert r.status_code == 422
    body = r.get_json()
    assert body['ok'] is False


def test_receipts_page_shows_manual_entry_fields(client, app, user):
    """Fix #3: the receipts page always renders the manual amount / category /
    description form (used when OCR cannot find a price)."""
    _make_test_account(client, app, user)
    r = client.get('/receipts')
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert 'name="manual_amount"' in text
    assert 'name="manual_category"' in text
    assert 'Manuel giriş' in text


def test_receipt_upload_manual_amount_when_ocr_fails(client, app, user):
    """Fix #3: when OCR cannot find a price, manual_amount / manual_category
    / description create a valid Transaction on resubmit."""
    acc = _make_test_account(client, app, user)
    with patch('routes.receipts.image_to_text', return_value=''), \
         patch('routes.receipts._OCR_OK', True):
        # 1st attempt — no manual values, still 422.
        r = client.post('/receipts/upload',
                        data={'account_id': str(acc.id),
                              'receipt_file': (io.BytesIO(b'img'), 'empty.png')},
                        content_type='multipart/form-data')
        assert r.status_code == 422

        # 2nd attempt — user filled the manual fields on the page.
        r = client.post('/receipts/upload',
                        data={'account_id': str(acc.id),
                              'manual_amount': '150,75',
                              'manual_category': 'Market',
                              'description': 'Migros alışverişi',
                              'receipt_file': (io.BytesIO(b'img'), 'empty.png')},
                        content_type='multipart/form-data')
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body['ok'] is True
    assert body['amount'] == 150.75
    assert body['category'] == 'Market'

    with app.app_context():
        txn = Transaction.query.filter_by(user_id=user.id).first()
        assert txn is not None
        assert txn.amount == 150.75
        assert 'Migros' in txn.description

def test_sms_preview_returns_parsed_dict(client, app, user):
    sms = "Garanti Bankası kredi kartınızdan 300 TL harcama yapılmıştır."
    r = client.get('/api/parse-sms?message=' + quote(sms))
    assert r.status_code == 200
    body = r.get_json()
    assert body['bank'] == 'Garanti Bankası'
    assert body['amount'] == 300.0


def test_sms_commit_creates_transaction(client, app, user):
    acc = _make_test_account(client, app, user, name='CommitAcct')
    sms = "Ziraat Bankası 1.500 TL kart hareketi — Migros"
    r = client.post('/api/parse-sms', json={'message': sms, 'commit': True,
                                              'account_id': acc.id})
    assert r.status_code == 201, r.get_data(as_text=True)
    body = r.get_json()
    assert body['ok'] is True
    assert body['transaction_id'] is not None

    with app.app_context():
        txn = Transaction.query.get(body['transaction_id'])
        assert txn is not None
        assert txn.amount == 1500.0
        assert 'Ziraat' in (txn.description or '')