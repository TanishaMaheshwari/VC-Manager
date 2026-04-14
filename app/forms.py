"""Forms for VC-Manager application"""
import json
from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField, FloatField, TextAreaField, SelectField, DateTimeField, SubmitField, IntegerField, DateField
from wtforms import SelectMultipleField
from wtforms.widgets import ListWidget, CheckboxInput
from wtforms.validators import DataRequired, Email, Optional, NumberRange, ValidationError
from datetime import datetime

class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()

class VCForm(FlaskForm):
    name         = StringField("Name",           validators=[DataRequired()])
    start_date   = DateField("Start Date",       format='%Y-%m-%d', validators=[DataRequired()], default=datetime.now)
    amount       = FloatField("Amount",          validators=[DataRequired(), NumberRange(min=1)])
    min_interest = FloatField("Minimum Interest",validators=[DataRequired(), NumberRange(min=0)])
    tenure       = IntegerField("Tenure",        validators=[DataRequired()])
    narration    = TextAreaField("Narration")
    members      = MultiCheckboxField("Members", coerce=int)

    submit = SubmitField("Create")

    def _get_slot_map(self):
        """Parse member_slots JSON → {person_id(int): slots(int)}."""
        try:
            raw = json.loads(self.member_slots.data or '{}')
            return {int(k): max(1, int(v)) for k, v in raw.items()}
        except (ValueError, TypeError):
            return {}

    def validate_members(self, field):
        if not field.data or len(field.data) < 1:
            raise ValidationError("Please select at least one member.")

class PersonForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    short_name = StringField('Short Name', validators=[DataRequired()])
    phone = StringField('Primary Phone Number', validators=[Optional()])
    phone2 = StringField('Secondary Phone Number', validators=[Optional()])
    opening_balance = FloatField('Opening Balance', validators=[Optional()])
    submit = SubmitField('Create')

class PaymentForm(FlaskForm):
    vc_id = SelectField('VC', validators=[DataRequired()], coerce=int)
    hand_id = SelectField('Hand', validators=[DataRequired()], coerce=int)
    person_id = SelectField('Person', validators=[DataRequired()], coerce=int)
    amount = FloatField('Amount', validators=[DataRequired(), NumberRange(min=0.01)])
    # Use HTML5 `datetime-local` format for consistent browser-level editing
    # Format corresponds to: YYYY-MM-DDTHH:MM (no seconds)
    date = DateTimeField('Date', format='%Y-%m-%dT%H:%M', validators=[DataRequired()], default=datetime.now)
    narration = TextAreaField('Narration')
    submit = SubmitField('Record Payment')

class LedgerEntryForm(FlaskForm):
    person_id = SelectField('Person', validators=[DataRequired()], coerce=int)
    vc_id = SelectField('VC (Optional)', validators=[Optional()], coerce=int)
    date = DateTimeField('Date', validators=[DataRequired()], default=datetime.now)
    narration = TextAreaField('Narration', validators=[DataRequired()])
    debit = FloatField('Debit', validators=[Optional(), NumberRange(min=0)], default=0)
    credit = FloatField('Credit', validators=[Optional(), NumberRange(min=0)], default=0)
    submit = SubmitField('Add Entry')


"""Transaction form for custom DR/CR entries"""
from flask_wtf import FlaskForm
from wtforms import SelectField, DecimalField, TextAreaField, SubmitField, RadioField
from wtforms.validators import DataRequired, NumberRange, Optional
from wtforms.widgets import HiddenInput


class TransactionForm(FlaskForm):
    
        person_id = SelectField('Member', coerce=int, validators=[DataRequired()])
        
        type = RadioField(
            'Transaction Type',
            choices=[('credit', 'Received'), ('debit', 'Paid')],
            default='credit',
            validators=[DataRequired()]
        )
        
        amount = DecimalField(
            'Amount (₹)',
            validators=[
                DataRequired(),
                NumberRange(min=0.01, message="Amount must be greater than 0")
            ],
            places=2
        )
        
        
        narration = TextAreaField(
            'Description',
            validators=[Optional()],
            render_kw={
                'placeholder': 'e.g., Personal loan, Deposit, Withdrawal, etc.',
                'rows': 3
            }
        )
        
        submit = SubmitField('Add Transaction')