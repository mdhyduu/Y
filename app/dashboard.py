# في ملف dashboard.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response
from .models import (
    User, 
    Employee, 
    OrderStatusNote, 
    db,
    OrderAssignment,
    SallaOrder,
    OrderStatus,  # ✅ التركيز على OrderStatus فقط
    OrderAddress
) 
from datetime import datetime
from functools import wraps
from sqlalchemy.orm import joinedload
import logging

# إعداد المسجل للإنتاج
logger = logging.getLogger('__init__')

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user_id = request.cookies.get('user_id')
        is_admin = request.cookies.get('is_admin') == 'true'
        
        if not user_id:
            flash('يجب تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('user_auth.login'))
        
        if not user_id.isdigit():
            return clear_cookies_and_redirect()
        
        try:
            if is_admin:
                user = User.query.get(int(user_id))
                if not user:
                    flash('بيانات المستخدم غير موجودة', 'error')
                    return clear_cookies_and_redirect()
                request.current_user = user
            else:
                employee = Employee.query.get(int(user_id))
                if not employee:
                    flash('بيانات الموظف غير موجودة', 'error')
                    return clear_cookies_and_redirect()
                request.current_user = employee
            
            return view_func(*args, **kwargs)
        except Exception as e:
            logger.error(f"خطأ في التحقق من تسجيل الدخول: {str(e)}")
            flash('حدث خطأ في التحقق من هوية المستخدم', 'error')
            return clear_cookies_and_redirect()
    return wrapper

def clear_cookies_and_redirect():
    """حذف الكوكيز وإعادة التوجيه إلى تسجيل الدخول"""
    resp = make_response(redirect(url_for('user_auth.login')))
    resp.delete_cookie('user_id')
    resp.delete_cookie('is_admin')
    resp.delete_cookie('employee_role')
    resp.delete_cookie('store_id')
    return resp

def _aggregate_statuses_for_store(store_id):
    """تجمع الحالات الأصلية لكل الطلبات في المتجر"""
    order_statuses = OrderStatus.query.filter_by(store_id=store_id).all()
    
    status_stats = []
    for status in order_statuses:
        count = SallaOrder.query.filter_by(
            store_id=store_id,
            status_id=status.id
        ).count()

        status_stats.append({
            'id': status.id,
            'name': status.name,
            'color': '#6c757d',
            'count': count
        })

    return status_stats

def _get_employee_status_stats(employee_id):
    """إحصاءات الحالات الأصلية لموظف محدد"""
    employee = Employee.query.get(employee_id)
    if not employee:
        return []

    order_statuses = OrderStatus.query.filter_by(store_id=employee.store_id).all()

    status_stats = []
    for status in order_statuses:
        count = SallaOrder.query.join(OrderAssignment).filter(
            OrderAssignment.employee_id == employee_id,
            SallaOrder.status_id == status.id
        ).count()
        
        status_stats.append({
            'id': status.id,
            'name': status.name,
            'color': '#6c757d',
            'count': count
        })

    return status_stats

def _get_active_orders_count(employee_id):
    """حساب عدد الطلبات النشطة (التي لم يتم توصيلها) للموظف"""
    delivered_status = OrderStatus.query.filter_by(
        store_id=Employee.query.get(employee_id).store_id,
        name='تم التوصيل'
    ).first()
    
    if not delivered_status:
        return 0
        
    assignments = OrderAssignment.query.filter_by(employee_id=employee_id).all()
    assigned_order_ids = [a.order_id for a in assignments]
    
    if not assigned_order_ids:
        return 0
        
    active_orders_count = 0
    for order_id in assigned_order_ids:
        order = SallaOrder.query.get(order_id)
        if order and order.status_id != delivered_status.id:
            active_orders_count += 1
            
    return active_orders_count

def _get_filtered_orders(store_id, status_id=None, for_delivery=False):
    """دالة مساعدة للحصول على الطلبات المصفاة بناءً على الحالة الأصلية"""
    query = SallaOrder.query.filter_by(store_id=store_id)
    
    if for_delivery:
        query = query.join(OrderAddress).filter(
            OrderAddress.city == 'الرياض',
            OrderAddress.address_type == 'receiver'
        )
    
    all_orders = query.options(
        db.joinedload(SallaOrder.status),
        db.joinedload(SallaOrder.status_notes),
        db.joinedload(SallaOrder.assignments).joinedload(OrderAssignment.employee),
        db.joinedload(SallaOrder.address)
    ).all()
    
    if status_id:
        filtered_orders = [order for order in all_orders 
                         if order.status and order.status.id == status_id]
    else:
        filtered_orders = all_orders
    
    return filtered_orders, all_orders

@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        
        status_stats = []
        all_employee_status_stats = []

        if is_admin:
            user = request.current_user

            all_employees = Employee.query.filter_by(
                store_id=user.store_id,
                is_active=True
            ).all()

            selected_employee_id = request.args.get('employee_id', type=int)
            selected_employee = None

            all_orders = SallaOrder.query.options(joinedload(SallaOrder.status)).filter_by(store_id=user.store_id).all()

            new_orders_count = db.session.query(SallaOrder).outerjoin(
                OrderStatusNote, OrderStatusNote.order_id == SallaOrder.id
            ).filter(
                SallaOrder.store_id == user.store_id,
                OrderStatusNote.id == None
            ).count()

            stats = {
                'total_orders': len(all_orders),
                'new_orders': new_orders_count,
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

            order_statuses = OrderStatus.query.filter_by(store_id=user.store_id).all()

            status_stats_list = []
            for status in order_statuses:
                count = SallaOrder.query.filter_by(
                    store_id=user.store_id,
                    status_id=status.id
                ).count()
                status_stats_list.append({
                    'status': status,
                    'count': count
                })

            recent_statuses = db.session.query(OrderStatusNote).join(SallaOrder).filter(
                SallaOrder.store_id == user.store_id
            ).order_by(OrderStatusNote.created_at.desc()).limit(10).all()

            employees_count = Employee.query.filter_by(store_id=user.store_id).count()
            products_count = 0

            if selected_employee_id:
                selected_employee = next((emp for emp in all_employees if emp.id == selected_employee_id), None)
                if selected_employee:
                    status_stats = _get_employee_status_stats(selected_employee_id)

                    return render_template('dashboard.html',
                                           current_user=user,
                                           stats=stats,
                                           status_stats_list=status_stats_list,
                                           recent_statuses=recent_statuses,
                                           employees_count=employees_count,
                                           products_count=products_count,
                                           all_employees=all_employees,
                                           selected_employee=selected_employee,
                                           status_stats=status_stats,
                                           is_admin=True)

            all_employee_status_stats = _aggregate_statuses_for_store(user.store_id)

            return render_template('dashboard.html',
                                   current_user=user,
                                   stats=stats,
                                   status_stats_list=status_stats_list,
                                   recent_statuses=recent_statuses,
                                   employees_count=employees_count,
                                   products_count=products_count,
                                   all_employees=all_employees,
                                   selected_employee=selected_employee,
                                   status_stats=status_stats,
                                   all_employee_status_stats=all_employee_status_stats,
                                   is_admin=True)
        else:
            employee = request.current_user
            if not employee:
                flash('بيانات الموظف غير موجودة', 'error')
                return clear_cookies_and_redirect()
            
            user = User.query.filter_by(store_id=employee.store_id).first()
            
            selected_status_id = request.args.get('status_id', type=int)
            
            if employee.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (employee.role == 'delivery_manager')
                
                if is_delivery_manager:
                    filtered_orders, all_orders = _get_filtered_orders(
                        employee.store_id, 
                        selected_status_id, 
                        for_delivery=True
                    )
                else:
                    assigned_order_ids = [a.order_id for a in OrderAssignment.query.filter_by(
                        employee_id=employee.id
                    ).all()]
                    
                    if assigned_order_ids:
                        _, store_orders = _get_filtered_orders(
                            employee.store_id, 
                            selected_status_id, 
                            for_delivery=True
                        )
                        filtered_orders = [order for order in store_orders if order.id in assigned_order_ids]
                        all_orders = filtered_orders
                    else:
                        filtered_orders, all_orders = [], []
                
                status_stats = {}
                for order in all_orders:
                    if order.status:
                        status_id = order.status.id
                        status_name = order.status.name
                        status_color = '#6c757d'
                        
                        if status_id not in status_stats:
                            status_stats[status_id] = {
                                'id': status_id,
                                'name': status_name,
                                'color': status_color,
                                'count': 1
                            }
                        else:
                            status_stats[status_id]['count'] += 1
                
                status_stats_list = list(status_stats.values())
                
                delivery_employees = []
                if is_delivery_manager:
                    delivery_employees = Employee.query.filter_by(
                        store_id=employee.store_id, 
                        role='delivery',
                        is_active=True
                    ).all()
                
                in_progress_count = len([order for order in all_orders 
                                       if order.status and 'قيد' in order.status.name])
                completed_count = len([order for order in all_orders 
                                     if order.status and 'تم' in order.status.name])
                
                new_orders_today = len([o for o in filtered_orders if o.created_at and o.created_at.date() == datetime.now().date()])
                
                return render_template('delivery_dashboard.html',
                                       current_user=user,
                                       is_delivery_manager=is_delivery_manager,
                                       employee=employee,
                                       in_progress_count=in_progress_count,
                                       completed_count=completed_count,
                                       status_stats_list=status_stats_list,
                                       total_orders=len(all_orders),
                                       delivery_employees=delivery_employees,
                                       filtered_orders=filtered_orders,
                                       active_employees_count=len(delivery_employees),
                                       new_orders_today=new_orders_today,
                                       order_trend=12,
                                       completed_trend=8,
                                       selected_status_id=selected_status_id
                              )
            else:
                # 🔹 باقي الموظفين
                assignments = OrderAssignment.query.filter_by(employee_id=employee.id).all()
                assigned_order_ids = [a.order_id for a in assignments]
                assigned_orders = SallaOrder.query.filter(
                    SallaOrder.id.in_(assigned_order_ids)
                ).all() if assigned_order_ids else []

                if employee.role in ['reviewer', 'manager']:
                    all_orders = SallaOrder.query.filter_by(store_id=employee.store_id).all()

                    stats = {
                        'total_orders': len(all_orders),
                        'new_orders': db.session.query(SallaOrder).outerjoin(
                            OrderStatusNote, OrderStatusNote.order_id == SallaOrder.id
                        ).filter(
                            SallaOrder.store_id == employee.store_id,
                            OrderStatusNote.id == None
                        ).count(),
                        'late_orders': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                            OrderStatusNote.status_flag == 'late',
                            SallaOrder.store_id == employee.store_id
                        ).count(),
                        'missing_orders': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                            OrderStatusNote.status_flag == 'missing',
                            SallaOrder.store_id == employee.store_id
                        ).count(),
                        'refunded_orders': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                            OrderStatusNote.status_flag == 'refunded',
                            SallaOrder.store_id == employee.store_id
                        ).count(),
                        'not_shipped_orders': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                            OrderStatusNote.status_flag == 'not_shipped',
                            SallaOrder.store_id == employee.store_id
                        ).count()
                    }

                    order_statuses = OrderStatus.query.filter_by(store_id=employee.store_id).all()

                    status_stats_list = []
                    for status in order_statuses:
                        count = SallaOrder.query.filter_by(
                            store_id=employee.store_id,
                            status_id=status.id
                        ).count()
                        status_stats_list.append({'status': status, 'count': count})

                    recent_statuses = OrderStatusNote.query.join(SallaOrder).filter(
                        SallaOrder.store_id == employee.store_id
                    ).options(
                        db.joinedload(OrderStatusNote.admin),
                        db.joinedload(OrderStatusNote.employee)
                    ).order_by(OrderStatusNote.created_at.desc()).limit(5).all()

                    all_employees = Employee.query.filter_by(
                        store_id=employee.store_id,
                        is_active=True
                    ).all()

                    selected_employee_id = request.args.get('employee_id', type=int)
                    if selected_employee_id:
                        selected_employee = next((emp for emp in all_employees if emp.id == selected_employee_id), None)
                        if selected_employee:
                            status_stats = _get_employee_status_stats(selected_employee_id)
                            return render_template('employee_dashboard.html',
                                                   current_user=user,
                                                   employee=employee,
                                                   stats=stats,
                                                   order_statuses=order_statuses,
                                                   status_stats_list=status_stats_list,
                                                   recent_statuses=recent_statuses,
                                                   assigned_orders=assigned_orders,
                                                   all_employees=all_employees,
                                                   selected_employee=selected_employee,
                                                   status_stats=status_stats,
                                                   is_reviewer=True)

                    all_employee_status_stats = _aggregate_statuses_for_store(employee.store_id)
                    return render_template('employee_dashboard.html',
                                           current_user=user,
                                           employee=employee,
                                           stats=stats,
                                           order_statuses=order_statuses,
                                           status_stats_list=status_stats_list,
                                           recent_statuses=recent_statuses,
                                           assigned_orders=assigned_orders,
                                           all_employees=all_employees,
                                           all_employee_status_stats=all_employee_status_stats,
                                           is_reviewer=True)

                else:
                    stats = {
                        'total_orders': len(assigned_orders),
                        'new_orders': 0,
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

                    order_statuses = OrderStatus.query.filter_by(store_id=employee.store_id).all()

                    status_stats_list = []
                    for status in order_statuses:
                        count = SallaOrder.query.join(OrderAssignment).filter(
                            OrderAssignment.employee_id == employee.id,
                            SallaOrder.status_id == status.id
                        ).count() if assigned_order_ids else 0

                        status_stats_list.append({
                            'status': status,
                            'count': count
                        })

                    recent_statuses = OrderStatusNote.query.filter(
                        OrderStatusNote.order_id.in_(assigned_order_ids)
                    ).options(
                        db.joinedload(OrderStatusNote.admin),
                        db.joinedload(OrderStatusNote.employee)
                    ).order_by(OrderStatusNote.created_at.desc()).limit(5).all()

                    return render_template('employee_dashboard.html',
                                           current_user=user,
                                           employee=employee,
                                           stats=stats,
                                           order_statuses=order_statuses,
                                           status_stats_list=status_stats_list,
                                           recent_statuses=recent_statuses,
                                           assigned_orders=assigned_orders,
                                           is_reviewer=False)

    except Exception as e:
        logger.error(f"خطأ في index dashboard: {str(e)}")
        flash("حدث خطأ في جلب بيانات لوحة التحكم", "error")
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
    
@dashboard_bp.route('/filter_orders')
@login_required
def filter_orders():
    """إرجاع الطلبات المصفاة فقط (لطلبات AJAX) بناءً على الحالة الأصلية"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        if is_admin:
            return "غير مصرح للمديرين", 403
            
        employee = request.current_user
        if not employee or employee.role not in ('delivery', 'delivery_manager'):
            return "غير مصرح", 403
            
        selected_status_id = request.args.get('status_id')
        
        if employee.role == 'delivery_manager':
            filtered_orders, _ = _get_filtered_orders(
                employee.store_id, 
                selected_status_id,
                for_delivery=True
            )
        else:
            assigned_order_ids = [a.order_id for a in OrderAssignment.query.filter_by(
                employee_id=employee.id
            ).all()]
            
            if assigned_order_ids:
                _, store_orders = _get_filtered_orders(
                    employee.store_id, 
                    selected_status_id,
                    for_delivery=True
                )
                filtered_orders = [order for order in store_orders if order.id in assigned_order_ids]
            else:
                filtered_orders = []
        
        return render_template('orders_partial.html', filtered_orders=filtered_orders)
        
    except Exception as e:
        logger.error(f"خطأ في filter_orders: {str(e)}")
        return "حدث خطأ أثناء جلب الطلبات", 500