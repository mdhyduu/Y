
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
    OrderAddress,
    OrderStatus  # ✅ إضافة الاستيراد
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
    """تجمع الحالات الأصلية لكل الطلبات في المتجر"""
    # الحصول على جميع الحالات الأصلية للمتجر
    order_statuses = OrderStatus.query.filter_by(store_id=store_id).all()
    
    status_stats_dict = {}
    for status in order_statuses:
        # حساب عدد الطلبات لكل حالة
        count = SallaOrder.query.filter_by(
            store_id=store_id,
            status_id=status.id
        ).count()

        status_stats_dict[status.id] = {
            'id': status.id,
            'name': status.name,
            'slug': status.slug,  # ✅ إضافة الـ slug
            'color': '#6c757d',  # لون افتراضي للحالات الأصلية
            'count': count
        }

    return list(status_stats_dict.values())
def _get_employee_status_stats(employee_id):
    """إحصاءات الحالات الأصلية لموظف محدد"""
    employee = Employee.query.get(employee_id)
    if not employee:
        return [], []

    # الحصول على جميع الحالات الأصلية للمتجر
    order_statuses = OrderStatus.query.filter_by(store_id=employee.store_id).all()

    default_status_stats = []
    for status in order_statuses:
        # حساب عدد الطلبات المسندة للموظف في كل حالة
        count = SallaOrder.query.join(OrderAssignment).filter(
            OrderAssignment.employee_id == employee_id,
            SallaOrder.status_id == status.id
        ).count()
        
        default_status_stats.append({
            'id': status.id,
            'name': status.name,
            'slug': status.slug,  # ✅ إضافة الـ slug
            'color': '#6c757d',  # لون افتراضي
            'count': count
        })

    # في هذا السياق، لا نستخدم الحالات المخصصة، لذا نرجع قائمة فارغة للثانية
    custom_status_stats_selected = []

    return default_status_stats, custom_status_stats_selected
    
def _get_delivery_status_stats(store_id, employee_id=None):
    """إحصائيات الحالات الأصلية لفريق التوصيل"""
    # الحصول على جميع الحالات الأصلية للمتجر
    order_statuses = OrderStatus.query.filter_by(store_id=store_id).all()
    
    status_stats = []
    for status in order_statuses:
        # بناء الاستعلام الأساسي
        query = SallaOrder.query.filter_by(
            store_id=store_id,
            status_id=status.id
        ).join(OrderAddress).filter(
            OrderAddress.city == 'الرياض',
            OrderAddress.address_type == 'receiver'
        )
        
        # إذا كان موظف محدد، نضيف التصفية بالطلبات المسندة له
        if employee_id:
            query = query.join(OrderAssignment).filter(
                OrderAssignment.employee_id == employee_id
            )
        
        count = query.count()
        
        status_stats.append({
            'id': status.id,
            'name': status.name,
            'slug': status.slug,  # ✅ إضافة الـ slug
            'color': '#6c757d',  # لون افتراضي للحالات الأصلية
            'count': count
        })
    
    return status_stats
def _get_active_orders_count(employee_id):
    """حساب عدد الطلبات النشطة (التي لم يتم توصيلها) للموظف"""
    # استخدام الحالة الأصلية "تم التوصيل" بدلاً من المخصصة
    delivered_status = OrderStatus.query.filter_by(
        store_id=Employee.query.get(employee_id).store_id,
        name='تم التوصيل'
    ).first()
    
    if not delivered_status:
        return 0
        
    # الحصول على جميع الطلبات المسندة للموظف
    assignments = OrderAssignment.query.filter_by(employee_id=employee_id).all()
    assigned_order_ids = [a.order_id for a in assignments]
    
    if not assigned_order_ids:
        return 0
        
    # حساب الطلبات التي لم يتم توصيلها (ليست في حالة "تم التوصيل")
    active_orders_count = 0
    for order_id in assigned_order_ids:
        order = SallaOrder.query.get(order_id)
        if order and order.status_id != delivered_status.id:
            active_orders_count += 1
            
    return active_orders_count

def _get_filtered_orders(store_id, status_id=None, for_delivery=False):
    """دالة مساعدة للحصول على الطلبات المصفاة بناءً على الحالة الأصلية"""
    query = SallaOrder.query.filter_by(store_id=store_id)
    
    # إذا كان للعرض على فريق التوصيل، نضيف join مع OrderAddress للتصفية حسب المدينة
    if for_delivery:
        query = query.join(OrderAddress).filter(
            OrderAddress.city == 'الرياض',
            OrderAddress.address_type == 'receiver'
        )
    
    # تحميل العلاقات المطلوبة بما في ذلك الحالة الأصلية
    all_orders = query.options(
        db.joinedload(SallaOrder.status),  # الحالة الأصلية
        db.joinedload(SallaOrder.status_notes),
        db.joinedload(SallaOrder.assignments).joinedload(OrderAssignment.employee),
        db.joinedload(SallaOrder.address)
    ).all()
    
    # تصفية الطلبات بناءً على الحالة المحددة
    if status_id:
        filtered_orders = [order for order in all_orders 
                         if order.status and order.status.id == status_id]
    else:
        filtered_orders = all_orders
    
    return filtered_orders, all_orders



@dashboard_bp.route('/')
@login_required
def index():
    print(f"user_id: {request.cookies.get('user_id')}")
    print(f"is_admin: {request.cookies.get('is_admin')}")
    print(f"employee_role: {request.cookies.get('employee_role')}")
    
    """لوحة التحكم الرئيسية"""
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
# في قسم إحصائيات المدير (admin)، قم بتحديث الـ stats لتشمل:
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
                ).count(),
                # ✅ إضافة الحالتين الجديدتين
                'not_shipped_count': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                    OrderStatusNote.status_flag == 'not_shipped',
                    SallaOrder.store_id == user.store_id
                ).count(),
                'refunded_count': db.session.query(OrderStatusNote).join(SallaOrder).filter(
                    OrderStatusNote.status_flag == 'refunded',
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
            if employee.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (employee.role == 'delivery_manager')
                
                # تحديد نطاق الطلبات بناءً على صلاحية الموظف
                if is_delivery_manager:
                    # المدير يرى جميع طلبات المتجر في الرياض فقط
                    filtered_orders, all_orders = _get_filtered_orders(
                        employee.store_id, 
                        selected_status_id, 
                        for_delivery=True
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
                            for_delivery=True
                        )
                        filtered_orders = [order for order in store_orders if order.id in assigned_order_ids]
                        all_orders = filtered_orders
                    else:
                        filtered_orders, all_orders = [], []
                
                # استخدام الدالة الجديدة لإحصائيات الحالات الأصلية
                default_status_stats = _get_delivery_status_stats(
                    employee.store_id, 
                    None if is_delivery_manager else employee.id
                )
                
                # جلب مناديب التوصيل (للمدير فقط)
                delivery_employees = []
                if is_delivery_manager:
                    delivery_employees = Employee.query.filter_by(
                        store_id=employee.store_id, 
                        role='delivery',
                        is_active=True
                    ).all()
                
                # حساب الإحصائيات بناءً على الحالة الأصلية
                in_progress_count = len([order for order in all_orders 
                                       if order.status and 'قيد' in order.status.name])
                completed_count = len([order for order in all_orders 
                                     if order.status and 'تم' in order.status.name])
                
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
                # 🔹 باقي الموظفين
                assignments = OrderAssignment.query.filter_by(employee_id=employee.id).all()
                assigned_order_ids = [a.order_id for a in assignments]
                assigned_orders = SallaOrder.query.filter(
                    SallaOrder.id.in_(assigned_order_ids)
                ).all() if assigned_order_ids else []

                # ✅ هنا التعديل: لو المراجع أو المدير يجيب كل الطلبات في المتجر
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

                    # استخدام الحالات الأصلية بدلاً من المخصصة
                    default_statuses = OrderStatus.query.filter_by(store_id=employee.store_id).all()

                    custom_status_stats = []
                    for status in default_statuses:
                        count = SallaOrder.query.filter_by(
                            store_id=employee.store_id,
                            status_id=status.id
                        ).count()
                        custom_status_stats.append({'status': status, 'count': count})

                    recent_statuses = OrderStatusNote.query.join(SallaOrder).filter(
                        SallaOrder.store_id == employee.store_id
                    ).options(
                        db.joinedload(OrderStatusNote.admin),
                        db.joinedload(OrderStatusNote.employee),
                        db.joinedload(OrderStatusNote.custom_status)
                    ).order_by(OrderStatusNote.created_at.desc()).limit(5).all()

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
                    # 🔹 الموظف العادي (غير مراجع/مدير) زي ما هو
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

                    # استخدام الحالات الأصلية بدلاً من المخصصة
                    default_statuses = OrderStatus.query.filter_by(store_id=employee.store_id).all()

                    custom_status_stats = []
                    for status in default_statuses:
                        count = SallaOrder.query.join(OrderAssignment).filter(
                            OrderAssignment.employee_id == employee.id,
                            SallaOrder.status_id == status.id
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
    """إرجاع الطلبات المصفاة فقط (لطلبات AJAX) بناءً على الحالة الأصلية"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        if is_admin:
            return "غير مصرح للمديرين", 403
            
        employee = request.current_user
        if not employee or employee.role not in ('delivery', 'delivery_manager'):
            return "غير مصرح", 403
            
        # الحصول على الحالة المحددة من الباراميتر (الحالة الأصلية)
        selected_status_id = request.args.get('status_id')
        
        # تحديد نطاق الطلبات بناءً على صلاحية الموظف مع التصفية للرياض فقط
        if employee.role == 'delivery_manager':
            # المدير يرى جميع طلبات المتجر في الرياض فقط
            filtered_orders, _ = _get_filtered_orders(
                employee.store_id, 
                selected_status_id,
                for_delivery=True
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
                    for_delivery=True
                )
                filtered_orders = [order for order in store_orders if order.id in assigned_order_ids]
            else:
                filtered_orders = []
        
        # إرجاع جزء HTML فقط بدلاً من الصفحة كاملة
        return render_template('orders_partial.html', filtered_orders=filtered_orders)
        
    except Exception as e:
        logger.error(f"خطأ في filter_orders: {str(e)}")
        return "حدث خطأ أثناء جلب الطلبات", 500
        
        
from flask_wtf.csrf import validate_csrf
from wtforms import ValidationError

@dashboard_bp.route('/check_late_orders', methods=['POST'])
@login_required
def check_late_orders():
    """فحص الطلبات المتأخرة يدوياً"""
    try:
        logger.info("🔍 بدء فحص الطلبات المتأخرة - طلب POST مستلم")
        
        # التحقق من CSRF Token
        csrf_token = request.headers.get('X-CSRFToken') or request.form.get('csrf_token')
        if not csrf_token:
            logger.warning("❌ طلب بدون CSRF token")
            return {
                'success': False,
                'message': 'رمز التحقق من الصلاحية مطلوب'
            }, 400
        
        try:
            validate_csrf(csrf_token)
            logger.info("✅ CSRF token صالح")
        except ValidationError as e:
            logger.error(f"❌ CSRF token غير صالح: {str(e)}")
            return {
                'success': False,
                'message': 'رمز التحقق من الصلاحية غير صالح'
            }, 400
        
        # تسجيل معلومات المستخدم
        is_admin = request.cookies.get('is_admin') == 'true'
        logger.info(f"👤 نوع المستخدم: {'مدير' if is_admin else 'موظف'}")
        
        if is_admin:
            user = request.current_user
            store_id = user.store_id
            logger.info(f"🏪 متجر المدير: {store_id}, البريد: {user.email}")
        else:
            employee = request.current_user
            store_id = employee.store_id
            logger.info(f"🏪 متجر الموظف: {store_id}, البريد: {employee.email}")
        
        if not store_id:
            logger.error("❌ خطأ: لا يوجد متجر مرتبط بحسابك")
            return {
                'success': False,
                'message': 'لا يوجد متجر مرتبط بحسابك'
            }, 400
        
        logger.info(f"🔍 جاري استيراد الدالة من scheduler_tasks...")
        
        # استيراد الدالة بشكل آمن
        try:
            from .scheduler_tasks import check_and_update_late_orders_for_store
            logger.info("✅ تم استيراد الدالة بنجاح")
        except ImportError as e:
            logger.error(f"❌ فشل استيراد الدالة: {str(e)}")
            return {
                'success': False,
                'message': 'فشل في تحميل وظيفة الفحص'
            }, 500
        
        logger.info(f"🚀 بدء فحص الطلبات المتأخرة للمتجر {store_id}")
        
        # استدعاء الدالة من scheduler_tasks.py للمتجر الحالي فقط
        updated_count = check_and_update_late_orders_for_store(store_id)
        
        if updated_count > 0:
            message = f'تم تحديث {updated_count} طلب إلى حالة متأخر في متجرك'
            logger.info(f"✅ {message}")
        else:
            message = 'لا توجد طلبات متأخرة تحتاج تحديث في متجرك'
            logger.info(f"✅ {message}")
            
        return {
            'success': True,
            'message': message,
            'updated_count': updated_count,
            'store_id': store_id
        }
        
    except Exception as e:
        logger.error(f"❌ خطأ في فحص الطلبات المتأخرة: {str(e)}", exc_info=True)
        return {
            'success': False,
            'message': f'حدث خطأ أثناء فحص الطلبات المتأخرة: {str(e)}'
        }, 500