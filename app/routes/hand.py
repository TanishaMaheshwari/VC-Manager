"""
routes/hand.py

BUSINESS LOGIC:
─────────────────────────────────────────────────────────────────────────────
Interest Charged is USER-ENTERED, fixed amount.

CONTRIBUTION FORMULA:
  Members contribute = (VC Amount - Interest Charged) / total_slots per slot

  Example: VC ₹10,000, Interest ₹1,000
  → Contribution pool = 10,000 - 1,000 = ₹9,000
  → Per slot = ₹9,000 / total_slots

PERSON(S) WIN:
  • Winner  → CREDIT payout amount
  • Operator (HM) → DEBIT same payout amount (pays out)
  • Operator (HM) → CREDIT interest_charged (earns interest)

OPERATOR KEEPS:
  • Members contribute: (VC Amount - Interest) / total_slots
  • Operator (HM) → CREDIT interest_charged

REQUIRES: A Person with short_name='HM' belonging to current_user.
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


# ── Helpers ──────────────────────────────────────────────────────────────────

def _redirect_hand(hand):
    return redirect(url_for(
        'vc.view_hand_distribution',
        vc_id=hand.vc_id,
        hand_number=hand.hand_number
    ))


def get_last_balance(person_id):
    last = (
        LedgerEntry.query
        .filter_by(person_id=person_id)
        .order_by(LedgerEntry.id.desc())
        .first()
    )
    return float(last.balance) if last else 0.0


def get_last_operator_balance(vc_id):
    last = (
        LedgerEntry.query
        .filter_by(vc_id=vc_id, person_id=None)
        .order_by(LedgerEntry.id.desc())
        .first()
    )
    return float(last.balance) if last else 0.0


def _get_operator(user_id):
    return Person.query.filter_by(user_id=user_id, short_name='OPERATOR').first()


def _add_operator_ledger(hand, net_amount, now, narration=None):
    prev_balance = get_last_operator_balance(hand.vc_id)
    credit = net_amount if net_amount >= 0 else 0
    debit  = abs(net_amount) if net_amount < 0 else 0

    entry = LedgerEntry(
        person_id=None,
        vc_id=hand.vc_id,
        hand_id=hand.id,
        date=now,
        narration=narration or f"Hand {hand.hand_number} — operator settlement",
        credit=credit,
        debit=debit,
        balance=prev_balance + net_amount
    )
    db.session.add(entry)
    db.session.flush()


def _add_hm_ledger(hand, operator, net_amount, now, narration=None):
    prev_balance = get_last_balance(operator.id)
    credit = net_amount if net_amount > 0 else 0
    debit  = abs(net_amount) if net_amount < 0 else 0

    entry = LedgerEntry(
        person_id=operator.id,
        vc_id=hand.vc_id,
        hand_id=hand.id,
        date=now,
        narration=narration or f"Hand {hand.hand_number} — HM settlement",
        credit=credit,
        debit=debit,
        balance=prev_balance + net_amount
    )
    db.session.add(entry)
    db.session.flush()


def _build_contributions(hand, vc, interest_charged, now):
    total_slots       = int(vc.total_slots)
    contribution_pool = float(vc.amount) - interest_charged
    contrib_per_slot  = contribution_pool / total_slots if total_slots > 0 else 0

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
            paid=False
        )
        db.session.add(contribution)
        db.session.flush()

        current_balance = get_last_balance(member.id)
        ledger_entry = LedgerEntry(
            person_id=member.id,
            vc_id=vc.id,
            hand_id=hand.id,
            date=hand.date,
            narration=f"{vc.name} Haath {hand.hand_number} mai aapka hissa raha",
            debit=member_contribution,
            credit=0,
            balance=current_balance - member_contribution
        )
        db.session.add(ledger_entry)
        db.session.flush()


def _delete_hand_entries(hand):
    """Delete only ledger/contribution entries for this specific hand."""
    LedgerEntry.query.filter_by(hand_id=hand.id).delete(synchronize_session=False)
    HandDistribution.query.filter_by(hand_id=hand.id).delete(synchronize_session=False)
    Contribution.query.filter_by(hand_id=hand.id).delete(synchronize_session=False)
    db.session.flush()


def _recalculate_balances_for_vc(vc):
    """
    After editing any hand, recalculate running balances
    for every member of this VC and the operator ledger.
    """
    member_ids = [m.id for m in vc.members if m is not None]

    operator = _get_operator(vc.user_id)
    if operator:
        member_ids.append(operator.id)

    for person_id in set(member_ids):
        person = Person.query.get(person_id)
        if not person:
            continue

        entries = (
            LedgerEntry.query
            .filter_by(person_id=person_id)
            .order_by(LedgerEntry.date.asc(), LedgerEntry.id.asc())
            .all()
        )

        running = float(person.opening_balance or 0)
        for entry in entries:
            running += float(entry.credit or 0) - float(entry.debit or 0)
            entry.balance = running

    # Recalculate operator (person_id=None) ledger for this VC
    op_entries = (
        LedgerEntry.query
        .filter_by(person_id=None, vc_id=vc.id)
        .order_by(LedgerEntry.date.asc(), LedgerEntry.id.asc())
        .all()
    )
    running = 0.0
    for entry in op_entries:
        running += float(entry.credit or 0) - float(entry.debit or 0)
        entry.balance = running

    db.session.flush()


# ── Routes ───────────────────────────────────────────────────────────────────

@hand_bp.route('/create/<int:hand_id>', methods=['POST'])
@login_required
def create_payout(hand_id):
    hand = VCHand.query.get_or_404(hand_id)
    vc   = hand.vc

    if vc.user_id != current_user.id:
        flash("Unauthorized access.", "danger")
        return redirect(url_for('dashboard.index'))

    operator = _get_operator(current_user.id)
    if not operator:
        flash(
            "Operator person (short name 'HM') not found. "
            "Please create it first before distributing a hand.",
            "danger"
        )
        return _redirect_hand(hand)

    if hand.hand_distributions:
        flash("This hand has already been distributed.", "warning")
        return _redirect_hand(hand)

    payout_type = request.form.get('payout_type', 'person')
    narration   = request.form.get('narration', '').strip()
    now         = datetime.utcnow()

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

        db.session.add(HandDistribution(
            hand_id=hand.id,
            person_id=None,
            amount=bid_price,
            narration=narration or f"Operator kept Hand {hand.hand_number}",
            payment_date=now,
            is_operator_taken=True,
            is_vc_money_taken=True
        ))

        _build_contributions(hand, vc, interest_charged, now)

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

    for i, (person_id_str, amount) in enumerate(zip(winner_ids, parsed_amounts)):
        person_id = int(person_id_str)
        person = Person.query.filter_by(id=person_id, user_id=current_user.id).first()

        if not person:
            flash(f"Person {person_id} not found.", "danger")
            db.session.rollback()
            return _redirect_hand(hand)

        db.session.add(HandDistribution(
            hand_id=hand.id,
            person_id=person_id,
            amount=amount,
            narration=narration or f"{vc.name} Haath {hand.hand_number} mai aapko diye",
            payment_date=now,
            is_operator_taken=False,
            is_vc_money_taken=True
        ))

        # Credit winner
        prev_balance = get_last_balance(person_id)
        db.session.add(LedgerEntry(
            person_id=person_id,
            vc_id=hand.vc_id,
            hand_id=hand.id,
            date=hand.date,
            narration=f"{vc.name} Haath {hand.hand_number} aapki rahi hai",
            credit=amount,
            debit=0,
            balance=prev_balance + amount
        ))
        db.session.flush()

        # HM debit — skip first winner
        if i > 0:
            _add_hm_ledger(
                hand, operator, -amount, now,
                narration=f"{vc.name} Hand {hand.hand_number} mai {person.name} ko gaye"
            )

        _add_operator_ledger(
            hand, -amount, now,
            narration=f"Hand {hand.hand_number} — paid out to {person.name} ₹{amount:,.0f}"
        )

    _build_contributions(hand, vc, interest_charged, now)

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


@hand_bp.route('/<int:vc_id>/hand/<int:hand_id>/edit-payout', methods=['POST'])
@login_required
def edit_payout(vc_id, hand_id):
    hand = VCHand.query.get_or_404(hand_id)
    vc   = hand.vc

    if vc.user_id != current_user.id:
        flash("Unauthorized access.", "danger")
        return redirect(url_for('dashboard.index'))

    operator = _get_operator(current_user.id)
    if not operator:
        flash(
            "Operator person (short name 'HM') not found. "
            "Please create it first before editing a payout.",
            "danger"
        )
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

    payout_type = request.form.get('payout_type', 'person')
    narration   = request.form.get('narration', '').strip()
    now         = datetime.utcnow()

    try:
        interest_charged = float(request.form.get('interest_charged', 0))
    except (KeyError, ValueError):
        flash("Invalid interest charged amount.", "danger")
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

    # Delete only this hand's entries — full rebuild
    _delete_hand_entries(hand)

    # ── OPERATOR KEEPS ───────────────────────────────────────────────────────
    if payout_type == 'operator':
        try:
            bid_price = float(request.form['bid_price'])
        except (KeyError, ValueError):
            flash("Invalid bid price.", "danger")
            return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

        db.session.add(HandDistribution(
            hand_id=hand.id,
            person_id=None,
            amount=bid_price,
            narration=narration or f"Operator kept Hand {hand.hand_number}",
            payment_date=now,
            is_operator_taken=True,
            is_vc_money_taken=True
        ))

        _build_contributions(hand, vc, interest_charged, now)

        _add_operator_ledger(
            hand, interest_charged, now,
            narration=f"Hand {hand.hand_number} — operator kept (interest ₹{interest_charged:,.0f})"
        )

        _recalculate_balances_for_vc(vc)
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

    for idx, (person_id, amount) in enumerate(zip([int(x) for x in winner_ids], parsed_amounts)):
        person = Person.query.filter_by(id=person_id, user_id=current_user.id).first()
        if not person:
            flash(f"Person {person_id} not found.", "danger")
            db.session.rollback()
            return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))

        db.session.add(HandDistribution(
            hand_id=hand.id,
            person_id=person_id,
            amount=amount,
            narration=narration or f"{vc.name} Haath {hand.hand_number} mai aapko diye",
            payment_date=now,
            is_operator_taken=False,
            is_vc_money_taken=True
        ))

        # Credit winner
        prev_balance = get_last_balance(person_id)
        db.session.add(LedgerEntry(
            person_id=person_id,
            vc_id=hand.vc_id,
            hand_id=hand.id,
            date=hand.date,
            narration=f"{vc.name} Haath {hand.hand_number} aapki rahi hai",
            credit=amount,
            debit=0,
            balance=prev_balance + amount
        ))
        db.session.flush()

        # HM debit — skip first winner
        if idx > 0:
            _add_hm_ledger(
                hand, operator, -amount, now,
                narration=f"{vc.name} Hand {hand.hand_number} mai {person.name} ko gaye"
            )

        _add_operator_ledger(
            hand, -amount, now,
            narration=f"Hand {hand.hand_number} — paid out to {person.name} ₹{amount:,.0f}"
        )

    _build_contributions(hand, vc, interest_charged, now)

    _add_operator_ledger(
        hand, interest_charged, now,
        narration=f"Hand {hand.hand_number} — interest charged ₹{interest_charged:,.0f}"
    )

    _recalculate_balances_for_vc(vc)
    db.session.commit()
    flash("Payout updated successfully.", "success")
    return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=hand.hand_number))