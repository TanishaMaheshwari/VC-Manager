"""Person routes for VC-Manager application"""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import current_user, login_required
from datetime import datetime
from sqlalchemy import or_
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
        base_query = base_query.filter(
            or_(
                Person.name.ilike(f'%{query}%'),
                Person.short_name.ilike(f'%{query}%')
            )
        )

    from sqlalchemy import select, func

    latest_balance = (
        db.session.query(
            LedgerEntry.person_id,
            LedgerEntry.balance.label('latest_balance')
        )
        .distinct(LedgerEntry.person_id)
        .order_by(LedgerEntry.person_id, LedgerEntry.date.desc())
        .subquery()
    )


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
    
    return render_template('person/list_partial.html', persons=persons)


@person_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_person():
    form = PersonForm()
    if form.validate_on_submit():

        # Check uniqueness per user before hitting DB
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

            # if form.opening_balance.data and form.opening_balance.data > 0:
            #     ledger_entry = LedgerEntry(
            #         person_id=person.id,
            #         date=person.created_at,
            #         narration="Opening Balance",
            #         debit=0,
            #         credit=form.opening_balance.data,
            #         balance=form.opening_balance.data
            #     )
            #     db.session.add(ledger_entry)
            #     db.session.commit()

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
            flash('Error: A person with that name or short name already exists. Please use a different name.', 'danger')
            return redirect(url_for('person.edit_person', id=id))
        except Exception as e:
            db.session.rollback()
            flash(f'An unexpected error occurred: {str(e)}', 'danger')
            return redirect(url_for('person.edit_person', id=id))
            
    return render_template('person/edit.html', form=form, person=person)

