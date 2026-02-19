"""User model for VC-Manager"""
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin
from app import db

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=True)  # Nullable for OAuth-only users
    google_id = db.Column(db.String(255), unique=True, nullable=True)  # For OAuth users
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login = db.Column(db.DateTime, nullable=True)

    # Relationships
    vcs = db.relationship('VC', backref='user', lazy=True, cascade='all, delete-orphan')
    persons = db.relationship('Person', backref='user', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.email}>'

    def set_password(self, password):
        """Hash and set the password"""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Check if provided password matches hash"""
        return self.password_hash and check_password_hash(self.password_hash, password)

    def is_oauth_user(self):
        """Check if this user signed in via Google OAuth"""
        return self.google_id is not None
