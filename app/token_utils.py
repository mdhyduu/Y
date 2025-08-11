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
    try:
        if not user.salla_refresh_token:
            logger.error("❌ No refresh token available in database")
            return None

        refresh_token = user.get_refresh_token()
        if not refresh_token:
            logger.error("❌ Failed to decrypt refresh token for user ID %s", user.id)
            return None

        if len(refresh_token) < 20:
            logger.error("❌ Refresh token appears malformed: %s...", refresh_token[:10])
            return None

        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': refresh_token,
            'client_id': Config.SALLA_CLIENT_ID,
            'client_secret': Config.SALLA_CLIENT_SECRET,
            'redirect_uri': Config.REDIRECT_URI
        }

        headers = {
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json'
        }

        logger.info(f"🔄 Attempting to refresh token for user ID {user.id}")
        logger.debug(f"🔍 Request payload (without refresh_token): {{ { {k: v for k, v in payload.items() if k != 'refresh_token'} } }}")

        response = requests.post(
            Config.SALLA_TOKEN_URL,
            data=payload,
            headers=headers,
            timeout=15
        )

        logger.info(f"📡 Response Status: {response.status_code}")
        logger.debug(f"📦 Response Body: {response.text[:500]}...")

        if response.status_code == 200:
            tokens = response.json()
            new_access_token = tokens.get('access_token')
            new_refresh_token = tokens.get('refresh_token', refresh_token)
            expires_in = tokens.get('expires_in', 3600)

            # تحديث القيم في قاعدة البيانات
            user.salla_access_token = new_access_token
            user.set_refresh_token(new_refresh_token)  # تخزين مشفر
            user.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            db.session.commit()

            logger.info(f"✅ Token refreshed successfully for user ID {user.id}")
            logger.debug(f"🆕 New Access Token: {new_access_token[:15]}...")
            logger.debug(f"🆕 New Refresh Token: {new_refresh_token[:15]}...")
            logger.debug(f"⏳ Expires At: {user.token_expires_at}")

            return new_access_token

        # لو فشل التجديد
        error_data = response.json()
        logger.error(f"❌ Token refresh failed: {error_data}")
        if 'invalid_grant' in error_data.get('error', ''):
            logger.error(f"⚠ Refresh token possibly expired or revoked for user ID {user.id}")

        return None

    except Exception as e:
        logger.exception(f"💥 Unexpected token refresh error for user ID {user.id}: {str(e)}")
        return None