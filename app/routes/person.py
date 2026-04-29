"""Person routes for VC-Manager application"""

from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import datetime
from sqlalchemy import or_, cast, String, func
from sqlalchemy.exc import IntegrityError
from app import db
from app.models.person import Person
from app.models.ledger import LedgerEntry
from app.forms import PersonForm

person_bp = Blueprint('person', __name__, url_prefix='/person')


@person_bp.route('/')
@login_required
def persons():

    # ── Subquery: latest ledger id per person ──
    latest_id_subq = (
        db.session.query(
            LedgerEntry.person_id,
            func.max(LedgerEntry.id).label('max_id')
        )
        .group_by(LedgerEntry.person_id)
        .subquery()
    )

    # ── Get balance of that latest id ──
    latest_balance_subq = (
        db.session.query(
            LedgerEntry.person_id,
            LedgerEntry.balance.label('latest_balance')
        )
        .join(
            latest_id_subq,
            (LedgerEntry.person_id == latest_id_subq.c.person_id) &
            (LedgerEntry.id == latest_id_subq.c.max_id)
        )
        .subquery()
    )

    # ── Main query ──
    results = (
        db.session.query(
            Person,
            latest_balance_subq.c.latest_balance
        )
        .outerjoin(
            latest_balance_subq,
            Person.id == latest_balance_subq.c.person_id
        )
        .filter(Person.user_id == current_user.id)
        .order_by(Person.name.asc())
        .all()
    )

    # ── Attach balance ──
    persons = []
    for person, balance in results:
        person.current_balance = float(balance or person.opening_balance or 0.0)
        persons.append(person)

    return render_template('person/list.html', persons=persons)

@person_bp.route('/search')
@login_required
def search_persons():
    query = request.args.get('q', '').strip()
    sort_order = request.args.get('sort', 'name_asc')

    # ── Subquery: latest entry by MAX(id) ──
    latest_id_subq = (
        db.session.query(
            LedgerEntry.person_id,
            func.max(LedgerEntry.id).label('max_id')
        )
        .group_by(LedgerEntry.person_id)
        .subquery()
    )

    latest_balance_subq = (
        db.session.query(
            LedgerEntry.person_id,
            LedgerEntry.balance.label('latest_balance')
        )
        .join(
            latest_id_subq,
            (LedgerEntry.person_id == latest_id_subq.c.person_id) &
            (LedgerEntry.id == latest_id_subq.c.max_id)
        )
        .subquery()
    )

    # ── MAIN QUERY (IMPORTANT: apply user filter here) ──
    q = db.session.query(
        Person,
        latest_balance_subq.c.latest_balance
    ).outerjoin(
        latest_balance_subq,
        Person.id == latest_balance_subq.c.person_id
    ).filter(
        Person.user_id == current_user.id   # ✅ THIS WAS MISSING
    )

    # ── Search filter ──
    if query:
        ledger_exists = db.session.query(LedgerEntry.id).filter(
            LedgerEntry.person_id == Person.id,
            or_(
                LedgerEntry.narration.ilike(f'%{query}%'),
                cast(LedgerEntry.debit, String).ilike(f'%{query}%'),
                cast(LedgerEntry.credit, String).ilike(f'%{query}%'),
                cast(LedgerEntry.balance, String).ilike(f'%{query}%')
            )
        ).exists()

        person_match = or_(
            Person.name.ilike(f'%{query}%'),
            Person.short_name.ilike(f'%{query}%'),
            Person.phone.ilike(f'%{query}%'),
            Person.phone2.ilike(f'%{query}%'),
            cast(Person.opening_balance, String).ilike(f'%{query}%')
        )

        q = q.filter(or_(person_match, ledger_exists))

    # ── Sorting ──
    if sort_order == 'name_asc':
        q = q.order_by(Person.name.asc())

    elif sort_order == 'balance_asc':
        q = q.order_by(
            latest_balance_subq.c.latest_balance.asc().nulls_last(),
            Person.name.asc()
        )

    elif sort_order == 'balance_desc':
        q = q.order_by(
            latest_balance_subq.c.latest_balance.desc().nulls_last(),
            Person.name.asc()
        )

    # ── Execute ──
    results = q.all()

    persons = []
    for person, balance in results:
        person.current_balance = float(balance or person.opening_balance or 0.0)
        persons.append(person)

    return render_template('person/list_card_partial.html', persons=persons)

@person_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_person():
    form = PersonForm()

    if form.validate_on_submit():

        # ✅ Check uniqueness per user
        name_exists = Person.query.filter_by(
            user_id=current_user.id,
            name=form.name.data
        ).first()

        if name_exists:
            flash('A person with that name already exists in your account.', 'danger')
            return render_template('person/create.html', form=form)

        short_name_exists = Person.query.filter_by(
            user_id=current_user.id,
            short_name=form.short_name.data
        ).first()

        if short_name_exists:
            flash('A person with that short name already exists in your account.', 'danger')
            return render_template('person/create.html', form=form)

        person = Person(
            user_id=current_user.id,
            name=form.name.data,
            short_name=form.short_name.data,
            phone=form.phone.data,
            phone2=form.phone2.data,
            opening_balance=form.opening_balance.data or 0,
            created_at=datetime.utcnow()
        )

        db.session.add(person)

        try:
            db.session.commit()

            flash('Person created successfully!', 'success')
            return redirect(url_for('person.persons'))

        except IntegrityError:
            db.session.rollback()
            flash('A person with that name or short name already exists.', 'danger')
            return render_template('person/create.html', form=form)

        except Exception as e:
            db.session.rollback()
            flash(f'An unexpected error occurred: {str(e)}', 'danger')
            return render_template('person/create.html', form=form)

    if form.is_submitted() and not form.validate():
        flash('Please fill out all required fields correctly.', 'danger')

    return render_template('person/create.html', form=form)


@person_bp.route('/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_person(id):
    person = Person.query.filter_by(id=id, user_id=current_user.id).first_or_404()
    form = PersonForm(obj=person)

    if form.validate_on_submit():
        person.name = form.name.data
        person.short_name = form.short_name.data
        person.phone = form.phone.data
        person.phone2 = form.phone2.data
        person.opening_balance = form.opening_balance.data or 0

        try:
            db.session.commit()
            flash('Person updated successfully!', 'success')
            return redirect(url_for('person.persons'))

        except IntegrityError:
            db.session.rollback()
            flash('Error: A person with that name or short name already exists.', 'danger')
            return redirect(url_for('person.edit_person', id=id))

        except Exception as e:
            db.session.rollback()
            flash(f'An unexpected error occurred: {str(e)}', 'danger')
            return redirect(url_for('person.edit_person', id=id))

    return render_template('person/edit.html', form=form, person=person)

@person_bp.route('/<int:id>/delete', methods=['POST'])
@login_required
def delete_person(id):
    person = Person.query.filter_by(id=id, user_id=current_user.id).first_or_404()

    try:
        db.session.delete(person)
        db.session.commit()
        flash('Person deleted successfully!', 'success')

    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting person: {str(e)}', 'danger')

    return redirect(url_for('person.persons'))