"""Transactions routes - Recent Transactions page"""
from flask import Blueprint, render_template, request, current_app
from flask_login import current_user, login_required
from datetime import datetime
from app import db
from app.models.ledger import LedgerEntry
from app.models.vc import VC
from app.models.person import Person

transactions_bp = Blueprint('transactions', __name__, url_prefix='/transactions')


@transactions_bp.route('/transactions')
@login_required
def recent_transactions():
    """Show recent transactions (received/paid entries) for current user"""
    
    # Get pagination params
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    # Get filter params
    txn_type = request.args.get('type', '')  # 'received' or 'paid'
    vc_id = request.args.get('vc_id', type=int)
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    
    # Get all VCs for current user
    user_vcs = VC.query.filter_by(user_id=current_user.id).all()
    user_vc_ids = [vc.id for vc in user_vcs]
    
    if not user_vc_ids:
        # No VCs, show empty
        return render_template(
            'transactions.html',
            transactions=[],
            all_vcs=[],
            total_received=0,
            total_paid=0,
            pages=1,
            page=1
        )
    
    # Base query: get all ledger entries for user's VCs
    query = LedgerEntry.query.filter(
        LedgerEntry.vc_id.in_(user_vc_ids),
        LedgerEntry.person_id.isnot(None)  # Only personal entries, not operator
    )
    
    # Apply VC filter
    if vc_id and vc_id in user_vc_ids:
        query = query.filter(LedgerEntry.vc_id == vc_id)
    
    # Apply date filters
    if from_date:
        try:
            from_dt = datetime.strptime(from_date + ' 00:00:00', '%Y-%m-%d %H:%M:%S')
            query = query.filter(LedgerEntry.date >= from_dt)
        except ValueError:
            pass
    
    if to_date:
        try:
            to_dt = datetime.strptime(to_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S')
            query = query.filter(LedgerEntry.date <= to_dt)
        except ValueError:
            pass
    
    # Get all matching entries (for summary calculation)
    all_entries = query.all()
    
    # Calculate totals from all entries
    total_received = sum(float(e.credit or 0) for e in all_entries)
    total_paid = sum(float(e.debit or 0) for e in all_entries)
    
    # Apply transaction type filter and order
    if txn_type == 'received':
        query = query.filter(LedgerEntry.credit > 0)
    elif txn_type == 'paid':
        query = query.filter(LedgerEntry.debit > 0)
    
    # Order by date descending (most recent first)
    query = query.order_by(LedgerEntry.date.desc())
    
    # Paginate
    paginated = query.paginate(page=page, per_page=per_page, error_out=False)
    entries = paginated.items
    pages = paginated.pages
    
    # Build transaction list with person/vc names
    transactions = []
    for entry in entries:
        person = Person.query.get(entry.person_id)
        vc = VC.query.get(entry.vc_id)
        
        # Determine type based on debit/credit
        if entry.credit > 0:
            txn_type_val = 'received'
            amount = entry.credit
        else:
            txn_type_val = 'paid'
            amount = entry.debit
        
        transactions.append({
            'date': entry.date,
            'person_name': person.name if person else 'Unknown',
            'vc_name': vc.name if vc else 'Unknown',
            'narration': entry.narration,
            'type': txn_type_val,
            'amount': amount
        })
    
    return render_template(
        'transactions.html',
        transactions=transactions,
        all_vcs=user_vcs,
        total_received=total_received,
        total_paid=total_paid,
        pages=pages,
        page=page
    )