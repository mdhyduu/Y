from flask import (jsonify, request, redirect, url_for, flash, render_template, 
                   make_response, current_app)
from . import orders_bp
from app.models import (db, OrderStatusNote, EmployeeCustomStatus, OrderEmployeeStatus, 
                       CustomNoteStatus, OrderProductStatus, OrderAssignment, OrderStatus,  SallaStatusChange)
from app.utils import get_user_from_cookies
from app.config import Config
import requests
from datetime import datetime
from app.models import Employee
import logging

# إعداد المسجل للإنتاج
logger = logging.getLogger('salla_app')


@orders_bp.route('/<int:order_id>/update_status', methods=['POST'])
def update_order_status(order_id):
    """تحديث حالة الطلب في سلة مع تسجيل من قام بالتغيير"""
    user, employee = get_user_from_cookies()
    
    if not user:
        flash("الرجاء تسجيل الدخول أولاً", "error")
        return redirect(url_for('user_auth.login'))
    
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
            'Content-Type': 'application/json'
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

        # ✅ تسجيل بسيط لمن قام بالتغيير
        status_change = SallaStatusChange(
            order_id=str(order_id),
            status_slug=new_status,
            changed_by=user.email if request.cookies.get('is_admin') == 'true' else employee.email,
            user_type='admin' if request.cookies.get('is_admin') == 'true' else 'employee'
        )
        
        db.session.add(status_change)
        db.session.commit()

        flash("تم تحديث حالة الطلب بنجاح", "success")
        return redirect(url_for('orders.order_details', order_id=order_id))

    except requests.exceptions.HTTPError as http_err:
        if http_err.response.status_code == 401:
            flash("انتهت صلاحية الجلسة، الرجاء إعادة الربط مع سلة", "error")
            return redirect(url_for('auth.link_store'))
        
        flash("حدث خطأ أثناء تحديث الحالة", "error")
        return redirect(url_for('orders.order_details', order_id=order_id))
    except Exception as e:
        db.session.rollback()
        flash("حدث خطأ غير متوقع", "error")
        return redirect(url_for('orders.order_details', order_id=order_id))


@orders_bp.route('/<int:order_id>/add_status_note', methods=['POST'])
def add_status_note(order_id):
    user, employee = get_user_from_cookies()
    
    if not user:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول أولاً'}), 401
        else:
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
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'غير مصرح لك بهذا الإجراء'}), 403
        else:
            flash('غير مصرح لك هذا الإجراء', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))
    
    status_type = request.form.get('status_type')
    note = request.form.get('note', '')
    
    if not status_type:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'يجب اختيار حالة'}), 400
        else:
            flash("يجب اختيار حالة", "error")
            return redirect(url_for('orders.order_details', order_id=order_id))
    
    try:
        custom_status_id = None
        status_flag = None
        
        if status_type.startswith('custom_'):
            custom_status_id = status_type.split('_')[1]
            status_flag = "custom"
        else:
            status_flag = status_type
        
        has_conflict, conflict_message = check_status_conflict(
            order_id, status_flag, custom_status_id
        )
        
        if has_conflict:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'error': conflict_message}), 400
            else:
                flash(conflict_message, "error")
                return redirect(url_for('orders.order_details', order_id=order_id))
        
        # ✅ إزالة جميع الحالات التلقائية الأخرى قبل إضافة الحالة الجديدة
        if status_flag != "custom":
            # حذف جميع الحالات التلقائية الأخرى لهذا الطلب
            OrderStatusNote.query.filter_by(
                order_id=str(order_id)
            ).filter(
                OrderStatusNote.status_flag != None,
                OrderStatusNote.status_flag != status_flag
            ).delete(synchronize_session=False)
        else:
            # حذف جميع الحالات المخصصة الأخرى لهذا الطلب
            OrderStatusNote.query.filter_by(
                order_id=str(order_id),
                status_flag="custom"
            ).filter(
                OrderStatusNote.custom_status_id != custom_status_id
            ).delete(synchronize_session=False)
        
        # ✅ تحديث أو إنشاء جديد
        existing_note = OrderStatusNote.query.filter_by(
            order_id=str(order_id),
            status_flag=status_flag,
            custom_status_id=custom_status_id
        ).first()
    
        if existing_note:
            existing_note.note = note
            existing_note.updated_at = datetime.utcnow()
            db.session.commit()
            message = "تم تحديث الملاحظة بنجاح"
        else:
            new_note = OrderStatusNote(
                order_id=str(order_id),
                status_flag=status_flag,
                custom_status_id=custom_status_id,
                note=note
            )
            if request.cookies.get('is_admin') == 'true':
                new_note.admin_id = request.cookies.get('user_id')
            else:
                new_note.employee_id = employee.id
            
            db.session.add(new_note)
            db.session.commit()
            message = "تم حفظ الملاحظة بنجاح"
        
        # إذا كان الطلب AJAX، نرجع JSON
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # تحضير بيانات الحالة الجديدة
            new_status_data = {}
            if custom_status_id:
                custom_status = CustomNoteStatus.query.get(custom_status_id)
                if custom_status:
                    new_status_data = {
                        'name': custom_status.name,
                        'color': custom_status.color,
                        'note': note,
                        'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M')
                    }
            else:
                # للحالات التلقائية
                status_name = ''
                status_color = ''
                if status_flag == 'late':
                    status_name = 'متأخر'
                    status_color = '#ffc107'
                elif status_flag == 'missing':
                    status_name = 'واصل ناقص'
                    status_color = '#dc3545'
                elif status_flag == 'not_shipped':
                    status_name = 'لم يتم الشحن'
                    status_color = '#0dcaf0'
                elif status_flag == 'refunded':
                    status_name = 'مرتجع'
                    status_color = '#6c757d'
                
                new_status_data = {
                    'name': status_name,
                    'color': status_color,
                    'note': note,
                    'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M')
                }
            
            return jsonify({
                'success': True,
                'message': message,
                'new_status': new_status_data
            })
        else:
            flash(message, "success")
            return redirect(url_for('orders.order_details', order_id=order_id))
     
    except Exception as e:
        db.session.rollback()
        error_msg = "حدث خطأ أثناء حفظ الملاحظة"
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': error_msg}), 500
        else:
            flash(error_msg, "error")
            return redirect(url_for('orders.order_details', order_id=order_id))
            
@orders_bp.route('/<int:order_id>/add_employee_status', methods=['POST'])
def add_employee_status(order_id):
    user, employee = get_user_from_cookies()

    if not user:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول أولاً'}), 401
        else:
            flash('الرجاء تسجيل الدخول أولاً', 'error')
            response = make_response(redirect(url_for('user_auth.login')))
            response.set_cookie('user_id', '', expires=0)
            response.set_cookie('is_admin', '', expires=0)
            return response
    
    if request.cookies.get('is_admin') == 'true':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'هذه الخدمة للموظفين فقط'}), 403
        else:
            flash('هذه الخدمة للموظفين فقط', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))
    
    if not employee:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'غير مصرح لك بهذا الإجراء'}), 403
        else:
            flash('غير مصرح لك بهذا الإجراء', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))
    
    status_id = request.form.get('status_id')
    note = request.form.get('note', '')
    
    if not status_id:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'يجب اختيار حالة'}), 400
        else:
            flash('يجب اختيار حالة', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))
    
    custom_status = EmployeeCustomStatus.query.filter_by(
        id=status_id,
        employee_id=employee.id
    ).first()
    
    if not custom_status:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': 'الحالة المحددة غير صالحة'}), 400
        else:
            flash('الحالة المحددة غير صالحة', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))
    
    try:
        has_conflict, conflict_message = check_status_conflict(
            order_id, 'custom', status_id
        )
        
        if has_conflict:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'error': conflict_message}), 400
            else:
                flash(conflict_message, "error")
                return redirect(url_for('orders.order_details', order_id=order_id))
        
        # ✅ إزالة جميع الحالات المخصصة الأخرى قبل إضافة الحالة الجديدة
        OrderEmployeeStatus.query.filter_by(
            order_id=str(order_id)
        ).delete(synchronize_session=False)
        
        # ✅ إنشاء الحالة الجديدة
        new_status = OrderEmployeeStatus(
            order_id=str(order_id),
            status_id=status_id,
            note=note
        )
        db.session.add(new_status)
        db.session.commit()
        message = 'تم إضافة الحالة بنجاح'
        
        # إذا كان الطلب AJAX، نرجع JSON
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            # جلب بيانات الحالة الجديدة لإرجاعها
            new_status_data = {
                'name': custom_status.name,
                'color': custom_status.color,
                'note': note,
                'employee_email': employee.email,
                'created_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M')
            }
            return jsonify({
                'success': True,
                'message': message,
                'new_status': new_status_data
            })
        else:
            flash(message, 'success')
            return redirect(url_for('orders.order_details', order_id=order_id))
        
    except Exception as e:
        db.session.rollback()
        error_msg = 'حدث خطأ أثناء إضافة الحالة'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': False, 'error': error_msg}), 500
        else:
            flash(error_msg, 'error')
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
    if not request.cookies.get('is_admin') == 'true' and employee:
        ensure_default_statuses(employee.id)
        
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
            # ✅ إزالة جميع الحالات المخصصة الأخرى قبل إضافة الحالة الجديدة
            OrderEmployeeStatus.query.filter_by(
                order_id=str(order_id)
            ).delete(synchronize_session=False)
            
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
                'error': f'حدث خطأ أثناء تحديث الطلب {order_id}'
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
            'error': 'حدث خطأ أثناء حفظ التغييرات'
        }), 500
        
def get_done_status_id(employee_id):
    """جلب ID الخاص بحالة 'تم التنفيذ' مع كاش داخلي لزيادة السرعة"""
    if not hasattr(current_app, "done_status_cache"):
        current_app.done_status_cache = {}

    if employee_id in current_app.done_status_cache:
        return current_app.done_status_cache[employee_id]

    status = EmployeeCustomStatus.query.filter_by(
        name="تم التنفيذ",
        employee_id=employee_id
    ).first()

    if status:
        current_app.done_status_cache[employee_id] = status.id
        return status.id
    return None



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
        logger.error(f"Error canceling product status: {str(e)}")
        return jsonify({
            'success': False, 
            'error': 'خطأ في إلغاء حالة المنتج'
        }), 500


def check_status_conflict(order_id, new_status_type, custom_status_id=None):
    """
    التحقق من وجود تعارض بين الحالة الجديدة والحالات الحالية
    """
    try:
        # الحصول على الحالات الحالية للطلب
        current_employee_statuses = OrderEmployeeStatus.query.filter_by(
            order_id=str(order_id)
        ).all()
        
        current_status_notes = OrderStatusNote.query.filter_by(
            order_id=str(order_id)
        ).all()

        # الحالات المتعارضة (لا يمكن وجودها معاً)
        conflict_rules = {
            'تم التنفيذ': ['ملغى', 'مسترجعة'],
            'ملغى': ['تم التنفيذ', 'قيد التنفيذ', 'تم التوصيل'],
            'مسترجعة': ['تم التنفيذ', 'قيد التنفيذ', 'تم التوصيل']
        }

        # جمع جميع الحالات الحالية
        current_statuses = []
        for status in current_employee_statuses:
            custom_status = EmployeeCustomStatus.query.get(status.status_id)
            if custom_status:
                current_statuses.append(custom_status.name)
        
        for note in current_status_notes:
            if note.status_flag and note.status_flag not in current_statuses:
                current_statuses.append(note.status_flag)

        # الحصول على اسم الحالة الجديدة
        new_status_name = new_status_type
        if custom_status_id:
            custom_status = EmployeeCustomStatus.query.get(custom_status_id)
            if custom_status:
                new_status_name = custom_status.name

        # التحقق من التعارض
        if new_status_name in conflict_rules:
            for conflicting_status in conflict_rules[new_status_name]:
                if conflicting_status in current_statuses:
                    return True, f"لا يمكن إضافة حالة '{new_status_name}' مع وجود حالة '{conflicting_status}'"

        return False, ""
        
    except Exception as e:
        logger.error(f"Error in check_status_conflict: {str(e)}")
        return True, "حدث خطأ في التحقق من التعارض"

@orders_bp.route('/<order_id>/product/<product_id>/update_status', methods=['POST'])
def update_product_status(order_id, product_id):
    """تحديث حالة منتج معين داخل الطلب + تحديث حالة الطلب داخلياً إذا كل المنتجات تم تنفيذها"""
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

        # ✅ التحقق: إذا كل المنتجات "تم التنفيذ"
        not_done = OrderProductStatus.query.filter(
            OrderProductStatus.order_id == str(order_id),
            OrderProductStatus.status != "تم التنفيذ"
        ).first()

        if not not_done:
            try:
                # ✅ تحديث الحالة المخصصة في النظام الداخلي فقط
                done_status_id = None
                if employee:
                    done_status_id = get_done_status_id(employee.id)
                else:
                    # في حالة الأدمن -> أنشئ حالة "تم التنفيذ" إذا غير موجودة
                    from app.models import EmployeeCustomStatus
                    admin_status = EmployeeCustomStatus.query.filter_by(
                        name="تم التنفيذ",
                        employee_id=user.id
                    ).first()
                    if not admin_status:
                        admin_status = EmployeeCustomStatus(
                            name="تم التنفيذ",
                            color="#28a745",
                            employee_id=user.id
                        )
                        db.session.add(admin_status)
                        db.session.commit()
                    done_status_id = admin_status.id

                if done_status_id:
                    existing_status = OrderEmployeeStatus.query.filter_by(
                        order_id=str(order_id),
                        status_id=done_status_id
                    ).first()
                    if not existing_status:
                        order_status = OrderEmployeeStatus(
                            order_id=str(order_id),
                            status_id=done_status_id,
                            note="تم تحويل الطلب تلقائياً بعد تنفيذ جميع المنتجات"
                        )
                        db.session.add(order_status)
                    else:
                        existing_status.note = "تم تحديث تلقائياً بعد تنفيذ جميع المنتجات"

                    db.session.commit()
                    logger.info(f"✅ تم تحديث الحالة المخصصة داخلياً للطلب {order_id}")

            except Exception as e:
                db.session.rollback()
                logger.error(f"⚠️ Error auto-updating internal order status: {str(e)}")

        # -------------------------------
        # استجابة للواجهة
        # -------------------------------
        return jsonify({
            'success': True, 
            'message': 'تم تحديث حالة المنتج بنجاح',
            'status': new_status,
            'updated_at': datetime.utcnow().strftime('%Y-%m-%d %H:%M'),
            'all_products_done': not not_done  # إرجاع إذا كان كل المنتجات منتهية
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"⚠️ Error updating product status: {str(e)}")
        return jsonify({
            'success': False, 
            'error': 'خطأ في تحديث حالة المنتج'
        }), 500
         
import concurrent.futures
import threading
from flask import current_app
from app import create_app  # تأكد من استيراد create_app


@orders_bp.route('/bulk_update_salla_status', methods=['POST'])
def bulk_update_salla_status():
    """تحديث حالة عدة طلبات في سلة دفعة واحدة مع تسجيل بسيط ومعالجة متوازية"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401
    
    if not user.salla_access_token:
        return jsonify({'success': False, 'error': 'يجب ربط متجرك مع سلة أولاً'}), 400
    
    data = request.get_json()
    order_ids = data.get('order_ids', [])
    status_slug = data.get('status_slug')
    
    if not order_ids or not status_slug:
        return jsonify({'success': False, 'error': 'بيانات ناقصة'}), 400
    
    # تحديد من قام بالتغيير
    changed_by = user.email if request.cookies.get('is_admin') == 'true' else employee.email
    user_type = 'admin' if request.cookies.get('is_admin') == 'true' else 'employee'
    
    updated_count = 0
    failed_orders = []
    lock = threading.Lock()
    
    def update_single_order(order_id):
        nonlocal updated_count
        try:
            headers = {
                'Authorization': f'Bearer {user.salla_access_token}',
                'Content-Type': 'application/json'
            }
            
            payload = {'slug': status_slug}
            
            response = requests.post(
                f"{Config.SALLA_ORDERS_API}/{order_id}/status",
                headers=headers,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            
            # ✅ تسجيل بسيط لكل طلب
            with lock:
                status_change = SallaStatusChange(
                    order_id=str(order_id),
                    status_slug=status_slug,
                    changed_by=changed_by,
                    user_type=user_type
                )
                db.session.add(status_change)
                updated_count += 1
            
        except Exception as e:
            with lock:
                failed_orders.append(f"الطلب {order_id}")
    
    # استخدام ThreadPoolExecutor للمعالجة المتوازية
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        executor.map(update_single_order, order_ids)
    
    # حفظ جميع التغييرات في قاعدة البيانات
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': 'فشل في حفظ سجل التغييرات'}), 500
    
    result = {
        'success': updated_count > 0,
        'message': f'تم تحديث {updated_count} طلب في سلة',
        'updated_count': updated_count
    }
    
    if failed_orders:
        result['failed_orders'] = failed_orders
    
    return jsonify(result)
def process_single_order(order_id, shared_data):
    """معالجة طلب واحد - دالة مساعدة للمعالجة المتوازية"""
    try:
        headers = {
            'Authorization': f'Bearer {shared_data["access_token"]}',
            'Content-Type': 'application/json'
        }
        
        payload = {
            'slug': shared_data['status_slug'],
            'note': shared_data['note']
        }
        
        api_url = f"https://api.salla.dev/admin/v2/orders/{order_id}/status"
        
        response = requests.post(
            api_url,
            headers=headers,
            json=payload,
            timeout=15
        )
        
        if response.status_code in [200, 201]:
            # ✅ تم التحديث في سلة بنجاح - بدون تخزين أي سجلات محلية
            current_app.logger.info(f"✅ تم تحديث حالة الطلب {order_id} في سلة بنجاح")
            return {'success': True}
        else:
            error_message = f"كود الخطأ: {response.status_code}"
            try:
                error_data = response.json()
                error_message = error_data.get('error', {}).get('message', error_message)
            except:
                error_message = response.text[:100] + "..." if len(response.text) > 100 else response.text
            
            return {'success': False, 'error': error_message}
            
    except requests.exceptions.Timeout:
        return {'success': False, 'error': 'انتهت مهلة الاتصال'}
    except requests.exceptions.RequestException as e:
        return {'success': False, 'error': f'فشل الاتصال - {str(e)}'}
    except Exception as e:
        return {'success': False, 'error': f'خطأ غير متوقع - {str(e)}'}