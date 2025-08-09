from flask import Blueprint, render_template, session, redirect, url_for, flash
from .models import User, Employee, OrderStatusNote, db
from datetime import datetime

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@dashboard_bp.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('user_auth.login'))
    
    # للمستخدمين العامين (المدراء)
    if session.get('is_admin'):
        user = User.query.get(session['user_id'])
        return render_template('dashboard.html', current_user=user)
    
    # للموظفين
    employee = Employee.query.get(session['user_id'])
    if not employee:
        flash('بيانات الموظف غير موجودة', 'error')
        return redirect(url_for('user_auth.login'))
    
    user = User.query.filter_by(store_id=employee.store_id).first()
    
    # تحديد نوع لوحة التحكم حسب الدور
    if employee.role in ('delivery', 'delivery_manager'):
        is_delivery_manager = (employee.role == 'delivery_manager')
        session['is_delivery_manager'] = is_delivery_manager
         
        return render_template('dashboard.html',
                            current_user=user,
                            is_delivery_manager=is_delivery_manager,
                            employee=employee)
    else:
        return redirect(url_for('orders.employee_dashboard'))
        # جلب الإحصائيات والحالات المخصصة للموظفين (غير مسؤولي التوصيل)
        try:
            # إحصائيات الحالات المخصصة
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
            
        except Exception as e:
            # في حالة حدوث خطأ، نستخدم قيم افتراضية
            stats = {
                'new_orders': 0,
                'late_orders': 0,
                'missing_orders': 0,
                'refunded_orders': 0,
                'not_shipped_orders': 0,
            }
            custom_statuses = []
            recent_statuses = []
            flash(f"حدث خطأ في جلب بيانات لوحة التحكم: {str(e)}", "error")
        
        return render_template('employee_dashboard.html',
                            current_user=user,
                            employee=employee,
                            stats=stats,
                            custom_statuses=custom_statuses,
                            recent_statuses=recent_statuses)