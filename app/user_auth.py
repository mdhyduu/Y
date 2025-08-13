# Add this at the very top of user_auth.py
# Add these imports at the top
import logging
from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, Email
from flask import session  # أضف هذا مع بقية الاستيرادات
# Initialize logger

# ... rest of your routes and logic ...
# ... rest of your imports ...
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, Email
from .models import db, User, Employee
from datetime import datetime, timedelta
from functools import wraps
from .forms import RegisterForm  # If defined in forms.py
import os
import re 
user_auth_bp = Blueprint('user_auth', __name__, url_prefix='/auth')

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Define LoginForm within the file
class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])


# ... (بقية الكود كما هو)
def get_cookie_settings():
    return {
        'secure': os.environ.get('FLASK_ENV') == 'production',
        'httponly': True,
        'samesite': 'Strict',
        'path': '/'
    }

# ديكور التحقق من المصادقة المحسّن
def auth_required(admin_only=False):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            user_id = session.get('user_id') or request.cookies.get('user_id')
            
            if not user_id:
                return redirect_to_login()

            is_admin = session.get('is_admin') or request.cookies.get('is_admin') == 'true'
            
            if admin_only and not is_admin:
                flash('ليس لديك صلاحية الوصول', 'danger')
                return redirect(url_for('dashboard.index'))

            # التحقق من صحة المستخدم في قاعدة البيانات
            user = None
            if is_admin:
                user = User.query.get(user_id)
            else:
                user = Employee.query.get(user_id)

            if not user:
                return redirect_to_login()

            request.current_user = user
            return view_func(*args, **kwargs)
        return wrapper
    return decorator
def redirect_if_authenticated(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # First check if already logged in via session
        if 'user_id' in session:
            return redirect(url_for('dashboard.index'))
            
        # Then check cookies only if no session
        user_id = request.cookies.get('user_id')
        if not user_id:
            return view_func(*args, **kwargs)
            
        # Verify user in database
        is_admin = request.cookies.get('is_admin') == 'true'
        current_user = None
        
        try:
            if is_admin:
                current_user = User.query.get(user_id)
            else:
                current_user = Employee.query.get(user_id)
            
            if current_user:
                # Set session to avoid future cookie checks
                set_auth_session(user=current_user if is_admin else None, 
                               employee=None if is_admin else current_user)
                return redirect(url_for('dashboard.index'))
        except Exception as e:
            logger.error(f"Error verifying user: {str(e)}")
        
        return view_func(*args, **kwargs)
    return wrapper
def redirect_to_login():
    response = make_response(redirect(url_for('user_auth.login')))
    clear_auth_cookies(response)
    flash('يجب تسجيل الدخول أولاً', 'warning')
    return response

def clear_auth_cookies(response):
    cookies = ['user_id', 'is_admin', 'employee_role', 'store_id', 
              'salla_access_token', 'salla_refresh_token']
    for cookie in cookies:
        response.delete_cookie(cookie, **get_cookie_settings())
    session.clear()

def set_auth_session(user=None, employee=None):
    session.clear()
    if user:
        session['user_id'] = user.id
        session['is_admin'] = True
        session['store_id'] = user.store_id
    elif employee:
        session['user_id'] = employee.id
        session['is_admin'] = False
        session['employee_role'] = employee.role
        session['store_id'] = employee.store_id

def set_auth_cookies(response, user=None, employee=None):
    clear_auth_cookies(response)
    cookie_settings = get_cookie_settings()
    expires = int((datetime.now() + timedelta(days=1)).timestamp())

    if user:
        response.set_cookie('user_id', str(user.id), expires=expires, **cookie_settings)
        response.set_cookie('is_admin', 'true', expires=expires, **cookie_settings)
        response.set_cookie('store_id', str(user.store_id), expires=expires, **cookie_settings)
    elif employee:
        response.set_cookie('user_id', str(employee.id), expires=expires, **cookie_settings)
        response.set_cookie('is_admin', 'false', expires=expires, **cookie_settings)
        response.set_cookie('employee_role', employee.role, expires=expires, **cookie_settings)
        response.set_cookie('store_id', str(employee.store_id), expires=expires, **cookie_settings)

    return response

@user_auth_bp.route('/login', methods=['GET', 'POST'])
@redirect_if_authenticated
def login():
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data

        try:
            # محاولة تسجيل الدخول كمشرف
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                response = make_response(redirect(url_for('dashboard.index', _scheme='https')))
                set_auth_session(user=user)
                set_auth_cookies(response, user=user)
                flash('تم تسجيل الدخول بنجاح', 'success')
                return response

            # محاولة تسجيل الدخول كموظف
            employee = Employee.query.filter_by(email=email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('الحساب غير نشط', 'danger')
                    return redirect(url_for('user_auth.login'))

                response = make_response(redirect(url_for('employee_dashboard.index')))
                set_auth_session(employee=employee)
                set_auth_cookies(response, employee=employee)
                flash('تم تسجيل الدخول بنجاح', 'success')
                return response

            flash('بيانات الدخول غير صحيحة', 'danger')
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"خطأ في تسجيل الدخول: {str(e)}")
            flash('حدث خطأ أثناء تسجيل الدخول', 'danger')

    return render_template('auth/login.html', form=form)
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
            
            # إذا كان هذا هو المستخدم الأول، اجعله مسؤولاً
            if User.query.count() == 0:
                new_user.is_admin = True
            
            db.session.add(new_user)
            db.session.commit()
        
        flash('تم إنشاء الحساب بنجاح! يرجى تسجيل الدخول', 'success')
        return redirect(url_for('user_auth.login'))
    
    return render_template('auth/register.html', form=form)
@user_auth_bp.route('/logout')
def logout():
    response = make_response(redirect(url_for('user_auth.login')))
    clear_auth_cookies(response)
    flash('تم تسجيل الخروج بنجاح', 'success')
    return response