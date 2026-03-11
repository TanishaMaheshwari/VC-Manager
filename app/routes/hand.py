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


@hand_bp.route('/create/<int:hand_id>', methods=['POST'])
@login_required
def create_payout(hand_id):
    """
    Record a payout for a hand. Supports three modes:
      - Operator taken: one distribution row with is_operator_taken=True, no ledger credit
      - Single winner:  one distribution row for one person
      - Multiple/split: multiple distribution rows, one per person with their share
    
    POST body expected:
      payout_type      = 'operator' | 'person'
      bid_price        = total payout amount (float)
      narration        = optional string

      If payout_type == 'person':
        winners[]      = list of person_id values  (one or more)
        amounts[]      = list of amounts matching winners[]
    """
    hand = VCHand.query.get_or_404(hand_id)

    if hand.vc.user_id != current_user.id:
        flash("Unauthorized access", "danger")
        return redirect(url_for('dashboard.index'))

    # Prevent double-distribution
    if hand.hand_distributions:
        flash("This hand has already been distributed.", "warning")
        return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

    payout_type = request.form.get('payout_type', 'person')
    narration   = request.form.get('narration', '').strip()
    now         = datetime.utcnow()

    from app.routes.ledger import get_last_balance

    # ── OPERATOR TAKES THE HAND ──────────────────────────────────────────────
    if payout_type == 'operator':
        try:
            bid_price = float(request.form['bid_price'])
        except (KeyError, ValueError):
            flash("Invalid bid price.", "danger")
            return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

        dist = HandDistribution(
            hand_id=hand.id,
            person_id=None,
            amount=bid_price,
            narration=narration or f"Operator took Hand {hand.hand_number}",
            payment_date=now,
            is_operator_taken=True,
            is_vc_money_taken=True
        )
        db.session.add(dist)
        # No ledger credit — operator keeps the money
        db.session.commit()
        flash(f"Hand {hand.hand_number} recorded as operator-taken (₹{bid_price:,.0f})", "success")
        return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

    # ── PERSON(S) WIN THE HAND ───────────────────────────────────────────────
    winner_ids = request.form.getlist('winners[]')
    amounts    = request.form.getlist('amounts[]')

    if not winner_ids:
        flash("Please select at least one winner.", "danger")
        return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

    if len(winner_ids) != len(amounts):
        flash("Winner and amount counts do not match.", "danger")
        return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

    # Parse and validate amounts
    try:
        parsed_amounts = [float(a) for a in amounts]
    except ValueError:
        flash("Invalid amount value.", "danger")
        return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

    total_distributed = sum(parsed_amounts)

    # Validate minimum interest requirement
    required_earned_interest = hand.vc.amount - hand.projected_payout
    earned_interest_from_bid = hand.vc.amount - total_distributed
    if earned_interest_from_bid < required_earned_interest:
        flash(
            f"Total bid must be ₹{hand.projected_payout:,.0f} or less to cover "
            f"minimum interest of ₹{required_earned_interest:,.0f}.",
            "danger"
        )
        return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

    # Create one HandDistribution row per winner
    for person_id_str, amount in zip(winner_ids, parsed_amounts):
        person_id = int(person_id_str)
        person = Person.query.filter_by(id=person_id, user_id=current_user.id).first()
        if not person:
            flash(f"Person {person_id} not found.", "danger")
            db.session.rollback()
            return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

        dist = HandDistribution(
            hand_id=hand.id,
            person_id=person_id,
            amount=amount,
            narration=narration or f"Payout for Hand {hand.hand_number}",
            payment_date=now,
            is_operator_taken=False,
            is_vc_money_taken=True
        )
        db.session.add(dist)

        # Mark this winner's contributions as paid
        Contribution.query.filter_by(
            hand_id=hand.id,
            person_id=person_id
        ).update({'paid': True})

        # Ledger credit entry for each winner
        prev_balance = get_last_balance(person_id)
        ledger_entry = LedgerEntry(
            person_id=person_id,
            vc_id=hand.vc_id,
            date=now,
            narration=f"Payout received for VC {hand.vc.vc_number}, Hand {hand.hand_number}",
            credit=amount,
            balance=prev_balance + amount
        )
        db.session.add(ledger_entry)
        
        # Create Contribution rows for all members if they don't exist yet
        winner_id_set = {int(pid) for pid in winner_ids}
        for person, slots in hand.vc.slots_display:
            existing = Contribution.query.filter_by(
                hand_id=hand.id,
                person_id=person.id
            ).first()
            if not existing:
                per_slot_amount = total_distributed / hand.vc.total_slots
                contrib_amount  = per_slot_amount * slots
                is_paid         = person.id in winner_id_set
                contrib = Contribution(
                    hand_id=hand.id,
                    person_id=person.id,
                    amount=contrib_amount,
                    paid=is_paid,
                    date=now
                )
                db.session.add(contrib)
        
        # Create Contribution rows for all members (nobody is paid — operator took it)
        for person, slots in hand.vc.slots_display:
            existing = Contribution.query.filter_by(
                hand_id=hand.id,
                person_id=person.id
            ).first()
            if not existing:
                per_slot_amount = bid_price / hand.vc.total_slots
                contrib = Contribution(
                    hand_id=hand.id,
                    person_id=person.id,
                    amount=per_slot_amount * slots,
                    paid=False,
                    date=now
                )
                db.session.add(contrib)

        
    db.session.commit()

    winner_count = len(winner_ids)
    flash(
        f"Hand {hand.hand_number} distributed to {winner_count} winner{'s' if winner_count > 1 else ''} "
        f"(total ₹{total_distributed:,.0f})",
        "success"
    )
    return redirect(url_for('vc.view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))


@hand_bp.route('/<int:vc_id>/hand/<int:hand_id>/edit-payout', methods=['POST'])
@login_required
def edit_payout(vc_id, hand_id):
    """
    Edit an existing payout. Replaces all existing distributions for this hand
    with the new submitted set, and rebuilds contributions accordingly.
    Same three modes as create_payout.
    """
    hand = VCHand.query.get_or_404(hand_id)
    vc   = hand.vc

    if vc.user_id != current_user.id:
        flash("Unauthorized access", "danger")
        return redirect(url_for('dashboard.index'))

    payout_type = request.form.get('payout_type', 'person')
    narration   = request.form.get('narration', '').strip()
    now         = datetime.utcnow()

    # Delete all existing distributions for this hand
    HandDistribution.query.filter_by(hand_id=hand.id).delete()
    # Delete existing contributions so they get rebuilt cleanly
    Contribution.query.filter_by(hand_id=hand.id).delete()

    from app.routes.ledger import get_last_balance

    # ── OPERATOR ─────────────────────────────────────────────────────────────
    if payout_type == 'operator':
        try:
            bid_price = float(request.form['bid_price'])
        except (KeyError, ValueError):
            flash("Invalid bid price.", "danger")
            return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

        dist = HandDistribution(
            hand_id=hand.id,
            person_id=None,
            amount=bid_price,
            narration=narration or f"Operator took Hand {hand.hand_number}",
            payment_date=now,
            is_operator_taken=True,
            is_vc_money_taken=True
        )
        db.session.add(dist)

        # Rebuild equal contributions for all members
        _rebuild_contributions(hand, vc, bid_price, winner_ids=[], now=now)

        db.session.commit()
        flash("Payout updated: operator-taken.", "success")
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

    # ── PERSON(S) ─────────────────────────────────────────────────────────────
    winner_ids = request.form.getlist('winners[]')
    amounts    = request.form.getlist('amounts[]')

    if not winner_ids or len(winner_ids) != len(amounts):
        flash("Invalid winner/amount data.", "danger")
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

    try:
        parsed_amounts = [float(a) for a in amounts]
    except ValueError:
        flash("Invalid amount value.", "danger")
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

    total_distributed = sum(parsed_amounts)

    required_earned_interest = vc.amount - hand.projected_payout
    earned_interest_from_bid = vc.amount - total_distributed
    if earned_interest_from_bid < required_earned_interest:
        flash(
            f"Total bid must be ₹{hand.projected_payout:,.0f} or less to cover "
            f"minimum interest of ₹{required_earned_interest:,.0f}.",
            "danger"
        )
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

    winner_id_ints = [int(pid) for pid in winner_ids]

    for person_id, amount in zip(winner_id_ints, parsed_amounts):
        dist = HandDistribution(
            hand_id=hand.id,
            person_id=person_id,
            amount=amount,
            narration=narration or f"Payout for Hand {hand.hand_number}",
            payment_date=now,
            is_operator_taken=False,
            is_vc_money_taken=True
        )
        db.session.add(dist)

    # Rebuild contributions with updated per-person amount
    _rebuild_contributions(hand, vc, total_distributed, winner_ids=winner_id_ints, now=now)

    db.session.commit()
    flash("Payout updated successfully.", "success")
    return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))


def _rebuild_contributions(hand, vc, total_payout, winner_ids, now):
    """
    Helper: create fresh Contribution rows for all members after a payout edit.
    per_person = total_payout / member_count
    Winners get their contribution marked paid=True immediately.
    """
    members = vc.members
    if not members:
        return

    per_person = total_payout / len(members)

    for member in members:
        is_winner = member.id in winner_ids
        contribution = Contribution(
            hand_id=hand.id,
            person_id=member.id,
            amount=per_person,
            date=now,
            paid=is_winner  # winners' contributions auto-marked paid
        )
        db.session.add(contribution)