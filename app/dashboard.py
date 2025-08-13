from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, session, current_app
from .models import User, Employee, OrderStatusNote, SallaOrder, Product, db
from datetime import datetime, timedelta
from functools import wraps
import logging

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
logger = logging.getLogger(__name__)

def login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        # التحقق من الجلسة أولاً
        if 'user_id' not in session:
            # التحقق من الكوكيز للتوافق مع الإصدارات القديمة
            user_id = request.cookies.get('user_id')
            if not user_id or not user_id.isdigit():
                flash('يجب تسجيل الدخول أولاً', 'warning')
                return redirect(url_for('user_auth.login'))
            
            # إذا كان هناك كوكي، إنشاء جلسة منه
            user_type = 'admin' if request.cookies.get('is_admin') == 'true' else 'employee'
            session['user_id'] = int(user_id)
            session['user_type'] = user_type
            session['is_admin'] = user_type == 'admin'
            
            if user_type == 'employee':
                employee = Employee.query.get(user_id)
                if employee:
                    session['employee_role'] = employee.role
                    session['store_id'] = employee.store_id
                    session['email'] = employee.email
                else:
                    flash('بيانات الموظف غير موجودة', 'error')
                    return redirect(url_for('user_auth.login'))
            else:
                user = User.query.get(user_id)
                if user:
                    session['email'] = user.email
                    session['store_id'] = user.store_id
                else:
                    flash('بيانات المستخدم غير موجودة', 'error')
                    return redirect(url_for('user_auth.login'))
        
        # التحقق من أن المستخدم لا يزال موجوداً في قاعدة البيانات
        user_id = session['user_id']
        if session.get('user_type') == 'admin':
            user = User.query.get(user_id)
            if not user:
                session.clear()
                flash('جلسة العمل منتهية، يرجى تسجيل الدخول مرة أخرى', 'error')
                return redirect(url_for('user_auth.login'))
            request.current_user = user
        else:
            employee = Employee.query.get(user_id)
            if not employee or not employee.is_active:
                session.clear()
                flash('حسابك غير نشط أو غير موجود', 'error')
                return redirect(url_for('user_auth.login'))
            request.current_user = employee
        
        return view_func(*args, **kwargs)
    return wrapper

def admin_required(view_func):
    """ديكوراتور للتحقق من صلاحيات المشرف"""
    @wraps(view_func)
    @login_required
    def wrapper(*args, **kwargs):
        if not session.get('is_admin'):
            flash('ليس لديك صلاحية الوصول إلى هذه الصفحة', 'danger')
            return redirect(url_for('dashboard.index'))
        return view_func(*args, **kwargs)
    return wrapper

@dashboard_bp.route('/')
@login_required
def index():
    """لوحة التحكم الرئيسية"""
    try:
        store_id = session.get('store_id', 1)
        is_admin = session.get('is_admin', False)
        user_type = session.get('user_type')
        
        # جلب الإحصائيات الأساسية
        stats = {
            'orders_count': SallaOrder.query.filter_by(store_id=store_id).count(),
            'employees_count': Employee.query.filter_by(store_id=store_id, is_active=True).count(),
            'products_count': Product.query.filter_by(store_id=store_id).count(),
            'today_orders': SallaOrder.query.filter(
                SallaOrder.store_id == store_id,
                SallaOrder.created_at >= datetime.today().date()
            ).count()
        }

        # جلب آخر الطلبات
        recent_orders = SallaOrder.query.filter_by(
            store_id=store_id
        ).order_by(
            SallaOrder.created_at.desc()
        ).limit(5).all()

        # جلب آخر الملاحظات
        recent_notes = OrderStatusNote.query.join(
            SallaOrder
        ).filter(
            SallaOrder.store_id == store_id
        ).order_by(
            OrderStatusNote.created_at.desc()
        ).limit(5).all()

        # تحديد القالب المناسب حسب نوع المستخدم
        template_name = 'dashboard.html'
        if user_type == 'employee':
            employee_role = session.get('employee_role', 'general')
            if employee_role in ('delivery', 'delivery_manager'):
                template_name = 'delivery_dashboard.html'
            else:
                template_name = 'employee_dashboard.html'

        return render_template(
            template_name,
            current_user=request.current_user,
            stats=stats,
            recent_orders=recent_orders,
            recent_notes=recent_notes,
            is_admin=is_admin,
            now=datetime.now()
        )

    except Exception as e:
        logger.error(f"خطأ في لوحة التحكم: {str(e)}", exc_info=True)
        flash('حدث خطأ في جلب بيانات لوحة التحكم', 'danger')
        return redirect(url_for('user_auth.login'))

@dashboard_bp.route('/profile')
@login_required
def profile():
    """صفحة الملف الشخصي"""
    try:
        if session.get('user_type') == 'admin':
            user = User.query.get(session['user_id'])
            return render_template('admin_profile.html', user=user)
        else:
            employee = Employee.query.get(session['user_id'])
            return render_template('employee_profile.html', employee=employee)
    except Exception as e:
        logger.error(f"خطأ في جلب الملف الشخصي: {str(e)}", exc_info=True)
        flash('حدث خطأ في جلب بيانات الملف الشخصي', 'danger')
        return redirect(url_for('dashboard.index'))

@dashboard_bp.route('/settings')
@admin_required
def settings():
    """صفحة الإعدادات (للمشرفين فقط)"""
    try:
        user = User.query.get(session['user_id'])
        store_id = session.get('store_id', 1)
        
        # جلب معلومات المتجر
        store_info = {
            'linked': user.salla_access_token is not None,
            'last_sync': user.last_sync,
            'employees_count': Employee.query.filter_by(store_id=store_id).count(),
            'products_count': Product.query.filter_by(store_id=store_id).count()
        }
        
        return render_template('settings.html', 
                            user=user,
                            store_info=store_info)
    
    except Exception as e:
        logger.error(f"خطأ في جلب الإعدادات: {str(e)}", exc_info=True)
        flash('حدث خطأ في جلب بيانات الإعدادات', 'danger')
        return redirect(url_for('dashboard.index'))

@dashboard_bp.after_request
def add_security_headers(response):
    """إضافة رؤوس أمان للاستجابات"""
    # منع التخزين المؤقت للصفحات المؤتمنة
    if 'user_id' in session:
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, post-check=0, pre-check=0, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '-1'
    
    # حماية ضد هجمات XSS و MIME-sniffing
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    
    return response