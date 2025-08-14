import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, current_app
from .models import User, Employee, OrderStatusNote, db
from datetime import datetime, timedelta
from functools import wraps
import os

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
logger = logging.getLogger(__name__)

def get_db_path():
    """الحصول على مسار قاعدة البيانات مع التحقق من وجودها"""
    db_path = os.path.join(current_app.instance_path, 'app.db')
    if not os.path.exists(db_path):
        logger.error(f"ملف قاعدة البيانات غير موجود في: {db_path}")
        raise RuntimeError("ملف قاعدة البيانات غير موجود")
    return db_path

def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        try:
            logger.debug("التحقق من تسجيل الدخول...")
            user_id = request.cookies.get('user_id')
            
            if not user_id:
                logger.warning("محاولة وصول بدون تسجيل دخول")
                flash('يجب تسجيل الدخول أولاً', 'warning')
                return redirect(url_for('user_auth.login', _scheme='https'))
            
            # التحقق من صحة الجلسة في قاعدة البيانات
            is_admin = request.cookies.get('is_admin') == 'true'
            
            if is_admin:
                user = User.query.get(user_id)
                if not user:
                    logger.warning(f"المشرف غير موجود في قاعدة البيانات: {user_id}")
                    resp = make_response(redirect(url_for('user_auth.login', _scheme='https'))
                    # حذف جميع الكوكيز
                    for cookie in ['user_id', 'is_admin', 'employee_role', 'store_id', 
                                 'salla_access_token', 'salla_refresh_token']:
                        resp.delete_cookie(cookie)
                    return resp
            else:
                employee = Employee.query.get(user_id)
                if not employee:
                    logger.warning(f"الموظف غير موجود في قاعدة البيانات: {user_id}")
                    resp = make_response(redirect(url_for('user_auth.login', _scheme='https'))
                    # حذف جميع الكوكيز
                    for cookie in ['user_id', 'is_admin', 'employee_role', 'store_id', 
                                 'salla_access_token', 'salla_refresh_token']:
                        resp.delete_cookie(cookie)
                    return resp
            
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
        
        # التحقق من اتصال قاعدة البيانات
        try:
            db.session.execute('SELECT 1').scalar()
        except Exception as db_error:
            logger.error(f"فشل الاتصال بقاعدة البيانات: {str(db_error)}")
            flash('حدث خطأ في الاتصال بالنظام. يرجى المحاولة لاحقاً', 'danger')
            return redirect(url_for('user_auth.login', _scheme='https'))
        
        if is_admin:
            user = db.session.query(User).get(user_id)
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
            employee = db.session.query(Employee).get(user_id)
            if not employee:
                logger.warning(f"الموظف غير موجود في قاعدة البيانات: {user_id}")
                flash('بيانات الموظف غير موجودة', 'error')
                resp = make_response(redirect(url_for('user_auth.login', _scheme='https')))
                resp.delete_cookie('user_id')
                resp.delete_cookie('is_admin')
                return resp
            
            user = db.session.query(User).filter_by(store_id=employee.store_id).first()
            
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
                
                # تحسين استعلامات SQLite لتجنب التحميل الزائد
                try:
                    # إحصائيات الطلبات - استعلام واحد لكل إحصائية
                    stats = {
                        'new_orders': db.session.query(OrderStatusNote).filter_by(status_flag='new').count(),
                        'late_orders': db.session.query(OrderStatusNote).filter_by(status_flag='late').count(),
                        'missing_orders': db.session.query(OrderStatusNote).filter_by(status_flag='missing').count(),
                        'refunded_orders': db.session.query(OrderStatusNote).filter_by(status_flag='refunded').count(),
                        'not_shipped_orders': db.session.query(OrderStatusNote).filter_by(status_flag='not_shipped').count(),
                    }
                    
                    # الحالات التي تحتاج متابعة (جميع الحالات المخصصة)
                    custom_statuses = db.session.query(OrderStatusNote).order_by(OrderStatusNote.created_at.desc()).all()
                    
                    # آخر 5 حالات مضافة
                    recent_statuses = db.session.query(OrderStatusNote).order_by(OrderStatusNote.created_at.desc()).limit(5).all()
                    
                    # تحسين جلب بيانات المستخدمين بطلب واحد
                    user_ids = {status.created_by for status in custom_statuses + recent_statuses if status.created_by}
                    users = {user.id: user for user in db.session.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}
                    
                    for status in custom_statuses + recent_statuses:
                        if status.created_by:
                            status.user = users.get(status.created_by)
                    
                    logger.debug(f"تم تحميل {len(custom_statuses)} حالة مخصصة و{len(recent_statuses)} حالات حديثة")
                    
                    return render_template('employee_dashboard.html',
                                        current_user=user,
                                        employee=employee,
                                        stats=stats,
                                        custom_statuses=custom_statuses,
                                        recent_statuses=recent_statuses)
                
                except Exception as query_error:
                    logger.error(f"خطأ في استعلامات قاعدة البيانات: {str(query_error)}", exc_info=True)
                    flash('حدث خطأ في جلب بيانات الطلبات. يرجى المحاولة لاحقاً', 'danger')
                    return redirect(url_for('dashboard.index'))
    
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
            user = db.session.query(User).get(user_id)
            if not user:
                logger.error(f"المستخدم غير موجود: {user_id}")
                flash('المستخدم غير موجود', 'danger')
                return redirect(url_for('dashboard.index'))
            logger.info(f"عرض ملف المشرف: {user.email}")
            return render_template('profile.html', user=user)
        else:
            employee = db.session.query(Employee).get(user_id)
            if not employee:
                logger.error(f"الموظف غير موجود: {user_id}")
                flash('الموظف غير موجود', 'danger')
                return redirect(url_for('dashboard.index'))
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
        user = db.session.query(User).get(user_id)
        if not user:
            logger.error(f"المستخدم غير موجود: {user_id}")
            flash('المستخدم غير موجود', 'danger')
            return redirect(url_for('dashboard.index'))
        
        logger.info(f"عرض إعدادات المشرف: {user.email}")
        return render_template('settings.html', user=user)
    except Exception as e:
        logger.error(f"خطأ في جلب الإعدادات: {str(e)}", exc_info=True)
        flash('حدث خطأ في جلب صفحة الإعدادات', 'danger')
        return redirect(url_for('dashboard.index'))
@dashboard_bp.before_request
@login_required
def refresh_session():
    """تجديد مدة الجلسة في كل طلب"""
    user_id = request.cookies.get('user_id')
    if user_id:
        # تجديد مدة الجلسة
        resp = make_response()
        cookie_settings = {
            'secure': current_app.config['SESSION_COOKIE_SECURE'],
            'httponly': True,
            'samesite': 'Lax',
            'path': '/'
        }
        resp.set_cookie('user_id', user_id, 
                       max_age=timedelta(days=1).total_seconds(),
                       **cookie_settings)
        return resp
@dashboard_bp.before_app_request
def check_db_connection():
    try:
        db.session.execute('SELECT 1')
    except Exception as e:
        logger.error(f"فشل الاتصال بقاعدة البيانات: {str(e)}")
        # إعادة الاتصال
        db.session.rollback()
        db.session.close()
        db.session.bind.pool.recreate()