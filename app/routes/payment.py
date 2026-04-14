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
from app.models.enums import PaymentStatus
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

    # 2. Show all hands for selected VCs
    all_hands = {}
    for vc in pending_vcs:
        for hand in vc.hands:
            all_hands[hand.id] = hand
    # Show only persons without payout ledger entry for selected hand
    all_members = {}
    selected_hand_id = form.hand_id.data if form.hand_id.data else None
    selected_vc_id = form.vc_id.data if form.vc_id.data else None
    selected_vc = VC.query.get(selected_vc_id) if selected_vc_id else None
    selected_hand = VCHand.query.get(selected_hand_id) if selected_hand_id else None
    if selected_vc and selected_hand:
        for member in selected_vc.members:
            if member.user_id != current_user.id:
                continue
            slots = selected_vc.get_slots(member.id)
            narration_like = f"{selected_vc.name} Haath {selected_hand.hand_number} mai aapko diye%"
            ledger_entries = LedgerEntry.query.filter(
                LedgerEntry.person_id == member.id,
                LedgerEntry.vc_id == selected_vc.id,
                LedgerEntry.narration.like(narration_like)
            ).count()
            if ledger_entries < slots:
                all_members[member.id] = member

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
            narration=f"{vc.name} Haath {hand.hand_number} {form.narration.data}"
        )
        db.session.add(payment)

        # 4. Add Transaction entry (type=credit for received)
        from app.models.transaction import Transaction
        transaction = Transaction(
            user_id=current_user.id,
            person_id=person.id,
            amount=form.amount.data,
            type='credit',
            date=form.date.data,
            narration=payment.narration
        )
        db.session.add(transaction)

        # 5. Update Ledger automatically
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
        return redirect(url_for("dashboard.index"))

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
            narration=f"{vc.name} Haath {hand.hand_number}: {form.narration.data}",
            debit=0,
            credit=form.amount.data,
            balance=prev_balance + form.amount.data
        )
        db.session.add(ledger_entry)

        db.session.commit()
        flash('Contribution recorded successfully!', 'success')
        return redirect(url_for('vc.vcs_list'))

    return render_template('payment/create.html', form=form, datetime=datetime)


@payment_bp.route('/record-payout', methods=['POST'])
@login_required
def record_payout_payment():
    """
    Record actual cash payout to a winner.
    Creates a Payment record and a ledger DEBIT entry
    (money going OUT from the VC/operator to the winner).
    """
    from app.routes.ledger import get_last_balance

    vc_id     = request.form.get('vc_id',     type=int)
    hand_id   = request.form.get('hand_id',   type=int)
    person_id = request.form.get('person_id', type=int)
    amount    = request.form.get('amount',    type=float)
    narration = request.form.get('narration', '').strip()
    date_str  = request.form.get('date', '').strip()

    if not all([vc_id, hand_id, person_id, amount]):
        flash("All fields are required.", "danger")
        return redirect(url_for('dashboard.index'))

    vc     = VC.query.filter_by(id=vc_id, user_id=current_user.id).first_or_404()
    hand   = VCHand.query.filter_by(id=hand_id, vc_id=vc_id).first_or_404()
    person = Person.query.filter_by(id=person_id, user_id=current_user.id).first_or_404()

    try:
        pay_date = datetime.strptime(date_str, '%Y-%m-%dT%H:%M') if date_str else datetime.utcnow()
    except ValueError:
        pay_date = datetime.utcnow()

    # Payment record
    payment = Payment(
        vc_id=vc_id,
        hand_id=hand_id,
        person_id=person_id,
        amount=amount,
        date=pay_date,
        narration=narration or f"{vc.name} Haath {hand.hand_number} ke diye gaye"
    )
    db.session.add(payment)

    # Transaction entry (type=debit for paid)
    from app.models.transaction import Transaction
    transaction = Transaction(
        user_id=current_user.id,
        person_id=person_id,
        amount=amount,
        type='debit',
        date=pay_date,
        narration=payment.narration
    )
    db.session.add(transaction)

    # Ledger CREDIT entry for the winner (they received cash)
    prev_balance = get_last_balance(person_id)
    ledger_entry = LedgerEntry(
        person_id=person_id,
        vc_id=vc_id,
        date=pay_date,
        narration=narration or f"{vc.name} Haath {hand.hand_number} mai aapko diye",
        credit=amount,  # CREDIT in ledger (person receives money)
        debit=0,
        balance=prev_balance + amount
    )
    db.session.add(ledger_entry)

    db.session.commit()
    flash(f"Payout of ₹{amount:,.0f} recorded for {person.name}.", "success")
    return redirect(url_for('dashboard.index'))