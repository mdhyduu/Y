from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, Email
from .models import db, User, Employee
from datetime import datetime
import logging
from functools import wraps

# تهيئة البلوبنت والسجل
user_auth_bp = Blueprint('user_auth', __name__, url_prefix='/auth')
logger = logging.getLogger(__name__)

# نموذج تسجيل الدخول
class LoginForm(FlaskForm):
    email = StringField('البريد الإلكتروني', validators=[DataRequired(), Email()])
    password = PasswordField('كلمة المرور', validators=[DataRequired()])

# نموذج التسجيل
class RegisterForm(FlaskForm):
    email = StringField('البريد الإلكتروني', validators=[DataRequired(), Email()])
    password = PasswordField('كلمة المرور', validators=[DataRequired()])
    confirm_password = PasswordField('تأكيد كلمة المرور', validators=[DataRequired()])
# ============= دوال المصادقة والتحكم =============

def auth_required(admin_only=False):
    """ديكوراتور للتحقق من تسجيل الدخول والصلاحيات"""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not session.get('user_id'):
                return redirect_to_login()
            
            if admin_only and not session.get('is_admin'):
                flash('صلاحيات غير كافية للوصول إلى هذه الصفحة', 'danger')
                return redirect(url_for('dashboard.index'))
                
            return view_func(*args, **kwargs)
        return wrapper
    return decorator

def redirect_if_authenticated(view_func):
    """تحويل المستخدم إذا كان مسجل الدخول بالفعل"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if session.get('user_id'):
            return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

def redirect_to_login():
    """تحويل إلى صفحة تسجيل الدخول مع تنظيف الجلسة"""
    clear_auth_session()
    flash('يجب تسجيل الدخول للوصول إلى هذه الصفحة', 'warning')
    return redirect(url_for('user_auth.login'))

def clear_auth_session():
    """مسح بيانات جلسة المستخدم"""
    session.clear()

def set_auth_session(user=None, employee=None):
    """تهيئة جلسة المستخدم بعد تسجيل الدخول"""
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

# ============= مسارات المصادقة =============

@user_auth_bp.route('/login', methods=['GET', 'POST'])
@redirect_if_authenticated
def login():
    """معالجة تسجيل الدخول"""
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data

        try:
            # محاولة تسجيل دخول مدير
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                set_auth_session(user=user)
                flash('تم تسجيل دخول المدير بنجاح', 'success')
                return redirect(url_for('dashboard.index'))

            # محاولة تسجيل دخول موظف
            employee = Employee.query.filter_by(email=email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('حساب الموظف غير مفعل', 'danger')
                    return redirect(url_for('user_auth.login'))

                set_auth_session(employee=employee)
                flash('تم تسجيل دخول الموظف بنجاح', 'success')
                return redirect(url_for('employee_dashboard.index'))

            flash('البريد الإلكتروني أو كلمة المرور غير صحيحة', 'danger')
            
        except Exception as e:
            db.session.rollback()
            logger.error(f"فشل تسجيل الدخول: {e}")
            flash('حدث خطأ أثناء محاولة تسجيل الدخول', 'danger')

    return render_template('auth/login.html', form=form)

@user_auth_bp.route('/register', methods=['GET', 'POST'])
@redirect_if_authenticated
def register():
    """معالجة إنشاء حساب جديد"""
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data

        try:
            if User.query.filter_by(email=email).first():
                flash('هذا البريد الإلكتروني مسجل بالفعل', 'danger')
                return redirect(url_for('user_auth.register'))

            new_user = User(email=email)
            new_user.set_password(password)
            
            # إذا كان أول مستخدم، يصبح مديراً تلقائياً
            if User.query.count() == 0:
                new_user.is_admin = True
            
            db.session.add(new_user)
            db.session.commit()
        
            flash('تم إنشاء الحساب بنجاح! يرجى تسجيل الدخول', 'success')
            return redirect(url_for('user_auth.login'))
        
        except Exception as e:
            db.session.rollback()
            logger.error(f"فشل إنشاء حساب: {e}")
            flash('حدث خطأ أثناء إنشاء الحساب', 'danger')

    return render_template('auth/register.html', form=form)

@user_auth_bp.route('/logout')
def logout():
    """معالجة تسجيل الخروج"""
    clear_auth_session()
    flash('تم تسجيل الخروج بنجاح', 'success')
    return redirect(url_for('user_auth.login'))