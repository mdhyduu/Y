from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
import re
import logging
from .models import db, User, Employee
from datetime import datetime, timedelta
from functools import wraps

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
                response = make_response(redirect(url_for('dashboard.index')))
                
                # إعداد الكوكيز مع Secure و HttpOnly و SameSite
                cookie_settings = {
                    'max_age': int(timedelta(days=30).total_seconds(),
                    'httponly': True,
                    'secure': True,
                    'samesite': 'Lax'
                }
                
                response.set_cookie('user_id', str(user.id), **cookie_settings)
                response.set_cookie('is_admin', 'true' if user.is_admin else 'false', **cookie_settings)
                response.set_cookie('employee_role', '', **cookie_settings)
                
                if user.salla_access_token:
                    response.set_cookie('salla_access_token', user.get_access_token(), **cookie_settings)
                    response.set_cookie('salla_refresh_token', user.salla_refresh_token, **cookie_settings)
                
                flash('تم تسجيل دخول المشرف بنجاح!', 'success')
                logger.info(f"تم تسجيل دخول المشرف: {user.email}")
                return response
            
            # تسجيل دخول كموظف
            employee = Employee.query.filter_by(email=email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('حسابك موقوف. يرجى الاتصال بالإدارة', 'danger')
                    logger.warning(f"محاولة تسجيل دخول لحساب موقوف: {email}")
                    return redirect(url_for('user_auth.login'))
                
                response = make_response(redirect(url_for('dashboard.index')))
                
                # نفس إعدادات الكوكيز للموظف
                cookie_settings = {
                    'max_age': int(timedelta(days=30).total_seconds()),
                    'httponly': True,
                    'secure': True,
                    'samesite': 'Lax'
                }
                
                response.set_cookie('user_id', str(employee.id), **cookie_settings)
                response.set_cookie('is_admin', 'false', **cookie_settings)
                response.set_cookie('employee_role', employee.role, **cookie_settings)
                response.set_cookie('store_id', str(employee.store_id), **cookie_settings)
                
                store_admin = User.query.filter_by(store_id=employee.store_id).first()
                if store_admin and store_admin.salla_access_token:
                    response.set_cookie('salla_access_token', store_admin.get_access_token(), **cookie_settings)
                    response.set_cookie('salla_refresh_token', store_admin.get_refresh_token(), **cookie_settings)
                
                flash('تم تسجيل دخول الموظف بنجاح!', 'success')
                logger.info(f"تم تسجيل دخول الموظف: {employee.email} - المتجر: {employee.store_id}")
                return response
            
            # إذا كانت بيانات الدخول غير صحيحة
            flash('بيانات الدخول غير صحيحة', 'danger')
            logger.warning(f"محاولة تسجيل دخول فاشلة للبريد: {email}")
            
        except Exception as e:
            db.session.rollback()
            flash('حدث خطأ أثناء تسجيل الدخول. يرجى المحاولة لاحقًا', 'danger')
            logger.error(f"خطأ في تسجيل الدخول: {str(e)}", exc_info=True)
    
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
    response.delete_cookie('user_id')
    response.delete_cookie('is_admin')
    response.delete_cookie('employee_role')
    response.delete_cookie('store_id')
    response.delete_cookie('salla_access_token')
    response.delete_cookie('salla_refresh_token')
    flash('تم تسجيل الخروج بنجاح', 'success')
    return response