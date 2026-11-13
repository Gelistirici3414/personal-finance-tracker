"""Tests for dashboard budget alerts + promotions page."""
from datetime import date, timedelta
from models import db, Account, Transaction, Budget


def _seed_budget_and_spending(app, user, category='Market', limit=100.0,
                              spent=150.0):
    """Create a budget + transactions that push spending over the limit."""
    with app.app_context():
        acc = Account(user_id=user.id, name='Cash', type='Cash',
                      initial_balance=1000.0, current_balance=1000.0)
        db.session.add(acc); db.session.commit(); db.session.refresh(acc)
        txn = Transaction(user_id=user.id, account_id=acc.id, type='expense',
                         amount=spent, category=category,
                         date=date.today())
        db.session.add(txn); db.session.add(acc)
        budget = Budget(user_id=user.id, category=category,
                        limit_amount=limit,
                        month=date.today().replace(day=1))
        db.session.add(budget); db.session.commit()
        return budget, txn


def test_dashboard_over_budget_alerts(client, app, user):
    """When spending exceeds the budget, the dashboard surfaces alerts."""
    _seed_budget_and_spending(app, user, limit=100.0, spent=150.0)
    r = client.get('/')
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert 'Bütçe Aşımı' in text or 'AŞILDI' in text
    # The alert data should also be in the page (for the toast).
    assert 'over_budget_alerts' in r.get_data(as_text=True) or '150.00' in text


def test_dashboard_no_alert_when_within_budget(client, app, user):
    _seed_budget_and_spending(app, user, limit=500.0, spent=150.0)
    r = client.get('/')
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert 'Bütçe Aşımı' not in text


def test_promotions_page_renders(client, app, user):
    r = client.get('/promotions')
    assert r.status_code == 200
    assert 'Aktüel Fırsatlar' in r.get_data(as_text=True)


def test_api_promotions_requires_login(client):
    fresh = client.application.test_client()
    r = fresh.get('/api/promotions')
    assert r.status_code == 401