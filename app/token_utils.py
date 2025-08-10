# token_utils.py
from .config import Config
import requests
from datetime import datetime, timedelta

def refresh_salla_token(user, db, logger):
    """دالة مستقلة لتجديد التوكن"""
    try:
        if not user.salla_refresh_token:
            return None
            
        payload = {
            'grant_type': 'refresh_token',
            'refresh_token': user.get_refresh_token(),
            'client_id': Config.SALLA_CLIENT_ID,
            'client_secret': Config.SALLA_CLIENT_SECRET,
            'redirect_uri': Config.REDIRECT_URI
        }
        
        response = requests.post(
            Config.SALLA_TOKEN_URL,
            data=payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10
        )
        
        if response.status_code == 200:
            token_data = response.json()
            user.set_tokens(
                token_data['access_token'],
                token_data.get('refresh_token', user.salla_refresh_token),
                token_data.get('expires_in', 3600)
            )
            db.session.commit()
            return token_data['access_token']
        
        error_data = response.json()
        logger.error(f"فشل تجديد التوكن: {error_data.get('error')}")
        
        if response.status_code == 400 and error_data.get('error') == 'invalid_grant':
            user.clear_tokens()
            db.session.commit()
            
    except Exception as e:
        logger.error(f"خطأ غير متوقع في تجديد التوكن: {str(e)}")
        db.session.rollback()
    
    return None
