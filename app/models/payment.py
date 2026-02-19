"""Payment model for VC-Manager"""
from datetime import datetime
from app import db

class Payment(db.Model):
    __tablename__ = 'payments'
    id = db.Column(db.Integer, primary_key=True)
    vc_id = db.Column(db.Integer, db.ForeignKey('vcs.id'), nullable=False, index=True)
    person_id = db.Column(db.Integer, db.ForeignKey('persons.id'), nullable=False, index=True)
    hand_id = db.Column(db.Integer, db.ForeignKey('vc_hands.id'), nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    narration = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Relationships
    hand = db.relationship('VCHand', backref='payments')
