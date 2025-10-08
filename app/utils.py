from datetime import datetime
import os
import barcode
from barcode.writer import ImageWriter
from flask import current_app, request
from .models import db, User, Employee, CustomOrder, SallaOrder
import logging
from io import BytesIO
import re, base64
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from contextlib import contextmanager
from sqlalchemy import text, create_engine
from sqlalchemy.pool import QueuePool
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import psycopg2
from threading import Lock
import queue
from flask import has_app_context

# إعداد المسجل للإنتاج
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'static/uploads/custom_orders'

@contextmanager
def app_context():
    """مدير سياق للتطبيق لاستخدامه في الخيوط"""
    from flask import current_app
    app = current_app._get_current_object()
    with app.app_context():
        yield

# إعداد محرك PostgreSQL مع Connection Pool
def create_postgresql_engine():
    """إنشاء محرك PostgreSQL محسن للأداء"""
    try:
        # الحصول على إعدادات الاتصال من التطبيق
        database_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI')
        if not database_uri:
            # استخدام URI افتراضي إذا لم يتم تعيينه
            database_uri = 'postgresql://username:password@localhost:5432/your_database'
        
        engine = create_engine(
            database_uri,
            poolclass=QueuePool,
            pool_size=20,
            max_overflow=30,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo_pool=False  # تعطيل التسجيل للأداء
        )
        return engine
    except Exception as e:
        logger.error(f"Error creating PostgreSQL engine: {str(e)}")
        # Fallback إلى الاتصال العادي
        return db.engine

# إنشاء محرك عالمي (سيتم تهيئته عند الاستخدام الأول)
_postgres_engine = None

def get_postgres_engine():
    """الحصول على محرك PostgreSQL مع تهيئة lazy"""
    global _postgres_engine
    if _postgres_engine is None:
        _postgres_engine = create_postgresql_engine()
    return _postgres_engine

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_next_order_number():
    """إنشاء رقم طلب تلقائي يبدأ من 1000"""
    try:
        with app_context():
            last_order = CustomOrder.query.order_by(CustomOrder.id.desc()).first()
            if last_order and last_order.order_number:
                try:
                    last_number = int(last_order.order_number)
                    return str(last_number + 1)
                except ValueError:
                    return str(last_order.id + 1000)
            return "1000"
    except Exception as e:
        logger.error(f"Error in get_next_order_number: {str(e)}")
        return "1000"
    finally:
        db.session.remove()

def get_user_from_cookies():
    """استخراج بيانات المستخدم من الكوكيز"""
    user_id = request.cookies.get('user_id')
    is_admin = request.cookies.get('is_admin') == 'true'
    employee_role = request.cookies.get('employee_role', '')
    
    if not user_id:
        return None, None
    
    try:
        with app_context():
            if is_admin:
                user = User.query.get(int(user_id))
                return user, None
            else:
                employee = Employee.query.get(int(user_id))
                if employee:
                    user = User.query.filter_by(store_id=employee.store_id).first()
                    return user, employee
                return None, None
    except (ValueError, TypeError) as e:
        logger.error(f"Error in get_user_from_cookies: {str(e)}")
        return None, None
    finally:
        db.session.remove()

def create_session():
    """إنشاء جلسة طلبات مع إعدادات التحسين"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session



from barcode.writer import ImageWriter
from PIL import Image

def clean_data(data: str) -> str:
    """تنظيف البيانات من الرموز الغير مرغوبة"""
    return re.sub(r'[^A-Za-z0-9\s\-]', '', str(data)).strip()

from io import BytesIO
from PIL import Image, ImageChops
import base64
import barcode
from barcode.writer import ImageWriter

from datetime import datetime
import os
import qrcode  # استبدال مكتبة الباركود بمكتبة QR Code
from qrcode.image.pil import PilImage
from io import BytesIO
import base64
from flask import current_app
import logging
from .services.storage_service import do_storage # استيراد خدمة التخزين

# ... (بقية الاستيرادات كما هي)

def generate_barcode(data, dpi=300):
    """إنشاء QR Code بدلاً من الباركود"""
    try:
        # استخدام الرابط الكامل بدلاً من رقم الطلب فقط
        base_url = "https://plankton-app-9im8u.ondigitalocean.app/"
        
        # إذا كان البيانات عبارة عن رقم طلب فقط، قم ببناء الرابط الكامل
        if str(data).strip().isdigit():
            data_str = f"{base_url}{data}"
        else:
            data_str = str(data).strip()
            
        if not data_str:
            return None

        # إنشاء QR Code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data_str)
        qr.make(fit=True)

        # إنشاء الصورة
        img = qr.make_image(fill_color="black", back_color="white")

        # حفظ الصورة في BytesIO
        buffer = BytesIO()
        img.save(buffer, format='PNG', optimize=True)
        buffer.seek(0)

        # رفع الصورة إلى DigitalOcean Spaces
        # استخدام رقم الطلب فقط كاسم للملف لتجنب المشاكل
        order_number = str(data).strip()
        qr_code_url = do_storage.upload_qr_code(buffer, order_number, folder='qrcodes')
        
        if qr_code_url:
            logger.info(f"QR Code generated and uploaded successfully for: {data_str}")
            return qr_code_url
        else:
            logger.error(f"Failed to upload QR Code for: {data_str}")
            # Fallback: إرجاع base64 إذا فشل الرفع
            buffer.seek(0)
            qr_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
            return f"data:image/png;base64,{qr_base64}"

    except Exception as e:
        logger.error(f"Error generating QR code: {str(e)}")
        return None

def generate_and_store_qr_code(order_id, order_type='salla', store_id=None):
    """إنشاء وتخزين QR Code مع حفظ الرابط في قاعدة البيانات"""
    try:
        with app_context():
            order_id_str = str(order_id).strip()
            if not order_id_str:
                logger.error("Empty order ID provided for QR code generation")
                return None
            
            logger.info(f"Attempting to generate QR code for order: {order_id_str}, type: {order_type}")
            
            # إنشاء QR Code باستخدام الرابط الكامل
            qr_code_url = generate_barcode(order_id_str)
            
            if not qr_code_url:
                logger.error(f"Failed to generate QR code for order: {order_id_str}")
                return None
            
            # تخزين الرابط في قاعدة البيانات
            try:
                engine = get_postgres_engine()
                with engine.connect() as conn:
                    if order_type == 'salla':
                        # البحث عن الطلب أولاً
                        existing_order_query = text("SELECT id, reference_id FROM salla_orders WHERE id = :id")
                        result = conn.execute(existing_order_query, {'id': order_id_str})
                        existing_order = result.fetchone()
                        
                        if existing_order:
                            reference_id = existing_order[1] or order_id_str
                            logger.info(f"Using reference_id: {reference_id} for order: {order_id_str}")
                        else:
                            reference_id = order_id_str
                        
                        query = text("""
                            INSERT INTO salla_orders (id, store_id, qr_code_url, barcode_generated_at, reference_id)
                            VALUES (:id, :store_id, :qr_code_url, :barcode_generated_at, :reference_id)
                            ON CONFLICT (id) 
                            DO UPDATE SET 
                                qr_code_url = EXCLUDED.qr_code_url,
                                barcode_generated_at = EXCLUDED.barcode_generated_at,
                                reference_id = EXCLUDED.reference_id
                        """)
                        
                        params = {
                            'id': order_id_str,
                            'store_id': store_id,
                            'qr_code_url': qr_code_url,
                            'barcode_generated_at': datetime.utcnow(),
                            'reference_id': reference_id
                        }
                    
                    else:
                        query = text("""
                            INSERT INTO custom_order (id, qr_code_url, barcode_generated_at)
                            VALUES (:id, :qr_code_url, :barcode_generated_at)
                            ON CONFLICT (id) 
                            DO UPDATE SET 
                                qr_code_url = EXCLUDED.qr_code_url,
                                barcode_generated_at = EXCLUDED.barcode_generated_at
                        """)
                        
                        params = {
                            'id': order_id_str,
                            'qr_code_url': qr_code_url,
                            'barcode_generated_at': datetime.utcnow()
                        }
                    
                    conn.execute(query, params)
                    conn.commit()
                    logger.info(f"QR Code URL stored successfully for order: {order_id_str}")
                    
            except Exception as storage_error:
                logger.error(f"Error storing QR code URL: {str(storage_error)}")
                # محاولة التحديث فقط إذا فشل الإدراج
                try:
                    engine = get_postgres_engine()
                    with engine.connect() as conn:
                        if order_type == 'salla':
                            update_query = text("""
                                UPDATE salla_orders 
                                SET qr_code_url = :qr_code_url, 
                                    barcode_generated_at = :barcode_generated_at
                                WHERE id = :id
                            """)
                        else:
                            update_query = text("""
                                UPDATE custom_order 
                                SET qr_code_url = :qr_code_url, 
                                    barcode_generated_at = :barcode_generated_at
                                WHERE id = :id
                            """)
                        
                        conn.execute(update_query, {
                            'id': order_id_str,
                            'qr_code_url': qr_code_url,
                            'barcode_generated_at': datetime.utcnow()
                        })
                        conn.commit()
                        logger.info(f"QR Code URL updated successfully for order: {order_id_str}")
                except Exception as update_error:
                    logger.error(f"Error updating QR code URL: {str(update_error)}")
            
            return qr_code_url
                
    except Exception as e:
        logger.error(f"Error in generate_and_store_qr_code: {str(e)}")
        return None
def get_cached_qr_code_url(order_id):
    """الحصول على رابط QR Code من التخزين المؤقت"""
    try:
        with app_context():
            order_id_str = str(order_id).strip()
            if not order_id_str:
                return None
            
            order = SallaOrder.query.filter_by(id=order_id_str).first()
            
            if order and order.qr_code_url:
                qr_code_url = order.qr_code_url
                
                if qr_code_url.startswith('http') or qr_code_url.startswith('data:image'):
                    return qr_code_url
                else:
                    # إذا كان الرابط غير صالح، نعيد None لإنشاء جديد
                    logger.warning(f"Invalid QR code URL for order {order_id_str}, generating new one")
                    return None
                    
            return None
            
    except Exception as e:
        logger.error(f"Error in get_cached_qr_code_url: {str(e)}")
        return None

def get_qr_codes_for_orders_optimized(order_ids):
    """نسخة محسنة من دالة جلب روابط QR Code"""
    try:
        if not order_ids:
            return {}
        
        order_ids_str = [str(oid).strip() for oid in order_ids if str(oid).strip()]
        
        if not order_ids_str:
            return {}
            
        # استخدام connection pool لتحسين الأداء
        engine = get_postgres_engine()
        with engine.connect() as conn:
            query = text("""
                SELECT id, qr_code_url 
                FROM salla_orders 
                WHERE id = ANY(:order_ids) AND qr_code_url IS NOT NULL
            """)
            
            result = conn.execute(query, {'order_ids': order_ids_str})
            rows = result.fetchall()
            
            qr_codes_map = {}
            for row in rows:
                qr_code_url = row[1]
                if qr_code_url and (qr_code_url.startswith('http') or qr_code_url.startswith('data:image')):
                    qr_codes_map[str(row[0])] = qr_code_url
            
            return qr_codes_map
            
    except Exception as e:
        logger.error(f"Error in get_qr_codes_for_orders_optimized: {str(e)}")
        return get_qr_codes_for_orders_fallback(order_ids)

def get_qr_codes_for_orders_fallback(order_ids):
    """طريقة احتياطية لجلب روابط QR Code"""
    try:
        with app_context():
            if not order_ids:
                return {}
            
            order_ids_str = [str(oid).strip() for oid in order_ids if str(oid).strip()]
            
            if not order_ids_str:
                return {}
                
            orders = SallaOrder.query.filter(
                SallaOrder.id.in_(order_ids_str), 
                SallaOrder.qr_code_url.isnot(None)
            ).all()
            
            qr_codes_map = {}
            for order in orders:
                if order.qr_code_url and (order.qr_code_url.startswith('http') or order.qr_code_url.startswith('data:image')):
                    qr_codes_map[str(order.id)] = order.qr_code_url
            
            return qr_codes_map
            
    except Exception as e:
        logger.error(f"Error in get_qr_codes_for_orders_fallback: {str(e)}")
        return {}

def process_order_data(order_id, items_data, qr_code_url=None, store_id=None):
    """معالجة بيانات الطلب مع استخدام QR Code المخزن"""
    try:
        with app_context():
            order_id_str = str(order_id).strip()
            if not order_id_str:
                logger.error("Empty order ID in process_order_data")
                return None
                
            logger.info(f"Processing order data for order: {order_id_str}")
                
            items = []
            
            for index, item in enumerate(items_data):
                try:
                    item_id = item.get('id') or f"temp_{index}"
                    main_image = get_main_image(item)
                    
                    options = []
                    item_options = item.get('options', [])
                    if isinstance(item_options, list):
                        for option in item_options:
                            raw_value = option.get('value', '')
                            display_value = 'غير محدد'
                            
                            if isinstance(raw_value, dict):
                                display_value = raw_value.get('name') or raw_value.get('value') or str(raw_value)
                            elif isinstance(raw_value, list):
                                values_list = [str(opt.get('name') or opt.get('value') or str(opt)) 
                                             for opt in raw_value if isinstance(opt, (dict, str))]
                                display_value = ', '.join(values_list)
                            else:
                                display_value = str(raw_value) if raw_value else 'غير محدد'
                            
                            options.append({
                                'name': option.get('name', ''),
                                'value': display_value,
                                'type': option.get('type', '')
                            })
                    
                    digital_codes = [{'code': code.get('code', ''), 'status': code.get('status', 'غير معروف')} 
                                    for code in item.get('codes', []) if isinstance(code, dict)]
                    
                    digital_files = [{'url': file.get('url', ''), 'name': file.get('name', ''), 'size': file.get('size', 0)} 
                                   for file in item.get('files', []) if isinstance(file, dict)]
                    
                    reservations = [{'id': res.get('id'), 'from': res.get('from', ''), 'to': res.get('to', ''), 'date': res.get('date', '')} 
                                  for res in item.get('reservations', []) if isinstance(res, dict)]
                    
                    item_data = {
                        'id': item_id,
                        'name': item.get('name', ''),
                        'sku': item.get('sku', ''),
                        'quantity': item.get('quantity', 0),
                        'currency': item.get('currency', 'SAR'),
                        'price': {
                            'amount': item.get('amounts', {}).get('price_without_tax', {}).get('amount', 0),
                            'currency': item.get('currency', 'SAR')
                        },
                        'tax_percent': item.get('amounts', {}).get('tax', {}).get('percent', '0.00'),
                        'tax_amount': item.get('amounts', {}).get('tax', {}).get('amount', {}).get('amount', 0),
                        'total_price': item.get('amounts', {}).get('total', {}).get('amount', 0),
                        'weight': item.get('weight', 0),
                        'weight_label': item.get('weight_label', ''),
                        'notes': item.get('notes', ''),
                        'options': options,
                        'main_image': main_image,
                        'codes': digital_codes,
                        'files': digital_files,
                        'reservations': reservations,
                        'product': {
                            'id': item_id,
                            'name': item.get('name', ''),
                            'description': item.get('notes', '')
                        }
                    }
                    
                    items.append(item_data)
                    
                except Exception as item_error:
                    logger.error(f"Error processing item: {str(item_error)}")
                    continue

            # الحصول على رابط QR Code
            final_qr_code_url = qr_code_url
            
            if not final_qr_code_url:
                final_qr_code_url = get_cached_qr_code_url(order_id_str)
            
            if not final_qr_code_url:
                logger.info(f"Generating new QR code for order: {order_id_str}")
                final_qr_code_url = generate_and_store_qr_code(order_id_str, 'salla', store_id)

            result = {
                'id': order_id_str,
                'order_items': items,
                'qr_code_url': final_qr_code_url,  # استخدام QR Code بدلاً من الباركود
                'barcode_order_id': order_id_str
            }
            
            logger.info(f"Order data processed successfully for order: {order_id_str}")
            return result
            
    except Exception as e:
        logger.error(f"Error in process_order_data: {str(e)}")
        return None

def bulk_generate_and_store_qr_codes(order_ids, order_type='salla', store_id=None):
    """إنشاء وتخزين QR Codes بشكل مجمع"""
    try:
        if not order_ids:
            logger.warning("No order IDs provided for bulk QR code generation")
            return {}
        
        logger.info(f"Starting bulk QR code generation for {len(order_ids)} orders, type: {order_type}")
        
        qr_codes_map = {}
        records_to_update = []
        lock = Lock()
        
        def generate_single_qr_code(order_id):
            order_id_str = str(order_id).strip()
            if not order_id_str:
                logger.warning("Empty order ID in bulk generation")
                return None, None
                
            try:
                logger.info(f"Generating QR code for order: {order_id_str}")
                
                # إنشاء QR Code باستخدام الرابط الكامل
                qr_code_url = generate_barcode(order_id_str)
                
                if qr_code_url:
                    logger.info(f"QR code generated successfully for order: {order_id_str}")
                    
                    with lock:
                        records_to_update.append({
                            'id': order_id_str,
                            'qr_code_url': qr_code_url,
                            'barcode_generated_at': datetime.utcnow(),
                            'store_id': store_id
                        })
                    return order_id_str, qr_code_url
                else:
                    logger.error(f"Failed to generate QR code for order: {order_id_str}")
                    return order_id_str, None
                    
            except Exception as e:
                logger.error(f"Error generating QR code for {order_id_str}: {str(e)}")
                return order_id_str, None
        
        # استخدام ThreadPoolExecutor لإنشاء QR Codes بشكل متزامن
        successful_generations = 0
        failed_generations = 0
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_id = {executor.submit(generate_single_qr_code, order_id): order_id for order_id in order_ids}
            
            for future in as_completed(future_to_id):
                order_id_str, qr_code_url = future.result()
                if qr_code_url:
                    qr_codes_map[order_id_str] = qr_code_url
                    successful_generations += 1
                else:
                    failed_generations += 1
        
        logger.info(f"QR code generation completed: {successful_generations} successful, {failed_generations} failed")
        
        # تخزين مجمع في PostgreSQL
        if records_to_update:
            try:
                with app_context():
                    engine = get_postgres_engine()
                    with engine.connect() as conn:
                        if order_type == 'salla':
                            # البحث عن reference_id للطلبات الموجودة
                            existing_orders_query = text("SELECT id, reference_id FROM salla_orders WHERE id = ANY(:order_ids)")
                            result = conn.execute(existing_orders_query, {'order_ids': [r['id'] for r in records_to_update]})
                            existing_orders = {row[0]: row[1] for row in result.fetchall()}
                            
                            query = text("""
                                INSERT INTO salla_orders (id, store_id, qr_code_url, barcode_generated_at, reference_id)
                                VALUES (:id, :store_id, :qr_code_url, :barcode_generated_at, :reference_id)
                                ON CONFLICT (id) 
                                DO UPDATE SET 
                                    qr_code_url = EXCLUDED.qr_code_url,
                                    barcode_generated_at = EXCLUDED.barcode_generated_at,
                                    reference_id = COALESCE(EXCLUDED.reference_id, salla_orders.reference_id)
                            """)
                            
                            # إضافة reference_id لكل سجل
                            for record in records_to_update:
                                record['reference_id'] = existing_orders.get(record['id'], record['id'])
                                
                        else:
                            query = text("""
                                INSERT INTO custom_order (id, qr_code_url, barcode_generated_at)
                                VALUES (:id, :qr_code_url, :barcode_generated_at)
                                ON CONFLICT (id) 
                                DO UPDATE SET 
                                    qr_code_url = EXCLUDED.qr_code_url,
                                    barcode_generated_at = EXCLUDED.barcode_generated_at
                            """)
                        
                        # التخزين المجمع
                        result = conn.execute(query, records_to_update)
                        conn.commit()
                        
                        logger.info(f"Successfully stored {len(records_to_update)} QR codes in database")
                        
            except Exception as e:
                logger.error(f"Error in bulk QR code storage: {str(e)}")
                # محاولة التحديث الفردي للطلبات التي فشل تخزينها
                successful_storages = 0
                failed_storages = 0
                
                try:
                    with app_context():
                        engine = get_postgres_engine()
                        with engine.connect() as conn:
                            if order_type == 'salla':
                                update_query = text("""
                                    UPDATE salla_orders 
                                    SET qr_code_url = :qr_code_url, 
                                        barcode_generated_at = :barcode_generated_at
                                    WHERE id = :id
                                """)
                            else:
                                update_query = text("""
                                    UPDATE custom_order 
                                    SET qr_code_url = :qr_code_url, 
                                        barcode_generated_at = :barcode_generated_at
                                    WHERE id = :id
                                """)
                            
                            for record in records_to_update:
                                try:
                                    conn.execute(update_query, {
                                        'id': record['id'],
                                        'qr_code_url': record['qr_code_url'],
                                        'barcode_generated_at': record['barcode_generated_at']
                                    })
                                    successful_storages += 1
                                except Exception as single_update_error:
                                    logger.error(f"Error updating QR code for {record['id']}: {str(single_update_error)}")
                                    failed_storages += 1
                            
                            conn.commit()
                            logger.info(f"Individual update completed: {successful_storages} successful, {failed_storages} failed")
                            
                except Exception as update_error:
                    logger.error(f"Error in bulk QR code update: {str(update_error)}")
        
        # التحقق النهائي من QR Codes المخزنة
        logger.info(f"Final QR codes map contains {len(qr_codes_map)} entries")
        
        # تسجيل عينة من QR Codes للتأكد
        sample_orders = list(qr_codes_map.keys())[:3] if qr_codes_map else []
        for order_id in sample_orders:
            logger.info(f"Sample - Order {order_id}: QR code generated successfully")
        
        return qr_codes_map
            
    except Exception as e:
        logger.error(f"Error in bulk_generate_and_store_qr_codes: {str(e)}")
        return {}
def get_barcodes_for_orders(order_ids):
    """واجهة متوافقة مع الكود القديم (ترجع QR Codes الآن)"""
    return get_qr_codes_for_orders_optimized(order_ids)

def generate_and_store_barcode(order_id, order_type='salla', store_id=None):
    """واجهة متوافقة مع الكود القديم (تستخدم QR Code الآن)"""
    return generate_and_store_qr_code(order_id, order_type, store_id)

def get_cached_barcode_data(order_id):
    """واجهة متوافقة مع الكود القديم (ترجع QR Code الآن)"""
    return get_cached_qr_code_url(order_id)

# ... (بقية الدوال كما هي بدون تغيير)
def get_main_image(item):
    """استخراج الصورة الرئيسية"""
    try:
        image_sources = [
            item.get('product_thumbnail'),
            item.get('thumbnail'),
            item.get('image'),
            item.get('url'),
            item.get('image_url'),
            item.get('picture')
        ]
        
        for image_url in image_sources:
            if image_url and isinstance(image_url, str) and image_url.strip():
                final_url = image_url.strip()
                if not final_url.startswith(('http://', 'https://')):
                    return f"https://cdn.salla.sa{final_url}"
                return final_url
        
        images = item.get('images', [])
        if images and isinstance(images, list):
            for image in images:
                if isinstance(image, dict):
                    image_url = image.get('image') or image.get('url')
                    if image_url and isinstance(image_url, str) and image_url.strip():
                        final_url = image_url.strip()
                        if not final_url.startswith(('http://', 'https://')):
                            return f"https://cdn.salla.sa{final_url}"
                        return final_url
        
        return ''
        
    except Exception as e:
        logger.error(f"Error in get_main_image: {str(e)}")
        return ''
def format_date(date_input):
    """تنسيق التاريخ مع معالجة جميع أنواع المدخلات"""
    try:
        if not date_input:
            return 'غير معروف'
        
        # إذا كان كائن datetime
        if isinstance(date_input, datetime):
            return date_input.strftime('%Y-%m-%d %H:%M')
        
        # إذا كان سلسلة نصية
        if isinstance(date_input, str):
            # إزالة الأجزاء الدقيقة إذا وجدت
            date_clean = date_input.split('.')[0] if '.' in date_input else date_input
            date_clean = date_clean.split('+')[0] if '+' in date_clean else date_clean
            date_clean = date_clean.split('Z')[0] if 'Z' in date_clean else date_clean
            
            # محاولة التنسيقات المختلفة
            for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d']:
                try:
                    dt = datetime.strptime(date_clean, fmt)
                    return dt.strftime('%Y-%m-%d %H:%M')
                except ValueError:
                    continue
        
        # إذا فشلت جميع المحاولات، إرجاع التمثيل النصي
        return str(date_input) if date_input else 'غير معروف'
        
    except Exception as e:
        logger.warning(f"Failed to format date: {str(e)}")
        if isinstance(date_input, datetime):
            return date_input.strftime('%Y-%m-%d %H:%M')
        return str(date_input) if date_input else 'غير معروف'
def process_order_data(order_id, items_data, barcode_data=None, store_id=None):
    """معالجة بيانات الطلب مع استخدام الباركود المخزن - معدل"""
    try:
        with app_context():
            order_id_str = str(order_id).strip()
            if not order_id_str:
                logger.error("Empty order ID in process_order_data")
                return None
                
            # تسجيل معالجة الطلب
            logger.info(f"Processing order data for order: {order_id_str}")
                
            items = []
            
            for index, item in enumerate(items_data):
                try:
                    item_id = item.get('id') or f"temp_{index}"
                    main_image = get_main_image(item)
                    
                    options = []
                    item_options = item.get('options', [])
                    if isinstance(item_options, list):
                        for option in item_options:
                            raw_value = option.get('value', '')
                            display_value = 'غير محدد'
                            
                            if isinstance(raw_value, dict):
                                display_value = raw_value.get('name') or raw_value.get('value') or str(raw_value)
                            elif isinstance(raw_value, list):
                                values_list = [str(opt.get('name') or opt.get('value') or str(opt)) 
                                             for opt in raw_value if isinstance(opt, (dict, str))]
                                display_value = ', '.join(values_list)
                            else:
                                display_value = str(raw_value) if raw_value else 'غير محدد'
                            
                            options.append({
                                'name': option.get('name', ''),
                                'value': display_value,
                                'type': option.get('type', '')
                            })
                    
                    digital_codes = [{'code': code.get('code', ''), 'status': code.get('status', 'غير معروف')} 
                                    for code in item.get('codes', []) if isinstance(code, dict)]
                    
                    digital_files = [{'url': file.get('url', ''), 'name': file.get('name', ''), 'size': file.get('size', 0)} 
                                   for file in item.get('files', []) if isinstance(file, dict)]
                    
                    reservations = [{'id': res.get('id'), 'from': res.get('from', ''), 'to': res.get('to', ''), 'date': res.get('date', '')} 
                                  for res in item.get('reservations', []) if isinstance(res, dict)]
                    
                    item_data = {
                        'id': item_id,
                        'name': item.get('name', ''),
                        'sku': item.get('sku', ''),
                        'quantity': item.get('quantity', 0),
                        'currency': item.get('currency', 'SAR'),
                        'price': {
                            'amount': item.get('amounts', {}).get('price_without_tax', {}).get('amount', 0),
                            'currency': item.get('currency', 'SAR')
                        },
                        'tax_percent': item.get('amounts', {}).get('tax', {}).get('percent', '0.00'),
                        'tax_amount': item.get('amounts', {}).get('tax', {}).get('amount', {}).get('amount', 0),
                        'total_price': item.get('amounts', {}).get('total', {}).get('amount', 0),
                        'weight': item.get('weight', 0),
                        'weight_label': item.get('weight_label', ''),
                        'notes': item.get('notes', ''),
                        'options': options,
                        'main_image': main_image,
                        'codes': digital_codes,
                        'files': digital_files,
                        'reservations': reservations,
                        'product': {
                            'id': item_id,
                            'name': item.get('name', ''),
                            'description': item.get('notes', '')
                        }
                    }
                    
                    items.append(item_data)
                    
                except Exception as item_error:
                    logger.error(f"Error processing item: {str(item_error)}")
                    continue

            # الحصول على الباركود - التأكد من استخدام رقم الطلب الصحيح
            final_barcode_data = barcode_data
            
            if not final_barcode_data:
                final_barcode_data = get_cached_barcode_data(order_id_str)
            
            if not final_barcode_data:
                logger.info(f"Generating new barcode for order: {order_id_str}")
                final_barcode_data = generate_and_store_barcode(order_id_str, 'salla', store_id)
            
            if not final_barcode_data:
                logger.warning(f"Using fallback barcode generation for order: {order_id_str}")
                final_barcode_data = generate_barcode(order_id_str)

            result = {
                'id': order_id_str,
                'order_items': items,
                'barcode': final_barcode_data,
                'barcode_order_id': order_id_str  # إضافة حقل لتتبع رقم الطلب المستخدم
            }
            
            logger.info(f"Order data processed successfully for order: {order_id_str}")
            return result
            
    except Exception as e:
        logger.error(f"Error in process_order_data: {str(e)}")
        return None
@contextmanager
def db_session_scope():
    """مدير سياق لإدارة جلسات قاعدة البيانات"""
    try:
        yield db.session
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Database session error: {str(e)}")
        raise
    finally:
        db.session.remove()

def process_orders_concurrently(order_ids, access_token, max_workers=10, app=None):
    """معالجة الطلبات بشكل متزامن مع إدارة سياق التطبيق"""
    from .config import Config
    
    if not order_ids:
        return []
    
    # إذا لم يتم تمرير التطبيق، نحاول استخدام current_app
    if app is None:
        try:
            app = current_app._get_current_object()
        except RuntimeError:
            # إذا كنا خارج سياق التطبيق، نستخدم النسخة المعدلة
            return process_orders_concurrently_fallback(order_ids, access_token, max_workers)
    
    # جلب جميع الباركودات مسبقاً في استعلام واحد
    barcodes_map = get_barcodes_for_orders(order_ids)
    
    orders = []
    successful_orders = 0
    failed_orders = 0
    lock = Lock()

    def process_single_order(order_id):
        """معالجة طلب واحد مع ضمان وجود سياق التطبيق"""
        nonlocal successful_orders, failed_orders
        order_id_str = str(order_id).strip()
        if not order_id_str:
            return None
            
        # استخدام سياق التطبيق الممرر
        with app.app_context():
            try:
                # إنشاء جلسة مستقلة لكل خيط
                session = create_session()
                headers = {
                    'Authorization': f'Bearer {access_token}',
                    'Accept': 'application/json'
                }
                
                # جلب بيانات الطلب
                order_response = session.get(
                    f"{Config.SALLA_ORDERS_API}/{order_id_str}",
                    headers=headers,
                    timeout=15
                )
                
                if order_response.status_code != 200:
                    with lock:
                        failed_orders += 1
                    return None
                    
                order_data = order_response.json().get('data', {})
                
                # جلب بيانات العناصر
                items_response = session.get(
                    f"{Config.SALLA_BASE_URL}/orders/items",
                    params={'order_id': order_id_str},
                    headers=headers,
                    timeout=15
                )
                
                items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
                
                # استخدام الباركود المخزن مسبقاً
                barcode_data = barcodes_map.get(order_id_str)
                
                # استخراج store_id من بيانات الطلب إذا أمكن
                store_id = order_data.get('store_id')
                
                # معالجة بيانات الطلب داخل سياق التطبيق
                processed_order = process_order_data(order_id_str, items_data, barcode_data, store_id)
                
                if processed_order:
                    processed_order['reference_id'] = order_data.get('reference_id', order_id_str)
                    processed_order['customer'] = order_data.get('customer', {})
                    processed_order['created_at'] = format_date(order_data.get('created_at', ''))
                    
                    with lock:
                        successful_orders += 1
                    return processed_order
                else:
                    with lock:
                        failed_orders += 1
                    return None
                    
            except Exception as e:
                with lock:
                    failed_orders += 1
                logger.error(f"Error processing order {order_id_str}: {str(e)}")
                return None
            finally:
                try:
                    session.close()
                except:
                    pass

    # استخدام ThreadPoolExecutor للمعالجة المتزامنة
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # تقديم جميع المهام مرة واحدة
        future_to_order = {executor.submit(process_single_order, order_id): order_id for order_id in order_ids}
        
        # جمع النتائج عند اكتمالها
        for future in as_completed(future_to_order):
            result = future.result()
            if result:
                orders.append(result)

    logger.info(f"Order processing completed: {successful_orders} successful, {failed_orders} failed")
    return orders

def process_orders_concurrently_fallback(order_ids, access_token, max_workers=10):
    """نسخة احتياطية للمعالجة المتزامنة بدون سياق تطبيق"""
    from .config import Config
    
    if not order_ids:
        return []
    
    orders = []
    successful_orders = 0
    failed_orders = 0
    lock = Lock()

    def process_single_order_fallback(order_id):
        """معالجة طلب واحد بدون سياق تطبيق"""
        nonlocal successful_orders, failed_orders
        order_id_str = str(order_id).strip()
        if not order_id_str:
            return None
            
        try:
            # إنشاء جلسة مستقلة لكل خيط
            session = create_session()
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            }
            
            # جلب بيانات الطلب
            order_response = session.get(
                f"{Config.SALLA_ORDERS_API}/{order_id_str}",
                headers=headers,
                timeout=15
            )
            
            if order_response.status_code != 200:
                with lock:
                    failed_orders += 1
                return None
                
            order_data = order_response.json().get('data', {})
            
            # جلب بيانات العناصر
            items_response = session.get(
                f"{Config.SALLA_BASE_URL}/orders/items",
                params={'order_id': order_id_str},
                headers=headers,
                timeout=15
            )
            
            items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
            
            # معالجة البيانات بدون استخدام قاعدة البيانات
            try:
                order_id_str = str(order_id).strip()
                if not order_id_str:
                    return None
                    
                items = []
                
                for index, item in enumerate(items_data):
                    try:
                        item_id = item.get('id') or f"temp_{index}"
                        main_image = get_main_image(item)
                        
                        options = []
                        item_options = item.get('options', [])
                        if isinstance(item_options, list):
                            for option in item_options:
                                raw_value = option.get('value', '')
                                display_value = 'غير محدد'
                                
                                if isinstance(raw_value, dict):
                                    display_value = raw_value.get('name') or raw_value.get('value') or str(raw_value)
                                elif isinstance(raw_value, list):
                                    values_list = [str(opt.get('name') or opt.get('value') or str(opt)) 
                                                 for opt in raw_value if isinstance(opt, (dict, str))]
                                    display_value = ', '.join(values_list)
                                else:
                                    display_value = str(raw_value) if raw_value else 'غير محدد'
                                
                                options.append({
                                    'name': option.get('name', ''),
                                    'value': display_value,
                                    'type': option.get('type', '')
                                })
                        
                        item_data = {
                            'id': item_id,
                            'name': item.get('name', ''),
                            'sku': item.get('sku', ''),
                            'quantity': item.get('quantity', 0),
                            'currency': item.get('currency', 'SAR'),
                            'price': {
                                'amount': item.get('amounts', {}).get('price_without_tax', {}).get('amount', 0),
                                'currency': item.get('currency', 'SAR')
                            },
                            'main_image': main_image,
                            'options': options,
                        }
                        
                        items.append(item_data)
                        
                    except Exception as item_error:
                        logger.error(f"Error processing item: {str(item_error)}")
                        continue

                # إنشاء الباركود مباشرة بدون تخزين
                barcode_data = generate_barcode(order_id_str)

                result = {
                    'id': order_id_str,
                    'order_items': items,
                    'barcode': barcode_data,
                    'reference_id': order_data.get('reference_id', order_id_str),
                    'customer': order_data.get('customer', {}),
                    'created_at': format_date(order_data.get('created_at', ''))
                }
                
                with lock:
                    successful_orders += 1
                return result
                
            except Exception as process_error:
                logger.error(f"Error processing order data: {str(process_error)}")
                with lock:
                    failed_orders += 1
                return None
                
        except Exception as e:
            with lock:
                failed_orders += 1
            logger.error(f"Error processing order {order_id_str}: {str(e)}")
            return None
        finally:
            try:
                session.close()
            except:
                pass

    # استخدام ThreadPoolExecutor للمعالجة المتزامنة
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # تقديم جميع المهام مرة واحدة
        future_to_order = {executor.submit(process_single_order_fallback, order_id): order_id for order_id in order_ids}
        
        # جمع النتائج عند اكتمالها
        for future in as_completed(future_to_order):
            result = future.result()
            if result:
                orders.append(result)

    logger.info(f"Order processing completed (fallback): {successful_orders} successful, {failed_orders} failed")
    return orders

def process_orders_sequentially(order_ids, access_token):
    """واجهة متوافقة مع الكود القديم (تستخدم المعالجة المتزامنة)"""
    # استخدام 10 عمال كحد افتراضي (يمكن تعديله حسب احتياجات الخادم)
    return process_orders_concurrently(order_ids, access_token, max_workers=10)

def get_salla_categories(access_token):
    from .config import Config
    
    session = create_session()
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    try:
        response = session.get(Config.SALLA_CATEGORIES_API, headers=headers)
        response.raise_for_status()
        return response.json().get('data', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching categories from Salla: {e}")
        return []
    finally:
        session.close()

def humanize_time(dt):
    """تحويل التاريخ إلى نص مقروء"""
    now = datetime.utcnow()
    diff = now - dt
    
    seconds = diff.total_seconds()
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24
    months = days // 30
    years = months // 12
    
    if years > 0:
        return f"منذ {int(years)} سنة" if years > 1 else "منذ سنة"
    elif months > 0:
        return f"منذ {int(months)} شهر" if months > 1 else "منذ شهر"
    elif days > 0:
        return f"منذ {int(days)} يوم" if days > 1 else "منذ يوم"
    elif hours > 0:
        return f"منذ {int(hours)} ساعة" if hours > 1 else "منذ ساعة"
    elif minutes > 0:
        return f"منذ {int(minutes)} دقيقة" if minutes > 1 else "منذ دقيقة"
    else:
        return "الآن"

# دوال مساعدة إضافية للأداء
def optimize_database_connections():
    """تحسين اتصالات قاعدة البيانات"""
    try:
        # ضبط إعدادات PostgreSQL للأداء
        engine = get_postgres_engine()
        with engine.connect() as conn:
            # تحسين إعدادات الجلسة
            conn.execute(text("SET work_mem = '256MB'"))
            conn.execute(text("SET maintenance_work_mem = '512MB'"))
            conn.execute(text("SET shared_buffers = '256MB'"))
            conn.commit()
        logger.info("Database connections optimized")
    except Exception as e:
        logger.warning(f"Could not optimize database connections: {str(e)}")

def cleanup_resources():
    """تنظيف الموارد عند إغلاق التطبيق"""
    global _postgres_engine
    try:
        if _postgres_engine:
            _postgres_engine.dispose()
            _postgres_engine = None
        logger.info("Database resources cleaned up")
    except Exception as e:
        logger.error(f"Error cleaning up resources: {str(e)}")
        
def verify_barcode_order_ids():
    """فحص جميع الباركودات للتأكد من استخدام أرقام الطلبات الصحيحة"""
    try:
        with app_context():
            engine = get_postgres_engine()
            with engine.connect() as conn:
                # الحصول على جميع الباركودات
                query = text("SELECT id, barcode_data FROM salla_orders WHERE barcode_data IS NOT NULL")
                result = conn.execute(query)
                rows = result.fetchall()
                
                problematic_orders = []
                for row in rows:
                    order_id = row[0]
                    barcode_data = row[1]
                    
                    # التحقق مما إذا كان الباركود يحتوي على رقم الطلب الصحيح
                    if barcode_data and order_id not in barcode_data:
                        problematic_orders.append({
                            'order_id': order_id,
                            'barcode_data_sample': barcode_data[:100] + '...' if len(barcode_data) > 100 else barcode_data
                        })
                
                if problematic_orders:
                    logger.warning(f"Found {len(problematic_orders)} orders with potential barcode ID mismatch")
                    for order in problematic_orders:
                        logger.warning(f"Order {order['order_id']} has barcode that doesn't match order ID")
                
                return problematic_orders
                
    except Exception as e:
        logger.error(f"Error in verify_barcode_order_ids: {str(e)}")
        return []
def get_orders_from_local_database(order_ids, store_id):
    """جلب الطلبات من قاعدة البيانات المحلية باستخدام full_order_data"""
    try:
        logger.info(f"🔍 جلب {len(order_ids)} طلب من قاعدة البيانات المحلية")
        
        # تصفية order_ids لضمان أنها نصية
        order_ids_str = [str(oid).strip() for oid in order_ids if str(oid).strip()]
        
        if not order_ids_str:
            logger.warning("❌ لا توجد معرفات طلبات صالحة")
            return []
        
        # جلب الطلبات من قاعدة البيانات
        salla_orders = SallaOrder.query.filter(
            SallaOrder.id.in_(order_ids_str),
            SallaOrder.store_id == store_id
        ).all()
        
        logger.info(f"✅ تم العثور على {len(salla_orders)} طلب في قاعدة البيانات")
        
        processed_orders = []
        
        for order in salla_orders:
            try:
                # استخدام full_order_data المخزن محلياً
                order_data = order.full_order_data
                
                if not order_data:
                    logger.warning(f"⚠️ الطلب {order.id} لا يحتوي على full_order_data")
                    # يمكننا إنشاء بيانات أساسية من المعلومات المتاحة
                    order_data = {
                        'customer': {
                            'first_name': '',
                            'last_name': decrypt_data(order.customer_name) if order.customer_name else 'عميل غير معروف'
                        },
                        'reference_id': order.id,
                        'amounts': {
                            'total': {'amount': order.total_amount, 'currency': order.currency}
                        },
                        'status': {'name': 'غير معروف'}
                    }
                    items_data = []
                else:
                    # استخراج العناصر من البيانات المحلية
                    items_data = order_data.get('items', [])
                
                # معالجة بيانات الطلب باستخدام البيانات المحلية
                processed_order = process_order_from_local_data(order, order_data, items_data)
                
                if processed_order:
                    processed_orders.append(processed_order)
                    logger.info(f"✅ تم معالجة الطلب {order.id} من البيانات المحلية")
                else:
                    logger.warning(f"❌ فشل في معالجة الطلب {order.id}")
                    
            except Exception as e:
                logger.error(f"❌ خطأ في معالجة الطلب {order.id}: {str(e)}")
                continue
        
        logger.info(f"🎉 تم معالجة {len(processed_orders)} طلب بنجاح من البيانات المحلية")
        return processed_orders
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب الطلبات من قاعدة البيانات: {str(e)}")
        return []