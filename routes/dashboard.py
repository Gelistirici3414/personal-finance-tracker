import os
from flask import Blueprint, render_template, session
from models import db, Account, Transaction, UpcomingPayment, Budget
from datetime import datetime, date, timedelta
from sqlalchemy import func

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
def dashboard():
    uid = session['user_id']
    today = date.today()
    month_start = date(today.year, today.month, 1)

    total_balance = db.session.query(func.sum(Account.current_balance)).filter(
        Account.user_id == uid
    ).scalar() or 0.0

    income_sum = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id == uid,
        Transaction.type == 'income',
        Transaction.date >= month_start,
        Transaction.date <= today
    ).scalar() or 0.0
    expense_sum = db.session.query(func.sum(Transaction.amount)).filter(
        Transaction.user_id == uid,
        Transaction.type == 'expense',
        Transaction.date >= month_start,
        Transaction.date <= today
    ).scalar() or 0.0

    pending_payments = UpcomingPayment.query.filter_by(user_id=uid, status='pending').all()

    category_data = db.session.query(
        Transaction.category, func.sum(Transaction.amount)
    ).filter(
        Transaction.user_id == uid,
        Transaction.type == 'expense',
        Transaction.date >= month_start,
        Transaction.date <= today
    ).group_by(Transaction.category).all()
    categories = [c for c, _ in category_data]
    category_amounts = [amt for _, amt in category_data]

    labels = []
    balance_trend = []
    for i in range(6, -1, -1):
        month = (today.replace(day=1) - timedelta(days=30 * i)).replace(day=1)
        next_month = (month + timedelta(days=31)).replace(day=1)
        month_income = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == uid,
            Transaction.type == 'income',
            Transaction.date >= month,
            Transaction.date < next_month
        ).scalar() or 0.0
        month_expense = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == uid,
            Transaction.type == 'expense',
            Transaction.date >= month,
            Transaction.date < next_month
        ).scalar() or 0.0
        balance_trend.append(month_income - month_expense)
        labels.append(month.strftime('%b %Y'))

    budgets = Budget.query.filter_by(user_id=uid, month=month_start).all()
    budget_info = []
    for b in budgets:
        spent = db.session.query(func.sum(Transaction.amount)).filter(
            Transaction.user_id == uid,
            Transaction.type == 'expense',
            Transaction.category == b.category,
            Transaction.date >= month_start,
            Transaction.date <= today,
        ).scalar() or 0.0
        percent = (spent / b.limit_amount * 100) if b.limit_amount else 0
        budget_info.append({
            'category': b.category,
            'limit': b.limit_amount,
            'spent': spent,
            'percent': percent,
        })

    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = date(prev_month_end.year, prev_month_end.month, 1)
    this_month_data = db.session.query(
        Transaction.category, func.sum(Transaction.amount)
    ).filter(
        Transaction.user_id == uid,
        Transaction.type == 'expense',
        Transaction.date >= month_start,
        Transaction.date <= today,
    ).group_by(Transaction.category).all()
    prev_month_data = db.session.query(
        Transaction.category, func.sum(Transaction.amount)
    ).filter(
        Transaction.user_id == uid,
        Transaction.type == 'expense',
        Transaction.date >= prev_month_start,
        Transaction.date <= prev_month_end,
    ).group_by(Transaction.category).all()
    this_dict = {c: amt for c, amt in this_month_data}
    prev_dict = {c: amt for c, amt in prev_month_data}
    insights = []
    for cat, this_amt in this_dict.items():
        prev_amt = prev_dict.get(cat, 0.0)
        if prev_amt == 0:
            if this_amt > 0:
                insights.append(f"New spending detected in category '{cat}' this month: {this_amt:,.2f}â‚º.")
        else:
            diff_pct = ((this_amt - prev_amt) / prev_amt) * 100
            if diff_pct > 10:
                insights.append(f"{cat.capitalize()} expenses increased by {diff_pct:.0f}% compared to last month. Consider reviewing your spending.")
            elif diff_pct < -10:
                insights.append(f"{cat.capitalize()} expenses decreased by {abs(diff_pct):.0f}% compared to last month. Good job saving!")
    if not insights:
        insights.append('Your spending is stable compared to the previous month.')

    days_min = today + timedelta(days=3)
    days_max = today + timedelta(days=5)
    due_soon = (
        UpcomingPayment.query
        .filter(
            UpcomingPayment.user_id == uid,
            UpcomingPayment.status == 'pending',
            UpcomingPayment.due_date >= days_min,
            UpcomingPayment.due_date <= days_max,
        )
        .all()
    )

    return render_template('dashboard.html', total_balance=total_balance,
                           income_sum=income_sum, expense_sum=expense_sum,
                           pending_payments=pending_payments,
                           categories=categories, amounts=category_amounts,
                           trend_labels=labels, trend_data=balance_trend,
                           budget_info=budget_info,
                           insights=insights,
                           due_soon=due_soon)
