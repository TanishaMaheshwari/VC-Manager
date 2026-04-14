"""Dashboard routes"""
from flask import Blueprint, jsonify, render_template, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import datetime, date
from app import db
from app.models import VC, VCHand, Person, Contribution, LedgerEntry, Payment
from app.models.transaction import Transaction
from app.forms import PaymentForm, TransactionForm

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    """Main dashboard page"""
    vcs = VC.query.filter_by(user_id=current_user.id).order_by(VC.vc_number).all()
    total_due = sum(c.amount for vc in vcs for hand in vc.hands for c in hand.contributions if not c.paid)
    total_vcs = len(vcs)
    persons = Person.query.filter_by(user_id=current_user.id).all()
    total_persons = len(persons)

    form = PaymentForm()
    transaction_form = TransactionForm()

    # Populate TransactionForm person choices
    transaction_form.person_id.choices = [(p.id, p.name) for p in persons]

    # ── Handle TransactionForm submission ────────────────────────────────────
    if transaction_form.submit.data and transaction_form.validate_on_submit():
        amount = float(transaction_form.amount.data)
        is_credit = transaction_form.type.data == 'credit'

        # 1. Save to Transaction table
        t = Transaction(
            user_id=current_user.id,
            person_id=transaction_form.person_id.data,
            amount=amount,
            type=transaction_form.type.data,
            date=datetime.utcnow(),
            narration=transaction_form.narration.data
        )
        db.session.add(t)

        # 2. Mirror to LedgerEntry (so it appears on person ledger pages)
        from app.routes.ledger import get_last_balance
        prev_balance = get_last_balance(transaction_form.person_id.data)
        ledger_entry = LedgerEntry(
            person_id=transaction_form.person_id.data,
            vc_id=None,
            date=datetime.utcnow(),
            narration=transaction_form.narration.data or '',
            debit=0 if is_credit else amount,
            credit=amount if is_credit else 0,
            balance=prev_balance + (amount if is_credit else -amount)
        )
        db.session.add(ledger_entry)

        db.session.commit()
        flash('Transaction added!', 'success')
        return redirect(url_for('dashboard.index'))

    # ── Recent transactions (last 10) ────────────────────────────────────────
    recent_transactions = (
        Transaction.query
        .filter_by(user_id=current_user.id)
        .order_by(Transaction.date.desc())
        .limit(10)
        .all()
    )

    # ── PaymentForm setup ────────────────────────────────────────────────────
    from app.models.enums import PaymentStatus
    pending_vcs = VC.query.filter(
        VC.user_id == current_user.id,
        VC.status != PaymentStatus.PAID
    ).all()
    form.vc_id.choices = [(vc.id, f"VC {vc.vc_number}") for vc in pending_vcs]

    hands_with_unpaid = []
    for vc in pending_vcs:
        for hand in vc.hands:
            unpaid_contribs = Contribution.query.filter_by(hand_id=hand.id, paid=False).all()
            if unpaid_contribs:
                hands_with_unpaid.append(hand)

    hands_with_unpaid.sort(key=lambda h: h.id)
    all_members = {
        member.id: member
        for vc in pending_vcs
        for member in vc.members
        if member.user_id == current_user.id
    }

    form.hand_id.choices = [(h.id, f"Hand {h.hand_number}") for h in hands_with_unpaid]
    form.person_id.choices = [(p.id, p.name) for p in all_members.values()]

    # ── Handle PaymentForm submission ────────────────────────────────────────
    if form.validate_on_submit():
        contrib = Contribution.query.filter_by(
            hand_id=form.hand_id.data,
            person_id=form.person_id.data
        ).order_by(Contribution.date.asc()).first()

        if contrib:
            contrib.paid = True
            try:
                if form.amount.data:
                    contrib.amount = form.amount.data
            except Exception:
                pass
            contrib.date = form.date.data or datetime.utcnow()
            db.session.add(contrib)
        else:
            flash("No existing contribution record found for this person in the selected hand.", 'warning')

        from app.routes.ledger import get_last_balance
        prev_balance = get_last_balance(form.person_id.data)
        ledger_entry = LedgerEntry(
            person_id=form.person_id.data,
            vc_id=form.vc_id.data,
            date=form.date.data or datetime.utcnow(),
            narration=form.narration.data or '',
            debit=0,
            credit=form.amount.data,
            balance=prev_balance + form.amount.data
        )
        db.session.add(ledger_entry)

        db.session.commit()
        flash('Contribution recorded successfully!', 'success')
        return redirect(url_for('dashboard.index'))

    return render_template(
        'dashboard.html',
        form=form,
        transaction_form=transaction_form,
        recent_transactions=recent_transactions,
        today=date.today(),
        total_due=total_due,
        total_vcs=total_vcs,
        vcs=vcs,
        persons=persons,
        total_persons=total_persons
    )


@dashboard_bp.route("/hand/<int:hand_id>/payout_details")
@login_required
def hand_payout_details(hand_id):
    """Returns winners and their payout amounts for a distributed hand."""
    hand = db.session.get(VCHand, hand_id)
    if not hand or hand.vc.user_id != current_user.id:
        return jsonify({"error": "Not found"}), 404

    winners = []
    for d in hand.hand_distributions:
        if not d.is_operator_taken and d.person_id:
            winners.append({
                "person_id": d.person_id,
                "name": d.person.name,
                "amount": d.amount
            })

    return jsonify({"winners": winners})


@dashboard_bp.route("/person_balance/<int:person_id>")
@login_required
def person_balance(person_id):
    """Returns current ledger balance for a person."""
    person = Person.query.filter_by(id=person_id, user_id=current_user.id).first()
    if not person:
        return jsonify({"success": False}), 404
    return jsonify({"success": True, "balance": person.ledger_balance})