from flask import Blueprint, render_template, flash, redirect, url_for, request, make_response
from .models import Employee, User, OrderDelivery, OrderAssignment, db
import requests
from .config import Config
from functools import wraps

delivery_bp = Blueprint('delivery', __name__, url_prefix='/delivery')

def delivery_login_required(view_func):
    """ديكوراتور للتحقق من تسجيل الدخول وتفويض التوصيل"""
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        user_id = request.cookies.get('user_id')
        employee_role = request.cookies.get('employee_role')
        store_id = request.cookies.get('store_id')
        
        # التحقق من وجود البيانات الأساسية
        if not user_id or not employee_role or not store_id:
            flash('غير مصرح لك بالوصول', 'error')
            return redirect(url_for('user_auth.login'))
        
        # التحقق من أن الموظف مندوب توصيل
        if employee_role not in ['delivery', 'delivery_manager']:
            flash('غير مصرح لك بالوصول', 'error')
            return redirect(url_for('dashboard.index'))
        
        # جلب بيانات الموظف
        employee = Employee.query.get(user_id)
        if not employee:
            flash('بيانات الموظف غير موجودة', 'error')
            response = make_response(redirect(url_for('user_auth.login')))
            response.delete_cookie('user_id')
            response.delete_cookie('employee_role')
            response.delete_cookie('store_id')
            return response
        
        # تخزين البيانات في الطلب للاستخدام لاحقاً
        request.current_employee = employee
        request.store_id = store_id
        return view_func(*args, **kwargs)
    return wrapper

@delivery_bp.route('/orders')
@delivery_login_required
def delivery_orders():
    employee = request.current_employee
    store_id = request.store_id
    
    user = User.query.filter_by(store_id=store_id).first()
    if not user or not user.salla_access_token:
        flash('يجب ربط المتجر مع سلة أولاً', 'error')
        return redirect(url_for('auth.link_store'))

    try:
        headers = {'Authorization': f'Bearer {user.salla_access_token}'}
        response = requests.get(Config.SALLA_ORDERS_API, headers=headers)
        response.raise_for_status()
        
        # جلب الطلبات المسلمة من قبل الموظفين في نفس المتجر
        delivered_orders = OrderDelivery.query.join(Employee).filter(
            Employee.store_id == store_id
        ).all()
        delivered_order_ids = {d.order_id for d in delivered_orders}
        
        # جلب جميع الإسنادات للمتجر
        assignments = OrderAssignment.query.join(Employee).filter(
            Employee.store_id == store_id
        ).all()
        assignment_dict = {a.order_id: a.employee for a in assignments}
        
        orders = []
        for order in response.json().get('data', []):
            # تحديد المدينة
            city = None
            if 'shipping' in order and 'city' in order['shipping']:
                city = order['shipping']['city']
            elif 'customer' in order and 'city' in order['customer']:
                city = order['customer']['city']
            
            # فقط طلبات الرياض
            if city != 'الرياض':
                continue
            
            # للمدير: جميع طلبات الرياض
            # للمندوب: فقط الطلبات المسندة إليه والتي تم تسليمها
            show_order = False
            if employee.role == 'delivery_manager':
                show_order = True
            else:
                # الطلبات المسندة للموظف
                assigned_employee = assignment_dict.get(str(order.get('id')))
                if assigned_employee and assigned_employee.id == employee.id:
                    show_order = True
                
                # الطلبات التي سلمها الموظف بنفسه
                if str(order.get('id')) in delivered_order_ids:
                    show_order = True
            
            if not show_order:
                continue
            
            # إضافة معلومات الإسناد
            assigned_employee = assignment_dict.get(str(order.get('id')))
            
            orders.append({
                'id': order.get('id'),
                'customer': {
                    'first_name': order.get('customer', {}).get('first_name', ''),
                    'last_name': order.get('customer', {}).get('last_name', '')
                },
                'created_at': order.get('date', {}).get('date', ''),
                'amount': order.get('amounts', {}).get('total', {}).get('amount', 0),
                'currency': order.get('currency', 'SAR'),
                'status': order.get('status', {}).get('name', 'غير معروف'),
                'city': city,
                'assigned_to': assigned_employee,
                'is_delivered': str(order.get('id')) in delivered_order_ids
            })
        
        return render_template('delivery/orders.html', 
                              orders=orders,
                              is_delivery_manager=(employee.role == 'delivery_manager'),
                              employee=employee)
    
    except Exception as e:
        flash(f'حدث خطأ: {str(e)}', 'error')
        return redirect(url_for('dashboard.index'))

@delivery_bp.route('/scan_barcode', methods=['GET', 'POST'])
@delivery_login_required
def scan_barcode():
    employee = request.current_employee
    
    if employee.role != 'delivery':
        flash('غير مصرح لك', 'error')
        return redirect(url_for('dashboard.index'))
    
    if request.method == 'POST':
        order_id = request.form.get('order_id')
        # التحقق من أن الطلب موجود في سلة
        user = User.query.filter_by(store_id=request.store_id).first()
        if not user or not user.salla_access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            return redirect(url_for('auth.link_store'))
        
        try:
            headers = {'Authorization': f'Bearer {user.salla_access_token}'}
            response = requests.get(f"{Config.SALLA_ORDERS_API}/{order_id}", headers=headers)
            response.raise_for_status()
            
            # إذا كان الطلب موجودًا، ننتقل إلى صفحة التسليم
            return redirect(url_for('delivery.deliver_order', order_id=order_id))
        
        except requests.exceptions.HTTPError:
            flash('رقم الطلب غير صحيح أو غير موجود', 'error')
        except Exception as e:
            flash(f'حدث خطأ: {str(e)}', 'error')
    
    return render_template('delivery/scan_barcode.html')

@delivery_bp.route('/deliver_order/<order_id>', methods=['GET', 'POST'])
@delivery_login_required
def deliver_order(order_id):
    employee = request.current_employee
    
    if employee.role != 'delivery':
        flash('غير مصرح لك', 'error')
        return redirect(url_for('dashboard.index'))
    
    # التحقق من أن الطلب مسند إلى هذا الموظف
    assignment = OrderAssignment.query.filter_by(
        order_id=order_id,
        employee_id=employee.id
    ).first()
    
    if not assignment:
        flash('هذا الطلب غير مسند لك', 'error')
        return redirect(url_for('delivery.scan_barcode'))
    
    if request.method == 'POST':
        # تسجيل تسليم الطلب
        delivery = OrderDelivery(order_id=order_id, employee_id=employee.id)
        db.session.add(delivery)
        db.session.commit()
        
        flash('تم تسجيل تسليم الطلب بنجاح', 'success')
        return redirect(url_for('delivery.delivery_orders'))
    
    # عرض تفاصيل الطلب للتأكيد
    user = User.query.filter_by(store_id=request.store_id).first()
    if not user or not user.salla_access_token:
        flash('يجب ربط المتجر مع سلة أولاً', 'error')
        return redirect(url_for('auth.link_store'))
    
    try:
        headers = {'Authorization': f'Bearer {user.salla_access_token}'}
        response = requests.get(f"{Config.SALLA_ORDERS_API}/{order_id}", headers=headers)
        response.raise_for_status()
        order = response.json().get('data', {})
        
        # تحديد المدينة
        city = None
        if 'shipping' in order and 'city' in order['shipping']:
            city = order['shipping']['city']
        elif 'customer' in order and 'city' in order['customer']:
            city = order['customer']['city']
        
        return render_template('delivery/confirm_delivery.html', 
                             order_id=order_id,
                             order={
                                 'id': order.get('id'),
                                 'customer': {
                                     'first_name': order.get('customer', {}).get('first_name', ''),
                                     'last_name': order.get('customer', {}).get('last_name', '')
                                 },
                                 'created_at': order.get('date', {}).get('date', ''),
                                 'amount': order.get('amounts', {}).get('total', {}).get('amount', 0),
                                 'currency': order.get('currency', 'SAR'),
                                 'status': order.get('status', {}).get('name', 'غير معروف'),
                                 'city': city
                             })
    
    except Exception as e:
        flash(f'حدث خطأ: {str(e)}', 'error')
        return redirect(url_for('delivery.scan_barcode'))

@delivery_bp.route('/assign_order/<order_id>', methods=['GET', 'POST'])
@delivery_login_required
def assign_order(order_id):
    employee = request.current_employee
    
    if employee.role != 'delivery_manager':
        flash('غير مصرح لك', 'error')
        return redirect(url_for('dashboard.index'))
    
    # جلب المندوبين الذين أضافهم مدير التوصيل الحالي فقط
    delivery_employees = Employee.query.filter(
        Employee.store_id == request.store_id,
        Employee.role == 'delivery',
        Employee.added_by == employee.id  # افترضنا وجود حقل added_by في النموذج
    ).all()
    
    if request.method == 'POST':
        selected_employee_id = request.form.get('employee_id')
        
        # تحقق من أن المندوب المختار مضاف بواسطة المدير الحالي
        selected_employee = next((e for e in delivery_employees if e.id == int(selected_employee_id)), None)
        if not selected_employee:
            flash('المندوب المحدد غير مصرح به', 'error')
            return redirect(url_for('delivery.assign_order', order_id=order_id))
        
        # ... باقي الكود كما هو
        else:
            new_assignment = OrderAssignment(
                order_id=order_id, 
                employee_id=selected_employee_id
            )
            db.session.add(new_assignment)
        
        db.session.commit()
        flash('تم إسناد الطلب بنجاح', 'success')
        return redirect(url_for('delivery.delivery_orders'))
    
    return render_template('delivery/assign_order.html', 
                         order_id=order_id,
                         employees=delivery_employees)
@delivery_bp.route('/manage_delivery_employees', methods=['GET', 'POST'])
@delivery_login_required
def manage_delivery_employees():
    employee = request.current_employee
    
    if employee.role != 'delivery_manager':
        flash('غير مصرح لك', 'error')
        return redirect(url_for('dashboard.index'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # التحقق من البريد الإلكتروني
        if not email or '@' not in email:
            flash('بريد إلكتروني غير صالح', 'error')
            return redirect(url_for('delivery.manage_delivery_employees'))
        
        # التحقق من عدم وجود موظف بنفس البريد
        existing_employee = Employee.query.filter_by(email=email).first()
        if existing_employee:
            flash('هذا البريد الإلكتروني مسجل مسبقًا', 'error')
            return redirect(url_for('delivery.manage_delivery_employees'))
        
        # إنشاء الموظف الجديد
        new_employee = Employee(
            email=email,
            password=generate_password_hash(password),
            role='delivery',
            store_id=request.store_id,
            added_by=employee.id  # تحديد المدير الذي أضافه
        )
        
        db.session.add(new_employee)
        db.session.commit()
        flash('تم إضافة المندوب بنجاح', 'success')
        return redirect(url_for('delivery.manage_delivery_employees'))
    
    # جلب مناديب المدير الحالي فقط
    delivery_employees = Employee.query.filter(
        Employee.store_id == request.store_id,
        Employee.role == 'delivery',
        Employee.added_by == employee.id
    ).all()
    
    return render_template('delivery/manage_employees.html', 
                          employees=delivery_employees)@delivery_bp.route('/manage_delivery_employees', methods=['GET', 'POST'])
@delivery_login_required
def manage_delivery_employees():
    employee = request.current_employee
    
    if employee.role != 'delivery_manager':
        flash('غير مصرح لك', 'error')
        return redirect(url_for('dashboard.index'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        # التحقق من البريد الإلكتروني
        if not email or '@' not in email:
            flash('بريد إلكتروني غير صالح', 'error')
            return redirect(url_for('delivery.manage_delivery_employees'))
        
        # التحقق من عدم وجود موظف بنفس البريد
        existing_employee = Employee.query.filter_by(email=email).first()
        if existing_employee:
            flash('هذا البريد الإلكتروني مسجل مسبقًا', 'error')
            return redirect(url_for('delivery.manage_delivery_employees'))
        
        # إنشاء الموظف الجديد
        new_employee = Employee(
            email=email,
            password=generate_password_hash(password),
            role='delivery',
            store_id=request.store_id,
            added_by=employee.id  # تحديد المدير الذي أضافه
        )
        
        db.session.add(new_employee)
        db.session.commit()
        flash('تم إضافة المندوب بنجاح', 'success')
        return redirect(url_for('delivery.manage_delivery_employees'))
    
    # جلب مناديب المدير الحالي فقط
    delivery_employees = Employee.query.filter(
        Employee.store_id == request.store_id,
        Employee.role == 'delivery',
        Employee.added_by == employee.id
    ).all()
    
    return render_template('delivery/manage_employees.html', 
                          employees=delivery_employees)