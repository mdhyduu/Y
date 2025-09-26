# token_utils.py
import requests
from datetime import datetime, timedelta
from .config import Config
import logging
from .models import db, User
from flask import current_app

logger = logging.getLogger(__name__)

def exchange_code_for_token(code):
    """تبادل رمز التفويض للحصول على التوكنات"""
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
        return token_response.json()
    except requests.exceptions.RequestException as req_err:
        logger.error(f"خطأ في الاتصال بسلة: {str(req_err)}")
        raise

def get_store_info(access_token):
    """جلب معلومات المتجر"""
    response = requests.get(
        f"{Config.SALLA_BASE_URL}/store/info",
        headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'},
        timeout=10
    )
    response.raise_for_status()
    return response.json().get('data', {})

def set_token_cookies(response, access_token, refresh_token, expires_at):
    """تعيين كوكيز التوكنات في الرد"""
    response.set_cookie('salla_access_token', access_token, 
                      max_age=timedelta(days=30).total_seconds(), 
                      httponly=True, secure=True)
    response.set_cookie('salla_refresh_token', refresh_token, 
                       max_age=timedelta(days=30).total_seconds(), 
                       httponly=True, secure=True)
    response.set_cookie('token_expires_at', expires_at.isoformat(), 
                       max_age=timedelta(days=30).total_seconds())
    response.set_cookie('store_linked', 'true', 
                       max_age=timedelta(days=30).total_seconds())
    return response

def refresh_salla_token(user):
    """تجديد توكن الوصول باستخدام توكن التحديث"""
    try:
        if not user or not user.salla_refresh_token:
            logger.error("❌ لا يوجد مستخدم أو توكن تحديث")
            return None
            
        refresh_token = user.salla_refresh_token
        if not refresh_token:
            logger.error("❌ لا يوجد توكن تحديث للمستخدم %s", user.id)
            return None
        
        logger.info("🔄 محاولة تجديد التوكن للمستخدم %s", user.id)
        
        # إعداد بيانات الطلب باستخدام Config مباشرة
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': Config.SALLA_CLIENT_ID,
            'client_secret': Config.SALLA_CLIENT_SECRET
        }
        
        logger.debug("📤 إرسال طلب تجديد التوكن إلى: %s", Config.SALLA_TOKEN_URL)
        
        # إرسال طلب التجديد
        response = requests.post(
            Config.SALLA_TOKEN_URL,
            data=data,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded', 
                'Accept': 'application/json'
            },
            timeout=30
        )
        
        logger.info("📥 استجابة تجديد التوكن: %s", response.status_code)
        
        if response.status_code != 200:
            error_msg = f"❌ فشل تجديد التوكن: {response.status_code} - {response.text}"
            logger.error(error_msg)
            
            # إذا كان الخطأ بسبب توكن التحديث غير الصالح
            if response.status_code == 400:
                error_data = response.json()
                if error_data.get('error') == 'invalid_grant':
                    logger.error("🚫 توكن التحديث غير صالح أو منتهي للمستخدم %s", user.id)
                    # إزالة التوكنات من قاعدة البيانات
                    user._salla_access_token = None
                    user._salla_refresh_token = None
                    user.token_expires_at = None
                    db.session.commit()
                    logger.info("✅ تم إزالة التوكنات المنتهية")
                    
            return None
        
        token_data = response.json()
        logger.debug("✅ تم تجديد التوكن بنجاح: %s", token_data.keys())
        
        # التحقق من وجود البيانات المطلوبة
        if not all(key in token_data for key in ['access_token', 'refresh_token', 'expires_in']):
            logger.error("❌ بيانات التوكن غير مكتملة: %s", token_data.keys())
            return None
        
        # تحديث التوكنات في قاعدة البيانات
        success = user.set_tokens(
            access_token=token_data['access_token'],
            refresh_token=token_data['refresh_token'],
            expires_in=token_data['expires_in']
        )
        
        if success:
            logger.info("✅ تم حفظ التوكنات الجديدة للمستخدم %s", user.id)
            return token_data['access_token']
        else:
            logger.error("❌ فشل في حفظ التوكنات الجديدة للمستخدم %s", user.id)
            return None
            
    except requests.exceptions.Timeout:
        logger.error("⏰ انتهت مهلة تجديد التوكن")
        return None
    except requests.exceptions.ConnectionError:
        logger.error("🌐 خطأ في الاتصال خلال تجديد التوكن")
        return None
    except Exception as e:
        logger.error("❌ خطأ غير متوقع في تجديد التوكن: %s", str(e), exc_info=True)
        return None

def verify_token_validity(access_token):
    """التحقق من صلاحية توكن الوصول"""
    try:
        response = requests.get(
            f"{Config.SALLA_BASE_URL}/store/info",
            headers={
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            },
            timeout=10
        )
        return response.status_code == 200
    except Exception as e:
        logger.error("❌ خطأ في التحقق من صلاحية التوكن: %s", str(e))
        return False