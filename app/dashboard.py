from flask import Blueprint, render_template, flash, redirect, url_for, session
from .models import User, Employee
import logging
from datetime import datetime
from .auth_utils import admin_required, get_current_user
from .user_auth import auth_required

# تهيئة البلوبنت والسجل
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
logger = logging.getLogger(__name__)

@dashboard_bp.route('/')
@auth_required()
def index():
    """لوحة التحكم الرئيسية"""
    try:
        current_user = get_current_user()
        if not current_user:
            return redirect(url_for('user_auth.login'))

        is_admin = session.get('is_admin', False)
        store_id = session.get('store_id', 0)
        employee_role = session.get('employee_role', '')

        # إحصائيات أساسية
        stats = {
            'welcome_message': 'مرحباً بك في لوحة التحكم',
            'last_login': datetime.now().strftime('%Y-%m-%d %H:%M'),
            'store_id': store_id
        }

        # لوحة المدير
        if is_admin:
            if not isinstance(current_user, User):
                flash('خطأ في صلاحيات المستخدم', 'danger')
                return redirect(url_for('user_auth.login'))

            return render_template('dashboard.html', current_user=current_user)

        # لوحة الموظف
        else:
            if not isinstance(current_user, Employee):
                flash('خطأ في صلاحيات المستخدم', 'danger')
                return redirect(url_for('user_auth.login'))

            if not current_user.is_active:
                flash('حسابك غير مفعل، يرجى التواصل مع المدير', 'danger')
                return redirect(url_for('user_auth.login'))

            store_admin = User.query.filter_by(store_id=store_id).first()

            # لوحة مندوب التوصيل
            if employee_role in ('delivery', 'delivery_manager'):
                return render_template('dashboard/delivery_dashboard.html',
                                    admin=store_admin,
                                    employee=current_user,
                                    is_manager=(employee_role == 'delivery_manager'),
                                    stats=stats)

            # لوحة الموظف العادي
            return render_template('dashboard/employee_dashboard.html',
                                admin=store_admin,
                                employee=current_user,
                                stats=stats)

    except Exception as e:
        logger.error(f"خطأ في لوحة التحكم: {e}")
        flash('حدث خطأ في النظام، يرجى المحاولة لاحقاً', 'danger')
        return redirect(url_for('user_auth.login'))

@dashboard_bp.route('/profile')
@auth_required()
def profile():
    """صفحة الملف الشخصي"""
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('user_auth.login'))

    if session.get('is_admin'):
        return render_template('dashboard/admin_profile.html', user=current_user)
    return render_template('dashboard/employee_profile.html', employee=current_user)

@dashboard_bp.route('/settings')
@admin_required
def settings():
    """صفحة الإعدادات (للمدير فقط)"""
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('user_auth.login'))
    return render_template('dashboard/settings.html', user=current_user)