# orders.py
from flask_wtf.csrf import CSRFProtect
csrf = CSRFProtect() 
from flask import Blueprint, render_template, flash, redirect, url_for, request, send_from_directory, current_app, jsonify, make_response, send_file
import requests
from sqlalchemy import nullslast
from .models import (
    db, User, Employee, Department, EmployeePermission, 
    Product, OrderDelivery, SallaOrder, CustomOrder, OrderAssignment,
    OrderStatusNote, EmployeeCustomStatus, OrderEmployeeStatus, CustomNoteStatus, OrderStatus
)
from werkzeug.utils import secure_filename
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

def sync_order_statuses_internal(user, access_token, store_id):
    """دالة مساعدة لمزامنة حالات الطلبات (يمكن استدعاؤها داخلياً)"""
    try:
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json'
        }
        
        current_app.logger.info(f"بدء مزامنة حالات الطلبات للمتجر {store_id}")
        
        response = requests.get(
            f"{Config.SALLA_API_BASE_URL}/orders/statuses",
            headers=headers,
            timeout=30
        )
        
        if response.status_code != 200:
            error_msg = f"خطأ في استجابة سلة: {response.status_code} - {response.text}"
            current_app.logger.error(error_msg)
            return False, f"فشل في جلب حالات الطلبات من سلة: {response.text[:200] if response.text else ''}"
        
        data = response.json()
        if 'data' not in data:
            error_msg = "استجابة غير متوقعة من سلة: هيكل البيانات غير مطابق للمواصفات"
            current_app.logger.error(error_msg)
            return False, error_msg
        
        statuses = data['data']
        current_app.logger.info(f"تم جلب {len(statuses)} حالة طلب للمزامنة")
        
        new_count, updated_count = 0, 0
        
        for status_data in statuses:
            try:
                status_id = str(status_data.get('id'))
                if not status_id:
                    continue
                
                # --- Normalize slug ---
                slug = status_data.get('slug')
                if not slug and status_data.get('name'):
                    slug = status_data['name'].lower().replace(' ', '_')
                if slug:
                    slug = slug.strip().lower().replace('-', '_')
                
                # البحث عن الحالة
                existing_status = OrderStatus.query.filter_by(id=status_id, store_id=store_id).first()
                
                if existing_status:
                    existing_status.name = status_data.get('name', '')
                    existing_status.type = status_data.get('type', '')
                    existing_status.slug = slug
                    existing_status.sort = status_data.get('sort', 0)
                    existing_status.message = status_data.get('message', '')
                    existing_status.icon = status_data.get('icon', '')
                    existing_status.is_active = status_data.get('is_active', True)
                    existing_status.store_id = store_id
                    
                    original_data = status_data.get('original', {})
                    if original_data and 'id' in original_data:
                        existing_status.original_id = str(original_data['id'])
                    
                    parent_data = status_data.get('parent', {})
                    if parent_data and 'id' in parent_data:
                        existing_status.parent_id = str(parent_data['id'])
                    
                    updated_count += 1
                else:
                    new_status = OrderStatus(
                        id=status_id,
                        name=status_data.get('name', ''),
                        type=status_data.get('type', ''),
                        slug=slug,
                        sort=status_data.get('sort', 0),
                        message=status_data.get('message', ''),
                        icon=status_data.get('icon', ''),
                        is_active=status_data.get('is_active', True),
                        store_id=store_id
                    )
                    
                    original_data = status_data.get('original', {})
                    if original_data and 'id' in original_data:
                        new_status.original_id = str(original_data['id'])
                    
                    parent_data = status_data.get('parent', {})
                    if parent_data and 'id' in parent_data:
                        new_status.parent_id = str(parent_data['id'])
                    
                    db.session.add(new_status)
                    new_count += 1
                    
            except Exception as e:
                current_app.logger.error(f"خطأ في معالجة الحالة {status_data.get('id', 'unknown')}: {str(e)}")
        
        db.session.commit()
        
        current_app.logger.info(f"تمت مزامنة حالات الطلبات بنجاح: {new_count} جديد، {updated_count} محدث")
        return True, f'تمت مزامنة حالات الطلبات بنجاح: {new_count} حالة جديدة، {updated_count} حالة محدثة'
    
    except requests.exceptions.RequestException as e:
        error_msg = f"خطأ في الاتصال بسلة: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        return False, error_msg
    except Exception as e:
        error_msg = f"خطأ غير متوقع: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        return False, error_msg

@orders_bp.route('/sync_statuses', methods=['POST'])
def sync_order_statuses():
    """مزامنة حالات الطلبات من سلة إلى قاعدة البيانات المحلية"""
    try:
        user, employee = get_user_from_cookies()
        
        if not user:
            response = jsonify({
                'success': False, 
                'error': 'الرجاء تسجيل الدخول أولاً',
                'code': 'UNAUTHORIZED'
            })
            response.set_cookie('user_id', '', expires=0)
            response.set_cookie('is_admin', '', expires=0)
            return response, 401
        
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
        
        if not access_token:
            return jsonify({
                'success': False,
                'error': 'يجب ربط المتجر مع سلة أولاً',
                'code': 'MISSING_ACCESS_TOKEN'
            }), 400
        
        # استخدام الدالة المساعدة للمزامنة
        success, message = sync_order_statuses_internal(user, access_token, store_id)
        
        if success:
            return jsonify({
                'success': True,
                'message': message
            })
        else:
            return jsonify({
                'success': False,
                'error': message,
                'code': 'SYNC_ERROR'
            }), 500
            
    except Exception as e:
        error_msg = f"خطأ غير متوقع: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        return jsonify({
            'success': False,
            'error': error_msg,
            'code': 'INTERNAL_ERROR'
        }), 500

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
        
        # مزامنة حالات الطلبات أولاً لضمان وجود أحدث الحالات
        status_success, status_message = sync_order_statuses_internal(user, access_token, store_id)
        if not status_success:
            return jsonify({
                'success': False,
                'error': f'فشل في مزامنة حالات الطلبات: {status_message}',
                'code': 'STATUS_SYNC_ERROR'
            }), 500
        
        ## التحسين: جلب كل معرفات الحالات (status IDs) الصالحة مرة واحدة لتحسين الأداء
        
        # تحديد وقت آخر مزامنة
        last_sync = getattr(user, 'last_sync', None)
        from_date = (datetime.utcnow() - timedelta(days=7)).strftime('%Y-%m-%d') if not last_sync else last_sync.strftime('%Y-%m-%d')
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json'
        }
        
        current_app.logger.info(f"بدء مزامنة الطلبات للمتجر {store_id} منذ {from_date}")
        
        all_orders = []
        page, total_pages = 1, 1
        token_refreshed = False
        
        while page <= total_pages:
            params = {'perPage': 100, 'page': page, 'from_date': from_date, 'sort_by': 'updated_at-desc'}
            
            # (اختياري) إضافة فلاتر من الطلب
            request_data = request.get_json() or {}
            for param in ['status', 'payment_method', 'country', 'city', 'product', 'tags']:
                if param in request_data:
                    params[param] = request_data[param]
            
            response = requests.get(f"{Config.SALLA_API_BASE_URL}/orders", headers=headers, params=params, timeout=30)
            
            if response.status_code == 401 and not token_refreshed:
                new_token = refresh_salla_token(user)
                if new_token:
                    headers['Authorization'] = f'Bearer {new_token}'
                    access_token = new_token
                    token_refreshed = True
                    continue
                else:
                    return jsonify({
                        'success': False, 'error': "انتهت صلاحية الجلسة، الرجاء إعادة تسجيل الدخول",
                        'code': 'TOKEN_EXPIRED', 'action_required': True, 'redirect_url': url_for('user_auth.logout')
                    }), 401
            
            if response.status_code != 200:
                error_msg = f"خطأ في استجابة سلة: {response.status_code} - {response.text}"
                return jsonify({'success': False, 'error': "فشل في جلب البيانات من سلة", 'code': 'SALLA_API_ERROR', 'details': response.text[:200]}), 500
            
            data = response.json()
            if 'data' not in data or 'pagination' not in data:
                return jsonify({'success': False, 'error': "استجابة غير متوقعة من سلة", 'code': 'INVALID_RESPONSE_FORMAT'}), 500
            
            orders = data['data']
            all_orders.extend(orders)
            pagination = data['pagination']
            total_pages = pagination.get('totalPages', 1)
            current_app.logger.info(f"تم جلب {len(orders)} طلب من الصفحة {pagination.get('currentPage', page)}/{total_pages}")
            page += 1
            time.sleep(0.2)
        
        current_app.logger.info(f"تم جلب {len(all_orders)} طلب إجمالاً للمعالجة")
        
        # معالجة الطلبات
        # ... بعد سطر current_app.logger.info(f"تم جلب {len(all_orders)} طلب إجمالاً للمعالجة")

# معالجة الطلبات
        new_count, updated_count, skipped_count = 0, 0, 0
        
        for order_data in all_orders:
            try:
                order_id = str(order_data.get('id'))
                if not order_id:
                    skipped_count += 1
                    continue
                
                status_info = order_data.get('status', {})
                status_id_from_api = str(status_info.get('id')) if status_info.get('id') else None
                status_slug_from_api = status_info.get('slug')
                
                # --- Normalize slug ---
                if status_slug_from_api:
                    status_slug_from_api = status_slug_from_api.strip().lower().replace('-', '_')
                
                # البحث عن الحالة: id -> slug -> name
                found_status = None
                if status_id_from_api:
                    found_status = OrderStatus.query.filter_by(id=status_id_from_api, store_id=store_id).first()
                if not found_status and status_slug_from_api:
                    found_status = OrderStatus.query.filter_by(slug=status_slug_from_api, store_id=store_id).first()
                if not found_status and status_info.get('name'):
                    normalized_name = status_info['name'].strip().lower().replace(' ', '_')
                    found_status = OrderStatus.query.filter_by(slug=normalized_name, store_id=store_id).first()
                if not found_status and status_id_from_api:
                    found_status = OrderStatus.query.filter_by(id=status_id_from_api).first()
                
                final_status_id = found_status.id if found_status else None
                
                # Debug log
                current_app.logger.info(
                    f"ربط حالة الطلب {order_id}: "
                    f"id={status_id_from_api}, slug={status_slug_from_api}, "
                    f"name={status_info.get('name')}, found={found_status}"
                )
                
                existing_order = SallaOrder.query.get(order_id)
                
                created_at = None
                date_info = order_data.get('date', {})
                if date_info and 'date' in date_info:
                    try:
                        date_str = date_info['date'].split('.')[0]
                        created_at = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass 
                
                total_info = order_data.get('total', {})
                total_amount = float(total_info.get('amount', 0))
                currency = total_info.get('currency', 'SAR')
                
                if existing_order:
                    existing_order.total_amount = total_amount
                    existing_order.currency = currency
                    existing_order.payment_method = order_data.get('payment_method', '')
                    existing_order.raw_data = json.dumps(order_data, ensure_ascii=False)
                    existing_order.updated_at = datetime.utcnow()
                    existing_order.status_id = final_status_id
                    updated_count += 1
                else:
                    customer = order_data.get('customer', {})
                    customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip() or order_data.get('customer', '')
                    
                    new_order = SallaOrder(
                        id=order_id,
                        store_id=store_id,
                        customer_name=customer_name,
                        created_at=created_at or datetime.utcnow(),
                        total_amount=total_amount,
                        currency=currency,
                        payment_method=order_data.get('payment_method', ''),
                        raw_data=json.dumps(order_data, ensure_ascii=False),
                        status_id=final_status_id
                    )
                    db.session.add(new_order)
                    new_count += 1
                    
            except Exception as e: 
                skipped_count += 1
                current_app.logger.error(f"خطأ في معالجة الطلب {order_data.get('id', 'unknown')}: {str(e)}", exc_info=True)

# ... استكمل باقي الدالة من هنا (user.last_sync = ...)
        
        user.last_sync = datetime.utcnow()
        db.session.commit()
        
        current_app.logger.info(f"تمت المزامنة بنجاح: {new_count} جديد، {updated_count} محدث، {skipped_count} متخطى")
        
        return jsonify({
            'success': True,
            'message': f'تمت المزامنة بنجاح: {new_count} طلب جديد، {updated_count} محدث. {status_message}',
            'stats': {
                'new_orders': new_count, 'updated_orders': updated_count,
                'skipped_orders': skipped_count, 'total_processed': len(all_orders)
            }
        })
    
    except requests.exceptions.RequestException as e:
        error_msg = f"خطأ في الاتصال بسلة: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        return jsonify({'success': False,'error': error_msg,'code': 'NETWORK_ERROR'}), 500
        
    except Exception as e:
        error_msg = f"خطأ غير متوقع: {str(e)}"
        current_app.logger.error(error_msg, exc_info=True)
        return jsonify({'success': False,'error': error_msg,'code': 'INTERNAL_ERROR'}), 500
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
    
    # جلب معلمات الترحيل والتصفية
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int) 
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
        # جلب الطلبات من قاعدة البيانات المحلية (سلة + مخصصة)
        salla_query = SallaOrder.query.filter_by(store_id=user.store_id).options(
            db.joinedload(SallaOrder.status),
            db.joinedload(SallaOrder.assignments).joinedload(OrderAssignment.employee)
        )
        
        custom_query = CustomOrder.query.filter_by(store_id=user.store_id)
        
        # للموظفين العاديين: عرض فقط الطلبات المسندة لهم
        if not is_reviewer and employee:
            salla_query = salla_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee.id)
        
        # تطبيق الفلاتر المشتركة
        if status_filter and order_type in ['all', 'salla']:
            salla_query = salla_query.filter(SallaOrder.status_slug == status_filter)
        
        # تطبيق فلتر الموظف
        if employee_filter and order_type in ['all', 'salla']:
            salla_query = salla_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)
        
        if search_query:
            if order_type in ['all', 'salla']:
                salla_query = salla_query.filter(
                    SallaOrder.customer_name.ilike(f'%{search_query}%') | 
                    SallaOrder.id.ilike(f'%{search_query}%')
                )
            
            if order_type in ['all', 'custom']:
                custom_query = custom_query.filter(
                    CustomOrder.customer_name.ilike(f'%{search_query}%') | 
                    CustomOrder.order_number.ilike(f'%{search_query}%')
                )
        
        # فلترة حسب التاريخ
        if date_from:
            try:
                date_from_obj = datetime.strptime(date_from, '%Y-%m-%d')
                if order_type in ['all', 'salla']:
                    salla_query = salla_query.filter(SallaOrder.created_at >= date_from_obj)
                if order_type in ['all', 'custom']:
                    custom_query = custom_query.filter(CustomOrder.created_at >= date_from_obj)
            except ValueError:
                pass
        
        if date_to:
            try:
                date_to_obj = datetime.strptime(date_to, '%Y-%m-%d') + timedelta(days=1)
                if order_type in ['all', 'salla']:
                    salla_query = salla_query.filter(SallaOrder.created_at <= date_to_obj)
                if order_type in ['all', 'custom']:
                    custom_query = custom_query.filter(CustomOrder.created_at <= date_to_obj)
            except ValueError:
                pass
        
        # جلب الحالات المخصصة بشكل صحيح
        custom_statuses = []
        if is_reviewer:
            # للمديرين/المراجعين: جميع الحالات في المتجر
            custom_statuses = EmployeeCustomStatus.query.join(Employee).filter(
                Employee.store_id == user.store_id
            ).all()
        elif employee:
            # للموظفين العاديين: حالاتهم الخاصة فقط
            custom_statuses = EmployeeCustomStatus.query.filter_by(employee_id=employee.id).all()
        
        # جلب الطلبات بناءً على النوع المحدد
        if order_type == 'salla':
            orders_query = salla_query.order_by(nullslast(db.desc('created_at')))
            pagination_obj = orders_query.paginate(page=page, per_page=per_page)
            orders = pagination_obj.items
        elif order_type == 'custom':
            orders_query = custom_query.order_by(nullslast(db.desc('created_at')))
            pagination_obj = orders_query.paginate(page=page, per_page=per_page)
            orders = pagination_obj.items
        else:  # all - دمج النتيجتين
            # جلب طلبات سلة
            salla_orders = salla_query.all()
            custom_orders = custom_query.all()
            
            # دمج القائمتين وترتيبهم حسب تاريخ الإنشاء
            all_orders = salla_orders + custom_orders
            all_orders.sort(key=lambda x: x.created_at or datetime.min, reverse=True)
            
            # تطبيق الترحيل يدوياً
            total_orders = len(all_orders)
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            paginated_orders = all_orders[start_idx:end_idx]
            
            # إنشاء كائن Pagination مخصص
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
        
        # معالجة البيانات للعرض
        processed_orders = []
        
        for order in orders:
            if isinstance(order, SallaOrder):
                # معالجة طلبات سلة
                raw_data = json.loads(order.raw_data) if order.raw_data else {}
                reference_id = raw_data.get('reference_id', order.id)
                status_name = order.status.name if order.status else 'غير محدد'
                status_slug = order.status.slug if order.status else 'unknown'
                
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
                    'assignments': order.assignments  # إضافة معلومات الإسناد
                }
                
            else:  # CustomOrder
                # معالجة الطلبات المخصصة
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
                    'currency': order.currency
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
@orders_bp.route('/get_quick_list_data', methods=['POST'])
def get_quick_list_data():
    """جلب بيانات القائمة السريعة للطلبات المحددة"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401
    
    # التحقق من الصلاحيات
    is_reviewer = False
    if request.cookies.get('is_admin') == 'true':
        is_reviewer = True
    else:
        if employee and employee.role in ['reviewer', 'manager']:
            is_reviewer = True
    
    if not is_reviewer:
        return jsonify({'success': False, 'error': 'غير مصرح لك بهذا الإجراء'}), 403
    
    data = request.get_json()
    order_ids = data.get('order_ids', [])
    
    if not order_ids:
        return jsonify({'success': False, 'error': 'لم يتم تحديد أي طلبات'}), 400
    
    access_token = user.salla_access_token
    if not access_token:
        return jsonify({'success': False, 'error': 'يجب ربط المتجر مع سلة أولاً'}), 400
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    orders_data = []
    
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
            
            # معالجة بيانات العناصر
            processed_items = []
            for item in items_data:
                # استخراج الصورة الرئيسية
                main_image = ''
                thumbnail_url = item.get('product_thumbnail') or item.get('thumbnail')
                if thumbnail_url and isinstance(thumbnail_url, str):
                    main_image = thumbnail_url
                else:
                    images = item.get('images', [])
                    if images and isinstance(images, list) and len(images) > 0:
                        first_image = images[0]
                        image_url = first_image.get('image', '')
                        if image_url:
                            if not image_url.startswith(('http://', 'https://')):
                                base_domain = "https://cdn.salla.sa"
                                main_image = f"{base_domain}{image_url}"
                            else:
                                main_image = image_url
                    else:
                        for field in ['image', 'url', 'image_url', 'picture']:
                            if item.get(field):
                                main_image = item[field]
                                break
                
                processed_items.append({
                    'name': item.get('name', ''),
                    'quantity': item.get('quantity', 0),
                    'main_image': main_image
                })
            
            orders_data.append({
                'id': order_id,
                'reference_id': order_data.get('reference_id', order_id),
                'items': processed_items
            })
            
        except Exception as e:
            current_app.logger.error(f"Error processing order {order_id} for quick list: {str(e)}")
            continue
    
    return jsonify({
        'success': True,
        'orders': orders_data
    })
    



# إعدادات تحميل الصور
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'static/uploads/custom_orders'

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_next_order_number():
    """إنشاء رقم طلب تلقائي يبدأ من 1000"""
    last_order = CustomOrder.query.order_by(CustomOrder.id.desc()).first()
    if last_order:
        last_number = int(last_order.order_number)
        return str(last_number + 1)
    return "100" 

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
            
            # معالجة تحميل الصورة
            image_file = request.files.get('order_image')
            image_filename = None
            
            if image_file and allowed_file(image_file.filename):
                filename = secure_filename(image_file.filename)
                # إنشاء اسم فريد للصورة
                image_filename = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{filename}"
                image_path = os.path.join(UPLOAD_FOLDER, image_filename)
                
                # إنشاء المجلد إذا لم يكن موجوداً
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                
                # حفظ الصورة
                image_file.save(image_path)
            
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
                store_id=user.store_id
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

@orders_bp.route('/custom/<int:order_id>')
def custom_order_details(order_id):
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    # جلب بيانات الطلب الخاص
    custom_order = CustomOrder.query.get_or_404(order_id)
    
    # التحقق من أن الطلب يخص المتجر الحالي
    if custom_order.store_id != user.store_id:
        flash('غير مصرح لك بالوصول إلى هذا الطلب', 'error')
        return redirect(url_for('orders.index'))
    
    # جلب البيانات الإضافية (ملاحظات الحالة، الإسنادات، إلخ)
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