from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
import re
import logging
from .models import db, User, Employee
from datetime import datetime, timedelta
from functools import wraps
from flask_mail import Message
import random
from . import mail   # استدعاء mail من __init__.py
user_auth_bp = Blueprint('user_auth', __name__)
logger = logging.getLogger(__name__)

# فلتر حماية يمنع الوصول إذا المستخدم مسجل دخول
def redirect_if_authenticated(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if request.cookies.get('user_id'):
            return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

# التحقق من صحة البريد
def validate_email(form, field):
    email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    if not re.match(email_regex, field.data):
        raise ValidationError('يجب إدخال بريد إلكتروني صالح')

# نموذج تسجيل الدخول
class LoginForm(FlaskForm):
    email = StringField('البريد الإلكتروني', validators=[DataRequired(), validate_email])
    password = PasswordField('كلمة المرور', validators=[DataRequired()])

# نموذج التسجيل
class RegisterForm(FlaskForm):
    email = StringField('البريد الإلكتروني', validators=[DataRequired(), validate_email])
    password = PasswordField('كلمة المرور', 
                           validators=[DataRequired(), 
                                      Length(min=8, message='يجب أن تكون كلمة المرور 8 أحرف على الأقل')])
    confirm_password = PasswordField('تأكيد كلمة المرور', 
                                   validators=[DataRequired(), 
                                              EqualTo('password', message='كلمتا المرور غير متطابقتين')])

# نموذج إدخال كود التحقق
class VerifyOTPForm(FlaskForm):
    otp_code = StringField('رمز التحقق', validators=[DataRequired(), Length(min=6, max=6)])
    submit = SubmitField('تأكيد')


# تسجيل الدخول
@user_auth_bp.route('/login', methods=['GET', 'POST'])
@redirect_if_authenticated
def login():
    form = LoginForm()
    
    if form.validate_on_submit():
        email = form.email.data
        password = form.password.data
        
        try:
            # تسجيل دخول كمشرف
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                if not user.is_verified:
                    flash('يجب تفعيل بريدك الإلكتروني أولاً باستخدام رمز التحقق', 'warning')
                    return redirect(url_for('user_auth.verify_otp', user_id=user.id))

                response = make_response(redirect(url_for('dashboard.index')))
                response.set_cookie('user_id', str(user.id), max_age=timedelta(days=30).total_seconds(), httponly=True, secure=False)
                response.set_cookie('is_admin', 'true', max_age=timedelta(days=30).total_seconds())
                response.set_cookie('employee_role', '', max_age=timedelta(days=30).total_seconds())
                
                if user.salla_access_token:
                    response.set_cookie('salla_access_token', user.get_access_token(), max_age=timedelta(days=30).total_seconds(), httponly=True, secure=False)
                    response.set_cookie('salla_refresh_token', user.salla_refresh_token, max_age=timedelta(days=30).total_seconds(), httponly=True, secure=False)
                
                flash('تم تسجيل دخول المشرف بنجاح!', 'success')
                logger.info(f"تم تسجيل دخول المشرف: {user.email}, is_admin: {user.is_admin}")
                return response
            
            # تسجيل دخول كموظف
            employee = Employee.query.filter_by(email=email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('حسابك موقوف. يرجى الاتصال بالإدارة', 'danger')
                    logger.warning(f"محاولة تسجيل دخول لحساب موقوف: {email}")
                    return redirect(url_for('user_auth.login'))
                
                response = make_response(redirect(url_for('dashboard.index')))
                response.set_cookie('user_id', str(employee.id), max_age=timedelta(days=30).total_seconds(), httponly=True, secure=False)
                response.set_cookie('is_admin', 'false', max_age=timedelta(days=30).total_seconds())
                response.set_cookie('employee_role', employee.role, max_age=timedelta(days=30).total_seconds())
                response.set_cookie('store_id', str(employee.store_id), max_age=timedelta(days=30).total_seconds())
                
                store_admin = User.query.filter_by(store_id=employee.store_id).first()
                if store_admin and store_admin.salla_access_token:
                    response.set_cookie('salla_access_token', store_admin.get_access_token(), max_age=timedelta(days=30).total_seconds(), httponly=True, secure=False)
                    response.set_cookie('salla_refresh_token', store_admin.get_refresh_token(), max_age=timedelta(days=30).total_seconds(), httponly=True, secure=False)
                
                flash('تم تسجيل دخول الموظف بنجاح!', 'success')
                logger.info(f"تم تسجيل دخول الموظف: {employee.email} - المتجر: {employee.store_id}")
                return response
            
            # إذا البيانات غلط
            flash('بيانات الدخول غير صحيحة', 'danger')
            logger.warning(f"محاولة تسجيل دخول فاشلة للبريد: {email}")
            
        except Exception as e:
            db.session.rollback()
            flash('حدث خطأ أثناء تسجيل الدخول. يرجى المحاولة لاحقًا', 'danger')
            logger.error(f"خطأ في تسجيل الدخول: {str(e)}", exc_info=True)
    
    return render_template('auth/login.html', form=form)


# تسجيل حساب جديد
@user_auth_bp.route('/register', methods=['GET', 'POST'])
@redirect_if_authenticated
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data
        password = form.password.data
         
        with current_app.app_context():
            if User.query.filter_by(email=email).first():
                flash('البريد الإلكتروني مسجل مسبقاً', 'danger')
                return redirect(url_for('user_auth.register'))

            new_user = User(email=email)
            new_user.set_password(password)
            new_user.store_id = User.query.count() + 1  # Example: assign unique store_id

            # إذا كان هذا هو المستخدم الأول، اجعله مسؤولاً
            if User.query.count() == 0:
                new_user.is_admin = True

            # توليد كود تحقق (OTP)
            new_user.otp_code = str(random.randint(100000, 999999))
            new_user.otp_expiration = datetime.utcnow() + timedelta(minutes=10)

            db.session.add(new_user)
            db.session.commit()

            # إرسال الإيميل
            msg = Message("رمز التحقق من البريد", recipients=[email])
            msg.body = f"رمز التحقق الخاص بك هو: {new_user.otp_code}\nصالح لمدة 10 دقائق."
            mail.send(msg)


        flash('تم إنشاء الحساب! تحقق من بريدك وأدخل الرمز', 'info')
        return redirect(url_for('user_auth.verify_otp', user_id=new_user.id))
    
    return render_template('auth/register.html', form=form)


# التحقق من الكود
@user_auth_bp.route('/verify/<int:user_id>', methods=['GET', 'POST'])
def verify_otp(user_id):
    user = User.query.get_or_404(user_id)
    form = VerifyOTPForm()

    if form.validate_on_submit():
        if user.otp_code == form.otp_code.data and user.otp_expiration > datetime.utcnow():
            user.is_verified = True
            user.otp_code = None
            user.otp_expiration = None
            db.session.commit()
            flash('تم تفعيل حسابك بنجاح! يمكنك تسجيل الدخول الآن', 'success')
            return redirect(url_for('user_auth.login'))
        else:
            flash('رمز غير صحيح أو منتهي الصلاحية', 'danger')

    # Pass the user object to the template
    return render_template('auth/verify_otp.html', form=form, user=user)
@user_auth_bp.route('/resend_verification/<int:user_id>', methods=['POST'])
def resend_verification(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_verified:
        return {"success": False, "message": "الحساب مفعل بالفعل"}

    # توليد كود جديد
    user.otp_code = str(random.randint(100000, 999999))
    user.otp_expiration = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()

    try:
        # إرسال الإيميل
        msg = Message("رمز تحقق جديد", recipients=[user.email])
        msg.body = f"رمز التحقق الخاص بك هو: {user.otp_code}\nصالح لمدة 10 دقائق."
        mail.send(msg)
        return {"success": True, "message": "تم إرسال رمز جديد"}
    except Exception as e:
        current_app.logger.error(f"فشل إرسال البريد: {str(e)}")
        # إرجاع الرمز مباشرة في حالة فشل الإرسال
        return {
            "success": True, 
            "message": f"تم إنشاء الرمز: {user.otp_code}",
            "otp_code": user.otp_code
        }
@user_auth_bp.route('/logout')
def logout():
    response = make_response(redirect(url_for('user_auth.login')))
    response.delete_cookie('user_id')
    response.delete_cookie('is_admin')
    response.delete_cookie('employee_role')
    response.delete_cookie('store_id')
    response.delete_cookie('salla_access_token')
    response.delete_cookie('salla_refresh_token')
    flash('تم تسجيل الخروج بنجاح', 'success')
    return response
    