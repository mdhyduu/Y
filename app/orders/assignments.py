# orders/assignment.py
from flask import jsonify, request
from . import orders_bp
from .models import db, Employee, OrderAssignment, SallaOrder, CustomOrder
from .utils import get_user_from_cookies


logger = logging.getLogger(__name__)

@orders_bp.route('/assign', methods=['POST'])
def assign_orders():
    """إسناد طلبات (سلة + مخصصة) إلى موظف"""
    user, employee = get_user_from_cookies()
    
    if not user:
        response = jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'})
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response, 401
    
    # التحقق من الصلاحيات
    is_admin = request.cookies.get('is_admin') == 'true'
    employee_role = employee.role if employee else ''
    
    if not (is_admin or employee_role == 'reviewer'):
        return jsonify({
            'success': False,
            'error': 'غير مصرح لك بهذا الإجراء',
            'details': 'يجب أن تكون مديرًا أو مراجعًا'
        }), 403
    
    data = request.get_json()
    employee_id = data.get('employee_id')
    orders_data = data.get('orders', [])  # الشكل: [{"id": "12345", "type": "salla"}, {"id": 7, "type": "custom"}]
    current_user_id = request.cookies.get('user_id')
    
    if not employee_id or not orders_data:
        return jsonify({
            'success': False,
            'error': 'بيانات ناقصة (يجب تحديد موظف وطلبات)'
        }), 400
    
    try:
        # تحقق من وجود الموظف
        target_employee = Employee.query.get(employee_id)
        if not target_employee or target_employee.role != 'general':
            return jsonify({
                'success': False,
                'error': 'الموظف غير موجود أو ليس موظفًا عامًا'
            }), 404
        
        assigned_count = 0
        failed_assignments = []
        
        for order in orders_data:
            order_id = str(order.get('id'))
            order_type = order.get('type')
            
            if not order_id or not order_type:
                failed_assignments.append({'order_id': order_id, 'reason': 'بيانات ناقصة'})
                continue
            
            if order_type == 'salla':
                existing_order = SallaOrder.query.get(order_id)
                if not existing_order:
                    failed_assignments.append({'order_id': order_id, 'reason': 'طلب سلة غير موجود'})
                    continue
                
                # تحقق من التكرار
                existing_assignment = OrderAssignment.query.filter_by(
                    order_id=order_id,
                    employee_id=employee_id
                ).first()
                if existing_assignment:
                    failed_assignments.append({'order_id': order_id, 'reason': 'تم الإسناد مسبقًا'})
                    continue
                
                db.session.add(OrderAssignment(
                    order_id=order_id,
                    employee_id=employee_id,
                    assigned_by=current_user_id
                ))
                assigned_count += 1
            
            elif order_type == 'custom':
                existing_order = CustomOrder.query.get(int(order_id))
                if not existing_order:
                    failed_assignments.append({'order_id': order_id, 'reason': 'طلب مخصص غير موجود'})
                    continue
                
                # تحقق من التكرار
                existing_assignment = OrderAssignment.query.filter_by(
                    custom_order_id=order_id,
                    employee_id=employee_id
                ).first()
                if existing_assignment:
                    failed_assignments.append({'order_id': order_id, 'reason': 'تم الإسناد مسبقًا'})
                    continue
                
                db.session.add(OrderAssignment(
                    custom_order_id=order_id,
                    employee_id=employee_id,
                    assigned_by=current_user_id
                ))
                assigned_count += 1
        
        if assigned_count > 0:
            db.session.commit()
            return jsonify({
                'success': True,
                'message': f'تم إسناد {assigned_count} طلب(ات) بنجاح',
                'assigned_count': assigned_count,
                'failed_assignments': failed_assignments
            }), 200
        else:
            db.session.rollback()
            return jsonify({
                'success': False,
                'error': 'لم يتم إسناد أي طلب',
                'details': failed_assignments
            }), 400
    
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error assigning orders: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'حدث خطأ أثناء الإسناد: {str(e)}',
            'code': 'ASSIGNMENT_ERROR'
        }), 500