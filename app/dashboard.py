from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, session
from .models import User, Employee, OrderStatusNote, db
from datetime import datetime
from functools import wraps
from .user_auth import auth_required, redirect_to_login  # استيراد من user_auth بدلاً من التعريف المحلي
from .auth_utils import admin_required, get_current_user
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

# ==============================================
# ديكوراتورات المصادقة المدمجة (بدون ملف منفصل)
# ==============================================






def get_order_stats(store_id):
    """إحصائيات الطلبات للمتجر"""
    return { 
        'new_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='new').count(),
        'late_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='late').count(),
        'missing_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='missing').count(),
        'refunded_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='refunded').count(),
        'not_shipped_orders': OrderStatusNote.query.filter_by(store_id=store_id, status_flag='not_shipped').count(),
    }

# ==============================================
# روابط لوحة التحكم
# ==============================================
@dashboard_bp.route('/')
@auth_required()
def index():
    """لوحة التحكم الرئيسية"""
    try:
        # الحصول على المستخدم الحالي
        current_user = get_current_user()
        
        # التحقق من وجود المستخدم
        if not current_user:
            flash('جلسة العمل منتهية، يرجى تسجيل الدخول مرة أخرى', 'warning')
            return redirect_to_login()
        
        # استخدام بيانات الجلسة مباشرة لتجنب مشاكل التوافق
        is_admin = session.get('is_admin', False)
        store_id = session.get('store_id', 0)
        employee_role = session.get('employee_role', '')

        # للمديرين
        if is_admin:
            # التحقق من أن المستخدم فعلاً مدير
            if not isinstance(current_user, User):
                flash('خطأ في صلاحيات المستخدم', 'danger')
                return redirect_to_login()
            
            # إحصائيات الطلبات للمتجر
            stats = get_order_stats(store_id)
            
            # ملاحظات حالة الطلب
            status_notes = OrderStatusNote.query.filter_by(
                store_id=store_id
            ).order_by(OrderStatusNote.created_at.desc()).limit(50).all()
            
            return render_template('dashboard.html',
                                current_user=current_user,
                                is_admin=True,
                                stats=stats,
                                status_notes=status_notes)
        
        # للموظفين
        else:
            # التحقق من أن المستخدم فعلاً موظف
            if not isinstance(current_user, Employee):
                flash('خطأ في صلاحيات المستخدم', 'danger')
                return redirect_to_login()
            
            # التحقق من حالة الموظف
            if not current_user.is_active:
                flash('حسابك غير نشط، يرجى التواصل مع المدير', 'danger')
                return redirect_to_login()
            
            # لمندوبي التوصيل ومديري التوصيل
            if employee_role in ('delivery', 'delivery_manager'):
                store_admin = User.query.filter_by(store_id=store_id).first()
                
                return render_template('dashboard/delivery.html',
                                    current_user=store_admin,
                                    is_delivery_manager=(employee_role == 'delivery_manager'),
                                    employee=current_user)
            
            # للموظفين العاديين
            stats = get_order_stats(store_id)
            status_notes = OrderStatusNote.query.filter_by(
                store_id=store_id
            ).order_by(OrderStatusNote.created_at.desc()).limit(50).all()
            
            store_admin = User.query.filter_by(store_id=store_id).first()
            
            return render_template('employee_dashboard.html',
                                current_user=store_admin,
                                employee=current_user,
                                stats=stats,
                                status_notes=status_notes)

    except Exception as e:
        logger.error(f"خطأ فادح في لوحة التحكم: {str(e)}")
        flash('حدث خطأ جسيم في النظام، يرجى المحاولة لاحقاً', 'danger')
        return redirect_to_login()
@dashboard_bp.route('/profile')
@auth_required()
def profile():
    """صفحة الملف الشخصي"""
    current_user = get_current_user()
    if isinstance(current_user, User):
        return render_template('profile.html', user=current_user)
    return render_template('employee_profile.html', employee=current_user)

@dashboard_bp.route('/settings')
@admin_required
def settings():
    """صفحة الإعدادات (للمدراء فقط)"""
    current_user = get_current_user()
    return render_template('settings.html', user=current_user)