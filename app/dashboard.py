from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response
from .models import User, Employee, OrderStatusNote, SallaOrder, OrderAssignment, OrderEmployeeStatus, EmployeeCustomStatus, OrderDelivery
from datetime import datetime, timedelta
from functools import wraps

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user_id = request.cookies.get('user_id')
        if not user_id:
            flash('يجب تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('user_auth.login'))
        
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
    
@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        user_id = request.cookies.get('user_id')
        
        if is_admin:
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
            employee = Employee.query.get(user_id)
            if not employee:
                flash('بيانات الموظف غير موجودة', 'error')
                resp = make_response(redirect(url_for('user_auth.login')))
                resp.delete_cookie('user_id')
                resp.delete_cookie('is_admin')
                return resp
            
            user = User.query.filter_by(store_id=employee.store_id).first()
            
            if employee.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (employee.role == 'delivery_manager')
                
                # جلب الطلبات المسندة لهذا الموظف فقط مع تفاصيل كل طلب
                assigned_orders = db.session.query(SallaOrder).join(
                    OrderAssignment,
                    OrderAssignment.order_id == SallaOrder.id
                ).filter(
                    OrderAssignment.employee_id == employee.id
                ).order_by(SallaOrder.created_at.desc()).all()
                
                stats = {
                    'assigned_orders': len(assigned_orders),
                    'delivered_orders': OrderDelivery.query.filter_by(
                        employee_id=employee.id
                    ).count(),
                }
                
                return render_template('dashboard.html',
                                    current_user=user,
                                    is_delivery_manager=is_delivery_manager,
                                    employee=employee,
                                    stats=stats)
            else:
                # إحصائيات الطلبات حسب المتجر
                stats = {
                    'new_orders': SallaOrder.query.filter_by(
                        store_id=employee.store_id
                    ).count(),
                    'late_orders': OrderStatusNote.query.join(
                        SallaOrder,
                        OrderStatusNote.order_id == SallaOrder.id
                    ).filter(
                        OrderStatusNote.status_flag == 'late',
                        SallaOrder.store_id == employee.store_id
                    ).count(),
                    'missing_orders': OrderStatusNote.query.join(
                        SallaOrder,
                        OrderStatusNote.order_id == SallaOrder.id
                    ).filter(
                        OrderStatusNote.status_flag == 'missing',
                        SallaOrder.store_id == employee.store_id
                    ).count(),
                    'refunded_orders': OrderStatusNote.query.join(
                        SallaOrder,
                        OrderStatusNote.order_id == SallaOrder.id
                    ).filter(
                        OrderStatusNote.status_flag == 'refunded',
                        SallaOrder.store_id == employee.store_id
                    ).count(),
                    'not_shipped_orders': OrderStatusNote.query.join(
                        SallaOrder,
                        OrderStatusNote.order_id == SallaOrder.id
                    ).filter(
                        OrderStatusNote.status_flag == 'not_shipped',
                        SallaOrder.store_id == employee.store_id
                    ).count(),
                }
                
                # جلب الطلبات المسندة لهذا الموظف
                assigned_orders = db.session.query(SallaOrder).join(
                    OrderAssignment,
                    OrderAssignment.order_id == SallaOrder.id
                ).filter(
                    OrderAssignment.employee_id == employee.id,
                    SallaOrder.store_id == employee.store_id
                ).order_by(SallaOrder.created_at.desc()).all()
                
                # جلب الحالات المخصصة التي أنشأها الموظف
                custom_statuses = OrderStatusNote.query.join(
                    SallaOrder,
                    OrderStatusNote.order_id == SallaOrder.id
                ).filter(
                    SallaOrder.store_id == employee.store_id,
                    OrderStatusNote.created_by == employee.id
                ).order_by(OrderStatusNote.created_at.desc()).all()
                
                # جلب آخر 5 حالات للموظف
                recent_statuses = OrderStatusNote.query.join(
                    SallaOrder,
                    OrderStatusNote.order_id == SallaOrder.id
                ).filter(
                    SallaOrder.store_id == employee.store_id,
                    OrderStatusNote.created_by == employee.id
                ).order_by(OrderStatusNote.created_at.desc()).limit(5).all()
                
                # إحصائيات الحالات المخصصة للموظف
                custom_status_stats = []
                if hasattr(employee, 'custom_statuses'):
                    for status in employee.custom_statuses:
                        count = db.session.query(OrderEmployeeStatus).filter(
                            OrderEmployeeStatus.status_id == status.id,
                            OrderEmployeeStatus.employee_id == employee.id
                        ).count()
                        if count > 0:
                            custom_status_stats.append({
                                'status': status,
                                'count': count
                            })
                
                return render_template('employee_dashboard.html',
                                    current_user=user,
                                    employee=employee,
                                    stats=stats,
                                    custom_status_stats=custom_status_stats,
                                    recent_statuses=recent_statuses,
                                    assigned_orders=assigned_orders)
    
    except Exception as e:
        flash(f"حدث خطأ في جلب بيانات لوحة التحكم: {str(e)}", "error")
        return redirect(url_for('user_auth.login'))
@dashboard_bp.route('/profile')
@login_required
def profile():
    """صفحة الملف الشخصي"""
    is_admin = request.cookies.get('is_admin') == 'true'
    user_id = request.cookies.get('user_id')
    
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
    is_admin = request.cookies.get('is_admin') == 'true'
    if not is_admin:
        flash('ليس لديك صلاحية الوصول إلى هذه الصفحة', 'danger')
        return redirect(url_for('dashboard.index'))
    
    user_id = request.cookies.get('user_id')
    user = User.query.get(user_id)
    return render_template('settings.html', user=user)