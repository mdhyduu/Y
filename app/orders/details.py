import json
import logging
from math import ceil
from datetime import datetime, timedelta
from flask import (render_template, request, flash, redirect, url_for, jsonify, 
                   make_response, current_app)
import requests
from flask_wtf.csrf import CSRFProtect, CSRFError
from sqlalchemy import nullslast, or_, and_, func
from app.scheduler_tasks import handle_order_completion
from weasyprint import HTML
import traceback
from sqlalchemy.orm import selectinload
from . import orders_bp
from app.models import (db, SallaOrder, CustomOrder, OrderStatus,User, Employee, 
                     OrderAssignment, EmployeeCustomStatus, OrderStatusNote, 
                     OrderEmployeeStatus, OrderProductStatus, CustomNoteStatus, OrderAddress, SallaStatusChange)
from app.utils import get_user_from_cookies, process_order_data, format_date,  humanize_time
from app.token_utils import refresh_salla_token
from app.config import Config
from flask import send_file
from io import BytesIO
import logging
from concurrent import futures
logger = logging.getLogger('salla_app')
@orders_bp.route('/')
def get_cipher():
    key = base64.urlsafe_b64encode(Config.SECRET_KEY[:32].encode().ljust(32, b'0'))
    return Fernet(key)

# دوال التشفير وفك التشفير
def encrypt_data(data):
    """تشفير البيانات النصية"""
    if not data:
        return data
    try:
        cipher = get_cipher()
        return cipher.encrypt(data.encode()).decode()
    except Exception as e:
        logger.error(f"خطأ في تشفير البيانات: {str(e)}")
        return data

def decrypt_data(encrypted_data):
    """فك تشفير البيانات"""
    if not encrypted_data:
        return encrypted_data
    try:
        cipher = get_cipher()
        return cipher.decrypt(encrypted_data.encode()).decode()
    except Exception as e:
        logger.error(f"خطأ في فك تشفير البيانات: {str(e)}")
        return encrypted_data
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
    status_filter = request.args.get('status', '')
    employee_filter = request.args.get('employee', '')
    order_statuses = OrderStatus.query.filter_by(store_id=user.store_id).order_by(OrderStatus.sort).all()
    custom_status_filter = request.args.get('custom_status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    search_query = request.args.get('search', '')
    
    if page < 1: 
        page = 1
    if per_page not in [10, 25, 50, 100, 125, 150, 200, 250, 300]: 
        per_page = 25
    
    is_general_employee = False
    is_reviewer = False
    is_delivery_personnel = False
    
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
        is_delivery_personnel = employee.role in ['delivery_manager', 'delivery']
    
    try:
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
            from sqlalchemy import exists
            assignment_exists = exists().where(
                and_(
                    OrderAssignment.order_id == SallaOrder.id,
                    OrderAssignment.employee_id == employee.id
                )
            )
            orders_query = orders_query.filter(assignment_exists)
        
        if status_filter in ['late', 'missing', 'not_shipped', 'refunded']:
            orders_query = orders_query.join(
                OrderStatusNote, 
                OrderStatusNote.order_id == SallaOrder.id
            ).filter(
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
            # البحث عن أرقام طلبات متعددة مفصولة بفواصل
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
        
        custom_statuses = []
        if is_reviewer:
            custom_statuses = EmployeeCustomStatus.query.join(Employee).filter(
                Employee.store_id == user.store_id
            ).all()
        elif employee:
            custom_statuses = EmployeeCustomStatus.query.filter_by(employee_id=employee.id).all()
        
        orders_query = orders_query.order_by(nullslast(db.desc(SallaOrder.created_at)))
        pagination_obj = orders_query.paginate(page=page, per_page=per_page, error_out=False)
        orders = pagination_obj.items
        
        processed_orders = []
        
        for order in orders:
            # استخدام full_order_data فقط
            order_data = order.full_order_data or {}
            reference_id = order_data.get('reference_id', order.id)
            status_name = order.status.name if order.status else 'غير محدد'
            status_slug = order.status.slug if order.status else 'unknown'
            
            last_note = OrderStatusNote.query.filter_by(order_id=order.id).order_by(OrderStatusNote.created_at.desc()).first()
            last_emp_status = OrderEmployeeStatus.query.filter_by(order_id=order.id).order_by(OrderEmployeeStatus.created_at.desc()).first()
            
            # استخدام payment_method من full_order_data
            payment_method = order_data.get('payment_method', '')
            payment_method_name = get_payment_method_name(payment_method)
            
            order_address = OrderAddress.query.filter_by(order_id=order.id).first()
            order_city = order_address.city if order_address else 'غير محدد'
            
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
                'employee_statuses': [last_emp_status] if last_emp_status else [],
                'status_notes': [last_note] if last_note else [],
                'payment_method': payment_method,
                'payment_method_name': payment_method_name,
                'city': order_city,
                'order_data': order_data
            }
                
            processed_orders.append(processed_order)
        
        employees = []
        if is_reviewer:
            employees = Employee.query.filter_by(store_id=user.store_id, is_active=True).all()
        
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
            'search': search_query
        }
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return render_template('orders_partial.html', 
                                orders=processed_orders, 
                                employees=employees,
                                custom_statuses=custom_statuses,
                                pagination=pagination,
                                filters=filters,
                                is_reviewer=is_reviewer,
                                is_delivery_personnel=is_delivery_personnel,
                                current_employee=employee)
        
        return render_template('orders.html', 
                            orders=processed_orders, 
                            employees=employees,
                            custom_statuses=custom_statuses,
                            pagination=pagination,
                            filters=filters,
                            order_statuses=order_statuses,  
                            is_reviewer=is_reviewer,
                            is_delivery_personnel=is_delivery_personnel,
                            current_employee=employee)
    
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
        is_reviewer = False
        if request.cookies.get('is_admin') == 'true':
            is_reviewer = True
        elif current_employee and current_employee.role in ['reviewer', 'manager']:
            is_reviewer = True

        order = SallaOrder.query.filter_by(id=str(order_id), store_id=user.store_id).first()
        
        order_data = None
        items_data = []
        
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

        processed_order = process_order_data(order_id, items_data)
        
        order_address = OrderAddress.query.filter_by(order_id=str(order_id)).first()
        
        if order_address:
            decrypted_name = decrypt_data(order_address.name) if order_address.name else ''
            decrypted_phone = decrypt_data(order_address.phone) if order_address.phone else ''

        notes = order_data.get('notes', '')
        payment_method = order_data.get('payment_method', {})
        
        if isinstance(payment_method, dict):
            payment_method_name = payment_method.get('name', 'غير محدد')
        else:
            payment_method_name = str(payment_method) if payment_method else 'غير محدد'

        processed_items = []
        for item in items_data:
            product_notes = item.get('notes', '')
            
            processed_item = {
                'id': item.get('id'),
                'name': item.get('name', ''),
                'quantity': item.get('quantity', 1),
                'price': item.get('amounts', {}).get('total', {}).get('amount', 0),
                'currency': item.get('amounts', {}).get('total', {}).get('currency', 'SAR'),
                'notes': product_notes,
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
        order_statuses = OrderStatus.query.filter_by(store_id=user.store_id).order_by(OrderStatus.sort).all()  
        
        order = SallaOrder.query.filter_by(id=str(order_id), store_id=user.store_id).first()
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

from weasyprint import HTML
from flask import send_file
from io import BytesIO
import tempfile
import os

@orders_bp.route('/<order_id>/shipping_policy/<int:shipment_index>')
def download_shipping_policy(order_id, shipment_index=0):
    """تحميل البوليصة كملف PDF مباشرة بدون أي إضافات"""
    user, current_employee = get_user_from_cookies()
    
    if not user:
        flash("الرجاء تسجيل الدخول أولاً", "error")
        return redirect(url_for('user_auth.login'))

    try:
        # جلب بيانات الطلب
        order = SallaOrder.query.filter_by(id=str(order_id), store_id=user.store_id).first()
        if not order:
            flash('الطلب غير موجود', 'error')
            return redirect(url_for('orders.index'))

        # استخراج معلومات الشحن
        order_data = order.full_order_data or {}
        shipping_info = extract_shipping_info(order_data)
        
        # التحقق من وجود الشحنة المطلوبة
        shipment_details = shipping_info.get('shipment_details', [])
        if shipment_index >= len(shipment_details):
            flash('بيانات الشحن غير موجودة', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))

        shipment = shipment_details[shipment_index]
        
        # التحقق من وجود بوليصة شحن
        if not shipment.get('has_shipping_policy') or not shipment.get('shipping_policy_url'):
            flash('لا توجد بوليصة شحن متاحة', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))

        # استخدام reference_id كاسم للملف
        reference_id = order.reference_id or order.id
        filename = f"بوليصة_شحن_{reference_id}.pdf"

        # جلب ملف البوليصة مباشرة
        access_token = ensure_valid_access_token(user)
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/pdf,application/octet-stream'
        }
        
        policy_url = shipment['shipping_policy_url']
        response = requests.get(policy_url, headers=headers, timeout=30, stream=True)
        
        if response.status_code == 200:
            # التحقق من أن الملف هو PDF
            content_type = response.headers.get('content-type', '')
            if 'pdf' in content_type.lower() or policy_url.lower().endswith('.pdf'):
                pdf_data = BytesIO(response.content)
                return send_file(
                    pdf_data,
                    as_attachment=True,
                    download_name=filename,
                    mimetype='application/pdf'
                )
            else:
                # إذا لم يكن PDF، نعيد الملف كما هو بدون تحويل
                file_data = BytesIO(response.content)
                return send_file(
                    file_data,
                    as_attachment=True,
                    download_name=filename,
                    mimetype=content_type
                )
        else:
            flash('فشل في تحميل البوليصة', 'error')
            return redirect(url_for('orders.order_details', order_id=order_id))

    except Exception as e:
        error_msg = f"خطأ في تحميل البوليصة: {str(e)}"
        flash(error_msg, "error")
        logger.exception(f"Error downloading shipping policy: {str(e)}")
        return redirect(url_for('orders.order_details', order_id=order_id))
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
            
            # إذا لم يكن هناك رابط مباشر، نبحث في أماكن أخرى
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
def ensure_valid_access_token(user):
    """التأكد من وجود توكن وصول صالح مع معالجة الأخطاء المحسنة"""
    try:
        if not user:
            logger.error("لا يوجد مستخدم للمصادقة")
            return None
            
        if user.tokens_are_valid:
            return user.salla_access_token
        
        from app.token_utils import refresh_salla_token
        success = refresh_salla_token(user)
        
        if success and user.tokens_are_valid:
            db.session.refresh(user)
            return user.salla_access_token
        else:
            if user.salla_access_token:
                return user.salla_access_token
            return None
            
    except Exception as e:
        logger.error(f"خطأ في التأكد من صلاحية التوكن: {str(e)}")
        if user and user.salla_access_token:
            return user.salla_access_token
        return None

def fetch_order_data_from_api(user, order_id):
    """جلب بيانات الطلب من API مع تضمين العناصر في البيانات الرئيسية"""
    try:
        access_token = ensure_valid_access_token(user)
        if not access_token:
            logger.error("لا يمكن الحصول على توكن وصول صالح")
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
            logger.error(f"خطأ في جلب بيانات الطلب من API: {order_response.status_code}")
            return None, []
        
        order_data = order_response.json().get('data', {})
        
        items_data = fetch_order_items_from_api(user, order_id)
        
        if items_data:
            order_data['items'] = items_data
        
        return order_data, items_data
        
    except Exception as e:
        logger.error(f"خطأ في جلب بيانات الطلب من API: {str(e)}")
        return None, []

def fetch_order_items_from_api(user, order_id):
    """جلب عناصر الطلب من API مع معالجة الأخطاء المحسنة"""
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
            items = response.json().get('data', [])
            return items
        else:
            logger.error(f"خطأ في جلب العناصر من API: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        logger.error(f"خطأ في جلب العناصر من API: {str(e)}")
        return []

def create_order_from_api_data(user, order_data, items_data=None):
    """إنشاء طلب جديد في قاعدة البيانات من بيانات API مع تضمين العناصر"""
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
        
        # استخدام full_order_data فقط بدون raw_data
        new_order = SallaOrder(
            id=order_id,
            store_id=user.store_id,
            customer_name=encrypt_data(customer_name),
            created_at=created_at or datetime.utcnow(),
            total_amount=total_amount,
            currency=currency,
            payment_method=order_data.get('payment_method', ''),
            full_order_data=order_data  # البيانات الكاملة فقط
        )
        
        db.session.add(new_order)
        
        address_info = extract_order_address(order_data)
        if address_info:
            address_info['name'] = encrypt_data(address_info.get('name', ''))
            address_info['phone'] = encrypt_data(address_info.get('phone', ''))
            
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
        custom_note_statuses = CustomNoteStatus.query.filter_by(
            store_id=store_id
        ).all()
        
        status_notes = OrderStatusNote.query.filter_by(
            order_id=order_id_str
        ).options(
            selectinload(OrderStatusNote.admin),
            selectinload(OrderStatusNote.employee),
            selectinload(OrderStatusNote.custom_status)
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
import hmac
import hashlib

def extract_store_id_from_webhook(webhook_data):
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
