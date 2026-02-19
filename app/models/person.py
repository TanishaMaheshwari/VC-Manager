"""Person model for VC-Manager"""
from datetime import datetime
from sqlalchemy.orm import validates
from app import db

class Person(db.Model):
    __tablename__ = 'persons'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    short_name = db.Column(db.String(20), unique=True, nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    phone2 = db.Column(db.String(20), nullable=True)
    opening_balance = db.Column(db.Float, default=0.0, nullable=True)
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

    @property
    def ledger_balance(self):
        """
        Get the balance from the most recent ledger entry.
        Returns the actual recorded balance, not a calculated sum.
        """
        if self.ledger_entries:
            # Sort by date descending to get the most recent entry
            last_entry = sorted(self.ledger_entries, key=lambda e: e.date, reverse=True)[0]
            return last_entry.balance
        return self.opening_balance or 0.0

    @property
    def total_due_per_person(self):
        """
        Total outstanding (unpaid) contributions for this person.
        Sums all contributions where paid=False for this specific person.
        """
        return sum(c.amount for c in self.contributions if not c.paid)
