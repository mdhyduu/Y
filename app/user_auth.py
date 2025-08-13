from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, current_app, session
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
        'secure': os.environ.get('FLASK_ENV') != 'development',
        'httponly': True,
        'samesite': 'Lax',
        'path': '/'
    }

# فلتر حماية يمنع الوصول إذا المستخدم مسجل دخول
def redirect_if_authenticated(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # التحقق من أن الاتصال آمن (HTTPS) في بيئة الإنتاج
        if not request.is_secure and current_app.env != 'development':
            logger.info("إعادة التوجيه إلى HTTPS")
            return redirect(request.url.replace('http://', 'https://'), code=301)
        
        # التحقق من الجلسة الحالية
        if 'user_id' in session and 'user_type' in session:
            logger.info(f"جلسة نشطة موجودة: user_id={session['user_id']}, user_type={session['user_type']}")
            return redirect(url_for('dashboard.index', _scheme='https'))
        
        # التحقق من الكوكيز (للتوافق مع الإصدارات القديمة)
        user_id = request.cookies.get('user_id')
        if user_id:
            logger.info(f"تم العثور على كوكي user_id: {user_id}")
            user = User.query.get(user_id)
            employee = Employee.query.get(user_id)
            
            if user or employee:
                logger.info("المستخدم مسجل دخول بالفعل، إعادة التوجيه إلى لوحة التحكم")
                return redirect(url_for('dashboard.index', _scheme='https'))
        
        logger.info("لا يوجد جلسة نشطة، السماح بالوصول إلى الصفحة")
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

def set_auth_session(user=None, employee=None):
    """تعيين بيانات الجلسة للمستخدم"""
    if user:
        session['user_id'] = user.id
        session['user_type'] = 'admin'
        session['email'] = user.email
        session['is_admin'] = user.is_admin
        session['store_id'] = user.store_id if hasattr(user, 'store_id') else None
        logger.info(f"تم تعيين جلسة المشرف: user_id={user.id}, email={user.email}")
    
    elif employee:
        session['user_id'] = employee.id
        session['user_type'] = 'employee'
        session['email'] = employee.email
        session['is_admin'] = False
        session['employee_role'] = employee.role
        session['store_id'] = employee.store_id
        logger.info(f"تم تعيين جلسة الموظف: user_id={employee.id}, email={employee.email}, role={employee.role}")

@user_auth_bp.route('/login', methods=['GET', 'POST'])
@redirect_if_authenticated
def login():
    form = LoginForm()
    
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data
        
        logger.info(f"محاولة تسجيل دخول بالبريد: {email}")
        
        try:
            # تنظيف الجلسة الحالية قبل تسجيل الدخول الجديد
            session.clear()
            logger.info("تم تنظيف الجلسة الحالية")
            
            # تسجيل دخول كمشرف
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                logger.info(f"تم التحقق من صحة مشرف: {email}")
                
                # تعيين الجلسة الجديدة
                set_auth_session(user=user)
                
                response = make_response(redirect(url_for('dashboard.index', _scheme='https')))
                
                # حذف أي كوكيز قديمة أولاً
                for cookie in ['user_id', 'is_admin', 'employee_role', 'store_id', 
                             'salla_access_token', 'salla_refresh_token']:
                    response.delete_cookie(cookie, path='/')
                
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
                
                logger.info(f"تم التحقق من صحة موظف: {email}")
                
                # تعيين الجلسة الجديدة
                set_auth_session(employee=employee)
                
                response = make_response(redirect(url_for('dashboard.index', _scheme='https')))
                
                # حذف أي كوكيز قديمة أولاً
                for cookie in ['user_id', 'is_admin', 'employee_role', 'store_id', 
                             'salla_access_token', 'salla_refresh_token']:
                    response.delete_cookie(cookie, path='/')
                
                flash('تم تسجيل دخول الموظف بنجاح!', 'success')
                logger.info(f"تم تسجيل دخول الموظف: {employee.email} - المتجر: {employee.store_id}")
                return response
            
            # إذا البيانات غير صحيحة
            flash('بيانات الدخول غير صحيحة', 'danger')
            logger.warning(f"محاولة تسجيل دخول فاشلة للبريد: {email}")
            
        except Exception as e:
            db.session.rollback()
            session.clear()
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
        
        logger.info(f"محاولة تسجيل حساب جديد: {email}")
         
        try:
            with current_app.app_context():
                if User.query.filter_by(email=email).first():
                    flash('البريد الإلكتروني مسجل مسبقاً', 'danger')
                    logger.warning(f"محاولة تسجيل بريد موجود مسبقًا: {email}")
                    return redirect(url_for('user_auth.register', _scheme='https'))
                
                new_user = User(email=email)
                new_user.set_password(password)
                
                # إذا كان هذا هو المستخدم الأول، اجعله مسؤولاً
                if User.query.count() == 0:
                    new_user.is_admin = True
                    logger.info("تم تعيين المستخدم الأول كمسؤول")
                
                db.session.add(new_user)
                db.session.commit()
            
            flash('تم إنشاء الحساب بنجاح! يرجى تسجيل الدخول', 'success')
            logger.info(f"تم إنشاء حساب جديد بنجاح: {email}")
            return redirect(url_for('user_auth.login', _scheme='https'))
        
        except Exception as e:
            db.session.rollback()
            flash('حدث خطأ أثناء إنشاء الحساب. يرجى المحاولة لاحقًا', 'danger')
            logger.error(f"خطأ في التسجيل: {str(e)}", exc_info=True)
            return redirect(url_for('user_auth.register', _scheme='https'))
    
    return render_template('auth/register.html', form=form)

@user_auth_bp.route('/logout')
def logout():
    logger.info(f"تسجيل الخروج: user_id={session.get('user_id')}, email={session.get('email')}")
    
    # تنظيف الجلسة
    session.clear()
    
    response = make_response(redirect(url_for('user_auth.login', _scheme='https')))
    cookie_settings = get_cookie_settings()
    
    # حذف جميع كوكيز المصادقة
    for cookie in ['user_id', 'is_admin', 'employee_role', 'store_id', 
                 'salla_access_token', 'salla_refresh_token']:
        response.delete_cookie(cookie, **cookie_settings)
    
    flash('تم تسجيل الخروج بنجاح', 'success')
    return response