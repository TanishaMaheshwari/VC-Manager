"""Payment routes for VC-Manager application"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import datetime
from app import db
from app.models.vc import VC, VCHand
from app.models.person import Person
from app.models.contribution import Contribution
from app.models.payment import Payment
from app.models.ledger import LedgerEntry
from app.forms import PaymentForm

payment_bp = Blueprint('payment', __name__, url_prefix='/payment')

@payment_bp.route('/record', methods=["GET", "POST"])
@login_required
def record_payment():
    form = PaymentForm()

    # 1. VC dropdown: only show VCs with pending payments for current user
    from app.models.vc import PaymentStatus
    from app.models.contribution import Contribution
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

    # Initialize hand and person choices for form validation
    form.hand_id.choices = [(h.id, f"Hand {h.hand_number}") for h in all_hands.values()]
    form.person_id.choices = [(p.id, p.name) for p in all_members.values()]

    if form.validate_on_submit():
        vc = VC.query.filter_by(id=form.vc_id.data, user_id=current_user.id).first()
        hand = VCHand.query.get(form.hand_id.data)
        person = Person.query.filter_by(id=form.person_id.data, user_id=current_user.id).first()

        if not vc or not hand or not person:
            flash("Valid VC, hand, and person are required.", "danger")
            return redirect(request.url)

        # 3. Record Payment
        payment = Payment(
            vc_id=vc.id,
            hand_id=hand.id,
            person_id=person.id,
            amount=form.amount.data,
            date=form.date.data,
            narration=f"Payment for VC {vc.vc_number}, Hand {hand.hand_number}: {form.narration.data}"
        )
        db.session.add(payment)

        # 4. Update Ledger automatically
        from app.routes.ledger import get_last_balance
        current_balance = get_last_balance(person.id)
        ledger_entry = LedgerEntry(
            person_id=person.id,
            vc_id=vc.id,
            date=form.date.data,
            narration=payment.narration,
            debit=0,
            credit=payment.amount,
            balance=current_balance + payment.amount
        )
        db.session.add(ledger_entry)
        # Mark any matching Contribution records for this hand/person as paid
        try:
            contribs = Contribution.query.filter_by(hand_id=hand.id, person_id=person.id).all()
            for c in contribs:
                c.paid = True
                db.session.add(c)
        except Exception:
            # ignore if contributions table not yet present or other issues
            pass

        db.session.commit()

        flash(f"Payment of ₹{payment.amount} recorded for {person.name}", "success")
        return redirect(url_for("payment.record_payment"))

    return render_template("payment/create.html", form=form, pending_vcs=pending_vcs, all_hands=all_hands, all_members=all_members)

@payment_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_payment():
    form = PaymentForm()

    # 1. VC dropdown: only show VCs with pending payments for current user
    from app.models.enums import PaymentStatus
    from app.models.contribution import Contribution
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
        # --- 4. Record contribution (payment IN) ---
        contribution = Contribution(
            hand_id=form.hand_id.data,
            person_id=form.person_id.data,
            amount=form.amount.data,
            date=datetime.utcnow(),
            paid=False
        )
        db.session.add(contribution)

        # --- 5. Ledger entry (credit) ---
        from app.routes.ledger import get_last_balance
        person = Person.query.filter_by(id=form.person_id.data, user_id=current_user.id).first()
        vc = VC.query.filter_by(id=form.vc_id.data, user_id=current_user.id).first()
        hand = VCHand.query.get(form.hand_id.data)
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

    return render_template('payment/create.html', form=form, datetime=datetime)
