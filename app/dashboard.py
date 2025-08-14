from flask import Blueprint, render_template, flash, redirect, url_for, session
from .models import User, Employee, db
import logging
from datetime import datetime
from .auth_utils import admin_required, get_current_user
from .user_auth import auth_required

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
logger = logging.getLogger(__name__)

@dashboard_bp.route('/')
@auth_required()
def index():
    try:
        current_user = get_current_user()
        
        if not current_user:
            flash('جلسة العمل منتهية، يرجى تسجيل الدخول مرة أخرى', 'warning')
            return redirect(url_for('user_auth.login'))
        
        is_admin = session.get('is_admin', False)
        store_id = session.get('store_id', 0)
        employee_role = session.get('employee_role', '')

        if is_admin:
            stats = {
                'welcome_message': 'مرحبًا بك في لوحة التحكم',
                'last_login': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'store_id': store_id
            }
            
            return render_template('dashboard.html',
                                current_user=current_user,
                                is_admin=True,
                                stats=stats)
        else:
            if not current_user.is_active:
                flash('حسابك غير نشط، يرجى التواصل مع المدير', 'danger')
                return redirect(url_for('user_auth.login'))
            
            if employee_role in ('delivery', 'delivery_manager'):
                store_admin = User.query.filter_by(store_id=store_id).first()
                return render_template('dashboard/delivery.html',
                                    current_user=store_admin,
                                    is_delivery_manager=(employee_role == 'delivery_manager'),
                                    employee=current_user)
            
            stats = {
                'welcome_message': 'مرحبًا بك في لوحة التحكم',
                'last_login': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'store_id': store_id
            }
            
            store_admin = User.query.filter_by(store_id=store_id).first()
            return render_template('employee_dashboard.html',
                                current_user=store_admin,
                                employee=current_user,
                                stats=stats)

    except Exception as e:
        logger.error(f"خطأ في لوحة التحكم: {str(e)}")
        flash('حدث خطأ في النظام، يرجى المحاولة لاحقاً', 'danger')
        return redirect(url_for('user_auth.login'))

@dashboard_bp.route('/profile')
@auth_required()
def profile():
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('user_auth.login'))
    
    if session.get('is_admin'):
        return render_template('profile.html', user=current_user)
    return render_template('employee_profile.html', employee=current_user)

@dashboard_bp.route('/settings')
@admin_required
def settings():
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('user_auth.login'))
    return render_template('settings.html', user=current_user)