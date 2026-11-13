"""Receipt OCR + SMS parsing — smart data entry.

All endpoints require a logged-in session.

    GET  /receipts                  → upload page
    POST /receipts/upload           → upload image/PDF, OCR, create Transaction
    GET  /api/parse-sms             → preview-parsed SMS dict
    POST /api/parse-sms/commit     → commit the parsed SMS as a Transaction
"""
import logging
from datetime import datetime
from typing import Optional

from flask import Blueprint, jsonify, render_template, request, session
from models import db, Account, Transaction

receipt_bp = Blueprint('receipt', __name__)
logger = logging.getLogger(__name__)

try:
    from services.ocr import image_to_text, pdf_to_text, parse_receipt_text
    from services.sms_parser import parse_bank_sms
    _OCR_OK = True
except Exception:
    image_to_text = pdf_to_text = parse_receipt_text = parse_bank_sms = None
    _OCR_OK = False


def _infer_category(text: str) -> str:
    lowered = (text or '').lower()
    for category, kws in {
        'Market': ['market', 'bim', 'a101', 'sok', 'migros', 'seyir'],
        'Bills': ['fatura', 'elektrik', 'su', 'gaz', 'internet'],
        'Fuel': ['akaryakit', 'benzin', 'motorin', 'petrol'],
        'Health': ['eczane', 'pharmacy', 'hastane', 'doktor'],
        'Transport': ['metro', 'otobus', 'taksi', 'ulasim'],
        'Personal': ['giyim', 'kozmetik', 'ozel'],
    }.items():
        if any(k in lowered for k in kws):
            return category
    return 'Market'


@receipt_bp.route('/receipts')
def receipts_page():
    accounts = Account.query.filter_by(user_id=session['user_id']).all()
    return render_template('receipts.html', accounts=accounts, ocr_available=_OCR_OK)


@receipt_bp.route('/receipts/upload', methods=['POST'])
def upload_receipt():
    uid = session.get('user_id')
    if not uid:
        return jsonify(error='authentication required'), 401
    uploaded = request.files.get('receipt_file')
    if not uploaded or not uploaded.filename:
        return jsonify(error='no file provided'), 400

    raw = uploaded.read()
    ext = uploaded.filename.rsplit('.', 1)[-1].lower() if '.' in uploaded.filename else ''
    text = (pdf_to_text(raw) if ext == 'pdf' else image_to_text(raw, uploaded.filename)) \
        if _OCR_OK else ''
    parsed = parse_receipt_text(text) if text else {}

    price = parsed.get('price')
    category = request.form.get('category') or _infer_category(text or uploaded.filename)
    desc_lines = (request.form.get('description') or (text or '').strip().splitlines())
    description = desc_lines[0][:150] if desc_lines and desc_lines[0] else 'Yüklenen fiş (OCR)'

    if price is None:
        return jsonify({
            'ok': False,
            'ocr_text': text,
            'parsed': parsed,
            'message': 'OCR aktif değil; alanları elle doldurun.' if not _OCR_OK
                       else 'Fişten fiyat çıkarılamadı; lütfen miktarı elle girin.',
        }), 422

    account = Account.query.filter_by(id=request.form.get('account_id'), user_id=uid).first() \
        if request.form.get('account_id') else _default_account(uid)
    if account is None:
        return jsonify(error='account required — önce bir hesap oluşturun'), 403

    try:
        txn = Transaction(user_id=uid, account_id=account.id, type='expense',
                          amount=float(price), category=category,
                          description=description, date=datetime.utcnow().date())
        db.session.add(txn)
        account.current_balance -= float(price)
        db.session.add(account)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return jsonify(ok=True, transaction_id=txn.id, account=account.name,
                   category=category, amount=float(price), ocr_text=text), 201


@receipt_bp.route('/api/parse-sms', methods=['GET', 'POST'])
def api_parse_sms():
    uid = session.get('user_id')
    if not uid:
        return jsonify(error='authentication required'), 401

    if request.method == 'GET':
        message = request.args.get('message', '')
        parsed = parse_bank_sms(message) if parse_bank_sms else {}
        return jsonify(parsed)

    # POST — optional commit
    json_body = request.get_json(silent=True) or {}
    message = request.form.get('message') or json_body.get('message', '')
    if not message:
        return jsonify(error='message is required'), 400

    parsed = parse_bank_sms(message) if parse_bank_sms else {}

    should_commit = request.form.get('commit') or json_body.get('commit')
    if should_commit:
        if not parsed.get('amount'):
            return jsonify(error='could not parse amount from SMS'), 422
        account_id = request.form.get('account_id') or json_body.get('account_id')
        account = (Account.query.filter_by(id=account_id, user_id=uid).first()
                   if account_id else _default_account(uid))
        if account is None:
            return jsonify(error='account required'), 403
        try:
            txn = Transaction(
                user_id=uid,
                account_id=account.id,
                type='expense',
                amount=float(parsed['amount']),
                category=parsed.get('category') or 'Market',
                description=(f"SMS: {parsed.get('bank') or 'banka'} — "
                             f"{parsed.get('merchant') or 'harcama'}"),
                date=parsed.get('date') or datetime.utcnow().date(),
            )
            db.session.add(txn)
            account.current_balance -= float(parsed['amount'])
            db.session.add(account)
            db.session.commit()
            return jsonify(ok=True, transaction_id=txn.id, parsed=parsed), 201
        except Exception:
            db.session.rollback()
            return jsonify(error='commit failed'), 500

    return jsonify(parsed)