from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, g
from .models import User, Employee, OrderStatusNote, db
from datetime import datetime, timedelta
from functools import wraps
from sqlalchemy import func, case
from .user_auth import login_required
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')



@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        current_user = g.current_user
        
        if is_admin:
            return render_template('dashboard.html', 
                                current_user=current_user,
                                is_admin=True)
        
        else:
            # تحديد نوع لوحة التحكم حسب الدور
            if current_user.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (current_user.role == 'delivery_manager')
                return render_template('dashboard.html',
                                    current_user=current_user,
                                    is_delivery_manager=is_delivery_manager,
                                    employee=current_user)
            else:
                # استعلام واحد فعال للحصول على جميع الإحصائيات
                counts = db.session.query(
                    func.sum(case((OrderStatusNote.status_flag == 'late', 1), else_=0)).label('late'),
                    func.sum(case((OrderStatusNote.status_flag == 'missing', 1), else_=0)).label('missing'),
                    func.sum(case((OrderStatusNote.status_flag == 'refunded', 1), else_=0)).label('refunded'),
                    func.sum(case((OrderStatusNote.status_flag == 'not_shipped', 1), else_=0)).label('not_shipped')
                ).one()
                
                stats = {
                    'new_orders': 0,  # يمكن استبدالها ببيانات حقيقية
                    'late_orders': counts[0] or 0,
                    'missing_orders': counts[1] or 0,
                    'refunded_orders': counts[2] or 0,
                    'not_shipped_orders': counts[3] or 0,
                }
                
                # الحالات التي تحتاج متابعة (جميع الحالات المخصصة)
                custom_statuses = OrderStatusNote.query.order_by(OrderStatusNote.created_at.desc()).all()
                
                # آخر 5 حالات مضافة
                recent_statuses = OrderStatusNote.query.order_by(OrderStatusNote.created_at.desc()).limit(5).all()
                
                # تحسين جلب معلومات المستخدمين باستخدام استعلام واحد
                user_ids = {status.created_by for status in custom_statuses + recent_statuses}
                users = {user.id: user for user in User.query.filter(User.id.in_(user_ids)).all()}
                
                for status in custom_statuses + recent_statuses:
                    status.user = users.get(status.created_by)
                
                return render_template('employee_dashboard.html',
                                    current_user=current_user,
                                    employee=current_user,
                                    stats=stats,
                                    custom_statuses=custom_statuses,
                                    recent_statuses=recent_statuses)
    
    except Exception as e:
        flash(f"حدث خطأ في جلب بيانات لوحة التحكم: {str(e)}", "error")
        return redirect(url_for('user_auth.login'))

@dashboard_bp.route('/profile')
@login_required
def profile():
    """صفحة الملف الشخصي"""
    current_user = g.current_user
    
    if request.cookies.get('is_admin') == 'true':
        return render_template('profile.html', user=current_user)
    else:
        return render_template('employee_profile.html', employee=current_user)

@dashboard_bp.route('/settings')
@login_required
def settings():
    """صفحة الإعدادات"""
    if not request.cookies.get('is_admin') == 'true':
        flash('ليس لديك صلاحية الوصول إلى هذه الصفحة', 'danger')
        return redirect(url_for('dashboard.index'))
    
    return render_template('settings.html', user=g.current_user)

# إضافة دالة لإدارة جلسات قاعدة البيانات
@dashboard_bp.teardown_request
def teardown_request(exception=None):
    """إغلاق جلسة قاعدة البيانات عند انتهاء الطلب"""
    db.session.remove()