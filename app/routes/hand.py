"""
Drop-in replacement for create_payout and edit_payout in routes/hand.py

Business logic:
─────────────────────────────────────────────────────────────────────────────
PERSON WINS:
  • Members contribute min(bid, projected) / total_slots each
  • Winner receives full bid amount (ledger credit)
  • If bid > projected: operator is debited (bid - projected) — pays the extra
  • Operator ledger net = vc.amount - bid  (interest earned minus any subsidy)

OPERATOR KEEPS:
  • Members contribute projected / total_slots each
  • Operator ledger credit = full projected_payout (pockets the pool)
  • bid_price field stores the projected_payout for record-keeping

In both cases operator always gets:  vc.amount - bid  net
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


def _build_contributions(hand, vc, contrib_per_slot, winner_id_set, now):
    """
    Build contribution and ledger entries for all VC members.
    Each member's ledger balance is calculated as: last_balance - contribution
    """
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
            paid=(member.id in winner_id_set)
        )
        db.session.add(contribution)
        db.session.flush()

        # FIX #1: Get LAST balance before this transaction
        current_balance = get_last_balance(member.id)
        
        ledger_entry = LedgerEntry(
            person_id=member.id,
            vc_id=vc.id,
            date=now,
            narration=f"Contribution for VC {vc.name}, Hand {hand.hand_number}.",
            debit=member_contribution,
            credit=0,
            balance=current_balance - member_contribution  # NEW balance after debit
        )
        db.session.add(ledger_entry)
        db.session.flush()


def _delete_hand_ledger_entries(hand, vc):
    """
    Selectively delete ledger entries created by this hand's distribution.
    
    Rules:
    - Operator entries (person_id=None): always delete
    - Winner payout entries: always delete
    - Contribution debit entries:
        - If a manual payment entry exists for same person+hand: keep it, rename to standard payment format
        - If no payment exists: delete the contribution entry
    """
    hand_num = hand.hand_number

    # 1. Delete operator entries for this hand
    LedgerEntry.query.filter(
        LedgerEntry.vc_id == vc.id,
        LedgerEntry.person_id.is_(None),
        LedgerEntry.narration.like(f"%Hand {hand_num}%")
    ).delete(synchronize_session=False)

    # 2. Delete winner payout entries
    LedgerEntry.query.filter(
        LedgerEntry.vc_id == vc.id,
        LedgerEntry.narration.like(f"%Payout received — VC Hand {hand_num}%")
    ).delete(synchronize_session=False)

    # 3. Handle contribution entries per member
    contrib_entries = LedgerEntry.query.filter(
        LedgerEntry.vc_id == vc.id,
        LedgerEntry.narration.like(f"%Contribution for VC%Hand {hand_num}%")
    ).all()

    for entry in contrib_entries:
        if entry.person_id is None:
            db.session.delete(entry)
            continue

        # Check if a manual payment entry exists for this person+hand
        payment_entry = LedgerEntry.query.filter(
            LedgerEntry.vc_id == vc.id,
            LedgerEntry.person_id == entry.person_id,
            LedgerEntry.narration.like(f"%Payment for VC%Hand {hand_num}%")
        ).first()

        if payment_entry:
            # Keep payment, just ensure narration is in standard format
            payment_entry.narration = f"Payment for VC {vc.name}, Hand {hand_num}"
            db.session.delete(entry)  # delete the contribution debit
        else:
            # No payment exists — delete the contribution entry entirely
            db.session.delete(entry)

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
    projected   = float(hand.projected_payout)
    total_slots = int(vc.total_slots)

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

        # Members pay projected / total_slots each
        contrib_per_slot = projected / total_slots
        _build_contributions(hand, vc, contrib_per_slot, winner_id_set=set(), now=now)

        # Operator ledger: credit full projected_payout (pockets the pool)
        _add_operator_ledger(
            hand, projected, now,
            narration=f"Hand {hand.hand_number} — operator kept pool (₹{projected:,.0f})"
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

    # Members always contribute based on min(bid, projected)
    member_contribution_total = min(total_bid, projected)
    contrib_per_slot          = member_contribution_total / total_slots

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

        # FIX #1: Get LAST balance before this credit
        prev_balance = get_last_balance(person_id)
        ledger_entry = LedgerEntry(
            person_id=person_id,
            vc_id=hand.vc_id,
            date=now,
            narration=f"Payout received — VC Hand {hand.hand_number}",
            credit=amount,
            debit=0,
            balance=prev_balance + amount  # NEW balance after credit
        )
        db.session.add(ledger_entry)

    # Build contributions for all members
    _build_contributions(hand, vc, contrib_per_slot, winner_id_set, now)

    # Operator ledger: net = vc.amount - total_bid
    # Positive = operator earned interest
    # Negative = operator subsidised (bid > projected)
    operator_net = float(vc.amount) - total_bid
    narr_parts   = [f"Hand {hand.hand_number} — "]
    if total_bid > projected:
        extra = total_bid - projected
        narr_parts.append(f"operator subsidised extra ₹{extra:,.0f} (bid ₹{total_bid:,.0f} > projected ₹{projected:,.0f})")
    else:
        interest = float(vc.amount) - total_bid
        narr_parts.append(f"operator earned interest ₹{interest:,.0f}")
    _add_operator_ledger(hand, operator_net, now, narration="".join(narr_parts))

    db.session.commit()

    winner_count = len(winner_ids)
    flash(
        f"Hand {hand.hand_number} distributed to "
        f"{winner_count} winner{'s' if winner_count > 1 else ''} "
        f"(₹{total_bid:,.0f}).",
        "success"
    )
    return _redirect_hand(hand)


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
    projected   = float(hand.projected_payout)
    total_slots = int(vc.total_slots)

    # FIX #2: Delete ALL previous ledger entries created by this hand
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

        contrib_per_slot = projected / total_slots
        _build_contributions(hand, vc, contrib_per_slot, winner_id_set=set(), now=now)

        _add_operator_ledger(
            hand, projected, now,
            narration=f"Hand {hand.hand_number} — operator kept pool (₹{projected:,.0f})"
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

    total_bid             = sum(parsed_amounts)
    member_contrib_total  = min(total_bid, projected)
    contrib_per_slot      = member_contrib_total / total_slots
    winner_id_set         = {int(pid) for pid in winner_ids}

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

        # FIX #1: Get LAST balance before this credit (after old entries deleted)
        prev_balance = get_last_balance(person_id)
        ledger_entry = LedgerEntry(
            person_id=person_id,
            vc_id=hand.vc_id,
            date=now,
            narration=f"Payout received — VC Hand {hand.hand_number}",
            credit=amount,
            debit=0,
            balance=prev_balance + amount
        )
        db.session.add(ledger_entry)

    # Rebuild contributions
    _build_contributions(hand, vc, contrib_per_slot, winner_id_set, now)

    # Rebuild operator ledger
    operator_net = float(vc.amount) - total_bid
    if total_bid > projected:
        extra = total_bid - projected
        op_narr = f"Hand {hand.hand_number} — operator subsidised extra ₹{extra:,.0f}"
    else:
        interest = float(vc.amount) - total_bid
        op_narr  = f"Hand {hand.hand_number} — operator earned interest ₹{interest:,.0f}"
    _add_operator_ledger(hand, operator_net, now, narration=op_narr)

    db.session.commit()
    flash("Payout updated successfully.", "success")
    return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))