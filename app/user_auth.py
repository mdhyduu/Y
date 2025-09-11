from flask import Blueprint, render_template, redirect, url_for, flash, request, make_response, current_app
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length, ValidationError
import re
import logging
from .models import db, User, Employee
from datetime import datetime, timedelta
from functools import wraps
from flask_mail import Message
import random
from . import mail
user_auth_bp = Blueprint('user_auth', __name__)
logger = logging.getLogger(__name__)
# تسجيل الخروج
from flask import session, g  # g لتمرير المستخدم

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

# نموذج إدخال كود التحقق
class VerifyOTPForm(FlaskForm):
    otp_code = StringField('رمز التحقق', validators=[DataRequired(), Length(min=6, max=6)])
    submit = SubmitField('تأكيد')


# تسجيل الدخول
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
                # إنشاء كود تحقق جديد في كل مرة
                user.otp_code = str(random.randint(100000, 999999))
                user.otp_expiration = datetime.utcnow() + timedelta(minutes=10)
                db.session.commit()
                
                # إرسال الرمز الجديد
                try:
                    msg = Message(
                        subject="رمز التحقق لتسجيل الدخول",
                        recipients=[email],
                        body=f"رمز التحقق الخاص بك هو: {user.otp_code}\nصالح لمدة 10 دقائق."
                    )
                    mail.send(msg)
                    flash('تم إرسال رمز التحقق إلى بريدك الإلكتروني', 'info')
                except Exception as e:
                    logger.error(f"فشل إرسال البريد: {str(e)}")
                    flash(f'حدث خطأ في إرسال البريد. رمز التحقق هو: {user.otp_code}', 'warning')
                
                return redirect(url_for('user_auth.verify_otp', user_id=user.id))
            
            # تسجيل دخول كموظف
            employee = Employee.query.filter_by(email=email).first()
            if employee and employee.check_password(password):
                if not employee.is_active:
                    flash('حسابك موقوف. يرجى الاتصال بالإدارة', 'danger')
                    logger.warning(f"محاولة تسجيل دخول لحساب موقوف: {email}")
                    return redirect(url_for('user_auth.login'))
                
                # للموظفين أيضاً ننشئ كود تحقق
                employee.otp_code = str(random.randint(100000, 999999))
                employee.otp_expiration = datetime.utcnow() + timedelta(minutes=10)
                db.session.commit()
                
                # إرسال الرمز للموظف
                try:
                    msg = Message(
                        subject="رمز التحقق لتسجيل الدخول",
                        recipients=[email],
                        body=f"رمز التحقق الخاص بك هو: {employee.otp_code}\nصالح لمدة 10 دقائق."
                    )
                    mail.send(msg)
                    flash('تم إرسال رمز التحقق إلى بريدك الإلكتروني', 'info')
                except Exception as e:
                    logger.error(f"فشل إرسال البريد: {str(e)}")
                    flash(f'حدث خطأ في إرسال البريد. رمز التحقق هو: {employee.otp_code}', 'warning')
                
                return redirect(url_for('user_auth.verify_employee_otp', employee_id=employee.id))
            
            # إذا البيانات غير صحيحة
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
        
        # التحقق من وجود البريد الإلكتروني مسبقاً
        if User.query.filter_by(email=email).first():
            flash('البريد الإلكتروني مسجل مسبقاً', 'danger')
            return redirect(url_for('user_auth.register'))

        # إنشاء مستخدم جديد
        new_user = User(email=email)
        new_user.set_password(password)
        new_user.store_id = User.query.count() + 1

        if User.query.count() == 0:
            new_user.is_admin = True

        # إنشاء كود تحقق جديد في كل مرة
        new_user.otp_code = str(random.randint(100000, 999999))
        new_user.otp_expiration = datetime.utcnow() + timedelta(minutes=10)
        new_user.is_verified = False  # التأكد من أن الحساب غير مفعل حتى التحقق

        db.session.add(new_user)
        db.session.commit()

        # إرسال كود التحقق
        try:
            msg = Message(
                subject="رمز التحقق من البريد",
                recipients=[email],
                body=f"رمز التحقق الخاص بك هو: {new_user.otp_code}\nصالح لمدة 10 دقائق."
            )
            mail.send(msg)
            flash('تم إنشاء الحساب! تحقق من بريدك وأدخل الرمز', 'info')
        except Exception as e:
            logger.error(f"فشل إرسال البريد: {str(e)}")
            # في حالة فشل الإرسال، نعرض الرمز للمستخدم مباشرة (لأغراض التطوير فقط)
            flash(f'تم إنشاء الحساب! لكن حدث خطأ في إرسال البريد. رمز التحقق هو: {new_user.otp_code}', 'warning')
        
        return redirect(url_for('user_auth.verify_otp', user_id=new_user.id))
    
    return render_template('auth/register.html', form=form)
@user_auth_bp.route('/verify/<int:user_id>', methods=['GET', 'POST'])
def verify_otp(user_id):
    user = User.query.get_or_404(user_id)
    form = VerifyOTPForm()

    if form.validate_on_submit():
        if user.otp_code == form.otp_code.data and user.otp_expiration > datetime.utcnow():
            user.is_verified = True
            user.otp_code = None
            user.otp_expiration = None
            db.session.commit()
            
            # تسجيل دخول المستخدم مباشرة بعد التحقق
            response = make_response(redirect(url_for('dashboard.index')))
            response.set_cookie('user_id', str(user.id), max_age=timedelta(days=14).total_seconds(), httponly=True, secure=True)
            response.set_cookie('is_admin', 'true', max_age=timedelta(days=14).total_seconds())
            response.set_cookie('employee_role', '', max_age=timedelta(days=14).total_seconds())
            
            if user.salla_access_token:
                response.set_cookie('salla_access_token', user.get_access_token(), max_age=timedelta(days=14).total_seconds(), httponly=True, secure=True)
                response.set_cookie('salla_refresh_token', user.salla_refresh_token, max_age=timedelta(days=14).total_seconds(), httponly=True, secure=True)
            
            flash('تم تفعيل حسابك وتسجيل الدخول بنجاح!', 'success')
            logger.info(f"تم تفعيل وتسجيل دخول المستخدم: {user.email}")
            return response
        else:
            flash('رمز غير صحيح أو منتهي الصلاحية', 'danger')

    return render_template('auth/verify_otp.html', form=form, user=user)
@user_auth_bp.route('/resend_verification/<int:user_id>', methods=['POST'])
def resend_verification(user_id):
    user = User.query.get_or_404(user_id)
    if user.is_verified:
        return {"success": False, "message": "الحساب مفعل بالفعل"}

    user.otp_code = str(random.randint(100000, 999999))
    user.otp_expiration = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()

    try:
        msg = Message(
            subject="رمز تحقق جديد",
            recipients=[user.email],
            body=f"رمز التحقق الخاص بك هو: {user.otp_code}\nصالح لمدة 10 دقائق."
        )
        mail.send(msg)  # Use mail directly
        return {"success": True, "message": "تم إرسال رمز جديد"}
    except Exception as e:
        logger.error(f"فشل إعادة إرسال البريد: {str(e)}")
        return {"success": False, "message": f"فشل إرسال البريد: {str(e)}"}

@user_auth_bp.route('/logout')
def logout():
    # افتراض أن لديك طريقة للحصول على المستخدم الحالي قبل مسح الكوكيز
    # مثلاً من g object أو session
    user_id = request.cookies.get('user_id')
    is_admin = request.cookies.get('is_admin') == 'true'

    if user_id and is_admin:
        user = User.query.get(user_id)
        if user:
            # مسح التوكنات من قاعدة البيانات
            user._salla_access_token = None
            user._salla_refresh_token = None
            user.token_expires_at = None
            db.session.commit()
            logger.info(f"تم مسح توكنات سلة للمستخدم {user.email} عند الخروج.")

    session.clear()
    response = make_response(redirect(url_for('user_auth.login')))
    
    cookies_to_delete = [
        'user_id', 'is_admin', 'employee_role', 'store_id',
        'salla_access_token', 'salla_refresh_token',
        'token_expires_at', 'store_linked', 'oauth_state'
    ]
    
    for cookie_name in cookies_to_delete:
        response.delete_cookie(cookie_name, path='/')




        response.delete_cookie(cookie_name, path='/auth')  # إذا كانت محددة المسار
        response.delete_cookie(cookie_name, path='/orders') # إذا كانت محددة المسار
    
    # إضافة رأس لمنع التخزين المؤقت
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    
    flash('تم تسجيل الخروج بنجاح', 'success')
    return response
@user_auth_bp.before_request
def cleanup_old_cookies():
    # تحقق من وجود كوكيز كبيرة
    large_cookies = ['large_data_cookie', 'other_large_cookie']
    for cookie_name in large_cookies:
        if request.cookies.get(cookie_name):
            # إذا كان الكوكي كبيراً، احذفه
            response = make_response()
            response.delete_cookie(cookie_name, path='/')
            return response