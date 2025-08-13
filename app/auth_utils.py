# ملف app/auth_utils.py
from functools import wraps
from flask import session, redirect, url_for, flash
from .models import User, Employee



def admin_required(view_func):
    """ديكوراتور للتحقق من صلاحيات المدير"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get('is_admin'):
            flash('ليس لديك صلاحية الوصول', 'danger')
            return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

def get_current_user():
    """الحصول على المستخدم الحالي من الجلسة"""
    if 'user_id' not in session:
        return None
    
    if session.get('is_admin'):
        return User.query.get(session['user_id'])
    else:
        return Employee.query.get(session['user_id'])