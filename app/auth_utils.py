# ملف app/auth_utils.py
from functools import wraps
from flask import session, request, current_app
from .models import User, Employee

def admin_required(view_func):
    """ديكوراتور للتحقق من صلاحيات المدير"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get('is_admin') and not request.cookies.get('is_admin') == 'true':
            flash('ليس لديك صلاحية الوصول', 'danger')
            return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

# ==============================================
# دوال المساعدة
# ==============================================

def get_current_user():
    """الحصول على المستخدم الحالي من الجلسة أو الكوكيز"""
    user_id = session.get('user_id') or request.cookies.get('user_id')
    
    if not user_id:
        return None
    
    is_admin = session.get('is_admin') or request.cookies.get('is_admin') == 'true'
    
    try:
        # تحويل user_id إلى عدد صحيح
        user_id = int(user_id)
        
        if is_admin:
            return User.query.get(user_id)
        return Employee.query.get(user_id)
    except (ValueError, Exception) as e:
        current_app.logger.error(f"Error getting current user: {str(e)}")
        return None