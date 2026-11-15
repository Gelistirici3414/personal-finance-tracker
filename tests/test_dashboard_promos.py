"""Tests for dashboard budget alerts + promotions page."""
from datetime import date, timedelta
from unittest.mock import patch
from models import db, Account, Transaction, Budget, PromoDeal


def _make_account(app, user, name='Cash', type='Cash', balance=100.0):
    with app.app_context():
        a = Account(user_id=user.id, name=name, type=type,
                    initial_balance=balance, current_balance=balance)
        db.session.add(a)
        db.session.commit()
        db.session.refresh(a)
        return a


def _make_transaction(app, user, account, type='expense', amount=50.0,
                      category='Market', description='', txn_date=None):
    # Capture IDs up-front so we never lazily-load a detached instance later.
    user_id = user.id
    account_id = account.id
    with app.app_context():
        t = Transaction(user_id=user_id, account_id=account_id, type=type,
                        amount=amount, category=category,
                        description=description,
                        date=txn_date or date.today())
        db.session.add(t)
        # Re-query a live instance so we don't mutate a detached one.
        live_acc = db.session.get(Account, account_id)
        if type == 'income':
            live_acc.current_balance += amount
        else:
            live_acc.current_balance -= amount
        db.session.add(live_acc)
        db.session.commit()
        db.session.refresh(t)
        return t



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


def test_discounts_page_renders(client, app, user):
    r = client.get('/discounts')
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    # Shows either real scraped deals or the honest "veri alınamadı" warning.
    assert 'İndirimler' in text or 'Fırsatlar' in text \
        or 'Anlık indirim verisi alınamadı' in text


def test_discounts_page_shows_honest_warning_without_data(client, app, user):
    """No scraped deals + no mock injection: the page shows the
    'Anlık indirim verisi alınamadı, lütfen tekrar deneyin' warning and
    NO fake products."""
    r = client.get('/discounts')
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert 'Anlık indirim verisi alınamadı' in text
    assert 'lütfen tekrar deneyin' in text
    # Mock catalogue products must be absent.
    for fake in ('Yarım Değerine Yağ', 'Toz Şeker 5kg',
                 'Migros Hediye Çeki', 'Etiyem Kampanyası'):
        assert fake not in text
    # The refresh / retry button must be present.
    assert 'refreshDeals' in text


def test_discounts_page_renders_real_deals_when_scraped(client, app, user):
    """When real scraped data exists, it renders (and no warning shows)."""
    from datetime import datetime
    with app.app_context():
        deal = PromoDeal(market='BIM', product='Gerçek Ürün 1L',
                         price=123.45, scraped_at=datetime.utcnow())
        db.session.add(deal)
        db.session.commit()
    r = client.get('/discounts')
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert 'Gerçek Ürün 1L' in text
    assert '123.45' in text  # rendered via "{:,.2f}".format()
    with app.app_context():
        db.session.rollback()


def test_api_discounts_returns_empty_list_when_no_data(client, app, user):
    """The JSON API returns an honest empty list (no mock offers)."""
    r = client.get('/api/discounts')
    assert r.status_code == 200
    assert r.get_json() == []


def test_api_discounts_refresh_unavailable_status(client, app, user):
    """Refresh that scrapes 0 fresh deals returns status 'unavailable' with
    a Turkish warning message — never fake data."""
    with patch('routes.discounts.scrape_all_markets', return_value={}):
        r = client.post('/api/discounts/refresh')
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'unavailable'
    assert 'Anlık indirim verisi alınamadı' in body['message']
    assert 'scraped' in body


def test_dashboard_insights_are_turkish_with_correct_lira(client, app, user):
    """Fix #2: AI Insights text is fully Turkish and uses the correct UTF-8
    lira sign (₺, U+20BA) — no mojibake like 'â‚º'."""
    acc = _make_account(app, user, name='Cash', type='Cash', balance=500.0)
    # Spending this month in 'Market' with no spending last month → new-cat insight.
    _make_transaction(app, user, acc, type='expense', amount=150.0, category='Market')
    r = client.get('/')
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert 'Yapay Zeka İçgörüleri' in text
    # New-spending insight, Turkish + correct lira sign.
    assert 'kategorisinde yeni harcama tespit edildi' in text
    assert '₺' in text
    # No mojibake leftovers.
    assert 'â‚º' not in text and 'Ã' not in text


def test_dashboard_insights_stable_spending_is_turkish(client, app, user):
    """Fix #2: the 'no change' insight is also Turkish."""
    acc = _make_account(app, user, name='Cash', type='Cash', balance=500.0)
    today = date.today()
    month_start = today.replace(day=1)
    prev_end = month_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)
    # Same amount in both months → 'stable' path.
    _make_transaction(app, user, acc, type='expense', amount=100.0,
                      category='Market', txn_date=month_start + timedelta(days=1))
    _make_transaction(app, user, acc, type='expense', amount=100.0,
                      category='Market', txn_date=prev_start + timedelta(days=1))
    r = client.get('/')
    assert r.status_code == 200
    text = r.get_data(as_text=True)
    assert 'dengeli seyrediyor' in text
    assert 'Harcamalarınız' in text



def test_api_discounts_requires_login(client):
    fresh = client.application.test_client()
    r = fresh.get('/api/discounts')
    assert r.status_code == 401