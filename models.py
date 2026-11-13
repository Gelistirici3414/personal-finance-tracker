from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), nullable=False, unique=True)
    email = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    password_reset_token = db.Column(db.String(64), nullable=True)
    password_reset_sent_at = db.Column(db.DateTime, nullable=True)

    # Relationships
    accounts = db.relationship('Account', backref='owner', lazy=True, cascade='all, delete-orphan')
    transactions = db.relationship('Transaction', backref='owner', lazy=True, cascade='all, delete-orphan')
    upcoming_payments = db.relationship('UpcomingPayment', backref='owner', lazy=True, cascade='all, delete-orphan')
    budgets = db.relationship('Budget', backref='owner', lazy=True, cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Account(db.Model):
    __tablename__ = 'accounts'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    initial_balance = db.Column(db.Float, default=0.0)
    current_balance = db.Column(db.Float, default=0.0)
    transactions = db.relationship('Transaction', backref='account', lazy=True, cascade='all, delete-orphan')


class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey('accounts.id', ondelete='CASCADE'), nullable=False)
    type = db.Column(db.String(10), nullable=False)  # 'income' or 'expense'
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)
    description = db.Column(db.String(200))
    person_name = db.Column(db.String(100))
    date = db.Column(db.Date, nullable=False)


class UpcomingPayment(db.Model):
    __tablename__ = 'upcoming_payments'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    due_date = db.Column(db.Date, nullable=False)
    status = db.Column(db.String(10), default='pending')  # 'pending' or 'paid'
    category = db.Column(db.String(50), nullable=False)


class Budget(db.Model):
    __tablename__ = 'budgets'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    limit_amount = db.Column(db.Float, nullable=False)
    month = db.Column(db.Date, nullable=False)


class PromoDeal(db.Model):
    """Market catalog item scraped by the background scheduler.

    Fields:
        market        – 'BIM', 'A101', 'SOK', 'MIGROS'
        product       – product name (from OCR or structured HTML)
        price         – parsed numeric price in TL (nullable if not found)
        discount_pct  – parsed discount percentage (nullable)
        image_url     – source image URL (brochure thumbnail)
        ocr_text      – raw OCR output (for audit / fallback display)
        source_page   – URL the deal was scraped from
        scraped_at    – ISO timestamp of the last scrape
        user_id       – NULL = global/system deal; set = user-submitted deal

    All writes go through a guarded helper that rolls back on error
    to keep the catalogue consistent with the main database.
    """
    __tablename__ = 'promo_deals'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    market = db.Column(db.String(20), nullable=False, index=True)
    product = db.Column(db.String(200), nullable=False, index=True)
    price = db.Column(db.Float, nullable=True)
    discount_pct = db.Column(db.Float, nullable=True)
    image_url = db.Column(db.Text, nullable=True)
    ocr_text = db.Column(db.Text, nullable=True)
    source_page = db.Column(db.Text, nullable=True)
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    def __repr__(self):
        return f"<PromoDeal {self.market}:{self.product} @ {self.price}>"


def save_promo_deals(deals, session=None):
    """Persist a batch of promo deals atomically.

    deals: list of dicts with keys matching PromoDeal fields.
    Returns a list of created PromoDeal instances; rolls back the whole
    transaction on any DB error so the catalogue stays consistent.
    """
    from contextlib import contextmanager
    target_session = session or db.session
    try:
        created = []
        for d in deals:
            deal = PromoDeal(
                market=(d.get('market') or '').upper() or 'UNKNOWN',
                product=d.get('product') or 'Bilinmeyen Ürün',
                price=d.get('price'),
                discount_pct=d.get('discount_pct'),
                image_url=d.get('image_url'),
                ocr_text=d.get('ocr_text'),
                source_page=d.get('source_page'),
                user_id=d.get('user_id'),
            )
            target_session.add(deal)
            created.append(deal)
        target_session.commit()
        return created
    except Exception:
        target_session.rollback()
        raise


