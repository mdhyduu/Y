from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField
from wtforms.validators import DataRequired, Email, EqualTo  # أضف EqualTo هنا
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
class RegisterForm(FlaskForm):
    email = StringField('البريد الإلكتروني', validators=[
        DataRequired(message='حقل مطلوب'),
        Email(message='بريد إلكتروني غير صالح')
    ])
    password = PasswordField('كلمة المرور', validators=[
        DataRequired(message='حقل مطلوب'),
        Length(min=6, message='يجب أن تكون كلمة المرور 6 أحرف على الأقل')
    ])
    confirm_password = PasswordField('تأكيد كلمة المرور', validators=[
        DataRequired(message='حقل مطلوب'),
        EqualTo('password', message='كلمتا المرور غير متطابقتين')  # استخدم EqualTo هنا
    ])