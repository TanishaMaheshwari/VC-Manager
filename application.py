#!/usr/bin/env python
"""
VC-Manager Application Entry Point

This is the main entry point for the Flask application.
It imports the app factory and creates the Flask app.
"""

import os
from app import create_app, db

# Create the Flask application
app = create_app()

if __name__ == '__main__':
    with app.app_context():
        # Create tables if they don't exist
        db.create_all()
        print("✅ Tables created successfully!")    
        try:
            with db.engine.connect() as conn:
                print("✅ Successfully connected to:", conn.engine.url)
        except Exception as e:
            print("❌ Database connection failed:", e)
    
    # Run the development server
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
