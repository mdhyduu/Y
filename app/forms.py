from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField
from wtforms.validators import DataRequired, Email, Length

class LoginForm(FlaskForm):
    email = StringField('البريد الإلكتروني', validators=[
        DataRequired(message='حقل البريد الإلكتروني مطلوب'),
        Email(message='يجب إدخال بريد إلكتروني صالح')
    ])
    password = PasswordField('كلمة المرور', validators=[
        DataRequired(message='حقل كلمة المرور مطلوب'),
        Length(min=8, message='يجب أن تكون كلمة المرور 8 أحرف على الأقل')
    ])
    remember = BooleanField('تذكرني')