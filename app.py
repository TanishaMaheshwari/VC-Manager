from flask import Flask, render_template, redirect, url_for, flash, request, send_file, jsonify
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, TextAreaField, SelectField, DateTimeField, SubmitField, IntegerField, DateField
from wtforms import SelectMultipleField
from wtforms.widgets import ListWidget, CheckboxInput
from wtforms.validators import DataRequired, Email, Optional, NumberRange, ValidationError
from datetime import datetime, timedelta, date
from enum import Enum
from fpdf import FPDF
from weasyprint import HTML
import os
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import validates
import io
from babel.numbers import format_decimal


app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///vc_committee.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Models
class PaymentStatus(Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    PAID = "paid"

class VC(db.Model):
    __tablename__ = 'vcs'
    
    id = db.Column(db.Integer, primary_key=True)
    vc_number = db.Column(db.Integer, unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    amount = db.Column(db.Float, nullable=False)
    tenure = db.Column(db.Integer, nullable=False)  # Number of months/hands
    current_hand = db.Column(db.Integer, default=1)  # Which hand is currently active
    narration = db.Column(db.Text)
    status = db.Column(db.Enum(PaymentStatus), default=PaymentStatus.PENDING)
    min_interest = db.Column(db.Float, nullable=False, default=0.0) # Added new column
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    hands = db.relationship('VCHand', backref='vc', lazy=True, cascade='all, delete-orphan')
    payments = db.relationship('Payment', backref='vc', lazy=True, cascade='all, delete-orphan')
    ledger_entries = db.relationship('LedgerEntry', backref='vc', lazy=True)

    members = db.relationship('Person', secondary='vc_members', backref='vcs')
    
    @property
    def total_paid(self):
        return sum(payment.amount for payment in self.payments)
    
    @property
    def due_count(self):
        # Count how many hands have due amounts
        return sum(1 for hand in self.hands if hand.due_amount > 0)
    
    @property
    def total_due(self):
        return sum(hand.due_amount for hand in self.hands)
    
    @property
    def completed_hands(self):
        """Return number of hands fully paid"""
        return sum(1 for hand in self.hands if hand.due_amount == 0)

    
    def create_hands(self):
        """Create hands for each month of tenure"""
        for month in range(1, self.tenure + 1):
            hand_date = self.start_date + timedelta(days=30 * (month - 1))
            hand = VCHand(
                vc_id=self.id,
                hand_number=month,
                date=hand_date,
                contribution_amount=self.amount / self.tenure,  # Equal distribution
                balance=self.amount,
                self_half_option='self'  # Default option
            )
            db.session.add(hand)

class VCHand(db.Model):
    __tablename__ = 'vc_hands'
    
    id = db.Column(db.Integer, primary_key=True)
    vc_id = db.Column(db.Integer, db.ForeignKey('vcs.id'), nullable=False)
    hand_number = db.Column(db.Integer, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    contribution_amount = db.Column(db.Float, nullable=False)
    balance = db.Column(db.Float, nullable=False)
    self_half_option = db.Column(db.String(10), default='self')  # 'self' or 'half'
    is_active = db.Column(db.Boolean, default=False)  # Current active hand
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    hand_distributions = db.relationship('HandDistribution', backref='vc_hand', lazy=True, cascade='all, delete-orphan')
    contributions = db.relationship('Contribution', backref='vc_hand', lazy=True, cascade='all, delete-orphan')
    
    @property
    def total_contributed(self):
        return sum(c.amount for c in self.contributions)

    @property
    def total_paid(self):
        """Sum of all payments/distributions made in this hand"""
        return sum(d.amount for d in self.hand_distributions)

    @property
    def due_amount(self):
        return max(self.contribution_amount * len(self.vc.members) - self.total_contributed, 0)

    @property
    def due_amount_for_month(self):
        return max(self.contribution_amount - self.total_paid, 0)

class HandDistribution(db.Model):
    __tablename__ = 'hand_distributions'
    
    id = db.Column(db.Integer, primary_key=True)
    hand_id = db.Column(db.Integer, db.ForeignKey('vc_hands.id'), nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    narration = db.Column(db.Text)
    payment_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_vc_money_taken = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    person = db.relationship('Person', backref='hand_distributions')


class Contribution(db.Model):
    __tablename__ = 'contributions'
    
    id = db.Column(db.Integer, primary_key=True)
    hand_id = db.Column(db.Integer, db.ForeignKey('vc_hands.id'), nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    person = db.relationship('Person', backref='contributions')

class Person(db.Model):
    __tablename__ = 'persons'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    short_name = db.Column(db.String(20), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    phone2 = db.Column(db.String(20), nullable=True)  # Made optional
    opening_balance = db.Column(db.Float, default=0.0, nullable=True)  # Made optional
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Person {self.name}>'

    @validates('phone', 'phone2')
    def validate_phone(self, key, value):
        if value and not value.isdigit():
            raise ValueError("Phone numbers must contain only digits.")
        return value
    
    # Relationships
    payments = db.relationship('Payment', backref='person', lazy=True)
    ledger_entries = db.relationship('LedgerEntry', backref='person', lazy=True)
    
    @property
    def total_balance(self):
        balance = self.opening_balance or 0.0
        for entry in self.ledger_entries:
            balance += (entry.credit or 0) - (entry.debit or 0)
        return balance

class Payment(db.Model):
    __tablename__ = 'payments'
    
    id = db.Column(db.Integer, primary_key=True)
    vc_id = db.Column(db.Integer, db.ForeignKey('vcs.id'), nullable=False)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    hand_id = db.Column(db.Integer, db.ForeignKey('vc_hands.id'), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    narration = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    hand = db.relationship('VCHand', backref='payments')

class LedgerEntry(db.Model):
    __tablename__ = 'ledger_entries'
    
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False)
    vc_id = db.Column(db.Integer, db.ForeignKey('vcs.id'), nullable=True)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    narration = db.Column(db.Text, nullable=False)
    debit = db.Column(db.Float, default=0)
    credit = db.Column(db.Float, default=0)
    balance = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Forms
vc_members = db.Table(
    'vc_members',
    db.Column('vc_id', db.Integer, db.ForeignKey('vcs.id'), primary_key=True),
    db.Column('person_id', db.Integer, db.ForeignKey('persons.id'), primary_key=True)
)

class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()

class VCForm(FlaskForm):
    # vc_number is now automated
    name = StringField("Name", validators=[DataRequired()])
    start_date = DateField("Start Date", format='%Y-%m-%d', validators=[DataRequired()], default=datetime.now())
    amount = FloatField("Amount", validators=[DataRequired(), NumberRange(min=1)])
    min_interest = FloatField("Minimum Interest", validators=[DataRequired(), NumberRange(min=0)])
    tenure = IntegerField("Tenure", validators=[DataRequired()])
    narration = TextAreaField("Narration")

    # ✅ Use the custom MultiCheckboxField
    members = MultiCheckboxField("Members", coerce=int)

    submit = SubmitField("Create")

    def validate_members(self, field):
        if not field.data or len(field.data) < 1:
            raise ValidationError("Please select at least one member")

class PersonForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    short_name = StringField('Short Name', validators=[DataRequired()])
    phone = StringField('Primary Phone Number', validators=[DataRequired()])
    phone2 = StringField('Secondary Phone Number', validators=[Optional()])
    opening_balance = FloatField('Opening Balance', validators=[Optional()])
    submit = SubmitField('Create')

class PaymentForm(FlaskForm):
    vc_id = SelectField('VC', validators=[DataRequired()], coerce=int)
    hand_id = SelectField('Hand', validators=[DataRequired()], coerce=int)
    person_id = SelectField('Person', validators=[DataRequired()], coerce=int)
    amount = FloatField('Amount', validators=[DataRequired(), NumberRange(min=0.01)])
    date = DateTimeField('Date', validators=[DataRequired()], default=datetime.utcnow)
    narration = TextAreaField('Narration')
    submit = SubmitField('Record Payment')

class LedgerEntryForm(FlaskForm):
    person_id = SelectField('Person', validators=[DataRequired()], coerce=int)
    vc_id = SelectField('VC (Optional)', validators=[Optional()], coerce=int)
    date = DateTimeField('Date', validators=[DataRequired()], default=datetime.now)
    narration = TextAreaField('Narration', validators=[DataRequired()])
    debit = FloatField('Debit', validators=[Optional(), NumberRange(min=0)], default=0)
    credit = FloatField('Credit', validators=[Optional(), NumberRange(min=0)], default=0)
    submit = SubmitField('Add Entry')

@app.cli.command()
def init_db():
    """Initialize the database with sample data."""
    db.create_all()
    
    # Create sample persons
    if Person.query.count() == 0:
        sample_persons = [
            Person(name='Narendra Singh', short_name='NS', phone='9876543210', opening_balance=500.0),
            Person(name='Hari Mohan', short_name='HM', phone='9876543211'),
            Person(name='Dinesh Kumar', short_name='DK', phone='9876543212', phone2='8765432109'),
            Person(name='Dharmendra Bhardwaj', short_name='DMB', phone='9876543213'),
            Person(name='Suresh Lal Tiwari', short_name='SLT', phone='9876543214', opening_balance=1000.0),
            Person(name='Bhagwan Singh', short_name='BG', phone='9876543215'),
            Person(name='Ashok Kumar', short_name='A', phone='9876543216'),
            Person(name='Anil Mishra', short_name='AM', phone='9876543217'),
            Person(name='Krishan Kumar', short_name='KK', phone='9876543218'),
            Person(name='Suresh Agarwal', short_name='SAL', phone='9876543219'),
        ]
        for person in sample_persons:
            db.session.add(person)
        
        db.session.commit()
        print('Sample persons created.')
    
    print('Database initialized!')


@app.template_filter('indian_comma')
def indian_comma(value):
    return format_decimal(value, locale='en_IN')

# Routes
@app.route('/')
def dashboard():
    vcs = VC.query.order_by(VC.vc_number).all()
    total_due = sum(vc.total_due for vc in vcs)
    total_vcs = len(vcs)
    persons = Person.query.all()
    total_persons = len(persons)
    return render_template('dashboard.html', today=date.today(), total_due=total_due, total_vcs=total_vcs, vcs=vcs, persons=persons, total_persons=total_persons)

@app.route('/vcs')
def vcs_list():
    vcs = VC.query.order_by(VC.vc_number).all()
    total_due = sum(vc.amount * vc.due_count for vc in vcs)
    total_vcs = len(vcs)
    total_members = sum(len(vc.members) for vc in vcs)
    return render_template('vc/list.html', vcs=vcs, total_due=total_due, total_members=total_members, total_vcs=total_vcs)

@app.route('/vc/create', methods=['GET', 'POST'])
def create_vc():
    form = VCForm()
    form.members.choices = [(p.id, p.name) for p in Person.query.all()]
    
    # Determine next VC number for display
    last_vc = VC.query.order_by(VC.vc_number.desc()).first()
    next_vc_number = (last_vc.vc_number + 1) if last_vc else 1

    if form.validate_on_submit():
        vc = VC(
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
        return redirect(url_for('vcs_list'))
    
    return render_template('vc/create.html', form=form, vc_number=next_vc_number)


@app.route('/vc/<int:id>')
def view_vc(id):
    vc = VC.query.get_or_404(id)
    hands = VCHand.query.filter_by(vc_id=id).order_by(VCHand.hand_number).all()
    return render_template('vc/view.html', vc=vc, hands=hands)



@app.route("/vc/<int:vc_id>/hand/<int:hand_number>")
# @login_required
def view_hand_distribution(vc_id, hand_number):
    vc = VC.query.get_or_404(vc_id)
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
        vc_payouts=[] # Placeholder for template
    )

@app.route("/vc/<int:vc_id>/distribute-hand", methods=['POST'])
def distribute_hand(vc_id):
    hand_id = request.form.get('hand_id')
    person_id = request.form.get('person_id')
    bid_price = request.form.get('bid_price', type=float)

    if not person_id or not bid_price:
        return jsonify(success=False, message="Winner and bid price are required."), 400

    hand = VCHand.query.get_or_404(hand_id)
    vc = hand.vc
    winner = Person.query.get_or_404(person_id)

    # Check if already distributed
    if HandDistribution.query.filter_by(hand_id=hand.id).first():
        return jsonify(success=False, message="This hand has already been distributed."), 400

    # Ensure winner hasn't won before
    winning_member_ids = {
        d.person_id
        for h in vc.hands
        for d in h.hand_distributions
    }
    if winner.id in winning_member_ids:
        return jsonify(success=False, message="This member has already won a previous hand and is ineligible."), 400

    try:
        # 1. Record payout for winner
        payout = HandDistribution(
            hand_id=hand.id,
            person_id=winner.id,
            amount=bid_price,
            narration=f"Payout for Hand {hand.hand_number}",
            is_vc_money_taken=True
        )
        db.session.add(payout)

        # 2. Contributions for all members
        members = vc.members  # assuming relationship VC -> Person
        per_person_contribution = bid_price / len(members)

        for member in members:
            contribution = Contribution(
                hand_id=hand.id,
                person_id=member.id,
                amount=per_person_contribution,
                payment_date=datetime.utcnow()
            )
            db.session.add(contribution)

            # Ledger entry (debit)
            ledger_entry = LedgerEntry(
                person_id=member.id,
                vc_id=vc.id,
                date=datetime.utcnow(),
                narration=f"Contribution for VC {vc.vc_number}, Hand {hand.hand_number}",
                debit=per_person_contribution,
                balance=member.total_balance - per_person_contribution
            )
            db.session.add(ledger_entry)

        # 3. Ledger entry for payout (credit)
        ledger_entry = LedgerEntry(
            person_id=winner.id,
            vc_id=vc.id,
            date=datetime.utcnow(),
            narration=f"Payout received for VC {vc.vc_number}, Hand {hand.hand_number}",
            credit=bid_price,
            balance=winner.total_balance + bid_price
        )
        db.session.add(ledger_entry)

        # 4. Advance VC hand
        if vc.current_hand == hand.hand_number:
            vc.current_hand += 1

        db.session.commit()
        return jsonify(success=True, message=f"Hand {hand.hand_number} distributed successfully with bid price {bid_price}!")

    except Exception as e:
        db.session.rollback()
        return jsonify(success=False, message=str(e)), 500

@app.route('/vc/delete/<int:id>')
def delete_vc(id):
    vc = VC.query.get_or_404(id)
    db.session.delete(vc)
    db.session.commit()
    flash(f'VC {vc.vc_number} deleted successfully!', 'success')
    return redirect(url_for('vcs_list'))


@app.route('/persons')
def persons():
    persons = Person.query.all()
    return render_template('person/list.html', persons=persons)

# New route to handle live search requests
@app.route('/search_persons')
def search_persons():
    query = request.args.get('q', '')
    sort_order = request.args.get('sort', 'name_asc')

    base_query = db.session.query(Person)

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

@app.route('/person/create', methods=['GET', 'POST'])
def create_person():
    form = PersonForm()
    if form.validate_on_submit():
        person = Person(
            name=form.name.data,
            short_name=form.short_name.data,
            phone=form.phone.data,
            phone2=form.phone2.data
        )
        db.session.add(person)
        try:
            db.session.commit()
            flash('Person created successfully!', 'success')
            return redirect(url_for('persons'))
        except IntegrityError:
            db.session.rollback()
            flash('Error: A person with that name already exists. Please use a different name.', 'danger')
            return redirect(url_for('create_person'))
        except Exception as e:
            db.session.rollback()
            flash(f'An unexpected error occurred: {str(e)}', 'danger')
            return redirect(url_for('create_person'))

    # If form validation fails, display a generic error message
    if not form.validate_on_submit() and form.is_submitted():
        flash('Please fill out all required fields correctly.', 'danger')

    return render_template('person/create.html', form=form)

@app.route('/person/<int:id>/edit', methods=['GET', 'POST'])
def edit_person(id):
    person = Person.query.get_or_404(id)
    form = PersonForm(obj=person)
    
    if form.validate_on_submit():
        person.name = form.name.data
        person.short_name = form.short_name.data
        person.phone = form.phone.data
        person.phone2 = form.phone2.data
        
        try:
            db.session.commit()
            flash('Person updated successfully!', 'success')
            return redirect(url_for('persons'))
        except IntegrityError:
            db.session.rollback()
            flash('Error: A person with that name or short name already exists. Please use a different name.', 'danger')
            return redirect(url_for('edit_person', id=id))
        except Exception as e:
            db.session.rollback()
            flash(f'An unexpected error occurred: {str(e)}', 'danger')
            return redirect(url_for('edit_person', id=id))
            
    return render_template('person/edit.html', form=form, person=person)

@app.route('/ledger/<int:person_id>')
def person_ledger(person_id):
    person = Person.query.get_or_404(person_id)
    entries = LedgerEntry.query.filter_by(person_id=person_id).order_by(LedgerEntry.date.desc()).all()
    
    # Filter by VC if specified
    vc_id = request.args.get('vc_id', type=int)
    if vc_id:
        entries = [e for e in entries if e.vc_id == vc_id]
    
    return render_template('ledger/person.html', person=person, entries=entries)

@app.route('/payment/create', methods=['GET', 'POST'])
def create_payment():
    form = PaymentForm()
    
    # Populate choices
    form.vc_id.choices = [(vc.id, f"VC {vc.vc_number} - {vc.name}") for vc in VC.query.all()]
    form.hand_id.choices = [(h.id, f"Hand {h.hand_number}") for h in VCHand.query.all()]
    form.person_id.choices = [(p.id, p.name) for p in Person.query.all()]
    
    if form.validate_on_submit():
        # 1. Record contribution (payment IN)
        contribution = Contribution(
            hand_id=form.hand_id.data,
            person_id=form.person_id.data,
            amount=form.amount.data,
            date=datetime.utcnow()
        )
        db.session.add(contribution)

        # 2. Ledger entry for contribution (debit)
        person = Person.query.get(form.person_id.data)
        vc = VC.query.get(form.vc_id.data)
        ledger_entry = LedgerEntry(
            person_id=form.person_id.data,
            vc_id=form.vc_id.data,
            date=datetime.utcnow(),
            narration=f"Contribution for VC {vc.vc_number}: {form.narration.data}",
            debit=form.amount.data
        )
        prev_balance = person.total_balance
        ledger_entry.balance = prev_balance - form.amount.data
        db.session.add(ledger_entry)

        db.session.commit()
        flash('Contribution recorded successfully!', 'success')
        return redirect(url_for('vcs_list'))

    return render_template('payment/create.html', form=form)

@app.route('/payout/create/<int:hand_id>/<int:person_id>', methods=['POST'])
def create_payout(hand_id, person_id):
    hand = VCHand.query.get_or_404(hand_id)
    person = Person.query.get_or_404(person_id)

    total_pool = hand.total_contributed
    if not total_pool:
        flash("No contributions yet to distribute.", "danger")
        return redirect(url_for('view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))

    # Create payout
    payout = HandDistribution(
        hand_id=hand.id,
        person_id=person.id,
        amount=total_pool,
        narration=f"Payout for Hand {hand.hand_number}",
        payment_date=datetime.utcnow(),
        is_vc_money_taken=True
    )
    db.session.add(payout)

    # Ledger entry (credit)
    ledger_entry = LedgerEntry(
        person_id=person.id,
        vc_id=hand.vc_id,
        date=datetime.utcnow(),
        narration=f"Payout received for VC {hand.vc.vc_number}, Hand {hand.hand_number}",
        credit=total_pool,
        balance=person.total_balance + total_pool
    )
    db.session.add(ledger_entry)

    db.session.commit()
    flash(f"Payout of {total_pool} given to {person.name}", "success")
    return redirect(url_for('view_hand_distribution', vc_id=hand.vc_id, hand_number=hand.hand_number))


@app.route('/ledger/create', methods=['GET', 'POST'])
def create_ledger_entry():
    form = LedgerEntryForm()
    
    # Populate choices
    form.person_id.choices = [(p.id, p.name) for p in Person.query.all()]
    form.vc_id.choices = [('', 'Select VC (Optional)')] + [(vc.id, f"VC {vc.vc_number}") for vc in VC.query.all()]
    
    if form.validate_on_submit():
        person = Person.query.get(form.person_id.data)
        prev_balance = person.total_balance
        
        entry = LedgerEntry(
            person_id=form.person_id.data,
            vc_id=form.vc_id.data if form.vc_id.data else None,
            date=form.date.data,
            narration=form.narration.data,
            debit=form.debit.data or 0,
            credit=form.credit.data or 0,
            balance=prev_balance + (form.credit.data or 0) - (form.debit.data or 0)
        )
        
        db.session.add(entry)
        db.session.commit()
        
        flash('Ledger entry created successfully!', 'success')
        return redirect(url_for('person_ledger', person_id=form.person_id.data))
    
    return render_template('ledger/create.html', form=form)


def generate_pdf_for_person(person_id):
    """
    Generates a PDF ledger for a given person ID and returns it as a
    file-like object in memory.
    """
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    
    # Add content to the PDF
    pdf.cell(200, 10, txt=f"Ledger Report for Person ID: {person_id}", ln=True, align="C")
    pdf.cell(200, 10, txt="This is a sample ledger content.", ln=True)
    pdf_bytes = pdf.output(dest='S').encode('latin-1')
    
    # Save the PDF content to a BytesIO object in memory
    pdf_output = io.BytesIO(pdf_bytes)
    pdf_output.seek(0)  # Go back to the start of the stream
    
    return pdf_output

# This is the route that your button's JavaScript function will call.
# The <int:personId> part captures the ID from the URL.
@app.route('/ledger/<int:person_id>/pdf')
def export_ledger_pdf(person_id):
    person = Person.query.get_or_404(person_id)
    entries = LedgerEntry.query.filter_by(person_id=person_id).order_by(LedgerEntry.date.desc()).all()
    
    # Filter by VC if specified
    vc_id = request.args.get('vc_id', type=int)
    if vc_id:
        entries = [e for e in entries if e.vc_id == vc_id]
        
    # 1. Render the dedicated HTML template for the PDF
    rendered_html = render_template(
        'ledger/pdf_template.html', 
        person=person, 
        entries=entries
    )
    
    # 2. Convert the rendered HTML to a PDF using WeasyPrint
    pdf_bytes = HTML(string=rendered_html).write_pdf()
    
    # 3. Use BytesIO to create an in-memory file for sending
    pdf_stream = io.BytesIO(pdf_bytes)
    pdf_stream.seek(0)
    
    # 4. Return the PDF file as a download
    return send_file(
        pdf_stream,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"ledger_{person.name.replace(' ', '_')}.pdf"
    )

if __name__ == "__main__":
    with app.app_context():
        # This is the crucial line that creates the database file and all tables.
        db.create_all()
        print("Database 'vc_committee.db' and tables created successfully.")
    port = int(os.environ.get("PORT", 5000))  # Render gives PORT dynamically
    app.run(host="0.0.0.0", port=port)
