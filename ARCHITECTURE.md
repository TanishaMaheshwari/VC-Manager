# Application Architecture Diagram

## Overall Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                   application.py (Entry Point)              │
│                                                               │
│  from app import create_app, db                             │
│  app = create_app()                                         │
│  app.run(host="0.0.0.0", port=5000)                        │
└─────────────────┬───────────────────────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────────────────────┐
│              app/__init__.py (App Factory)                  │
│                                                               │
│  create_app():                                              │
│  ├─ Initialize Flask                                        │
│  ├─ Initialize SQLAlchemy (db)                              │
│  ├─ Initialize Flask-Migrate                                │
│  ├─ Register all blueprints                                 │
│  ├─ Set up template filters                                 │
│  └─ Register CLI commands                                   │
└──┬──────────────────────────────────────────────────────────┘
   │
   ├─────────────────────────┬─────────────────────────────────┐
   │                         │                                 │
   ▼                         ▼                                 ▼
┌─────────────────┐  ┌──────────────────┐  ┌─────────────────┐
│  app/models/    │  │  app/routes/     │  │  app/forms.py   │
│                 │  │                  │  │                 │
│ Database Models │  │ Route Blueprints │  │ WTForms Forms   │
│                 │  │                  │  │                 │
│ ├─ enums.py    │  │ ├─ auth.py       │  │ ├─ VCForm       │
│ ├─ person.py   │  │ ├─ dashboard.py  │  │ ├─ PersonForm   │
│ ├─ vc.py       │  │ ├─ vc.py         │  │ ├─ PaymentForm  │
│ ├─ contrib...  │  │ ├─ person.py     │  │ └─ LedgerForm   │
│ ├─ payment.py  │  │ ├─ hand.py       │  └─────────────────┘
│ ├─ ledger.py   │  │ ├─ payment.py    │
│ └─ __init__.py │  │ ├─ ledger.py     │
└─────────────────┘  │ ├─ api.py        │
                     │ └─ __init__.py   │
                     └──────────────────┘
```

## Data Flow: Request to Response

```
HTTP Request
    │
    ▼
application.py (Entry Point)
    │
    ▼
app/__init__.py (create_app())
    │
    ├─ Initializes Flask
    ├─ Sets up Database (SQLAlchemy)
    └─ Registers Blueprints
         │
         ├─► auth_bp (login/logout)
         ├─► dashboard_bp (home)
         ├─► vc_bp (VC management)
         ├─► person_bp (Person management)
         ├─► hand_bp (Hand distribution)
         ├─► payment_bp (Payment recording)
         ├─► ledger_bp (Ledger management)
         └─► api_bp (JSON API)
    │
    ▼
Route Handler (e.g., vc.py)
    │
    ├─ @login_required decorator (app/utils.py)
    ├─ Load data from Models (app/models/)
    ├─ Process form data (app/forms.py)
    └─ Query database using db (from app/__init__.py)
    │
    ▼
Render Template or Return JSON
    │
    ▼
HTTP Response
```

## Module Dependencies

```
application.py
    │
    └─► app/__init__.py (App Factory)
         │
         ├─► app/models/__init__.py
         │    ├─► models/enums.py
         │    ├─► models/person.py
         │    ├─► models/vc.py (imports from enums, person)
         │    ├─► models/contribution.py
         │    ├─► models/payment.py
         │    └─► models/ledger.py
         │
         ├─► app/forms.py
         │
         ├─► app/utils.py
         │
         └─► app/routes/__init__.py
              │
              ├─► routes/auth.py (imports utils)
              ├─► routes/dashboard.py (imports models, forms, utils)
              ├─► routes/vc.py (imports models, forms, utils)
              ├─► routes/person.py (imports models, forms, utils)
              ├─► routes/hand.py (imports models, forms, utils)
              ├─► routes/payment.py (imports models, forms, utils)
              ├─► routes/ledger.py (imports models, forms, utils)
              └─► routes/api.py (imports models, utils)
```

## Database Schema Relationships

```
Person (persons table)
    │
    ├─◄─────── Many-to-Many ─────────►VC (vcs table)
    │           (vc_members)           │
    │                                  │
    │                                  ├─► VCHand (vc_hands)
    │                                  │    │
    │                                  │    ├─► HandDistribution
    │                                  │    │    │
    │                                  │    │    └─► Person
    │                                  │    │
    │                                  │    └─► Contribution
    │                                  │         └─► Person
    │
    ├─► Contribution
    │    └─► VCHand
    │
    ├─► Payment
    │    ├─► VC
    │    └─► VCHand
    │
    ├─► LedgerEntry
    │    ├─► VC (optional)
    │    └─► Person
    │
    └─► HandDistribution
         └─► VCHand
```

## Authentication Flow

```
┌──────────────┐
│ /login       │
└──────┬───────┘
       │
       ▼
┌─────────────────────────────┐
│ POST login form             │
│ ├─ user_id (VCManager001)   │
│ └─ password (123vc)         │
└──────┬──────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ routes/auth.py::login()      │
│                              │
│ if valid credentials:        │
│   session['logged_in']=True  │
│   redirect(dashboard)        │
└──────┬───────────────────────┘
       │
       ▼
┌──────────────────────────────┐
│ @login_required routes       │
│                              │
│ All routes check:            │
│ if not session.get('logged_in')
│   redirect to login          │
└──────────────────────────────┘
```

## Creating a New Feature: Step-by-Step

```
1. Create Model (app/models/feature.py)
   │
   ├─ from app import db
   │
   ├─ class Feature(db.Model):
   │    ├─ __tablename__ = 'features'
   │    ├─ id = db.Column(...)
   │    └─ # ... other columns
   │
   └─ Export in app/models/__init__.py

2. Update Models Package (app/models/__init__.py)
   │
   └─ from app.models.feature import Feature
      Add to __all__

3. Create Routes (app/routes/feature.py)
   │
   ├─ from flask import Blueprint
   ├─ from app import db
   ├─ from app.models.feature import Feature
   ├─ from app.utils import login_required
   │
   ├─ feature_bp = Blueprint('feature', __name__, url_prefix='/feature')
   │
   ├─ @feature_bp.route('/')
   ├─ @login_required
   └─ def list_features():
       # ...

4. Create Form (app/forms.py)
   │
   ├─ class FeatureForm(FlaskForm):
   │   ├─ name = StringField(...)
   │   ├─ # ... other fields
   │   └─ submit = SubmitField('Create')
   │
   └─ Import in routes

5. Register Blueprint (app/__init__.py)
   │
   ├─ from app.routes.feature import feature_bp
   │
   └─ app.register_blueprint(feature_bp)

6. Create Templates (templates/feature/)
   │
   ├─ list.html
   ├─ create.html
   └─ detail.html
```

## Deployment Considerations

```
Production Setup
│
├─ Use a WSGI server (Gunicorn, uWSGI)
│  │
│  ├─ gunicorn -w 4 -b 0.0.0.0:5000 application:app
│  │
│  └─ Or via application.py factory:
│     gunicorn -w 4 -b 0.0.0.0:5000 'app:create_app()'
│
├─ Use environment variables for config
│  │
│  ├─ DATABASE_URL
│  ├─ SECRET_KEY
│  └─ FLASK_ENV
│
├─ Database migrations
│  │
│  ├─ flask db upgrade
│  │
│  └─ Supports SQLite, PostgreSQL, MySQL
│
└─ Static files
   │
   ├─ Use CDN or nginx for static files
   │
   └─ Collected from templates/static/
```

---

This architecture provides:
- ✅ Clear separation of concerns
- ✅ Easy to test
- ✅ Easy to scale
- ✅ Easy to maintain
- ✅ Easy to add new features
