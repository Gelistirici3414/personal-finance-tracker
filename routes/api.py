"""JSON API blueprint — programmatic access to all app data.

Endpoints (all require a logged-in session):
  GET  /api/health            → status, current user
  GET  /api/accounts          → list accounts
  POST /api/accounts          → create account
  DELETE /api/accounts/<id>   → delete account (cascades)
  GET  /api/transactions      → list transactions (filters: start_date, end_date, category, account_id)
  POST /api/transactions      → create transaction
  DELETE /api/transactions/<id> → delete transaction
  GET  /api/payments          → list upcoming payments
  GET  /api/debts            → list debts
  GET  /api/budgets          → list budgets
  GET  /api/export/csv       → CSV download of filtered transactions

All responses are JSON. 401 → not logged in, 403 → not the owner,
404 → not found, 400 → validation error.
"""
import csv
import io
from datetime import date, datetime
from flask import Blueprint, request, session, Response, jsonify
from models import db, Account, Transaction, UpcomingPayment, Budget

api_bp = Blueprint('api', __name__)


def _require_login():
    """Return (uid, error_response, error_code)."""
    uid = session.get('user_id')
    if not uid:
        return None, jsonify(error='authentication required'), 401
    return uid, None, None


def _parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


@api_bp.route('/health', methods=['GET'])
def health():
    uid, err, code = _require_login()
    if err:
        return err, code
    return jsonify(status='ok', user_id=uid, username=session.get('username'))


# --- Accounts ---
@api_bp.route('/accounts', methods=['GET'])
def list_accounts():
    uid, err, code = _require_login()
    if err:
        return err, code
    accounts = Account.query.filter_by(user_id=uid).all()
    return jsonify([
        {'id': a.id, 'name': a.name, 'type': a.type,
         'initial_balance': a.initial_balance, 'current_balance': a.current_balance}
        for a in accounts
    ])


@api_bp.route('/accounts', methods=['POST'])
def create_account():
    uid, err, code = _require_login()
    if err:
        return err, code
    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    acc_type = (data.get('type') or '').strip()
    if not name or not acc_type:
        return jsonify(error='name and type are required'), 400
    try:
        initial = float(data.get('initial_balance') or 0)
    except (TypeError, ValueError):
        return jsonify(error='initial_balance must be a number'), 400
    acct = Account(user_id=uid, name=name, type=acc_type,
                   initial_balance=initial, current_balance=initial)
    db.session.add(acct)
    db.session.commit()
    return jsonify(id=acct.id), 201


@api_bp.route('/accounts/<int:account_id>', methods=['DELETE'])
def delete_account(account_id):
    uid, err, code = _require_login()
    if err:
        return err, code
    acct = Account.query.filter_by(id=account_id, user_id=uid).first()
    if acct is None:
        return jsonify(error='account not found'), 404
    db.session.delete(acct)
    db.session.commit()
    return jsonify(deleted=account_id), 200


# --- Transactions ---
@api_bp.route('/transactions', methods=['GET'])
def list_transactions():
    uid, err, code = _require_login()
    if err:
        return err, code
    q = Transaction.query.filter_by(user_id=uid)
    start = _parse_date(request.args.get('start_date'))
    end = _parse_date(request.args.get('end_date'))
    cat = request.args.get('category')
    acc = request.args.get('account_id')
    if start:
        q = q.filter(Transaction.date >= start)
    if end:
        q = q.filter(Transaction.date <= end)
    if cat:
        q = q.filter(Transaction.category == cat)
    if acc:
        q = q.filter(Transaction.account_id == int(acc))
    items = q.order_by(Transaction.date.desc()).all()
    return jsonify([
        {'id': t.id, 'account_id': t.account_id, 'type': t.type, 'amount': t.amount,
         'category': t.category, 'description': t.description,
         'person_name': t.person_name, 'date': t.date.isoformat()}
        for t in items
    ])


@api_bp.route('/transactions', methods=['POST'])
def create_transaction():
    uid, err, code = _require_login()
    if err:
        return err, code
    data = request.get_json(silent=True) or {}
    account_id = data.get('account_id')
    if not account_id:
        return jsonify(error='account_id is required'), 400
    account = Account.query.filter_by(id=account_id, user_id=uid).first()
    if account is None:
        return jsonify(error='account not found or not yours'), 403
    trans_type = data.get('type')
    if trans_type not in ('income', 'expense'):
        return jsonify(error="type must be 'income' or 'expense'"), 400
    try:
        amount = float(data.get('amount'))
    except (TypeError, ValueError):
        return jsonify(error='amount must be a number'), 400
    txn = Transaction(
        user_id=uid,
        account_id=account_id,
        type=trans_type,
        amount=amount,
        category=data.get('category') or 'General',
        description=data.get('description') or '',
        person_name=data.get('person_name') or '',
        date=_parse_date(data.get('date')) or date.today(),
    )
    db.session.add(txn)
    if trans_type == 'income':
        account.current_balance += amount
    else:
        account.current_balance -= amount
    db.session.commit()
    return jsonify(id=txn.id), 201


@api_bp.route('/transactions/<int:txn_id>', methods=['DELETE'])
def delete_transaction(txn_id):
    uid, err, code = _require_login()
    if err:
        return err, code
    txn = Transaction.query.filter_by(id=txn_id, user_id=uid).first()
    if txn is None:
        return jsonify(error='transaction not found'), 404
    account = Account.query.filter_by(id=txn.account_id, user_id=uid).first()
    if account is not None:
        if txn.type == 'income':
            account.current_balance -= txn.amount
        else:
            account.current_balance += txn.amount
        db.session.add(account)
    db.session.delete(txn)
    db.session.commit()
    return jsonify(deleted=txn_id), 200


# --- Payments / Debts / Budgets ---
@api_bp.route('/payments', methods=['GET'])
def list_payments():
    uid, err, code = _require_login()
    if err:
        return err, code
    items = UpcomingPayment.query.filter_by(user_id=uid)\
        .order_by(UpcomingPayment.due_date.asc()).all()
    return jsonify([
        {'id': p.id, 'title': p.title, 'amount': p.amount,
         'due_date': p.due_date.isoformat(), 'status': p.status, 'category': p.category}
        for p in items
    ])


@api_bp.route('/debts', methods=['GET'])
def list_debts():
    uid, err, code = _require_login()
    if err:
        return err, code
    items = UpcomingPayment.query.filter(
        UpcomingPayment.user_id == uid,
        UpcomingPayment.category.ilike('%debt%')
    ).order_by(UpcomingPayment.due_date.asc()).all()
    return jsonify([
        {'id': p.id, 'title': p.title, 'amount': p.amount,
         'due_date': p.due_date.isoformat(), 'status': p.status, 'category': p.category}
        for p in items
    ])


@api_bp.route('/budgets', methods=['GET'])
def list_budgets():
    uid, err, code = _require_login()
    if err:
        return err, code
    month = date.today().replace(day=1)
    items = Budget.query.filter_by(user_id=uid, month=month).all()
    return jsonify([
        {'id': b.id, 'category': b.category, 'limit_amount': b.limit_amount,
         'month': b.month.isoformat()}
        for b in items
    ])


# --- CSV Export ---
@api_bp.route('/export/csv', methods=['GET'])
def export_csv():
    uid, err, code = _require_login()
    if err:
        return err, code
    q = Transaction.query.filter_by(user_id=uid)
    start = _parse_date(request.args.get('start_date'))
    end = _parse_date(request.args.get('end_date'))
    cat = request.args.get('category')
    acc = request.args.get('account_id')
    if start:
        q = q.filter(Transaction.date >= start)
    if end:
        q = q.filter(Transaction.date <= end)
    if cat:
        q = q.filter(Transaction.category == cat)
    if acc:
        q = q.filter(Transaction.account_id == int(acc))
    items = q.order_by(Transaction.date.desc()).all()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(['ID', 'Account', 'Type', 'Amount', 'Category',
                     'Description', 'Person', 'Date'])
    for t in items:
        writer.writerow([
            t.id, t.account.name, t.type, t.amount, t.category,
            t.description or '', t.person_name or '', t.date.isoformat(),
        ])
    return Response(
        out.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=transactions.csv'},
    )
