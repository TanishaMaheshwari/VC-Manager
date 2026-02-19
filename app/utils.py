"""Utilities for VC-Manager"""
from functools import wraps
from flask_login import login_required as flask_login_required

# Use Flask-Login's login_required decorator
login_required = flask_login_required

