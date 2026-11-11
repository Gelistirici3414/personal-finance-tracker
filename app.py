import os
import urllib.parse
from flask import Flask, Blueprint, render_template, request, redirect, url_for, session, abort, g, flash
from models import db, User
from routes.dashboard import dashboard_bp
from routes.accounts import accounts_bp
from routes.transactions import transactions_bp
from routes.payments import payments_bp
from routes.debts import debts_bp
from routes.budgets import budgets_bp
from routes.discounts import discounts_bp

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('dashboard.dashboard'))
    error = None
    if request.method == 'POST':
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


@auth_bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))


def create_app():
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///finance.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = os.environ.get('FPU_SECRET_KEY', 'dev-secret-change-in-production')
    db.init_app(app)

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(budgets_bp, url_prefix='/budgets')
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(accounts_bp, url_prefix='/accounts')
    app.register_blueprint(transactions_bp, url_prefix='/transactions')
    app.register_blueprint(payments_bp, url_prefix='/payments')
    app.register_blueprint(debts_bp, url_prefix='/debts')
    app.register_blueprint(discounts_bp)

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


