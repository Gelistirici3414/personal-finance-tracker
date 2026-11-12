"""Data isolation, CRUD, and API-layer tests."""
import json
from datetime import date
from models import db, Account, User
from tests.conftest import make_account, make_transaction


def test_two_users_cannot_see_each_others_data(client, app, user):
    """Strict multi-tenant isolation: user B must not read user A's accounts."""
    # User A (the `user` fixture) has an account.
    acc = make_account(app, user, name='My Bank', type='Bank')

    # Create user B directly in the DB (the shared client is already logged in
    # as A, so /register would 403). Then use a *fresh* client for B's session.
    with app.app_context():
        bob = User(username='bob', email='bob@x.com')
        bob.set_password('secret123')
        db.session.add(bob)
        db.session.commit()
        db.session.refresh(bob)

    # B's own session via a fresh test client (no A-session contamination).
    client_b = client.application.test_client()
    r2 = client_b.post('/login', data={'username': 'bob', 'password': 'secret123'})
    assert r2.status_code == 302, 'Bob login should redirect on success'

    # B lists accounts → must NOT see "My Bank".
    r3 = client_b.get('/accounts/')
    assert 'My Bank' not in r3.get_data(as_text=True)

    # B's API call to /api/accounts returns only B's own (zero) accounts.
    r4 = client_b.get('/api/accounts')
    assert r4.status_code == 200
    assert json.loads(r4.get_data()) == []


def test_transaction_crud_updates_balance(client, app, user):
    acc = make_account(app, user, name='Cash', balance=100.0)
    # Add expense
    r = client.post('/transactions/add', data={
        'account_id': acc.id, 'type': 'expense', 'amount': '40',
        'category': 'Market', 'description': 'groceries',
        'date': '2025-06-01',
    })
    assert r.status_code == 302
    with app.app_context():
        fresh_acc = Account.query.get(acc.id)
        assert fresh_acc.current_balance == 60.0

    # Delete it → balance rolls back to 100
    with app.app_context():
        from models import Transaction
        txn = Transaction.query.filter_by(user_id=user.id).first()
        client.get(f'/transactions/delete/{txn.id}', follow_redirects=True)
        fresh_acc2 = Account.query.get(acc.id)
        assert fresh_acc2.current_balance == 100.0


def test_api_export_csv(client, app, user):
    acc = make_account(app, user, name='Bank', balance=500.0)
    make_transaction(app, user, acc, type='expense', amount=25.0,
                     category='Bills', date=date(2025, 6, 1))
    r = client.get('/api/export/csv')
    assert r.status_code == 200
    assert r.mimetype == 'text/csv'
    assert b'Bills' in r.data


def test_api_create_transaction(client, app, user):
    """Create a transaction via the JSON API.

    Use a *fresh* client and log in explicitly so the session is definitely
    populated for the API call (independent of any earlier test state).
    """
    acc = make_account(app, user, name='X', balance=0.0)
    fresh = client.application.test_client()
    r_login = fresh.post('/login', data={
        'username': user.username, 'password': 'secret123',
    })
    assert r_login.status_code == 302, 'login must succeed before API call'
    r = fresh.post('/api/transactions', json={
        'account_id': acc.id, 'type': 'income', 'amount': 100,
        'category': 'Salary',
    })
    assert r.status_code == 201, r.get_data(as_text=True)
    assert json.loads(r.get_data())['id'] > 0


def test_api_requires_login(client, app, user):
    # A brand-new client has no session cookie at all.
    c2 = client.application.test_client()
    r = c2.get('/api/accounts')
    assert r.status_code == 401
