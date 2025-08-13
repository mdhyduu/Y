from flask import Blueprint, render_template, request, redirect, url_for, flash, make_response, session 
from .models import User, Employee, OrderStatusNote, db
from datetime import datetime, timedelta
from functools import wraps
from .user_auth import auth_required  # استيراد من user_auth فقط

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

@dashboard_bp.route('/')
@auth_required()  # استخدام الديكوراتور الموحد
def index():
    current_user = None
    is_admin = session.get('is_admin') or request.cookies.get('is_admin') == 'true'
    user_id = session.get('user_id') or request.cookies.get('user_id')

    if is_admin:
        current_user = User.query.get(user_id)
        if not current_user:
            return redirect_to_login()
        return render_template('dashboard.html', current_user=current_user)
    else:
        employee = Employee.query.get(user_id)
        if not employee:
            return redirect_to_login()

        store_admin = User.query.filter_by(store_id=employee.store_id).first()
        
        if employee.role in ('delivery', 'delivery_manager'):
            return render_template('dashboard/delivery.html',
                                current_user=store_admin,
                                is_delivery_manager=(employee.role == 'delivery_manager'),
                                employee=employee)
        
        # للموظفين العاديين
        stats = {
            'new_orders': OrderStatusNote.query.filter_by(store_id=employee.store_id, status_flag='new').count(),
            'late_orders': OrderStatusNote.query.filter_by(store_id=employee.store_id, status_flag='late').count(),
            'missing_orders': OrderStatusNote.query.filter_by(store_id=employee.store_id, status_flag='missing').count(),
            'refunded_orders': OrderStatusNote.query.filter_by(store_id=employee.store_id, status_flag='refunded').count(),
            'not_shipped_orders': OrderStatusNote.query.filter_by(store_id=employee.store_id, status_flag='not_shipped').count(),
        }
        
        status_notes = OrderStatusNote.query.filter_by(
            store_id=employee.store_id
        ).order_by(OrderStatusNote.created_at.desc()).limit(50).all()
        
        return render_template('dashboard/employee.html',
                            current_user=store_admin,
                            employee=employee,
                            stats=stats,
                            status_notes=status_notes)

def redirect_to_login():
    response = make_response(redirect(url_for('user_auth.login')))
    response.delete_cookie('user_id')
    response.delete_cookie('is_admin')
    session.clear()
    return response