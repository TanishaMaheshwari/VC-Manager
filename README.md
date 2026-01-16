# VC-Manager

A Flask-based application for managing Rotating Savings and Credit Associations (VC - Vikram Chakra). This application helps track members, hands (rounds), contributions, and payouts in a structured way.

## Features

- **VC Management**: Create and manage multiple VCs with configurable tenure and amount
- **Member Management**: Add members to VCs and track their participation
- **Hand Distribution**: Distribute hands (rounds) with bidding system
- **Contribution Tracking**: Track unpaid contributions and mark them as paid
- **Payment Recording**: Record payments and update contribution status
- **Ledger Management**: Maintain detailed ledger entries for each person
- **Due Tracking**: Calculate and display total due amounts globally, by VC, and by person
- **PDF Reports**: Generate PDF ledger reports for export

## Tech Stack

- **Backend**: Flask
- **Database**: SQLite (SQLAlchemy ORM)
- **Frontend**: HTML, CSS, Bootstrap, JavaScript
- **Migrations**: Flask-Migrate
- **PDF Generation**: WeasyPrint, FPDF
- **Forms**: WTForms
- **Number Formatting**: Babel

## Prerequisites

- Python 3.8+
- pip (Python package manager)

## Installation & Setup

### 1. Clone the Repository

```bash
git clone <repository-url>
cd VC-Manager
```

### 2. Create a Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Initialize the Database

The database will be automatically created when you run the app for the first time.

### 5. (Optional) Load Sample Data

```bash
export FLASK_APP=application.py
flask init-db
```

This will populate the database with sample persons for testing.

## Running the Application

### Start the Flask Development Server

```bash
python application.py
```

The application will be available at:
- `http://127.0.0.1:5000` (localhost)
- `http://192.168.1.167:5000` (network)

### Access the App

1. Open your browser and go to `http://127.0.0.1:5000`
2. Login with credentials:
   - **User ID**: `VCManager001`
   - **Password**: `123vc`

## Database Management

### Reset the Database (Clean All Data)

```bash
rm app.db
python application.py
```

Or use a one-liner:

```bash
rm -f app.db && python application.py
```

### Using Flask Migrations (Advanced)

```bash
export FLASK_APP=application.py
flask db migrate -m "Description of changes"
flask db upgrade
```

## Project Structure

```
VC-Manager/
├── application.py          # Main Flask application
├── requirements.txt        # Python dependencies
├── README.md              # This file
├── templates/             # HTML templates
│   ├── base.html
│   ├── login.html
│   ├── dashboard.html
│   ├── vc/                # VC-related templates
│   │   ├── list.html
│   │   ├── create.html
│   │   ├── view.html
│   │   └── hand_distribution.html
│   ├── person/            # Person management templates
│   │   ├── list.html
│   │   ├── create.html
│   │   └── edit.html
│   ├── payment/           # Payment templates
│   │   └── create.html
│   └── ledger/            # Ledger templates
│       ├── person.html
│       ├── create.html
│       └── pdf_template.html
├── instance/              # Instance folder (created on first run)
│   └── app.db            # SQLite database
└── __pycache__/          # Python cache (auto-generated)
```

## API Endpoints

### Authentication
- `GET /login` - Login page
- `POST /login` - Submit login credentials
- `GET /logout` - Logout

### Dashboard & Overview
- `GET /` - Dashboard (main page)
- `GET /vcs` - List all VCs

### VC Management
- `GET /vc/create` - Create new VC form
- `POST /vc/create` - Submit new VC
- `GET /vc/<id>` - View VC details
- `GET /vc/delete/<id>` - Delete VC

### Hand Distribution
- `GET /vc/<vc_id>/hand/<hand_number>` - View hand distribution details
- `POST /vc/<vc_id>/distribute-hand` - Distribute a hand
- `POST /vc/<vc_id>/hand/<hand_id>/edit-payout` - Edit hand payout

### Person Management
- `GET /persons` - List all persons
- `GET /person/create` - Create new person form
- `POST /person/create` - Submit new person
- `GET /person/<id>/edit` - Edit person form
- `POST /person/<id>/edit` - Submit person edit

### Payments & Ledger
- `GET /payment/create` - Record payment form
- `POST /payment/create` - Submit payment
- `GET /record-payment` - Alternative payment recording
- `GET /ledger/<person_id>` - View person's ledger
- `GET /ledger/create?person_id=<id>` - Create ledger entry (pre-filled with person)
- `POST /ledger/create` - Submit ledger entry
- `GET /ledger/<person_id>/pdf` - Export ledger as PDF

### API Routes
- `GET /api/vc/<vc_id>/details` - Get VC details (JSON)
- `GET /api/hand/<hand_id>/details` - Get hand details (JSON)

## Key Concepts

### Total Due Calculation

The application tracks three levels of "due" amounts:

1. **Total Due (Global)** - Sum of all unpaid contributions across all people and VCs
   - Used on dashboard and VC list pages
   - Represents the overall outstanding amount

2. **Total Due by VC** - Outstanding amount for a specific VC
   - Accessed via `vc.total_due_per_vc` property
   - Shows unpaid contributions in that VC only

3. **Total Due by Person** - Outstanding amount for a specific person
   - Accessed via `person.total_due_per_person` property
   - Shows all unpaid contributions by that person across all VCs

### Contribution Status

Contributions have a `paid` flag that tracks payment status:
- **paid=False** - Contribution record created but not yet paid
- **paid=True** - Payment has been recorded for this contribution

The `paid` flag is automatically set to `True` when a payment is recorded via the payment recording routes.

## Troubleshooting

### Port Already in Use

If port 5000 is already in use, stop the process using that port:

```bash
lsof -i :5000
kill -9 <PID>
```

### Database Locked Error

Close all instances of the app and try again. If the issue persists:

```bash
rm app.db
python application.py
```

### Import Errors

Ensure all dependencies are installed:

```bash
pip install -r requirements.txt
```

## Git Repository - Push Changes

To commit and push changes to the git repository:

```bash
git add .
git commit -m "Update README with setup and run instructions"
git push origin main
```

Or push to the original cloned repository:

```bash
git add .
git commit -m "Your commit message"
git push
```

## License

[Add your license information here]

## Support

For issues or questions, please contact the development team.
