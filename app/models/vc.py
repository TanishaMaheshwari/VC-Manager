"""VC and VCHand models for VC-Manager"""
from datetime import datetime, timedelta, timezone
from app import db
from app.models.enums import PaymentStatus

# Association table for VC members
vc_members = db.Table(
    'vc_members',
    db.Column('vc_id', db.Integer, db.ForeignKey('vcs.id'), primary_key=True, index=True),
    db.Column('person_id', db.Integer, db.ForeignKey('persons.id'), primary_key=True, index=True)
)

class VC(db.Model):
    __tablename__ = 'vcs'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    vc_number = db.Column(db.Integer, unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    amount = db.Column(db.Float, nullable=False)
    tenure = db.Column(db.Integer, nullable=False)
    current_hand = db.Column(db.Integer, default=1)
    narration = db.Column(db.Text)
    status = db.Column(db.Enum(PaymentStatus, native_enum=False), default=PaymentStatus.PENDING)
    min_interest = db.Column(db.Float, nullable=False, default=0.0)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    # Relationships
    hands = db.relationship('VCHand', backref='vc', lazy=True, cascade='all, delete-orphan')
    payments = db.relationship('Payment', backref='vc', lazy=True, cascade='all, delete-orphan')
    ledger_entries = db.relationship('LedgerEntry', backref='vc', lazy=True)
    members = db.relationship('Person', secondary=vc_members, backref='vcs')
    
    @property
    def total_paid(self):
        return sum(payment.amount for payment in self.payments)
    
    @property
    def due_count(self):
        # Count how many hands have due amounts
        return sum(1 for hand in self.hands if hand.due_amount > 0)
    
    @property
    def total_due_per_vc(self):
        """
        Total outstanding (unpaid) contributions for this VC.
        Sums all contributions where paid=False across all hands in this VC.
        """
        return sum(c.amount for hand in self.hands for c in hand.contributions if not c.paid)
    
    @property
    def current_hand_obj(self):
        """Return the current active hand object, or the last hand if current_hand > tenure."""
        if self.current_hand <= self.tenure:
            return self.current_hand
        else:
            return self.tenure
        
    @property
    def completed_hand_obj(self):
        """Return the current active hand object, or the last hand if current_hand > tenure."""
        if self.current_hand <= self.tenure:
            return self.current_hand - 1
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
    vc_id = db.Column(db.Integer, db.ForeignKey('vcs.id'), nullable=False, index=True)
    hand_number = db.Column(db.Integer, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    contribution_amount = db.Column(db.Float, nullable=False)
    balance = db.Column(db.Float, nullable=False)
    self_half_option = db.Column(db.String(10), default='self')
    is_active = db.Column(db.Boolean, default=False)
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
        distributions = self.hand_distributions
        
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
    hand_id = db.Column(db.Integer, db.ForeignKey('vc_hands.id'), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    narration = db.Column(db.Text)
    payment_date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_vc_money_taken = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    person = db.relationship('Person', backref='hand_distributions')
