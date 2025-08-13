from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, session
from .models import User, Employee, OrderStatusNote, db
from datetime import datetime
from functools import wraps
from .user_auth import auth_required  # استيراد من user_auth بدلاً من التعريف المحلي

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

# ==============================================
# ديكوراتورات المصادقة المدمجة (بدون ملف منفصل)
# ==============================================


def admin_required(view_func):
    """ديكوراتور للتحقق من صلاحيات المدير"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get('is_admin'):
            flash('ليس لديك صلاحية الوصول', 'danger')
            return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

# ==============================================
# دوال المساعدة
# ==============================================

def get_current_user():
    """الحصول على المستخدم الحالي من الجلسة"""
    if 'user_id' not in session:
        return None 
    
    if session.get('is_admin'):
        return User.query.get(session['user_id'])
    return Employee.query.get(session['user_id'])

def redirect_to_login():
    """إعادة توجيه إلى صفحة تسجيل الدخول مع تنظيف الجلسة"""
    response = make_response(redirect(url_for('user_auth.login')))
    session.clear()
    return response

def get_order_stats(store_id):
    """إحصائيات الطلبات للمتجر"""
    return { 
        'new_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='new').count(),
        'late_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='late').count(),
        'missing_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='missing').count(),
        'refunded_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='refunded').count(),
        'not_shipped_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='not_shipped').count(),
    }

# ==============================================
# روابط لوحة التحكم
# ==============================================

@dashboard_bp.route('/')
@auth_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        current_user = get_current_user()
        if not current_user:
            return redirect_to_login()

        if isinstance(current_user, User):  # مدير
            return render_template('dashboard/admin.html',
                                current_user=current_user,
                                is_admin=True)
        
        # موظف
        store_admin = User.query.filter_by(store_id=current_user.store_id).first()
        
        if current_user.role in ('delivery', 'delivery_manager'):
            return render_template('dashboard/delivery.html',
                                current_user=store_admin,
                                is_delivery_manager=(current_user.role == 'delivery_manager'),
                                employee=current_user)
        
        # موظف عادي
        stats = get_order_stats(current_user.store_id)
        status_notes = OrderStatusNote.query.filter_by(
            store_id=current_user.store_id
        ).order_by(OrderStatusNote.created_at.desc()).limit(50).all()
        
        return render_template('dashboard/employee.html',
                            current_user=store_admin,
                            employee=current_user,
                            stats=stats,
                            status_notes=status_notes)

    except Exception as e:
        flash(f"خطأ في النظام: {str(e)}", "error")
        return redirect_to_login()

@dashboard_bp.route('/profile')
@auth_required
def profile():
    """صفحة الملف الشخصي"""
    current_user = get_current_user()
    if isinstance(current_user, User):
        return render_template('profile.html', user=current_user)
    return render_template('employee_profile.html', employee=current_user)

@dashboard_bp.route('/settings')
@admin_required
def settings():
    """صفحة الإعدادات (للمدراء فقط)"""
    current_user = get_current_user()
    return render_template('settings.html', user=current_user)