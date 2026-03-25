"""VC routes for VC-Manager application"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import current_user
from datetime import datetime, timezone

from flask_wtf import FlaskForm
from app import db
from app.models import vc
from app.models.vc import VC, VCHand, HandDistribution, PaymentStatus, vc_members
from app.models.payment import Payment
from app.models.contribution import Contribution
from app.models.ledger import LedgerEntry
from app.models.person import Person
from app.forms import VCForm
from app.routes import hand
from app.routes.ledger import get_last_balance
from app.utils import login_required
import traceback
import json

vc_bp = Blueprint('vc', __name__, url_prefix='/vc')

@vc_bp.route('/')
@login_required
def vcs_list():
    vcs = VC.query.filter_by(user_id=current_user.id).order_by(VC.vc_number).all()
    # Total due = sum of all unpaid contributions globally (across all people)
    total_due = sum(c.amount for vc in vcs for hand in vc.hands for c in hand.contributions if not c.paid)
    total_vcs = len(vcs)
    total_members = sum(vc.total_slots for vc in vcs) if vcs else 0
    return render_template('vc/list.html', vcs=vcs, total_due=total_due, total_members=total_members, total_vcs=total_vcs)


@vc_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_vc():
    print(f"DEBUG raw request.form = {dict(request.form)}")

    form = VCForm()
    form.members.choices = [
        (p.id, p.name)
        for p in Person.query.filter_by(user_id=current_user.id).all()
    ]

    last_vc        = VC.query.order_by(VC.vc_number.desc()).first()
    next_vc_number = (last_vc.vc_number + 1) if last_vc else 1

    if form.validate_on_submit():
        try:
            raw_slots = json.loads(request.form.get('slot_data', '{}'))
            slot_map  = {int(k): max(1, int(v)) for k, v in raw_slots.items()}
        except (ValueError, TypeError):
            slot_map = {}

        vc = VC(
            user_id      = current_user.id,
            vc_number    = next_vc_number,
            name         = form.name.data,
            start_date   = datetime.combine(form.start_date.data, datetime.min.time()),
            amount       = form.amount.data,
            min_interest = form.min_interest.data,
            tenure       = form.tenure.data,
            narration    = form.narration.data,
        )

        selected_ids    = form.members.data
        selected_people = Person.query.filter(Person.id.in_(selected_ids)).all()
        vc.members.extend(selected_people)

        db.session.add(vc)
        db.session.flush()

        print(f"DEBUG slot_map = {slot_map}")
        for person in selected_people:
            print(f"DEBUG person.id = {person.id!r}  type = {type(person.id)}  lookup = {slot_map.get(person.id, 'NOT FOUND')}")
            slots = slot_map.get(person.id, 1)
            slots = slot_map.get(person.id, 1)
            db.session.execute(
                vc_members.update()
                .where(
                    (vc_members.c.vc_id     == vc.id) &
                    (vc_members.c.person_id == person.id)
                )
                .values(slots=slots)
            )

        db.session.flush()
        vc.create_hands()
        db.session.commit()

        total_slots = sum(slot_map.get(p.id, 1) for p in selected_people)
        flash(
            f'VC "{vc.name}" created — {form.tenure.data} hands, '
            f'{len(selected_people)} members ({total_slots} total slots).',
            'success'
        )
        return redirect(url_for('vc.vcs_list'))

    return render_template('vc/create.html', form=form, vc_number=next_vc_number)


@vc_bp.route('/<int:id>')
@login_required
def view_vc(id):
    vc = VC.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    hands = VCHand.query.filter_by(vc_id=id).order_by(VCHand.hand_number).all()
    csrf_form = FlaskForm()
    return render_template('vc/view.html', vc=vc, hands=hands, form=csrf_form)

@vc_bp.route('/<int:id>/edit', methods=['POST'])
@login_required
def edit_vc(id):
    vc = VC.query.filter_by(id=id, user_id=current_user.id).first_or_404()

    name       = request.form.get('name', '').strip()
    start_date = request.form.get('start_date', '').strip()

    if not name:
        flash("VC name cannot be empty.", "danger")
        return redirect(url_for('vc.view_vc', id=id))

    if not start_date:
        flash("Start date cannot be empty.", "danger")
        return redirect(url_for('vc.view_vc', id=id))

    try:
        new_start = datetime.strptime(start_date, '%Y-%m-%d')
    except ValueError:
        flash("Invalid date format.", "danger")
        return redirect(url_for('vc.view_vc', id=id))

    # If start date changed, shift all hand dates by the same delta
    if new_start != vc.start_date.replace(hour=0, minute=0, second=0, microsecond=0):
        delta = new_start - vc.start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        for hand in vc.hands:
            hand.date = hand.date + delta

    vc.name       = name
    vc.start_date = new_start

    db.session.commit()
    flash(f"VC updated successfully.", "success")
    return redirect(url_for('vc.view_vc', id=id))

# ── Drop-in replacement for view_hand_distribution in routes/vc.py ──────────
# Replace your existing view_hand_distribution function with this one.
# Everything else in routes/vc.py stays the same.

@vc_bp.route('/<int:vc_id>/hand/<int:hand_number>')
@login_required
def view_hand_distribution(vc_id, hand_number):
    vc   = VC.query.filter_by(id=vc_id, user_id=current_user.id).first_or_404()
    hand = VCHand.query.filter_by(vc_id=vc.id, hand_number=hand_number).first_or_404()

    vc_member_ids = [m.id for m in vc.members]

    # All distributions for this hand
    distributions = HandDistribution.query.filter_by(hand_id=hand.id).all()
    payout_recorded = len(distributions) > 0
    payout = distributions[0] if distributions else None

    balance_map = {}
    for d in distributions:
        if not d.is_operator_taken and d.person_id:
            balance_map[d.person_id] = get_last_balance(d.person_id)

    contributions = Contribution.query.filter(
        Contribution.hand_id == hand.id,
        Contribution.person_id.in_(vc_member_ids)
    ).order_by(Contribution.date.asc()).all()

    # Keep `payout` for backwards-compat with any other template references.
    # For single-winner hands this is the one record; for multi-winner it's
    # the first one. The template should prefer iterating `distributions`.
    payout = distributions[0] if distributions else None

    ledger_entries = LedgerEntry.query.filter(
        LedgerEntry.vc_id == vc.id,
        LedgerEntry.narration.like(f'%Payment for VC {vc.vc_number}, Hand {hand.hand_number}%')
    ).all()

    ledger_map = {}
    for entry in ledger_entries:
        if entry.person_id not in ledger_map:
            ledger_map[entry.person_id] = entry

    members = vc.members

    # Build eligibility: a person is ineligible if they've already won ANY
    # previous hand (person-type distribution only, not operator hands).
    all_person_winner_ids = set()
    for h in vc.hands:
        for d in h.hand_distributions:
            if not d.is_operator_taken and d.person_id is not None:
                all_person_winner_ids.add(d.person_id)

    # Build win history per member for this VC
    member_eligibility = {}
    for member in members:
        if member is None:
            continue
        wins = [
            d for h in vc.hands
            for d in h.hand_distributions
            if not d.is_operator_taken and d.person_id == member.id
        ]
        if wins:
            # e.g. "Hand 2 · ₹800"
            win_labels = [f"Hand {d.vc_hand.hand_number} · ₹{d.amount:,.0f}" for d in wins]
            member_eligibility[member.id] = {
                'is_eligible': True,  # always eligible now
                'win_info': ", ".join(win_labels)
            }
        else:
            member_eligibility[member.id] = {
                'is_eligible': True,
                'win_info': None
            }
    member_slots = {p.id: vc.get_slots(p.id) for p in vc.members}

    return render_template(
        'vc/hand_distribution.html',
        vc=vc,
        hand=hand,
        members=members,
        member_eligibility=member_eligibility,
        payout_recorded=payout_recorded,
        contributions=contributions,
        payout=payout,               # first distribution or None
        distributions=distributions, # all distributions for this hand
        ledger_map=ledger_map,
        balance_map=balance_map ,
        member_slots=member_slots
    )

@vc_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete_vc(id):
    from app.routes.ledger import close_ledger
    
    vc = VC.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    vc_number = vc.vc_number
    
    # Close ledgers for all members of this VC
    for member in vc.members:
        if member is None:
            continue    
        close_ledger(member.id)
    
    # Delete the VC
    db.session.delete(vc)
    db.session.commit()
    
    flash(f'VC {vc_number} and associated ledgers deleted successfully!', 'success')
    return redirect(url_for('vc.vcs_list'))
