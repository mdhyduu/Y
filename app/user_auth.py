from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
import re
import logging
from .models import db, User, Employee
from datetime import datetime, timedelta
from functools import wraps
from .email_utils import generate_verification_code, send_verification_email

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
                if not user.email_verified:
                    flash('يجب تأكيد بريدك الإلكتروني أولاً', 'warning')
                    response = make_response(redirect(url_for('user_auth.verify_email')))
                    response.set_cookie('temp_user_id', str(user.id), 
                                      max_age=timedelta(minutes=15).total_seconds(), 
                                      httponly=True, secure=False)
                    return response
    
    # باقي كود تسجيل الدخول...
                response = make_response(redirect(url_for('dashboard.index')))
                response.set_cookie('user_id', str(user.id), max_age=timedelta(days=30).total_seconds(), httponly=True, secure=False)
                response.set_cookie('is_admin', 'true', max_age=timedelta(days=30).total_seconds())  # تأكد من تعيينها إلى 'true'
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
@user_auth_bp.route('/register', methods=['GET', 'POST'])
@redirect_if_authenticated
def register():
    form = RegisterForm()
    if form.validate_on_submit():
        email = form.email.data
        password = form.password.data
        
        if User.query.filter_by(email=email).first():
            flash('البريد الإلكتروني مسجل مسبقاً', 'danger')
            return redirect(url_for('user_auth.register'))
        
        new_user = User(email=email)
        new_user.set_password(password)
        new_user.store_id = User.query.count() + 1
        
        # إنشاء وإرسال كود التحقق
        verification_code = generate_verification_code()
        new_user.verification_token = verification_code
        new_user.verification_token_expires = datetime.utcnow() + timedelta(minutes=15)
        
        if User.query.count() == 0:
            new_user.is_admin = True
        
        db.session.add(new_user)
        db.session.commit()
        
        # إرسال بريد التحقق
        if send_verification_email(email, verification_code):
            flash('تم إرسال كود التحقق إلى بريدك الإلكتروني', 'success')
            
            # تعيين كوكي مؤقت للتحقق
            response = make_response(redirect(url_for('user_auth.verify_email')))
            response.set_cookie('temp_user_id', str(new_user.id), 
                              max_age=timedelta(minutes=15).total_seconds(), 
                              httponly=True, secure=False)
            return response
        else:
            db.session.delete(new_user)
            db.session.commit()
            flash('فشل إرسال بريد التحقق. يرجى المحاولة لاحقاً', 'danger')
    
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
@user_auth_bp.route('/verify-email', methods=['GET', 'POST'])
def verify_email():
    user_id = request.cookies.get('temp_user_id')
    if not user_id:
        flash('يرجى التسجيل أولاً', 'danger')
        return redirect(url_for('user_auth.register'))
    
    user = User.query.get(user_id)
    if not user:
        flash('لم يتم العثور على المستخدم', 'danger')
        return redirect(url_for('user_auth.register'))
    
    if user.email_verified:
        flash('تم تأكيد بريدك الإلكتروني مسبقاً', 'info')
        return redirect(url_for('user_auth.login'))
    
    if request.method == 'POST':
        entered_code = request.form.get('verification_code')
        
        if entered_code == user.verification_token and user.verification_token_expires > datetime.utcnow():
            user.email_verified = True
            user.verification_token = None
            user.verification_token_expires = None
            db.session.commit()
            
            flash('تم تأكيد بريدك الإلكتروني بنجاح! يمكنك الآن تسجيل الدخول', 'success')
            response = make_response(redirect(url_for('user_auth.login')))
            response.delete_cookie('temp_user_id')
            return response
        else:
            flash('كود التحقق غير صحيح أو منتهي الصلاحية', 'danger')
    
    return render_template('auth/verify_email.html')

@user_auth_bp.route('/resend-verification', methods=['POST'])
def resend_verification():
    user_id = request.cookies.get('temp_user_id')
    if not user_id:
        return jsonify({'success': False, 'message': 'لم يتم العثور على المستخدم'})
    
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': 'المستخدم غير موجود'})
    
    if user.email_verified:
        return jsonify({'success': False, 'message': 'تم التحقق من البريد مسبقاً'})
    
    # إنشاء كود جديد
    verification_code = generate_verification_code()
    user.verification_token = verification_code
    user.verification_token_expires = datetime.utcnow() + timedelta(minutes=15)
    db.session.commit()
    
    # إرسال البريد الجديد
    if send_verification_email(user.email, verification_code):
        return jsonify({'success': True})
    else:
        return jsonify({'success': False, 'message': 'فشل إرسال البريد'})