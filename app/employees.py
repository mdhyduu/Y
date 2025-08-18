from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect()
from flask import Blueprint, render_template, redirect, url_for, request, flash, make_response
from .models import db, User, Employee
from datetime import datetime

employees_bp = Blueprint('employees', __name__, url_prefix='/dashboard')

@employees_bp.route('/employees')
def list_employees():
    user_id = request.cookies.get('user_id')
    is_admin = request.cookies.get('is_admin') == 'true'
    
    if not user_id:
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    user = User.query.get(user_id)
    if not user:
        flash('المستخدم غير موجود', 'error')
        return redirect(url_for('user_auth.login'))
    
    employees = Employee.query.filter_by(store_id=user.store_id).all()
    return render_template('employees/list.html', 
                        employees=employees,
                        now=datetime.utcnow())

@employees_bp.route('/employees/add', methods=['GET', 'POST'])
def add_employee():
    user_id = request.cookies.get('user_id')
    is_admin = request.cookies.get('is_admin') == 'true'
    is_delivery_manager = request.cookies.get('employee_role') == 'delivery_manager'
    
    if not user_id:
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    # الحصول على المستخدم الحالي
    user = User.query.get(user_id)
    if not user:
        flash('المستخدم غير موجود', 'error')
        return redirect(url_for('user_auth.login'))
    
    store_id = user.store_id  # التصحيح هنا: الحصول من المستخدم بدل الكوكيز
    
    if not (is_admin or is_delivery_manager):
        flash('ليس لديك صلاحية إضافة موظفين', 'error')
        return redirect(url_for('dashboard.index'))
    
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        role = request.form['role']
        
        if is_delivery_manager and role != 'delivery':
            flash('يمكنك فقط إضافة مناديب توصيل', 'error')
            return redirect(url_for('employees.add_employee'))
        
        existing_employee = Employee.query.filter_by(email=email).first()
        if existing_employee:
            flash('البريد الإلكتروني موجود مسبقاً', 'error')
            return redirect(url_for('employees.add_employee'))
        
        region = 'الرياض' if role in ('delivery', 'delivery_manager') else None
        
        new_employee = Employee(
            email=email,
            store_id=store_id,  # استخدام store_id من المستخدم
            role=role,
            region=region
        )
        new_employee.set_password(password)
        db.session.add(new_employee)
        db.session.commit()
        
        flash('تمت إضافة الموظف بنجاح', 'success')
        return redirect(url_for('employees.list_employees'))
    
    if is_delivery_manager:
        roles = [('delivery', 'مندوب توصيل')]
    else:
        roles = [
            ('general', 'موظف عام'),
            ('delivery', 'مندوب توصيل'),
            ('delivery_manager', 'مدير التوصيل'),
            ('reviewer', 'مراجع الطلبات')
        ]
    
    return render_template('employees/add.html', 
                        roles=roles,
                        is_delivery_manager=is_delivery_manager)
@employees_bp.route('/employees/<int:employee_id>/delete', methods=['POST'])
def delete_employee(employee_id):
    user_id = request.cookies.get('user_id')
    is_admin = request.cookies.get('is_admin') == 'true'
    is_delivery_manager = request.cookies.get('employee_role') == 'delivery_manager'
    
    if not user_id or not (is_admin or is_delivery_manager):
        flash('غير مصرح لك بهذا الإجراء', 'error')
        return redirect(url_for('user_auth.login'))
    
    employee = Employee.query.get(employee_id)
    if not employee:
        flash('الموظف غير موجود', 'error')
        return redirect(url_for('employees.list_employees'))
    
    if is_delivery_manager and employee.role == 'delivery_manager':
        flash('لا يمكنك حذف مدراء آخرين', 'error')
        return redirect(url_for('employees.list_employees'))
    
    db.session.delete(employee)
    db.session.commit()
    
    flash('تم حذف الموظف بنجاح', 'success')
    return redirect(url_for('employees.list_employees'))

@employees_bp.route('/employees/<int:employee_id>/toggle_active', methods=['POST'])
def toggle_employee_active(employee_id):
    user_id = request.cookies.get('user_id')
    is_admin = request.cookies.get('is_admin') == 'true'
    is_delivery_manager = request.cookies.get('employee_role') == 'delivery_manager'
    
    if not user_id or not (is_admin or is_delivery_manager):
        flash('غير مصرح لك بهذا الإجراء', 'error')
        return redirect(url_for('user_auth.login'))
    
    employee = Employee.query.get(employee_id)
    if not employee:
        flash('الموظف غير موجود', 'error')
        return redirect(url_for('employees.list_employees'))
    
    if is_delivery_manager and employee.role == 'delivery_manager':
        flash('لا يمكنك تعديل حالة مدراء آخرين', 'error')
        return redirect(url_for('employees.list_employees'))
    
    employee.is_active = not employee.is_active
    
    if not employee.is_active:
        employee.deactivated_at = datetime.utcnow()
    else:
        employee.deactivated_at = None
    
    db.session.commit()
    
    action = "تم تفعيل الموظف" if employee.is_active else "تم إيقاف الموظف"
    flash(f'{action} بنجاح', 'success')
    return redirect(url_for('employees.list_employees'))