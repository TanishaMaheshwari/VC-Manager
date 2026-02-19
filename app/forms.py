"""Forms for VC-Manager application"""
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, TextAreaField, SelectField, DateTimeField, SubmitField, IntegerField, DateField
from wtforms import SelectMultipleField
from wtforms.widgets import ListWidget, CheckboxInput
from wtforms.validators import DataRequired, Email, Optional, NumberRange, ValidationError
from datetime import datetime

class MultiCheckboxField(SelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()

class VCForm(FlaskForm):
    # vc_number is now automated
    name = StringField("Name", validators=[DataRequired()])
    start_date = DateField("Start Date", format='%Y-%m-%d', validators=[DataRequired()], default=datetime.now())
    amount = FloatField("Amount", validators=[DataRequired(), NumberRange(min=1)])
    min_interest = FloatField("Minimum Interest", validators=[DataRequired(), NumberRange(min=0)])
    tenure = IntegerField("Tenure", validators=[DataRequired()])
    narration = TextAreaField("Narration")

    # Use the custom MultiCheckboxField
    members = MultiCheckboxField("Members", coerce=int)

    submit = SubmitField("Create")

    def validate_tenure(self, field):
        if len(self.members.data) != field.data:
            raise ValidationError("Tenure (number of hands) must be equal to the number of members.")

    def validate_members(self, field):
        if not field.data or len(field.data) < 1:
            raise ValidationError("Please select at least one member")

class PersonForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    short_name = StringField('Short Name', validators=[DataRequired()])
    phone = StringField('Primary Phone Number', validators=[DataRequired()])
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
