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

# إعداد الـ logger
logger = logging.getLogger(__name__)

@orders_bp.route('/')
def index():
    """عرض قائمة الطلبات (سلة + مخصصة) مع نظام الترحيل الكامل"""
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response

    # جلب الفلاتر
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    status_filter = request.args.get('status', '')
    employee_filter = request.args.get('employee', '')
    custom_status_filter = request.args.get('custom_status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    search_query = request.args.get('search', '')
    order_type = request.args.get('order_type', 'all')

    # تحميل الحالات
    order_statuses = OrderStatus.query.filter_by(store_id=user.store_id).order_by(OrderStatus.sort).all()

    # التحقق من الصلاحيات
    is_reviewer = False
    if request.cookies.get('is_admin') == 'true':
        is_reviewer = True
    elif employee and employee.role in ['reviewer', 'manager']:
        is_reviewer = True

    try:
        # Queries
        salla_query = SallaOrder.query.filter_by(store_id=user.store_id)
        custom_query = CustomOrder.query.filter_by(store_id=user.store_id)

        # فلترة حسب الموظف
        if employee_filter:
            salla_query = salla_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)
            custom_query = custom_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)

        # فلترة حسب الحالة المخصصة (EmployeeCustomStatus)
        if custom_status_filter:
            custom_status_id = int(custom_status_filter)
            salla_query = salla_query.join(OrderEmployeeStatus).filter(OrderEmployeeStatus.status_id == custom_status_id)
            custom_query = custom_query.join(OrderEmployeeStatus).filter(OrderEmployeeStatus.status_id == custom_status_id)

        # فلترة حسب البحث
        if search_query:
            search_filter = f"%{search_query}%"
            salla_query = salla_query.filter(SallaOrder.customer_name.ilike(search_filter) | SallaOrder.id.ilike(search_filter))
            custom_query = custom_query.filter(CustomOrder.customer_name.ilike(search_filter) | CustomOrder.order_number.ilike(search_filter))

        # فلترة حسب التاريخ
        if date_from or date_to:
            if date_from:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                salla_query = salla_query.filter(SallaOrder.created_at >= date_from_obj)
                custom_query = custom_query.filter(CustomOrder.created_at >= date_from_obj)
            if date_to:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
                salla_query = salla_query.filter(SallaOrder.created_at <= date_to_obj)
                custom_query = custom_query.filter(CustomOrder.created_at <= date_to_obj)

        # الترحيل
        if order_type == "salla":
            orders_query = salla_query.order_by(db.desc(SallaOrder.created_at))
            pagination_obj = orders_query.paginate(page=page, per_page=per_page, error_out=False)
            orders = pagination_obj.items
        elif order_type == "custom":
            orders_query = custom_query.order_by(db.desc(CustomOrder.created_at))
            pagination_obj = orders_query.paginate(page=page, per_page=per_page, error_out=False)
            orders = pagination_obj.items
        else:
            salla_pagination = salla_query.order_by(db.desc(SallaOrder.created_at)).paginate(page=1, per_page=per_page*2, error_out=False)
            custom_pagination = custom_query.order_by(db.desc(CustomOrder.created_at)).paginate(page=1, per_page=per_page*2, error_out=False)
            all_orders = salla_pagination.items + custom_pagination.items
            all_orders.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
            total_orders = salla_pagination.total + custom_pagination.total
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            orders = all_orders[start_idx:end_idx]
            pagination_obj = type('Obj', (object,), {
                'items': orders,
                'page': page,
                'per_page': per_page,
                'total': total_orders,
                'pages': ceil(total_orders / per_page),
                'has_prev': page > 1,
                'has_next': end_idx < total_orders,
                'prev_num': page - 1 if page > 1 else None,
                'next_num': page + 1 if end_idx < total_orders else None
            })()

        # تجهيز البيانات
        processed_orders = []
        for order in orders:
            if isinstance(order, SallaOrder):
                # آخر حالة ملاحظة
                last_note = OrderStatusNote.query.filter_by(order_id=order.id)\
                    .order_by(OrderStatusNote.created_at.desc()).first()
                # آخر حالة مخصصة
                last_emp_status = OrderEmployeeStatus.query.filter_by(order_id=order.id)\
                    .order_by(OrderEmployeeStatus.created_at.desc()).first()

                processed_orders.append({
                    'id': order.id,
                    'reference_id': order.id,
                    'customer_name': order.customer_name,
                    'created_at': humanize_time(order.created_at) if order.created_at else '',
                    'status': {
                        'slug': order.status.slug if order.status else 'unknown',
                        'name': order.status.name if order.status else 'غير محدد'
                    },
                    'status_obj': order.status,
                    'raw_created_at': order.created_at,
                    'type': 'salla',
                    'last_note': last_note,
                    'last_emp_status': last_emp_status,
                })
            else:
                last_note = OrderStatusNote.query.filter_by(custom_order_id=order.id)\
                    .order_by(OrderStatusNote.created_at.desc()).first()
                last_emp_status = OrderEmployeeStatus.query.filter_by(custom_order_id=order.id)\
                    .order_by(OrderEmployeeStatus.created_at.desc()).first()

                processed_orders.append({
                    'id': order.id,
                    'reference_id': order.order_number,
                    'customer_name': order.customer_name,
                    'created_at': humanize_time(order.created_at) if order.created_at else '',
                    'status': {
                        'slug': order.status.slug if order.status else 'custom',
                        'name': order.status.name if order.status else 'مخصص'
                    },
                    'status_obj': order.status,
                    'raw_created_at': order.created_at,
                    'type': 'custom',
                    'total_amount': order.total_amount,
                    'currency': order.currency,
                    'last_note': last_note,
                    'last_emp_status': last_emp_status,
                })

        employees = Employee.query.filter_by(store_id=user.store_id, is_active=True).all() if is_reviewer else []

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

        filters = {
            'status': status_filter,
            'employee': employee_filter,
            'custom_status': custom_status_filter,
            'date_from': date_from,
            'date_to': date_to,
            'search': search_query,
            'order_type': order_type
        }

        return render_template('orders.html',
                               orders=processed_orders,
                               employees=employees,
                               custom_statuses=EmployeeCustomStatus.query.filter_by(employee_id=employee.id).all() if employee else [],
                               pagination=pagination,
                               filters=filters,
                               order_statuses=order_statuses,
                               is_reviewer=is_reviewer,
                               current_employee=employee,
                               order_type=order_type)

    except Exception as e:
        flash(f'خطأ غير متوقع: {str(e)}', 'error')
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