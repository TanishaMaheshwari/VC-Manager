"""Ledger routes for VC-Manager application"""
from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file
from flask_login import current_user, login_required
from datetime import datetime
from io import BytesIO
from weasyprint import HTML
from app import db
from app.models.person import Person
from app.models.ledger import LedgerEntry
from app.models.vc import VC
from app.forms import LedgerEntryForm

ledger_bp = Blueprint('ledger', __name__, url_prefix='/ledger')

def get_last_balance(person_id):
    """Get the last balance for a person from their most recent ledger entry"""
    last_entry = LedgerEntry.query.filter_by(person_id=person_id).order_by(LedgerEntry.date.desc()).first()
    if last_entry:
        return last_entry.balance
    # If no entries, return opening balance
    person = Person.query.get(person_id)
    return person.opening_balance or 0.0

def close_ledger(person_id):
    """
    Close a person's ledger by:
    1. Getting the last balance from the most recent ledger entry
    2. Deleting all existing ledger entries for that person
    3. Adding a new entry with the last balance and narration 'ledger closed on {date}'
    """
    person = Person.query.get(person_id)
    if not person:
        return False
    
    # Get the last entry to capture the final balance
    last_entry = LedgerEntry.query.filter_by(person_id=person_id).order_by(LedgerEntry.date.desc()).first()
    
    if not last_entry:
        return False  # No entries to close
    
    final_balance = last_entry.balance
    
    # Delete all existing ledger entries for this person
    LedgerEntry.query.filter_by(person_id=person_id).delete()
    
    # Add new entry with the final balance
    closing_date = datetime.utcnow()
    closing_entry = LedgerEntry(
        person_id=person_id,
        vc_id=None,
        date=closing_date,
        narration=f"Ledger closed on {closing_date.strftime('%d-%m-%Y %H:%M')}",
        debit=0,
        credit=0,
        balance=final_balance
    )
    
    db.session.add(closing_entry)
    db.session.commit()
    
    return True

@ledger_bp.route('/<int:person_id>')
@login_required
def person_ledger(person_id):
    person = Person.query.filter_by(id=person_id, user_id=current_user.id).first_or_404()

    # Filter by VC if specified
    vc_id = request.args.get('vc_id', type=int)
    from_date = request.args.get('from_date')
    to_date   = request.args.get('to_date')
    
    query = LedgerEntry.query.filter_by(person_id=person_id)

    if vc_id:
        query = query.filter(LedgerEntry.vc_id == vc_id)
    if from_date:
        query = query.filter(LedgerEntry.date >= datetime.strptime(from_date + ' 00:00:00', '%Y-%m-%d %H:%M:%S'))
    if to_date:
        query = query.filter(LedgerEntry.date <= datetime.strptime(to_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))

    entries = query.order_by(LedgerEntry.date.desc()).all()

    return render_template('ledger/person.html', person=person, entries=entries)

@ledger_bp.route('/create', methods=['GET', 'POST'])
@login_required
def create_ledger_entry():
    form = LedgerEntryForm()
    
    # Populate choices with current user's persons and VCs only
    form.person_id.choices = [(p.id, p.name) for p in Person.query.filter_by(user_id=current_user.id).all()]
    form.vc_id.choices = [(0, '--- None ---')] + [(vc.id, vc.name) for vc in VC.query.filter_by(user_id=current_user.id).all()]
    
    # Pre-fill person_id from query parameter if provided
    person_id = request.args.get('person_id', type=int)
    if person_id and not form.is_submitted():
        form.person_id.data = person_id
    
    if form.validate_on_submit():
        person = Person.query.filter_by(id=form.person_id.data, user_id=current_user.id).first()
        prev_balance = get_last_balance(form.person_id.data)
        
        entry = LedgerEntry(
            person_id=form.person_id.data,
            vc_id = form.vc_id.data if form.vc_id.data != 0 else None,
            date=form.date.data,
            narration=form.narration.data,
            debit=form.debit.data or 0,
            credit=form.credit.data or 0,
            balance=prev_balance + (form.credit.data or 0) - (form.debit.data or 0)
        )
        
        db.session.add(entry)
        db.session.commit()
        
        flash('Ledger entry created successfully!', 'success')
        return redirect(url_for('ledger.person_ledger', person_id=form.person_id.data))
    
    return render_template('ledger/create.html', form=form)


@ledger_bp.route('/<int:person_id>/close', methods=['POST'])
@login_required
def close_ledger_route(person_id):
    """Route to close a person's ledger"""
    person = Person.query.filter_by(id=person_id, user_id=current_user.id).first_or_404()
    if close_ledger(person_id):
        flash(f'Ledger closed successfully for person!', 'success')
    else:
        flash('Failed to close ledger. Person or entries not found.', 'danger')
    
    return redirect(url_for('ledger.person_ledger', person_id=person_id))


@ledger_bp.route('/<int:person_id>/pdf')
@login_required
def export_ledger_pdf(person_id):
    person = Person.query.filter_by(id=person_id, user_id=current_user.id).first_or_404()

    vc_id     = request.args.get('vc_id', type=int)
    from_date = request.args.get('from_date')
    to_date   = request.args.get('to_date')

    query = LedgerEntry.query.filter_by(person_id=person_id)

    if vc_id:
        query = query.filter(LedgerEntry.vc_id == vc_id)
    if from_date:
        query = query.filter(LedgerEntry.date >= datetime.strptime(from_date + ' 00:00:00', '%Y-%m-%d %H:%M:%S'))
    if to_date:
        query = query.filter(LedgerEntry.date <= datetime.strptime(to_date + ' 23:59:59', '%Y-%m-%d %H:%M:%S'))

    entries = query.order_by(LedgerEntry.date.desc()).all()

    rendered_html = render_template(
        'ledger/pdf_template.html',
        person=person,
        entries=entries
    )

    pdf_bytes = HTML(string=rendered_html).write_pdf()
    pdf_stream = BytesIO(pdf_bytes)
    pdf_stream.seek(0)

    return send_file(
        pdf_stream,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"ledger_{person.name.replace(' ', '_')}.pdf"
    )