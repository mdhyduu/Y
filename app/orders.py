# orders.py
from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect() 
from flask import Blueprint, render_template, flash, redirect, url_for, request, send_from_directory, current_app, jsonify, make_response, send_file
import requests
from sqlalchemy import nullslast
from .models import (
    db, User, Employee, Department, EmployeePermission, 
    Product, OrderDelivery, SallaOrder, OrderAssignment,
    OrderStatusNote, EmployeeCustomStatus, OrderEmployeeStatus, CustomNoteStatus
)
from .config import Config
from .utils import process_order_data, format_date, generate_barcode, humanize_time
from .token_utils import exchange_code_for_token, get_store_info, set_token_cookies, refresh_salla_token
import os
from datetime import datetime, timedelta
import logging
import time
from math import ceil
import json
from weasyprint import HTML

import tempfile

# إعداد تسجيل الأخطاء
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

orders_bp = Blueprint('orders', __name__)

def get_user_from_cookies():
    """استخراج بيانات المستخدم من الكوكيز"""
    user_id = request.cookies.get('user_id')
    is_admin = request.cookies.get('is_admin') == 'true'
    
    if not user_id:
        return None, None
    
    if is_admin:
        user = User.query.get(user_id)
        return user, None
    else:
        employee = Employee.query.get(user_id)
        if employee:
            user = User.query.filter_by(store_id=employee.store_id).first()
            return user, employee
        return None, None

@orders_bp.route('/sync_orders', methods=['POST'])
def sync_orders():
    """مزامنة الطلبات من سلة إلى قاعدة البيانات المحلية وفق المواصفات الرسمية"""
    try:
        user, employee = get_user_from_cookies()
        
        # التحقق من صحة الكوكيز
        if not user:
            response = jsonify({
                'success': False, 
                'error': 'الرجاء تسجيل الدخول أولاً',
                'code': 'UNAUTHORIZED'
            })
            response.set_cookie('user_id', '', expires=0)
            response.set_cookie('is_admin', '', expires=0)
            return response, 401
        
        # الحصول على معرف المتجر وتوكن الوصول
        store_id = None
        access_token = None
        
        if request.cookies.get('is_admin') == 'true':
            store_id = user.store_id
            access_token = user.salla_access_token
        else:
            if not employee:
                return jsonify({
                    'success': False,
                    'error': 'الموظف غير موجود',
                    'code': 'EMPLOYEE_NOT_FOUND'
                }), 404
                
            store_id = employee.store_id
            access_token = user.salla_access_token
        
        # التحقق من وجود توكن الوصول
        if not access_token:
            return jsonify({
                'success': False,
                'error': 'يجب ربط المتجر مع سلة أولاً',
                'code': 'MISSING_ACCESS_TOKEN'
            }), 400
        
        # تحديد وقت آخر مزامنة (استخدم تاريخًا فقط كما في المواصفات)
        last_sync = getattr(user, 'last_sync', None)
        if not last_sync:
            # إذا لم تكن هناك مزامنة سابقة، جلب طلبات آخر 7 أيام
            from_date = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d')
        else:
            # استخدام تاريخ آخر مزامنة فقط (بدون وقت)
            from_date = last_sync.strftime('%Y-%m-%d')
        
        # جلب الطلبات من سلة وفق المواصفات الرسمية
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json'
        }
        
        current_app.logger.info(f"بدء مزامنة الطلبات للمتجر {store_id} منذ {from_date}")
        
        all_orders = []
        page = 1
        total_pages = 1
        token_refreshed = False
        
        while page <= total_pages:
            # استخدام المعلمات وفق مواصفات OpenAPI
            params = {
                'perPage': 100,  # لاحظ P الكبيرة كما في المواصفات
                'page': page,
                'from_date': from_date,  # التنسيق yyyy-mm-dd
                'sort_by': 'updated_at-desc'  # ترتيب حسب تاريخ التحديث
            }
            
            # إضافة معلمات تصفية إضافية إذا كانت متوفرة في الطلب
            request_data = request.get_json() or {}
            for param in ['status', 'payment_method', 'country', 'city', 'product', 'tags']:
                if param in request_data:
                    params[param] = request_data[param]
            
            response = requests.get(
                f"{Config.SALLA_API_BASE_URL}/orders",  # استخدام النقطة الأساسية
                headers=headers,
                params=params,
                timeout=30
            )
            
            # معالجة الأخطاء الخاصة بالتوكن
            if response.status_code == 401 and not token_refreshed:
                # محاولة تجديد التوكن مرة واحدة فقط
                new_token = refresh_salla_token(user)
                if new_token:
                    headers['Authorization'] = f'Bearer {new_token}'
                    access_token = new_token
                    token_refreshed = True
                    continue  # إعادة المحاولة بنفس الصفحة
                else:
                    return jsonify({
                        'success': False,
                        'error': "انتهت صلاحية الجلسة، الرجاء تسجيل الخروج وإعادة تسجيل الدخول",
                        'code': 'TOKEN_EXPIRED',
                        'action_required': True,
                        'redirect_url': url_for('user_auth.logout')
                    }), 401
            
            # التحقق من استجابة API
            if response.status_code != 200:
                error_msg = f"خطأ في استجابة سلة: {response.status_code} - {response.text}"
                current_app.logger.error(error_msg)
                return jsonify({
                    'success': False,
                    'error': "فشل في جلب البيانات من سلة",
                    'code': 'SALLA_API_ERROR',
                    'details': response.text[:200] if response.text else ''
                }), 500
            
            # معالجة الاستجابة وفق هيكل OpenAPI
            data = response.json()
            
            # التحقق من هيكل البيانات المتوقع
            if 'data' not in data or 'pagination' not in data:
                error_msg = "استجابة غير متوقعة من سلة: هيكل البيانات غير مطابق للمواصفات"
                current_app.logger.error(error_msg)
                return jsonify({
                    'success': False,
                    'error': error_msg,
                    'code': 'INVALID_RESPONSE_FORMAT'
                }), 500
            
            orders = data['data']
            all_orders.extend(orders)
            
            # تحديث معلومات الترقيم من الاستجابة
            pagination = data['pagination']
            total_pages = pagination.get('totalPages', 1)
            current_page = pagination.get('currentPage', page)
            
            current_app.logger.info(f"تم جلب {len(orders)} طلب من الصفحة {current_page}/{total_pages}")
            
            # الانتقال للصفحة التالية
            page += 1
            
            # إضافة تأخير بسيط لتجنب تجاوز معدل الطلبات
            time.sleep(0.2)
        
        current_app.logger.info(f"تم جلب {len(all_orders)} طلب إجمالاً للمزامنة")
        
        # معالجة الطلبات وتخزينها
        new_count = 0
        updated_count = 0
        skipped_count = 0
        
        for order in all_orders:
            try:
                order_id = str(order.get('id'))
                if not order_id:
                    skipped_count += 1
                    continue
                
                # البحث عن الطلب في قاعدة البيانات
                existing_order = SallaOrder.query.get(order_id)
                
                # تحويل تاريخ الإنشاء إذا كان موجوداً
                created_at = None
                date_info = order.get('date', {})
                if date_info and 'date' in date_info:
                    try:
                        # تحويل تنسيق التاريخ من "2022-06-16 14:48:20.000000"
                        date_str = date_info['date']
                        if '.' in date_str:
                            created_at = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S.%f')
                        else:
                            created_at = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                    except (ValueError, TypeError) as e:
                        current_app.logger.warning(f"خطأ في تحويل التاريخ للطلب {order_id}: {str(e)}")
                        created_at = datetime.utcnow()
                
                # معالجة حالة الطلب
                status_info = order.get('status', {})
                status_name = status_info.get('name', '')
                status_slug = status_info.get('slug', '')
                
                # معالجة المبلغ الإجمالي
                total_info = order.get('total', {})
                total_amount = float(total_info.get('amount', 0)) if total_info else 0
                currency = total_info.get('currency', 'SAR') if total_info else 'SAR'
                
                if existing_order:
                    # تحديث الطلب الموجود
                    existing_order.status = status_name
                    existing_order.status_slug = status_slug
                    existing_order.total_amount = total_amount
                    existing_order.currency = currency
                    existing_order.payment_method = order.get('payment_method', '')
                    existing_order.updated_at = datetime.utcnow()
                    updated_count += 1
                else:
                    # معالجة بيانات العميل
                    customer = order.get('customer', {})
                    customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
                    if not customer_name and 'customer' in order:
                        customer_name = order.get('customer', '')
                    
                    # إنشاء طلب جديد
                    new_order = SallaOrder(
                        id=order_id,
                        store_id=store_id,
                        customer_name=customer_name,
                        created_at=created_at or datetime.utcnow(),
                        total_amount=total_amount,
                        currency=currency,
                        payment_method=order.get('payment_method', ''),
                        status=status_name,
                        status_slug=status_slug,
                        raw_data=json.dumps(order, ensure_ascii=False)
                    )
                    db.session.add(new_order)
                    new_count += 1
                    
            except Exception as e:
                skipped_count += 1
                current_app.logger.error(f"خطأ في معالجة الطلب {order.get('id', 'unknown')}: {str(e)}")
        
        # تحديث وقت آخر مزامنة
        user.last_sync = datetime.utcnow()
        db.session.commit()
        
        current_app.logger.info(f"تمت المزامنة بنجاح: {new_count} جديد، {updated_count} محدث، {skipped_count} تم تخطيه")
        
        return jsonify({
            'success': True,
            'message': f'تمت المزامنة بنجاح: {new_count} طلب جديد، {updated_count} طلب محدث',
            'stats': {
                'new_orders': new_count,
                'updated_orders': updated_count,
                'skipped_orders': skipped_count,
                'total_processed': len(all_orders)
            }
        })
    
    except requests.exceptions.RequestException as e:
        error_msg = f"خطأ في الاتصال بسلة: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        return jsonify({
            'success': False,
            'error': error_msg,
            'code': 'NETWORK_ERROR'
        }), 500
        
    except Exception as e:
        error_msg = f"خطأ غير متوقع: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        return jsonify({
            'success': False,
            'error': error_msg,
            'code': 'INTERNAL_ERROR'
        }), 500

@orders_bp.route('/')
def index():
    """عرض قائمة الطلبات مع نظام الترحيل الكامل"""
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    # جلب معلمات الترحيل والتصفية
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    status_filter = request.args.get('status', '')
    employee_filter = request.args.get('employee', '')
    custom_status_filter = request.args.get('custom_status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    search_query = request.args.get('search', '')
    
    # التحقق من صحة معاملات الترحيل
    if page < 1: 
        page = 1
    if per_page not in [10, 20, 50, 100]: 
        per_page = 20
    
    # جلب بيانات المستخدم والمتجر
    is_general_employee = False
    is_reviewer = False
    
    if request.cookies.get('is_admin') == 'true':
        is_reviewer = True
        if not user.salla_access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            return redirect(url_for('auth.link_store'))
    else:
        if not employee:
            flash('غير مصرح لك بالوصول', 'error')
            response = make_response(redirect(url_for('user_auth.login')))
            response.set_cookie('user_id', '', expires=0)
            response.set_cookie('is_admin', '', expires=0)
            return response
        
        if not user.salla_access_token:
            flash('المتجر غير مرتبط بسلة', 'error')
            return redirect(url_for('user_auth.logout'))
        
        is_general_employee = employee.role == 'general'
        is_reviewer = employee.role in ['reviewer', 'manager']
    
    try:
        # جلب الطلبات من قاعدة البيانات المحلية
        query = SallaOrder.query.filter_by(store_id=user.store_id)
        
        # تطبيق الفلاتر المشتركة
        if status_filter:
            query = query.filter_by(status_slug=status_filter)
        
        if search_query:
            query = query.filter(
                SallaOrder.customer_name.ilike(f'%{search_query}%') | 
                SallaOrder.id.ilike(f'%{search_query}%')
            )
        
        # فلترة حسب التاريخ
        if date_from:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                query = query.filter(SallaOrder.created_at >= date_from_obj)
            except ValueError:
                pass
        
        if date_to:
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
                query = query.filter(SallaOrder.created_at <= date_to_obj)
            except ValueError:
                pass
        
        # فلترة خاصة بالمديرين والمراجعين
        if is_reviewer:
            if employee_filter:
                # جلب الطلبات المسندة لموظف معين
                assigned_order_ids = [a.order_id for a in 
                                    OrderAssignment.query.filter_by(employee_id=employee_filter).all()]
                query = query.filter(SallaOrder.id.in_(assigned_order_ids))
            
            if custom_status_filter:
                # جلب الطلبات بحالة مخصصة معينة
                status_order_ids = [s.order_id for s in 
                                  OrderEmployeeStatus.query.filter_by(status_id=custom_status_filter).all()]
                query = query.filter(SallaOrder.id.in_(status_order_ids))
        
        # فلترة خاصة بالموظفين العاديين
        elif is_general_employee:
            # جلب الطلبات المسندة لهذا الموظف فقط
            assigned_order_ids = [a.order_id for a in 
                                OrderAssignment.query.filter_by(employee_id=employee.id).all()]
            query = query.filter(SallaOrder.id.in_(assigned_order_ids))
            
            # فلترة حسب الحالات المخصصة التي أضافها الموظف
            if custom_status_filter:
                status_order_ids = [s.order_id for s in 
                                  OrderEmployeeStatus.query.filter_by(
                                      status_id=custom_status_filter,
                                      order_id=SallaOrder.id
                                  ).all()]
                query = query.filter(SallaOrder.id.in_(status_order_ids))
            
            # فلترة حسب ملاحظات الحالة (متأخر، واصل ناقص، إلخ)
            if status_filter in ['late', 'missing', 'refunded', 'not_shipped']:
                query = query.join(OrderStatusNote).filter(
                    OrderStatusNote.status_flag == status_filter,
                    OrderStatusNote.order_id == SallaOrder.id
                )
        
        # الترحيل
        pagination_obj = query.order_by(
            nullslast(SallaOrder.created_at.desc())
        ).paginate(page=page, per_page=per_page)
        
        # جلب البيانات الإضافية
        assigned_order_ids = [order.id for order in pagination_obj.items]
        
        # جلب جميع الإسنادات دفعة واحدة لتحسين الأداء
        assignments = OrderAssignment.query.filter(
            OrderAssignment.order_id.in_(assigned_order_ids)
        ).options(
            db.joinedload(OrderAssignment.employee)
        ).all()
        
        # تجميع الإسنادات حسب order_id
        assignments_dict = {}
        for assignment in assignments:
            if assignment.order_id not in assignments_dict:
                assignments_dict[assignment.order_id] = []
            assignments_dict[assignment.order_id].append(assignment)
        
        # جلب جميع الحالات المخصصة للطلبات
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
            OrderEmployeeStatus.order_id.in_(assigned_order_ids)
        ).all()
        
        # تجميع الحالات المخصصة حسب order_id
        statuses_dict = {}
        for status in employee_statuses:
            if status.OrderEmployeeStatus.order_id not in statuses_dict:
                statuses_dict[status.OrderEmployeeStatus.order_id] = []
            statuses_dict[status.OrderEmployeeStatus.order_id].append(status)
        
        # جلب جميع ملاحظات الحالة
        status_notes = OrderStatusNote.query.filter(
            OrderStatusNote.order_id.in_(assigned_order_ids)
        ).options(
            db.joinedload(OrderStatusNote.admin),
            db.joinedload(OrderStatusNote.employee)
        ).all()
        
        # تجميع ملاحظات الحالة حسب order_id
        notes_dict = {}
        for note in status_notes:
            if note.order_id not in notes_dict:
                notes_dict[note.order_id] = []
            notes_dict[note.order_id].append(note)
      
        
        # معالجة البيانات للعرض
        processed_orders = []
        for order in pagination_obj.items:
            raw_data = json.loads(order.raw_data) if order.raw_data else {}
            reference_id = raw_data.get('reference_id', order.id)  # استخدام id كاحتياطي
            
            processed_orders.append({
                'id': order.id,
                'reference_id': reference_id,  # إضافة reference_id 
                'customer_name': order.customer_name,
                'created_at': humanize_time(order.created_at) if order.created_at else '',
                'status': {
                    'slug': order.status_slug,
                    'name': order.status
                },
                'status_notes': notes_dict.get(order.id, []),
                'employee_statuses': statuses_dict.get(order.id, []),
                'assignments': assignments_dict.get(order.id, []),
                'raw_created_at': order.created_at
            })
        
        # جلب الموظفين للإسناد (للمديرين والمراجعين فقط)
        employees = []
        if is_reviewer:
            employees = Employee.query.filter_by(store_id=user.store_id, is_active=True).all()
        
        # جلب الحالات المخصصة (للعرض في الفلاتر)
        custom_statuses = []
        if is_reviewer:
            # للمديرين/المراجعين: جميع الحالات في المتجر
            custom_statuses = EmployeeCustomStatus.query.join(Employee).filter(
                Employee.store_id == user.store_id
            ).all()
        elif employee:
            # للموظفين العاديين: حالاتهم الخاصة فقط
            custom_statuses = employee.custom_statuses
        
        # إعداد بيانات الترحيل للقالب
        pagination = {
            'page': pagination_obj.page,
            'per_page': pagination_obj.per_page,
            'total_items': pagination_obj.total,
            'total_pages': pagination_obj.pages,
            'has_prev': pagination_obj.has_prev,
            'has_next': pagination_obj.has_next,
            'prev_page': pagination_obj.prev_num,
            'next_page': pagination_obj.next_num,
            'start_item': (pagination_obj.page - 1) * pagination_obj.per_page + 1,
            'end_item': min(pagination_obj.page * pagination_obj.per_page, pagination_obj.total)
        }
        
        # إعداد بيانات الفلاتر للقالب
        filters = {
            'status': status_filter,
            'employee': employee_filter,
            'custom_status': custom_status_filter,
            'date_from': date_from,
            'date_to': date_to,
            'search': search_query
        }
        
        # إذا كان الطلب AJAX، نرجع القالب الجزئي فقط
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('orders_partial.html', 
                                orders=processed_orders, 
                                employees=employees,
                                custom_statuses=custom_statuses,
                                pagination=pagination,
                                filters=filters,
                                is_reviewer=is_reviewer,
                                current_employee=employee)
        
        return render_template('orders.html', 
                            orders=processed_orders, 
                            employees=employees,
                            custom_statuses=custom_statuses,
                            pagination=pagination,
                            filters=filters,
                            is_reviewer=is_reviewer,
                            current_employee=employee)
    
    except Exception as e:
        error_msg = f'حدث خطأ غير متوقع: {str(e)}'
        flash(error_msg, 'error')
        logger.exception(error_msg)
        return redirect(url_for('orders.index'))

@orders_bp.route('/assign', methods=['POST'])
def assign_orders():
    """إسناد طلبات إلى موظف مع تحسينات للتحقق"""
    user, employee = get_user_from_cookies()
    
    if not user:
        response = jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'})
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response, 401
    
    # التحقق من الصلاحيات بشكل صارم
    is_admin = request.cookies.get('is_admin') == 'true'
    employee_role = employee.role if employee else ''
    
    # السماح للمديرين والمراجعين فقط
    if not (is_admin or employee_role == 'reviewer'):
        return jsonify({
            'success': False,
            'error': 'غير مصرح لك بهذا الإجراء',
            'details': 'يجب أن تكون مديرًا أو مراجعًا'
        }), 403
    
    data = request.get_json()
    employee_id = data.get('employee_id')
    order_ids = data.get('order_ids', [])
    current_user_id = request.cookies.get('user_id')
    
    if not employee_id or not order_ids:
        return jsonify({
            'success': False,
            'error': 'بيانات ناقصة (يجب تحديد موظف وطلبات)'
        }), 400
    
    try:
        # التحقق من وجود الموظف
        employee = Employee.query.get(employee_id)
        if not employee or employee.role != 'general':
            return jsonify({
                'success': False,
                'error': 'الموظف غير موجود أو ليس موظفًا عامًا'
            }), 404
        
        # إسناد كل طلب مع التحقق من وجوده
        assigned_count = 0
        failed_assignments = []
        
        for order_id in order_ids:
            order_id_str = str(order_id)
            
            # التحقق من وجود الطلب
            order = SallaOrder.query.get(order_id_str)
            if not order:
                failed_assignments.append({'order_id': order_id, 'reason': 'الطلب غير موجود'})
                continue
            
            # التحقق من عدم تكرار الإسناد
            existing_assignment = OrderAssignment.query.filter_by(
                order_id=order_id_str,
                employee_id=employee_id
            ).first()
            
            if existing_assignment:
                failed_assignments.append({'order_id': order_id, 'reason': 'تم الإسناد مسبقًا'})
                continue
            
            # إنشاء إسناد جديد
            new_assignment = OrderAssignment(
                order_id=order_id_str,
                employee_id=employee_id,
                assigned_by=current_user_id
            )
            db.session.add(new_assignment)
            assigned_count += 1
        
        # إضافة سجل للإسناد
        if assigned_count > 0:
            db.session.commit()
            
            # إرسال إشعارات أو تحديثات حسب الحاجة
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
@orders_bp.route('/<int:order_id>')
def order_details(order_id):
    """عرض تفاصيل طلب معين مع المنتجات مباشرة من سلة"""
    user, current_employee = get_user_from_cookies()
    
    if not user:
        flash("الرجاء تسجيل الدخول أولاً", "error")
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response

    try:
        # ========== [1] التحقق من صلاحية المستخدم ==========
        is_reviewer = False
        
        if request.cookies.get('is_admin') == 'true':
            is_reviewer = True
        elif current_employee:
            if current_employee.role in ['reviewer', 'manager']:
                is_reviewer = True

        # ========== [2] التحقق من صلاحية التوكن ==========
        def refresh_and_get_token():
            """دالة مساعدة لتجديد التوكن"""
            new_token = refresh_salla_token(user)
            if not new_token:
                flash("انتهت صلاحية الجلسة، الرجاء إعادة الربط مع سلة", "error")
                response = make_response(redirect(url_for('auth.link_store' if request.cookies.get('is_admin') == 'true' else 'user_auth.logout')))
                response.set_cookie('user_id', '', expires=0)
                response.set_cookie('is_admin', '', expires=0)
                return response
            return new_token

        access_token = user.salla_access_token
        if not access_token:
            flash('يجب ربط متجرك مع سلة أولاً', 'error')
            response = make_response(redirect(url_for('auth.link_store' if request.cookies.get('is_admin') == 'true' else 'user_auth.logout')))
            response.set_cookie('user_id', '', expires=0)
            response.set_cookie('is_admin', '', expires=0)
            return response

        # ========== [3] جلب بيانات الطلب من Salla API ==========
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        # دالة مساعدة للتعامل مع طلبات API
        def make_salla_api_request(url, params=None):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=15)
                
                # إذا كان التوكن منتهي الصلاحية، حاول تجديده
                if response.status_code == 401:
                    new_token = refresh_and_get_token()
                    if isinstance(new_token, str):  # إذا كان التوكن الجديد نصًا (تم تجديده بنجاح)
                        headers['Authorization'] = f'Bearer {new_token}'
                        response = requests.get(url, headers=headers, params=params, timeout=15)
                    else:  # إذا كان redirect (فشل التجديد)
                        return new_token  # هذا سيكون redirect response
                
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                raise e

        # جلب بيانات الطلب الأساسية - بدون format=light للحصول على العنوان
        order_response = make_salla_api_request(f"{Config.SALLA_ORDERS_API}/{order_id}")
        if not isinstance(order_response, requests.Response):  # إذا كان redirect
            return order_response
        order_data = order_response.json().get('data', {})

        # جلب عناصر الطلب
        items_response = make_salla_api_request(
            f"{Config.SALLA_BASE_URL}/orders/items",
            params={'order_id': order_id, 'include': 'images'}
        )
        if not isinstance(items_response, requests.Response):
            return items_response
        items_data = items_response.json().get('data', [])

        # جلب بيانات الشحنات الخاصة بالطلب
        shipments_response = make_salla_api_request(
            f"{Config.SALLA_API_BASE_URL}/shipments",
            params={'order_id': order_id}
        )
        if not isinstance(shipments_response, requests.Response):
            return shipments_response
        shipments_data = shipments_response.json().get('data', [])

        # ========== [4] معالجة بيانات الطلب ==========
        processed_order = process_order_data(order_id, items_data)

        # ========== [5] استخراج بيانات العنوان بشكل صحيح ==========
        # تهيئة بيانات المستلم مسبقًا لتجنب الخطأ
        receiver_info = {}
        address_data = {}
        full_address = 'لم يتم تحديد العنوان'
        
        # المحاولة 1: من بيانات الشحنات (shipments)
        if shipments_data and len(shipments_data) > 0:
            # نأخذ أول شحنة (يمكن تعديل هذا إذا كان هناك multiple shipments)
            first_shipment = shipments_data[0]
            address_data = first_shipment.get('ship_to', {})
            current_app.logger.info(f"تم العثور على عنوان من shipments: {address_data}")
        
        # المحاولة 2: من shipping.address في بيانات الطلب
        if not address_data:
            shipping_data = order_data.get('shipping', {})
            if shipping_data and 'address' in shipping_data:
                address_data = shipping_data.get('address', {})
                current_app.logger.info(f"تم العثور على عنوان من shipping.address: {address_data}")
        
        # المحاولة 3: من ship_to مباشرة في بيانات الطلب
        if not address_data and 'ship_to' in order_data:
            address_data = order_data.get('ship_to', {})
            current_app.logger.info(f"تم العثور على عنوان من order_data.ship_to: {address_data}")
        
        # المحاولة 4: من customer (fallback أخير)
        if not address_data and 'customer' in order_data:
            customer = order_data.get('customer', {})
            address_data = {
                'country': customer.get('country', ''),
                'city': customer.get('city', ''),
                'description': customer.get('location', '')
            }
            current_app.logger.info(f"تم استخدام بيانات العميل كعنوان: {address_data}")

        # بناء العنوان الكامل للمستلم
        if address_data:
            parts = []
            if address_data.get('name'):
                parts.append(f"الاسم: {address_data['name']}")
            if address_data.get('country'):
                parts.append(f"الدولة: {address_data['country']}")
            if address_data.get('city'):
                parts.append(f"المدينة: {address_data['city']}")
            if address_data.get('district'):
                parts.append(f"الحي: {address_data['district']}")
            if address_data.get('street'):
                parts.append(f"الشارع: {address_data['street']}")
            if address_data.get('street_number'):
                parts.append(f"رقم الشارع: {address_data['street_number']}")
            if address_data.get('block'):
                parts.append(f"القطعة: {address_data['block']}")
            if address_data.get('description'):
                parts.append(f"وصف إضافي: {address_data['description']}")
            if address_data.get('postal_code'):
                parts.append(f"الرمز البريدي: {address_data['postal_code']}")
            
            if parts:
                full_address = "، ".join(parts)

        # استخراج معلومات المستلم من بيانات العنوان
        receiver_info = {
            'name': address_data.get('name', ''),
            'phone': address_data.get('phone', ''),
            'email': address_data.get('email', '')
        }

        # إذا لم تكن هناك بيانات مستقلة للمستلم، نستخدم بيانات العميل
        if not receiver_info['name']:
            customer_info = order_data.get('customer', {})
            receiver_info = {
                'name': f"{customer_info.get('first_name', '')} {customer_info.get('last_name', '')}".strip(),
                'phone': f"{customer_info.get('mobile_code', '')}{customer_info.get('mobile', '')}",
                'email': customer_info.get('email', '')
            }

        # تحديث بيانات الطلب المعالجة
        processed_order.update({
            'id': order_id,
            'reference_id': order_data.get('reference_id') or 'غير متوفر',
            'customer': {
                'first_name': order_data.get('customer', {}).get('first_name', ''),
                'last_name': order_data.get('customer', {}).get('last_name', ''),
                'email': order_data.get('customer', {}).get('email', ''),
                'phone': f"{order_data.get('customer', {}).get('mobile_code', '')}{order_data.get('customer', {}).get('mobile', '')}"
            },
            'status': {
                'name': order_data.get('status', {}).get('name', 'غير معروف'),
                'slug': order_data.get('status', {}).get('slug', 'unknown')
            },
            'created_at': format_date(order_data.get('created_at', '')),
            'payment_method': order_data.get('payment_method', 'غير محدد'),
            'receiver': receiver_info,
            'shipping': {
                'customer_name': receiver_info.get('name', ''),
                'phone': receiver_info.get('phone', ''),
                'method': order_data.get('shipping', {}).get('courier_name', 'غير محدد'),
                'tracking_number': order_data.get('shipping', {}).get('tracking_number', ''),
                'tracking_link': order_data.get('shipping', {}).get('tracking_link', ''),
                
                # بيانات العنوان
                'address': full_address,
                'country': address_data.get('country', ''),
                'city': address_data.get('city', ''),
                'district': address_data.get('district', ''),
                'street': address_data.get('street', ''),
                'description': address_data.get('description', ''),
                'postal_code': address_data.get('postal_code', ''),

                # البيانات الأصلية كمرجع
                'raw_data': address_data
            },
            'payment': {
                'status': order_data.get('payment', {}).get('status', ''),
                'method': order_data.get('payment', {}).get('method', '')
            },
            'amount': {
                'sub_total': order_data.get('amounts', {}).get('sub_total', {'amount': 0, 'currency': 'SAR'}),
                'shipping_cost': order_data.get('amounts', {}).get('shipping_cost', {'amount': 0, 'currency': 'SAR'}),
                'discount': order_data.get('amounts', {}).get('discount', {'amount': 0, 'currency': 'SAR'}),
                'total': order_data.get('amounts', {}).get('total', {'amount': 0, 'currency': 'SAR'})
            }
        })

        # إنشاء الباركود إذا لم يكن موجوداً
        if not processed_order.get('barcode'):
            barcode_filename = generate_barcode(order_id)
            if barcode_filename:
                processed_order['barcode'] = barcode_filename

        # ========== [6] جلب البيانات الإضافية من قاعدة البيانات ==========
        # جلب الملاحظات الخاصة بالطلب (للمراجعين فقط)
        custom_note_statuses = CustomNoteStatus.query.filter_by(
            store_id=user.store_id
        ).all()
    
        # ========== [7] جلب ملاحظات الحالة مع العلاقات ==========
        status_notes = OrderStatusNote.query.filter_by(
            order_id=str(order_id)
        ).options(
            db.joinedload(OrderStatusNote.admin),
            db.joinedload(OrderStatusNote.employee),
            db.joinedload(OrderStatusNote.custom_status)  # إضافة تحميل الحالة المخصصة
        ).order_by(
            OrderStatusNote.created_at.desc()
        ).all()
        
        # ========== [8] جلب الحالات المخصصة للموظفين ==========
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
            OrderEmployeeStatus.order_id == str(order_id)
        ).order_by(
            OrderEmployeeStatus.created_at.desc()
        ).all()
        
        return render_template('order_details.html', 
            order=processed_order,
            status_notes=status_notes,
            employee_statuses=employee_statuses,
            custom_note_statuses=custom_note_statuses,
            current_employee=current_employee,
            is_reviewer=is_reviewer
        )

    except requests.exceptions.HTTPError as http_err:
        error_msg = f"خطأ في جلب تفاصيل الطلب: {http_err}"
        if http_err.response.status_code == 401:
            error_msg = "انتهت صلاحية الجلسة، الرجاء إعادة الربط مع سلة"
        flash(error_msg, "error")
        logger.error(f"HTTP Error: {http_err} - Status Code: {http_err.response.status_code}")
        return redirect(url_for('orders.index'))

    except requests.exceptions.RequestException as e:
        error_msg = f"حدث خطأ في الاتصال: {str(e)}"
        flash(error_msg, "error")
        logger.error(f"Request Exception: {str(e)}")
        return redirect(url_for('orders.index'))

    except Exception as e:
        error_msg = f"حدث خطأ غير متوقع: {str(e)}"
        flash(error_msg, "error")
        logger.exception(f"Unexpected error: {str(e)}")
        return redirect(url_for('orders.index'))
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
@orders_bp.route('/static/barcodes/<filename>')
def serve_barcode(filename):
    """تخدم ملفات الباركود"""
    barcode_folder = Config.BARCODE_FOLDER
    return send_from_directory(barcode_folder, filename)

@orders_bp.route('/scan')
def scan_barcode():
    """صفحة مسح الباركود"""
    user, _ = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    return render_template('scan_barcode.html')


 
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

@orders_bp.route('/print_orders')
def print_orders():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    order_ids = request.args.get('order_ids', '').split(',')
    if not order_ids or order_ids == ['']:
        flash('لم يتم تحديد أي طلبات للطباعة', 'error')
        return redirect(url_for('orders.index'))
    
    try:
        # جلب بيانات الطلبات المحددة
        orders = []
        access_token = user.salla_access_token
        
        if not access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            return redirect(url_for('auth.link_store'))
            
        headers = {'Authorization': f'Bearer {access_token}'}
        
        for order_id in order_ids:
            try:
                # جلب بيانات الطلب الأساسية
                order_response = requests.get(
                    f"{Config.SALLA_ORDERS_API}/{order_id}",
                    headers=headers,
                    timeout=10
                )
                
                if order_response.status_code != 200:
                    continue
                    
                order_data = order_response.json().get('data', {})
                
                # جلب عناصر الطلب
                items_response = requests.get(
                    f"{Config.SALLA_BASE_URL}/orders/items",
                    params={'order_id': order_id},
                    headers=headers,
                    timeout=10
                )
                
                items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
                
                # معالجة بيانات الطلب
                processed_order = process_order_data(order_id, items_data)
                
                # إضافة معلومات إضافية
                processed_order['reference_id'] = order_data.get('reference_id', order_id)
                processed_order['customer'] = order_data.get('customer', {})
                processed_order['created_at'] = format_date(order_data.get('created_at', ''))
                
                orders.append(processed_order)
                
            except Exception as e:
                current_app.logger.error(f"Error fetching order {order_id}: {str(e)}")
                continue
        
        if not orders:
            flash('لم يتم العثور على أي طلبات للطباعة', 'error')
            return redirect(url_for('orders.index'))
        
        # إضافة الوقت الحالي للقالب
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # إنشاء HTML من template مخصص للطباعة
        html_content = render_template('print_orders.html', 
                                     orders=orders, 
                                     current_time=current_time)
        
        # إنشاء PDF من HTML
        pdf = HTML(string=html_content, base_url=request.base_url).write_pdf()
        
        # إنشاء اسم ملف فريد
        filename = f"orders_{'_'.join(order_ids)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        # إعداد response مع PDF للتحميل
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        
        return response
        
    except Exception as e:
        current_app.logger.error(f"Error generating PDF: {str(e)}")
        flash(f'حدث خطأ أثناء إنشاء PDF: {str(e)}', 'error')
        return redirect(url_for('orders.index'))
@orders_bp.route('/quick_list') 
def quick_list():
    """صفحة القائمة السريعة لعرض الطلبات والمنتجات بشكل مصغر"""
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    # جلب معلمات التصفية والترحيل
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 10, type=int)
    status_filter = request.args.get('status', '')
    search_query = request.args.get('search', '')
    
    # التحقق من صحة معاملات الترحيل
    if page < 1: 
        page = 1
    if per_page not in [5, 10, 20, 50]: 
        per_page = 10
    
    try:
        # جلب الطلبات من قاعدة البيانات المحلية
        query = SallaOrder.query.filter_by(store_id=user.store_id)
        
        # تطبيق الفلاتر
        if status_filter:
            query = query.filter_by(status_slug=status_filter)
        
        if search_query:
            query = query.filter(
                SallaOrder.customer_name.ilike(f'%{search_query}%') | 
                SallaOrder.id.ilike(f'%{search_query}%')
            )
        
        # الترحيل والترتيب حسب أحدث الطلبات
        pagination_obj = query.order_by(
            nullslast(SallaOrder.created_at.desc())
        ).paginate(page=page, per_page=per_page)
        
        # معالجة بيانات كل طلب
        processed_orders = []
        for order in pagination_obj.items:
            # تحليل البيانات الخام للطلب
            raw_data = {}
            if order.raw_data:
                try:
                    raw_data = json.loads(order.raw_data)
                except json.JSONDecodeError:
                    raw_data = {}
                    current_app.logger.error(f"Failed to parse raw_data for order {order.id}")
            
            # استخراج معلومات المنتجات من البيانات الخام
            items = raw_data.get('items', [])
            processed_items = []
            
            for item in items:
                if not isinstance(item, dict):
                    continue  # تخطي العناصر غير الصالحة
                
                # استخراج صورة المنتج بشكل آمن
                image_url = ''
                
                # المحاولة 1: استخدام product_thumbnail
                product_thumbnail = item.get('product_thumbnail')
                if product_thumbnail and isinstance(product_thumbnail, str):
                    image_url = product_thumbnail
                
                # المحاولة 2: استخدام images إذا كانت موجودة
                if not image_url:
                    images = item.get('images')
                    if images and isinstance(images, list) and len(images) > 0:
                        first_image = images[0]
                        if isinstance(first_image, dict):
                            image_url = first_image.get('image', '')
                        elif isinstance(first_image, str):
                            image_url = first_image
                
                # المحاولة 3: استخدام image مباشرة
                if not image_url:
                    image_field = item.get('image')
                    if image_field and isinstance(image_field, str):
                        image_url = image_field
                
                # إذا كان الرابط لا يبدأ بـ http، نضيف النطاق الأساسي لسلة
                if image_url and not image_url.startswith(('http://', 'https://')):
                    image_url = f"https://cdn.salla.sa{image_url}"
                
                # استخراج الخيارات بشكل آمن
                options = []
                item_options = item.get('options', [])
                if isinstance(item_options, list):
                    for option in item_options:
                        if isinstance(option, dict):
                            options.append({
                                'name': option.get('name', ''),
                                'value': option.get('value', '')
                            })
                
                processed_items.append({
                    'name': item.get('name', ''),
                    'quantity': item.get('quantity', 1),
                    'image_url': image_url,
                    'options': options,
                    'sku': item.get('sku', '')
                })
            
            processed_orders.append({
                'id': order.id,
                'customer_name': order.customer_name,
                'created_at': humanize_time(order.created_at) if order.created_at else '',
                'status': {
                    'slug': order.status_slug,
                    'name': order.status
                },
                'items': processed_items,
                'total_amount': order.total_amount,
                'currency': order.currency
            })
        
        # إعداد بيانات الترحيل للقالب
        pagination = {
            'page': pagination_obj.page,
            'per_page': pagination_obj.per_page,
            'total_items': pagination_obj.total,
            'total_pages': pagination_obj.pages,
            'has_prev': pagination_obj.has_prev,
            'has_next': pagination_obj.has_next,
            'prev_page': pagination_obj.prev_num,
            'next_page': pagination_obj.next_num
        }
        
        return render_template('quick_list.html', 
                            orders=processed_orders,
                            pagination=pagination,
                            status_filter=status_filter,
                            search_query=search_query)
    
    except Exception as e:
        error_msg = f'حدث خطأ غير متوقع: {str(e)}'
        flash(error_msg, 'error')
        logger.exception(error_msg)
        return redirect(url_for('orders.index'))