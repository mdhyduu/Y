from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, g, session
from .models import User, Employee, OrderStatusNote, db
from datetime import datetime, timedelta
from functools import wraps
from sqlalchemy import func, case
import hashlib
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')




# دالة مساعدة لتوليد مفتاح تخزين مؤقت
def get_session_key(user_id):
    return f"user_{user_id}"

# ديكوراتور التحقق من الدخول مع التخزين المؤقت
def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول مع تحسين الأداء"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user_id = request.cookies.get('user_id')
        if not user_id:
            flash('يجب تسجيل الدخول أولاً', 'warning')
            return redirect(url_for('user_auth.login'))
        
        # التحقق من أن user_id رقمية
        if not user_id.isdigit():
            resp = make_response(redirect(url_for('user_auth.login')))
            clear_auth_cookies(resp)
            return resp
        
        # التحقق من التخزين المؤقت أولاً
        session_key = get_session_key(user_id)
        cached_user = session.get(session_key)
        
        # إذا كانت الجلسة المؤقتة صالحة
        if cached_user and cached_user.get('expires') > datetime.utcnow().timestamp():
            g.current_user = cached_user['user']
            return view_func(*args, **kwargs)
        
        # إذا لم توجد جلسة مؤقتة أو انتهت صلاحيتها
        is_admin = request.cookies.get('is_admin') == 'true'
        
        if is_admin:
            user = User.query.get(user_id)
            if not user:
                resp = make_response(redirect(url_for('user_auth.login')))
                clear_auth_cookies(resp)
                return resp
            g.current_user = user
            # تخزين مؤقت للجلسة (30 دقيقة)
            session[session_key] = {
                'user': user,
                'expires': (datetime.utcnow() + timedelta(minutes=30)).timestamp()
            }
        else:
            employee = Employee.query.get(user_id)
            if not employee:
                flash('بيانات الموظف غير موجودة', 'error')
                resp = make_response(redirect(url_for('user_auth.login')))
                clear_auth_cookies(resp)
                return resp
            g.current_user = employee
            # تخزين مؤقت للجلسة (30 دقيقة)
            session[session_key] = {
                'user': employee,
                'expires': (datetime.utcnow() + timedelta(minutes=30)).timestamp()
            }
        
        return view_func(*args, **kwargs)
    return wrapper

# دالة مساعدة لحذف الكوكيز
def clear_auth_cookies(response):
    cookies = ['user_id', 'is_admin', 'employee_role', 'store_id', 
               'salla_access_token', 'salla_refresh_token']
    for cookie in cookies:
        response.delete_cookie(cookie, path='/')
@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        current_user = g.current_user
        
        if is_admin:
            return render_template('dashboard.html', 
                                current_user=current_user,
                                is_admin=True)
        
        else:
            # تحديد نوع لوحة التحكم حسب الدور
            if current_user.role in ('delivery', 'delivery_manager'):
                is_delivery_manager = (current_user.role == 'delivery_manager')
                return render_template('dashboard.html',
                                    current_user=current_user,
                                    is_delivery_manager=is_delivery_manager,
                                    employee=current_user)
            else:
                # استعلام واحد فعال للحصول على جميع الإحصائيات
                counts = db.session.query(
                    func.sum(case((OrderStatusNote.status_flag == 'late', 1), else_=0)).label('late'),
                    func.sum(case((OrderStatusNote.status_flag == 'missing', 1), else_=0)).label('missing'),
                    func.sum(case((OrderStatusNote.status_flag == 'refunded', 1), else_=0)).label('refunded'),
                    func.sum(case((OrderStatusNote.status_flag == 'not_shipped', 1), else_=0)).label('not_shipped')
                ).one()
                
                stats = {
                    'new_orders': 0,  # يمكن استبدالها ببيانات حقيقية
                    'late_orders': counts[0] or 0,
                    'missing_orders': counts[1] or 0,
                    'refunded_orders': counts[2] or 0,
                    'not_shipped_orders': counts[3] or 0,
                }
                
                # الحالات التي تحتاج متابعة (جميع الحالات المخصصة)
                custom_statuses = OrderStatusNote.query.order_by(OrderStatusNote.created_at.desc()).all()
                
                # آخر 5 حالات مضافة
                recent_statuses = OrderStatusNote.query.order_by(OrderStatusNote.created_at.desc()).limit(5).all()
                
                # تحسين جلب معلومات المستخدمين باستخدام استعلام واحد
                user_ids = {status.created_by for status in custom_statuses + recent_statuses}
                users = {user.id: user for user in User.query.filter(User.id.in_(user_ids)).all()}
                
                for status in custom_statuses + recent_statuses:
                    status.user = users.get(status.created_by)
                
                return render_template('employee_dashboard.html',
                                    current_user=current_user,
                                    employee=current_user,
                                    stats=stats,
                                    custom_statuses=custom_statuses,
                                    recent_statuses=recent_statuses)
    
    except Exception as e:
        flash(f"حدث خطأ في جلب بيانات لوحة التحكم: {str(e)}", "error")
        return redirect(url_for('user_auth.login'))

@dashboard_bp.route('/profile')
@login_required
def profile():
    """صفحة الملف الشخصي"""
    current_user = g.current_user
    
    if request.cookies.get('is_admin') == 'true':
        return render_template('profile.html', user=current_user)
    else:
        return render_template('employee_profile.html', employee=current_user)

@dashboard_bp.route('/settings')
@login_required
def settings():
    """صفحة الإعدادات"""
    if not request.cookies.get('is_admin') == 'true':
        flash('ليس لديك صلاحية الوصول إلى هذه الصفحة', 'danger')
        return redirect(url_for('dashboard.index'))
    
    return render_template('settings.html', user=g.current_user)

# إضافة دالة لإدارة جلسات قاعدة البيانات
@dashboard_bp.teardown_request
def teardown_request(exception=None):
    """إغلاق جلسة قاعدة البيانات عند انتهاء الطلب"""
    db.session.remove()