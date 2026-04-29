"""Transaction model for custom DR/CR transactions"""
from datetime import datetime
from app import db

class Transaction(db.Model):
    __tablename__ = 'transactions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False, index=True)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    amount = db.Column(db.Float, nullable=False)
    type = db.Column(db.String(10), nullable=False)  # 'debit' or 'credit'
    narration = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref='transactions')
    person = db.relationship('Person', back_populates='transactions')

    def __repr__(self):
        return f'<Transaction {self.id}: {self.person.name} {self.type} ₹{self.amount}>'
