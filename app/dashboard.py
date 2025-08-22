from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response
from .models import (
    User, 
    Employee, 
    OrderStatusNote, 
    db,
    OrderAssignment,  # تمت الإضافة
    SallaOrder,       # تمت الإضافة
    EmployeeCustomStatus,  # تمت الإضافة
    OrderEmployeeStatus, 
    CustomNoteStatus  # تمت الإضافة
)
from datetime import datetime, timedelta
from functools import wraps

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user_id = request.cookies.get('user_id')
        is_admin = request.cookies.get('is_admin') == 'true'
        
        # إذا لم يكن هناك user_id، نعيد التوجيه إلى تسجيل الدخول
        if not user_id:
            flash('يجب تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('user_auth.login'))
        
        # إذا كان user_id غير رقمية، نحذف الكوكيز ونعيد التوجيه
        if not user_id.isdigit():
            return clear_cookies_and_redirect()
        
        if is_admin:
            user = User.query.get(user_id)
            if not user:
                return clear_cookies_and_redirect()
            request.current_user = user
        else:
            employee = Employee.query.get(user_id)
            if not employee:
                flash('بيانات الموظف غير موجودة', 'error')
                return clear_cookies_and_redirect()
            request.current_user = employee
        
        return view_func(*args, **kwargs)
    return wrapper

def clear_cookies_and_redirect():
    """حذف الكوكيز وإعادة التوجيه إلى تسجيل الدخول"""
    resp = make_response(redirect(url_for('user_auth.login')))
    resp.delete_cookie('user_id')
    resp.delete_cookie('is_admin')
    resp.delete_cookie('employee_role')
    resp.delete_cookie('store_id')
    return resp

# ... بقية الكود كما هو

@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        
        if is_admin:
            user = request.current_user
            
            # جلب جميع الطلبات للمتجر
            all_orders = SallaOrder.query.filter_by(store_id=user.store_id).all()
            
            # حساب الإحصائيات الشاملة
            stats = {
                'total_orders': len(all_orders),
                'new_orders': len([o for o in all_orders if o.status_slug == 'new']),
                'late_orders': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                    OrderStatusNote.status_flag == 'late',
                    SallaOrder.store_id == user.store_id
                ).count(),
                'missing_orders': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                    OrderStatusNote.status_flag == 'missing',
                    SallaOrder.store_id == user.store_id
                ).count(),
                'refunded_orders': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                    OrderStatusNote.status_flag == 'refunded',
                    SallaOrder.store_id == user.store_id
                ).count(),
                'not_shipped_orders': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                    OrderStatusNote.status_flag == 'not_shipped',
                    SallaOrder.store_id == user.store_id
                ).count()
            }
            
            # جلب الحالات المخصصة للمتجر
            custom_statuses = CustomNoteStatus.query.filter_by(store_id=user.store_id).all()
            
            # حساب عدد الطلبات لكل حالة
            custom_status_stats = []
            for status in custom_statuses:
                count = db.session.query(OrderStatusNote).join(SallaOrder).filter(
                    OrderStatusNote.custom_status_id == status.id,
                    SallaOrder.store_id == user.store_id
                ).count()
                custom_status_stats.append({
                    'status': status,
                    'count': count
                })
            
            # جلب آخر 10 طلبات
            recent_orders = SallaOrder.query.filter_by(
                store_id=user.store_id
            ).order_by(SallaOrder.created_at.desc()).limit(10).all()
            
            # جلب آخر النشاطات
            recent_statuses = db.session.query(OrderStatusNote).join(SallaOrder).filter(
                SallaOrder.store_id == user.store_id
            ).order_by(OrderStatusNote.created_at.desc()).limit(10).all()
            
            # جلب عدد الموظفين
            employees_count = Employee.query.filter_by(store_id=user.store_id).count()
            
            # جلب عدد المنتجات (افتراضي)
            products_count = 0
            
            return render_template('dashboard.html', 
                                current_user=user,
                                stats=stats,
                                custom_status_stats=custom_status_stats,
                                recent_orders=recent_orders,
                                recent_statuses=recent_statuses,
                                employees_count=employees_count,
                                products_count=products_count,
                                is_admin=True)
    
        else:
            employee = request.current_user  
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
                return render_template('delivery_dashboard.html',
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
                
                # حساب عدد الطلبات لكل حالة مخصصة للموظف الحالي
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
                ).options(
                    db.joinedload(OrderStatusNote.admin),
                    db.joinedload(OrderStatusNote.employee),
                    db.joinedload(OrderStatusNote.custom_status)
                ).order_by(OrderStatusNote.created_at.desc()).limit(5).all()
                
                # إذا كان الموظف مراجعًا (reviewer أو manager)، نضيف إحصائيات جميع الموظفين
                if employee.role in ['reviewer', 'manager']:
                    # جلب جميع الحالات المخصصة لجميع الموظفين في المتجر
                    all_employee_statuses = EmployeeCustomStatus.query.join(
                        Employee
                    ).filter(
                        Employee.store_id == employee.store_id
                    ).all()
                    
                    # تجميع الحالات المخصصة حسب الاسم (بدون تكرار) وجمع عدد الطلبات لكل حالة
                    status_stats_dict = {}
                    for status in all_employee_statuses:
                        count = OrderEmployeeStatus.query.filter(
                            OrderEmployeeStatus.status_id == status.id
                        ).count()

                        if status.name in status_stats_dict:
                            status_stats_dict[status.name]['count'] += count
                        else:
                            status_stats_dict[status.name] = {
                                'name': status.name,
                                'color': status.color,
                                'count': count
                            }

                    # تحويل القاموس إلى قائمة
                    all_employee_status_stats = list(status_stats_dict.values())
                    
                    return render_template('employee_dashboard.html',
                                        current_user=user,
                                        employee=employee,
                                        stats=stats,
                                        custom_statuses=custom_statuses,
                                        custom_status_stats=custom_status_stats,
                                        recent_statuses=recent_statuses,
                                        assigned_orders=assigned_orders,
                                        # البيانات الجديدة لجميع الموظفين
                                        all_employee_status_stats=all_employee_status_stats,
                                        is_reviewer=True)
                
                else:
                    # للموظفين العاديين
                    return render_template('employee_dashboard.html',
                                        current_user=user,
                                        employee=employee,
                                        stats=stats,
                                        custom_statuses=custom_statuses,
                                        custom_status_stats=custom_status_stats,
                                        recent_statuses=recent_statuses,
                                        assigned_orders=assigned_orders,
                                        is_reviewer=False)
        
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