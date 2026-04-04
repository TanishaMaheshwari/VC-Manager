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
    persons = Person.query.filter_by(user_id=current_user.id).all()
    return render_template('person/list.html', persons=persons)


@person_bp.route('/search')
@login_required
def search_persons():
    query = request.args.get('q', '')
    sort_order = request.args.get('sort', 'name_asc')

    base_query = db.session.query(Person).filter_by(user_id=current_user.id)

    if query:
        # Only include persons if their own fields match, or if they have at least one ledger entry matching
        ledger_match = db.session.query(LedgerEntry.id).filter(
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

        base_query = base_query.filter(or_(person_match, ledger_match)).distinct()

    # ✅ Latest balance subquery (SAFE version using window function)
    latest_balance_subq = (
        db.session.query(
            LedgerEntry.person_id,
            LedgerEntry.balance.label('latest_balance'),
            func.row_number().over(
                partition_by=LedgerEntry.person_id,
                order_by=LedgerEntry.date.desc()
            ).label('rn')
        ).subquery()
    )

    latest_balance = db.session.query(latest_balance_subq)\
        .filter(latest_balance_subq.c.rn == 1)\
        .subquery()

    # ✅ Sorting
    if sort_order == 'name_asc':
        base_query = base_query.order_by(Person.name.asc())

    elif sort_order == 'balance_asc':
        base_query = base_query.outerjoin(
            latest_balance, Person.id == latest_balance.c.person_id
        ).order_by(latest_balance.c.latest_balance.asc().nulls_last())

    elif sort_order == 'balance_desc':
        base_query = base_query.outerjoin(
            latest_balance, Person.id == latest_balance.c.person_id
        ).order_by(latest_balance.c.latest_balance.desc().nulls_last())

    persons = base_query.all()

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