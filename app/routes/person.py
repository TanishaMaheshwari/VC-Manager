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
    
    if sort_order == 'name_asc':
        base_query = base_query.order_by(Person.name.asc())
    elif sort_order == 'balance_asc':
        base_query = base_query.order_by(Person.opening_balance.asc())
    elif sort_order == 'balance_desc':
        base_query = base_query.order_by(Person.opening_balance.desc())
    
    persons = base_query.all()
    
    return render_template('person/list_partial.html', persons=persons)


@person_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_person():
    form = PersonForm()
    if form.validate_on_submit():
        # Create person object
        person = Person(
            user_id=current_user.id,
            name=form.name.data,
            short_name=form.short_name.data,
            phone=form.phone.data,
            phone2=form.phone2.data,
            opening_balance=0,
            created_at=datetime.utcnow()
        )
        db.session.add(person)

        try:
            db.session.commit()
            
            # --- Add opening balance to ledger if > 0 ---
            if form.opening_balance.data and form.opening_balance.data > 0:
                ledger_entry = LedgerEntry(
                    person_id=person.id,
                    date=person.created_at,
                    narration="Opening Balance",
                    credit=form.opening_balance.data,
                    balance=form.opening_balance.data  # set initial balance
                )
                db.session.add(ledger_entry)
                db.session.commit()

            flash('Person created successfully!', 'success')    
            return redirect(url_for('person.persons'))
            
        except IntegrityError:
            db.session.rollback()
            flash('Error: A person with that name or short name already exists. Please use a different value.', 'danger')
            return redirect(url_for('person.create_person'))
        except Exception as e:
            db.session.rollback()
            flash(f'An unexpected database error occurred: {str(e)}', 'danger')
            return redirect(url_for('person.create_person'))

    # If form validation fails, display specific errors
    if not form.validate_on_submit() and form.is_submitted():
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
