"""Hand/Payment routes for VC-Manager application"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import datetime
from app import db
from app.models.vc import VC, VCHand, HandDistribution
from app.models.person import Person
from app.models.contribution import Contribution
from app.models.payment import Payment
from app.models.ledger import LedgerEntry
from app.forms import PaymentForm

hand_bp = Blueprint('hand', __name__, url_prefix='/hand')

@hand_bp.route('/create/<int:hand_id>/<int:person_id>', methods=['POST'])
@login_required
def create_payout(hand_id, person_id):
    hand = VCHand.query.get_or_404(hand_id)
    # Verify hand belongs to current user's VC
    if hand.vc.user_id != current_user.id:
        flash("Unauthorized access", "danger")
        return redirect(url_for('dashboard.index'))
    
    person = Person.query.filter_by(id=person_id, user_id=current_user.id).first_or_404()

    total_pool = hand.total_contributed
    if not total_pool:
        flash("No contributions yet to distribute.", "danger")
        return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

    # Create payout
    payout = HandDistribution(
        hand_id=hand.id,
        person_id=person.id,
        amount=total_pool,
        narration=f"Payout for Hand {hand.hand_number}",
        payment_date=datetime.utcnow(),
        is_vc_money_taken=True
    )
    db.session.add(payout)

    # Mark the payout recipient's contributions as paid for this hand
    contributions = Contribution.query.filter_by(
        hand_id=hand.id,
        person_id=person.id
    ).all()
    for contribution in contributions:
        contribution.paid = True

    # Ledger entry (credit)
    from app.routes.ledger import get_last_balance
    prev_balance = get_last_balance(person.id)
    ledger_entry = LedgerEntry(
        person_id=person.id,
        vc_id=hand.vc_id,
        date=datetime.utcnow(),
        narration=f"Payout received for VC {hand.vc.vc_number}, Hand {hand.hand_number}",
        credit=total_pool,
        balance=prev_balance + total_pool
    )
    db.session.add(ledger_entry)

    db.session.commit()
    flash(f"Payout of {total_pool} given to {person.name}", "success")
    return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

@hand_bp.route('/<int:vc_id>/hand/<int:hand_id>/edit-payout', methods=['POST'])
@login_required
def edit_payout(vc_id, hand_id):
    payout_id = request.form["payout_id"]
    person_id = int(request.form["person_id"])
    amount = float(request.form["amount"])

    payout = HandDistribution.query.get_or_404(payout_id)
    hand = VCHand.query.get_or_404(hand_id)
    vc = hand.vc

    # Calculate minimum allowed bid price based on min_interest
    required_earned_interest = vc.amount - hand.projected_payout
    earned_interest_from_bid = vc.amount - amount

    if earned_interest_from_bid < required_earned_interest:
        flash(
            f"The bid price must be ₹{hand.projected_payout:.0f} or less to cover the minimum interest of ₹{required_earned_interest:.0f}.",
            "danger"
        )
        return redirect(url_for("vc.view_hand_distribution", vc_id=vc_id, hand_number=hand.hand_number))

    # Update payout
    payout.person_id = person_id
    payout.amount = amount

    # Update contributions for all members (contr per person = new bid price / member count)
    members = vc.members
    per_person_contribution = amount / len(members)
    # Remove old contributions for this hand
    Contribution.query.filter_by(hand_id=hand.id).delete()
    # Add new contributions
    for member in members:
        contribution = Contribution(
            hand_id=hand.id,
            person_id=member.id,
            amount=per_person_contribution,
            date=datetime.utcnow(),
            paid=False
        )
        db.session.add(contribution)

    db.session.commit()

    flash("Payout updated successfully", "success")
    return redirect(url_for("vc.view_hand_distribution", vc_id=vc_id, hand_number=hand.hand_number))
