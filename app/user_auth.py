from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
import re
import logging
from .models import db, User, Employee
from datetime import datetime, timedelta
from functools import wraps
import os

user_auth_bp = Blueprint('user_auth', __name__)
logger = logging.getLogger(__name__)

# إعدادات الأمان للكوكيز
def get_cookie_settings():
    """إرجاع إعدادات الكوكيز بناءً على بيئة التشغيل"""
    return {
        'secure': os.environ.get('FLASK_ENV') != 'development',  # تفعيل في الإنتاج فقط
        'httponly': True,
        'samesite': 'None',
        'path': '/'
    }

# فلتر حماية يمنع الوصول إذا المستخدم مسجل دخول
def redirect_if_authenticated(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # التحقق من أن الاتصال آمن (HTTPS) في بيئة الإنتاج
        if not request.is_secure and current_app.env != 'development':
            return redirect(request.url.replace('http://', 'https://'), code=301)
        
        user_id = request.cookies.get('user_id')
        if user_id:
            user = User.query.get(user_id)
            employee = Employee.query.get(user_id)
            
            if user or employee:
                return redirect(url_for('dashboard.index', _scheme='https'))
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

def set_auth_cookies(response, user=None, employee=None):
    """دالة مساعدة لتعيين كوكيز المصادقة بشكل آمن"""
    cookie_settings = get_cookie_settings()
    
    if user:
        response.set_cookie(
            'user_id', 
            str(user.id), 
            max_age=timedelta(days=30).total_seconds(),
            **cookie_settings
        )
        response.set_cookie(
            'is_admin', 
            'true' if user.is_admin else 'false', 
            max_age=timedelta(days=30).total_seconds(),
            **cookie_settings
        )
        response.set_cookie(
            'employee_role', 
            '', 
            max_age=timedelta(days=30).total_seconds(),
            **cookie_settings
        )
        
        if user.salla_access_token:
            response.set_cookie(
                'salla_access_token', 
                user.get_access_token(), 
                max_age=timedelta(days=30).total_seconds(),
                **cookie_settings
            )
            response.set_cookie(
                'salla_refresh_token', 
                user.salla_refresh_token, 
                max_age=timedelta(days=30).total_seconds(),
                **cookie_settings
            )
    
    elif employee:
        response.set_cookie(
            'user_id', 
            str(employee.id), 
            max_age=timedelta(days=30).total_seconds(),
            **cookie_settings
        )
        response.set_cookie(
            'is_admin', 
            'false', 
            max_age=timedelta(days=30).total_seconds(),
            **cookie_settings
        )
        response.set_cookie(
            'employee_role', 
            employee.role, 
            max_age=timedelta(days=30).total_seconds(),
            **cookie_settings
        )
        response.set_cookie(
            'store_id', 
            str(employee.store_id), 
            max_age=timedelta(days=30).total_seconds(),
            **cookie_settings
        )
        
        store_admin = User.query.filter_by(store_id=employee.store_id).first()
        if store_admin and store_admin.salla_access_token:
            response.set_cookie(
                'salla_access_token', 
                store_admin.get_access_token(), 
                max_age=timedelta(days=30).total_seconds(),
                **cookie_settings
            )
            response.set_cookie(
                'salla_refresh_token', 
                store_admin.get_refresh_token(), 
                max_age=timedelta(days=30).total_seconds(),
                **cookie_settings
            )
    
    return response

@user_auth_bp.route('/login', methods=['GET', 'POST'])
@redirect_if_authenticated
def login():
    form = LoginForm()
    
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data
        
        try:
            # تسجيل دخول كمشرف
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                response = make_response(redirect(url_for('dashboard.index', _scheme='https')))
                # حذف أي كوكيز قديمة أولاً
                for cookie in ['user_id', 'is_admin', 'employee_role', 'store_id', 
                             'salla_access_token', 'salla_refresh_token']:
                    response.delete_cookie(cookie, path='/')
                
                # تعيين الكوكيز الجديدة
                response = set_auth_cookies(response, user=user)
                
                flash('تم تسجيل دخول المشرف بنجاح!', 'success')
                logger.info(f"تم تسجيل دخول المشرف: {user.email}")
                return response
            
            # تسجيل دخول كموظف
            employee = Employee.query.filter_by(email=email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('حسابك موقوف. يرجى الاتصال بالإدارة', 'danger')
                    logger.warning(f"محاولة تسجيل دخول لحساب موقوف: {email}")
                    return redirect(url_for('user_auth.login', _scheme='https'))
                
                response = make_response(redirect(url_for('dashboard.index', _scheme='https')))
                # حذف أي كوكيز قديمة أولاً
                for cookie in ['user_id', 'is_admin', 'employee_role', 'store_id', 
                             'salla_access_token', 'salla_refresh_token']:
                    response.delete_cookie(cookie, path='/')
                
                # تعيين الكوكيز الجديدة
                response = set_auth_cookies(response, employee=employee)
                
                flash('تم تسجيل دخول الموظف بنجاح!', 'success')
                logger.info(f"تم تسجيل دخول الموظف: {employee.email} - المتجر: {employee.store_id}")
                return response
            
            # إذا البيانات غير صحيحة
            flash('بيانات الدخول غير صحيحة', 'danger')
            logger.warning(f"محاولة تسجيل دخول فاشلة للبريد: {email}")
            
        except Exception as e:
            db.session.rollback()
            flash('حدث خطأ أثناء تسجيل الدخول. يرجى المحاولة لاحقًا', 'danger')
            logger.error(f"خطأ في تسجيل الدخول: {str(e)}", exc_info=True)
            return redirect(url_for('user_auth.login', _scheme='https'))
    
    return render_template('auth/login.html', form=form)

@user_auth_bp.route('/register', methods=['GET', 'POST'])
@redirect_if_authenticated
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data
         
        try:
            with current_app.app_context():
                if User.query.filter_by(email=email).first():
                    flash('البريد الإلكتروني مسجل مسبقاً', 'danger')
                    return redirect(url_for('user_auth.register', _scheme='https'))
                
                new_user = User(email=email)
                new_user.set_password(password)
                
                # إذا كان هذا هو المستخدم الأول، اجعله مسؤولاً
                if User.query.count() == 0:
                    new_user.is_admin = True
                
                db.session.add(new_user)
                db.session.commit()
            
            flash('تم إنشاء الحساب بنجاح! يرجى تسجيل الدخول', 'success')
            return redirect(url_for('user_auth.login', _scheme='https'))
        
        except Exception as e:
            db.session.rollback()
            flash('حدث خطأ أثناء إنشاء الحساب. يرجى المحاولة لاحقًا', 'danger')
            logger.error(f"خطأ في التسجيل: {str(e)}", exc_info=True)
            return redirect(url_for('user_auth.register', _scheme='https'))
    
    return render_template('auth/register.html', form=form)

@user_auth_bp.route('/logout')
def logout():
    response = make_response(redirect(url_for('user_auth.login', _scheme='https')))
    cookie_settings = get_cookie_settings()
    
    # حذف جميع كوكيز المصادقة
    for cookie in ['user_id', 'is_admin', 'employee_role', 'store_id', 
                 'salla_access_token', 'salla_refresh_token']:
        response.delete_cookie(cookie, **cookie_settings)
    
    flash('تم تسجيل الخروج بنجاح', 'success')
    return response