"""API routes for VC-Manager application"""
from flask import Blueprint, jsonify
from flask_login import current_user, login_required
from app import db
from app.models.vc import VC, VCHand
from app.models.person import Person
from app.models.ledger import LedgerEntry

api_bp = Blueprint('api', __name__, url_prefix='/api')

@api_bp.route("/vc/<int:vc_id>/details")
@login_required
def vc_details(vc_id):
    from app.models.contribution import Contribution
    vc = VC.query.filter_by(id=vc_id, user_id=current_user.id).first_or_404()
    
    hands = []
    # Filter hands: only include those with at least one unpaid contribution
    hands_with_unpaid = []
    for h in vc.hands:
        # Check if this hand has any unpaid contributions
        unpaid_contribs = Contribution.query.filter_by(
            hand_id=h.id,
            paid=False
        ).all()
        if unpaid_contribs:
            hands_with_unpaid.append(h)
    
    # Sort by hand_id to get the minimum hand_id with unpaid contributions
    hands_with_unpaid.sort(key=lambda h: h.id)
    
    for h in hands_with_unpaid:
        # Include hands with unpaid contributions
        winner_name = h.winner_short_name or "Pending"

        hands.append({
            "id": h.id,
            "hand_number": h.hand_number,
            "winner_name": winner_name,
            "date": h.date.isoformat() if h.date else None
        })

    return jsonify({
        "hands": hands
    })

@api_bp.route("/hand/<int:hand_id>/details")
@login_required
def hand_details(hand_id):
    hand = db.session.get(VCHand, hand_id)
    if not hand:
        return jsonify({"error": "Hand not found"}), 404
    
    # Verify hand belongs to current user's VC
    if hand.vc.user_id != current_user.id:
        return jsonify({"error": "Unauthorized"}), 403

    expected_ids = {m.id for m in hand.vc.members}
    distributed_ids = {d.person_id for d in hand.hand_distributions}
    potential_ids = expected_ids - distributed_ids

    # Exclude persons who already have ledger entries
    ledger_entries = LedgerEntry.query.filter(
        LedgerEntry.vc_id == hand.vc.id,
        LedgerEntry.narration.like(f"Payment for VC {hand.vc.vc_number}, Hand {hand.hand_number}%")
    ).all()
    paid_ids = {l.person_id for l in ledger_entries}
    pending_ids = potential_ids - paid_ids

    pending_persons = Person.query.filter(Person.id.in_(pending_ids), Person.user_id==current_user.id).all()

    contribution_amount = hand.actual_contribution_per_person  # or compute dynamically

    return jsonify({
        "pending_persons": [{"id": p.id, "name": p.name} for p in pending_persons],
        "contribution_amount": contribution_amount
    })

@api_bp.route('/person_balance/<int:person_id>')
@login_required
def person_balance(person_id):
    from app.routes.ledger import get_last_balance
    person = Person.query.filter_by(id=person_id, user_id=current_user.id).first()
    if not person:
        return jsonify({'success': False, 'error': 'Person not found'}), 404
    balance = get_last_balance(person_id)
    return jsonify({'success': True, 'balance': balance, 'name': person.name})

@api_bp.route("/hand/<int:hand_id>/payout_details")
@login_required
def hand_payout_details(hand_id):
    hand = db.session.get(VCHand, hand_id)
    if not hand or hand.vc.user_id != current_user.id:
        return jsonify({"error": "Not found"}), 404

    print("DISTRIBUTIONS:", [(d.person_id, d.is_operator_taken, d.amount) for d in hand.hand_distributions])

    from app.models.contribution import Contribution
    winners = []
    for d in hand.hand_distributions:
        if not d.is_operator_taken and d.person_id:
            # Find member contribution for this person in this hand
            member_contribution = sum(
                c.amount for c in Contribution.query.filter_by(hand_id=hand.id, person_id=d.person_id)
            )
            interest_amount = getattr(hand, 'interest_amount', None)
            net_payout = d.amount - member_contribution if member_contribution is not None else None
            winners.append({
                "person_id": d.person_id,
                "name": d.person.name,
                "amount": d.amount,
                "member_contribution": member_contribution,
                "interest_amount": interest_amount,
                "net_payout": net_payout
            })

    return jsonify({"winners": winners})