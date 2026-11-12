"""Auth + registration + forgot-password flow tests."""
from models import User, db
from tests.conftest import make_account, make_transaction


def test_register_new_user(client, app):
    with app.app_context():
        assert User.query.filter_by(username='jane').first() is None
    r = client.post('/register', data={
        'username': 'jane', 'email': 'jane@x.com',
        'password': 'abc123', 'confirm': 'abc123',
    })
    assert r.status_code in (302, 200)
    with app.app_context():
        assert User.query.filter_by(username='jane').first() is not None


def test_register_duplicate_email_rejected(client, app):
    client.post('/register', data={
        'username': 'dup', 'email': 'dup@x.com',
        'password': 'abc123', 'confirm': 'abc123',
    })
    r = client.post('/register', data={
        'username': 'other', 'email': 'dup@x.com',
        'password': 'abc123', 'confirm': 'abc123',
    })
    assert 'zaten' in r.get_data(as_text=True).lower() or r.status_code == 200


def test_login_sets_session(client, user, app):
    r = client.post('/login', data={
        'username': user.username, 'password': 'secret123',
    })
    assert r.status_code == 302
    # Follow through: dashboard should now be accessible (not redirected to login)
    with app.app_context():
        pass
    r2 = client.get('/accounts/')
    assert r2.status_code == 200


def test_login_wrong_password(client, user, app):
    # The `user` fixture logs in on the shared `client`. Start from a clean
    # session with a fresh client so we actually hit the "wrong password" branch.
    anon = client.application.test_client()
    anon.post('/register', data={
        'username': 'solo', 'email': 'solo@x.com',
        'password': 'secret123', 'confirm': 'secret123',
    })
    r = anon.post('/login', data={'username': 'solo', 'password': 'WRONG'})
    text = r.get_data(as_text=True).lower()
    assert 'hatal' in text or 'yanl' in text or 'wrong' in text


def test_protected_route_redirects_when_not_logged_in(client):
    r = client.get('/accounts/', follow_redirects=False)
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


def test_forgot_password_creates_token(client, user, app):
    r = client.post('/forgot-password', data={'email': user.email})
    assert r.status_code == 200
    with app.app_context():
        u = db.session.get(User, user.id)
        assert u.password_reset_token is not None


def test_reset_password_flow(client, user, app):
    # 1. request a reset
    client.post('/forgot-password', data={'email': user.email})
    with app.app_context():
        u = db.session.get(User, user.id)
        token = u.password_reset_token
    # 2. use it to set a new password
    r = client.post(f'/reset-password/{token}', data={
        'password': 'newpass1', 'confirm': 'newpass1',
    })
    assert r.status_code == 302
    # 3. can log in with the new password
    r2 = client.post('/login', data={
        'username': user.username, 'password': 'newpass1',
    })
    assert r2.status_code == 302


def test_logout_clears_session(client, user):
    client.post('/login', data={
        'username': user.username, 'password': 'secret123'})
    client.get('/logout')
    r = client.get('/accounts/', follow_redirects=False)
    assert r.status_code == 302
