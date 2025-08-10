from flask import Blueprint, redirect, url_for, session, request, flash
import requests
import secrets
import logging
from .config import Config
from .models import db, User
from datetime import datetime, timedelta
from functools import wraps

auth_bp = Blueprint('auth', __name__)
logger = logging.getLogger(__name__)

# ديكور لحماية الروابط من الوصول بدون تسجيل دخول
def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            flash("الرجاء تسجيل الدخول أولاً", "error")
            return redirect(url_for('user_auth.login'))
        return view_func(*args, **kwargs)
    return wrapper
@auth_bp.route('/link_store')
@login_required
def link_store():
    logger.info("بدء عملية ربط المتجر")
    
    user = User.query.get(session['user_id'])
    if user and user.salla_access_token and user.token_expires_at > datetime.utcnow():
        session['store_linked'] = True  # تحديث حالة الجلسة
        return redirect(url_for('orders.index'))  # توجيه مباشر
    
    if session.get('store_linked'):
        flash("يوجد مشكلة في توكن الربط، يرجى إعادة الربط", "warning")
    
    oauth_state = secrets.token_urlsafe(32)
    session['oauth_state'] = oauth_state
    
    auth_url = (
        f"{Config.SALLA_AUTH_URL}?"
        f"client_id={Config.SALLA_CLIENT_ID}&"
        f"response_type=code&"
        f"scope=offline_access%20orders.read&"
        f"redirect_uri={Config.REDIRECT_URI}&"
        f"state={oauth_state}"
    )
    
    logger.info(f"إعادة توجيه إلى عنوان سلة: {auth_url}")
    return redirect(auth_url)
@auth_bp.route('/callback')
@login_required
def callback():
    """معالجة رد سلة بعد المصادقة"""
    expected_state = session.pop('oauth_state', None)
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

    token_payload = {
        'grant_type': 'authorization_code',
        'client_id': Config.SALLA_CLIENT_ID,
        'client_secret': Config.SALLA_CLIENT_SECRET,
        'redirect_uri': Config.REDIRECT_URI,
        'code': code
    }

    try:
        token_response = requests.post(
            Config.SALLA_TOKEN_URL,
            data=token_payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'},
            timeout=15
        )
        token_response.raise_for_status()
        token_data = token_response.json()

        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in', 3600)

        if not access_token or not refresh_token:
            logger.error("توكنات غير صالحة")
            flash("لم يتم استلام التوكنات المطلوبة", "error")
            return redirect(url_for('dashboard.index'))

        user = User.query.get(session['user_id'])
        if not user:
            logger.error(f"المستخدم غير موجود: {session['user_id']}")
            session.clear()
            flash("خطأ في بيانات الحساب", "error")
            return redirect(url_for('user_auth.login'))

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

        # تحديث بيانات الجلسة
        session.update({
            'salla_access_token': access_token,
            'salla_refresh_token': refresh_token,
            'token_expires_at': user.token_expires_at.isoformat(),
            'store_linked': True
        })
        session.permanent = True

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
        return redirect(url_for('dashboard.index'))

    except requests.exceptions.RequestException as req_err:
        logger.error(f"خطأ في الاتصال بسلة: {str(req_err)}")
        flash("حدث خطأ في الاتصال بسلة", "error")
        return redirect(url_for('dashboard.index'))

def get_store_info(access_token):
    """جلب معلومات المتجر"""
    response = requests.get(
        f"{Config.SALLA_BASE_URL}/store/info",
        headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'},
        timeout=10
    )
    response.raise_for_status()
    return response.json().get('data', {})
