from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, session
from .models import User, Employee, OrderStatusNote, db
import logging
from datetime import datetime
from functools import wraps
from .user_auth import auth_required, redirect_to_login  # نستورد auth_required من هنا
# نزيل استيراد admin_required و get_current_user من auth_utils

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ... (لا حاجة للديكوراتورات المحلية) ...

@dashboard_bp.route('/')
@auth_required()
def index():
    """لوحة التحكم الرئيسية بدون إحصائيات الحالات"""
    try:
        # الحصول على المستخدم الحالي من request (تم تعيينه في الديكوراتور)
        current_user = request.current_user
        
        # استخدام بيانات الجلسة مباشرة
        is_admin = session.get('is_admin', False)
        store_id = session.get('store_id', 0)
        employee_role = session.get('employee_role', '')

        # للمديرين
        if is_admin:
            # التحقق من أن المستخدم فعلاً مدير (يجب أن يكون من نوع User)
            if not isinstance(current_user, User):
                flash('خطأ في صلاحيات المستخدم', 'danger')
                return redirect_to_login()
            
            # إحصائيات بديلة للمدير (بدون استخدام OrderStatusNote)
            stats = {
                'welcome_message': 'مرحبًا بك في لوحة التحكم',
                'last_login': datetime.now().strftime('%Y-%m-%d %H:%M'),
                'store_id': store_id
            }
            
            return render_template('dashboard.html',
                                current_user=current_user,
                                is_admin=True,
                                stats=stats)
        
        # للموظفين
        else:
            # التحقق من أن المستخدم فعلاً موظف (من نوع Employee)
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
            
            # للموظفين العاديين (بدون إحصائيات الحالات)
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
        return redirect_to_login()

@dashboard_bp.route('/profile')
@auth_required()
def profile():
    """صفحة الملف الشخصي"""
    current_user = request.current_user
    if isinstance(current_user, User):
        return render_template('profile.html', user=current_user)
    return render_template('employee_profile.html', employee=current_user)

@dashboard_bp.route('/settings')
@auth_required(admin_only=True)  # استخدام الديكوراتور المعدل
def settings():
    """صفحة الإعدادات (للمدراء فقط)"""
    current_user = request.current_user
    return render_template('settings.html', user=current_user)