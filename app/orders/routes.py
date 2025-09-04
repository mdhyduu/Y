# orders/routes.py
import json
import logging  # إضافة استيراد logging
from math import ceil
from datetime import datetime, timedelta
from flask import (render_template, request, flash, redirect, url_for, 
                   make_response, current_app)
import requests
from sqlalchemy import nullslast
from . import orders_bp
from app.models import (db, SallaOrder, CustomOrder, OrderStatus, Employee, 
                     OrderAssignment, EmployeeCustomStatus, OrderStatusNote, 
                     OrderEmployeeStatus, OrderProductStatus, CustomNoteStatus)  # إضافة CustomNoteStatus وإزالة الفاصلة الزائدة
from app.utils import get_user_from_cookies, process_order_data, format_date, generate_barcode, humanize_time
from app.token_utils import refresh_salla_token
from app.config import Config

# إضافة تعريف الـ logger
logger = logging.getLogger(__name__)

# باقي الكود بدون تغيير...

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
  
        salla_query = SallaOrder.query.filter_by(store_id=user.store_id).options(
            db.joinedload(SallaOrder.status),
            db.joinedload(SallaOrder.assignments).joinedload(OrderAssignment.employee),
            db.joinedload(SallaOrder.status_notes)  # إضافة joinedload للملاحظات
        )
        
        custom_query = CustomOrder.query.filter_by(store_id=user.store_id).options(
            db.joinedload(CustomOrder.status),
            db.joinedload(CustomOrder.assignments).joinedload(OrderAssignment.employee),
            db.joinedload(CustomOrder.status_notes)  # إضافة joinedload للملاحظات
        )
        
        # للموظفين العاديين: عرض فقط الطلبات المسندة لهم
        if not is_reviewer and employee:
            salla_query = salla_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee.id)
            custom_query = custom_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee.id)
        
        # تطبيق الفلاتر المشتركة
        # تطبيق فلتر الحالة الخاصة (late, missing, etc.)
        # تطبيق فلتر الحالة الخاصة (late, missing, etc.)
        if status_filter in ['late', 'missing', 'not_shipped', 'refunded']:
            if order_type in ['all', 'salla']:
                # للطلبات السلة: استخدام status_notes المرتبطة بـ SallaOrder
                salla_query = salla_query.join(
                    OrderStatusNote, 
                    OrderStatusNote.order_id == SallaOrder.id
                ).filter(
                    OrderStatusNote.status_flag == status_filter
                )
            if order_type in ['all', 'custom']:
                # للطلبات المخصصة: استخدام status_notes المرتبطة بـ CustomOrder
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
        
        # تطبيق فلتر الموظف
        if employee_filter:
            if order_type in ['all', 'salla']:
                salla_query = salla_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)
            if order_type in ['all', 'custom']:
                custom_query = custom_query.join(OrderAssignment).filter(OrderAssignment.employee_id == employee_filter)
        
        # تطبيق فلتر الحالة المخصصة
        if custom_status_filter:
            custom_status_id = int(custom_status_filter)
            if order_type in ['all', 'salla']:
                salla_query = salla_query.join(SallaOrder.status_notes).filter(
                    OrderStatusNote.custom_status_id == custom_status_id
                )
            if order_type in ['all', 'custom']:
                custom_query = custom_query.join(CustomOrder.status_notes).filter(
                    OrderStatusNote.custom_status_id == custom_status_id
                )
        
        # تطبيق فلتر البحث
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
        
        # ... [بقية الكود دون تغيير] ...
        
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
                    'assignments': order.assignments,
                    'employee_statuses': order.employee_statuses,  # <<< أضف هذا
                    'status_notes': order.status_notes             # <<< وأيضًا هذا لو عايز تعرض الملاحظات
                } 
                
            else:  # CustomOrder
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
                    'employee_statuses': order.employee_statuses,  # <<< نفس الشيء هنا
                    'status_notes': order.status_notes
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
# ... existing code ...

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

        # جلب حالات المنتجات للطلب
        # جلب حالات المنتجات للطلب
        # في دالة order_details، استبدل كود جلب حالات المنتجات بالكود التالي:
        product_statuses = {}
        # جلب جميع حالات المنتجات للطلب الحالي
        status_records = OrderProductStatus.query.filter_by(order_id=str(order_id)).all()
        for status in status_records:
            product_statuses[status.product_id] = {
                'status': status.status,
                'notes': status.notes,
                'updated_at': status.updated_at
            }

# ... rest of the code ...
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
        