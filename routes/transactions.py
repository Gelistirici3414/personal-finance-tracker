import os
import csv
import io
import re
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, url_for, flash, Response, send_file, session
from werkzeug.utils import secure_filename
from ..models import db, Account, Transaction


transactions_bp = Blueprint('transactions', __name__)


def _filtered_transactions():
    uid = session['user_id']
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    filter_category = request.args.get('category')
    filter_account = request.args.get('account_id')

    query = Transaction.query.filter(Transaction.user_id == uid)
    if start_date:
        query = query.filter(Transaction.date >= datetime.strptime(start_date, '%Y-%m-%d').date())
    if end_date:
        query = query.filter(Transaction.date <= datetime.strptime(end_date, '%Y-%m-%d').date())
    if filter_category:
        query = query.filter(Transaction.category == filter_category)
    if filter_account:
        query = query.filter(Transaction.account_id == int(filter_account))

    return query.order_by(Transaction.date.desc())


@transactions_bp.route('/')
def list_transactions():
    uid = session['user_id']
    transactions = _filtered_transactions().all()
    accounts = Account.query.filter_by(user_id=uid).all()
    categories = db.session.query(Transaction.category).filter(
        Transaction.user_id == uid
    ).distinct().all()
    return render_template('transactions.html',
                           transactions=transactions,
                           accounts=accounts,
                           categories=categories)


@transactions_bp.route('/export')
def export_csv():
    uid = session['user_id']
    transactions = _filtered_transactions().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        'ID', 'Account', 'Type', 'Amount', 'Category',
        'Description', 'Person', 'Date'
    ])

    for t in transactions:
        writer.writerow([
            t.id,
            t.account.name,
            t.type,
            t.amount,
            t.category,
            t.description or '',
            t.person_name or '',
            t.date.isoformat(),
        ])

    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={
            'Content-Disposition':
                'attachment; filename=transactions.csv'
        }
    )


@transactions_bp.route('/delete/<int:id>', methods=['POST', 'GET'])
def delete_transaction(id):
    uid = session['user_id']
    transaction = Transaction.query.filter_by(id=id, user_id=uid).first()
    if transaction is None:
        flash('Transaction not found', 'error')
        return redirect(url_for('transactions.list_transactions'))
    account = Account.query.filter_by(id=transaction.account_id, user_id=uid).first()
    if account is not None:
        if transaction.type == 'income':
            account.current_balance -= transaction.amount
        else:
            account.current_balance += transaction.amount
        db.session.add(account)
    db.session.delete(transaction)
    db.session.commit()
    flash('Transaction deleted successfully', 'success')
    return redirect(request.referrer or url_for('transactions.list_transactions'))


@transactions_bp.route('/add', methods=['GET', 'POST'])
def add_transaction():
    uid = session['user_id']
    if request.method == 'POST':
        account_id = int(request.form.get('account_id'))
        trans_type = request.form.get('type')
        amount = float(request.form.get('amount'))
        category = request.form.get('category')
        description = request.form.get('description')
        person_name = request.form.get('person_name')
        date_str = request.form.get('date')
        date_obj = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else datetime.utcnow().date()

        transaction = Transaction(
            user_id=uid,
            account_id=account_id,
            type=trans_type,
            amount=amount,
            category=category,
            description=description,
            person_name=person_name,
            date=date_obj
        )
        db.session.add(transaction)

        # Update account balance
        account = Account.query.filter_by(id=account_id, user_id=uid).first()
        if account:
            if trans_type == 'income':
                account.current_balance += amount
            else:
                account.current_balance -= amount
            db.session.add(account)

        db.session.commit()
        flash('Transaction added successfully', 'success')
        return redirect(url_for('transactions.list_transactions'))

    accounts = Account.query.filter_by(user_id=uid).all()
    receipt_data = None
    prefill = session.pop('prefill_transaction', None)
    if prefill:
        receipt_data = prefill
        session.modified = True
    return render_template('add_transaction.html', accounts=accounts,
                           receipt_data=receipt_data, last_receipt=None)


# ---------------------------------------------------------------------------
# Receipt / OCR Scanning (Simple Upload & Parse)
# ---------------------------------------------------------------------------
CATEGORY_KEYWORDS = [
    ("Market", ["market", "bim", "a101", "sok", "migros", "seyir", "buyuk", "suk", "grocery", "mutfak"]),
    ("Bills", ["fatura", "elektrik", "su", "dogal", "doğal", "internet", "mobil", "telefon", "epay", "gaz", "abon"]),
    ("Fuel", ["akaryakit", "benzin", "motorin", "oil", "fuel", "istasyon", "petrol", "otomotiv"]),
    ("Debt", ["kredi", "borrow", "deb", "alacak", "alacak", "ödünç", "taksit", "kart"]),
    ("Personal", ["giyim", "kus", "ozel", "personal", "giys", "kose", "kozmetik", "cosmetic"]),
    ("Transport", ["ulasim", "toplu", "otobus", "metro", "taksi", "taxi", "public", "feri"],),
    ("Entertainment", ["sinema", "muzik", "game", "oyun", "ticket", "film", "konser"]),
    ("Health", ["eczane", "pharmacy", "doctor", "doktor", "hospital", "hastane", "medical"]),
]

DEFAULT_CATEGORY = "Market"


def _infer_category(text):
    lowered = text.lower()
    for category, keywords in CATEGORY_KEYWORDS:
        if any(k.lower() in lowered for k in keywords):
            return category
    return DEFAULT_CATEGORY


def _extract_receipt_text(file_stream):
    """Best-effort OCR-like text extraction.

    Tries OCR (pytesseract + PIL) if available; otherwise falls back to
    reading embedded text from PDFs. The extracted text is then analysed with
    simple regex to pull out amount / category / description fields.
    """
    filename = secure_filename(file_stream.filename or "")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # 1) Attempt real OCR for images when the libraries are present.
    try:
        from PIL import Image  # noqa
        import pytesseract  # noqa
        import tempfile

        if ext in ("jpg", "jpeg", "png", "bmp", "tiff"):
            with tempfile.NamedTemporaryFile(suffix=ext) as tmp:
                file_stream.save(tmp.name)
                img = Image.open(tmp.name)
                return pytesseract.image_to_string(img)
    except Exception:
        pass

    # 2) Attempt PDF text extraction when PyPDF2 is installed.
    if ext == "pdf":
        try:
            import PyPDF2
            import tempfile
            with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
                file_stream.save(tmp.name)
                reader = PyPDF2.PdfReader(tmp.name)
                return "\n".join((p.extract_text() or "") for p in reader.pages)
        except Exception:
            pass

    # 3) Fallback: read any ASCII text content from image/PDF.
    try:
        raw = file_stream.read()
        import contextlib
        with contextlib.suppress(Exception):
            return raw.decode("utf-8")
    except Exception:
        return ""


def _parse_receipt(text):
    """Produce a pre-filled transaction dict from extracted receipt text."""
    result = {
        "amount": None,
        "category": DEFAULT_CATEGORY,
        "description": "Yüklenen fiş (OCR)",
        "raw_text": text[:1000] if text else "",
    }

    if not text:
        # No OCR library or empty file; let the user fill manually.
        return result

    result["category"] = _infer_category(text)

    # Total amount: look for lines containing "Total" / "Tutar" / "Genel".
    amount = None
    for line in text.splitlines():
        if re.search(r"(?i)(total|tutar|genel\s*toplam|grand)", line):
            nums = re.findall(r"\d+(?:[.,]\d+)?", line)
            if nums:
                amount = float(nums[-1].replace(",", "."))

    # If no labelled total, take the largest number on its own line as total.
    if amount is None:
        standalone = [float(n.replace(",", ".")) for n in re.findall(r"^\s*\d+(?:[.,]\d+)?\s*$", text, re.M)]
        if standalone:
            amount = max(standalone)

    result["amount"] = amount

    # Description: first meaningful line that is not purely numeric.
    for line in text.splitlines():
        line = line.strip()
        if line and not re.fullmatch(r"[\d\s.,:;:%\-/]+", line) and len(line) > 3:
            result["description"] = line[:150]
            break

    return result


@transactions_bp.route('/upload-receipt', methods=['GET', 'POST'])
def upload_receipt():
    """Standalone page where the user uploads a receipt image/PDF, the app
    attempts to parse it, and the parsed fields are used to pre-fill the
    transaction form."""
    parsed = None
    if request.method == 'POST':
        uploaded = request.files.get('receipt_file')
        text = ""
        if uploaded and uploaded.filename:
            text = _extract_receipt_text(uploaded)
        parsed = _parse_receipt(text)

        # If parsing succeeded, jump to the add form pre-filled via session.
        if parsed.get("amount"):
            session['prefill_transaction'] = parsed
            flash('Fiş tarandı ve form dolduruldu.', 'success')
            return redirect(url_for('transactions.add_transaction'))

    accounts = Account.query.filter_by(user_id=session['user_id']).all()
    return render_template('upload_receipt.html', accounts=accounts,
                           receipt_data=parsed, last_receipt=None,
                           standalone=True)