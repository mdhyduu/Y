# orders/custom_orders.py
import os
from datetime import datetime
from flask import (render_template, request, flash, redirect, url_for, 
                   current_app, send_from_directory)
from werkzeug.utils import secure_filename
from . import orders_bp
from app.models import db, CustomOrder, Employee
from app.utils import get_user_from_cookies, allowed_file, get_next_order_number

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'static/uploads/custom_orders'

@orders_bp.route('/uploads/custom_orders/<filename>')
def serve_custom_order_image(filename):
    """خدمة صور الطلبات الخاصة"""
    upload_folder = os.path.join(current_app.root_path, 'static', 'uploads', 'custom_orders')
    return send_from_directory(upload_folder, filename)

@orders_bp.route('/custom/add', methods=['GET', 'POST'])
def add_custom_order():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    # التحقق من الصلاحيات (فقط المديرون والمراجعون يمكنهم إضافة طلبات خاصة)
    is_reviewer = False
    if request.cookies.get('is_admin') == 'true':
        is_reviewer = True
    else:
        if employee and employee.role in ['reviewer', 'manager']:
            is_reviewer = True
    
    if not is_reviewer:
        flash('غير مصرح لك بهذا الإجراء', 'error')
        return redirect(url_for('orders.index'))
    
    if request.method == 'POST':
        try:
            # معالجة البيانات المرسلة
            customer_name = request.form.get('customer_name')
            customer_phone = request.form.get('customer_phone')
            customer_address = request.form.get('customer_address')
            total_amount = request.form.get('total_amount', 0, type=float)
            notes = request.form.get('notes', '')
            
            # التحقق من الحقول المطلوبة
            if not customer_name or not total_amount:
                flash('اسم العميل والمبلغ الإجمالي حقلان مطلوبان', 'error')
                return render_template('add_custom_order.html')
            
            # معالجة تحميل الصورة
            image_file = request.files.get('order_image')
            image_filename = None
            
            if image_file and image_file.filename != '':
                if allowed_file(image_file.filename):
                    filename = secure_filename(image_file.filename)
                    # إنشاء اسم فريد للصورة
                    image_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
                    
                    # إنشاء المسار الكامل للملف
                    upload_folder = os.path.join(current_app.root_path, 'static', 'uploads', 'custom_orders')
                    os.makedirs(upload_folder, exist_ok=True)
                    image_path = os.path.join(upload_folder, image_filename)
                    
                    # حفظ الصورة
                    image_file.save(image_path)
                    
                    # التحقق من أن الصورة حفظت بنجاح
                    if not os.path.exists(image_path):
                        current_app.logger.error(f"فشل في حفظ الصورة: {image_path}")
                        flash('حدث خطأ أثناء حفظ الصورة', 'error')
                        return render_template('add_custom_order.html')
                else:
                    flash('صيغة الملف غير مدعومة', 'error')
                    return render_template('add_custom_order.html')
            
            # إنشاء رقم الطلب التلقائي
            order_number = get_next_order_number()
            
            # إنشاء الطلب الخاص
            custom_order = CustomOrder(
                order_number=order_number,
                customer_name=customer_name,
                customer_phone=customer_phone,
                customer_address=customer_address,
                total_amount=total_amount,
                order_image=image_filename,
                notes=notes,
                store_id=user.store_id,
                currency='SAR'  # إضافة عملة افتراضية
            )
            
            db.session.add(custom_order)
            db.session.commit()
            
            flash('تم إضافة الطلب الخاص بنجاح', 'success')
            return redirect(url_for('orders.custom_order_details', order_id=custom_order.id))
            
        except Exception as e:
            db.session.rollback()
            flash(f'حدث خطأ أثناء إضافة الطلب: {str(e)}', 'error')
            current_app.logger.error(f"Error adding custom order: {str(e)}", exc_info=True)
            return render_template('add_custom_order.html')
    
    return render_template('add_custom_order.html')
@orders_bp.route('/custom/<int:order_id>')
def custom_order_details(order_id):
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    # جلب بيانات الطلب الخاص مع العلاقات
    custom_order = CustomOrder.query.options(
        db.joinedload(CustomOrder.status),
        db.joinedload(CustomOrder.assignments).joinedload(OrderAssignment.employee),
        db.joinedload(CustomOrder.status_notes),
        db.joinedload(CustomOrder.employee_statuses)
    ).get_or_404(order_id)
    
    # التحقق من أن الطلب يخص المتجر الحالي
    if custom_order.store_id != user.store_id:
        flash('غير مصرح لك بالوصول إلى هذا الطلب', 'error')
        return redirect(url_for('orders.index'))
    
    # جلب البيانات الإضافية
    status_notes = OrderStatusNote.query.filter_by(custom_order_id=order_id).options(
        db.joinedload(OrderStatusNote.admin),
        db.joinedload(OrderStatusNote.employee),
        db.joinedload(OrderStatusNote.custom_status)
    ).order_by(OrderStatusNote.created_at.desc()).all()
    
    employee_statuses = db.session.query(
        OrderEmployeeStatus,
        EmployeeCustomStatus,
        Employee
    ).join(
        EmployeeCustomStatus,
        OrderEmployeeStatus.status_id == EmployeeCustomStatus.id
    ).join(
        Employee,
        EmployeeCustomStatus.employee_id == Employee.id
    ).filter(
        OrderEmployeeStatus.custom_order_id == order_id
    ).order_by(
        OrderEmployeeStatus.created_at.desc()
    ).all()
    
    # جلب الموظفين للإسناد (للمديرين والمراجعين فقط)
    employees = []
    is_reviewer = False
    if request.cookies.get('is_admin') == 'true':
        is_reviewer = True
        employees = Employee.query.filter_by(store_id=user.store_id, is_active=True).all()
    elif employee and employee.role in ['reviewer', 'manager']:
        is_reviewer = True
        employees = Employee.query.filter_by(store_id=user.store_id, is_active=True).all()
    
    return render_template('custom_order_details.html',
                         order=custom_order,
                         status_notes=status_notes,
                         employee_statuses=employee_statuses,
                         employees=employees,
                         is_reviewer=is_reviewer,
                         current_employee=employee)
