"""Pytest fixtures for the Personal Finance Tracker.

Run:
    pytest -q
"""
import os
import sys
import pytest
from sqlalchemy.pool import StaticPool

# Make the repo root importable so `import app`, `import models` work in tests.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import create_app  # noqa: E402
from models import db, User  # noqa: E402


@pytest.fixture(scope='function')
def app():
    """App bound to a *shared* in-memory SQLite DB via StaticPool, fresh per test.

    StaticPool keeps a single connection alive across all app contexts so that
    data created in one `with app.app_context()` block is still visible in
    subsequent blocks (and inside test-client requests).
    """
    from sqlalchemy.pool import StaticPool
    # Reset Flask-SQLAlchemy's engine cache so the new app gets its own engine
    # (a shared in-memory DB across multiple test functions would otherwise leak
    # data between tests).
    db._engines = {}
    app = create_app(
        database_uri='sqlite://',
        engine_options={
            'poolclass': StaticPool,
            'connect_args': {'check_same_thread': False},
        },
    )
    app.config['TESTING'] = True
    app.config['WTF_CSRF_ENABLED'] = False
    with app.app_context():
        db.drop_all()
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()



@pytest.fixture(autouse=True)
def _clean_db_session():
    """Ensure each test starts from a committed, non-dirty session.

    Prevents open transactions / unit-of-work state from leaking between
    tests that share the same module-level `db` (Flask-SQLAlchemy binds a
    single session to the current app context).
    """
    yield
    try:
        db.session.expunge_all()
        db.session.rollback()
    except Exception:
        pass


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def user(app, client):
    """A logged-in test user (session set via a real /register POST)."""
    with app.app_context():
        u = User(username='testuser', email='test@example.com')
        u.set_password('secret123')
        db.session.add(u)
        db.session.commit()
        db.session.refresh(u)
    # Log in through the real auth flow to populate the session cookie.
    client.post('/login',
                data={'username': 'testuser', 'password': 'secret123'},
                follow_redirects=False)
    return u


def make_account(app, user, name='Cash', type='Cash', balance=100.0):
    from models import Account
    with app.app_context():
        a = Account(user_id=user.id, name=name, type=type,
                    initial_balance=balance, current_balance=balance)
        db.session.add(a)
        db.session.commit()
        db.session.refresh(a)
        return a


def make_transaction(app, user, account, type='expense', amount=50.0,
                     category='Market', description='', person_name='',
                     date=None):
    from models import Transaction
    from datetime import date as _date
    with app.app_context():
        t = Transaction(user_id=user.id, account_id=account.id, type=type,
                        amount=amount, category=category,
                        description=description, person_name=person_name,
                        date=date or _date.today())
        db.session.add(t)
        if type == 'income':
            account.current_balance += amount
        else:
            account.current_balance -= amount
        db.session.add(account)
        db.session.commit()
        db.session.refresh(t)
        return t
