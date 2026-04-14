"""Transactions routes - Recent Transactions page"""
from flask import Blueprint, render_template, request
from flask_login import current_user, login_required
from datetime import datetime
from app import db
from app.models.transaction import Transaction
from app.models.person import Person
from app.models.vc import VC

transactions_bp = Blueprint('transactions', __name__, url_prefix='/transactions')


@transactions_bp.route('/transactions')
@login_required
def recent_transactions():
    """Show recent transactions (received/paid entries) for current user"""

    # Pagination: 15 on first page, 10 on subsequent
    page = request.args.get('page', 1, type=int)
    per_page = 15 if page == 1 else 10

    # Filters
    txn_type = request.args.get('type', '')   # 'received' or 'paid'
    from_date = request.args.get('from_date', '')
    to_date = request.args.get('to_date', '')

    # Base query — current user only
    query = Transaction.query.filter_by(user_id=current_user.id)

    # Search filter
    search = request.args.get('search', '').strip()
    if search:
        from sqlalchemy import or_, cast, String
        search_like = f"%{search}%"
        query = query.join(Transaction.person)
        query = query.filter(
            or_(
                cast(Transaction.amount, String).ilike(search_like),
                Transaction.narration.ilike(search_like),
                cast(Transaction.date, String).ilike(search_like),
                Person.name.ilike(search_like),
                Person.short_name.ilike(search_like)
            )
        )

    # Type filter  ('received' maps to type='credit', 'paid' maps to type='debit')
    if txn_type == 'received':
        query = query.filter(Transaction.type == 'credit')
    elif txn_type == 'paid':
        query = query.filter(Transaction.type == 'debit')

    # Date filters
    if from_date:
        try:
            query = query.filter(Transaction.date >= datetime.strptime(from_date, '%Y-%m-%d'))
        except ValueError:
            pass

    if to_date:
        try:
            query = query.filter(
                Transaction.date <= datetime.strptime(to_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S')
            )
        except ValueError:
            pass

    # Totals across all matching rows (before pagination)
    all_txns = query.all()
    total_received = sum(t.amount for t in all_txns if t.type == 'credit')
    total_paid = sum(t.amount for t in all_txns if t.type == 'debit')

    # Order and paginate
    paginated = (
        query.order_by(Transaction.date.desc())
        .paginate(page=page, per_page=per_page, error_out=False)
    )

    # Build display list
    transactions = []
    for t in paginated.items:
        transactions.append({
            'date': t.date,
            'short_name': t.person.short_name if t.person else 'Unknown',
            'person_name': t.person.name if t.person else 'Unknown',
            'narration': t.narration or '—',
            'type': 'received' if t.type == 'credit' else 'paid',
            'amount': t.amount,
        })

    all_vcs = VC.query.filter_by(user_id=current_user.id).all()

    return render_template(
        'transactions.html',
        transactions=transactions,
        all_vcs=all_vcs,
        total_received=total_received,
        total_paid=total_paid,
        pages=paginated.pages,
        page=page
    )