import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from models import db, Account

accounts_bp = Blueprint('accounts', __name__)

@accounts_bp.route('/')
def list_accounts():
    uid = session['user_id']
    accounts = Account.query.filter_by(user_id=uid).all()
    return render_template('accounts.html', accounts=accounts)

@accounts_bp.route('/add', methods=['GET', 'POST'])
def add_account():
    if request.method == 'POST':
        uid = session['user_id']
        name = request.form.get('name')
        acc_type = request.form.get('type')
        initial_balance = float(request.form.get('initial_balance', 0))
        account = Account(user_id=uid, name=name, type=acc_type,
                          initial_balance=initial_balance, current_balance=initial_balance)
        db.session.add(account)
        db.session.commit()
        flash('Account added successfully', 'success')
        return redirect(url_for('accounts.list_accounts'))
    return render_template('add_account.html')

@accounts_bp.route('/delete/<int:id>', methods=['POST', 'GET'])
def delete_account(id):
    uid = session['user_id']
    account = Account.query.filter_by(id=id, user_id=uid).first()
    if account is None:
        flash('Account not found', 'error')
        return redirect(url_for('accounts.list_accounts'))
    db.session.delete(account)
    db.session.commit()
    flash('Account deleted successfully', 'success')
    return redirect(url_for('accounts.list_accounts'))
