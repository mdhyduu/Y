from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, session
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField  # تم إضافة BooleanField هنا
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
import re
import logging
from .models import db, User, Employee
from datetime import datetime, timedelta
from functools import wraps

user_auth_bp = Blueprint('user_auth', __name__)
logger = logging.getLogger(__name__)

def redirect_if_authenticated(view_func):
    """ديكوراتور يمنع الوصول إذا كان المستخدم مسجل دخول بالفعل"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # التحقق من الجلسة أولاً
        if 'user_id' in session:
            user_id = session['user_id']
            user_type = session.get('user_type')
            
            if user_type == 'admin':
                user = User.query.get(user_id)
                if user:
                    return redirect(url_for('dashboard.index'))
            elif user_type == 'employee':
                employee = Employee.query.get(user_id)
                if employee and employee.is_active:
                    return redirect(url_for('dashboard.index'))
        
        # التحقق من الكوكيز للتوافق مع الإصدارات القديمة
        user_id = request.cookies.get('user_id')
        if user_id and user_id.isdigit():
            user_id = int(user_id)
            user = User.query.get(user_id)
            employee = Employee.query.get(user_id)
            
            if user:
                return redirect(url_for('dashboard.index'))
            if employee and employee.is_active:
                return redirect(url_for('dashboard.index'))
        
        return view_func(*args, **kwargs)
    return wrapper

def validate_email(form, field):
    """التحقق من صحة البريد الإلكتروني"""
    email_regex = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    if not re.match(email_regex, field.data):
        raise ValidationError('يجب إدخال بريد إلكتروني صالح')

class LoginForm(FlaskForm):
    """نموذج تسجيل الدخول"""
    email = StringField('البريد الإلكتروني', validators=[DataRequired(), validate_email])
    password = PasswordField('كلمة المرور', validators=[DataRequired()])
    remember_me = BooleanField('تذكرني')  # الآن BooleanField معرّف بشكل صحيح

class RegisterForm(FlaskForm):
    """نموذج التسجيل"""
    email = StringField('البريد الإلكتروني', validators=[DataRequired(), validate_email])
    password = PasswordField('كلمة المرور', 
                           validators=[DataRequired(), 
                                      Length(min=8, message='يجب أن تكون كلمة المرور 8 أحرف على الأقل')])
    confirm_password = PasswordField('تأكيد كلمة المرور', 
                                   validators=[DataRequired(), 
                                              EqualTo('password', message='كلمتا المرور غير متطابقتين')])

def set_auth_session(user=None, employee=None):
    """تعيين بيانات الجلسة للمستخدم"""
    session.clear()
    
    if user:
        session['user_id'] = user.id
        session['user_type'] = 'admin'
        session['is_admin'] = user.is_admin
        session['email'] = user.email
        session['store_id'] = user.store_id
        
        if user.salla_access_token:
            session['salla_access_token'] = user.get_access_token()
            session['salla_refresh_token'] = user.salla_refresh_token
    
    elif employee:
        session['user_id'] = employee.id
        session['user_type'] = 'employee'
        session['is_admin'] = False
        session['email'] = employee.email
        session['employee_role'] = employee.role
        session['store_id'] = employee.store_id
        
        store_admin = User.query.filter_by(store_id=employee.store_id).first()
        if store_admin and store_admin.salla_access_token:
            session['salla_access_token'] = store_admin.get_access_token()
            session['salla_refresh_token'] = store_admin.get_refresh_token()

def set_auth_cookies(response, user=None, employee=None):
    """تعيين كوكيز المصادقة (للتوافق مع الإصدارات القديمة)"""
    # حذف الكوكيز القديمة أولاً
    cookies_to_delete = [
        'user_id', 'is_admin', 'employee_role',
        'store_id', 'salla_access_token', 'salla_refresh_token',
        'remember_token'
    ]
    
    for cookie in cookies_to_delete:
        response.delete_cookie(cookie, path='/')
    
    if user:
        response.set_cookie(
            'user_id', 
            str(user.id), 
            max_age=timedelta(days=30).total_seconds(), 
            httponly=True, 
            secure=True,
            path='/',
            samesite='Lax'
        )
        response.set_cookie(
            'is_admin', 
            'true' if user.is_admin else 'false', 
            max_age=timedelta(days=30).total_seconds(),
            path='/',
            samesite='Lax'
        )
        
        if user.salla_access_token:
            response.set_cookie(
                'salla_access_token', 
                user.get_access_token(), 
                max_age=timedelta(days=30).total_seconds(), 
                httponly=True, 
                secure=True,
                path='/',
                samesite='Lax'
            )
    
    elif employee:
        response.set_cookie(
            'user_id', 
            str(employee.id), 
            max_age=timedelta(days=30).total_seconds(), 
            httponly=True, 
            secure=True,
            path='/',
            samesite='Lax'
        )
        response.set_cookie(
            'employee_role', 
            employee.role, 
            max_age=timedelta(days=30).total_seconds(),
            path='/',
            samesite='Lax'
        )
        response.set_cookie(
            'store_id', 
            str(employee.store_id), 
            max_age=timedelta(days=30).total_seconds(),
            path='/',
            samesite='Lax'
        )
        
        store_admin = User.query.filter_by(store_id=employee.store_id).first()
        if store_admin and store_admin.salla_access_token:
            response.set_cookie(
                'salla_access_token', 
                store_admin.get_access_token(), 
                max_age=timedelta(days=30).total_seconds(), 
                httponly=True, 
                secure=True,
                path='/',
                samesite='Lax'
            )
    
    return response

@user_auth_bp.route('/login', methods=['GET', 'POST'])
@redirect_if_authenticated
def login():
    form = LoginForm()
    
    if form.validate_on_submit():
        email = form.email.data.lower().strip()
        password = form.password.data
        remember_me = form.remember_me.data
        
        try:
            # محاولة تسجيل الدخول كمشرف
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                set_auth_session(user=user)
                response = make_response(redirect(url_for('dashboard.index')))
                
                if remember_me:
                    response = set_auth_cookies(response, user=user)
                    user.generate_remember_token()
                    db.session.commit()
                
                flash('تم تسجيل دخول المشرف بنجاح!', 'success')
                logger.info(f"تم تسجيل دخول المشرف: {user.email}")
                return response
            
            # محاولة تسجيل الدخول كموظف
            employee = Employee.query.filter_by(email=email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('حسابك موقوف. يرجى الاتصال بالإدارة', 'danger')
                    logger.warning(f"محاولة تسجيل دخول لحساب موقوف: {email}")
                    return redirect(url_for('user_auth.login'))
                
                set_auth_session(employee=employee)
                response = make_response(redirect(url_for('dashboard.index')))
                
                if remember_me:
                    response = set_auth_cookies(response, employee=employee)
                    employee.generate_remember_token()
                    db.session.commit()
                
                flash('تم تسجيل دخول الموظف بنجاح!', 'success')
                logger.info(f"تم تسجيل دخول الموظف: {employee.email}")
                return response
            
            # إذا فشل تسجيل الدخول
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
        email = form.email.data.lower().strip()
        password = form.password.data
        
        try:
            # التحقق من عدم وجود البريد مسبقاً
            if User.query.filter_by(email=email).first() or Employee.query.filter_by(email=email).first():
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
        
        except Exception as e:
            db.session.rollback()
            flash('حدث خطأ أثناء إنشاء الحساب. يرجى المحاولة لاحقًا', 'danger')
            logger.error(f"خطأ في التسجيل: {str(e)}", exc_info=True)
    
    return render_template('auth/register.html', form=form)

@user_auth_bp.route('/logout')
def logout():
    # حذف بيانات الجلسة
    session.clear()
    
    # إنشاء رد وإعداد حذف الكوكيز
    response = make_response(redirect(url_for('user_auth.login')))
    
    # حذف جميع الكوكيز
    cookies_to_delete = [
        'user_id', 'is_admin', 'employee_role',
        'store_id', 'salla_access_token', 'salla_refresh_token',
        'remember_token'
    ]
    
    for cookie in cookies_to_delete:
        response.delete_cookie(cookie, path='/')
    
    flash('تم تسجيل الخروج بنجاح', 'success')
    return response