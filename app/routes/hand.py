"""
Drop-in replacement for create_payout and edit_payout in routes/hand.py

CORRECTED BUSINESS LOGIC:
─────────────────────────────────────────────────────────────────────────────
Interest Charged is USER-ENTERED, fixed amount.

CONTRIBUTION FORMULA:
  Members contribute = (VC Amount - Interest Charged) / total_slots per slot
  
  Example: VC ₹10,000, Interest ₹1,000
  → Contribution pool = 10,000 - 1,000 = ₹9,000
  → Per slot = ₹9,000 / total_slots

PERSON(S) WIN:
  • User enters "Interest Charged (₹)" → Fixed amount
  • Members contribute: (VC Amount - Interest) / total_slots per slot
  • Each winner receives their payout amount (independent)
  • Operator gets: Interest_Charged

OPERATOR KEEPS:
  • User enters interest charged
  • Members contribute: (VC Amount - Interest) / total_slots
  • Operator gets: Interest_Charged

Key: Contribution pool = VC Amount - Interest (what members need to pay back)
─────────────────────────────────────────────────────────────────────────────
"""

from datetime import datetime
from flask import Blueprint, request, redirect, url_for, flash
from flask_login import login_required, current_user
from app import db
from app.models.vc import VCHand, HandDistribution
from app.models.person import Person
from app.models.contribution import Contribution
from app.models.ledger import LedgerEntry

hand_bp = Blueprint('hand', __name__)


def _redirect_hand(hand):
    return redirect(url_for(
        'vc.view_hand_distribution',
        vc_id=hand.vc_id,
        hand_number=hand.hand_number
    ))


def get_last_balance(person_id):
    """
    Get the last balance for a person across all ledger entries.
    Returns 0 if no entries exist.
    """
    last_entry = (
        LedgerEntry.query
        .filter_by(person_id=person_id)
        .order_by(LedgerEntry.id.desc())
        .first()
    )
    return float(last_entry.balance) if last_entry else 0.0


def _add_operator_ledger(hand, net_amount, now, narration=None):
    """
    Add a ledger entry for the operator account (person_id=None, vc-level).
    net_amount > 0 = credit (operator gains), < 0 = debit (operator pays out).
    """
    # Get last operator balance for this VC
    last = (
        LedgerEntry.query
        .filter_by(vc_id=hand.vc_id, person_id=None)
        .order_by(LedgerEntry.id.desc())
        .first()
    )
    prev_balance = float(last.balance) if last else 0.0

    credit = net_amount if net_amount >= 0 else 0
    debit  = abs(net_amount) if net_amount < 0 else 0

    entry = LedgerEntry(
        person_id=None,
        vc_id=hand.vc_id,
        date=now,
        narration=narration or f"Hand {hand.hand_number} — operator settlement",
        credit=credit,
        debit=debit,
        balance=prev_balance + net_amount
    )
    db.session.add(entry)


def _build_contributions(hand, vc, interest_charged, now):
    """
    Build contribution and ledger entries for all VC members.
    
    CORRECTED: Contributions are based on (VC Amount - Interest Charged).
    Members pay back what they borrowed minus the interest the operator keeps.
    
    Each member contributes: (vc.amount - interest_charged) / total_slots per slot
    
    Example: VC Amount = ₹10,000, Interest = ₹1,000
    Contribution pool = 10,000 - 1,000 = ₹9,000
    Per slot = ₹9,000 / total_slots
    """
    total_slots = int(vc.total_slots)
    vc_amount = float(vc.amount)
    contribution_pool = vc_amount - interest_charged
    contrib_per_slot = contribution_pool / total_slots if total_slots > 0 else 0
    
    for member in vc.members:
        if member is None:
            continue
        member_slots = vc.get_slots(member.id)
        if member_slots == 0:
            continue
        member_contribution = contrib_per_slot * member_slots

        contribution = Contribution(
            hand_id=hand.id,
            person_id=member.id,
            amount=member_contribution,
            date=now,
            paid=False  # Set to False initially, will be marked paid if they're a winner
        )
        db.session.add(contribution)
        db.session.flush()

        # Get LAST balance before this transaction
        current_balance = get_last_balance(member.id)
        
        ledger_entry = LedgerEntry(
            person_id=member.id,
            vc_id=vc.id,
            date=now,
            narration=f"contri for VC {vc.name}, Hand {hand.hand_number}.",
            debit=member_contribution,
            credit=0,
            balance=current_balance - member_contribution  # NEW balance after debit
        )
        db.session.add(ledger_entry)
        db.session.flush()


@hand_bp.route('/create/<int:hand_id>', methods=['POST'])
@login_required
def create_payout(hand_id):
    hand = VCHand.query.get_or_404(hand_id)
    vc   = hand.vc

    if vc.user_id != current_user.id:
        flash("Unauthorized access.", "danger")
        return redirect(url_for('dashboard.index'))

    if hand.hand_distributions:
        flash("This hand has already been distributed.", "warning")
        return _redirect_hand(hand)

    payout_type = request.form.get('payout_type', 'person')
    narration   = request.form.get('narration', '').strip()
    now         = datetime.utcnow()
    
    # Get user-entered interest_charged
    try:
        interest_charged = float(request.form.get('interest_charged', 0))
    except (KeyError, ValueError):
        flash("Invalid interest charged amount.", "danger")
        return _redirect_hand(hand)

    # ── OPERATOR KEEPS ───────────────────────────────────────────────────────
    if payout_type == 'operator':
        try:
            bid_price = float(request.form['bid_price'])
        except (KeyError, ValueError):
            flash("Invalid bid price.", "danger")
            return _redirect_hand(hand)

        # Distribution record
        dist = HandDistribution(
            hand_id=hand.id,
            person_id=None,
            amount=bid_price,
            narration=narration or f"Operator kept Hand {hand.hand_number}",
            payment_date=now,
            is_operator_taken=True,
            is_vc_money_taken=True
        )
        db.session.add(dist)

        # Build contributions: (VC Amount - Interest) / total_slots
        _build_contributions(hand, vc, interest_charged, now)

        # Operator ledger: credit interest_charged
        _add_operator_ledger(
            hand, interest_charged, now,
            narration=f"Hand {hand.hand_number} — operator kept (interest ₹{interest_charged:,.0f})"
        )

        db.session.commit()
        flash(f"Hand {hand.hand_number} recorded as operator-kept (₹{bid_price:,.0f}).", "success")
        return _redirect_hand(hand)

    # ── PERSON(S) WIN ────────────────────────────────────────────────────────
    winner_ids = request.form.getlist('winners[]')
    amounts    = request.form.getlist('amounts[]')

    if not winner_ids:
        flash("Please select at least one winner.", "danger")
        return _redirect_hand(hand)

    if len(winner_ids) != len(amounts):
        flash("Winner and amount counts do not match.", "danger")
        return _redirect_hand(hand)

    try:
        parsed_amounts = [float(a) for a in amounts]
    except ValueError:
        flash("Invalid amount value.", "danger")
        return _redirect_hand(hand)

    total_bid = sum(parsed_amounts)
    winner_id_set = {int(pid) for pid in winner_ids}

    # Create HandDistribution rows and winner ledger credits
    for person_id_str, amount in zip(winner_ids, parsed_amounts):
        person_id = int(person_id_str)
        person    = Person.query.filter_by(id=person_id, user_id=current_user.id).first()
        if not person:
            flash(f"Person {person_id} not found.", "danger")
            db.session.rollback()
            return _redirect_hand(hand)

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

        # Get LAST balance before this credit
        prev_balance = get_last_balance(person_id)
        ledger_entry = LedgerEntry(
            person_id=person_id,
            vc_id=hand.vc_id,
            date=now,
            narration=f"आपकी व्.स. छुट्टी है — हाथ {hand.hand_number}",
            credit=amount,
            debit=0,
            balance=prev_balance + amount  # NEW balance after credit
        )
        db.session.add(ledger_entry)

    # Build contributions: (VC Amount - Interest) / total_slots
    _build_contributions(hand, vc, interest_charged, now)

    # Operator ledger: interest_charged
    _add_operator_ledger(
        hand, interest_charged, now,
        narration=f"Hand {hand.hand_number} — interest charged ₹{interest_charged:,.0f}"
    )

    db.session.commit()

    winner_count = len(winner_ids)
    flash(
        f"Hand {hand.hand_number} distributed to "
        f"{winner_count} winner{'s' if winner_count > 1 else ''} "
        f"(₹{total_bid:,.0f}), interest ₹{interest_charged:,.0f}.",
        "success"
    )
    return _redirect_hand(hand)


def _delete_hand_ledger_entries(hand, vc):
    """
    Delete ALL ledger entries created by this hand:
    - Member contribution entries
    - Winner payout entries
    - Operator ledger entries
    """
    hand_narration_pattern = f"Hand {hand.hand_number}"
    
    # Delete all ledger entries that mention this hand
    LedgerEntry.query.filter(LedgerEntry.vc_id == vc.id).delete(synchronize_session=False)
    
    db.session.flush()


@hand_bp.route('/<int:vc_id>/hand/<int:hand_id>/edit-payout', methods=['POST'])
@login_required
def edit_payout(vc_id, hand_id):
    hand = VCHand.query.get_or_404(hand_id)
    vc   = hand.vc

    if vc.user_id != current_user.id:
        flash("Unauthorized access.", "danger")
        return redirect(url_for('dashboard.index'))

    payout_type = request.form.get('payout_type', 'person')
    narration   = request.form.get('narration', '').strip()
    now         = datetime.utcnow()

    # Get user-entered interest_charged
    try:
        interest_charged = float(request.form.get('interest_charged', 0))
    except (KeyError, ValueError):
        flash("Invalid interest charged amount.", "danger")
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

    # Delete ALL previous ledger entries created by this hand
    _delete_hand_ledger_entries(hand, vc)
    
    # Delete distributions and contributions
    HandDistribution.query.filter_by(hand_id=hand.id).delete(synchronize_session=False)
    Contribution.query.filter_by(hand_id=hand.id).delete(synchronize_session=False)
    
    db.session.flush()

    # ── OPERATOR KEEPS ───────────────────────────────────────────────────────
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
            narration=narration or f"Operator kept Hand {hand.hand_number}",
            payment_date=now,
            is_operator_taken=True,
            is_vc_money_taken=True
        )
        db.session.add(dist)

        # Build contributions: (VC Amount - Interest) / total_slots
        _build_contributions(hand, vc, interest_charged, now)

        _add_operator_ledger(
            hand, interest_charged, now,
            narration=f"Hand {hand.hand_number} — operator kept (interest ₹{interest_charged:,.0f})"
        )

        db.session.commit()
        flash("Payout updated: operator-kept.", "success")
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

    # ── PERSON(S) WIN ────────────────────────────────────────────────────────
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

    total_bid = sum(parsed_amounts)
    winner_id_set = {int(pid) for pid in winner_ids}

    # Create new winner payouts with recalculated balances
    for person_id, amount in zip([int(x) for x in winner_ids], parsed_amounts):
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

        # Get LAST balance before this credit (after old entries deleted)
        prev_balance = get_last_balance(person_id)
        ledger_entry = LedgerEntry(
            person_id=person_id,
            vc_id=hand.vc_id,
            date=now,
            narration=f"आपकी vc छुट्टी है — हाथ {hand.hand_number}",
            credit=amount,
            debit=0,
            balance=prev_balance + amount
        )
        db.session.add(ledger_entry)

    # Build contributions: (VC Amount - Interest) / total_slots
    _build_contributions(hand, vc, interest_charged, now)

    # Rebuild operator ledger
    _add_operator_ledger(
        hand, interest_charged, now,
        narration=f"Hand {hand.hand_number} — interest charged ₹{interest_charged:,.0f}"
    )

    db.session.commit()
    flash("Payout updated successfully.", "success")
    return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))