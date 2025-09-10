# orders/sync.py
import requests
import json
import time
from datetime import datetime, timedelta
from flask import jsonify, request, current_app, url_for
from . import orders_bp
from app.models import db, SallaOrder, OrderStatus, User
from app.utils import get_user_from_cookies
from app.config import Config
from app.token_utils import refresh_salla_token
# orders/sync.py - إضافة الواردات الجديدة
import hmac
import hashlib

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
# orders/sync.py - إضافة الدوال التالية

def verify_webhook_signature(payload, signature):
    """التحقق من توقيع Webhook باستخدام السر السري"""
    try:
        computed_signature = hmac.new(
            Config.WEBHOOK_SECRET.encode('utf-8'),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(computed_signature, signature)
    except Exception as e:
        current_app.logger.error(f"خطأ في التحقق من التوقيع: {str(e)}")
        return False

@orders_bp.route('/webhook/order_status', methods=['POST'])
def handle_order_status_webhook():
    """معالجة Webhook لتحديثات حالة الطلب من Salla"""
    try: 
        # تسجيل تفصيلي للطلب الوارد
        current_app.logger.info(f"طلب Webhook وارد: {request.method} {request.path}")
        current_app.logger.info(f"الرؤوس: {dict(request.headers)}")
        
        # التحقق من صحة التوقيع
        signature = request.headers.get('X-Salla-Signature')
        if not signature:
            current_app.logger.warning("طلب Webhook بدون توقيع")
            return jsonify({'success': False, 'error': 'Missing signature'}), 401
            
        if not verify_webhook_signature(request.get_data(), signature):
            current_app.logger.warning("طلب Webhook غير موثوق - توقيع غير صالح")
            return jsonify({'success': False, 'error': 'Invalid signature'}), 401
        
        data = request.get_json()
        if not data:
            current_app.logger.error("طلب Webhook بدون بيانات JSON")
            return jsonify({'success': False, 'error': 'No JSON data'}), 400
            
        current_app.logger.info(f"تم استقبال Webhook: {data.get('event')}")
        current_app.logger.debug(f"بيانات Webhook كاملة: {json.dumps(data, ensure_ascii=False)}")
        
        # باقي الكود...     current_app.logger.info(f"تم استقبال Webhook: {data.get('event')}")
        
        # معالجة أنواع الأحداث المختلفة
        event_type = data.get('event')
        
        if event_type == 'order.status.updated':
            return handle_order_status_update(data)
        elif event_type == 'order.created':
            return handle_order_created(data)
        elif event_type == 'order.updated':
            return handle_order_updated(data)
        else:
            current_app.logger.info(f"تم استقبال حدث غير معالج: {event_type}")
            return jsonify({'success': True, 'message': 'Event received but not processed'})
            
    except Exception as e:
        current_app.logger.error(f"خطأ في معالجة Webhook: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Internal server error'}), 500
    finally:
        # إغلاق اتصال قاعدة البيانات
        db.session.close()
def handle_order_status_update(data):
    """معالجة تحديث حالة الطلب"""
    try:
        order_data = data.get('data', {})
        order_id = str(order_data.get('id'))
        
        if not order_id:
            return jsonify({'success': False, 'error': 'Missing order ID'}), 400
        
        # البحث عن الطلب في قاعدة البيانات
        order = SallaOrder.query.get(order_id)
        if not order:
            current_app.logger.warning(f"طلب غير موجود لتحديث الحالة: {order_id}")
            return jsonify({'success': False, 'error': 'Order not found'}), 404
        
        # تحديث حالة الطلب
        status_info = order_data.get('status', {})
        status_id = str(status_info.get('id')) if status_info.get('id') else None
        
        if status_id:
            # البحث عن الحالة في قاعدة البيانات
            status = OrderStatus.query.filter_by(id=status_id, store_id=order.store_id).first()
            if status:
                order.status_id = status.id
                order.updated_at = datetime.utcnow()
                db.session.commit()
                
                current_app.logger.info(f"تم تحديث حالة الطلب {order_id} إلى {status_id}")
                return jsonify({'success': True, 'message': 'Order status updated'})
        
        return jsonify({'success': False, 'error': 'Status not found'}), 404
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"خطأ في تحديث حالة الطلب: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to update order status'}), 500


def handle_order_updated(data):
    """معالجة تحديث الطلب"""
    try:
        order_data = data.get('data', {})
        order_id = str(order_data.get('id'))
        
        if not order_id:
            return jsonify({'success': False, 'error': 'Missing order ID'}), 400
        
        # البحث عن الطلب وتحديثه
        order = SallaOrder.query.get(order_id)
        if not order:
            current_app.logger.warning(f"طلب غير موجود للتحديث: {order_id}")
            return jsonify({'success': False, 'error': 'Order not found'}), 404
        
        # تحديث بيانات الطلب
        total_info = order_data.get('total', {})
        if total_info:
            order.total_amount = float(total_info.get('amount', order.total_amount))
            order.currency = total_info.get('currency', order.currency)
        
        order.payment_method = order_data.get('payment_method', order.payment_method)
        order.raw_data = json.dumps(order_data, ensure_ascii=False)
        order.updated_at = datetime.utcnow()
        
        db.session.commit()
        
        current_app.logger.info(f"تم تحديث الطلب: {order_id}")
        return jsonify({'success': True, 'message': 'Order updated'})
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"خطأ في تحديث الطلب: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to update order'}), 500      

# orders/sync.py - تعديل دالة register_webhook

def register_webhook(user, event_type='order.status.updated'):
    """تسجيل webhook في سلة لاستقبال تحديثات الحالات - متوافق مع v2"""
    try:
        access_token = user.salla_access_token
        if not access_token:
            return False, "لا يوجد توكن وصول"
        
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        
        webhook_url = f"{Config.BASE_URL}/webhook/order_status"
        
        # الأحداث المهمة التي نريد متابعتها
        important_events = [
            'order.status.updated',
            'order.created',
            'order.updated',
            'order.cancelled'
        ]
        
        # تسجيل جميع الأحداث المهمة
        results = []
        for event in important_events:
            payload = {
                "url": webhook_url,
                "event": event,
                "secret": Config.WEBHOOK_SECRET,
                "version": 2,
                "security_strategy": "signature"
            }
            
            response = requests.post(
                f"{Config.SALLA_API_BASE_URL}/webhooks",
                headers=headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code in [200, 201]:
                results.append(f"تم تسجيل {event} بنجاح")
            else:
                error_details = response.text
                results.append(f"فشل في تسجيل {event}: {error_details}")
                current_app.logger.error(f"فشل في تسجيل webhook للحدث {event}: {error_details}")
        
        return True, " | ".join(results)
            
    except Exception as e:
        current_app.logger.error(f"خطأ في تسجيل webhook: {str(e)}")
        return False, f"خطأ في تسجيل webhook: {str(e)}"
@orders_bp.route('/register_webhook', methods=['POST'])
def register_webhook_route():
    """تسجيل webhook في سلة لاستقبال تحديثات الحالات"""
    try:
        user, employee = get_user_from_cookies()
        
        if not user:
            return jsonify({
                'success': False, 
                'error': 'الرجاء تسجيل الدخول أولاً'
            }), 401
        
        success, message = register_webhook(user)
        
        if success:
            return jsonify({
                'success': True,
                'message': message
            })
        else:
            return jsonify({
                'success': False,
                'error': message
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'خطأ غير متوقع: {str(e)}'
        }), 500
# orders/sync.py - إضافة دالة مساعدة

def extract_store_id_from_webhook(webhook_data):
    """استخراج معرف المتجر من بيانات Webhook - الإصدار النهائي"""
    try:
        # تسجيل بنية البيانات للتصحيح
        logger.info(f"🔍 تحليل بنية Webhook: المفاتيح الموجودة: {list(webhook_data.keys())}")
        
        # البحث في المواقع الأكثر شيوعاً أولاً
        if 'merchant' in webhook_data and webhook_data['merchant'] is not None:
            store_id = str(webhook_data['merchant'])
            logger.info(f"✅ تم العثور على معرف المتجر في 'merchant': {store_id}")
            return store_id
            
        if 'merchant_id' in webhook_data and webhook_data['merchant_id'] is not None:
            store_id = str(webhook_data['merchant_id'])
            logger.info(f"✅ تم العثور على معرف المتجر في 'merchant_id': {store_id}")
            return store_id
            
        if 'store_id' in webhook_data and webhook_data['store_id'] is not None:
            store_id = str(webhook_data['store_id'])
            logger.info(f"✅ تم العثور على معرف المتجر في 'store_id': {store_id}")
            return store_id
        
        # البحث داخل كائن data إذا وجد
        if 'data' in webhook_data and isinstance(webhook_data['data'], dict):
            data_obj = webhook_data['data']
            
            if 'merchant' in data_obj and data_obj['merchant'] is not None:
                store_id = str(data_obj['merchant'])
                logger.info(f"✅ تم العثور على معرف المتجر في 'data.merchant': {store_id}")
                return store_id
                
            if 'merchant_id' in data_obj and data_obj['merchant_id'] is not None:
                store_id = str(data_obj['merchant_id'])
                logger.info(f"✅ تم العثور على معرف المتجر في 'data.merchant_id': {store_id}")
                return store_id
                
            if 'store_id' in data_obj and data_obj['store_id'] is not None:
                store_id = str(data_obj['store_id'])
                logger.info(f"✅ تم العثور على معرف المتجر في 'data.store_id': {store_id}")
                return store_id
        
        # إذا لم نجد في أي مكان، نبحث بشكل متعمق
        def deep_find(obj, key):
            """الببحث المتعمق عن مفتاح في أي مستوى من الكائن"""
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
        
        # البحث عن أي من المفاتيح المحتملة
        for key in ['merchant', 'merchant_id', 'store_id']:
            value = deep_find(webhook_data, key)
            if value is not None:
                store_id = str(value)
                logger.info(f"✅ تم العثور على معرف المتجر بالبحث المتعمق في '{key}': {store_id}")
                return store_id
        
        logger.warning("❌ لم يتم العثور على معرف المتجر في أي من المواقع المتوقعة")
        logger.debug(f"بيانات الويب هوك كاملة: {json.dumps(webhook_data, ensure_ascii=False)}")
        return None
        
    except Exception as e:
        logger.error(f"❌ خطأ في استخراج معرف المتجر: {str(e)}", exc_info=True)
        return None