"""Flask app factory and initialization"""
import os
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from dotenv import load_dotenv

load_dotenv()

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()

def create_app(config_name='development'):
    """Create and configure the Flask application"""
    import os
    
    # Get the parent directory (project root)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    app = Flask(
        __name__,
        template_folder=os.path.join(project_root, 'templates'),
        static_folder=os.path.join(project_root, 'static') if os.path.exists(os.path.join(project_root, 'static')) else None
    )
    
    # Configuration
    app.config['SECRET_KEY'] = "123123"
    app.config['SQLALCHEMY_DATABASE_URI'] = "sqlite:////Users/tanishamaheshwari/VC_update/VC-Manager/instance/app.db"
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # Initialize extensions with app
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    
    # User loader for Flask-Login
    @login_manager.user_loader
    def load_user(user_id):
        from app.models.user import User
        return User.query.get(int(user_id))
    
    # Import models to make them available to migrations
    with app.app_context():
        from app.models import (
            User, PaymentStatus, Person, VC, VCHand, HandDistribution,
            Contribution, Payment, LedgerEntry
        )
    
    # Register blueprints
    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.vc import vc_bp
    from app.routes.hand import hand_bp
    from app.routes.person import person_bp
    from app.routes.payment import payment_bp
    from app.routes.ledger import ledger_bp
    from app.routes.api import api_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(vc_bp)
    app.register_blueprint(hand_bp)
    app.register_blueprint(person_bp)
    app.register_blueprint(payment_bp)
    app.register_blueprint(ledger_bp)
    app.register_blueprint(api_bp)
    
    # Register template filters
    @app.template_filter('indian_comma')
    def indian_comma(value):
        from babel.numbers import format_decimal
        return format_decimal(value, locale='en_IN')
    
    # Register shell commands
    register_shell_commands(app)
    
    return app

def register_shell_commands(app):
    """Register Flask CLI commands"""
    from app.models import Person
    
    @app.cli.command()
    def init_db():
        """Initialize the database with sample data"""
        db.create_all()
        
        # Check if persons already exist
        if Person.query.first():
            print('Database already initialized with persons.')
            return
        
        sample_persons = [
            Person(name='Tanisha Maheshwari', short_name='TM', phone='9876543210'),
            Person(name='Dharmendra Bhardwaj', short_name='DMB', phone='9876543213'),
            Person(name='Suresh Lal Tiwari', short_name='SLT', phone='9876543214', opening_balance=1000.0),
            Person(name='Bhagwan Singh', short_name='BG', phone='9876543215'),
            Person(name='Ashok Kumar', short_name='A', phone='9876543216'),
            Person(name='Anil Mishra', short_name='AM', phone='9876543217'),
            Person(name='Krishan Kumar', short_name='KK', phone='9876543218'),
            Person(name='Suresh Agarwal', short_name='SAL', phone='9876543219'),
        ]
        for person in sample_persons:
            db.session.add(person)
        
        db.session.commit()
        print('Sample persons created.')
        print('Database initialized!')
