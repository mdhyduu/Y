from flask import Blueprint, render_template, redirect, url_for, flash, request, session 
from .models import db, Department, Employee, EmployeePermission, User
from .utils import format_date
from .config import Config
import requests

permissions_bp = Blueprint('permissions', __name__, url_prefix='/dashboard/permissions')
 
@permissions_bp.route('/')
def manage_permissions():
    """صفحة إدارة صلاحيات الموظفين"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    user = User.query.get(session['user_id'])
    
    # جلب جميع الموظفين والأقسام
    employees = Employee.query.filter_by(store_id=user.store_id).all()
    departments = Department.query.filter_by(store_id=user.store_id).all()
    
    # الحصول على الموظف المحدد إذا كان هناك معرف في الطلب
    selected_employee_id = request.args.get('employee_id')
    selected_employee = None
    employee_permissions = []
    
    if selected_employee_id:
        selected_employee = Employee.query.get(selected_employee_id)
        if selected_employee:
            # الحصول على الأقسام المسموحة لهذا الموظف
            employee_permissions = [p.department for p in selected_employee.permissions]
    
    return render_template('permissions/manage.html', 
                         employees=employees,
                         departments=departments,
                         selected_employee=selected_employee,
                         employee_permissions=employee_permissions)

@permissions_bp.route('/update', methods=['POST'])
def update_permissions():
    """تحديث صلاحيات الموظف"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    employee_id = request.form.get('employee_id')
    if not employee_id:
        flash('لم يتم تحديد موظف', 'error')
        return redirect(url_for('permissions.manage_permissions'))
    
    employee = Employee.query.get(employee_id)
    if not employee:
        flash('الموظف غير موجود', 'error')
        return redirect(url_for('permissions.manage_permissions'))
    
    # حذف جميع الصلاحيات الحالية للموظف
    EmployeePermission.query.filter_by(employee_id=employee_id).delete()
    
    # إضافة الصلاحيات الجديدة
    user = User.query.get(session['user_id'])
    departments = Department.query.filter_by(store_id=user.store_id).all()
    
    for department in departments:
        if f'department_{department.id}' in request.form:
            permission = EmployeePermission(
                employee_id=employee_id,
                department_id=department.id
            )
            db.session.add(permission)
    
    db.session.commit()
    flash('تم تحديث الصلاحيات بنجاح', 'success')
    return redirect(url_for('permissions.manage_permissions', employee_id=employee_id))

@permissions_bp.route('/departments', methods=['GET', 'POST'])
def manage_departments():
    """صفحة إدارة الأقسام"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    user = User.query.get(session['user_id'])
    has_salla_token = bool(user.salla_access_token)
    
    # مزامنة الأقسام من سلة إذا طلب المستخدم ذلك
    if request.method == 'POST' and 'sync_categories' in request.form:
        return sync_categories()
    
    # إضافة قسم جديد
    if request.method == 'POST' and 'name' in request.form:
        name = request.form['name']
        if name:
            # التحقق من عدم وجود قسم بنفس الاسم
            existing = Department.query.filter_by(name=name, store_id=user.store_id).first()
            if existing:
                flash('اسم القسم موجود مسبقاً', 'error')
            else:
                new_department = Department(
                    name=name,
                    store_id=user.store_id
                )
                db.session.add(new_department)
                flash('تم إضافة القسم بنجاح', 'success')
    
    # حذف الأقسام المحددة
    if request.method == 'POST':
        for department in Department.query.filter_by(store_id=user.store_id).all():
            if f'delete_{department.id}' in request.form:
                # حذف جميع الصلاحيات المرتبطة بهذا القسم أولاً
                EmployeePermission.query.filter_by(department_id=department.id).delete()
                db.session.delete(department)
                flash(f'تم حذف قسم: {department.name}', 'success')
        
        db.session.commit()
    
    departments = Department.query.filter_by(store_id=user.store_id).all()
    return render_template('permissions/departments.html', 
                          departments=departments,
                          has_salla_token=has_salla_token)

def sync_categories():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    user = User.query.get(session['user_id'])
    
    if not user.salla_access_token:
        flash('يرجى ربط المتجر أولاً', 'error')
        return redirect(url_for('auth.link_store'))
    
    # تأكد من وجود store_id للمستخدم
    if not user.store_id:
        # حاول تعيين store_id افتراضي إذا لم يكن موجودًا
        user.store_id = f"store_{user.id}"
        db.session.commit()
        flash('تم تعيين معرف متجر افتراضي', 'warning')
    
    try:
        headers = {
            'Authorization': f'Bearer {user.salla_access_token}',
            'Accept': 'application/json'
        }
        response = requests.get(Config.SALLA_CATEGORIES_API, headers=headers)
        response.raise_for_status()
        categories_data = response.json().get('data', [])
        
        added_count = 0
        for category in categories_data:
            existing = Department.query.filter_by(salla_id=category['id']).first()
            if not existing:
                new_category = Department(
                    salla_id=category['id'],
                    name=category['name'],
                    store_id=user.store_id  # استخدم store_id من المستخدم
                )
                db.session.add(new_category)
                added_count += 1
        
        db.session.commit()
        flash(f'تم مزامنة {added_count} قسم جديد من سلة', 'success')
    except Exception as e:
        flash(f'خطأ في مزامنة الأقسام: {str(e)}', 'error')
    
    return redirect(url_for('permissions.manage_departments'))

@permissions_bp.route('/remove_permission/<int:permission_id>', methods=['POST'])
def remove_permission(permission_id):
    """إزالة صلاحية من موظف"""
    if 'user_id' not in session or not session.get('is_admin'):
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    permission = EmployeePermission.query.get(permission_id)
    if not permission:
        flash('الصلاحية غير موجودة', 'error')
        return redirect(url_for('permissions.manage_permissions'))
    
    employee_id = permission.employee_id
    db.session.delete(permission)
    db.session.commit()
    flash('تم إزالة الصلاحية بنجاح', 'success')
    return redirect(url_for('employees.detail_employee', employee_id=employee_id))