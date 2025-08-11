# token_manager.py
import logging
import time
import jwt
import requests
from functools import lru_cache
from datetime import datetime, timedelta
from flask import make_response, redirect, url_for, flash
from .models import db, User
from .config import Config
from .exceptions import TokenRefreshFailed

logger = logging.getLogger(__name__)

class TokenManager:
    @staticmethod
    @lru_cache(maxsize=100)
    def get_valid_token(user_id: int) -> str:
        """استرجاع توكن صالح من الذاكرة المؤقتة"""
        try:
            user = User.query.get(user_id)
            if not user:
                raise TokenRefreshFailed("User not found")
    
            if TokenManager.is_token_valid(user.salla_access_token):
                return user.salla_access_token
    
            try:
                return TokenManager.refresh_user_token(user)
            except TokenRefreshFailed as e:
                if getattr(e, 'code', None) == 'reauth_required':
                    # إعادة تعيين الذاكرة المؤقتة لهذا المستخدم
                    TokenManager.get_valid_token.cache_clear()
                raise
    
        except Exception as e:
            logger.error(f"Failed to get valid token for user {user_id}: {str(e)}")
            raise

    @staticmethod
    def is_token_valid(token: str) -> bool:
        """تحقق من صلاحية التوكن بدون اتصال بالخادم"""
        try:
            payload = jwt.decode(token, options={"verify_signature": False})
            expiry = payload.get('exp')
            return expiry and expiry > time.time()
        except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
            logger.debug(f"Invalid token: {str(e)}")
            return False

        
    @staticmethod
    def refresh_user_token(user: User) -> str:
        """تجديد توكن المستخدم مع معالجة متقدمة للأخطاء"""
        try:
            if not user.salla_refresh_token:
                logger.error("No refresh token available for user %s", user.id)
                raise TokenRefreshFailed("No refresh token available", 'reauth_required')
    
            refresh_token = user.get_refresh_token()
            if not refresh_token:
                logger.error("Failed to decrypt refresh token for user %s", user.id)
                # حذف التوكنات القديمة
                user.salla_access_token = None
                user.salla_refresh_token = None
                db.session.commit()
                raise TokenRefreshFailed("Failed to decrypt refresh token", 'reauth_required')
    
            payload = {
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
                'client_id': Config.SALLA_CLIENT_ID,
                'client_secret': Config.SALLA_CLIENT_SECRET,
                'redirect_uri': Config.REDIRECT_URI
            }
            
            headers = {'Content-Type': 'application/x-www-form-urlencoded'}
            
            try:
                response = requests.post(
                    Config.SALLA_TOKEN_URL,
                    data=payload,
                    headers=headers,
                    timeout=10
                )
                
                if response.status_code == 200:
                    token_data = response.json()
                    user.set_tokens(
                        token_data['access_token'],
                        token_data.get('refresh_token', refresh_token)
                    )
                    db.session.commit()
                    logger.info("Token refreshed successfully for user %s", user.id)
                    return token_data['access_token']
                
                error_data = response.json()
                logger.error(f"Token refresh failed: {response.status_code} - {error_data}")
                
                if response.status_code == 400 and error_data.get('error') == 'invalid_grant':
                    user.salla_access_token = None
                    user.salla_refresh_token = None
                    db.session.commit()
                    logger.warning("Cleared invalid tokens for user %s", user.id)
                    raise TokenRefreshFailed("Session expired, please re-authenticate", 'reauth_required')
                
                raise TokenRefreshFailed(f"API error: {error_data.get('error', 'unknown_error')}")
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Network error during token refresh: {str(e)}")
                raise TokenRefreshFailed(f"Network error: {str(e)}")
                
        except Exception as e:
            logger.exception("Unexpected error during token refresh")
            raise TokenRefreshFailed("Internal server error")
    @staticmethod
    def validate_request_token(request):
        """تحقق شامل من التوكن للطلبات الواردة"""
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        return TokenManager.is_token_valid(token)

    @staticmethod
    def exchange_code_for_token(code: str):
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
        except requests.exceptions.RequestException as e:
            logger.error(f"Error exchanging code for tokens: {str(e)}")
            raise TokenRefreshFailed(f"Failed to exchange code: {str(e)}")

    @staticmethod
    def save_user_tokens(user: User, token_data: dict):
        """حفظ التوكنات مع تحسينات الأمان والتسجيل"""
        try:
            access_token = token_data.get('access_token')
            refresh_token = token_data.get('refresh_token')
            
            if not access_token or not refresh_token:
                raise ValueError("Missing tokens in token_data")
            
            # تسجيل القيم قبل التشفير (لأغراض debugging فقط)
            logger.debug(f"Access Token (Raw): {access_token[:15]}...")
            logger.debug(f"Refresh Token (Raw): {refresh_token[:15]}...")
            
            # حفظ التوكنات
            user.set_tokens(access_token, refresh_token)
            
            # تحديث وقت الصلاحية
            expires_in = token_data.get('expires_in', 3600)
            user.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            user.token_refreshed_at = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"تم حفظ التوكنات للمستخدم {user.id} بنجاح")
            
            # التحقق من إمكانية استرجاع التوكنات
            decrypted_refresh = user.get_refresh_token()
            if not decrypted_refresh:
                raise ValueError("Failed to decrypt saved refresh token")
                
            return True
        except Exception as e:
            db.session.rollback()
            logger.error(f"فشل حفظ التوكنات: {str(e)}", exc_info=True)
            raise TokenRefreshFailed(f"Failed to save tokens: {str(e)}")

    @staticmethod
    def create_auth_response(user: User, redirect_url: str = None):
        """إنشاء استجابة مصادقة مع الكوكيز"""
        if not redirect_url:
            redirect_url = url_for('dashboard.index')

        access_token = user.get_access_token()
        refresh_token = user.get_refresh_token()
        response = make_response(redirect(redirect_url))

        response.set_cookie('salla_access_token', access_token, 
                          max_age=timedelta(days=30).total_seconds(), 
                          httponly=True, secure=True)
        response.set_cookie('salla_refresh_token', refresh_token, 
                          max_age=timedelta(days=30).total_seconds(), 
                          httponly=True, secure=True)
        response.set_cookie('token_expires_at', user.token_expires_at.isoformat(), 
                          max_age=timedelta(days=30).total_seconds())
        response.set_cookie('store_linked', 'true', 
                          max_age=timedelta(days=30).total_seconds())

        return response

    @staticmethod
    def get_store_info(access_token: str):
        """جلب معلومات المتجر"""
        try:
            response = requests.get(
                f"{Config.SALLA_BASE_URL}/store/info",
                headers={'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'},
                timeout=10
            )
            response.raise_for_status()
            return response.json().get('data', {})
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching store info: {str(e)}")
            return None