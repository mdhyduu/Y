from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, Email
from .models import db, User, Employee
from datetime import datetime
import logging

user_auth_bp = Blueprint('user_auth', __name__, url_prefix='/auth')
logger = logging.getLogger(__name__)

class LoginForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])

def auth_required(admin_only=False):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not session.get('user_id'):
                return redirect_to_login()
                
            if admin_only and not session.get('is_admin'):
                flash('ليس لديك صلاحية الوصول', 'danger')
                return redirect(url_for('dashboard.index'))
                
            return view_func(*args, **kwargs)
        return wrapper
    return decorator

def redirect_if_authenticated(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if session.get('user_id'):
            return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

def redirect_to_login():
    clear_auth_session()
    flash('يجب تسجيل الدخول أولاً', 'warning')
    return redirect(url_for('user_auth.login'))

def clear_auth_session():
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

@user_auth_bp.route('/login', methods=['GET', 'POST'])
@redirect_if_authenticated
def login():
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data

        try:
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                set_auth_session(user=user)
                flash('تم تسجيل الدخول بنجاح', 'success')
                return redirect(url_for('dashboard.index'))

            employee = Employee.query.filter_by(email=email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('الحساب غير نشط', 'danger')
                    return redirect(url_for('user_auth.login'))

                set_auth_session(employee=employee)
                flash('تم تسجيل الدخول بنجاح', 'success')
                return redirect(url_for('employee_dashboard.index'))

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
    clear_auth_session()
    flash('تم تسجيل الخروج بنجاح', 'success')
    return redirect(url_for('user_auth.login'))