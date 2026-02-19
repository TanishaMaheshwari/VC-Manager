"""Dashboard routes"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_login import current_user, login_required
from datetime import datetime, date
from app import db
from app.models import VC, VCHand, Person, Contribution, LedgerEntry, Payment
from app.forms import PaymentForm

dashboard_bp = Blueprint('dashboard', __name__)

@dashboard_bp.route('/', methods=['GET', 'POST'])
@login_required
def index():
    """Main dashboard page"""
    vcs = VC.query.filter_by(user_id=current_user.id).order_by(VC.vc_number).all()
    # Total due = sum of all unpaid contributions globally (across all people)
    total_due = sum(c.amount for vc in vcs for hand in vc.hands for c in hand.contributions if not c.paid)
    total_vcs = len(vcs)
    persons = Person.query.filter_by(user_id=current_user.id).all()
    total_persons = len(persons)
    form = PaymentForm()

    # 1. VC dropdown: only show VCs with pending payments for current user
    from app.models.enums import PaymentStatus
    pending_vcs = VC.query.filter(VC.user_id==current_user.id, VC.status != PaymentStatus.PAID).all()
    form.vc_id.choices = [(vc.id, f"VC {vc.vc_number}") for vc in pending_vcs]

    # 2. Filter hands with unpaid contributions, get minimum hand_id with unpaid contributions
    hands_with_unpaid = []
    for vc in pending_vcs:
        for hand in vc.hands:
            unpaid_contribs = Contribution.query.filter_by(
                hand_id=hand.id,
                paid=False
            ).all()
            if unpaid_contribs:
                hands_with_unpaid.append(hand)
    
    # Sort to get minimum hand_id
    hands_with_unpaid.sort(key=lambda h: h.id)
    all_hands = {hand.id: hand for hand in hands_with_unpaid}
    # Get members from current user's persons only
    all_members = {member.id: member for vc in pending_vcs for member in vc.members if member.user_id == current_user.id}
    
    # Initialize hand and person choices for form validation on POST
    form.hand_id.choices = [(h.id, f"Hand {h.hand_number}") for h in hands_with_unpaid]
    form.person_id.choices = [(p.id, p.name) for p in all_members.values()]

    if form.validate_on_submit():
        # --- 4. Mark existing contribution as paid (do not add duplicate) ---
        contrib = Contribution.query.filter_by(
            hand_id=form.hand_id.data,
            person_id=form.person_id.data
        ).order_by(Contribution.date.asc()).first()

        if contrib:
            # Update existing contribution record
            contrib.paid = True
            # if amount provided, update stored amount to actual paid amount
            try:
                if form.amount.data:
                    contrib.amount = form.amount.data
            except Exception:
                pass
            contrib.date = form.date.data or datetime.utcnow()
            db.session.add(contrib)
        else:
            alert_msg = "No existing contribution record found for this person in the selected hand."

        # --- 5. Ledger entry (credit) ---
        from app.routes.ledger import get_last_balance
        person = Person.query.get(form.person_id.data)
        vc = VC.query.get(form.vc_id.data)
        hand = db.session.get(VCHand, form.hand_id.data)
        prev_balance = get_last_balance(form.person_id.data)
        ledger_entry = LedgerEntry(
            person_id=form.person_id.data,
            vc_id=form.vc_id.data,
            date=form.date.data or datetime.utcnow(),
            narration=f"Payment for VC {vc.vc_number}, Hand {hand.hand_number}: {form.narration.data}",
            credit=form.amount.data,
            balance=prev_balance + form.amount.data
        )
        db.session.add(ledger_entry)

        db.session.commit()
        flash('Contribution recorded successfully!', 'success')
        return redirect(url_for('vc.vcs_list'))
    
    return render_template('dashboard.html', form=form, today=date.today(), total_due=total_due, total_vcs=total_vcs, vcs=vcs, persons=persons, total_persons=total_persons)
