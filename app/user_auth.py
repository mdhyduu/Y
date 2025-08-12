from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
from sqlalchemy import func
import re
import logging
from datetime import timedelta
from functools import wraps
from .models import db, User, Employee

# Initialize Blueprint
user_auth_bp = Blueprint('user_auth', __name__)
logger = logging.getLogger(__name__)

# ==============================================
# Helper Functions and Decorators
# ==============================================

def redirect_if_authenticated(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # لا تتحقق من الكوكيز مباشرة، بل استخدم الدوال المخصصة للتحقق
        if 'user_type' in request.cookies:
            if request.cookies.get('user_type') == 'admin' and User.verify_remember_token(request.cookies.get('remember_token')):
                return redirect(url_for('dashboard.index'))
            elif request.cookies.get('user_type') == 'employee' and Employee.verify_remember_token(request.cookies.get('employee_token')):
                return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

def login_required(view_func):
    """Restrict access to authenticated users only"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # Check employee token
        emp_token = request.cookies.get('employee_token')
        if emp_token:
            employee = Employee.verify_remember_token(emp_token)
            if employee and employee.is_active:
                return view_func(*args, **kwargs)
        
        # Check admin token
        user_token = request.cookies.get('remember_token')
        if user_token:
            user = User.verify_remember_token(user_token)
            if user:
                return view_func(*args, **kwargs)
        
        flash('يجب تسجيل الدخول للوصول إلى هذه الصفحة', 'danger')
        return redirect(url_for('user_auth.login'))
    return wrapper

def validate_email(form, field):
    """Email validation"""
    if not re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', field.data):
        raise ValidationError('بريد إلكتروني غير صالح')

# ==============================================
# Forms
# ==============================================

class LoginForm(FlaskForm):
    email = StringField('البريد الإلكتروني', validators=[
        DataRequired(),
        validate_email
    ])
    password = PasswordField('كلمة المرور', validators=[
        DataRequired()
    ])

class RegisterForm(FlaskForm):
    email = StringField('البريد الإلكتروني', validators=[
        DataRequired(),
        validate_email
    ])
    password = PasswordField('كلمة المرور', validators=[
        DataRequired(),
        Length(min=8, message='يجب أن تكون كلمة المرور 8 أحرف على الأقل')
    ])
    confirm_password = PasswordField('تأكيد كلمة المرور', validators=[
        DataRequired(),
        EqualTo('password', message='كلمتا المرور غير متطابقتين')
    ])

# ==============================================
# Routes
# ==============================================
@user_auth_bp.route('/login', methods=['GET', 'POST'])
@redirect_if_authenticated
def login():
    form = LoginForm()
    
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data
        
        try:
            # تسجيل الدخول كمشرف
            user = User.query.filter(func.lower(User.email) == email).first()
            if user and user.check_password(password):
                response = make_response(redirect(url_for('dashboard.index')))
                
                # إعداد الكوكيز بشكل صارم
                response.set_cookie(
                    'remember_token',
                    user.generate_remember_token(),
                    secure=True,
                    httponly=True,
                    samesite='Lax',
                    max_age=timedelta(days=30).total_seconds(),
                    path='/'
                )
                response.set_cookie(
                    'user_type',
                    'admin',
                    secure=True,
                    httponly=True,
                    samesite='Lax',
                    max_age=timedelta(days=30).total_seconds(),
                    path='/'
                )
                
                flash('تم تسجيل دخول المشرف بنجاح', 'success')
                return response
            
            # تسجيل الدخول كموظف
            employee = Employee.query.filter(func.lower(Employee.email) == email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('حسابك معطل، يرجى التواصل مع المدير', 'danger')
                    return redirect(url_for('user_auth.login'))
                
                response = make_response(redirect(url_for('dashboard.index')))
                
                response.set_cookie(
                    'employee_token',
                    employee.generate_remember_token(),
                    secure=True,
                    httponly=True,
                    samesite='Lax',
                    max_age=timedelta(days=30).total_seconds(),
                    path='/'
                )
                response.set_cookie(
                    'user_type',
                    'employee',
                    secure=True,
                    httponly=True,
                    samesite='Lax',
                    max_age=timedelta(days=30).total_seconds(),
                    path='/'
                )
                
                flash('تم تسجيل دخول الموظف بنجاح', 'success')
                return response
            
            flash('بيانات الدخول غير صحيحة', 'danger')
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Login error: {str(e)}", exc_info=True)
            flash('حدث خطأ أثناء تسجيل الدخول', 'danger')
    
    return render_template('auth/login.html', form=form)
@user_auth_bp.route('/register', methods=['GET', 'POST'])
@redirect_if_authenticated
def register():
    form = RegisterForm()
    
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data
        
        try:
            if User.query.filter(func.lower(User.email) == email).first():
                flash('هذا البريد الإلكتروني مسجل بالفعل', 'danger')
                return redirect(url_for('user_auth.register'))
            
            new_user = User(email=email)
            new_user.set_password(password)
            
            # First user becomes admin
            if User.query.count() == 0:
                new_user.is_admin = True
            
            db.session.add(new_user)
            db.session.commit()
            
            flash('تم إنشاء الحساب بنجاح، يرجى تسجيل الدخول', 'success')
            return redirect(url_for('user_auth.login'))
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"Registration error: {str(e)}", exc_info=True)
            flash('حدث خطأ أثناء إنشاء الحساب', 'danger')
    
    return render_template('auth/register.html', form=form)

@user_auth_bp.route('/logout')
def logout():
    response = make_response(redirect(url_for('user_auth.login')))
    
    # Clear all auth cookies securely
    cookies_to_clear = [
        'remember_token', 'employee_token', 'user_type',
        'employee_role', 'salla_access_token', 'salla_refresh_token'
    ]
    
    for cookie in cookies_to_clear:
        response.delete_cookie(cookie)
    
    flash('تم تسجيل الخروج بنجاح', 'success')
    return response