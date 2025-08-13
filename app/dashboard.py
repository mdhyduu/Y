from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, session
from .models import User, Employee, OrderStatusNote, db
from datetime import datetime, timedelta
from functools import wraps
import logging

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
logger = logging.getLogger(__name__)

def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول باستخدام الجلسة"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        logger.info("===== بدء التحقق من تسجيل الدخول =====")
        
        # استخراج معلومات المستخدم من الجلسة
        user_id = session.get('user_id')
        user_type = session.get('user_type')
        
        if not user_id or not user_type:
            logger.warning("لم يتم العثور على جلسة نشطة")
            flash('يجب تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('user_auth.login'))
        
        logger.info(f"جلسة نشطة موجودة: user_id={user_id}, user_type={user_type}")
        
        if user_type == 'admin':
            user = User.query.get(user_id)
            if not user:
                logger.error(f"المستخدم غير موجود في قاعدة البيانات: user_id={user_id}")
                flash('بيانات المستخدم غير موجودة', 'error')
                # تنظيف الجلسة غير الصالحة
                session.clear()
                return redirect(url_for('user_auth.login'))
            request.current_user = user
            logger.info(f"تم تحميل بيانات المشرف: {user.email}")
            
        else:  # employee
            employee = Employee.query.get(user_id)
            if not employee:
                logger.error(f"الموظف غير موجود في قاعدة البيانات: user_id={user_id}")
                flash('بيانات الموظف غير موجودة', 'error')
                # تنظيف الجلسة غير الصالحة
                session.clear()
                return redirect(url_for('user_auth.login'))
            request.current_user = employee
            logger.info(f"تم تحميل بيانات الموظف: {employee.email}")
        
        logger.info("تم التحقق من تسجيل الدخول بنجاح")
        return view_func(*args, **kwargs)
    return wrapper

@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        logger.info("===== الدخول إلى لوحة التحكم الرئيسية =====")
        user_type = session.get('user_type')
        user_id = session.get('user_id')
        
        if user_type == 'admin':
            # للمستخدمين العامين (المدراء)
            user = User.query.get(user_id)
            if not user:
                logger.error(f"المستخدم غير موجود في قاعدة البيانات: {user_id}")
                flash('بيانات المستخدم غير موجودة', 'error')
                session.clear()
                return redirect(url_for('user_auth.login'))
                
            logger.info(f"عرض لوحة تحكم المشرف للمستخدم: {user.email}")
            return render_template('dashboard.html', 
                                current_user=user,
                                is_admin=True)
        
        else:
            # للموظفين
            employee = Employee.query.get(user_id)
            if not employee:
                logger.error(f"الموظف غير موجود في قاعدة البيانات: {user_id}")
                flash('بيانات الموظف غير موجودة', 'error')
                session.clear()
                return redirect(url_for('user_auth.login'))
            
            user = User.query.filter_by(store_id=employee.store_id).first()
            
            # تحديد نوع لوحة التحكم حسب الدور
            if employee.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (employee.role == 'delivery_manager')
                logger.info(f"عرض لوحة تحكم مسؤول التوصيل: {employee.email}, role={employee.role}")
                return render_template('dashboard.html',
                                    current_user=user,
                                    is_delivery_manager=is_delivery_manager,
                                    employee=employee)
            else:
                logger.info(f"عرض لوحة تحكم الموظفين: {employee.email}, role={employee.role}")
                
                # جلب الإحصائيات والحالات المخصصة للموظفين (غير مسؤولي التوصيل)
                stats = {
                    'new_orders': 0,  # يمكن استبدالها ببيانات حقيقية
                    'late_orders': OrderStatusNote.query.filter_by(status_flag='late').count(),
                    'missing_orders': OrderStatusNote.query.filter_by(status_flag='missing').count(),
                    'refunded_orders': OrderStatusNote.query.filter_by(status_flag='refunded').count(),
                    'not_shipped_orders': OrderStatusNote.query.filter_by(status_flag='not_shipped').count(),
                }
                
                # الحالات التي تحتاج متابعة (جميع الحالات المخصصة)
                custom_statuses = OrderStatusNote.query.order_by(OrderStatusNote.created_at.desc()).all()
                
                # آخر 5 حالات مضافة
                recent_statuses = OrderStatusNote.query.order_by(OrderStatusNote.created_at.desc()).limit(5).all()
                
                # إضافة معلومات المستخدم الذي أضاف الحالة
                for status in custom_statuses + recent_statuses:
                    status.user = User.query.get(status.created_by)
                
                return render_template('employee_dashboard.html',
                                    current_user=user,
                                    employee=employee,
                                    stats=stats,
                                    custom_statuses=custom_statuses,
                                    recent_statuses=recent_statuses)
    
    except Exception as e:
        logger.error(f"حدث خطأ في جلب بيانات لوحة التحكم: {str(e)}", exc_info=True)
        flash(f"حدث خطأ في جلب بيانات لوحة التحكم: {str(e)}", "error")
        return redirect(url_for('user_auth.login'))

@dashboard_bp.route('/profile')
@login_required
def profile():
    """صفحة الملف الشخصي"""
    logger.info("===== الدخول إلى صفحة الملف الشخصي =====")
    user_type = session.get('user_type')
    user_id = session.get('user_id')
    
    if user_type == 'admin':
        user = User.query.get(user_id)
        logger.info(f"عرض ملف المشرف الشخصي: {user.email}")
        return render_template('profile.html', user=user)
    else:
        employee = Employee.query.get(user_id)
        logger.info(f"عرض ملف الموظف الشخصي: {employee.email}")
        return render_template('employee_profile.html', employee=employee)

@dashboard_bp.route('/settings')
@login_required
def settings():
    """صفحة الإعدادات"""
    logger.info("===== الدخول إلى صفحة الإعدادات =====")
    user_type = session.get('user_type')
    if user_type != 'admin':
        logger.warning(f"محاولة غير مصرح بها للوصول إلى الإعدادات: user_id={session.get('user_id')}")
        flash('ليس لديك صلاحية الوصول إلى هذه الصفحة', 'danger')
        return redirect(url_for('dashboard.index'))
    
    user_id = session.get('user_id')
    user = User.query.get(user_id)
    logger.info(f"عرض صفحة الإعدادات للمشرف: {user.email}")
    return render_template('settings.html', user=user)