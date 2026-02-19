#!/usr/bin/env python3
"""Seed test users for multi-user VC-Manager system"""

from application import app, db
from app.models.user import User
from app.models.person import Person
from app.models.ledger import LedgerEntry
from datetime import datetime

def seed_users():
    """Create test users with sample persons"""
    
    with app.app_context():
        # Check if users already exist
        if User.query.first():
            print("✓ Users already exist in database")
            return
        
        # Create test users
        test_users = [
            {
                'email': 'user1@example.com',
                'password': 'password123',
                'persons': [
                    {'name': 'Tanisha Maheshwari', 'short_name': 'TM', 'phone': '9876543210', 'opening_balance': 0},
                    {'name': 'Dharmendra Bhardwaj', 'short_name': 'DB', 'phone': '9876543213', 'opening_balance': 5000},
                    {'name': 'Suresh Lal', 'short_name': 'SL', 'phone': '9876543214', 'opening_balance': 0},
                ]
            },
            {
                'email': 'user2@example.com',
                'password': 'password123',
                'persons': [
                    {'name': 'Ashok Kumar', 'short_name': 'AK', 'phone': '9876543215', 'opening_balance': 3000},
                    {'name': 'Anil Mishra', 'short_name': 'AM', 'phone': '9876543216', 'opening_balance': 0},
                    {'name': 'Krishan Kumar', 'short_name': 'KK', 'phone': '9876543217', 'opening_balance': 2500},
                ]
            },
            {
                'email': 'demo@example.com',
                'password': 'demo123',
                'persons': [
                    {'name': 'Rajesh Verma', 'short_name': 'RV', 'phone': '9876543218', 'opening_balance': 1000},
                    {'name': 'Priya Singh', 'short_name': 'PS', 'phone': '9876543219', 'opening_balance': 0},
                ]
            }
        ]
        
        # Create users and their persons
        for user_data in test_users:
            user = User(
                email=user_data['email'],
                name=user_data['email'].split('@')[0]  # Use part before @ as name
            )
            user.set_password(user_data['password'])
            db.session.add(user)
            db.session.flush()  # Get user ID
            
            print(f"✓ Created user: {user_data['email']}")
            
            # Create persons for this user
            for person_data in user_data['persons']:
                person = Person(
                    user_id=user.id,
                    name=person_data['name'],
                    short_name=person_data['short_name'],
                    phone=person_data['phone'],
                    opening_balance=person_data['opening_balance'],
                    created_at=datetime.utcnow()
                )
                db.session.add(person)
                db.session.flush()
                
                # Add opening balance to ledger if > 0
                if person_data['opening_balance'] > 0:
                    ledger_entry = LedgerEntry(
                        person_id=person.id,
                        date=datetime.utcnow(),
                        narration="Opening Balance",
                        credit=person_data['opening_balance'],
                        balance=person_data['opening_balance']
                    )
                    db.session.add(ledger_entry)
                
                print(f"  ✓ Created person: {person_data['name']} (opening balance: ₹{person_data['opening_balance']})")
        
        db.session.commit()
        print("\n✅ Database seeded successfully with test users!")
        print("\nTest accounts created:")
        print("  Email: user1@example.com | Password: password123")
        print("  Email: user2@example.com | Password: password123")
        print("  Email: demo@example.com  | Password: demo123")

if __name__ == '__main__':
    seed_users()
