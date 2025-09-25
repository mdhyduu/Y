# orders/routes.py
import json
import logging
from math import ceil
from datetime import datetime, timedelta
from flask import (render_template, request, flash, redirect, url_for, jsonify, 
                   make_response, current_app)
import requests
from sqlalchemy import nullslast, or_, and_, func
from sqlalchemy.orm import selectinload
from . import orders_bp
from app.models import (db, SallaOrder, CustomOrder, OrderStatus,User, Employee, 
                     OrderAssignment, EmployeeCustomStatus, OrderStatusNote, 
                     OrderEmployeeStatus, OrderProductStatus, CustomNoteStatus, OrderAddress)
from app.utils import get_user_from_cookies, process_order_data, format_date,  humanize_time
from app.token_utils import refresh_salla_token
from app.config import Config
from flask import send_file
from io import BytesIO
import pandas as pd
from concurrent import futures

import logging

# إعداد المسجل للإنتاج
logger = logging.getLogger('salla_app')

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
    status_filter = request.args.get('status', '')
    employee_filter = request.args.get('employee', '')
    order_statuses = OrderStatus.query.filter_by(store_id=user.store_id).order_by(OrderStatus.sort).all()
    custom_status_filter = request.args.get('custom_status', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    search_query = request.args.get('search', '')
    order_type = request.args.get('order_type', 'all')
    
    if page < 1: 
        page = 1
    if per_page not in [10, 25, 50, 100]: 
        per_page = 25
    
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
        salla_query = SallaOrder.query.filter_by(store_id=user.store_id).options(
            selectinload(SallaOrder.status),
            selectinload(SallaOrder.assignments).selectinload(OrderAssignment.employee)
        )
        
        custom_query = CustomOrder.query.filter_by(store_id=user.store_id).options(
            selectinload(CustomOrder.status),
            selectinload(CustomOrder.assignments).selectinload(OrderAssignment.employee)
        )
        
        if not is_reviewer and employee:
            salla_query = salla_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee.id)
            custom_query = custom_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee.id)
        
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
        elif status_filter:
            if order_type in ['all', 'salla']:
                salla_query = salla_query.join(SallaOrder.status).filter(OrderStatus.slug == status_filter)
            if order_type in ['all', 'custom']:
                custom_query = custom_query.join(CustomOrder.status).filter(
                    OrderStatus.slug == status_filter
                )
        
        if employee_filter:
            if order_type in ['all', 'salla']:
                salla_query = salla_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)
            if order_type in ['all', 'custom']:
                custom_query = custom_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)
        
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
        
        custom_statuses = []
        if is_reviewer:
            custom_statuses = EmployeeCustomStatus.query.join(Employee).filter(
                Employee.store_id == user.store_id
            ).all()
        elif employee:
            custom_statuses = EmployeeCustomStatus.query.filter_by(employee_id=employee.id).all()
        
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
        
        processed_orders = []
        
        for order in orders:
            # التحقق إذا كان الـ ID صالحاً للرابط (ليس None ويمكن تحويله إلى int)
            has_valid_link = True
            try:
                if order.id is None:
                    has_valid_link = False
                elif isinstance(order.id, str) and (order.id.lower() == 'none' or not order.id.isdigit()):
                    has_valid_link = False
                else:
                    # محاولة تحويل إلى int للتحقق
                    int(order.id)
            except (ValueError, TypeError):
                has_valid_link = False
            
            if isinstance(order, SallaOrder):
                raw_data = json.loads(order.raw_data) if order.raw_data else {}
                reference_id = raw_data.get('reference_id', order.id)
                status_name = order.status.name if order.status else 'غير محدد'
                status_slug = order.status.slug if order.status else 'unknown'
                
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
                    'employee_statuses': [last_emp_status] if last_emp_status else [],
                    'status_notes': [last_note] if last_note else [],
                    'has_valid_link': has_valid_link  # إضافة هذا الحقل الجديد
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
                    'status_notes': [last_note] if last_note else [],
                    'has_valid_link': has_valid_link  # إضافة هذا الحقل الجديد
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
            'search': search_query,
            'order_type': order_type
        }
        
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

import copy

@orders_bp.route('/<int:order_id>')
def order_details(order_id):
    user, current_employee = get_user_from_cookies()
    
    # إصلاح المشكلة: إعادة تحميل employee مع العلاقات
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

        def fetch_order_data():
            order_response = make_salla_api_request(f"{Config.SALLA_ORDERS_API}/{order_id}")
            if not isinstance(order_response, requests.Response):
                return order_response
            return order_response.json().get('data', {})

        def fetch_order_items():
            items_response = make_salla_api_request(
                f"{Config.SALLA_BASE_URL}/orders/items",
                params={'order_id': order_id, 'include': 'images'}
            )
            if not isinstance(items_response, requests.Response):
                return items_response
            return items_response.json().get('data', [])

        def fetch_db_data(app_context, store_id, order_id_str):
            with app_context:
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

        app_context = current_app.app_context()
        
        with futures.ThreadPoolExecutor() as executor:
            order_future = executor.submit(fetch_order_data)
            items_future = executor.submit(fetch_order_items)
            db_future = executor.submit(fetch_db_data, app_context, user.store_id, str(order_id))
            
            order_data = order_future.result()
            items_data = items_future.result()
            db_data = db_future.result()

        processed_order = process_order_data(order_id, items_data)
        
        # جلب العنوان مباشرة من قاعدة البيانات
        order_address = OrderAddress.query.filter_by(order_id=str(order_id)).first()
        print(f"🔍 في order_details - العنوان من DB: {order_address}")
        
        # استخدام البيانات المحفوظة فقط - إزالة جزء API
        if order_address:
            print("✅ استخدام العنوان المحفوظ في قاعدة البيانات")
            full_address = order_address.full_address or 'لم يتم تحديد العنوان'
            receiver_info = {
                'name': order_address.name or '',
                'phone': order_address.phone or '',

            }
        else:
            print("❌ لا يوجد عنوان محفوظ")
            full_address = 'لم يتم تحديد العنوان'
            receiver_info = {
                'name': '',
                'phone': '',
            
            }

        processed_order.update({
            'id': order_id,
            'reference_id': order_data.get('reference_id') or 'غير متوفر',

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
            }
        })

        return render_template('order_details.html', 
            order=processed_order,
            order_address=order_address,  # تمرير العنوان المحفوظ للقالب
            status_notes=db_data['status_notes'],
            employee_statuses=db_data['employee_statuses'],
            custom_note_statuses=db_data['custom_note_statuses'],
            current_employee=current_employee,
            is_reviewer=is_reviewer,
            product_statuses=db_data['product_statuses']
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
        
from flask_wtf.csrf import CSRFProtect, CSRFError

csrf = CSRFProtect()

def handle_order_creation(data, webhook_version='2'):
    try:
        print(f"🔔 بدء معالجة ويب هوك - الإصدار: {webhook_version}")
        
        # --- استخراج البيانات الأساسية من Webhook ---
        if webhook_version == '2':
            order_data = data.get('data', {})
            merchant_id = data.get('merchant')
        else:
            order_data = data
            merchant_id = data.get('merchant_id')

        print(f"📦 بيانات الطلب المستلمة: {order_data.get('id')}")
        
        store_id = extract_store_id_from_webhook(data)
        print(f"🏪 معرف المتجر المستخرج: {store_id}")
        
        if store_id is None:
            print("❌ فشل في استخراج معرف المتجر")
            return False

        order_id = str(order_data.get('id'))
        print(f"🆔 معرف الطلب: {order_id}")
        
        if not order_id:
            print("❌ لا يوجد معرف طلب")
            return False

        # --- التحقق إذا الطلب موجود مسبقاً ---
        existing_order = SallaOrder.query.get(order_id)
        if existing_order:
            print(f"✅ الطلب موجود مسبقاً في قاعدة البيانات")
            
            # التصحيح: التحقق من وجود العنوان في جدول OrderAddress
            existing_address = OrderAddress.query.filter_by(order_id=order_id).first()
            print(f"📫 العنوان الموجود: {existing_address}")
            
            if not existing_address:  # إذا لم يكن هناك عنوان نضيفه
                print("📝 لم يتم العثور على عنوان، جاري استخراج العنوان من البيانات...")
                address_info = extract_order_address(order_data)
                print(f"📍 بيانات العنوان المستخرجة: {address_info}")
                
                if address_info:  # التأكد من وجود بيانات العنوان
                    # تشفير البيانات الحساسة قبل الحفظ
                    address_info['name'] = encrypt_data(address_info.get('name', ''))
                    address_info['phone'] = encrypt_data(address_info.get('phone', ''))
                    
                    new_address = OrderAddress(
                        order_id=order_id,
                        **address_info
                    )
                    db.session.add(new_address)
                    db.session.commit()
                    print("✅ تم حفظ العنوان الجديد بنجاح")
                else:
                    print("⚠️ لا توجد بيانات عنوان لاحفظها")
            else:
                print("✅ العنوان موجود مسبقاً، لا حاجة للإضافة")
            return True

        print("🆕 طلب جديد، جاري إنشاؤه...")

        # --- ربط الطلب بالمستخدم (store owner) ---
        user = User.query.filter_by(store_id=store_id).first()
        if not user:
            print("🔍 البحث عن مستخدم بديل...")
            user_with_salla = User.query.filter(
                User._salla_access_token.isnot(None),
                User.store_id.isnot(None)
            ).first()
            if not user_with_salla:
                print("❌ لم يتم العثور على أي مستخدم")
                return False
            store_id = user_with_salla.store_id
            print(f"✅ تم العثور على مستخدم بديل: {store_id}")

        # --- معالجة تاريخ الإنشاء ---
        created_at = None
        date_info = order_data.get('date', {})
        if date_info and 'date' in date_info:
            try:
                date_str = date_info['date'].split('.')[0]
                created_at = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                print(f"📅 تاريخ الإنشاء: {created_at}")
            except Exception:
                created_at = datetime.utcnow()
                print("⚠️ استخدام التاريخ الحالي بسبب خطأ في التحليل")

        # --- المبلغ والعملة ---
        total_info = order_data.get('total') or order_data.get('amounts', {}).get('total', {})
        total_amount = float(total_info.get('amount', 0))
        currency = total_info.get('currency', 'SAR')
        print(f"💰 المبلغ: {total_amount} {currency}")

        # --- بيانات العميل ---
        customer = order_data.get('customer', {})
        customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        if not customer_name:
            customer_name = order_data.get('customer_name', 'عميل غير معروف')
        print(f"👤 اسم العميل: {customer_name}")

        # --- تشفير اسم العميل قبل الحفظ ---
        encrypted_customer_name = encrypt_data(customer_name)

        # --- تحديد حالة الطلب ---
        status_id = None
        status_info = order_data.get('status', {})
        if status_info:
            status_slug = status_info.get('slug', '').lower().replace('-', '_')
            if not status_slug and status_info.get('name'):
                status_slug = status_info['name'].lower().replace(' ', '_')

            status = OrderStatus.query.filter_by(
                slug=status_slug,
                store_id=store_id
            ).first()
            if status:
                status_id = status.id
                print(f"🏷️ حالة الطلب: {status_slug} (ID: {status_id})")

        if not status_id:
            default_status = OrderStatus.query.filter_by(
                store_id=store_id,
                is_active=True
            ).order_by(OrderStatus.sort).first()
            if default_status:
                status_id = default_status.id
                print(f"🔧 استخدام الحالة الافتراضية: {status_id}")

        # --- إنشاء الطلب ---
        new_order = SallaOrder(
            id=order_id,
            store_id=store_id,
            customer_name=encrypted_customer_name,  # حفظ الاسم مشفر
            created_at=created_at or datetime.utcnow(),
            total_amount=total_amount,
            currency=currency,
            payment_method=order_data.get('payment_method', ''),
            raw_data=json.dumps(order_data, ensure_ascii=False),
            status_id=status_id
        )
        db.session.add(new_order)
        db.session.flush()
        print("✅ تم إنشاء الطلب في قاعدة البيانات")

        # --- إضافة العنوان (مع التشفير) ---
        address_info = extract_order_address(order_data)
        print(f"📍 بيانات العنوان المستخرجة للطلب الجديد: {address_info}")
        
        if address_info:
            # تشفير البيانات الحساسة قبل الحفظ
            address_info['name'] = encrypt_data(address_info.get('name', ''))
            address_info['phone'] = encrypt_data(address_info.get('phone', ''))
            
            new_address = OrderAddress(
                order_id=order_id,
                **address_info
            )
            db.session.add(new_address)
            print("✅ تم إضافة العنوان للطلب الجديد")
        else:
            print("⚠️ لا توجد بيانات عنوان للطلب الجديد")

        # --- حفظ كل شيء ---
        db.session.commit()
        print("🎉 تم حفظ الطلب والعنوان بنجاح")
        return True

    except Exception as e:
        db.session.rollback()
        error_msg = f"❌ خطأ في إنشاء الطلب من Webhook: {str(e)}"
        print(error_msg)
        logger.error(error_msg, exc_info=True)
        return False
@orders_bp.route('/webhook/orders', methods=['POST'])
@csrf.exempt
def order_status_webhook():
    setattr(request, "_dont_enforce_csrf", True)

    try:
        webhook_version = request.headers.get('X-Salla-Webhook-Version', '1')
        security_strategy = request.headers.get('X-Salla-Security-Strategy', 'signature')
        
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
            webhook_data = data.get('data', {})
            merchant_id = data.get('merchant')
            
            if merchant_id is None:
                merchant_id = webhook_data.get('merchant') or webhook_data.get('store_id')
                if merchant_id is None:
                    return jsonify({'success': False, 'error': 'لا يوجد معرف متجر'}), 400
            
            order_data = webhook_data
        else:
            event = data.get('event')
            order_data = data.get('data', {})
            merchant_id = order_data.get('merchant_id')

        if event == 'order.created' and order_data:
            success = handle_order_creation(data if webhook_version == '2' else order_data, webhook_version)
            if success:
                return jsonify({'success': True, 'message': 'تم إنشاء الطلب بنجاح'}), 200
            else:
                return jsonify({'success': False, 'error': 'فشل في إنشاء الطلب'}), 500
            
        elif event in ['order.status.updated', 'order.updated'] and order_data:
            order_id = str(order_data.get('id'))
            
            # تحديث حالة الطلب (الكود الحالي)
            if event == 'order.status.updated':
                status_data = order_data.get('status', {})
            else:
                status_data = order_data.get('status', {}) or order_data.get('current_status', {})
            
            if order_id and status_data:
                order = SallaOrder.query.get(order_id)
                if order:
                    status_slug = status_data.get('slug', '').lower().replace('-', '_')
                    if not status_slug and status_data.get('name'):
                        status_slug = status_data['name'].lower().replace(' ', '_')
                    
                    status = OrderStatus.query.filter_by(
                        slug=status_slug,
                        store_id=order.store_id
                    ).first()

                    if status:
                        order.status_id = status.id
                        print(f"✅ تم تحديث حالة الطلب {order_id} إلى {status_slug}")

            # ⭐⭐ إضافة تحديث العنوان عند حدث order.updated ⭐⭐
            if event == 'order.updated' and order_data:
                print(f"🔄 معالجة تحديث الطلب والعنوان للطلب {order_id}")
                update_success = update_order_address(order_id, order_data)
                if update_success:
                    print(f"✅ تم تحديث بيانات العنوان للطلب {order_id}")
                else:
                    print(f"⚠️ فشل في تحديث العنوان للطلب {order_id}")

            db.session.commit()

        return jsonify({'success': True, 'message': 'تم استقبال البيانات بنجاح'}), 200

    except Exception as e:
        logger.error(f'خطأ في معالجة webhook: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        db.session.close()
        
def extract_order_address(order_data):
    """
    استخراج بيانات العنوان مع الأولوية للمتسلم
    يرجع: اسم كامل، هاتف، بلد، مدينة، عنوان كامل
    """
    print("🔍 بدء استخراج العنوان من بيانات الطلب...")
    
    shipping_data = order_data.get('shipping', {}) or {}
    customer_data = order_data.get('customer', {}) or {}
    
    print(f"🚚 بيانات الشحن: {shipping_data}")
    print(f"👤 بيانات العميل: {customer_data}")
    
    # الأولوية للمتسلم (receiver)
    receiver_data = shipping_data.get('receiver', {}) or {}
    address_data = shipping_data.get('address') or shipping_data.get('pickup_address', {}) or {}
    
    print(f"📦 بيانات المتسلم: {receiver_data}")
    print(f"🏠 بيانات العنوان: {address_data}")
    
    if receiver_data.get('name') or address_data:
        print("✅ استخدام بيانات المتسلم والعنوان")
        name = receiver_data.get('name', '').strip()
        phone = receiver_data.get('phone') or f"{customer_data.get('mobile_code', '')}{customer_data.get('mobile', '')}"
        country = address_data.get('country', customer_data.get('country', ''))
        city = address_data.get('city', customer_data.get('city', ''))
        full_address = address_data.get('shipping_address', '') or customer_data.get('location', '')
        
        if not name:
            name = customer_data.get('full_name') or f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip()
        
        address_type = 'receiver'
    
    else:
        print("🔍 استخدام بيانات العميل كبديل")
        name = customer_data.get('full_name') or f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip()
        phone = f"{customer_data.get('mobile_code', '')}{customer_data.get('mobile', '')}"
        country = customer_data.get('country', '')
        city = customer_data.get('city', '')
        full_address = customer_data.get('location', '')
        address_type = 'customer'
    
    if not name:
        name = 'عميل غير معروف'
        print("⚠️ استخدام اسم افتراضي: عميل غير معروف")
    
    if not full_address:
        parts = [p for p in [country, city] if p]
        full_address = ' - '.join(parts) if parts else 'لم يتم تحديد العنوان'
        print("⚠️ استخدام عنوان مبني من البلد والمدينة")
    
    result = {
        'name': name,
        'phone': phone,
        'country': country,
        'city': city,
        'full_address': full_address,
        'address_type': address_type
    }
    
    print(f"📋 النتيجة النهائية للعنوان: {result}")
    return result
    
def update_order_address(order_id, order_data):
    """
    تحديث عنوان الطلب في قاعدة البيانات
    """
    try:
        print(f"🔄 محاولة تحديث العنوان للطلب {order_id}")
        
        # استخراج بيانات العنوان من الطلب
        address_info = extract_order_address(order_data)
        print(f"📍 بيانات العنوان المستخرجة للتحديث: {address_info}")
        
        if not address_info:
            print("⚠️ لا توجد بيانات عنوان للتحديث")
            return False
        
        # البحث عن العنوان الحالي في قاعدة البيانات
        existing_address = OrderAddress.query.filter_by(order_id=str(order_id)).first()
        
        if existing_address:
            print("✅ وجود عنوان موجود، جاري التحديث...")
            # تحديث البيانات الحالية
            existing_address.name = encrypt_data(address_info.get('name', ''))
            existing_address.phone = encrypt_data(address_info.get('phone', ''))
            existing_address.country = address_info.get('country', '')
            existing_address.city = address_info.get('city', '')
            existing_address.full_address = address_info.get('full_address', '')
            existing_address.address_type = address_info.get('address_type', 'customer')
        else:
            print("🆕 إنشاء عنوان جديد...")
            # إنشاء سجل جديد إذا لم يكن موجوداً
            new_address = OrderAddress(
                order_id=str(order_id),
                name=encrypt_data(address_info.get('name', '')),
                phone=encrypt_data(address_info.get('phone', '')),
                country=address_info.get('country', ''),
                city=address_info.get('city', ''),
                full_address=address_info.get('full_address', ''),
                address_type=address_info.get('address_type', 'customer')
            )
            db.session.add(new_address)
        
        db.session.commit()
        print("✅ تم تحديث العنوان بنجاح")
        return True
        
    except Exception as e:
        db.session.rollback()
        error_msg = f"❌ خطأ في تحديث العنوان: {str(e)}"
        print(error_msg)
        logger.error(error_msg, exc_info=True)
        return False
