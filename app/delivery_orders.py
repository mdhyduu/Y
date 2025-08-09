from flask import Blueprint, render_template, session, flash, redirect, url_for, request
from .models import Employee, User, OrderDelivery, OrderAssignment, db
import requests
from .config import Config

delivery_bp = Blueprint('delivery', __name__, url_prefix='/delivery')

@delivery_bp.route('/orders')
def delivery_orders():
    if 'user_id' not in session:
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    employee = Employee.query.get(session['user_id'])
    if not employee or employee.role not in ['delivery', 'delivery_manager']:
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('dashboard.index'))
    
    user = User.query.filter_by(store_id=employee.store_id).first()
    if not user or not user.salla_access_token:
        flash('يجب ربط المتجر مع سلة أولاً', 'error')
        return redirect(url_for('auth.link_store'))

    try:
        headers = {'Authorization': f'Bearer {user.salla_access_token}'}
        response = requests.get(Config.SALLA_ORDERS_API, headers=headers)
        response.raise_for_status()
        
        # جلب الطلبات المسلمة من قبل الموظفين في نفس المتجر
        delivered_orders = OrderDelivery.query.join(Employee).filter(
            Employee.store_id == employee.store_id
        ).all()
        delivered_order_ids = {d.order_id for d in delivered_orders}
        
        # جلب جميع الإسنادات للمتجر
        assignments = OrderAssignment.query.join(Employee).filter(
            Employee.store_id == employee.store_id
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
def scan_barcode():
    if 'user_id' not in session:
        flash('غير مصرح لك', 'error')
        return redirect(url_for('user_auth.login'))
    
    employee = Employee.query.get(session['user_id'])
    if not employee or employee.role != 'delivery':
        flash('غير مصرح لك', 'error')
        return redirect(url_for('dashboard.index'))
    
    if request.method == 'POST':
        order_id = request.form.get('order_id')
        # التحقق من أن الطلب موجود في سلة
        user = User.query.filter_by(store_id=employee.store_id).first()
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
def deliver_order(order_id):
    if 'user_id' not in session:
        flash('غير مصرح لك', 'error')
        return redirect(url_for('user_auth.login'))
    
    employee = Employee.query.get(session['user_id'])
    if not employee or employee.role != 'delivery':
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
    user = User.query.filter_by(store_id=employee.store_id).first()
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
def assign_order(order_id):
    if 'user_id' not in session:
        flash('غير مصرح لك', 'error')
        return redirect(url_for('user_auth.login'))
    
    employee = Employee.query.get(session['user_id'])
    if not employee or employee.role != 'delivery_manager':
        flash('غير مصرح لك', 'error')
        return redirect(url_for('dashboard.index'))
    
    # جلب الموظفين (مندوبي التوصيل) في نفس المتجر
    delivery_employees = Employee.query.filter_by(
        store_id=employee.store_id, 
        role='delivery'
    ).all()
    
    if request.method == 'POST':
        selected_employee_id = request.form.get('employee_id')
        
        # تحقق من وجود إسناد سابق
        existing_assignment = OrderAssignment.query.filter_by(order_id=order_id).first()
        if existing_assignment:
            existing_assignment.employee_id = selected_employee_id
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