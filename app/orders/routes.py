# orders/routes.py
import json
import logging
from math import ceil
from datetime import datetime, timedelta
from flask import (render_template, request, flash, redirect, url_for, jsonify, 
                   make_response, current_app)
import requests
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

        # --- استخراج reference_id من البيانات ---
        reference_id = order_data.get('reference_id')
        print(f"🔗 reference_id المستخرج: {reference_id}")
        
        # --- التحقق إذا الطلب موجود مسبقاً ---
        existing_order = SallaOrder.query.get(order_id)
        if existing_order:
            print(f"✅ الطلب موجود مسبقاً في قاعدة البيانات")

            # تحديث full_order_data إذا كان ناقص
            if not existing_order.full_order_data:
                existing_order.full_order_data = order_data
                print("✅ تم تحديث الطلب ببيانات كاملة (full_order_data)")

            # تحديث reference_id إذا لم يكن موجوداً
            if not existing_order.reference_id and reference_id:
                existing_order.reference_id = str(reference_id)
                print(f"✅ تم تحديث reference_id للطلب: {reference_id}")
            
            db.session.commit()

            # التحقق من وجود العنوان
            existing_address = OrderAddress.query.filter_by(order_id=order_id).first()
            if not existing_address:
                print("📝 لم يتم العثور على عنوان، جاري إضافته...")
                address_info = extract_order_address(order_data)
                if address_info:
                    address_info['name'] = encrypt_data(address_info.get('name', ''))
                    address_info['phone'] = encrypt_data(address_info.get('phone', ''))
                    new_address = OrderAddress(order_id=order_id, **address_info)
                    db.session.add(new_address)
                    db.session.commit()
                    print("✅ تم حفظ العنوان الجديد بنجاح")
            return True

        print("🆕 طلب جديد، جاري إنشاؤه...")

        # --- ربط الطلب بالمستخدم (store owner) ---
        user = User.query.filter_by(store_id=store_id).first()
        if not user:
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
            except Exception:
                created_at = datetime.utcnow()

        # --- المبلغ والعملة ---
        total_info = order_data.get('total') or order_data.get('amounts', {}).get('total', {})
        total_amount = float(total_info.get('amount', 0))
        currency = total_info.get('currency', 'SAR')

        # --- بيانات العميل ---
        customer = order_data.get('customer', {})
        customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        if not customer_name:
            customer_name = order_data.get('customer_name', 'عميل غير معروف')
        encrypted_customer_name = encrypt_data(customer_name)

        # --- تحديد حالة الطلب ---
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

        # --- إنشاء الطلب الجديد مع reference_id ---
        new_order = SallaOrder(
            id=order_id,
            store_id=store_id,
            customer_name=encrypted_customer_name,
            created_at=created_at or datetime.utcnow(),
            total_amount=total_amount,
            currency=currency,
            payment_method=order_data.get('payment_method', ''),
            raw_data=json.dumps(order_data, ensure_ascii=False),
            full_order_data=order_data,   # ✅ تخزين البيانات الكاملة
            status_id=status_id,
            reference_id=str(reference_id) if reference_id else None  # ✅ حفظ reference_id
        )
        db.session.add(new_order)
        db.session.flush()

        # --- إضافة العنوان ---
        address_info = extract_order_address(order_data)
        if address_info:
            address_info['name'] = encrypt_data(address_info.get('name', ''))
            address_info['phone'] = encrypt_data(address_info.get('phone', ''))
            new_address = OrderAddress(order_id=order_id, **address_info)
            db.session.add(new_address)

        db.session.commit()
        print(f"🎉 تم حفظ الطلب مع reference_id: {reference_id} والعنوان بنجاح")
        return True

    except Exception as e:
        db.session.rollback()
        error_msg = f"❌ خطأ في إنشاء الطلب من Webhook: {str(e)}"
        print(error_msg)
        logger.error(error_msg, exc_info=True)
        return False
        
def update_order_items_from_webhook(order, order_data):
    """
    تحديث المنتجات داخل full_order_data عند استلام order.updated
    - يستبدل items بالقائمة الجديدة
    - يقارن القديمة مع الجديدة
    - يسجل المنتجات المحذوفة والمضافة في OrderProductStatus
    """
    try:
        old_items = order.full_order_data.get('items', []) if order.full_order_data else []
        new_items = order_data.get('items', [])

        # استخراج IDs للمنتجات القديمة والجديدة
        old_ids = {str(i.get('id')) for i in old_items if i.get('id')}
        new_ids = {str(i.get('id')) for i in new_items if i.get('id')}

        removed_ids = old_ids - new_ids
        added_ids = new_ids - old_ids

        print(f"🔄 تحديث عناصر الطلب {order.id}: removed={removed_ids}, added={added_ids}")

        # تحديث full_order_data بالكامل
        order.full_order_data = order_data

        # تحديث raw_data كنسخة أصلية
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
        print(f"❌ خطأ في تحديث المنتجات للطلب {order.id}: {str(e)}")
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
            
            # ⭐⭐ إضافة معالجة إضافية لـ merchant_id من الكود الثاني ⭐⭐
            if merchant_id is None:
                merchant_id = webhook_data.get('merchant') or webhook_data.get('store_id')
                if merchant_id is None:
                    return jsonify({'success': False, 'error': 'لا يوجد معرف متجر'}), 400
            
            order_data = webhook_data
        else:
            event = data.get('event')
            order_data = data.get('data', {})
            merchant_id = order_data.get('merchant_id')

        # إنشاء طلب جديد
        if event == 'order.created' and order_data:
            success = handle_order_creation(data if webhook_version == '2' else order_data, webhook_version)
            if success:
                return jsonify({'success': True, 'message': 'تم إنشاء الطلب بنجاح'}), 200
            else:
                return jsonify({'success': False, 'error': 'فشل في إنشاء الطلب'}), 500

        # تحديث حالة أو بيانات الطلب
        elif event in ['order.status.updated', 'order.updated'] and order_data:
            order_id = str(order_data.get('id'))
            order = SallaOrder.query.get(order_id)

            if not order:
                return jsonify({'success': False, 'error': 'الطلب غير موجود'}), 404

            # ⭐⭐ الإصلاح: تحديث الحالة في كلا الحدثين ⭐⭐
            status_updated = False
            store_id = order.store_id
            
            # تحديث حالة الطلب في حدث order.status.updated
            if event == 'order.status.updated':
                status_data = order_data.get('status', {}) or order_data.get('current_status', {})
                if status_data:
                    status_slug = status_data.get('slug', '').lower().replace('-', '_')
                    if not status_slug and status_data.get('name'):
                        status_slug = status_data['name'].lower().replace(' ', '_')
                    status = OrderStatus.query.filter_by(slug=status_slug, store_id=order.store_id).first()
                    if status:
                        order.status_id = status.id
                        status_updated = True
                        print(f"✅ تم تحديث حالة الطلب {order_id} إلى {status_slug}")
                        
                        # ⭐⭐ التحقق وإزالة حالة "متأخر" إذا أصبح الطلب مكتملاً ⭐⭐
                        print(f"🔄 التحقق من إزالة حالة المتأخر للطلب {order_id}")
                        handle_order_completion(store_id, order_id, status_slug)

            # ⭐⭐ تحديث الحالة أيضاً في حدث order.updated (من الكود الثاني) ⭐⭐
            elif event == 'order.updated':
                status_data = order_data.get('status', {}) or order_data.get('current_status', {})
                if status_data:
                    status_slug = status_data.get('slug', '').lower().replace('-', '_')
                    if not status_slug and status_data.get('name'):
                        status_slug = status_data['name'].lower().replace(' ', '_')
                    status = OrderStatus.query.filter_by(slug=status_slug, store_id=order.store_id).first()
                    if status:
                        order.status_id = status.id
                        status_updated = True
                        print(f"✅ تم تحديث حالة الطلب {order_id} إلى {status_slug}")
                        
                        # ⭐⭐ التحقق وإزالة حالة "متأخر" إذا أصبح الطلب مكتملاً ⭐⭐
                        print(f"🔄 التحقق من إزالة حالة المتأخر للطلب {order_id}")
                        handle_order_completion(store_id, order_id, status_slug)

                if 'payment_method' in order_data:
                    order.payment_method = order_data.get('payment_method')
                    payment_updated = True
                    print(f"✅ تم تحديث طريقة الدفع للطلب {order_id} إلى {order.payment_method}")
                            
                # تحديث المنتجات باستخدام الدالة الجديدة
                update_order_items_from_webhook(order, order_data)

                # تحديث العنوان إذا تغير
                update_success = update_order_address(order_id, order_data)
                if update_success:
                    print(f"✅ تم تحديث بيانات العنوان للطلب {order_id}")
                else:
                    print(f"⚠️ فشل في تحديث العنوان للطلب {order_id}")

            # حفظ التغييرات في قاعدة البيانات
            if status_updated:
                db.session.commit()
                print(f"💾 تم حفظ تغييرات حالة الطلب {order_id} في قاعدة البيانات")

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
