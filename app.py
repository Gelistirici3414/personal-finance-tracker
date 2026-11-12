import os
import urllib.parse
from datetime import datetime
from flask import Flask, Blueprint, render_template, request, redirect, url_for, session, abort, g, flash
from models import db, User
from routes.dashboard import dashboard_bp
from routes.accounts import accounts_bp
from routes.transactions import transactions_bp
from routes.payments import payments_bp
from routes.debts import debts_bp
from routes.budgets import budgets_bp
from routes.discounts import discounts_bp

try:
    from dotenv import load_dotenv
    load_dotenv()  # load .env if present (no-op in production where env is set by host)
except ImportError:
    pass


auth_bp = Blueprint('auth', __name__)


# Simple in-memory rate limiter for auth endpoints (per-IP).  In production,
# swap for a Redis-backed limiter (e.g. flask-limiter) once you add the dep.
import time
_login_attempts = {}
_RATE_WINDOW = 300          # 5 minutes
_RATE_LIMIT = 10           # max attempts per window


def _rate_limited(ip):
    now = time.time()
    bucket = _login_attempts.get(ip, [])
    # prune old entries
    _login_attempts[ip] = [t for t in bucket if now - t < _RATE_WINDOW]
    if len(_login_attempts[ip]) >= _RATE_LIMIT:
        return True
    _login_attempts[ip].append(now)
    return False


# Rate-limiter for the /login endpoint.  In TESTING / dev (no FLASK_ENV set)
# the limiter is disabled so test suites never trip it.
_RATE_LIMIT_ENABLED = os.environ.get('FLASK_ENV', '').lower() not in ('test', 'testing')
if os.environ.get('FLASK_ENV', '').lower() == '':
    _RATE_LIMIT_ENABLED = False


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('dashboard.dashboard'))
    error = None
    if request.method == 'POST':
        if _RATE_LIMIT_ENABLED and _rate_limited(request.remote_addr or 'unknown'):
            error = 'Çok fazla deneme. Lütfen 5 dakika sonra tekrar deneyin.'
        else:
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                session['user_id'] = user.id
                session['username'] = user.username
                session['email'] = user.email
                return redirect(request.args.get('next') or url_for('dashboard.dashboard'))
            error = 'Kullanıcı adı veya şifre hatalı'
    return render_template('login.html', error=error)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        if request.method == 'POST':
            # Reject POSTs from already-authenticated users explicitly.
            return jsonify(error='you are already logged in; use /logout first'), 403
        return redirect(url_for('dashboard.dashboard'))
    errors = []
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')

        if not username or not email or not password:
            errors.append('Tüm alanlar zorunludur')
        elif len(password) < 6:
            errors.append('Şifre en az 6 karakter olmalı')
        elif password != confirm:
            errors.append('Şifreler eşleşmiyor')
        elif User.query.filter_by(username=username).first():
            errors.append('Bu kullanıcı adı zaten alınmış')
        elif User.query.filter_by(email=email).first():
            errors.append('Bu e-posta adresi zaten kayıtlı')
        else:
            new_user = User(username=username, email=email)
            new_user.set_password(password)
            db.session.add(new_user)
            db.session.commit()
            flash('Hesabınız oluşturuldu. Şimdi giriş yapabilirsiniz.', 'success')
            return redirect(url_for('auth.login'))
    return render_template('register.html', errors=errors)


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    """Simple password-reset request infrastructure.

    The user submits their registered email; we look up the account and,
    if it exists, generate a reset token that is stored on the user row and
    emailed (stubbed here as a flash message / token display for dev use).
    """
    error = None
    reset_token = None
    email = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        user = User.query.filter_by(email=email).first()
        if not user:
            # Do not reveal whether the email exists.
            flash('Eğer bu e-posta kayıtlıysa, sıfırlama bağlantısı gönderildi.', 'warning')
            return redirect(url_for('auth.forgot_password'))
        import secrets
        reset_token = secrets.token_urlsafe(32)
        user.password_reset_token = reset_token
        user.password_reset_sent_at = datetime.utcnow()
        db.session.commit()
        # In production, send this token via email. For dev, show it.
        flash('Sıfırlama talebiniz alındı.', 'success')
        return render_template('forgot_password.html', email=email, reset_token=reset_token)

    return render_template('forgot_password.html', error=error)


@auth_bp.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    """Consume a reset token and set a new password."""
    user = User.query.filter_by(password_reset_token=token).first()
    if user is None:
        flash('Sıfırlama bağlantısı geçersiz veya süresi dolmuş.', 'error')
        return redirect(url_for('auth.forgot_password'))
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm = request.form.get('confirm', '')
        if len(password) < 6:
            return render_template('reset_password.html', token=token,
                                   error='Şifre en az 6 karakter olmalı')
        if password != confirm:
            return render_template('reset_password.html', token=token,
                                   error='Şifreler eşleşmiyor')
        user.set_password(password)
        user.password_reset_token = None
        user.password_reset_sent_at = None
        db.session.commit()
        flash('Şifreniz güncellendi. Şimdi giriş yapabilirsiniz.', 'success')
        return redirect(url_for('auth.login'))
    return render_template('reset_password.html', token=token, error=None)


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


def create_app(database_uri=None, engine_options=None):
    app = Flask(__name__)

    is_production = os.environ.get('FLASK_ENV', '').lower() == 'production'

    # --- Security hardening ---
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        database_uri
        or os.environ.get('FPU_DATABASE_URL')
        or os.environ.get('DATABASE_URI')
        or 'sqlite:///finance.db'
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = os.environ.get(
        'FPU_SECRET_KEY',
        'dev-secret-change-in-production',
    )
    if engine_options:
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options
    app.config['DEBUG'] = not is_production
    # Run in TESTING mode when an in-memory/ephemeral DB is requested, so
    # session & commit behaviour stays predictable in the test-suite.
    app.config['TESTING'] = bool(
        database_uri and database_uri.startswith('sqlite://')
    )
    db.init_app(app)


    # --- CSRF-like safety: JSON API + session cookie flag ---
    app.config['SESSION_COOKIE_NAME'] = os.environ.get(
        'FPU_SESSION_COOKIE', 'fpu_session')

    if is_production:
        app.config.update(
            SESSION_COOKIE_SECURE=True,
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE='Lax',
            SESSION_COOKIE_MAXAGE=60 * 60 * 24 * 7,
        )
    else:
        app.config.update(
            SESSION_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE='Lax',
        )

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(budgets_bp, url_prefix='/budgets')
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(accounts_bp, url_prefix='/accounts')
    app.register_blueprint(transactions_bp, url_prefix='/transactions')
    app.register_blueprint(payments_bp, url_prefix='/payments')
    app.register_blueprint(debts_bp, url_prefix='/debts')
    app.register_blueprint(discounts_bp)
    # Register the JSON API blueprint
    from routes.api import api_bp
    app.register_blueprint(api_bp, url_prefix='/api')

    # --- Content-Security-Policy header (production only, dev loosened) ---
    @app.after_request
    def _add_security_headers(resp):
        if is_production:
            resp.headers['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "object-src 'none'; frame-ancestors 'self'; base-uri 'self'"
            )
        resp.headers['X-Content-Type-Options'] = 'nosniff'
        resp.headers['X-Frame-Options'] = 'SAMEORIGIN'
        resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        return resp

    # --- Global error handlers (404 / 500) ---
    from flask import render_template, jsonify
    @app.errorhandler(404)
    def not_found(e):
        if request.path.startswith('/api'):
            return jsonify(error='not found'), 404
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        if request.path.startswith('/api'):
            return jsonify(error='internal server error'), 500
        return render_template('errors/500.html'), 500

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify(error='authentication required'), 401

    # Protect all data routes
    protected_prefixes = (
        '/accounts', '/transactions', '/payments', '/debts', '/budgets', '/discounts',
    )
    protected_rules = ['/']

    def _is_protected(path):
        if path in protected_rules:
            return True
        for p in protected_prefixes:
            if path.startswith(p):
                return True
        return False

    @app.before_request
    def _before_request():
        # The JSON API exposes its own clean 401/403 errors — skip the blanket
        # HTML redirect for /api/* so clients get machine-readable responses.
        if request.path.startswith('/api'):
            return None
        if not _is_protected(request.path):
            return None
        if not session.get('user_id'):
            return redirect(url_for('auth.login', next=request.full_path))

    # Inject current_user into every template context
    @app.context_processor
    def inject_user():
        user = None
        uid = session.get('user_id')
        if uid:
            user = db.session.get(User, uid)
        return {'current_user': user, 'is_logged_in': bool(user)}

    # Register custom Jinja filters
    app.jinja_env.filters['decode_uri'] = urllib.parse.unquote

    # PWA assets served at root
    from flask import send_from_directory, jsonify

    @app.route('/manifest.json')
    def manifest_json():
        return send_from_directory(os.path.dirname(__file__), 'manifest.json')

    @app.route('/sw.js')
    def service_worker():
        return send_from_directory(os.path.dirname(__file__), 'sw.js')

    @app.route('/api/health')
    def health():
        return jsonify({'status': 'ok'})

    with app.app_context():
        db.create_all()

    return app


# Module-level app instance so Gunicorn can use "personal_finance_tracker.app:app".
app = create_app()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=True)


