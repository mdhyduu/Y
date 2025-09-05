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
from sqlalchemy.orm import joinedload
import logging
logger = logging.getLogger(__name__)
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user_id = request.cookies.get('user_id')
        is_admin = request.cookies.get('is_admin') == 'true'
        
        logger.info(f"محاولة دخول إلى لوحة التحكم - user_id: {user_id}, is_admin: {is_admin}")
        
        # إذا لم يكن هناك user_id، نعيد التوجيه إلى تسجيل الدخول
        if not user_id:
            logger.warning("لم يتم العثور على user_id في الكوكيز")
            flash('يجب تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('user_auth.login'))
        
        # إذا كان user_id غير رقمية، نحذف الكوكيز ونعيد التوجيه
        if not user_id.isdigit():
            logger.warning(f"user_id غير رقمي: {user_id}")
            return clear_cookies_and_redirect()
        
        try:
            if is_admin:
                user = User.query.get(int(user_id))
                if not user:
                    logger.error(f"لم يتم العثور على المستخدم بالرقم: {user_id}")
                    flash('بيانات المستخدم غير موجودة', 'error')
                    return clear_cookies_and_redirect()
                request.current_user = user
                logger.info(f"تم التحقق من هوية المشرف: {user.email}")
            else:
                employee = Employee.query.get(int(user_id))
                if not employee:
                    logger.error(f"لم يتم العثور على الموظف بالرقم: {user_id}")
                    flash('بيانات الموظف غير موجودة', 'error')
                    return clear_cookies_and_redirect()
                request.current_user = employee
                logger.info(f"تم التحقق من هوية الموظف: {employee.email}")
            
            return view_func(*args, **kwargs)
        except Exception as e:
            logger.error(f"خطأ في التحقق من تسجيل الدخول: {str(e)}", exc_info=True)
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

# ... بقية الكود كما هو

@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        
        if is_admin:
            user = request.current_user
            
            # جلب جميع الموظفين للمتجر
            all_employees = Employee.query.filter_by(
                store_id=user.store_id, 
                is_active=True
            ).all()
            
            # جلب معرف الموظف المحدد من query string إذا وجد
            selected_employee_id = request.args.get('employee_id', type=int)
            selected_employee = None
            
            # جلب جميع الطلبات للمتجر
            all_orders = SallaOrder.query.options(joinedload(SallaOrder.status)).filter_by(store_id=user.store_id).all()
            
            # حساب الإحصائيات الشاملة مع روابط لتوجيه للصفحة المفلترة
            stats = {
                'total_orders': len(all_orders),
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
            # روابط للفلترة حسب الحالة العامة
            stats_links = {
                'late_orders': url_for('orders.index', status='late'),
                'missing_orders': url_for('orders.index', status='missing'),
                'refunded_orders': url_for('orders.index', status='refunded'),
                'not_shipped_orders': url_for('orders.index', status='not_shipped'),
                'total_orders': url_for('orders.index')  # رابط عام إلى صفحة الطلبات
            }
            
            # جلب الحالات المخصصة للمتجر
            custom_statuses = CustomNoteStatus.query.filter_by(store_id=user.store_id).all()
            
            # حساب عدد الطلبات لكل حالة مخصصة + رابط فلترة (custom_status => id)
            custom_status_stats = []
            for status in custom_statuses:
                count = db.session.query(OrderStatusNote).join(SallaOrder).filter(
                    OrderStatusNote.custom_status_id == status.id,
                    SallaOrder.store_id == user.store_id
                ).count()
                custom_status_stats.append({
                    'status': status,
                    'count': count,
                    'link': url_for('orders.index', custom_status=status.id)
                })
            
            # جلب آخر النشاطات
            recent_statuses = db.session.query(OrderStatusNote).join(SallaOrder).filter(
                SallaOrder.store_id == user.store_id
            ).order_by(OrderStatusNote.created_at.desc()).limit(10).all()
            
            # جلب عدد الموظفين
            employees_count = Employee.query.filter_by(store_id=user.store_id).count()
            
            # جلب عدد المنتجات (افتراضي)
            products_count = 0
            
            # إذا تم اختيار موظف معين
            if selected_employee_id:
                selected_employee = next((emp for emp in all_employees if emp.id == selected_employee_id), None)
                if selected_employee:
                    # جلب الحالات التلقائية للموظف المحدد
                    default_statuses_selected = EmployeeCustomStatus.query.filter_by(
                        employee_id=selected_employee_id,
                        is_default=True
                    ).all()
                    
                    # حساب عدد الطلبات لكل حالة تلقائية للموظف المحدد + رابط فلترة (custom_status)
                    default_status_stats = []
                    for status in default_statuses_selected:
                        count = OrderEmployeeStatus.query.filter(
                            OrderEmployeeStatus.status_id == status.id
                        ).count()
                        
                        default_status_stats.append({
                            'name': status.name,
                            'color': status.color,
                            'count': count,
                            'link': url_for('orders.index', custom_status=status.id)
                        })
                    
                    # جلب الحالات المخصصة التي أضافها الموظف المحدد
                    custom_statuses_selected = EmployeeCustomStatus.query.filter_by(
                        employee_id=selected_employee_id,
                        is_default=False
                    ).all()
                    
                    # حساب عدد الطلبات لكل حالة مخصصة للموظف المحدد + رابط
                    custom_status_stats_selected = []
                    for status in custom_statuses_selected:
                        count = OrderEmployeeStatus.query.filter(
                            OrderEmployeeStatus.status_id == status.id
                        ).count()
                        
                        custom_status_stats_selected.append({
                            'name': status.name,
                            'color': status.color,
                            'count': count,
                            'link': url_for('orders.index', custom_status=status.id)
                        })
                    
                    return render_template('dashboard.html', 
                                        current_user=user,
                                        stats=stats,
                                        stats_links=stats_links,
                                        custom_status_stats=custom_status_stats,
                                        recent_statuses=recent_statuses,
                                        employees_count=employees_count,
                                        products_count=products_count,
                                        all_employees=all_employees,
                                        selected_employee=selected_employee,
                                        default_status_stats=default_status_stats,
                                        custom_status_stats_selected=custom_status_stats_selected,
                                        is_admin=True)
            
            # إذا لم يتم اختيار موظف، نعرض إحصائيات الحالات التلقائية لجميع الموظفين
            all_default_statuses = EmployeeCustomStatus.query.filter_by(
                is_default=True
            ).join(Employee).filter(
                Employee.store_id == user.store_id
            ).all()
            
            # تجميع الحالات التلقائية حسب الاسم (بدون تكرار) وجمع عدد الطلبات لكل حالة
            status_stats_dict = {}
            for status in all_default_statuses:
                count = OrderEmployeeStatus.query.filter(
                    OrderEmployeeStatus.status_id == status.id
                ).count()
    
                if status.name in status_stats_dict:
                    status_stats_dict[status.name]['count'] += count
                else:
                    # نحتفظ أيضاً بـ id الأولى الموجودة لنستعملها كرابط فلترة
                    status_stats_dict[status.name] = {
                        'name': status.name,
                        'color': status.color,
                        'count': count,
                        'example_id': status.id
                    }
    
            # تحويل القاموس إلى قائمة، ونتولّد رابط الفلترة باستخدام example_id
            all_employee_status_stats = []
            for v in status_stats_dict.values():
                all_employee_status_stats.append({
                    'name': v['name'],
                    'color': v['color'],
                    'count': v['count'],
                    'link': url_for('orders.index', custom_status=v['example_id'])
                })
            
            return render_template('dashboard.html', 
                                current_user=user,
                                stats=stats,
                                stats_links=stats_links,
                                custom_status_stats=custom_status_stats,
                                recent_statuses=recent_statuses,
                                employees_count=employees_count,
                                products_count=products_count,
                                all_employees=all_employees,
                                all_employee_status_stats=all_employee_status_stats,
                                is_admin=True)
    
        # ... بقية الكود للموظفين غير المديرين
        # (وفّر نفس المعاملة: أضِف روابط url_for('orders.index', ...) في الأقسام الخاصة بالموظفين كذلك)                
                else:
                    # للموظفين العاديين
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