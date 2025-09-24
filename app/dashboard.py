from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response
from .models import (
    User, 
    Employee, 
    OrderStatusNote, 
    db,
    OrderAssignment,
    SallaOrder,
    EmployeeCustomStatus,
    OrderEmployeeStatus,
    CustomNoteStatus,
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

def _aggregate_default_statuses_for_store(store_id):
    """تجمع الحالات التلقائية لكل الموظفين في المتجر"""
    all_default_statuses = EmployeeCustomStatus.query.filter_by(
        is_default=True
    ).join(Employee).filter(
        Employee.store_id == store_id
    ).all()

    status_stats_dict = {}
    for status in all_default_statuses:
        count = OrderEmployeeStatus.query.filter(
            OrderEmployeeStatus.status_id == status.id
        ).count()

        if status.name in status_stats_dict:
            status_stats_dict[status.name]['count'] += count
        else:
            status_stats_dict[status.name] = {
                'id': status.id,
                'name': status.name,
                'color': status.color,
                'count': count
            }

    return list(status_stats_dict.values())

def _get_employee_status_stats(employee_id):
    """إحصاءات الحالات لموظف محدد"""
    default_statuss = EmployeeCustomStatus.query.filter_by(
        employee_id=employee_id,
        is_default=True
    ).all()

    default_status_stats = []
    for s in default_statuss:
        cnt = OrderEmployeeStatus.query.filter(
            OrderEmployeeStatus.status_id == s.id
        ).count()
        default_status_stats.append({
            'id': s.id,
            'name': s.name,
            'color': s.color,
            'count': cnt
        })

    custom_statuss = EmployeeCustomStatus.query.filter_by(
        employee_id=employee_id,
        is_default=False
    ).all()

    custom_status_stats_selected = []
    for s in custom_statuss:
        cnt = OrderEmployeeStatus.query.filter(
            OrderEmployeeStatus.status_id == s.id
        ).count()
        custom_status_stats_selected.append({
            'id': s.id,
            'name': s.name,
            'color': s.color,
            'count': cnt
        })

    return default_status_stats, custom_status_stats_selected
def _get_active_orders_count(employee_id):
    """حساب عدد الطلبات النشطة (التي لم يتم توصيلها) للموظف"""
    delivered_status = EmployeeCustomStatus.query.filter_by(
        employee_id=employee_id,
        name='تم التوصيل'
    ).first()
    
    if not delivered_status:
        return 0
        
    # الحصول على جميع الطلبات المسندة للموظف
    assignments = OrderAssignment.query.filter_by(employee_id=employee_id).all()
    assigned_order_ids = [a.order_id for a in assignments]
    
    if not assigned_order_ids:
        return 0
        
    # حساب الطلبات التي لم يتم توصيلها
    active_orders_count = 0
    for order_id in assigned_order_ids:
        # الحصول على آخر حالة للطلب
        latest_status = OrderEmployeeStatus.query.filter_by(
            order_id=order_id
        ).order_by(OrderEmployeeStatus.created_at.desc()).first()
        
        # إذا لم تكن الحالة الأخيرة هي "تم التوصيل"، فإن الطلب لا يزال نشطًا
        if not latest_status or latest_status.status_id != delivered_status.id:
            active_orders_count += 1
            
    return active_orders_count

def _get_filtered_orders(store_id, status_id=None, for_delivery=False):
    """دالة مساعدة للحصول على الطلبات المصفاة"""
    # استخدام joinedload لتحميل جميع العلاقات المطلوبة
    query = SallaOrder.query.filter_by(store_id=store_id)
    
    # إذا كان للعرض على فريق التوصيل، نضيف join مع OrderAddress للتصفية حسب المدينة
    if for_delivery:
        query = query.join(OrderAddress).filter(
            OrderAddress.city == 'الرياض',
            OrderAddress.address_type == 'receiver'
        )
    
    all_orders = query.options(
        db.joinedload(SallaOrder.employee_statuses).joinedload(OrderEmployeeStatus.status),
        db.joinedload(SallaOrder.status_notes),
        db.joinedload(SallaOrder.assignments).joinedload(OrderAssignment.employee),
        db.joinedload(SallaOrder.address)  # تحميل بيانات العنوان
    ).all()
    
    # إضافة الحالة الحالية لكل طلب
    for order in all_orders:
        if order.employee_statuses:
            sorted_statuses = sorted(order.employee_statuses, key=lambda x: x.created_at, reverse=True)
            order.current_status = sorted_statuses[0].status
        else:
            order.current_status = None
    
    # تصفية الطلبات بناءً على الحالة المحددة
    if status_id:
        filtered_orders = [order for order in all_orders 
                         if order.current_status and order.current_status.id == status_id]
    else:
        filtered_orders = all_orders
    
    return filtered_orders, all_orders
@dashboard_bp.route('/')
@login_required
def index():

    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        
        default_status_stats = []
        custom_status_stats_selected = []
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

            custom_statuses = CustomNoteStatus.query.filter_by(store_id=user.store_id).all()

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

            recent_statuses = db.session.query(OrderStatusNote).join(SallaOrder).filter(
                SallaOrder.store_id == user.store_id
            ).order_by(OrderStatusNote.created_at.desc()).limit(10).all()

            employees_count = Employee.query.filter_by(store_id=user.store_id).count()
            products_count = 0

            if selected_employee_id:
                selected_employee = next((emp for emp in all_employees if emp.id == selected_employee_id), None)
                if selected_employee:
                    default_status_stats, custom_status_stats_selected = _get_employee_status_stats(selected_employee_id)

                    return render_template('dashboard.html',
                                           current_user=user,
                                           stats=stats,
                                           custom_status_stats=custom_status_stats,
                                           recent_statuses=recent_statuses,
                                           employees_count=employees_count,
                                           products_count=products_count,
                                           all_employees=all_employees,
                                           selected_employee=selected_employee,
                                           default_status_stats=default_status_stats,
                                           custom_status_stats_selected=custom_status_stats_selected,
                                           is_admin=True)

            all_employee_status_stats = _aggregate_default_statuses_for_store(user.store_id)

            return render_template('dashboard.html',
                                   current_user=user,
                                   stats=stats,
                                   custom_status_stats=custom_status_stats,
                                   recent_statuses=recent_statuses,
                                   employees_count=employees_count,
                                   products_count=products_count,
                                   all_employees=all_employees,
                                   selected_employee=selected_employee,
                                   default_status_stats=default_status_stats,
                                   custom_status_stats_selected=custom_status_stats_selected,
                                   all_employee_status_stats=all_employee_status_stats,
                                   is_admin=True)
        else:
            employee = request.current_user
            if not employee:
                flash('بيانات الموظف غير موجودة', 'error')
                return clear_cookies_and_redirect()
            
            user = User.query.filter_by(store_id=employee.store_id).first()
            
            # الحصول على الحالة المحددة من الباراميتر
            selected_status_id = request.args.get('status_id', type=int)
            
            # إذا كان موظف توصيل أو مدير توصيل، نعرض لوحة التوصيل
            # في قسم delivery و delivery_manager:
            if employee.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (employee.role == 'delivery_manager')
                
                # تحديد نطاق الطلبات بناءً على صلاحية الموظف
                if is_delivery_manager:
                    # المدير يرى جميع طلبات المتجر في الرياض فقط
                    filtered_orders, all_orders = _get_filtered_orders(
                        employee.store_id, 
                        selected_status_id, 
                        for_delivery=True  # تصفية لطلبات الرياض فقط
                    )
                else:
                    # الموظف العادي يرى فقط الطلبات المسندة إليه في الرياض فقط
                    assigned_order_ids = [a.order_id for a in OrderAssignment.query.filter_by(
                        employee_id=employee.id
                    ).all()]
                    
                    if assigned_order_ids:
                        # جلب جميع طلبات المتجر في الرياض ثم تصفيتها للموظف
                        _, store_orders = _get_filtered_orders(
                            employee.store_id, 
                            selected_status_id, 
                            for_delivery=True  # تصفية لطلبات الرياض فقط
                        )
                        filtered_orders = [order for order in store_orders if order.id in assigned_order_ids]
                        all_orders = filtered_orders
                    else:
                        filtered_orders, all_orders = [], []
    
    # باقي الكود كما هو...
                
                # حساب إحصائيات الحالات للعرض في التبويبات
                status_stats = {}
                for order in all_orders:
                    if order.current_status:
                        status_id = order.current_status.id
                        status_name = order.current_status.name
                        status_color = order.current_status.color
                        
                        if status_id not in status_stats:
                            status_stats[status_id] = {
                                'id': status_id,
                                'name': status_name,
                                'color': status_color,
                                'count': 1
                            }
                        else:
                            status_stats[status_id]['count'] += 1
                
                default_status_stats = list(status_stats.values())
                
                # جلب مناديب التوصيل (للمدير فقط)
                delivery_employees = []
                if is_delivery_manager:
                    delivery_employees = Employee.query.filter_by(
                        store_id=employee.store_id, 
                        role='delivery',
                        is_active=True
                    ).all()
                
                # حساب الإحصائيات بناءً على الطلبات
                in_progress_count = len([order for order in all_orders 
                                       if order.current_status and order.current_status.name == 'قيد التنفيذ'])
                completed_count = len([order for order in all_orders 
                                     if order.current_status and order.current_status.name == 'تم التنفيذ'])
                
                # حساب عدد الطلبات الجديدة اليوم
                new_orders_today = len([o for o in filtered_orders if o.created_at and o.created_at.date() == datetime.now().date()])
                
                return render_template('delivery_dashboard.html',
                                       current_user=user,
                                       is_delivery_manager=is_delivery_manager,
                                       employee=employee,
                                       in_progress_count=in_progress_count,
                                       completed_count=completed_count,
                                       default_status_stats=default_status_stats,
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
                # للموظفين الآخرين (مراجعين، مديرين، إلخ)
                assignments = OrderAssignment.query.filter_by(employee_id=employee.id).all()
                assigned_order_ids = [a.order_id for a in assignments]

                assigned_orders = SallaOrder.query.filter(
                    SallaOrder.id.in_(assigned_order_ids)
                ).all() if assigned_order_ids else []

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

                default_statuses = EmployeeCustomStatus.query.filter_by(
                    employee_id=employee.id,
                    is_default=True
                ).all()

                custom_status_stats = []
                for status in default_statuses:
                    count = OrderEmployeeStatus.query.filter(
                        OrderEmployeeStatus.status_id == status.id,
                        OrderEmployeeStatus.order_id.in_(assigned_order_ids)
                    ).count() if assigned_order_ids else 0

                    custom_status_stats.append({
                        'status': status,
                        'count': count
                    })

                recent_statuses = OrderStatusNote.query.filter(
                    OrderStatusNote.order_id.in_(assigned_order_ids)
                ).options(
                    db.joinedload(OrderStatusNote.admin),
                    db.joinedload(OrderStatusNote.employee),
                    db.joinedload(OrderStatusNote.custom_status)
                ).order_by(OrderStatusNote.created_at.desc()).limit(5).all()

                # في قسم المراجع (reviewer) في dashboard.py

                if employee.role in ['reviewer', 'manager']:
                    # حساب الطلبات الجديدة بشكل صحيح - مثل المدير
                    new_orders_count = db.session.query(SallaOrder).outerjoin(
                        OrderStatusNote, OrderStatusNote.order_id == SallaOrder.id
                    ).filter(
                        SallaOrder.store_id == employee.store_id,
                        OrderStatusNote.id == None,  # الطلبات بدون ملاحظات
                        SallaOrder.id.in_(assigned_order_ids)  # فقط الطلبات المسندة للمراجع
                    ).count() if assigned_order_ids else 0
                
                    stats = {
                        'total_orders': len(assigned_orders),
                        'new_orders': new_orders_count,  # ✅ الآن محسوبة بشكل صحيح
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
                    all_employees = Employee.query.filter_by(
                        store_id=employee.store_id,
                        is_active=True
                    ).all()

                    selected_employee_id = request.args.get('employee_id', type=int)

                    if selected_employee_id:
                        selected_employee = next((emp for emp in all_employees if emp.id == selected_employee_id), None)
                        if selected_employee:
                            default_status_stats, custom_status_stats_selected = _get_employee_status_stats(selected_employee_id)

                            return render_template('employee_dashboard.html',
                                                   current_user=user,
                                                   employee=employee,
                                                   stats=stats,
                                                   custom_statuses=default_statuses,
                                                   custom_status_stats=custom_status_stats,
                                                   recent_statuses=recent_statuses,
                                                   assigned_orders=assigned_orders,
                                                   all_employees=all_employees,
                                                   selected_employee=selected_employee,
                                                   default_status_stats=default_status_stats,
                                                   custom_status_stats_selected=custom_status_stats_selected,
                                                   is_reviewer=True)

                    all_employee_status_stats = _aggregate_default_statuses_for_store(employee.store_id)

                    return render_template('employee_dashboard.html',
                                           current_user=user,
                                           employee=employee,
                                           stats=stats,
                                           custom_statuses=default_statuses,
                                           custom_status_stats=custom_status_stats,
                                           recent_statuses=recent_statuses,
                                           assigned_orders=assigned_orders,
                                           all_employees=all_employees,
                                           all_employee_status_stats=all_employee_status_stats,
                                           is_reviewer=True)

                else:
                    return render_template('employee_dashboard.html',
                                           current_user=user,
                                           employee=employee,
                                           stats=stats,
                                           custom_statuses=default_statuses,
                                           custom_status_stats=custom_status_stats,
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
    """إرجاع الطلبات المصفاة فقط (لطلبات AJAX)"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        if is_admin:
            return "غير مصرح للمديرين", 403
            
        employee = request.current_user
        if not employee or employee.role not in ('delivery', 'delivery_manager'):
            return "غير مصرح", 403
            
        # الحصول على الحالة المحددة من الباراميتر
        selected_status_id = request.args.get('status_id', type=int)
        
        # تحديد نطاق الطلبات بناءً على صلاحية الموظف مع التصفية للرياض فقط
        if employee.role == 'delivery_manager':
            # المدير يرى جميع طلبات المتجر في الرياض فقط
            filtered_orders, _ = _get_filtered_orders(
                employee.store_id, 
                selected_status_id, 
                for_delivery=True  # تصفية لطلبات الرياض فقط
            )
        else:
            # الموظف العادي يرى فقط الطلبات المسندة إليه في الرياض فقط
            assigned_order_ids = [a.order_id for a in OrderAssignment.query.filter_by(
                employee_id=employee.id
            ).all()]
            
            if assigned_order_ids:
                # جلب جميع طلبات المتجر في الرياض ثم تصفيتها للموظف
                _, store_orders = _get_filtered_orders(
                    employee.store_id, 
                    selected_status_id, 
                    for_delivery=True  # تصفية لطلبات الرياض فقط
                )
                filtered_orders = [order for order in store_orders if order.id in assigned_order_ids]
            else:
                filtered_orders = []
        
        # إرجاع جزء HTML فقط بدلاً من الصفحة كاملة
        return render_template('orders_partial.html', filtered_orders=filtered_orders)
        
    except Exception as e:
        logger.error(f"خطأ في filter_orders: {str(e)}")
        return "حدث خطأ أثناء جلب الطلبات", 500