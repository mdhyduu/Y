from flask import Blueprint, redirect, url_for, request, flash, make_response
import secrets
import logging
from .config import Config
from .models import db, User
from datetime import datetime, timedelta
from functools import wraps
from .token_utils import exchange_code_for_token, get_store_info, set_token_cookies

auth_bp = Blueprint('auth', __name__)
logger = logging.getLogger(__name__)

# ديكور لحماية الروابط من الوصول بدون تسجيل دخول
def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user_id = request.cookies.get('user_id')
        if not user_id:
            flash("الرجاء تسجيل الدخول أولاً", "error")
            return redirect(url_for('user_auth.login'))
        return view_func(*args, **kwargs)
    return wrapper

@auth_bp.route('/link_store')
@login_required
def link_store():
    
    user_id = request.cookies.get('user_id')
    user = User.query.get(user_id)
    
    # تحقق شامل من صحة التوكن
     
    # إعادة عملية الربط إذا كانت هناك مشكلة
    oauth_state = secrets.token_urlsafe(32)
    response = make_response(redirect(
        f"{Config.SALLA_AUTH_URL}?client_id={Config.SALLA_CLIENT_ID}&response_type=code&scope=offline_access%20orders.read&redirect_uri={Config.REDIRECT_URI}&state={oauth_state}"
    ))
    response.set_cookie('oauth_state', oauth_state, max_age=600, httponly=True, secure=True)
    response.delete_cookie('store_linked')  # مسح حالة الربط القديمة
    return response

@auth_bp.route('/callback')
@login_required
def callback():
    """معالجة رد سلة بعد المصادقة"""
    expected_state = request.cookies.get('oauth_state')
    state = request.args.get('state')

    if not state or state != expected_state:
        logger.error("عدم تطابق state")
        flash("خطأ أمان: عملية الربط غير صالحة", "error")
        return redirect(url_for('dashboard.index'))

    if 'error' in request.args:
        error_desc = request.args.get('error_description', 'لا يوجد وصف للخطأ')
        logger.error(f"خطأ من سلة: {error_desc}")
        flash(f"فشل المصادقة: {error_desc}", "error")
        return redirect(url_for('dashboard.index'))

    code = request.args.get('code')
    if not code:
        logger.error("لم يتم استلام رمز التفويض")
        flash("لم يتم استلام رمز التفويض", "error")
        return redirect(url_for('dashboard.index'))

    try:
        token_data = exchange_code_for_token(code)
        
        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in', 3600)

        if not access_token or not refresh_token:
            logger.error("توكنات غير صالحة")
            flash("لم يتم استلام التوكنات المطلوبة", "error")
            return redirect(url_for('dashboard.index'))

        user_id = request.cookies.get('user_id')
        user = User.query.get(user_id)
        if not user:
            logger.error(f"المستخدم غير موجود: {user_id}")
            response = make_response(redirect(url_for('user_auth.login')))
            response.delete_cookie('user_id')
            flash("خطأ في بيانات الحساب", "error")
            return response

        # حفظ التوكنات في قاعدة البيانات
        try:
            user.set_tokens(access_token, refresh_token)
            user.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            user.token_refreshed_at = datetime.utcnow()
            db.session.commit()
            logger.info("تم حفظ التوكنات بنجاح")
        except Exception as db_error:
            logger.error(f"خطأ في حفظ التوكنات: {str(db_error)}")
            db.session.rollback()
            flash("حدث خطأ أثناء حفظ بيانات المصادقة", "error")
            return redirect(url_for('dashboard.index'))

        # إنشاء الرد مع الكوكيز
        response = make_response(redirect(url_for('dashboard.index')))
        response = set_token_cookies(response, access_token, refresh_token, user.token_expires_at)

        # جلب معلومات المتجر
        try:
            store_info = get_store_info(access_token)
            if store_info:
                user.store_id = store_info.get('id', f"store_{user.id}")
                user.store_name = store_info.get('name', 'متجر سلة')
                db.session.commit()
                logger.info(f"تم ربط المتجر: {user.store_id} - {user.store_name}")
        except Exception as store_error:
            logger.error(f"خطأ في جلب معلومات المتجر: {str(store_error)}")

        flash("تم ربط متجرك بنجاح!", "success")
        return response

    except Exception as e:
        logger.error(f"خطأ في عملية المصادقة: {str(e)}")
        flash("حدث خطأ في عملية المصادقة", "error")
        return redirect(url_for('dashboard.index'))