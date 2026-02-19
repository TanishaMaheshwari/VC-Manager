"""VC routes for VC-Manager application"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import current_user
from datetime import datetime, timezone
from app import db
from app.models.vc import VC, VCHand, HandDistribution, PaymentStatus
from app.models.payment import Payment
from app.models.contribution import Contribution
from app.models.ledger import LedgerEntry
from app.models.person import Person
from app.forms import VCForm
from app.utils import login_required
import traceback

vc_bp = Blueprint('vc', __name__, url_prefix='/vc')

@vc_bp.route('/')
@login_required
def vcs_list():
    vcs = VC.query.filter_by(user_id=current_user.id).order_by(VC.vc_number).all()
    # Total due = sum of all unpaid contributions globally (across all people)
    total_due = sum(c.amount for vc in vcs for hand in vc.hands for c in hand.contributions if not c.paid)
    total_vcs = len(vcs)
    total_members = sum(len(vc.members) for vc in vcs)
    return render_template('vc/list.html', vcs=vcs, total_due=total_due, total_members=total_members, total_vcs=total_vcs)

@vc_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_vc():
    form = VCForm()
    form.members.choices = [(p.id, p.name) for p in Person.query.filter_by(user_id=current_user.id).all()]
    
    # Determine next VC number for display
    last_vc = VC.query.order_by(VC.vc_number.desc()).first()
    next_vc_number = (last_vc.vc_number + 1) if last_vc else 1

    if form.validate_on_submit():
        vc = VC(
            user_id=current_user.id,
            vc_number=next_vc_number,
            name=form.name.data,
            start_date=datetime.combine(form.start_date.data, datetime.min.time()),
            amount=form.amount.data,
            min_interest=form.min_interest.data,
            tenure=form.tenure.data,
            narration=form.narration.data
        )

        # Add members
        selected_people = Person.query.filter(Person.id.in_(form.members.data)).all()
        vc.members.extend(selected_people)

        db.session.add(vc)
        db.session.flush()

        # Create hands automatically
        vc.create_hands()

        db.session.commit()
        flash(f'VC {next_vc_number} created successfully with {form.tenure.data} hands and {len(vc.members)} members!', 'success')
        return redirect(url_for('vc.vcs_list'))
    
    return render_template('vc/create.html', form=form, vc_number=next_vc_number)


@vc_bp.route('/<int:id>')
@login_required
def view_vc(id):
    vc = VC.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    hands = VCHand.query.filter_by(vc_id=id).order_by(VCHand.hand_number).all()
    return render_template('vc/view.html', vc=vc, hands=hands)



@vc_bp.route('/<int:vc_id>/hand/<int:hand_number>')
@login_required
def view_hand_distribution(vc_id, hand_number):
    vc = VC.query.filter_by(id=vc_id, user_id=current_user.id).first_or_404()
    hand = VCHand.query.filter_by(vc_id=vc.id, hand_number=hand_number).first_or_404()

    # Get list of member IDs for this VC
    vc_member_ids = [m.id for m in vc.members]

    # Filter contributions: only for this hand AND only from VC members
    contributions = Contribution.query.filter(
        Contribution.hand_id == hand.id,
        Contribution.person_id.in_(vc_member_ids)
    ).order_by(Contribution.date.asc()).all()

    payout = HandDistribution.query.filter_by(hand_id=hand.id).first()
    payout_recorded = payout is not None

    ledger_entries = LedgerEntry.query.filter(
        LedgerEntry.vc_id == vc.id,
        LedgerEntry.narration.like(f'%Payment for VC {vc.vc_number}, Hand {hand.hand_number}%')
    ).all()

    # Map person_id → ledger entry
    ledger_map = {}
    for entry in ledger_entries:
        if entry.person_id not in ledger_map:
            ledger_map[entry.person_id] = entry  # take first matching entry

    members = vc.members
    member_eligibility = {}
    
    # Get all payouts for this VC by traversing the relationships
    all_payouts = []
    for h in vc.hands:
        all_payouts.extend(h.hand_distributions)
    winning_member_ids = {p.person_id for p in all_payouts}

    for member in members:
        is_eligible = member.id not in winning_member_ids
        member_eligibility[member.id] = {
            'is_eligible': is_eligible,
            'reason': 'won a previous hand' if not is_eligible else None
        }
    
    return render_template(
        'vc/hand_distribution.html',
        vc=vc,
        hand=hand,
        members=members,
        member_eligibility=member_eligibility,
        payout_recorded=payout_recorded,
        contributions=contributions,
        payout=payout,
        distributions=[], # Placeholder for template
        vc_payouts=[], # Placeholder for template
        ledger_map=ledger_map
    )

@vc_bp.route('/<int:vc_id>/distribute-hand', methods=['POST'])
@login_required
def distribute_hand(vc_id):
    try:
        print("--- Starting distribute_hand function ---")

        # Verify VC belongs to current user
        vc = VC.query.filter_by(id=vc_id, user_id=current_user.id).first_or_404()

        # 1. Fetch form data
        hand_id = request.form.get("hand_id")
        winners = request.form.getlist("winners")
        bid_price = request.form.get("bid_price", type=float)
        narration = request.form.get("narration")

        print(f"Received data: hand_id={hand_id}, winners={winners}, bid_price={bid_price}, narration={narration}")

        # Basic validation
        if not winners or bid_price is None or bid_price <= 0:
            print("Validation failed: winners or bid_price is invalid.")
            flash("Error: Winner(s) and a valid bid price are required.", 'danger')
            return redirect(url_for('vc.view_hand_distribution', vc_id=vc_id, hand_number=VCHand.query.get(hand_id).hand_number))

        hand = VCHand.query.filter_by(id=hand_id, vc_id=vc.id).first_or_404()

        required_earned_interest = vc.amount - hand.projected_payout
        earned_interest_from_bid = vc.amount - bid_price
        
        if earned_interest_from_bid < required_earned_interest:
            flash(f"The bid price must be ₹{hand.projected_payout:.0f} or less to cover the minimum interest of ₹{required_earned_interest:.0f}.", 'danger')
            return redirect(url_for('vc.view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))

        # Get winner objects from current user's persons only
        winner_objs = Person.query.filter(Person.id.in_(winners), Person.user_id==current_user.id).all()

        # Check if already distributed
        if HandDistribution.query.filter_by(hand_id=hand.id).first():
            print("Hand already distributed.")
            flash("Error: This hand has already been distributed.", 'danger')
            return redirect(url_for('vc.view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))

        # Check if winner has already won
        winning_member_ids = {d.person_id for h in vc.hands for d in h.hand_distributions}
        for w in winner_objs:
            if w.id in winning_member_ids:
                print(f"Winner {w.name} already won a previous hand.")
                flash(f"Error: {w.name} has already won a previous hand and is ineligible.", 'danger')
                return redirect(url_for('vc.view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))

        print("Validation checks passed. Starting database operations.")
        # Ledger Fix: Use a dictionary to track real-time balances
        all_persons = Person.query.all()
        person_balances = {p.id: p.total_balance for p in all_persons}
        print(f"Initial person_balances: {person_balances}")

        # 1. Record payouts for winner(s)
        payout_per_winner = bid_price / len(winner_objs)
        print(f"Payout per winner: {payout_per_winner}")
        for winner in winner_objs:
            print(f"Processing winner: {winner.name}")
            # Hand Distribution record
            payout = HandDistribution(
                hand_id=hand.id,
                person_id=winner.id,
                amount=payout_per_winner,
                narration=narration or f"Payout for Hand {hand.hand_number}",
                is_vc_money_taken=True
            )
            db.session.add(payout)
            print("Added HandDistribution to session.")

            # Ledger entry for payout (credit)
            from app.routes.ledger import get_last_balance
            current_balance = get_last_balance(winner.id)
            new_balance = current_balance + payout_per_winner
            ledger_entry = LedgerEntry(
                person_id=winner.id,
                vc_id=vc.id,
                date=datetime.utcnow(),
                narration=f"Payout received for VC {vc.name}, Hand {hand.hand_number}. ({narration or 'No comment'})",
                credit=payout_per_winner,
                balance=new_balance
            )
            db.session.add(ledger_entry)
            person_balances[winner.id] = new_balance
            print(f"Added LedgerEntry (credit). New balance for {winner.name}: {new_balance}")

        # 2. Contributions for all members
        members = vc.members
        per_person_contribution = bid_price / len(members)
        print(f"Contribution per member: {per_person_contribution}")
        winning_ids = {w.id for w in winner_objs}
        for member in members:
            print(f"Processing member: {member.name}")

            # Contribution record
            # Mark as paid=True if this member is a winner, False otherwise
            contribution = Contribution(
                hand_id=hand.id,
                person_id=member.id,
                amount=per_person_contribution,
                date=datetime.utcnow(),
                paid=(member.id in winning_ids)  # Mark as paid if member is a winner
            )
            db.session.add(contribution)
            print("Added Contribution to session.")

            # Ledger entry (debit)
            from app.routes.ledger import get_last_balance
            current_balance = get_last_balance(member.id)
            new_balance = current_balance - per_person_contribution
            ledger_entry = LedgerEntry(
                person_id=member.id,
                vc_id=vc.id,
                date=datetime.utcnow(),
                narration=f"Contribution for VC {vc.name}, Hand {hand.hand_number}.",
                debit=per_person_contribution,
                balance=new_balance
            )
            db.session.add(ledger_entry)
            person_balances[member.id] = new_balance
            print(f"Added LedgerEntry (debit). New balance for {member.name}: {new_balance}")

        # 3. Advance VC hand
        if vc.current_hand == hand.hand_number:
            vc.current_hand += 1
            print("Advanced VC's current hand number.")

        db.session.commit()
        flash(f"Hand {hand.hand_number} distributed successfully with bid price ₹{bid_price}!", 'success')
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))

    except Exception as e:
        db.session.rollback()
        print("An exception occurred! Rolling back changes.")
        traceback.print_exc()
        flash(f"An error occurred during distribution: {str(e)}. Changes have been rolled back.", 'danger')
        return redirect(url_for('vc.view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))

@vc_bp.route('/delete/<int:id>', methods=['POST'])
@login_required
def delete_vc(id):
    from app.routes.ledger import close_ledger
    
    vc = VC.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    vc_number = vc.vc_number
    
    # Close ledgers for all members of this VC
    for member in vc.members:
        close_ledger(member.id)
    
    # Delete the VC
    db.session.delete(vc)
    db.session.commit()
    
    flash(f'VC {vc_number} and associated ledgers deleted successfully!', 'success')
    return redirect(url_for('vc.vcs_list'))
