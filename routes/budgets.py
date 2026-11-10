from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from ..models import db, Budget
from datetime import date

budgets_bp = Blueprint('budgets', __name__)

@budgets_bp.route('/')
def list_budgets():
    uid = session['user_id']
    month_start = date.today().replace(day=1)
    budgets = Budget.query.filter_by(user_id=uid, month=month_start).all()
    return render_template('budgets.html', budgets=budgets)

@budgets_bp.route('/add', methods=['GET', 'POST'])
def add_budget():
    if request.method == 'POST':
        uid = session['user_id']
        category = request.form.get('category')
        limit_amount = float(request.form.get('limit_amount'))
        month = date.today().replace(day=1)
        budget = Budget(user_id=uid, category=category, limit_amount=limit_amount, month=month)
        db.session.add(budget)
        db.session.commit()
        flash('Budget added successfully', 'success')
        return redirect(url_for('budgets.list_budgets'))
    return render_template('add_budget.html')

@budgets_bp.route('/delete/<int:id>', methods=['POST', 'GET'])
def delete_budget(id):
    uid = session['user_id']
    budget = Budget.query.filter_by(id=id, user_id=uid).first()
    if budget is None:
        flash('Budget not found', 'error')
        return redirect(url_for('budgets.list_budgets'))
    db.session.delete(budget)
    db.session.commit()
    flash('Budget deleted successfully', 'success')
    return redirect(request.referrer or url_for('budgets.list_budgets'))
