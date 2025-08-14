import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, current_app
from .models import User, Employee, OrderStatusNote, db
from datetime import datetime, timedelta
from functools import wraps

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
logger = logging.getLogger(__name__)

def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        try:
            logger.debug("التحقق من تسجيل الدخول...")
            user_id = request.cookies.get('user_id')
            
            if not user_id:
                logger.warning("محاولة وصول بدون تسجيل دخول")
                flash('يجب تسجيل الدخول أولاً', 'warning')
                return redirect(url_for('user_auth.login', _scheme='https'))
            
            # تحقق من أن user_id رقمية
            if not user_id.isdigit():
                logger.warning(f"معرف مستخدم غير صالح: {user_id}")
                resp = make_response(redirect(url_for('user_auth.login', _scheme='https')))
                resp.delete_cookie('user_id')
                resp.delete_cookie('is_admin')
                resp.delete_cookie('employee_role')
                resp.delete_cookie('store_id')
                return resp
            
            is_admin = request.cookies.get('is_admin') == 'true'
            
            if is_admin:
                user = User.query.get(user_id)
                if not user:
                    logger.warning(f"المشرف غير موجود في قاعدة البيانات: {user_id}")
                    resp = make_response(redirect(url_for('user_auth.login', _scheme='https')))
                    resp.delete_cookie('user_id')
                    resp.delete_cookie('is_admin')
                    resp.delete_cookie('employee_role')
                    resp.delete_cookie('store_id')
                    return resp
                request.current_user = user
                logger.debug(f"تم التحقق من المشرف: {user.email}")
            else:
                employee = Employee.query.get(user_id)
                if not employee:
                    logger.warning(f"الموظف غير موجود في قاعدة البيانات: {user_id}")
                    flash('بيانات الموظف غير موجودة', 'error')
                    resp = make_response(redirect(url_for('user_auth.login', _scheme='https')))
                    resp.delete_cookie('user_id')
                    resp.delete_cookie('is_admin')
                    resp.delete_cookie('employee_role')
                    resp.delete_cookie('store_id')
                    return resp
                request.current_user = employee
                logger.debug(f"تم التحقق من الموظف: {employee.email}")
            
            return view_func(*args, **kwargs)
        except Exception as e:
            logger.error(f"خطأ في التحقق من تسجيل الدخول: {str(e)}", exc_info=True)
            flash('حدث خطأ في التحقق من هويتك. يرجى تسجيل الدخول مرة أخرى', 'danger')
            return redirect(url_for('user_auth.login', _scheme='https'))
    return wrapper

@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        logger.info("تحميل لوحة التحكم...")
        is_admin = request.cookies.get('is_admin') == 'true'
        user_id = request.cookies.get('user_id')
        
        if is_admin:
            # للمستخدمين العامين (المدراء)
            user = User.query.get(user_id)
            if not user:
                logger.warning(f"المشرف غير موجود في قاعدة البيانات: {user_id}")
                resp = make_response(redirect(url_for('user_auth.login', _scheme='https')))
                resp.delete_cookie('user_id')
                resp.delete_cookie('is_admin')
                return resp
                
            logger.info(f"عرض لوحة تحكم المشرف: {user.email}")
            return render_template('dashboard.html', 
                                current_user=user,
                                is_admin=True)
        
        else:
            # للموظفين
            employee = Employee.query.get(user_id)
            if not employee:
                logger.warning(f"الموظف غير موجود في قاعدة البيانات: {user_id}")
                flash('بيانات الموظف غير موجودة', 'error')
                resp = make_response(redirect(url_for('user_auth.login', _scheme='https')))
                resp.delete_cookie('user_id')
                resp.delete_cookie('is_admin')
                return resp
            
            user = User.query.filter_by(store_id=employee.store_id).first()
            
            # تحديد نوع لوحة التحكم حسب الدور
            if employee.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (employee.role == 'delivery_manager')
                logger.info(f"عرض لوحة تحكم التوصيل للموظف: {employee.email} (مدير: {is_delivery_manager})")
                return render_template('dashboard.html',
                                    current_user=user,
                                    is_delivery_manager=is_delivery_manager,
                                    employee=employee)
            else:
                logger.info(f"عرض لوحة تحكم الموظف: {employee.email} (الدور: {employee.role})")
                
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
                user_ids = {status.created_by for status in custom_statuses + recent_statuses}
                users = {user.id: user for user in User.query.filter(User.id.in_(user_ids)).all()}
                
                for status in custom_statuses + recent_statuses:
                    status.user = users.get(status.created_by)
                
                logger.debug(f"تم تحميل {len(custom_statuses)} حالة مخصصة و{len(recent_statuses)} حالات حديثة")
                
                return render_template('employee_dashboard.html',
                                    current_user=user,
                                    employee=employee,
                                    stats=stats,
                                    custom_statuses=custom_statuses,
                                    recent_statuses=recent_statuses)
    
    except Exception as e:
        logger.error(f"خطأ في جلب لوحة التحكم: {str(e)}", exc_info=True)
        flash(f"حدث خطأ في جلب بيانات لوحة التحكم: {str(e)}", "error")
        return redirect(url_for('user_auth.login', _scheme='https'))

@dashboard_bp.route('/profile')
@login_required
def profile():
    """صفحة الملف الشخصي"""
    try:
        logger.info("تحميل صفحة الملف الشخصي...")
        is_admin = request.cookies.get('is_admin') == 'true'
        user_id = request.cookies.get('user_id')
        
        if is_admin:
            user = User.query.get(user_id)
            logger.info(f"عرض ملف المشرف: {user.email}")
            return render_template('profile.html', user=user)
        else:
            employee = Employee.query.get(user_id)
            logger.info(f"عرض ملف الموظف: {employee.email}")
            return render_template('employee_profile.html', employee=employee)
    except Exception as e:
        logger.error(f"خطأ في جلب الملف الشخصي: {str(e)}", exc_info=True)
        flash('حدث خطأ في جلب بيانات الملف الشخصي', 'danger')
        return redirect(url_for('dashboard.index'))

@dashboard_bp.route('/settings')
@login_required
def settings():
    """صفحة الإعدادات"""
    try:
        logger.info("تحميل صفحة الإعدادات...")
        is_admin = request.cookies.get('is_admin') == 'true'
        if not is_admin:
            logger.warning("محاولة وصول غير مصرح بها إلى الإعدادات")
            flash('ليس لديك صلاحية الوصول إلى هذه الصفحة', 'danger')
            return redirect(url_for('dashboard.index'))
        
        user_id = request.cookies.get('user_id')
        user = User.query.get(user_id)
        logger.info(f"عرض إعدادات المشرف: {user.email}")
        return render_template('settings.html', user=user)
    except Exception as e:
        logger.error(f"خطأ في جلب الإعدادات: {str(e)}", exc_info=True)
        flash('حدث خطأ في جلب صفحة الإعدادات', 'danger')
        return redirect(url_for('dashboard.index'))