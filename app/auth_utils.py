from functools import wraps
from flask import session, redirect, url_for, flash, current_app
from .models import User, Employee

def admin_required(view_func):
    """ديكوراتور للتحقق من صلاحيات المدير"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get('is_admin'):
            flash('ليس لديك صلاحية الوصول إلى هذه الصفحة', 'danger')
            return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

def get_current_user():
    """
    الحصول على المستخدم الحالي من الجلسة
    يعيد كائن User إذا كان مدير، أو Employee إذا كان موظف
    """
    user_id = session.get('user_id')
    if not user_id:
        return None

    is_admin = session.get('is_admin', False)
    
    try:
        if is_admin:
            return User.query.get(user_id)
        return Employee.query.get(user_id)
    except Exception as e:
        current_app.logger.error(f"خطأ في جلب بيانات المستخدم: {e}")
        return None