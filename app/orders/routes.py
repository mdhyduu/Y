# orders/routes.py
# orders/routes.py
import json
import logging
from math import ceil
from datetime import datetime, timedelta
from flask import (render_template, request, flash, redirect, url_for, 
                   make_response, current_app)
import requests
from sqlalchemy import nullslast, or_, and_, func
from sqlalchemy.orm import selectinload
from . import orders_bp
from app.models import (db, SallaOrder, CustomOrder, OrderStatus, Employee, 
                     OrderAssignment, EmployeeCustomStatus, OrderStatusNote, 
                     OrderEmployeeStatus, OrderProductStatus, CustomNoteStatus)
from app.utils import get_user_from_cookies, process_order_data, format_date, generate_barcode, humanize_time
from app.token_utils import refresh_salla_token
from app.config import Config
from flask import send_file
import pandas as pd
from io import BytesIO
import openpyxl
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.styles import Alignment
from openpyxl.worksheet.datavalidation import DataValidation
# إعداد الـ logger
logger = logging.getLogger(__name__)

@orders_bp.route('/')
def index():
    """عرض قائمة الطلبات (سلة + مخصصة) مع نظام الترحيل الكامل
    - يحافظ على بنية البيانات الحالية للـ template.
    - يقلل التحميل عبر جلب آخر حالة مخصصة وآخر ملاحظة فقط لكل طلب.
    """
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    # جلب معلمات الترحيل والتصفية
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int) 
    status_filter = request.args.get('status', '')
    employee_filter = request.args.get('employee', '')
    order_statuses = OrderStatus.query.filter_by(store_id=user.store_id).order_by(OrderStatus.sort).all()
    custom_status_filter = request.args.get('custom_status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    search_query = request.args.get('search', '')
    order_type = request.args.get('order_type', 'all')
    
    # التحقق من صحة معاملات الترحيل
    if page < 1: 
        page = 1
    if per_page not in [10, 25, 50, 100]: 
        per_page = 25
    
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
        # Queries الأساسية مع selectinload كما كان
        salla_query = SallaOrder.query.filter_by(store_id=user.store_id).options(
            selectinload(SallaOrder.status),
            selectinload(SallaOrder.assignments).selectinload(OrderAssignment.employee)
        )
        
        custom_query = CustomOrder.query.filter_by(store_id=user.store_id).options(
            selectinload(CustomOrder.status),
            selectinload(CustomOrder.assignments).selectinload(OrderAssignment.employee)
        )
        
        # للموظفين العاديين: عرض فقط الطلبات المسندة لهم
        if not is_reviewer and employee:
            salla_query = salla_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee.id)
            custom_query = custom_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee.id)
        
        # تطبيق الفلاتر المشتركة (الحالات الخاصة والفلترة حسب slug)
        if status_filter in ['late', 'missing', 'not_shipped', 'refunded']:
            if order_type in ['all', 'salla']:
                salla_query = salla_query.join(
                    OrderStatusNote, 
                    OrderStatusNote.order_id == SallaOrder.id
                ).filter(
                    OrderStatusNote.status_flag == status_filter
                )
            if order_type in ['all', 'custom']:
                custom_query = custom_query.join(
                    OrderStatusNote, 
                    OrderStatusNote.custom_order_id == CustomOrder.id
                ).filter(
                    OrderStatusNote.status_flag == status_filter
                )
        elif status_filter:  # فلتر الحالة العادية
            if order_type in ['all', 'salla']:
                salla_query = salla_query.join(SallaOrder.status).filter(OrderStatus.slug == status_filter)
            if order_type in ['all', 'custom']:
                custom_query = custom_query.join(CustomOrder.status).filter(
                    OrderStatus.slug == status_filter
                )
        
        # تطبيق فلتر الموظف (إذا تم تمريره)
        if employee_filter:
            if order_type in ['all', 'salla']:
                salla_query = salla_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)
            if order_type in ['all', 'custom']:
                custom_query = custom_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)
        
        # فلترة الحالة المخصصة (EmployeeCustomStatus) — حافظنا على المنطق اللي عدلته سابقًا
        if custom_status_filter:
            custom_status_id = int(custom_status_filter)
            if order_type in ['all', 'salla']:
                salla_query = salla_query.join(SallaOrder.employee_statuses).filter(
                    OrderEmployeeStatus.status_id == custom_status_id
                )
            if order_type in ['all', 'custom']:
                custom_query = custom_query.join(CustomOrder.employee_statuses).filter(
                    OrderEmployeeStatus.status_id == custom_status_id
                )
        
        # فلتر البحث
        if search_query:
            search_filter = f'%{search_query}%'
            if order_type in ['all', 'salla']:
                salla_query = salla_query.filter(
                    or_(
                        SallaOrder.customer_name.ilike(search_filter),
                        SallaOrder.id.ilike(search_filter)
                    )
                )
            if order_type in ['all', 'custom']:
                custom_query = custom_query.filter(
                    or_(
                        CustomOrder.customer_name.ilike(search_filter),
                        CustomOrder.order_number.ilike(search_filter)
                    )
                )
        
        # فلترة حسب التاريخ
        if date_from and date_to:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
                
                if order_type in ['all', 'salla']:
                    salla_query = salla_query.filter(SallaOrder.created_at.between(date_from_obj, date_to_obj))
                if order_type in ['all', 'custom']:
                    custom_query = custom_query.filter(CustomOrder.created_at.between(date_from_obj, date_to_obj))
            except ValueError:
                pass
        elif date_from:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                if order_type in ['all', 'salla']:
                    salla_query = salla_query.filter(SallaOrder.created_at >= date_from_obj)
                if order_type in ['all', 'custom']:
                    custom_query = custom_query.filter(CustomOrder.created_at >= date_from_obj)
            except ValueError:
                pass
        elif date_to:
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
                if order_type in ['all', 'salla']:
                    salla_query = salla_query.filter(SallaOrder.created_at <= date_to_obj)
                if order_type in ['all', 'custom']:
                    custom_query = custom_query.filter(CustomOrder.created_at <= date_to_obj)
            except ValueError:
                pass
        
        # جلب الحالات المخصصة للقائمة
        custom_statuses = []
        if is_reviewer:
            custom_statuses = EmployeeCustomStatus.query.join(Employee).filter(
                Employee.store_id == user.store_id
            ).all()
        elif employee:
            custom_statuses = EmployeeCustomStatus.query.filter_by(employee_id=employee.id).all()
        
        # الترحيل وجلب الطلبات (كما كانت)
        if order_type == 'salla':
            orders_query = salla_query.order_by(nullslast(db.desc(SallaOrder.created_at)))
            pagination_obj = orders_query.paginate(page=page, per_page=per_page, error_out=False)
            orders = pagination_obj.items
        elif order_type == 'custom':
            orders_query = custom_query.order_by(nullslast(db.desc(CustomOrder.created_at)))
            pagination_obj = orders_query.paginate(page=page, per_page=per_page, error_out=False)
            orders = pagination_obj.items
        else:
            salla_pagination = salla_query.order_by(nullslast(db.desc(SallaOrder.created_at))).paginate(
                page=1, per_page=per_page * 2, error_out=False)
            custom_pagination = custom_query.order_by(nullslast(db.desc(CustomOrder.created_at))).paginate(
                page=1, per_page=per_page * 2, error_out=False)
            all_orders = salla_pagination.items + custom_pagination.items
            all_orders.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
            total_orders = salla_pagination.total + custom_pagination.total
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            start_idx = max(0, min(start_idx, total_orders))
            end_idx = max(0, min(end_idx, total_orders))
            paginated_orders = all_orders[start_idx:end_idx]
            pagination_obj = type('Obj', (object,), {
                'items': paginated_orders,
                'page': page,
                'per_page': per_page,
                'total': total_orders,
                'pages': ceil(total_orders / per_page),
                'has_prev': page > 1,
                'has_next': end_idx < total_orders,
                'prev_num': page - 1 if page > 1 else None,
                'next_num': page + 1 if end_idx < total_orders else None
            })()
            orders = pagination_obj.items
        
        # معالجة البيانات للعرض — مع الحفاظ على نفس المفاتيح للـ template
        processed_orders = []
        
        for order in orders:
            if isinstance(order, SallaOrder):
                raw_data = json.loads(order.raw_data) if order.raw_data else {}
                reference_id = raw_data.get('reference_id', order.id)
                status_name = order.status.name if order.status else 'غير محدد'
                status_slug = order.status.slug if order.status else 'unknown'
                
                # جلب آخر ملاحظة وآخر حالة مخصصة فقط (لتحسين الأداء)
                last_note = OrderStatusNote.query.filter_by(order_id=order.id).order_by(OrderStatusNote.created_at.desc()).first()
                last_emp_status = OrderEmployeeStatus.query.filter_by(order_id=order.id).order_by(OrderEmployeeStatus.created_at.desc()).first()
                
                processed_order = {
                    'id': order.id,
                    'reference_id': reference_id,
                    'customer_name': order.customer_name,
                    'created_at': humanize_time(order.created_at) if order.created_at else '',
                    'status': {
                        'slug': status_slug,
                        'name': status_name
                    },
                    'status_obj': order.status,
                    'raw_created_at': order.created_at,
                    'type': 'salla',
                    'assignments': order.assignments,
                    # للحفاظ على التوافق: نعيد نفس القوائم لكن تحتوي فقط على آخر عنصر (إن وجد)
                    'employee_statuses': [last_emp_status] if last_emp_status else [],
                    'status_notes': [last_note] if last_note else []
                } 
                
            else:  # CustomOrder
                last_note = OrderStatusNote.query.filter_by(custom_order_id=order.id).order_by(OrderStatusNote.created_at.desc()).first()
                last_emp_status = OrderEmployeeStatus.query.filter_by(custom_order_id=order.id).order_by(OrderEmployeeStatus.created_at.desc()).first()
                
                processed_order = {
                    'id': order.id,
                    'reference_id': order.order_number,
                    'customer_name': order.customer_name,
                    'created_at': humanize_time(order.created_at) if order.created_at else '',
                    'status': {
                        'slug': order.status_id or 'custom',
                        'name': order.status.name if order.status else 'مخصص'
                    },
                    'status_obj': order.status,
                    'raw_created_at': order.created_at,
                    'type': 'custom',
                    'total_amount': order.total_amount,
                    'currency': order.currency,
                    'assignments': order.assignments,
                    'employee_statuses': [last_emp_status] if last_emp_status else [],
                    'status_notes': [last_note] if last_note else []
                }
                    
            processed_orders.append(processed_order)
        
        employees = []
        if is_reviewer:
            employees = Employee.query.filter_by(store_id=user.store_id, is_active=True).all()
        
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
            'search': search_query,
            'order_type': order_type
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
                            order_statuses=order_statuses,  
                            is_reviewer=is_reviewer,
                            current_employee=employee,
                            order_type=order_type)
    
    except Exception as e:
        error_msg = f'حدث خطأ غير متوقع: {str(e)}'
        flash(error_msg, 'error')
        logger.exception(error_msg)
        return redirect(url_for('orders.index'))
@orders_bp.route('/<int:order_id>')
def order_details(order_id):
    """عرض تفاصيل طلب معين مع المنتجات مباشرة من سلة (بدون shipments)"""
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
        elif current_employee and current_employee.role in ['reviewer', 'manager']:
            is_reviewer = True

        # ========== [2] التحقق من صلاحية التوكن ==========
        def refresh_and_get_token():
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

        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }

        def make_salla_api_request(url, params=None):
            try:
                response = requests.get(url, headers=headers, params=params, timeout=15)
                if response.status_code == 401:
                    new_token = refresh_and_get_token()
                    if isinstance(new_token, str):
                        headers['Authorization'] = f'Bearer {new_token}'
                        response = requests.get(url, headers=headers, params=params, timeout=15)
                    else:
                        return new_token
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                raise e

        # ========== [3] جلب بيانات الطلب ==========
        order_response = make_salla_api_request(f"{Config.SALLA_ORDERS_API}/{order_id}")
        if not isinstance(order_response, requests.Response):
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

        # ========== [4] معالجة بيانات الطلب ==========
        processed_order = process_order_data(order_id, items_data)

        # ========== [5] استخراج بيانات العنوان ==========
        address_data = {}
        full_address = 'لم يتم تحديد العنوان'

        # المحاولة 1: من shipping.address
        shipping_data = order_data.get('shipping', {})
        if shipping_data and 'address' in shipping_data:
            address_data = shipping_data.get('address', {})

        # المحاولة 2: من ship_to
        if not address_data and 'ship_to' in order_data:
            address_data = order_data.get('ship_to', {})

        # المحاولة 3: fallback على customer
        if not address_data and 'customer' in order_data:
            customer = order_data.get('customer', {})
            address_data = {
                'country': customer.get('country', ''),
                'city': customer.get('city', ''),
                'description': customer.get('location', '')
            }

        # بناء العنوان الكامل
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

        # بيانات المستلم
        receiver_info = {
            'name': address_data.get('name', ''),
            'phone': address_data.get('phone', ''),
            'email': address_data.get('email', '')
        }
        if not receiver_info['name']:
            customer_info = order_data.get('customer', {})
            receiver_info = {
                'name': f"{customer_info.get('first_name', '')} {customer_info.get('last_name', '')}".strip(),
                'phone': f"{customer_info.get('mobile_code', '')}{customer_info.get('mobile', '')}",
                'email': customer_info.get('email', '')
            }

        # تحديث بيانات الطلب
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
                'address': full_address,
                'country': address_data.get('country', ''),
                'city': address_data.get('city', ''),
                'district': address_data.get('district', ''),
                'street': address_data.get('street', ''),
                'description': address_data.get('description', ''),
                'postal_code': address_data.get('postal_code', ''),
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

        if not processed_order.get('barcode'):
            barcode_filename = generate_barcode(order_id)
            if barcode_filename:
                processed_order['barcode'] = barcode_filename

        # ========== [6] جلب البيانات الإضافية ==========
        custom_note_statuses = CustomNoteStatus.query.filter_by(
            store_id=user.store_id
        ).all()

        status_notes = OrderStatusNote.query.filter_by(
            order_id=str(order_id)
        ).options(
            db.joinedload(OrderStatusNote.admin),
            db.joinedload(OrderStatusNote.employee),
            db.joinedload(OrderStatusNote.custom_status)
        ).order_by(
            OrderStatusNote.created_at.desc()
        ).all()

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

        product_statuses = {}
        status_records = OrderProductStatus.query.filter_by(order_id=str(order_id)).all()
        for status in status_records:
            product_statuses[status.product_id] = {
                'status': status.status,
                'notes': status.notes,
                'updated_at': status.updated_at
            }

        return render_template('order_details.html', 
            order=processed_order,
            status_notes=status_notes,
            employee_statuses=employee_statuses,
            custom_note_statuses=custom_note_statuses,
            current_employee=current_employee,
            is_reviewer=is_reviewer,
            product_statuses=product_statuses
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

@orders_bp.route('/upload_updated_excel', methods=['POST'])
def upload_updated_excel():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    if 'excel_file' not in request.files:
        flash('لم يتم اختيار ملف', 'error')
        return redirect(request.referrer or url_for('orders.index'))
    
    file = request.files['excel_file']
    if file.filename == '':
        flash('لم يتم اختيار ملف', 'error')
        return redirect(request.referrer or url_for('orders.index'))
    
    if not file.filename.endswith(('.xlsx', '.xls')):
        flash('يجب أن يكون الملف بصيغة Excel', 'error')
        return redirect(request.referrer or url_for('orders.index'))
    
    try:
        # قراءة ملف Excel
        df = pd.read_excel(file)
        
        # معالجة كل صف
        updated_count = 0
        for _, row in df.iterrows():
            order_id = str(row['order_id'])
            order_type = row['order_type']
            new_status = row['new_status']
            notes = row.get('notes', '')
            
            if not new_status or pd.isna(new_status):
                continue
            
            # البحث عن الطلب
            if order_type == 'salla':
                order = SallaOrder.query.filter_by(id=order_id, store_id=user.store_id).first()
            else:
                order = CustomOrder.query.filter_by(order_number=order_id, store_id=user.store_id).first()
            
            if not order:
                continue
            
            # تحديث الحالة
            if order_type == 'salla':
                status_note = OrderStatusNote(
                    order_id=order_id,
                    employee_id=employee.id,
                    status_flag=new_status,
                    notes=notes
                )
            else:
                status_note = OrderStatusNote(
                    custom_order_id=order_id,
                    employee_id=employee.id,
                    status_flag=new_status,
                    notes=notes
                )
            
            db.session.add(status_note)
            updated_count += 1
        
        db.session.commit()
        flash(f'تم تحديث {updated_count} طلب بنجاح', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'حدث خطأ أثناء معالجة الملف: {str(e)}', 'error')
        current_app.logger.error(f"Error processing Excel file: {str(e)}")
    
    return redirect(url_for('orders.index'))


# routes.py - إضافة endpoint جديد

@orders_bp.route('/download_excel_template')
def download_excel_template():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    # جلب الطلبات المحددة من المعامل
    selected_orders_param = request.args.get('selected_orders', '')
    selected_orders = selected_orders_param.split(',') if selected_orders_param else []
    
    # جلب الحالات المخصصة المتاحة
    custom_statuses = EmployeeCustomStatus.query.join(Employee).filter(
        Employee.store_id == user.store_id
    ).all()
    
    # جلب الطلبات المطلوبة
    if selected_orders:
        salla_order_ids = []
        custom_order_ids = []
        
        for order_str in selected_orders:
            if ':' in order_str:
                order_type, order_id = order_str.split(':', 1)
                if order_type == 'salla':
                    salla_order_ids.append(order_id)
                elif order_type == 'custom':
                    custom_order_ids.append(order_id)
        
        salla_orders = SallaOrder.query.filter(
            SallaOrder.id.in_(salla_order_ids),
            SallaOrder.store_id == user.store_id
        ).all() if salla_order_ids else []
        
        custom_orders = CustomOrder.query.filter(
            CustomOrder.id.in_(custom_order_ids),
            CustomOrder.store_id == user.store_id
        ).all() if custom_order_ids else []
        
    else:
        if request.cookies.get('is_admin') == 'true' or (employee and employee.role in ['reviewer', 'manager']):
            salla_orders = SallaOrder.query.filter_by(store_id=user.store_id).all()
            custom_orders = CustomOrder.query.filter_by(store_id=user.store_id).all()
        else:
            salla_orders = SallaOrder.query.join(OrderAssignment).filter(
                OrderAssignment.employee_id == employee.id,
                SallaOrder.store_id == user.store_id
            ).all()
            custom_orders = CustomOrder.query.join(OrderAssignment).filter(
                OrderAssignment.employee_id == employee.id,
                CustomOrder.store_id == user.store_id
            ).all()
    
    # تحضير البيانات ل Excel - كل منتج في صف منفصل
    data = []
    image_urls = []  # لتخزين روابط الصور لكل صف (كل منتج)
    order_id_map = {}  # لتتبع رقم الطلب الأول لكل مجموعة منتجات
    
    for order in salla_orders + custom_orders:
        order_type = 'salla' if isinstance(order, SallaOrder) else 'custom'
        order_id = order.id if order_type == 'salla' else order.order_number
        
        # جلب الحالة المخصصة (آخر حالة)
        last_emp_status = None
        if order_type == 'salla':
            last_emp_status = OrderEmployeeStatus.query.filter_by(order_id=order.id).order_by(OrderEmployeeStatus.created_at.desc()).first()
        else:
            last_emp_status = OrderEmployeeStatus.query.filter_by(custom_order_id=order.id).order_by(OrderEmployeeStatus.created_at.desc()).first()
        
        custom_status_name = last_emp_status.status.name if last_emp_status and last_emp_status.status else ''

        # استخراج بيانات المنتجات وخياراتها (لطلبات سلة فقط)
        if order_type == 'salla' and order.raw_data:
            try:
                raw_data = json.loads(order.raw_data)
                items = raw_data.get('items', [])
                
                for item_index, item in enumerate(items):
                    product_name = item.get('name', '')
                    quantity = item.get('quantity', 0)
                    price = item.get('price', {}).get('amount', 0) if isinstance(item.get('price'), dict) else item.get('price', 0)
                    sku = item.get('sku', '')
                    
                    # استخراج صورة المنتج
                    main_image = ''
                    thumbnail_url = item.get('product_thumbnail') or item.get('thumbnail')
                    if thumbnail_url and isinstance(thumbnail_url, str):
                        main_image = thumbnail_url
                    
                    # استخراج الخيارات بشكل مفصل
                    options_text = ""
                    options = item.get('options', [])
                    if options:
                        option_details = []
                        for option in options:
                            option_name = option.get('name', '')
                            option_value = option.get('value', '')
                            
                            # معالجة القيم المعقدة (قاموس أو قائمة)
                            if isinstance(option_value, dict):
                                option_value = option_value.get('name', '') or option_value.get('value', '') or str(option_value)
                            elif isinstance(option_value, list):
                                # إذا كانت القيمة قائمة، نعالج كل عنصر
                                values_list = []
                                for val in option_value:
                                    if isinstance(val, dict):
                                        val_str = val.get('name', '') or val.get('value', '') or str(val)
                                        values_list.append(val_str)
                                    else:
                                        values_list.append(str(val))
                                option_value = ', '.join(values_list)
                            
                            option_details.append(f"{option_name}: {option_value}")
                        
                        options_text = " | ".join(option_details)
                    
                    # إضافة المنتج مع خياراته
                    product_info = f"{product_name} (×{quantity})"
                    if options_text:
                        product_info += f" - {options_text}"
                    
                    # إضافة صف لكل منتج
                    # فقط أول منتج في الطلب يعرض رقم الطلب والحالة
                    display_order_id = order_id if item_index == 0 else ""
                    display_custom_status = custom_status_name if item_index == 0 else ""
                    
                    data.append({
                        'order_id': display_order_id,
                        'product_name': product_name,
                        'quantity': quantity,
                        'price': price,
                        'sku': sku,
                        'product_options': options_text,
                        'custom_status': display_custom_status
                    })
                    
                    # تخزين معلومات لدمج الخلايا لاحقاً
                    if item_index == 0:
                        order_id_map[len(data)] = len(items)  # حفظ عدد المنتجات لهذا الطلب
                    
                    # إضافة صورة هذا المنتج
                    image_urls.append(main_image)
                    
            except Exception as e:
                logger.error(f"Error parsing order data: {str(e)}")
                # إضافة صف للخطأ
                data.append({
                    'order_id': order_id,
                    'product_name': "خطأ في تحليل البيانات",
                    'quantity': "",
                    'price': "",
                    'sku': "",
                    'product_options': "",
                    'custom_status': custom_status_name
                })
                image_urls.append("")
                order_id_map[len(data)] = 1  # طلب به خطأ
        else:
            # للطلبات المخصصة أو إذا لم يكن هناك منتجات
            data.append({
                'order_id': order_id,
                'product_name': "لا توجد منتجات",
                'quantity': "",
                'price': "",
                'sku': "",
                'product_options': "",
                'custom_status': custom_status_name
            })
            image_urls.append("")
            order_id_map[len(data)] = 1  # طلب بدون منتجات
    
    # إنشاء DataFrame
    df = pd.DataFrame(data)
    
    # إنشاء Excel في الذاكرة
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='الطلبات', index=False)
        
        # الحصول على ورقة العمل للتنسيق
        workbook = writer.book
        worksheet = writer.sheets['الطلبات']
        
        # إضافة عمود للصور يدويًا
        worksheet.insert_cols(2)  # إدراج عمود جديد في الموضع الثاني (لصور المنتجات)
        worksheet.cell(row=1, column=2, value='صورة المنتج')
        
        # إضافة الصور إلى الخلايا
        from openpyxl.drawing.image import Image
        import requests
        from io import BytesIO

        # إضافة الصور لكل منتج
        for row_idx, img_url in enumerate(image_urls, start=2):
            if img_url and isinstance(img_url, str):
                try:
                    response = requests.get(img_url, timeout=10)
                    if response.status_code == 200:
                        img_data = BytesIO(response.content)
                        img = Image(img_data)
                        
                        # تكبير حجم الصورة
                        img.width = 120
                        img.height = 120
                        
                        # وضع الصورة في الخلية مع تكبير حجم الخلية
                        cell_ref = f'B{row_idx}'
                        worksheet.add_image(img, cell_ref)
                        worksheet.row_dimensions[row_idx].height = 90
                        worksheet.column_dimensions['B'].width = 20  # زيادة عرض عمود الصور
                except Exception as e:
                    logger.error(f"Error loading image: {str(e)}")
                    # وضع رابط الصورة كنص إذا فشل تحميل الصورة
                    worksheet.cell(row=row_idx, column=2, value=img_url)
        
        # دمج خلايا رقم الطلب والحالة المخصصة للمنتجات المنتمية لنفس الطلب
        from openpyxl.styles import Alignment, Border, Side, Font
        current_row = 2
        for row_num, product_count in order_id_map.items():
            if product_count > 1:
                # دمج الخلايا العمودية لرقم الطلب
                start_cell = f'A{current_row}'
                end_cell = f'A{current_row + product_count - 1}'
                worksheet.merge_cells(f'{start_cell}:{end_cell}')
                
                # دمج الخلايا العمودية للحالة المخصصة
                status_start_cell = f'H{current_row}'  # الحالة في العمود H
                status_end_cell = f'H{current_row + product_count - 1}'
                worksheet.merge_cells(f'{status_start_cell}:{status_end_cell}')
                
                # محاذاة النص في منتصف الخلية المدمجة
                worksheet[start_cell].alignment = Alignment(vertical='center', horizontal='center')
                worksheet[status_start_cell].alignment = Alignment(vertical='center', horizontal='center')
            
            current_row += product_count
        
        # إضافة قائمة منسدلة للحالات
        if custom_statuses:
            status_names = [status.name for status in custom_statuses]
            status_sheet = workbook.create_sheet("الحالات المخفية")
            for i, status in enumerate(status_names, 1):
                status_sheet.cell(row=i, column=1, value=status)
            
            # إضافة قائمة منسدلة باستخدام Data Validation
            dv = DataValidation(
                type="list",
                formula1="='الحالات المخفية'!$A$1:$A$" + str(len(status_names))
            )
            dv.error = 'القيمة غير صحيحة'
            dv.errorTitle = 'قيمة غير صالحة'
            dv.prompt = 'يرجى اختيار حالة من القائمة'
            dv.promptTitle = 'اختيار الحالة'
            
            # تطبيق التحقق على عمود الحالة المخصصة (العمود H)
            for row in range(2, len(df) + 2):
                # فقط الصف الأول من كل طلب يحتوي على قائمة منسدلة للحالة
                cell_value = worksheet.cell(row=row, column=1).value
                if cell_value:  # إذا كانت الخلية تحتوي على رقم طلب (أي هي الصف الأول للطلب)
                    dv.add(worksheet.cell(row=row, column=8))
            
            worksheet.add_data_validation(dv)
            status_sheet.sheet_state = 'hidden'
        
        # تنسيق الأعمدة وإضافة الفواصل
        column_widths = {
            'A': 15,  # رقم الطلب
            'B': 20,  # صورة المنتج
            'C': 30,  # اسم المنتج
            'D': 10,  # الكمية
            'E': 15,  # السعر
            'F': 15,  # SKU
            'G': 40,  # خيارات المنتج
            'H': 20   # الحالة المخصصة
        }
        
        for col_letter, width in column_widths.items():
            worksheet.column_dimensions[col_letter].width = width
        
        # إضافة الفواصل والتنسيق
        thin_border = Border(left=Side(style='thin'), 
                             right=Side(style='thin'), 
                             top=Side(style='thin'), 
                             bottom=Side(style='thin'))
        
        # تطبيق الفواصل على جميع الخلايا
        for row in worksheet.iter_rows(min_row=1, max_row=worksheet.max_row, min_col=1, max_col=8):
            for cell in row:
                cell.border = thin_border
                cell.alignment = Alignment(vertical='center', horizontal='center', wrap_text=True)
        
        # جعل عناوين الأعمدة غامقة
        for cell in worksheet[1]:
            cell.font = Font(bold=True)
        
        # تمكين التفاف النص للأعمدة
        for row in range(2, len(df) + 2):
            worksheet.cell(row=row, column=3).alignment = Alignment(wrap_text=True)
            worksheet.cell(row=row, column=7).alignment = Alignment(wrap_text=True)
    
    output.seek(0)
    
    # إرسال الملف
    filename = f"orders_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return send_file(
        output,
        as_attachment=True,
        download_name=filename,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )