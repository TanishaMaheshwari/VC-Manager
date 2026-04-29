"""LedgerEntry model for VC-Manager"""
from datetime import datetime
from app import db

class LedgerEntry(db.Model):
    __tablename__ = 'ledger_entries'
    id = db.Column(db.Integer, primary_key=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=True, index=True)
    vc_id = db.Column(db.Integer, db.ForeignKey('vcs.id'), nullable=True, index=True)
    hand_id    = db.Column(db.Integer, db.ForeignKey('vc_hands.id'), nullable=True)  
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    narration = db.Column(db.Text, nullable=False)
    debit = db.Column(db.Float, default=0)
    credit = db.Column(db.Float, default=0)
    balance = db.Column(db.Float, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    vc = db.relationship('VC', foreign_keys=[vc_id], back_populates='ledger_entries', lazy=True)
    person = db.relationship(
        'Person',
        back_populates='ledger_entries'
    )