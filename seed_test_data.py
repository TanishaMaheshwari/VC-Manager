#!/usr/bin/env python3
"""Script to seed test data in the database"""

import sys
sys.path.insert(0, '/Users/tanishamaheshwari/VC_update/VC-Manager')

from application import app, db
from app.models.person import Person

with app.app_context():
    # Create 5 test persons
    persons = [
        Person(name="Raj Kumar", short_name="RK", phone="9876543210", opening_balance=5000.0),
        Person(name="Priya Singh", short_name="PS", phone="9876543211", opening_balance=10000.0),
        Person(name="Amit Patel", short_name="AP", phone="9876543212", opening_balance=0.0),
        Person(name="Anjali Sharma", short_name="AS", phone="9876543213", opening_balance=0.0),
        Person(name="Vikram Gupta", short_name="VG", phone="9876543214", opening_balance=0.0),
    ]
    
    for person in persons:
        db.session.add(person)
    
    db.session.commit()
    
    print("✓ Successfully added 5 test persons:")
    for person in persons:
        print(f"  - {person.name} ({person.short_name}) - Opening Balance: ₹{person.opening_balance}")
