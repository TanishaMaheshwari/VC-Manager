from flask import Flask, render_template, redirect, url_for, flash, request, send_file, jsonify, session
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
from sqlalchemy import or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import validates
import io
from babel.numbers import format_decimal


app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'  # Make sure this is set for session
# --- Authentication Config ---
LOGIN_ID = 'VCManager001'
LOGIN_PASSWORD = '123vc'

# Routes
from functools import wraps

# --- Authentication Decorator ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

# --- Login Route ---
@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        user_id = request.form.get('user_id')
        password = request.form.get('password')
        if user_id == LOGIN_ID and password == LOGIN_PASSWORD:
            session['logged_in'] = True
            flash('Logged in successfully!', 'success')
            next_url = request.args.get('next')
            return redirect(next_url or url_for('dashboard'))
        else:
            error = 'Invalid credentials. Please try again.'
    return render_template('login.html', error=error)

# --- Logout Route ---
@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('login'))
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
        """
        Total due across all declared hands in this VC.
        Declared hand = has at least one HandDistribution.
        Formula = per-person contribution * (members - 1).
        """
        total = 0
        member_count = len(self.members)
        if member_count == 0:
            return 0

        for hand in self.hands:
            if hand.hand_distributions:  # hand has been declared
                per_person = hand.actual_contribution_per_person
                total += per_person * (member_count - 1)
        return total

    
    @property
    def current_hand_obj(self):
        """Return the current active hand object, or the last hand if current_hand > tenure."""
        if self.current_hand <= self.tenure:
            return self.current_hand
        else:
            return self.tenure
    
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
    
    @property
    def winner_short_name(self):
        """
        Returns a comma-separated string of the short names of all winners for this hand.
        Returns None if the hand has not been distributed.
        """
        # Use .all() to get all distribution records for this hand
        distributions = HandDistribution.query.filter_by(hand_id=self.id).all()
        
        if distributions:
            # Get the short name for each winner and join them into a string
            winner_names = [d.person.short_name for d in distributions]
            return ", ".join(winner_names)
            
        return None
    
    @property
    def projected_payout(self):
        """
        Calculates the projected payout amount for this hand based on the minimum interest.
        This is used for hands that have not been distributed yet.
        """
        # The number of steps from the last hand, where the last hand has a step of 1
        steps_from_end = self.vc.tenure - self.hand_number + 1
        
        # Linear deduction based on min_interest as a percentage of the total amount
        # Example: if amount is 100, min_interest is 2%, the step is 2.
        deduction_amount = self.vc.amount * (self.vc.min_interest/100)
        payout_amount = self.vc.amount - (steps_from_end * deduction_amount)
        
        return payout_amount
    
    @property
    def actual_contribution_per_person(self):
        """
        Returns the actual per-person contribution for this hand based on the bid price,
        or the projected amount if the hand is not yet distributed.
        """
        if self.hand_distributions:
            # The bid price is the amount from the first (and only) hand distribution record
            payout = self.hand_distributions[0]
            if len(self.vc.members) > 0:
                return payout.amount / len(self.vc.members)
        
        # Fallback to the projected contribution based on the new logic
        if len(self.vc.members) > 0:
            return self.projected_payout / len(self.vc.members)
        
        return 0
    
    @property
    def interest_rate(self):
        """
        Calculates the interest rate based on the bid price for distributed hands,
        or the projected payout for pending hands.
        """
        if self.hand_distributions:
            payout_amount = self.hand_distributions[0].amount
        else:
            payout_amount = self.projected_payout
            
        total_vc_amount = self.vc.amount
        
        if total_vc_amount > 0:
            interest_amount = total_vc_amount - payout_amount
            # The interest rate is the percentage of the original pool
            return (interest_amount / total_vc_amount) * 100
            
        return 0
    
    @property
    def interest_amount(self):
        """
        Calculates the total interest amount (money saved) for this hand,
        based on the projected payout.
        """
        return self.vc.amount - self.projected_payout

    def amount_due_for(self, person_id):
        # winner(s) pay nothing
        winner_ids = [d.person_id for d in self.hand_distributions]
        if person_id in winner_ids:
            return 0

        expected = self.actual_contribution_per_person
        paid = sum(c.amount for c in self.contributions if c.person_id == person_id)
        return max(expected - paid, 0)


    
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

    def validate_tenure(self, field):
        if len(self.members.data) != field.data:
            raise ValidationError("Tenure (number of hands) must be equal to the number of members.")

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
@app.route('/', methods=['GET', 'POST'])
@login_required
def dashboard():
    vcs = VC.query.order_by(VC.vc_number).all()
    total_due = sum(vc.total_due for vc in vcs)
    total_vcs = len(vcs)
    persons = Person.query.all()
    total_persons = len(persons)
    form = PaymentForm()

    # --- 1. Show only VCs with pending payments ---
    pending_vcs = []
    for vc in VC.query.all():
        for hand in vc.hands:
            # contribution is expected from all except the winner(s)
            expected_ids = {m.id for m in vc.members} - {d.person_id for d in hand.hand_distributions}
            paid_ids = {c.person_id for c in hand.contributions}
            pending_ids = expected_ids - paid_ids
            if pending_ids:
                pending_vcs.append(vc)
                break

    form.vc_id.choices = [(vc.id, f"VC {vc.vc_number} - {vc.name}") for vc in pending_vcs]

    # --- 2. Populate Hand dropdown (empty until VC selected) ---
    form.hand_id.choices = []
    if form.vc_id.data:
        vc = db.session.get(VC, form.vc_id.data)
        form.hand_id.choices = [
            (h.id, f"Hand {h.hand_number} - Winner: {h.hand_distributions[0].person.short_name if h.hand_distributions else 'TBD'}")
            for h in vc.hands
        ]

    # --- 3. Populate Person dropdown (only pending members) ---
    if form.hand_id.data:
        hand = db.session.get(VCHand, form.hand_id.data)
        if hand:
            expected_ids = {m.id for m in hand.vc.members}  # all VC members
            distributed_ids = {d.person_id for d in hand.hand_distributions}  # winners
            potential_ids = expected_ids - distributed_ids
            # Ledger entries already recorded for this VC & Hand
            ledger_entries = LedgerEntry.query.filter(
                LedgerEntry.vc_id == hand.vc.id,
                LedgerEntry.narration.like(f"Payment for VC {hand.vc.vc_number}, Hand {hand.hand_number}%")
            ).all()
            paid_ids = {l.person_id for l in ledger_entries}
            pending_ids = potential_ids - paid_ids
            pending_persons = Person.query.filter(Person.id.in_(pending_ids)).all()
            form.person_id.choices = [(p.id, p.name) for p in pending_persons]

    if form.validate_on_submit():
        # --- 4. Record contribution (payment IN) ---
        contribution = Contribution(
            hand_id=form.hand_id.data,
            person_id=form.person_id.data,
            amount=form.amount.data,
            date=datetime.utcnow()
        )
        db.session.add(contribution)

        # --- 5. Ledger entry (debit) ---
        person = Person.query.get(form.person_id.data)
        vc = VC.query.get(form.vc_id.data)
        ledger_entry = LedgerEntry(
            person_id=form.person_id.data,
            vc_id=form.vc_id.data,
            date=form.date.data or datetime.utcnow(),
            narration=f"Payment for VC {vc.vc_number}, Hand {hand.hand_number}: {form.narration.data}",
            credit=form.amount.data,
            balance=person.total_balance - form.amount.data
        )
        db.session.add(ledger_entry)

        db.session.commit()
        flash('Contribution recorded successfully!', 'success')
        return redirect(url_for('vcs_list'))
    
    return render_template('dashboard.html', form=form, today=date.today(), total_due=total_due, total_vcs=total_vcs, vcs=vcs, persons=persons, total_persons=total_persons)

@app.route('/vcs')
@login_required
def vcs_list():
    vcs = VC.query.order_by(VC.vc_number).all()
    total_due = sum(vc.total_due for vc in vcs)
    total_vcs = len(vcs)
    total_members = sum(len(vc.members) for vc in vcs)
    return render_template('vc/list.html', vcs=vcs, total_due=total_due, total_members=total_members, total_vcs=total_vcs)

@app.route('/vc/create', methods=['GET', 'POST'])
@login_required
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
@login_required
def view_vc(id):
    vc = VC.query.get_or_404(id)
    hands = VCHand.query.filter_by(vc_id=id).order_by(VCHand.hand_number).all()
    return render_template('vc/view.html', vc=vc, hands=hands)



@app.route("/vc/<int:vc_id>/hand/<int:hand_number>")
@login_required
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

import traceback

@app.route("/vc/<int:vc_id>/distribute-hand", methods=['POST'])
@login_required
def distribute_hand(vc_id):
    try:
        print("--- Starting distribute_hand function ---")

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
            return redirect(url_for('view_hand_distribution', vc_id=vc_id, hand_number=VCHand.query.get(hand_id).hand_number))

        hand = VCHand.query.get_or_404(hand_id)
        vc = hand.vc

        required_earned_interest = vc.amount - hand.projected_payout
        earned_interest_from_bid = vc.amount - bid_price
        
        if earned_interest_from_bid < required_earned_interest:
            flash(f"The bid price must be ₹{hand.projected_payout:.0f} or less to cover the minimum interest of ₹{required_earned_interest:.0f}.", 'danger')
            return redirect(url_for('view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))

        winner_objs = Person.query.filter(Person.id.in_(winners)).all()

        # Check if already distributed
        if HandDistribution.query.filter_by(hand_id=hand.id).first():
            print("Hand already distributed.")
            flash("Error: This hand has already been distributed.", 'danger')
            return redirect(url_for('view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))

        # Check if winner has already won
        winning_member_ids = {d.person_id for h in vc.hands for d in h.hand_distributions}
        for w in winner_objs:
            if w.id in winning_member_ids:
                print(f"Winner {w.name} already won a previous hand.")
                flash(f"Error: {w.name} has already won a previous hand and is ineligible.", 'danger')
                return redirect(url_for('view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))

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
            current_balance = person_balances.get(winner.id, 0)
            new_balance = current_balance + payout_per_winner
            ledger_entry = LedgerEntry(
                person_id=winner.id,
                vc_id=vc.id,
                date=datetime.utcnow(),
                narration=f"Payout received for VC {vc.vc_number}, Hand {hand.hand_number}. ({narration or 'No comment'})",
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
        for member in members:
            print(f"Processing member: {member.name}")
            # Contribution record
            contribution = Contribution(
                hand_id=hand.id,
                person_id=member.id,
                amount=per_person_contribution,
                date=datetime.utcnow()
            )
            db.session.add(contribution)
            print("Added Contribution to session.")

            # Ledger entry (debit)
            current_balance = person_balances.get(member.id, 0)
            new_balance = current_balance - per_person_contribution
            ledger_entry = LedgerEntry(
                person_id=member.id,
                vc_id=vc.id,
                date=datetime.utcnow(),
                narration=f"Contribution for VC {vc.vc_number}, Hand {hand.hand_number}.",
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
        print("db.session.commit() successful. Ending function.")
        flash(f"Hand {hand.hand_number} distributed successfully with bid price ₹{bid_price}!", 'success')
        return redirect(url_for('view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))

    except Exception as e:
        db.session.rollback()
        print("An exception occurred! Rolling back changes.")
        traceback.print_exc()
        flash(f"An error occurred during distribution: {str(e)}. Changes have been rolled back.", 'danger')
        return redirect(url_for('view_hand_distribution', vc_id=vc.id, hand_number=hand.hand_number))
@app.route('/vc/delete/<int:id>')
@login_required
def delete_vc(id):
    vc = VC.query.get_or_404(id)
    db.session.delete(vc)
    db.session.commit()
    flash(f'VC {vc.vc_number} deleted successfully!', 'success')
    return redirect(url_for('vcs_list'))


@app.route('/persons')
@login_required
def persons():
    persons = Person.query.all()
    return render_template('person/list.html', persons=persons)

# New route to handle live search requests
@app.route('/search_persons')
@login_required
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
@login_required
def create_person():
    form = PersonForm()
    if form.validate_on_submit():
        # Create person object
        person = Person(
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
            return redirect(url_for('persons'))
            
        except IntegrityError:
            db.session.rollback()
            flash('Error: A person with that name or short name already exists. Please use a different value.', 'danger')
            return redirect(url_for('create_person'))
        except Exception as e:
            db.session.rollback()
            flash(f'An unexpected database error occurred: {str(e)}', 'danger')
            return redirect(url_for('create_person'))

    # If form validation fails, display specific errors
    if not form.validate_on_submit() and form.is_submitted():
        flash('Please fill out all required fields correctly.', 'danger')
        
    return render_template('person/create.html', form=form)


@app.route('/person/<int:id>/edit', methods=['GET', 'POST'])
@login_required
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
@login_required
def person_ledger(person_id):
    person = Person.query.get_or_404(person_id)
    entries = LedgerEntry.query.filter_by(person_id=person_id).order_by(LedgerEntry.date.desc()).all()
    
    # Filter by VC if specified
    vc_id = request.args.get('vc_id', type=int)
    if vc_id:
        entries = [e for e in entries if e.vc_id == vc_id]
    
    return render_template('ledger/person.html', person=person, entries=entries)

@app.route("/record-payment", methods=["GET", "POST"])
@login_required
def record_payment():
    form = PaymentForm()

    # 1. VC dropdown: only show VCs with pending payments
    pending_vcs = VC.query.filter(VC.status != PaymentStatus.PAID).all()
    form.vc_id.choices = [(vc.id, f"VC {vc.vc_number}") for vc in pending_vcs]

    # 2. Dynamic hand & person choices handled via JS, pass all hands & members for simplicity
    all_hands = {hand.id: hand for vc in pending_vcs for hand in vc.hands}
    all_members = {member.id: member for vc in pending_vcs for member in vc.members}

    if form.validate_on_submit():
        vc = VC.query.get(form.vc_id.data)
        hand = VCHand.query.get(form.hand_id.data)
        person = Person.query.get(form.person_id.data)

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
        current_balance = person.total_balance
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
        db.session.commit()

        flash(f"Payment of ₹{payment.amount} recorded for {person.name}", "success")
        return redirect(url_for("record_payment"))

    return render_template("payment/create.html", form=form, pending_vcs=pending_vcs, all_hands=all_hands, all_members=all_members)

# @app.route('/payment/create', methods=['GET', 'POST'])
# def create_payment():
#     form = PaymentForm()
    
#     # Populate choices
#     form.vc_id.choices = [(vc.id, f"VC {vc.vc_number} - {vc.name}") for vc in VC.query.all()]
#     form.hand_id.choices = [(h.id, f"Hand {h.hand_number}") for h in VCHand.query.all()]
#     form.person_id.choices = [(p.id, p.name) for p in Person.query.all()]
    
#     if form.validate_on_submit():
#         # 1. Record contribution (payment IN)
#         contribution = Contribution(
#             hand_id=form.hand_id.data,
#             person_id=form.person_id.data,
#             amount=form.amount.data,
#             date=datetime.utcnow()
#         )
#         db.session.add(contribution)

#         # 2. Ledger entry for contribution (debit)
#         person = Person.query.get(form.person_id.data)
#         vc = VC.query.get(form.vc_id.data)
#         ledger_entry = LedgerEntry(
#             person_id=form.person_id.data,
#             vc_id=form.vc_id.data,
#             date=datetime.utcnow(),
#             narration=f"Contribution for VC {vc.vc_number}: {form.narration.data}",
#             debit=form.amount.data
#         )
#         prev_balance = person.total_balance
#         ledger_entry.balance = prev_balance - form.amount.data
#         db.session.add(ledger_entry)

#         db.session.commit()
#         flash('Contribution recorded successfully!', 'success')
#         return redirect(url_for('vcs_list'))

#     return render_template('payment/create.html', form=form)

@app.route("/api/vc/<int:vc_id>/details")
@login_required
def vc_details(vc_id):
    vc = VC.query.get_or_404(vc_id)
    
    hands = []
    for h in vc.hands:
        # Only include hands with a winner
        winner_name = h.winner_short_name or None
        if not winner_name:
            continue

        hands.append({
            "id": h.id,
            "hand_number": h.hand_number,
            "winner_name": winner_name,
            "date": h.date.isoformat()  # send ISO string for JS
        })

    return jsonify({
        "hands": hands
    })

@app.route("/api/hand/<int:hand_id>/details")
@login_required
def hand_details(hand_id):
    hand = db.session.get(VCHand, hand_id)
    if not hand:
        return jsonify({"error": "Hand not found"}), 404

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

    pending_persons = Person.query.filter(Person.id.in_(pending_ids)).all()

    contribution_amount = hand.actual_contribution_per_person  # or compute dynamically

    return jsonify({
        "pending_persons": [{"id": p.id, "name": p.name} for p in pending_persons],
        "contribution_amount": contribution_amount
    })


@app.route('/payment/create', methods=['GET', 'POST'])
@login_required
def create_payment():
    form = PaymentForm()

    # --- 1. Show only VCs with pending payments ---
    pending_vcs = []
    for vc in VC.query.all():
        for hand in vc.hands:
            # contribution is expected from all except the winner(s)
            expected_ids = {m.id for m in vc.members} - {d.person_id for d in hand.hand_distributions}
            paid_ids = {c.person_id for c in hand.contributions}
            pending_ids = expected_ids - paid_ids
            if pending_ids:
                pending_vcs.append(vc)
                break

    form.vc_id.choices = [(vc.id, f"VC {vc.vc_number} - {vc.name}") for vc in pending_vcs]

    # --- 2. Populate Hand dropdown (empty until VC selected) ---
    form.hand_id.choices = []
    if form.vc_id.data:
        vc = db.session.get(VC, form.vc_id.data)
        form.hand_id.choices = [
            (h.id, f"Hand {h.hand_number} - Winner: {h.hand_distributions[0].person.short_name if h.hand_distributions else 'TBD'}")
            for h in vc.hands
        ]

    # --- 3. Populate Person dropdown (only pending members) ---
    if form.hand_id.data:
        hand = db.session.get(VCHand, form.hand_id.data)
        if hand:
            expected_ids = {m.id for m in hand.vc.members}  # all VC members
            distributed_ids = {d.person_id for d in hand.hand_distributions}  # winners
            potential_ids = expected_ids - distributed_ids
            # Ledger entries already recorded for this VC & Hand
            ledger_entries = LedgerEntry.query.filter(
                LedgerEntry.vc_id == hand.vc.id,
                LedgerEntry.narration.like(f"Payment for VC {hand.vc.vc_number}, Hand {hand.hand_number}%")
            ).all()
            paid_ids = {l.person_id for l in ledger_entries}
            pending_ids = potential_ids - paid_ids
            pending_persons = Person.query.filter(Person.id.in_(pending_ids)).all()
            form.person_id.choices = [(p.id, p.name) for p in pending_persons]

    if form.validate_on_submit():
        # --- 4. Record contribution (payment IN) ---
        contribution = Contribution(
            hand_id=form.hand_id.data,
            person_id=form.person_id.data,
            amount=form.amount.data,
            date=datetime.utcnow()
        )
        db.session.add(contribution)

        # --- 5. Ledger entry (debit) ---
        person = Person.query.get(form.person_id.data)
        vc = VC.query.get(form.vc_id.data)
        ledger_entry = LedgerEntry(
            person_id=form.person_id.data,
            vc_id=form.vc_id.data,
            date=form.date.data or datetime.utcnow(),
            narration=f"Payment for VC {vc.vc_number}, Hand {hand.hand_number}: {form.narration.data}",
            credit=form.amount.data,
            balance=person.total_balance - form.amount.data
        )
        db.session.add(ledger_entry)

        db.session.commit()
        flash('Contribution recorded successfully!', 'success')
        return redirect(url_for('vcs_list'))

    return render_template('payment/create.html', form=form, datetime=datetime)

@app.route('/payout/create/<int:hand_id>/<int:person_id>', methods=['POST'])
@login_required
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

@app.route("/vc/<int:vc_id>/hand/<int:hand_id>/edit-payout", methods=["POST"])
@login_required
def edit_payout(vc_id, hand_id):
    payout_id = request.form["payout_id"]
    person_id = int(request.form["person_id"])
    amount = float(request.form["amount"])

    payout = HandDistribution.query.get_or_404(payout_id)
    hand = VCHand.query.get_or_404(hand_id)
    vc = hand.vc

    # Calculate minimum allowed bid price based on min_interest
    required_earned_interest = vc.amount - hand.projected_payout
    earned_interest_from_bid = vc.amount - amount

    if earned_interest_from_bid < required_earned_interest:
        flash(
            f"The bid price must be ₹{hand.projected_payout:.0f} or less to cover the minimum interest of ₹{required_earned_interest:.0f}.",
            "danger"
        )
        return redirect(url_for("view_hand_distribution", vc_id=vc_id, hand_number=hand.hand_number))

    # Update payout
    payout.person_id = person_id
    payout.amount = amount

    # Update contributions for all members (contr per person = new bid price / member count)
    members = vc.members
    per_person_contribution = amount / len(members)
    # Remove old contributions for this hand
    Contribution.query.filter_by(hand_id=hand.id).delete()
    # Add new contributions
    for member in members:
        contribution = Contribution(
            hand_id=hand.id,
            person_id=member.id,
            amount=per_person_contribution,
            date=datetime.utcnow()
        )
        db.session.add(contribution)

    db.session.commit()

    flash("Payout updated successfully", "success")
    return redirect(url_for("view_hand_distribution", vc_id=vc_id, hand_number=hand.hand_number))


@app.route('/ledger/create', methods=['GET', 'POST'])
@login_required
def create_ledger_entry():
    form = LedgerEntryForm()
    
    # Populate choices
    form.person_id.choices = [(p.id, p.name) for p in Person.query.all()]
    form.vc_id.choices = [(0, '--- None ---')] + [(vc.id, vc.name) for vc in VC.query.all()]
    
    if form.validate_on_submit():
        person = Person.query.get(form.person_id.data)
        prev_balance = person.total_balance
        
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
@login_required
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
