from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from .models import User, Employee, OrderStatusNote, db
from functools import wraps

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            flash('يجب تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('user_auth.login'))
        
        user_id = session['user_id']
        is_admin = session.get('is_admin', False)
        
        if is_admin:
            user = User.query.get(user_id)
            if not user:
                session.clear()
                return redirect(url_for('user_auth.login'))
            request.current_user = user
        else:
            employee = Employee.query.get(user_id)
            if not employee:
                flash('بيانات الموظف غير موجودة', 'error')
                session.clear()
                return redirect(url_for('user_auth.login'))
            request.current_user = employee
        
        return view_func(*args, **kwargs)
    return wrapper

@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        is_admin = session.get('is_admin', False)
        user_id = session['user_id']
        
        if is_admin:
            user = User.query.get(user_id)
            if not user:
                session.clear()
                return redirect(url_for('user_auth.login'))
                
            return render_template('dashboard.html', 
                                current_user=user,
                                is_admin=True)
        
        else:
            employee = Employee.query.get(user_id)
            if not employee:
                flash('بيانات الموظف غير موجودة', 'error')
                session.clear()
                return redirect(url_for('user_auth.login'))
            
            user = User.query.filter_by(store_id=employee.store_id).first()
            
            if employee.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (employee.role == 'delivery_manager')
                return render_template('dashboard.html',
                                    current_user=user,
                                    is_delivery_manager=is_delivery_manager,
                                    employee=employee)
            else:
                stats = {
                    'new_orders': 0,
                    'late_orders': OrderStatusNote.query.filter_by(status_flag='late').count(),
                    'missing_orders': OrderStatusNote.query.filter_by(status_flag='missing').count(),
                    'refunded_orders': OrderStatusNote.query.filter_by(status_flag='refunded').count(),
                    'not_shipped_orders': OrderStatusNote.query.filter_by(status_flag='not_shipped').count(),
                }
                
                custom_statuses = OrderStatusNote.query.order_by(OrderStatusNote.created_at.desc()).all()
                recent_statuses = OrderStatusNote.query.order_by(OrderStatusNote.created_at.desc()).limit(5).all()
                
                for status in custom_statuses + recent_statuses:
                    status.user = User.query.get(status.created_by)
                
                return render_template('employee_dashboard.html',
                                    current_user=user,
                                    employee=employee,
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
    is_admin = session.get('is_admin', False)
    user_id = session['user_id']
    
    if is_admin:
        user = User.query.get(user_id)
        return render_template('profile.html', user=user)
    else:
        employee = Employee.query.get(user_id)
        return render_template('employee_profile.html', employee=employee)

@dashboard_bp.route('/settings')
@login_required
def settings():
    """صفحة الإعدادات"""
    if not session.get('is_admin', False):
        flash('ليس لديك صلاحية الوصول إلى هذه الصفحة', 'danger')
        return redirect(url_for('dashboard.index'))
    
    user_id = session['user_id']
    user = User.query.get(user_id)
    return render_template('settings.html', user=user)