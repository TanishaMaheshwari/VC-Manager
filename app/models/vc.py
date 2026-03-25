"""VC models — supports multi-slot members (one person, multiple hands)"""
from datetime import datetime, timedelta, timezone
from app import db
from app.models.enums import PaymentStatus

# ── Association table ────────────────────────────────────────────────────────
# slots: how many hands this person holds in the VC.
# Priya with slots=2 counts as 2 members — pays 2× contribution per hand,
# eligible to win up to 2 hands.
vc_members = db.Table(
    'vc_members',
    db.Column('vc_id',     db.Integer, db.ForeignKey('vcs.id'),     primary_key=True, index=True),
    db.Column('person_id', db.Integer, db.ForeignKey('persons.id'), primary_key=True, index=True),
    db.Column('slots',     db.Integer, nullable=False, default=1)
)


class VC(db.Model):
    __tablename__ = 'vcs'

    id           = db.Column(db.Integer, primary_key=True)
    user_id      = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    vc_number    = db.Column(db.Integer, unique=True, nullable=False)
    name         = db.Column(db.String(100), nullable=False)
    start_date   = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    amount       = db.Column(db.Float, nullable=False)
    tenure       = db.Column(db.Integer, nullable=False)
    current_hand = db.Column(db.Integer, default=1)
    narration    = db.Column(db.Text)
    status       = db.Column(db.Enum(PaymentStatus, native_enum=False), default=PaymentStatus.PENDING)
    min_interest = db.Column(db.Float, nullable=False, default=0.0)
    created_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at   = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                             onupdate=lambda: datetime.now(timezone.utc))

    hands          = db.relationship('VCHand',      backref='vc', lazy=True, cascade='all, delete-orphan')
    payments       = db.relationship('Payment',     backref='vc', lazy=True, cascade='all, delete-orphan')
    ledger_entries = db.relationship('LedgerEntry', back_populates='vc', lazy=True)
    members        = db.relationship('Person', secondary=vc_members, backref='vcs')

    # ── Slot helpers ─────────────────────────────────────────────────────────

    def get_slots(self, person_id):
        """Slots held by a specific person in this VC (0 if not a member)."""
        row = db.session.execute(
            vc_members.select().where(
                (vc_members.c.vc_id     == self.id) &
                (vc_members.c.person_id == person_id)
            )
        ).fetchone()
        return row.slots if row else 0

    def set_slots(self, person_id, slots):
        """Update slot count for a person already in this VC."""
        db.session.execute(
            vc_members.update()
            .where(
                (vc_members.c.vc_id     == self.id) &
                (vc_members.c.person_id == person_id)
            )
            .values(slots=slots)
        )

    @property
    def total_slots(self):
        """
        Sum of all slots across all members — equals tenure.
        Rajesh(1) + Priya(2) = 3 total slots for a 3-hand VC.
        """
        result = db.session.execute(
            db.select(db.func.sum(vc_members.c.slots))
            .where(vc_members.c.vc_id == self.id)
        ).scalar()
        return int(result or 0)

    @property
    def slots_display(self):
        """List of (person, slots) tuples for display — e.g. [(Rajesh, 1), (Priya, 2)]."""
        rows = db.session.execute(
            db.select(vc_members.c.person_id, vc_members.c.slots)
            .where(vc_members.c.vc_id == self.id)
        ).fetchall()
        person_map = {p.id: p for p in self.members}
        return [(person_map[r.person_id], r.slots) for r in rows if r.person_id in person_map]

    # ── General properties ────────────────────────────────────────────────────

    @property
    def total_paid(self):
        return sum(p.amount for p in self.payments)

    @property
    def due_count(self):
        return sum(1 for h in self.hands if h.due_amount > 0)

    @property
    def total_due_per_vc(self):
        return sum(c.amount for h in self.hands for c in h.contributions if not c.paid)

    @property
    def current_hand_obj(self):
        return self.current_hand if self.current_hand <= self.tenure else self.tenure

    @property
    def completed_hand_obj(self):
        return (self.current_hand - 1) if self.current_hand <= self.tenure else self.tenure

    @property
    def completed_hands(self):
        return sum(1 for h in self.hands if h.due_amount == 0)

    def create_hands(self):
        """Create one VCHand per month of tenure."""
        for month in range(1, self.tenure + 1):
            hand_date = self.start_date + timedelta(days=30 * (month - 1))
            hand = VCHand(
                vc_id=self.id,
                hand_number=month,
                date=hand_date,
                contribution_amount=self.amount / self.tenure,
                balance=self.amount,
                self_half_option='self'
            )
            db.session.add(hand)


class VCHand(db.Model):
    __tablename__ = 'vc_hands'

    id                  = db.Column(db.Integer, primary_key=True)
    vc_id               = db.Column(db.Integer, db.ForeignKey('vcs.id'), nullable=False, index=True)
    hand_number         = db.Column(db.Integer, nullable=False)
    date                = db.Column(db.DateTime, nullable=False)
    contribution_amount = db.Column(db.Float, nullable=False)
    balance             = db.Column(db.Float, nullable=False)
    self_half_option    = db.Column(db.String(10), default='self')
    is_active           = db.Column(db.Boolean, default=False)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow)

    hand_distributions = db.relationship('HandDistribution', backref='vc_hand', lazy=True, cascade='all, delete-orphan')
    contributions      = db.relationship('Contribution',     backref='vc_hand', lazy=True, cascade='all, delete-orphan')

    @property
    def total_contributed(self):
        return sum(c.amount for c in self.contributions)

    @property
    def total_paid(self):
        return sum(d.amount for d in self.hand_distributions)

    @property
    def due_amount(self):
        """Total due for this hand = contribution_amount × total_slots − contributed so far."""
        return max(self.contribution_amount * self.vc.total_slots - self.total_contributed, 0)

    @property
    def is_operator_hand(self):
        return any(d.is_operator_taken for d in self.hand_distributions)

    @property
    def winner_short_name(self):
        if not self.hand_distributions:
            return None
        names = []
        for d in self.hand_distributions:
            names.append("Operator" if d.is_operator_taken else d.person.short_name)
        return ", ".join(names)

    @property
    def projected_payout(self):
        steps_from_end   = self.vc.tenure - self.hand_number + 1
        deduction_amount = self.vc.amount * (self.vc.min_interest / 100)
        return self.vc.amount - (steps_from_end * deduction_amount)

    @property
    def actual_contribution_per_slot(self):
        """Per-slot share for this hand. A person with slots=2 pays this × 2."""
        if self.hand_distributions:
            total_payout = sum(d.amount for d in self.hand_distributions)
            if self.vc.total_slots > 0:
                return total_payout / self.vc.total_slots
        if self.vc.total_slots > 0:
            return self.projected_payout / self.vc.total_slots
        return 0

    # Alias so existing templates don't break
    @property
    def actual_contribution_per_person(self):
        return self.actual_contribution_per_slot

    @property
    def interest_rate(self):
        payout = sum(d.amount for d in self.hand_distributions) if self.hand_distributions else self.projected_payout
        return ((self.vc.amount - payout) / self.vc.amount * 100) if self.vc.amount > 0 else 0

    @property
    def interest_amount(self):
        return self.vc.amount - self.projected_payout

    def amount_due_for(self, person_id):
        """
        Amount still owed by a person for this hand.
        Accounts for their slot count — slots=2 means they owe 2× per-slot amount.
        Winners owe nothing regardless of slot count.
        """
        winner_ids = {
            d.person_id for d in self.hand_distributions
            if not d.is_operator_taken and d.person_id is not None
        }
        if person_id in winner_ids:
            return 0

        slots    = self.vc.get_slots(person_id)
        expected = self.actual_contribution_per_slot * slots
        paid     = sum(c.amount for c in self.contributions if c.person_id == person_id)
        return max(expected - paid, 0)


class HandDistribution(db.Model):
    __tablename__ = 'hand_distributions'

    id                = db.Column(db.Integer, primary_key=True)
    hand_id           = db.Column(db.Integer, db.ForeignKey('vc_hands.id'), nullable=False, index=True)
    person_id         = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=True, index=True)
    amount            = db.Column(db.Float, nullable=False)
    narration         = db.Column(db.Text)
    payment_date      = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_operator_taken = db.Column(db.Boolean, nullable=False, default=False)
    is_vc_money_taken = db.Column(db.Boolean, default=False)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

    person = db.relationship('Person', backref='hand_distributions')