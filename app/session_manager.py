from datetime import datetime, timedelta
from flask import session, current_app
from functools import wraps

def init_session_manager(app):
    @app.before_request
    def check_session():
        if 'user_id' in session:
            # التحقق من انتهاء صلاحية الجلسة
            last_active = session.get('last_active')
            if last_active and (datetime.now() - last_active) > timedelta(hours=2):
                session.clear()
                return redirect(url_for('user_auth.login'))
            
            session['last_active'] = datetime.now()

def session_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('user_auth.login'))
        return view_func(*args, **kwargs)
    return wrapper