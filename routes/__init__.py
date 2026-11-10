from .dashboard import dashboard_bp
from .accounts import accounts_bp
from .transactions import transactions_bp
from .payments import payments_bp
from .debts import debts_bp
from .budgets import budgets_bp
from .discounts import discounts_bp

__all__ = ['dashboard_bp', 'accounts_bp', 'transactions_bp', 'payments_bp', 'debts_bp', 'budgets_bp', 'discounts_bp']