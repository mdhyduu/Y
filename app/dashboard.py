from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response
from .models import (
    User, 
    Employee, 
    OrderStatusNote, 
    db,
    OrderAssignment,  # تمت الإضافة
    SallaOrder,       # تمت الإضافة
    EmployeeCustomStatus,  # تمت الإضافة
    OrderEmployeeStatus    # تمت الإضافة
)
from datetime import datetime, timedelta
from functools import wraps

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول"""
    @wraps(view_func)  # <-- الآن ستعمل بشكل صحيح
    def wrapper(*args, **kwargs):
        user_id = request.cookies.get('user_id')
        if not user_id:
            flash('يجب تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('user_auth.login'))
        
        # تحقق من أن user_id رقمية
        if not user_id.isdigit():
            resp = make_response(redirect(url_for('user_auth.login')))
            resp.delete_cookie('user_id')
            resp.delete_cookie('is_admin')
            resp.delete_cookie('employee_role')
            resp.delete_cookie('store_id')
            return resp
        
        is_admin = request.cookies.get('is_admin') == 'true'
        
        if is_admin:
            user = User.query.get(user_id)
            if not user:
                resp = make_response(redirect(url_for('user_auth.login')))
                resp.delete_cookie('user_id')
                resp.delete_cookie('is_admin')
                resp.delete_cookie('employee_role')
                resp.delete_cookie('store_id')
                return resp
            request.current_user = user
        else:
            employee = Employee.query.get(user_id)
            if not employee:
                flash('بيانات الموظف غير موجودة', 'error')
                resp = make_response(redirect(url_for('user_auth.login')))
                resp.delete_cookie('user_id')
                resp.delete_cookie('is_admin')
                resp.delete_cookie('employee_role')
                resp.delete_cookie('store_id')
                return resp
            request.current_user = employee
        
        return view_func(*args, **kwargs)
    return wrapper

# ... بقية الكود كما هو

@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        user_id = request.cookies.get('user_id')
        
        if is_admin:
            # للمستخدمين العامين (المدراء)
            user = User.query.get(user_id)
            if not user:
                resp = make_response(redirect(url_for('user_auth.login')))
                resp.delete_cookie('user_id')
                resp.delete_cookie('is_admin')
                return resp
                
            return render_template('dashboard.html', 
                                current_user=user,
                                is_admin=True)
        
        else:
            # للموظفين
            employee = Employee.query.get(user_id)
            if not employee:
                flash('بيانات الموظف غير موجودة', 'error')
                resp = make_response(redirect(url_for('user_auth.login')))
                resp.delete_cookie('user_id')
                resp.delete_cookie('is_admin')
                return resp
            
            user = User.query.filter_by(store_id=employee.store_id).first()
            
            # تحديد نوع لوحة التحكم حسب الدور
            if employee.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (employee.role == 'delivery_manager')
                return render_template('dashboard.html',
                                    current_user=user,
                                    is_delivery_manager=is_delivery_manager,
                                    employee=employee)
            else:
                # جلب الطلبات المسندة لهذا الموظف فقط
                assignments = OrderAssignment.query.filter_by(employee_id=employee.id).all()
                assigned_order_ids = [a.order_id for a in assignments]
                
                # جلب الطلبات المسندة
                assigned_orders = SallaOrder.query.filter(
                    SallaOrder.id.in_(assigned_order_ids)
                ).all() if assigned_order_ids else []
                
                # حساب الإحصائيات بناءً على الطلبات المسندة فقط
                stats = {
                    'new_orders': len([o for o in assigned_orders if o.status_slug == 'new']),
                    'late_orders': len([o for o in assigned_orders if any(
                        note.status_flag == 'late' for note in o.status_notes
                    )]),
                    'missing_orders': len([o for o in assigned_orders if any(
                        note.status_flag == 'missing' for note in o.status_notes
                    )]),
                    'refunded_orders': len([o for o in assigned_orders if any(
                        note.status_flag == 'refunded' for note in o.status_notes
                    )]),
                    'not_shipped_orders': len([o for o in assigned_orders if any(
                        note.status_flag == 'not_shipped' for note in o.status_notes
                    )])
                }
                
                # جلب الحالات المخصصة للموظف الحالي
                custom_statuses = EmployeeCustomStatus.query.filter_by(
                    employee_id=employee.id
                ).all()
                
                # حساب عدد الطلبات لكل حالة مخصصة
                custom_status_stats = []
                for status in custom_statuses:
                    count = OrderEmployeeStatus.query.filter(
                        OrderEmployeeStatus.status_id == status.id,
                        OrderEmployeeStatus.order_id.in_(assigned_order_ids)
                    ).count() if assigned_order_ids else 0
                    
                    custom_status_stats.append({
                        'status': status,
                        'count': count
                    })
                
                # جلب آخر النشاطات للطلبات المسندة فقط
                recent_statuses = OrderStatusNote.query.filter(
                    OrderStatusNote.order_id.in_(assigned_order_ids)
                ).order_by(OrderStatusNote.created_at.desc()).limit(5).all()
                
                # إضافة معلومات المستخدم الذي أضاف الحالة (للملاحظات)
                for status_note in recent_statuses:
                    status_note.user = User.query.get(status_note.created_by)
                
                return render_template('employee_dashboard.html',
                                    current_user=user,
                                    employee=employee,
                                    stats=stats,
                                    custom_statuses=custom_statuses,
                                    custom_status_stats=custom_status_stats,
                                    recent_statuses=recent_statuses,
                                    assigned_orders=assigned_orders)
    
    except Exception as e:
        flash(f"حدث خطأ في جلب بيانات لوحة التحكم: {str(e)}", "error")
        return redirect(url_for('user_auth.login'))
@dashboard_bp.route('/settings')
@login_required
def settings():
    """صفحة الإعدادات"""
    is_admin = request.cookies.get('is_admin') == 'true'
    if not is_admin:
        flash('ليس لديك صلاحية الوصول إلى هذه الصفحة', 'danger')
        return redirect(url_for('dashboard.index'))
    
    user_id = request.cookies.get('user_id')
    user = User.query.get(user_id)
    return render_template('settings.html', user=user)