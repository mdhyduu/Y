# orders/routes.py
import json
import logging
import hmac
import hashlib
from math import ceil
from datetime import datetime, timedelta
from flask import (render_template, request, flash, redirect, url_for, jsonify, 
                   make_response, current_app, send_file)
import requests
from sqlalchemy import nullslast, or_, and_, func, exists
from sqlalchemy.orm import selectinload
from weasyprint import HTML
from io import BytesIO

from . import orders_bp
from app.models import (db, SallaOrder, CustomOrder, OrderStatus, User, Employee, 
                     OrderAssignment, EmployeeCustomStatus, OrderStatusNote, 
                     OrderEmployeeStatus, OrderProductStatus, CustomNoteStatus, OrderAddress, SallaStatusChange)
from app.utils import get_user_from_cookies, process_order_data, format_date, humanize_time
from app.token_utils import refresh_salla_token
from app.config import Config
from app.scheduler_tasks import handle_order_completion

# إعداد المسجل
logger = logging.getLogger('salla_app')

# ===== دوال مساعدة عامة =====
def get_payment_method_name(payment_method):
    """تحويل رمز طريقة الدفع إلى اسم مفهوم"""
    payment_methods = {
        'tabby_installment': 'تابي ',
        'tamara_installment': 'تامرا ',
        'mada': 'مدى',
        'visa': 'فيزا / ماستركارد',
        'mastercard': 'ماستركارد',
        'apple_pay': 'Apple Pay',
        'stc_pay': 'STC Pay',
        'urpay': 'UrPay',
        'cod': 'الدفع عند الاستلام',
        'bank': 'تحويل بنكي',
        'wallet': 'المحفظة',
        '': 'غير محدد'
    }
    
    if isinstance(payment_method, dict):
        return payment_method.get('name', 'غير محدد')
    
    return payment_methods.get(payment_method, payment_method or 'غير محدد')

def ensure_valid_access_token(user):
    """التأكد من وجود توكن وصول صالح مع معالجة الأخطاء المحسنة"""
    try:
        if not user:
            logger.error("لا يوجد مستخدم للمصادقة")
            return None
            
        if user.tokens_are_valid:
            return user.salla_access_token
        
        success = refresh_salla_token(user)
        
        if success and user.tokens_are_valid:
            db.session.refresh(user)
            return user.salla_access_token
        else:
            return user.salla_access_token if user.salla_access_token else None
            
    except Exception as e:
        logger.error(f"خطأ في التأكد من صلاحية التوكن: {str(e)}")
        return user.salla_access_token if user and user.salla_access_token else None

def extract_shipping_info(order_data):
    """استخراج معلومات الشحن من بيانات الطلب مع تحسينات البوليصة"""
    try:
        shipments_data = order_data.get('shipments', [])
        
        shipping_info = {
            'has_shipping': bool(shipments_data),
            'status': '',
            'tracking_number': None,
            'tracking_link': None,
            'has_tracking': False,
            'has_shipping_policy': False,
            'shipping_policy_url': None,
            'shipment_details': []
        }
        
        for shipment in shipments_data:
            shipment_tracking_link = shipment.get('tracking_link')
            shipment_tracking_number = shipment.get('tracking_number')
            shipment_label = shipment.get('label')
            
            # استخراج رابط البوليصة
            shipment_policy_url = None
            if shipment_label and isinstance(shipment_label, dict):
                shipment_policy_url = shipment_label.get('url')
            
            if not shipment_policy_url:
                shipment_policy_url = shipment.get('shipping_policy_url')
            
            if not shipment_policy_url and shipment.get('documents'):
                for doc in shipment.get('documents', []):
                    if doc.get('type') == 'shipping_policy':
                        shipment_policy_url = doc.get('url')
                        break
            
            shipment_has_tracking = False
            final_tracking_link = None
            
            if shipment_tracking_link and shipment_tracking_link not in ["", "0", "null", "None"]:
                if shipment_tracking_link.startswith(('http://', 'https://')):
                    final_tracking_link = shipment_tracking_link
                else:
                    final_tracking_link = f"https://track.salla.sa/track/{shipment_tracking_link}"
                shipment_has_tracking = True
            
            if not final_tracking_link and shipment_tracking_number:
                final_tracking_link = f"https://track.salla.sa/track/{shipment_tracking_number}"
                shipment_has_tracking = True
            
            shipment_info = {
                'id': shipment.get('id'),
                'courier_name': shipment.get('courier_name', ''),
                'courier_logo': shipment.get('courier_logo', ''),
                'tracking_number': shipment_tracking_number,
                'tracking_link': final_tracking_link,
                'has_tracking': shipment_has_tracking,
                'status': shipment.get('status', ''),
                'label': shipment_label,
                'has_label': bool(shipment_label and shipment_label not in ["", "0", "null"]),
                'shipping_policy_url': shipment_policy_url,
                'has_shipping_policy': bool(shipment_policy_url),
                'shipping_number': shipment.get('shipping_number'),
                'total_weight': shipment.get('total_weight', {}),
                'packages': shipment.get('packages', [])
            }
            
            shipping_info['shipment_details'].append(shipment_info)
            
            # تحديث المعلومات العامة
            if shipment_info['has_shipping_policy'] and not shipping_info['has_shipping_policy']:
                shipping_info['has_shipping_policy'] = True
                shipping_info['shipping_policy_url'] = shipment_policy_url
            
            if not shipping_info['status'] and shipment_info['status']:
                shipping_info['status'] = shipment_info['status']
            
            if shipment_has_tracking and not shipping_info['has_tracking']:
                shipping_info['tracking_link'] = final_tracking_link
                shipping_info['tracking_number'] = shipment_tracking_number
                shipping_info['has_tracking'] = True
        
        return shipping_info
    
    except Exception as e:
        logger.error(f"Error extracting shipping info: {str(e)}")
        return {}

def extract_order_address(order_data):
    """استخراج بيانات العنوان مع الأولوية للمتسلم"""
    shipping_data = order_data.get('shipping', {}) or {}
    customer_data = order_data.get('customer', {}) or {}
    
    # الأولوية للمتسلم (receiver)
    receiver_data = shipping_data.get('receiver', {}) or {}
    address_data = shipping_data.get('address') or shipping_data.get('pickup_address', {}) or {}
    
    if receiver_data.get('name') or address_data:
        name = receiver_data.get('name', '').strip()
        phone = receiver_data.get('phone') or f"{customer_data.get('mobile_code', '')}{customer_data.get('mobile', '')}"
        country = address_data.get('country', customer_data.get('country', ''))
        city = address_data.get('city', customer_data.get('city', ''))
        full_address = address_data.get('shipping_address', '') or customer_data.get('location', '')
        
        if not name:
            name = customer_data.get('full_name') or f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip()
        
        address_type = 'receiver'
    else:
        name = customer_data.get('full_name') or f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip()
        phone = f"{customer_data.get('mobile_code', '')}{customer_data.get('mobile', '')}"
        country = customer_data.get('country', '')
        city = customer_data.get('city', '')
        full_address = customer_data.get('location', '')
        address_type = 'customer'
    
    if not name:
        name = 'عميل غير معروف'
    
    if not full_address:
        parts = [p for p in [country, city] if p]
        full_address = ' - '.join(parts) if parts else 'لم يتم تحديد العنوان'
    
    return {
        'name': name,
        'phone': phone,
        'country': country,
        'city': city,
        'full_address': full_address,
        'address_type': address_type
    }

def fetch_order_items_from_api(user, order_id):
    """جلب عناصر الطلب من API"""
    try:
        access_token = ensure_valid_access_token(user)
        if not access_token:
            return []
            
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        response = requests.get(
            f"{Config.SALLA_BASE_URL}/orders/items",
            params={'order_id': order_id, 'include': 'images'},
            headers=headers,
            timeout=15
        )
        
        if response.status_code == 200:
            return response.json().get('data', [])
        else:
            logger.error(f"خطأ في جلب العناصر من API: {response.status_code}")
            return []
    except Exception as e:
        logger.error(f"خطأ في جلب العناصر من API: {str(e)}")
        return []

def fetch_order_data_from_api(user, order_id):
    """جلب بيانات الطلب من API مع تضمين العناصر"""
    try:
        access_token = ensure_valid_access_token(user)
        if not access_token:
            return None, []
            
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        order_response = requests.get(
            f"{Config.SALLA_ORDERS_API}/{order_id}",
            headers=headers,
            timeout=15
        )
        
        if order_response.status_code != 200:
            return None, []
        
        order_data = order_response.json().get('data', {})
        items_data = fetch_order_items_from_api(user, order_id)
        
        if items_data:
            order_data['items'] = items_data
        
        return order_data, items_data
        
    except Exception as e:
        logger.error(f"خطأ في جلب بيانات الطلب من API: {str(e)}")
        return None, []

def create_order_from_api_data(user, order_data, items_data=None):
    """إنشاء طلب جديد في قاعدة البيانات من بيانات API"""
    try:
        order_id = str(order_data.get('id'))
        if not order_id:
            return None
            
        customer = order_data.get('customer', {})
        customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        if not customer_name:
            customer_name = order_data.get('customer_name', 'عميل غير معروف')
            
        created_at = None
        date_info = order_data.get('date', {})
        if date_info and 'date' in date_info:
            try:
                date_str = date_info['date'].split('.')[0]
                created_at = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
            except Exception:
                created_at = datetime.utcnow()
        
        total_info = order_data.get('total') or order_data.get('amounts', {}).get('total', {})
        total_amount = float(total_info.get('amount', 0))
        currency = total_info.get('currency', 'SAR')
        
        if items_data:
            order_data['items'] = items_data
        else:
            items_data = fetch_order_items_from_api(user, order_id)
            if items_data:
                order_data['items'] = items_data
        
        new_order = SallaOrder(
            id=order_id,
            store_id=user.store_id,
            customer_name=customer_name,
            created_at=created_at or datetime.utcnow(),
            total_amount=total_amount,
            currency=currency,
            payment_method=order_data.get('payment_method', ''),
            full_order_data=order_data
        )
        
        db.session.add(new_order)
        
        address_info = extract_order_address(order_data)
        if address_info:
            new_address = OrderAddress(
                order_id=order_id,
                **address_info
            )
            db.session.add(new_address)
        
        return new_order
        
    except Exception as e:
        logger.error(f"خطأ في إنشاء الطلب من بيانات API: {str(e)}")
        return None

def fetch_additional_order_data(store_id, order_id_str):
    """جلب البيانات الإضافية للطلب من قاعدة البيانات"""
    try:
        custom_note_statuses = CustomNoteStatus.query.filter_by(store_id=store_id).all()
        
        status_notes = OrderStatusNote.query.filter_by(order_id=order_id_str).options(
            selectinload(OrderStatusNote.admin),
            selectinload(OrderStatusNote.employee),
            selectinload(OrderStatusNote.custom_status)
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
            OrderEmployeeStatus.order_id == order_id_str
        ).order_by(
            OrderEmployeeStatus.created_at.desc()
        ).all()

        status_records = OrderProductStatus.query.filter_by(order_id=order_id_str).all()
        product_statuses = {}
        for status in status_records:
            product_statuses[status.product_id] = {
                'status': status.status,
                'notes': status.notes,
                'updated_at': status.updated_at
            }
        
        return {
            'custom_note_statuses': custom_note_statuses,
            'status_notes': status_notes,
            'employee_statuses': employee_statuses,
            'product_statuses': product_statuses
        }
    except Exception as e:
        logger.error(f"خطأ في جلب البيانات الإضافية: {str(e)}")
        return {
            'custom_note_statuses': [],
            'status_notes': [],
            'employee_statuses': [],
            'product_statuses': {}
        }

def build_orders_query(user, employee, is_reviewer, is_delivery_personnel):
    """بناء استعلام الطلبات بناءً على صلاحيات المستخدم"""
    orders_query = SallaOrder.query.filter_by(store_id=user.store_id).options(
        selectinload(SallaOrder.status),
        selectinload(SallaOrder.assignments).selectinload(OrderAssignment.employee)
    )
    
    if is_delivery_personnel:
        orders_query = orders_query.join(OrderAddress).filter(
            OrderAddress.city == 'الرياض',
            OrderAddress.address_type == 'receiver'
        )
    
    if not is_reviewer and employee and not (employee.role in ['delivery_manager']):
        assignment_exists = exists().where(
            and_(
                OrderAssignment.order_id == SallaOrder.id,
                OrderAssignment.employee_id == employee.id
            )
        )
        orders_query = orders_query.filter(assignment_exists)
    
    return orders_query

def apply_orders_filters(orders_query, filters):
    """تطبيق الفلاتر على استعلام الطلبات"""
    status_filter = filters.get('status')
    employee_filter = filters.get('employee')
    custom_status_filter = filters.get('custom_status')
    date_from = filters.get('date_from')
    date_to = filters.get('date_to')
    search_query = filters.get('search')
    
    if status_filter in ['late', 'missing', 'not_shipped', 'refunded']:
        orders_query = orders_query.join(OrderStatusNote).filter(
            OrderStatusNote.status_flag == status_filter
        )
    elif status_filter:
        orders_query = orders_query.join(SallaOrder.status).filter(OrderStatus.slug == status_filter)
    
    if employee_filter:
        orders_query = orders_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)
    
    if custom_status_filter:
        custom_status_id = int(custom_status_filter)
        orders_query = orders_query.join(SallaOrder.employee_statuses).filter(
            OrderEmployeeStatus.status_id == custom_status_id
        )
            
    if search_query:
        search_terms = [term.strip() for term in search_query.split(',')]
        search_filters = []
        for term in search_terms:
            if term:
                search_filters.append(SallaOrder.reference_id.ilike(f'%{term}%'))
        
        if search_filters:
            orders_query = orders_query.filter(or_(*search_filters))
    
    if date_from and date_to:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            orders_query = orders_query.filter(SallaOrder.created_at.between(date_from_obj, date_to_obj))
        except ValueError:
            pass
    elif date_from:
        try:
            date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
            orders_query = orders_query.filter(SallaOrder.created_at >= date_from_obj)
        except ValueError:
            pass
    elif date_to:
        try:
            date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
            orders_query = orders_query.filter(SallaOrder.created_at <= date_to_obj)
        except ValueError:
            pass
    
    return orders_query

def process_order_for_display(order):
    """معالجة بيانات الطلب للعرض"""
    order_data = order.full_order_data or {}
    reference_id = order_data.get('reference_id', order.id)
    status_name = order.status.name if order.status else 'غير محدد'
    status_slug = order.status.slug if order.status else 'unknown'
    
    last_note = OrderStatusNote.query.filter_by(order_id=order.id).order_by(OrderStatusNote.created_at.desc()).first()
    last_emp_status = OrderEmployeeStatus.query.filter_by(order_id=order.id).order_by(OrderEmployeeStatus.created_at.desc()).first()
    
    payment_method = order_data.get('payment_method', '')
    payment_method_name = get_payment_method_name(payment_method)
    
    order_address = OrderAddress.query.filter_by(order_id=order.id).first()
    order_city = order_address.city if order_address else 'غير محدد'
    
    return {
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
        'employee_statuses': [last_emp_status] if last_emp_status else [],
        'status_notes': [last_note] if last_note else [],
        'payment_method': payment_method,
        'payment_method_name': payment_method_name,
        'city': order_city,
        'order_data': order_data
    }

# ===== الروتات الرئيسية =====
@orders_bp.route('/')
def index():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 25, type=int) 
    
    if page < 1: 
        page = 1
    if per_page not in [10, 25, 50, 100, 125, 150, 200, 250, 300]: 
        per_page = 25
    
    # التحقق من صلاحيات المستخدم
    is_admin = request.cookies.get('is_admin') == 'true'
    is_reviewer = is_admin or (employee and employee.role in ['reviewer', 'manager'])
    is_delivery_personnel = employee and employee.role in ['delivery_manager', 'delivery']
    
    if not user.salla_access_token:
        flash('يجب ربط المتجر مع سلة أولاً' if is_admin else 'المتجر غير مرتبط بسلة', 'error')
        return redirect(url_for('auth.link_store' if is_admin else 'user_auth.logout'))
    
    try:
        # بناء الاستعلام الأساسي
        orders_query = build_orders_query(user, employee, is_reviewer, is_delivery_personnel)
        
        # تطبيق الفلاتر
        filters = {
            'status': request.args.get('status', ''),
            'employee': request.args.get('employee', ''),
            'custom_status': request.args.get('custom_status', ''),
            'date_from': request.args.get('date_from', ''),
            'date_to': request.args.get('date_to', ''),
            'search': request.args.get('search', '')
        }
        
        orders_query = apply_orders_filters(orders_query, filters)
        orders_query = orders_query.order_by(nullslast(db.desc(SallaOrder.created_at)))
        
        # التقسيم إلى صفحات
        pagination_obj = orders_query.paginate(page=page, per_page=per_page, error_out=False)
        orders = pagination_obj.items
        
        # معالجة الطلبات للعرض
        processed_orders = [process_order_for_display(order) for order in orders]
        
        # جلب البيانات الإضافية
        order_statuses = OrderStatus.query.filter_by(store_id=user.store_id).order_by(OrderStatus.sort).all()
        employees = Employee.query.filter_by(store_id=user.store_id, is_active=True).all() if is_reviewer else []
        
        custom_statuses = []
        if is_reviewer:
            custom_statuses = EmployeeCustomStatus.query.join(Employee).filter(
                Employee.store_id == user.store_id
            ).all()
        elif employee:
            custom_statuses = EmployeeCustomStatus.query.filter_by(employee_id=employee.id).all()
        
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
        
        template_data = {
            'orders': processed_orders, 
            'employees': employees,
            'custom_statuses': custom_statuses,
            'pagination': pagination,
            'filters': filters,
            'order_statuses': order_statuses,
            'is_reviewer': is_reviewer,
            'is_delivery_personnel': is_delivery_personnel,
            'current_employee': employee
        }
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('orders_partial.html', **template_data)
        
        return render_template('orders.html', **template_data)
    
    except Exception as e:
        error_msg = f'حدث خطأ غير متوقع: {str(e)}'
        flash(error_msg, 'error')
        logger.exception(error_msg)
        return redirect(url_for('orders.index'))

@orders_bp.route('/<int:order_id>')
def order_details(order_id):
    user, current_employee = get_user_from_cookies()
    
    if current_employee:
        current_employee = db.session.query(Employee).options(
            selectinload(Employee.custom_statuses)
        ).get(current_employee.id)
    
    if not user:
        flash("الرجاء تسجيل الدخول أولاً", "error")
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response

    try:
        is_admin = request.cookies.get('is_admin') == 'true'
        is_reviewer = is_admin or (current_employee and current_employee.role in ['reviewer', 'manager'])

        # جلب بيانات الطلب
        order = SallaOrder.query.filter_by(id=str(order_id), store_id=user.store_id).first()
        order_data, items_data = None, []
        
        if order and order.full_order_data:
            order_data = order.full_order_data
            items_data = order_data.get('items', [])
            
            if not items_data:
                items_data = fetch_order_items_from_api(user, order_id)
                if items_data:
                    order_data['items'] = items_data
                    order.full_order_data = order_data
                    db.session.commit()
        else:
            order_data, items_data = fetch_order_data_from_api(user, order_id)
            
            if order_data:
                if order:
                    order.full_order_data = order_data
                else:
                    new_order = create_order_from_api_data(user, order_data, items_data)
                    if new_order:
                        order = new_order
                
                db.session.commit()

        if not order_data:
            flash('الطلب غير موجود أو لا يمكن الوصول إليه', 'error')
            return redirect(url_for('orders.index'))

        # معالجة بيانات الطلب للعرض
        processed_order = process_order_data(order_id, items_data)
        
        order_address = OrderAddress.query.filter_by(order_id=str(order_id)).first()
        notes = order_data.get('notes', '')
        payment_method = order_data.get('payment_method', {})
        
        if isinstance(payment_method, dict):
            payment_method_name = payment_method.get('name', 'غير محدد')
        else:
            payment_method_name = str(payment_method) if payment_method else 'غير محدد'

        processed_items = []
        for item in items_data:
            processed_item = {
                'id': item.get('id'),
                'name': item.get('name', ''),
                'quantity': item.get('quantity', 1),
                'price': item.get('amounts', {}).get('total', {}).get('amount', 0),
                'currency': item.get('amounts', {}).get('total', {}).get('currency', 'SAR'),
                'notes': item.get('notes', ''),
                'product_type': item.get('product_type', ''),
                'product_thumbnail': item.get('product_thumbnail', ''),
                'options': item.get('options', []),
                'sku': item.get('sku', '')
            }
            processed_items.append(processed_item)

        processed_order.update({
            'id': order_id,
            'reference_id': order_data.get('reference_id') or order_data.get('id') or 'غير متوفر',
            'notes': notes,
            'payment_method': payment_method_name,
            'status': {
                'name': order_data.get('status', {}).get('name', 'غير معروف'),
                'slug': order_data.get('status', {}).get('slug', 'unknown')
            },
            'created_at': format_date(order_data.get('created_at', '')),
            'amount': {
                'sub_total': order_data.get('amounts', {}).get('sub_total', {'amount': 0, 'currency': 'SAR'}),
                'shipping_cost': order_data.get('amounts', {}).get('shipping_cost', {'amount': 0, 'currency': 'SAR'}),
                'discount': order_data.get('amounts', {}).get('discount', {'amount': 0, 'currency': 'SAR'}),
                'total': order_data.get('amounts', {}).get('total', {'amount': 0, 'currency': 'SAR'})
            },
            'items': processed_items,
            'full_order_data': order_data
        })
        
        # جلب البيانات الإضافية
        order_statuses = OrderStatus.query.filter_by(store_id=user.store_id).order_by(OrderStatus.sort).all()
        status_changes = SallaStatusChange.query.filter_by(
            order_id=str(order_id)
        ).order_by(SallaStatusChange.created_at.desc()).all()
        
        db_data = fetch_additional_order_data(user.store_id, str(order_id))
        shipping_info = extract_shipping_info(order_data)
        processed_order['shipping'] = shipping_info
        
        return render_template('order_details.html', 
            order=processed_order,
            status_changes=status_changes,
            order_address=order_address,
            order_statuses=order_statuses,
            status_notes=db_data['status_notes'],
            employee_statuses=db_data['employee_statuses'],
            custom_note_statuses=db_data['custom_note_statuses'],
            current_employee=current_employee,
            is_reviewer=is_reviewer,
            product_statuses=db_data['product_statuses']
        )

    except Exception as e:
        error_msg = f"حدث خطأ غير متوقع: {str(e)}"
        flash(error_msg, "error")
        logger.exception(f"Unexpected error: {str(e)}")
        return redirect(url_for('orders.index'))

@orders_bp.route('/<order_id>/shipping_policy/<int:shipment_index>')
def download_shipping_policy(order_id, shipment_index=0):
    """تحميل البوليصة كملف PDF مع تحسين الذاكرة"""
    user, current_employee = get_user_from_cookies()
    
    if not user:
        flash("الرجاء تسجيل الدخول أولاً", "error")
        return redirect(url_for('user_auth.login'))

    try:
        order = SallaOrder.query.filter_by(id=str(order_id), store_id=user.store_id).first()
        if not order:
            flash('الطلب غير موجود', 'error')
            return redirect(url_for('orders.index'))

        shipping_info = extract_shipping_info(order.full_order_data or {})
        shipment_details = shipping_info.get('shipment_details', [])
        
        if shipment_index >= len(shipment_details):
            flash('بيانات الشحن غير موجودة', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))

        shipment = shipment_details[shipment_index]
        
        if not shipment.get('has_shipping_policy') or not shipment.get('shipping_policy_url'):
            flash('لا توجد بوليصة شحن متاحة', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))

        reference_id = order.reference_id or order.id
        filename = f"بوليصة_شحن_{reference_id}.pdf"

        access_token = ensure_valid_access_token(user)
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/pdf,application/octet-stream'
        }
        
        policy_url = shipment['shipping_policy_url']
        
        # استخدام stream مع chunk_size محدد
        response = requests.get(policy_url, headers=headers, timeout=60, stream=True)
        
        if response.status_code == 200:
            # استخدام طريقة أكثر أماناً للتعامل مع الملفات الكبيرة
            file_data = BytesIO()
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    file_data.write(chunk)
            
            file_data.seek(0)
            
            content_type = response.headers.get('content-type', '')
            mimetype = 'application/pdf'
            if 'pdf' not in content_type.lower() and not policy_url.lower().endswith('.pdf'):
                mimetype = content_type
            
            return send_file(
                file_data,
                as_attachment=True,
                download_name=filename,
                mimetype=mimetype,
                as_attachment=True,
                conditional=True  # إضافة conditional response
            )
        else:
            flash('فشل في تحميل البوليصة', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))

    except requests.exceptions.Timeout:
        flash('انتهت مهلة تحميل البوليصة. يرجى المحاولة مرة أخرى.', 'error')
        logger.error(f"Timeout while downloading shipping policy for order {order_id}")
        return redirect(url_for('orders.order_details', order_id=order_id))
    except requests.exceptions.RequestException as e:
        flash('فشل في تحميل البوليصة due to a network error.', 'error')
        logger.error(f"Network error while downloading shipping policy for order {order_id}: {str(e)}")
        return redirect(url_for('orders.order_details', order_id=order_id))
    except Exception as e:
        error_msg = f"خطأ في تحميل البوليصة: {str(e)}"
        flash(error_msg, "error")
        logger.exception(f"Error downloading shipping policy for order {order_id}: {str(e)}")
        return redirect(url_for('orders.order_details', order_id=order_id))
# ===== دوال معالجة Webhook =====
def extract_store_id_from_webhook(webhook_data):
    """استخراج معرف المتجر من بيانات Webhook"""
    try:
        if 'merchant' in webhook_data and webhook_data['merchant'] is not None:
            return str(webhook_data['merchant'])
            
        if 'merchant_id' in webhook_data and webhook_data['merchant_id'] is not None:
            return str(webhook_data['merchant_id'])
            
        if 'store_id' in webhook_data and webhook_data['store_id'] is not None:
            return str(webhook_data['store_id'])
        
        if 'data' in webhook_data and isinstance(webhook_data['data'], dict):
            data_obj = webhook_data['data']
            
            if 'merchant' in data_obj and data_obj['merchant'] is not None:
                return str(data_obj['merchant'])
                
            if 'merchant_id' in data_obj and data_obj['merchant_id'] is not None:
                return str(data_obj['merchant_id'])
                
            if 'store_id' in data_obj and data_obj['store_id'] is not None:
                return str(data_obj['store_id'])
        
        def deep_find(obj, key):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k == key and v is not None:
                        return v
                    if isinstance(v, (dict, list)):
                        result = deep_find(v, key)
                        if result is not None:
                            return result
            elif isinstance(obj, list):
                for item in obj:
                    result = deep_find(item, key)
                    if result is not None:
                        return result
            return None
        
        for key in ['merchant', 'merchant_id', 'store_id']:
            value = deep_find(webhook_data, key)
            if value is not None:
                return str(value)
        
        return None
        
    except Exception as e:
        logger.error(f"خطأ في استخراج معرف المتجر: {str(e)}", exc_info=True)
        return None

def handle_order_creation(data, webhook_version='2'):
    """معالجة إنشاء طلب جديد من Webhook"""
    try:
        if webhook_version == '2':
            order_data = data.get('data', {})
            merchant_id = data.get('merchant')
        else:
            order_data = data
            merchant_id = data.get('merchant_id')

        store_id = extract_store_id_from_webhook(data)
        if store_id is None:
            return False

        order_id = str(order_data.get('id'))
        if not order_id:
            return False

        reference_id = order_data.get('reference_id')
        
        # التحقق إذا الطلب موجود مسبقاً
        existing_order = SallaOrder.query.get(order_id)
        if existing_order:
            if not existing_order.full_order_data:
                existing_order.full_order_data = order_data

            if not existing_order.reference_id and reference_id:
                existing_order.reference_id = str(reference_id)
            
            db.session.commit()

            existing_address = OrderAddress.query.filter_by(order_id=order_id).first()
            if not existing_address:
                address_info = extract_order_address(order_data)
                if address_info:
                    new_address = OrderAddress(order_id=order_id, **address_info)
                    db.session.add(new_address)
                    db.session.commit()
            return True

        # ربط الطلب بالمستخدم
        user = User.query.filter_by(store_id=store_id).first()
        if not user:
            user_with_salla = User.query.filter(
                User._salla_access_token.isnot(None),
                User.store_id.isnot(None)
            ).first()
            if not user_with_salla:
                return False
            store_id = user_with_salla.store_id

        # معالجة تاريخ الإنشاء
        created_at = None
        date_info = order_data.get('date', {})
        if date_info and 'date' in date_info:
            try:
                date_str = date_info['date'].split('.')[0]
                created_at = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
            except Exception:
                created_at = datetime.utcnow()

        # المبلغ والعملة
        total_info = order_data.get('total') or order_data.get('amounts', {}).get('total', {})
        total_amount = float(total_info.get('amount', 0))
        currency = total_info.get('currency', 'SAR')

        # بيانات العميل
        customer = order_data.get('customer', {})
        customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        if not customer_name:
            customer_name = order_data.get('customer_name', 'عميل غير معروف')

        # تحديد حالة الطلب
        status_id = None
        status_info = order_data.get('status', {})
        if status_info:
            status_slug = status_info.get('slug', '').lower().replace('-', '_')
            if not status_slug and status_info.get('name'):
                status_slug = status_info['name'].lower().replace(' ', '_')
            status = OrderStatus.query.filter_by(slug=status_slug, store_id=store_id).first()
            if status:
                status_id = status.id

        if not status_id:
            default_status = OrderStatus.query.filter_by(
                store_id=store_id, is_active=True
            ).order_by(OrderStatus.sort).first()
            if default_status:
                status_id = default_status.id

        # إنشاء الطلب الجديد
        new_order = SallaOrder(
            id=order_id,
            store_id=store_id,
            customer_name=customer_name,
            created_at=created_at or datetime.utcnow(),
            total_amount=total_amount,
            currency=currency,
            payment_method=order_data.get('payment_method', ''),
            raw_data=json.dumps(order_data, ensure_ascii=False),
            full_order_data=order_data,
            status_id=status_id,
            reference_id=str(reference_id) if reference_id else None
        )
        db.session.add(new_order)
        db.session.flush()

        # إضافة العنوان
        address_info = extract_order_address(order_data)
        if address_info:
            new_address = OrderAddress(order_id=order_id, **address_info)
            db.session.add(new_address)

        db.session.commit()
        return True

    except Exception as e:
        db.session.rollback()
        logger.error(f"خطأ في إنشاء الطلب من Webhook: {str(e)}", exc_info=True)
        return False

def update_order_items_from_webhook(order, order_data):
    """تحديث المنتجات داخل full_order_data"""
    try:
        old_items = order.full_order_data.get('items', []) if order.full_order_data else []
        new_items = order_data.get('items', [])

        old_ids = {str(i.get('id')) for i in old_items if i.get('id')}
        new_ids = {str(i.get('id')) for i in new_items if i.get('id')}

        removed_ids = old_ids - new_ids
        added_ids = new_ids - old_ids

        order.full_order_data = order_data
        order.raw_data = json.dumps(order_data, ensure_ascii=False)

        # تسجيل المنتجات المحذوفة
        for pid in removed_ids:
            rec = OrderProductStatus.query.filter_by(order_id=order.id, product_id=pid).first()
            if rec:
                rec.status = 'removed'
                rec.notes = (rec.notes or '') + ' | removed via webhook'
                rec.updated_at = datetime.utcnow()
            else:
                db.session.add(OrderProductStatus(
                    order_id=order.id,
                    product_id=pid,
                    status='removed',
                    notes='Removed via webhook',
                    updated_at=datetime.utcnow()
                ))

        # تسجيل المنتجات المضافة
        for pid in added_ids:
            db.session.add(OrderProductStatus(
                order_id=order.id,
                product_id=pid,
                status='added',
                notes='Added via webhook',
                updated_at=datetime.utcnow()
            ))

        db.session.commit()
        return True

    except Exception as e:
        db.session.rollback()
        logger.error(f"خطأ في تحديث المنتجات للطلب {order.id}: {str(e)}")
        return False

def update_order_address(order_id, order_data):
    """تحديث عنوان الطلب في قاعدة البيانات"""
    try:
        address_info = extract_order_address(order_data)
        if not address_info:
            return False
        
        existing_address = OrderAddress.query.filter_by(order_id=str(order_id)).first()
        
        if existing_address:
            existing_address.name = address_info.get('name', '')
            existing_address.phone = address_info.get('phone', '')
            existing_address.country = address_info.get('country', '')
            existing_address.city = address_info.get('city', '')
            existing_address.full_address = address_info.get('full_address', '')
            existing_address.address_type = address_info.get('address_type', 'customer')
        else:
            new_address = OrderAddress(
                order_id=str(order_id),
                **address_info
            )
            db.session.add(new_address)
        
        db.session.commit()
        return True
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"خطأ في تحديث العنوان: {str(e)}", exc_info=True)
        return False

@orders_bp.route('/webhook/orders', methods=['POST'])
def order_status_webhook():
    """معالجة Webhook الطلبات"""
    try:
        webhook_version = request.headers.get('X-Salla-Webhook-Version', '1')
        security_strategy = request.headers.get('X-Salla-Security-Strategy', 'signature')
        
        # التحقق من الأمان
        if security_strategy == 'signature' and Config.WEBHOOK_SECRET:
            signature = request.headers.get('X-Salla-Signature')
            raw_body = request.data
            
            expected_sig = hmac.new(
                Config.WEBHOOK_SECRET.encode(),
                raw_body,
                hashlib.sha256
            ).hexdigest()
            
            if not hmac.compare_digest(signature, expected_sig):
                return jsonify({'success': False, 'error': 'توقيع غير صحيح'}), 403
        
        elif security_strategy == 'token':
            token = request.headers.get('Authorization')
            if not token or token != f"Bearer {Config.WEBHOOK_SECRET}":
                return jsonify({'success': False, 'error': 'توكن غير صحيح'}), 403

        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'لا يوجد بيانات'}), 400

        if webhook_version == '2':
            event = data.get('event')
            order_data = data.get('data', {})
            merchant_id = data.get('merchant')
            
            if merchant_id is None:
                merchant_id = order_data.get('merchant') or order_data.get('store_id')
                if merchant_id is None:
                    return jsonify({'success': False, 'error': 'لا يوجد معرف متجر'}), 400
        else:
            event = data.get('event')
            order_data = data.get('data', {})
            merchant_id = order_data.get('merchant_id')

        # معالجة الأحداث
        if event == 'order.created' and order_data:
            success = handle_order_creation(data if webhook_version == '2' else order_data, webhook_version)
            status_code = 200 if success else 500
            message = 'تم إنشاء الطلب بنجاح' if success else 'فشل في إنشاء الطلب'
            return jsonify({'success': success, 'message': message}), status_code

        elif event in ['order.status.updated', 'order.updated'] and order_data:
            order_id = str(order_data.get('id'))
            order = SallaOrder.query.get(order_id)

            if not order:
                return jsonify({'success': False, 'error': 'الطلب غير موجود'}), 404

            status_updated = False
            store_id = order.store_id
            
            # تحديث حالة الطلب
            if event in ['order.status.updated', 'order.updated']:
                status_data = order_data.get('status', {}) or order_data.get('current_status', {})
                if status_data:
                    status_slug = status_data.get('slug', '').lower().replace('-', '_')
                    if not status_slug and status_data.get('name'):
                        status_slug = status_data['name'].lower().replace(' ', '_')
                    status = OrderStatus.query.filter_by(slug=status_slug, store_id=order.store_id).first()
                    if status:
                        order.status_id = status.id
                        status_updated = True
                        
                        # التحقق وإزالة حالة "متأخر" إذا أصبح الطلب مكتملاً
                        handle_order_completion(store_id, order_id, status_slug)

            # تحديث بيانات إضافية في order.updated
            if event == 'order.updated':
                if 'payment_method' in order_data:
                    order.payment_method = order_data.get('payment_method')
                            
                update_order_items_from_webhook(order, order_data)
                update_order_address(order_id, order_data)

            if status_updated:
                db.session.commit()

        return jsonify({'success': True, 'message': 'تم استقبال البيانات بنجاح'}), 200

    except Exception as e:
        logger.error(f'خطأ في معالجة webhook: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db.session.close()