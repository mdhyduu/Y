from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, g
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
import re
import logging
import json
from datetime import datetime, timedelta
from functools import wraps
from cryptography.fernet import Fernet, InvalidToken
from .models import db, User, Employee

user_auth_bp = Blueprint('user_auth', __name__)
logger = logging.getLogger(__name__)

# فلتر يمنع الوصول إذا المستخدم مسجل دخول
def redirect_if_authenticated(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if request.headers.get('Authorization') or request.args.get('token'):
            return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

# نموذج تسجيل الدخول
class LoginForm(FlaskForm):
    email = StringField('البريد الإلكتروني', validators=[DataRequired()])
    password = PasswordField('كلمة المرور', validators=[DataRequired()])

# دالة إنشاء توكن الجلسة
def create_session_token(user):
    try:
        fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
        auth_data = {
            'user_id': user.id,
            'is_admin': getattr(user, 'is_admin', False),
            'email': user.email,
            'role': getattr(user, 'role', None),
            'store_id': getattr(user, 'store_id', None),
            'exp': (datetime.utcnow() + timedelta(days=30)).timestamp(),
            'iat': datetime.utcnow().timestamp()
        }
        return fernet.encrypt(json.dumps(auth_data).encode('utf-8')).decode('utf-8')
    except Exception as e:
        logger.error(f"فشل إنشاء توكن الجلسة: {str(e)}")
        raise

@user_auth_bp.route('/login', methods=['GET', 'POST'])
@redirect_if_authenticated
def login():
    form = LoginForm()
    
    if form.validate_on_submit():
        email = form.email.data.lower()
        password = form.password.data
        
        try:
            # محاولة تسجيل الدخول كمشرف
            user = User.query.filter_by(email=email).first()
            if user and user.check_password(password):
                return handle_successful_login(user, is_admin=True)
            
            # محاولة تسجيل الدخول كموظف
            employee = Employee.query.filter_by(email=email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('حسابك موقوف. يرجى الاتصال بالإدارة', 'danger')
                    logger.warning(f"محاولة تسجيل دخول لحساب موقوف: {email}")
                    return redirect(url_for('user_auth.login'))
                return handle_successful_login(employee, is_admin=False)
            
            # إذا فشل تسجيل الدخول
            flash('بيانات الدخول غير صحيحة', 'danger')
            logger.warning(f"محاولة تسجيل دخول فاشلة للبريد: {email}")
            
        except Exception as e:
            db.session.rollback()
            flash('حدث خطأ أثناء تسجيل الدخول. يرجى المحاولة لاحقًا', 'danger')
            logger.error(f"خطأ في تسجيل الدخول: {str(e)}", exc_info=True)
    
    return render_template('auth/login.html', form=form)

def handle_successful_login(user, is_admin):
    """معالجة تسجيل الدخول الناجح"""
    try:
        # إنشاء توكن الجلسة
        session_token = create_session_token(user)
        
        # إعداد بيانات الجلسة
        auth_data = {
            'token': session_token,
            'user_info': {
                'id': user.id,
                'email': user.email,
                'is_admin': is_admin,
                'role': getattr(user, 'role', None),
                'store_id': getattr(user, 'store_id', None)
            }
        }
        
        # إذا كان مشرفاً ولديه توكن سلة
        salla_token = None
        if is_admin and hasattr(user, 'salla_access_token') and user.salla_access_token:
            salla_token = user.salla_access_token
        
        # إذا كان موظفاً، نحصل على توكن سلة من مشرف المتجر
        elif not is_admin and hasattr(user, 'store_id'):
            store_admin = User.query.filter_by(store_id=user.store_id).first()
            if store_admin and store_admin.salla_access_token:
                salla_token = store_admin.salla_access_token
        
        # تسجيل معلومات الدخول
        user_type = "مشرف" if is_admin else "موظف"
        logger.info(f"تم تسجيل دخول {user_type}: {user.email}")
        
        # إرجاع صفحة نجاح التسجيل مع البيانات
        return render_template('auth/login_success.html',
                            auth_data=json.dumps(auth_data),
                            salla_token=salla_token)
    
    except Exception as e:
        db.session.rollback()
        flash('حدث خطأ أثناء تسجيل الدخول. يرجى المحاولة لاحقًا', 'danger')
        logger.error(f"خطأ في معالجة تسجيل الدخول: {str(e)}", exc_info=True)
        return redirect(url_for('user_auth.login'))

# دالة للتحقق من صحة التوكن
def token_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        token = None
        
        # الحصول على التوكن من رؤوس الطلب أو الباراميترات
        if 'Authorization' in request.headers:
            token = request.headers['Authorization'].split()[1]
        elif 'token' in request.args:
            token = request.args['token']
        
        if not token:
            return jsonify({'error': 'التوكن مطلوب'}), 401
        
        try:
            # فك تشفير التوكن
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            decrypted = fernet.decrypt(token.encode('utf-8')).decode('utf-8')
            token_data = json.loads(decrypted)
            
            # التحقق من صلاحية التوكن
            if datetime.utcnow().timestamp() > token_data['exp']:
                return jsonify({'error': 'انتهت صلاحية الجلسة'}), 401
            
            # تخزين بيانات المستخدم في g للوصول في الدوال الأخرى
            g.current_user = token_data
            
        except InvalidToken:
            return jsonify({'error': 'توكن غير صالح'}), 401
        except Exception as e:
            return jsonify({'error': 'خطأ في المصادقة'}), 401
        
        return view_func(*args, **kwargs)
    return wrapper

@user_auth_bp.route('/logout')
@token_required
def logout():
    # يمكنك هنا إضافة أي منطق لإبطال التوكن في السيرفر إذا لزم الأمر
    return jsonify({
        'success': True,
        'message': 'قم بحذف التوكن من localStorage في الواجهة الأمامية'
    })