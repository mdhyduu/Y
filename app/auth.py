from flask import Blueprint, redirect, url_for, session, request, flash
import requests
import secrets
import logging
from .config import Config
from .models import db, User
from datetime import datetime, timedelta
auth_bp = Blueprint('auth', __name__)

# إعداد السجلات
logger = logging.getLogger(__name__)

@auth_bp.route('/link_store')
def link_store():
    logger.info("بدء عملية ربط المتجر")
    if 'user_id' not in session:
        logger.warning("المستخدم غير مسجل الدخول، إعادة توجيه للصفحة الرئيسية")
        return redirect(url_for('user_auth.login'))
    
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
def callback():
    """معالجة رد سلة بعد المصادقة مع تحسينات الأمان والأداء"""
    # 1. التحقق من وجود المستخدم في الجلسة
    if 'user_id' not in session:
        logger.error("المستخدم غير مسجل الدخول")
        flash("الرجاء تسجيل الدخول أولاً", "error")
        return redirect(url_for('user_auth.login'))

    # 2. التحقق من state لمنع هجمات CSRF
    expected_state = session.get('oauth_state')
    state = request.args.get('state')
    
    if not state or state != expected_state:
        logger.error(f"عدم تطابق state - المتوقع: {expected_state}، المستلم: {state}")
        flash("خطأ أمان: عملية الربط غير صالحة", "error")
        return redirect(url_for('dashboard.index'))
    
    # حذف الـ state من الجلسة بعد التحقق الناجح
    session.pop('oauth_state', None)

    # 3. معالجة أخطاء سلة
    if 'error' in request.args:
        error_desc = request.args.get('error_description', 'لا يوجد وصف للخطأ')
        logger.error(f"خطأ من سلة: {request.args['error']} - {error_desc}")
        flash(f"فشل المصادقة: {error_desc}", "error")
        return redirect(url_for('dashboard.index'))

    # 4. الحصول على رمز التفويض
    code = request.args.get('code')
    if not code:
        logger.error("لم يتم استلام رمز التفويض")
        flash("فشل عملية الربط: لم يتم استلام رمز التفويض", "error")
        return redirect(url_for('dashboard.index'))

    # 5. طلب التوكن من سلة
    token_payload = {
        'grant_type': 'authorization_code',
        'client_id': Config.SALLA_CLIENT_ID,
        'client_secret': Config.SALLA_CLIENT_SECRET,
        'redirect_uri': Config.REDIRECT_URI,
        'code': code
    }

    headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Accept': 'application/json'
    }

    try:
        # 6. طلب الحصول على التوكن
        token_response = requests.post(
            Config.SALLA_TOKEN_URL,
            data=token_payload,
            headers=headers,
            timeout=15
        )
        token_response.raise_for_status()
        token_data = token_response.json()

        # 7. التحقق من وجود التوكنات
        access_token = token_data.get('access_token')
        refresh_token = token_data.get('refresh_token')
        expires_in = token_data.get('expires_in', 3600)  # افتراضي: ساعة واحدة

        if not access_token or not refresh_token:
            error_msg = token_data.get('error_description', 'توكنات غير صالحة')
            logger.error(f"استجابة غير متوقعة: {error_msg}")
            flash("فشل المصادقة: لم يتم استلام التوكنات المطلوبة", "error")
            return redirect(url_for('dashboard.index'))

        # 8. الحصول على بيانات المستخدم
        user = User.query.get(session['user_id'])
        if not user:
            logger.error(f"المستخدم غير موجود: {session['user_id']}")
            session.clear()
            flash("خطأ في بيانات الحساب", "error")
            return redirect(url_for('user_auth.login'))

        # 9. حفظ التوكنات في قاعدة البيانات
        try:
            user.set_tokens(access_token, refresh_token)
            user.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            user.token_refreshed_at = datetime.utcnow()
            db.session.commit()
        except Exception as db_error:
            logger.error(f"خطأ في حفظ التوكنات: {str(db_error)}")
            db.session.rollback()
            flash("حدث خطأ أثناء حفظ بيانات المصادقة", "error")
            return redirect(url_for('dashboard.index'))

        # 10. تحديث بيانات الجلسة
        session.update({
            'salla_access_token': access_token,
            'salla_refresh_token': refresh_token,
            'token_expires_at': user.token_expires_at.isoformat(),
            'store_linked': True
        })
        session.permanent = True  # جعل الجلسة دائمة
                # في callback function بعد الحصول على token_data
        try:
            user.set_tokens(access_token, refresh_token)
            user.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            user.token_refreshed_at = datetime.utcnow()
            db.session.commit()
            logger.info("تم حفظ التوكنات بنجاح في قاعدة البيانات")
        except Exception as db_error:
            logger.error(f"خطأ في حفظ التوكنات: {str(db_error)}")
            db.session.rollback()
            flash("حدث خطأ أثناء حفظ بيانات المصادقة", "error")
            return redirect(url_for('dashboard.index'))
        # 11. جلب معلومات المتجر (اختياري)
        try:
            store_info = get_store_info(access_token)
            if store_info:
                user.store_id = store_info.get('id', f"store_{user.id}")
                user.store_name = store_info.get('name', 'متجر سلة')
                db.session.commit()
                logger.info(f"تم ربط متجر سلة: {user.store_id} - {user.store_name}")
        except Exception as store_error:
            logger.error(f"خطأ في جلب معلومات المتجر: {str(store_error)}")
            # لا نوقف العملية إذا فشل جلب معلومات المتجر

        flash("تم ربط متجرك بنجاح!", "success")
        logger.info(f"تمت مصادقة المستخدم {user.id} مع متجر سلة")
        return redirect(url_for('dashboard.index'))

    except requests.exceptions.HTTPError as http_err:
        logger.error(f"خطأ HTTP: {http_err.response.status_code} - {http_err.response.text}")
        if http_err.response.status_code == 401:
            flash("انتهت صلاحية الجلسة، يرجى إعادة المحاولة", "error")
            return redirect(url_for('auth.link_store'))
        flash("حدث خطأ أثناء الاتصال بسلة", "error")
        return redirect(url_for('dashboard.index'))
 
    except requests.exceptions.RequestException as req_err:
        logger.error(f"خطأ في الاتصال: {str(req_err)}")
        flash("حدث خطأ في الاتصال بسلة، يرجى التحقق من اتصال الإنترنت", "error")
        return redirect(url_for('dashboard.index'))

    except Exception as e:
        logger.error(f"خطأ غير متوقع: {str(e)}", exc_info=True)
        flash("حدث خطأ غير متوقع أثناء المصادقة", "error")
        return redirect(url_for('dashboard.index'))
def get_store_info(access_token):
    """دالة مساعدة لجلب معلومات المتجر"""
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    response = requests.get(
        f"{Config.SALLA_BASE_URL}/store/info",
        headers=headers,
        timeout=10
    )
    response.raise_for_status()
    return response.json().get('data', {})
from .orders import refresh_salla_token  # استيراد الدالة من ملف orders

# في auth.py
def refresh_salla_token(user):
    """تجديد توكن الوصول لسلة باستخدام refresh token"""
    try:
        token_manager = TokenManager(user)
        
        # التحقق من وجود refresh token
        refresh_token = user.get_refresh_token()
        if not refresh_token:
            logger.error("No refresh token available for user")
            return None
            
        # طلب تجديد التوكن
        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': Config.SALLA_CLIENT_ID,
            'client_secret': Config.SALLA_CLIENT_SECRET,
            'redirect_uri': Config.REDIRECT_URI
        }
        
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        
        response = requests.post(
            Config.SALLA_TOKEN_URL,
            data=payload,
            headers=headers,
            timeout=10
        )
        
        if response.status_code == 200:
            token_data = response.json()
            
            # حفظ التوكنات الجديدة
            user.set_tokens(
                token_data['access_token'],
                token_data.get('refresh_token', refresh_token),
                token_data.get('expires_in', 3600)
            )
            
            logger.info(f"تم تجديد التوكن للمستخدم {user.id}")
            return token_data['access_token']
        
        # معالجة الأخطاء
        error_data = response.json()
        logger.error(f"فشل تجديد التوكن: {response.status_code} - {error_data}")
        
        if response.status_code == 400 and error_data.get('error') == 'invalid_grant':
            # مسح التوكنات القديمة إذا كان refresh token غير صالح
            user.salla_access_token = None
            user.salla_refresh_token = None
            db.session.commit()
            logger.warning("تم مسح التوكنات القديمة بسبب refresh token غير صالح")
    
    except Exception as e:
        logger.error(f"خطأ في تجديد التوكن: {str(e)}", exc_info=True)
    
    return None