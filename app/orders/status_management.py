# orders/status_management.py
from flask import jsonify, request, redirect, url_for, flash, render_template
from . import orders_bp
from .models import db, OrderStatusNote, EmployeeCustomStatus, OrderEmployeeStatus, CustomNoteStatus, OrderProductStatus
from .utils import get_user_from_cookies
from .config import Config
import requests
from datetime import datetime






@orders_bp.route('/<order_id>/product/<product_id>/update_status', methods=['POST'])
def update_product_status(order_id, product_id):
    """Updates the status of a specific product within an order."""
    # ... (Copy the full update_product_status route function here) ...
    pass

@orders_bp.route('/<order_id>/product/<product_id>/cancel_status', methods=['POST'])
def cancel_product_status(order_id, product_id):
    """Cancels (deletes) the status of a specific product within an order."""
    # ... (Copy the full cancel_product_status route function here) ...
    pass


logger = logging.getLogger(__name__)

@orders_bp.route('/<int:order_id>/update_status', methods=['POST'])
def update_order_status(order_id):
    """تحديث حالة الطلب في سلة"""
    user, _ = get_user_from_cookies()
    
    if not user:
        flash("الرجاء تسجيل الدخول أولاً", "error")
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    if not user.salla_access_token:
        flash('يجب ربط متجرك مع سلة أولاً', 'error')
        return redirect(url_for('auth.link_store'))
    
    try:
        new_status = request.form.get('status_slug')
        note = request.form.get('note', '')

        if not new_status:
            flash("يجب اختيار حالة جديدة", "error")
            return redirect(url_for('orders.order_details', order_id=order_id))

        headers = {
            'Authorization': f'Bearer {user.salla_access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        payload = {
            'slug': new_status,
            'note': note
        }

        response = requests.post(
            f"{Config.SALLA_ORDERS_API}/{order_id}/status",
            headers=headers,
            json=payload,
            timeout=10
        )
        response.raise_for_status()

        flash("تم تحديث حالة الطلب بنجاح", "success")
        return redirect(url_for('orders.order_details', order_id=order_id))

    except requests.exceptions.HTTPError as http_err:
        if http_err.response.status_code == 401:
            flash("انتهت صلاحية الجلسة، الرجاء إعادة الربط مع سلة", "error")
            return redirect(url_for('auth.link_store'))
        
        error_data = http_err.response.json()
        error_message = error_data.get('error', {}).get('message', 'حدث خطأ أثناء تحديث الحالة')
        
        if http_err.response.status_code == 422:
            field_errors = error_data.get('error', {}).get('fields', {})
            for field, errors in field_errors.items():
                for error in errors:
                    flash(f"{field}: {error}", "error")
        else:
            flash(f"خطأ: {error_message}", "error")
        return redirect(url_for('orders.order_details', order_id=order_id))
    except Exception as e:
        flash(f"حدث خطأ غير متوقع: {str(e)}", "error")
        return redirect(url_for('orders.order_details', order_id=order_id))

@orders_bp.route('/<int:order_id>/add_status_note', methods=['POST'])
def add_status_note(order_id):
    user, employee = get_user_from_cookies()
    
    if not user:
        flash("الرجاء تسجيل الدخول أولاً", "error")
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    # التحقق من الصلاحية: فقط المراجعون والمديرون
    is_reviewer = False
    if request.cookies.get('is_admin') == 'true':
        is_reviewer = True
    else:
        if employee and employee.role in ['reviewer', 'manager']:
            is_reviewer = True
    
    if not is_reviewer:
        flash('غير مصرح لك بهذا الإجراء', 'error')
        return redirect(url_for('orders.order_details', order_id=order_id))
    
    status_type = request.form.get('status_type')
    note = request.form.get('note', '')
    
    if not status_type:
        flash("يجب اختيار حالة", "error")
        return redirect(url_for('orders.order_details', order_id=order_id))
    
    try:
        # معالجة نوع الحالة
        custom_status_id = None
        status_flag = None
        
        if status_type.startswith('custom_'):
            # حالة مخصصة
            custom_status_id = status_type.split('_')[1]
            # للحالات المخصصة، نستخدم اسم الحالة كـ status_flag
            status_flag = "custom"
        else:
            # حالة تلقائية
            status_flag = status_type
        
        # إنشاء كائن الملاحظة الجديدة
        new_note = OrderStatusNote(
            order_id=str(order_id),
            status_flag=status_flag,
            custom_status_id=custom_status_id,
            note=note
        )
        
        # تحديد من أضاف الملاحظة (مدير أو موظف)
        if request.cookies.get('is_admin') == 'true':
            new_note.admin_id = request.cookies.get('user_id')
        else:
            new_note.employee_id = employee.id
        
        db.session.add(new_note)
        db.session.commit()
        flash("تم حفظ الملاحظة بنجاح", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"حدث خطأ: {str(e)}", "error")
        current_app.logger.error(f"Error adding status note: {str(e)}", exc_info=True)
    
    return redirect(url_for('orders.order_details', order_id=order_id))
    
@orders_bp.route('/employee_status', methods=['GET', 'POST'])
def manage_employee_status():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    # للموظفين العاديين: جلب بيانات الموظف
    if not request.cookies.get('is_admin') == 'true':
        if not employee:
            flash('غير مصرح لك بالوصول', 'error')
            response = make_response(redirect(url_for('user_auth.login')))
            response.set_cookie('user_id', '', expires=0)
            response.set_cookie('is_admin', '', expires=0)
            return response
    
    if request.method == 'POST':
        name = request.form.get('name')
        color = request.form.get('color', '#6c757d')
        
        if name:
            # للمديرين: استخدام user_id، للموظفين: استخدام employee.id
            employee_id = request.cookies.get('user_id') if request.cookies.get('is_admin') == 'true' else employee.id
            new_status = EmployeeCustomStatus(
                name=name,
                color=color,
                employee_id=employee_id
            )
            db.session.add(new_status)
            db.session.commit()
            flash('تمت إضافة الحالة بنجاح', 'success')
        return redirect(url_for('orders.manage_employee_status'))
    
    # جلب الحالات حسب نوع المستخدم
    if request.cookies.get('is_admin') == 'true':
        statuses = EmployeeCustomStatus.query.filter_by(employee_id=request.cookies.get('user_id')).all()
    else:
        statuses = employee.custom_statuses
    
    return render_template('manage_custom_status.html', statuses=statuses)
@orders_bp.route('/employee_status/<int:status_id>/delete', methods=['POST'])
def delete_employee_status(status_id):
    user, _ = get_user_from_cookies()
    
    if not user:
        flash('غير مصرح لك بالوصول', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    status = EmployeeCustomStatus.query.get(status_id)
    if status and status.employee_id == request.cookies.get('user_id'):
        db.session.delete(status)
        db.session.commit()
        flash('تم حذف الحالة بنجاح', 'success')
    return redirect(url_for('orders.manage_employee_status'))

@orders_bp.route('/<int:order_id>/add_employee_status', methods=['POST'])
def add_employee_status(order_id):
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    # التحقق من أن المستخدم موظف وليس مديراً
    if request.cookies.get('is_admin') == 'true':
        flash('هذه الخدمة للموظفين فقط', 'error')
        return redirect(url_for('orders.order_details', order_id=order_id))
    
    if not employee:
        flash('غير مصرح لك بهذا الإجراء', 'error')
        return redirect(url_for('orders.order_details', order_id=order_id))
    
    status_id = request.form.get('status_id')
    note = request.form.get('note', '')
    
    if not status_id:
        flash('يجب اختيار حالة', 'error')
        return redirect(url_for('orders.order_details', order_id=order_id))
    
    # التحقق أن الحالة تخص الموظف الحالي
    custom_status = EmployeeCustomStatus.query.filter_by(
        id=status_id,
        employee_id=employee.id
    ).first()
    
    if not custom_status:
        flash('الحالة المحددة غير صالحة', 'error')
        return redirect(url_for('orders.order_details', order_id=order_id))
    
    try:
        new_status = OrderEmployeeStatus(
            order_id=str(order_id),
            status_id=status_id,
            note=note
        )
        db.session.add(new_status)
        db.session.commit()
        flash('تم إضافة الحالة بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ: {str(e)}', 'error')
    
    return redirect(url_for('orders.order_details', order_id=order_id))
@orders_bp.route('/note_status/<int:status_id>/delete', methods=['POST'])
def delete_note_status(status_id):
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('غير مصرح لك بالوصول', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    status = CustomNoteStatus.query.get(status_id)
    if status and status.store_id == user.store_id:
        db.session.delete(status)
        db.session.commit()
        flash('تم حذف الحالة بنجاح', 'success')
    return redirect(url_for('orders.manage_note_status'))
    
@orders_bp.route('/manage_note_status', methods=['GET', 'POST'])
def manage_note_status():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    # التحقق من الصلاحية (مدير أو مراجع فقط)
    is_reviewer = False
    if request.cookies.get('is_admin') == 'true':
        is_reviewer = True
    else:
        if employee and employee.role in ['reviewer', 'manager']:
            is_reviewer = True
    
    if not is_reviewer:
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('orders.index'))
    
    store_id = user.store_id
    
    if request.method == 'POST':
        name = request.form.get('name')
        color = request.form.get('color', '#6c757d')
        
        if name:
            new_status = CustomNoteStatus(
                name=name,
                color=color,
                store_id=store_id
            )
            
            if request.cookies.get('is_admin') == 'true':
                new_status.created_by_admin = user.id
            else:
                new_status.created_by_employee = employee.id
                
            db.session.add(new_status)
            db.session.commit()
            flash('تمت إضافة الحالة بنجاح', 'success')
        return redirect(url_for('orders.manage_note_status'))
    
    # جلب الحالات الخاصة بالمتجر
    statuses = CustomNoteStatus.query.filter_by(store_id=store_id).all()
    
    return render_template('manage_note_status.html', statuses=statuses)
@orders_bp.route('/bulk_update_status', methods=['POST'])
def bulk_update_status():
    """تحديث حالة عدة طلبات دفعة واحدة"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401
    
    # التحقق من أن المستخدم موظف وليس مديراً
    if request.cookies.get('is_admin') == 'true':
        return jsonify({
            'success': False,
            'error': 'هذه الخدمة للموظفين فقط'
        }), 403
    
    if not employee:
        return jsonify({
            'success': False,
            'error': 'غير مصرح لك بهذا الإجراء'
        }), 403
    
    data = request.get_json()
    order_ids = data.get('order_ids', [])
    status_id = data.get('status_id')
    note = data.get('note', '')
    
    if not order_ids or not status_id:
        return jsonify({
            'success': False,
            'error': 'بيانات ناقصة'
        }), 400
    
    # التحقق أن الحالة تخص الموظف الحالي
    custom_status = EmployeeCustomStatus.query.filter_by(
        id=status_id,
        employee_id=employee.id
    ).first()
    
    if not custom_status:
        return jsonify({
            'success': False,
            'error': 'الحالة المحددة غير صالحة'
        }), 400
    
    # التحقق من أن الطلبات مسندة للموظف الحالي
    for order_id in order_ids:
        assignment = OrderAssignment.query.filter_by(
            order_id=str(order_id),
            employee_id=employee.id
        ).first()
        
        if not assignment:
            return jsonify({
                'success': False,
                'error': f'الطلب {order_id} غير مسند لك'
            }), 403
    
    # تحديث حالة كل طلب
    updated_count = 0
    for order_id in order_ids:
        try:
            new_status = OrderEmployeeStatus(
                order_id=str(order_id),
                status_id=status_id,
                note=note
            )
            db.session.add(new_status)
            updated_count += 1
        except Exception as e:
            db.session.rollback()
            return jsonify({
                'success': False,
                'error': f'حدث خطأ أثناء تحديث الطلب {order_id}: {str(e)}'
            }), 500
    
    try:
        db.session.commit()
        return jsonify({
            'success': True,
            'message': f'تم تحديث {updated_count} طلب بنجاح'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': f'حدث خطأ أثناء حفظ التغييرات: {str(e)}'
        }), 500
        
        
@orders_bp.route('/<order_id>/product/<product_id>/update_status', methods=['POST'])
def update_product_status(order_id, product_id):
    """تحديث حالة منتج معين داخل الطلب"""
    user, employee = get_user_from_cookies()
    if not user:
        return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401

    # استقبال البيانات كـ JSON
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': 'بيانات غير صالحة'}), 400
        
    new_status = data.get('status', 'تم التنفيذ')
    notes = data.get('notes', '')

    # التحقق من صحة product_id
    if not product_id or product_id == 'undefined':
        return jsonify({
            'success': False, 
            'error': 'معرف المنتج غير صالح'
        }), 400

    try:
        # البحث عن حالة المنتج الحالية أو إنشاء جديدة
        status_obj = OrderProductStatus.query.filter_by(
            order_id=str(order_id),
            product_id=str(product_id)
        ).first()

        if status_obj:
            status_obj.status = new_status
            status_obj.notes = notes
            status_obj.updated_at = datetime.utcnow()
            if employee:
                status_obj.employee_id = employee.id
        else:
            status_obj = OrderProductStatus(
                order_id=str(order_id),
                product_id=str(product_id),
                status=new_status,
                notes=notes,
                employee_id=employee.id if employee else None
            )
            db.session.add(status_obj)

        db.session.commit()
        
        # إرجاع بيانات محدثة للعرض
        return jsonify({
            'success': True, 
            'message': 'تم تحديث حالة المنتج بنجاح',
            'status': new_status,
            'updated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M')
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error updating product status: {str(e)}", exc_info=True)
        return jsonify({
            'success': False, 
            'error': f'خطأ في الخادم: {str(e)}'
        }), 500
@orders_bp.route('/<order_id>/product/<product_id>/cancel_status', methods=['POST'])
def cancel_product_status(order_id, product_id):
    """إلغاء حالة منتج معين داخل الطلب"""
    user, employee = get_user_from_cookies()
    if not user:
        return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401

    # التحقق من صحة product_id
    if not product_id or product_id == 'undefined':
        return jsonify({
            'success': False, 
            'error': 'معرف المنتج غير صالح'
        }), 400

    try:
        # البحث عن حالة المنتج الحالية وحذفها
        status_obj = OrderProductStatus.query.filter_by(
            order_id=str(order_id),
            product_id=str(product_id)
        ).first()

        if status_obj:
            db.session.delete(status_obj)
            db.session.commit()
            
            return jsonify({
                'success': True, 
                'message': 'تم إلغاء حالة المنتج بنجاح'
            })
        else:
            return jsonify({
                'success': False, 
                'error': 'لم يتم العثور على حالة المنتج'
            }), 404
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Error canceling product status: {str(e)}", exc_info=True)
        return jsonify({
            'success': False, 
            'error': f'خطأ في الخادم: {str(e)}'
        }), 500
        