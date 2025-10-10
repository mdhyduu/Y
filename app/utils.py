from datetime import datetime
import os
import re
import base64
import logging
from io import BytesIO
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import current_app, request
from sqlalchemy import text, create_engine
from sqlalchemy.pool import QueuePool
import qrcode
from qrcode.image.pil import PilImage

from .models import db, User, Employee, CustomOrder, SallaOrder
from .services.storage_service import do_storage
from .config import Config

# إعداد المسجل
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'static/uploads/custom_orders'

# متغيرات عالمية
_postgres_engine = None


@contextmanager
def app_context():
    """مدير سياق للتطبيق لاستخدامه في الخيوط"""
    app = current_app._get_current_object()
    with app.app_context():
        yield


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


def create_postgresql_engine():
    """إنشاء محرك PostgreSQL محسن للأداء"""
    try:
        database_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI')
        if not database_uri:
            database_uri = 'postgresql://username:password@localhost:5432/your_database'
        
        engine = create_engine(
            database_uri,
            poolclass=QueuePool,
            pool_size=20,
            max_overflow=30,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo_pool=False
        )
        return engine
    except Exception as e:
        logger.error(f"Error creating PostgreSQL engine: {str(e)}")
        return db.engine


def get_postgres_engine():
    """الحصول على محرك PostgreSQL مع تهيئة lazy"""
    global _postgres_engine
    if _postgres_engine is None:
        _postgres_engine = create_postgresql_engine()
    return _postgres_engine


def allowed_file(filename):
    """التحقق من صحة امتداد الملف"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_next_order_number():
    """إنشاء رقم طلب تلقائي يبدأ من 1000"""
    try:
        with app_context():
            last_order = CustomOrder.query.order_by(CustomOrder.id.desc()).first()
            if last_order and last_order.order_number:
                try:
                    return str(int(last_order.order_number) + 1)
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


def clean_data(data: str) -> str:
    """تنظيف البيانات من الرموز الغير مرغوبة"""
    return re.sub(r'[^A-Za-z0-9\s\-]', '', str(data)).strip()


def generate_barcode(data, dpi=300):
    """إنشاء QR Code"""
    try:
        data_str = str(data).strip()
        if not data_str:
            return None

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(data_str)
        qr.make(fit=True)

        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer, format='PNG', optimize=True)
        buffer.seek(0)

        qr_code_url = do_storage.upload_qr_code(buffer, data_str, folder='qrcodes')
        
        if qr_code_url:
            logger.info(f"QR Code generated and uploaded successfully for: {data_str}")
            return qr_code_url
        else:
            logger.error(f"Failed to upload QR Code for: {data_str}")
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
            
            qr_code_url = generate_barcode(order_id_str)
            
            if not qr_code_url:
                logger.error(f"Failed to generate QR code for order: {order_id_str}")
                return None
            
            _store_qr_code_in_db(order_id_str, qr_code_url, order_type, store_id)
            return qr_code_url
                
    except Exception as e:
        logger.error(f"Error in generate_and_store_qr_code: {str(e)}")
        return None


def _store_qr_code_in_db(order_id, qr_code_url, order_type, store_id):
    """تخزين رابط QR Code في قاعدة البيانات"""
    try:
        engine = get_postgres_engine()
        with engine.connect() as conn:
            if order_type == 'salla':
                existing_order_query = text("SELECT id, reference_id FROM salla_orders WHERE id = :id")
                result = conn.execute(existing_order_query, {'id': order_id})
                existing_order = result.fetchone()
                
                reference_id = existing_order[1] if existing_order else order_id
                
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
                    'id': order_id,
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
                    'id': order_id,
                    'qr_code_url': qr_code_url,
                    'barcode_generated_at': datetime.utcnow()
                }
            
            conn.execute(query, params)
            conn.commit()
            logger.info(f"QR Code URL stored successfully for order: {order_id}")
            
    except Exception as e:
        logger.error(f"Error storing QR code URL: {str(e)}")
        _update_qr_code_in_db(order_id, qr_code_url, order_type)


def _update_qr_code_in_db(order_id, qr_code_url, order_type):
    """تحديث رابط QR Code في قاعدة البيانات (fallback)"""
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
                'id': order_id,
                'qr_code_url': qr_code_url,
                'barcode_generated_at': datetime.utcnow()
            })
            conn.commit()
            logger.info(f"QR Code URL updated successfully for order: {order_id}")
    except Exception as e:
        logger.error(f"Error updating QR code URL: {str(e)}")


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
        return _get_qr_codes_fallback(order_ids)


def _get_qr_codes_fallback(order_ids):
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
        logger.error(f"Error in get_qr_codes_fallback: {str(e)}")
        return {}


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


def _process_item_data(item, index):
    """معالجة بيانات عنصر فردي"""
    try:
        item_id = item.get('id') or f"temp_{index}"
        main_image = get_main_image(item)
        
        # معالجة الخيارات
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
        
        # معالجة البيانات الرقمية
        digital_codes = [{'code': code.get('code', ''), 'status': code.get('status', 'غير معروف')} 
                        for code in item.get('codes', []) if isinstance(code, dict)]
        
        digital_files = [{'url': file.get('url', ''), 'name': file.get('name', ''), 'size': file.get('size', 0)} 
                       for file in item.get('files', []) if isinstance(file, dict)]
        
        reservations = [{'id': res.get('id'), 'from': res.get('from', ''), 'to': res.get('to', ''), 'date': res.get('date', '')} 
                      for res in item.get('reservations', []) if isinstance(res, dict)]
        
        return {
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
        
    except Exception as e:
        logger.error(f"Error processing item: {str(e)}")
        return None


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
                processed_item = _process_item_data(item, index)
                if processed_item:
                    items.append(processed_item)

            # الحصول على رابط QR Code
            final_qr_code_url = qr_code_url or get_cached_qr_code_url(order_id_str)
            
            if not final_qr_code_url:
                logger.info(f"Generating new QR code for order: {order_id_str}")
                final_qr_code_url = generate_and_store_qr_code(order_id_str, 'salla', store_id)

            result = {
                'id': order_id_str,
                'order_items': items,
                'qr_code_url': final_qr_code_url,
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
                return None, None
                
            try:
                qr_code_url = generate_barcode(order_id_str)
                
                if qr_code_url:
                    with lock:
                        records_to_update.append({
                            'id': order_id_str,
                            'qr_code_url': qr_code_url,
                            'barcode_generated_at': datetime.utcnow(),
                            'store_id': store_id
                        })
                    return order_id_str, qr_code_url
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
            _bulk_store_qr_codes(records_to_update, order_type)
        
        logger.info(f"Final QR codes map contains {len(qr_codes_map)} entries")
        return qr_codes_map
            
    except Exception as e:
        logger.error(f"Error in bulk_generate_and_store_qr_codes: {str(e)}")
        return {}


def _bulk_store_qr_codes(records_to_update, order_type):
    """تخزين مجمع لرموز QR في قاعدة البيانات"""
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
                
                conn.execute(query, records_to_update)
                conn.commit()
                logger.info(f"Successfully stored {len(records_to_update)} QR codes in database")
                
    except Exception as e:
        logger.error(f"Error in bulk QR code storage: {str(e)}")
        _bulk_update_qr_codes(records_to_update, order_type)


def _bulk_update_qr_codes(records_to_update, order_type):
    """تحديث مجمع لرموز QR في قاعدة البيانات (fallback)"""
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
                    except Exception as e:
                        logger.error(f"Error updating QR code for {record['id']}: {str(e)}")
                        failed_storages += 1
                
                conn.commit()
                logger.info(f"Individual update completed: {successful_storages} successful, {failed_storages} failed")
                
    except Exception as e:
        logger.error(f"Error in bulk QR code update: {str(e)}")


# دوال التوافق مع الكود القديم
def get_barcodes_for_orders(order_ids):
    """واجهة متوافقة مع الكود القديم (ترجع QR Codes الآن)"""
    return get_qr_codes_for_orders_optimized(order_ids)


def generate_and_store_barcode(order_id, order_type='salla', store_id=None):
    """واجهة متوافقة مع الكود القديم (تستخدم QR Code الآن)"""
    return generate_and_store_qr_code(order_id, order_type, store_id)


def get_cached_barcode_data(order_id):
    """واجهة متوافقة مع الكود القديم (ترجع QR Code الآن)"""
    return get_cached_qr_code_url(order_id)


def format_date(date_input):
    """تنسيق التاريخ مع معالجة جميع أنواع المدخلات"""
    try:
        if not date_input:
            return 'غير معروف'
        
        if isinstance(date_input, datetime):
            return date_input.strftime('%Y-%m-%d %H:%M')
        
        if isinstance(date_input, str):
            date_clean = date_input.split('.')[0] if '.' in date_input else date_input
            date_clean = date_clean.split('+')[0] if '+' in date_clean else date_clean
            date_clean = date_clean.split('Z')[0] if 'Z' in date_clean else date_clean
            
            for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d']:
                try:
                    dt = datetime.strptime(date_clean, fmt)
                    return dt.strftime('%Y-%m-%d %H:%M')
                except ValueError:
                    continue
        
        return str(date_input) if date_input else 'غير معروف'
        
    except Exception as e:
        logger.warning(f"Failed to format date: {str(e)}")
        if isinstance(date_input, datetime):
            return date_input.strftime('%Y-%m-%d %H:%M')
        return str(date_input) if date_input else 'غير معروف'


def process_orders_concurrently(order_ids, access_token, max_workers=10, app=None):
    """معالجة الطلبات بشكل متزامن مع إدارة سياق التطبيق"""
    if not order_ids:
        return []
    
    if app is None:
        try:
            app = current_app._get_current_object()
        except RuntimeError:
            return _process_orders_fallback(order_ids, access_token, max_workers)
    
    # جلب جميع QR codes مسبقاً
    qr_codes_map = get_barcodes_for_orders(order_ids)
    
    orders = []
    successful_orders = 0
    failed_orders = 0
    lock = Lock()

    def process_single_order(order_id):
        nonlocal successful_orders, failed_orders
        order_id_str = str(order_id).strip()
        if not order_id_str:
            return None
            
        with app.app_context():
            try:
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
                
                qr_code_data = qr_codes_map.get(order_id_str)
                store_id = order_data.get('store_id')
                
                processed_order = process_order_data(order_id_str, items_data, qr_code_data, store_id)
                
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
        future_to_order = {executor.submit(process_single_order, order_id): order_id for order_id in order_ids}
        
        for future in as_completed(future_to_order):
            result = future.result()
            if result:
                orders.append(result)

    logger.info(f"Order processing completed: {successful_orders} successful, {failed_orders} failed")
    return orders


def _process_orders_fallback(order_ids, access_token, max_workers=10):
    """نسخة احتياطية للمعالجة المتزامنة بدون سياق تطبيق"""
    if not order_ids:
        return []
    
    orders = []
    successful_orders = 0
    failed_orders = 0
    lock = Lock()

    def process_single_order_fallback(order_id):
        nonlocal successful_orders, failed_orders
        order_id_str = str(order_id).strip()
        if not order_id_str:
            return None
            
        try:
            session = create_session()
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            }
            
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
            
            items_response = session.get(
                f"{Config.SALLA_BASE_URL}/orders/items",
                params={'order_id': order_id_str},
                headers=headers,
                timeout=15
            )
            
            items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
            
            return _process_order_data_simple(order_id_str, items_data, order_data)
                
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

    def _process_order_data_simple(order_id_str, items_data, order_data):
        """معالجة مبسطة لبيانات الطلب بدون استخدام قاعدة البيانات"""
        try:
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
                    
                except Exception as e:
                    logger.error(f"Error processing item: {str(e)}")
                    continue

            qr_code_data = generate_barcode(order_id_str)

            result = {
                'id': order_id_str,
                'order_items': items,
                'qr_code_url': qr_code_data,
                'reference_id': order_data.get('reference_id', order_id_str),
                'customer': order_data.get('customer', {}),
                'created_at': format_date(order_data.get('created_at', ''))
            }
            
            with lock:
                successful_orders += 1
            return result
            
        except Exception as e:
            logger.error(f"Error processing order data: {str(e)}")
            with lock:
                failed_orders += 1
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_order = {executor.submit(process_single_order_fallback, order_id): order_id for order_id in order_ids}
        
        for future in as_completed(future_to_order):
            result = future.result()
            if result:
                orders.append(result)

    logger.info(f"Order processing completed (fallback): {successful_orders} successful, {failed_orders} failed")
    return orders


def process_orders_sequentially(order_ids, access_token):
    """واجهة متوافقة مع الكود القديم (تستخدم المعالجة المتزامنة)"""
    return process_orders_concurrently(order_ids, access_token, max_workers=10)


def get_salla_categories(access_token):
    """جلب التصنيفات من Salla"""
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


def optimize_database_connections():
    """تحسين اتصالات قاعدة البيانات"""
    try:
        engine = get_postgres_engine()
        with engine.connect() as conn:
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


def get_orders_from_local_database(order_ids, store_id):
    """جلب الطلبات من قاعدة البيانات المحلية باستخدام full_order_data"""
    try:
        logger.info(f"🔍 جلب {len(order_ids)} طلب من قاعدة البيانات المحلية")
        
        order_ids_str = [str(oid).strip() for oid in order_ids if str(oid).strip()]
        
        if not order_ids_str:
            logger.warning("❌ لا توجد معرفات طلبات صالحة")
            return []
        
        salla_orders = SallaOrder.query.filter(
            SallaOrder.id.in_(order_ids_str),
            SallaOrder.store_id == store_id
        ).all()
        
        logger.info(f"✅ تم العثور على {len(salla_orders)} طلب في قاعدة البيانات")
        
        processed_orders = []
        
        for order in salla_orders:
            try:
                order_data = order.full_order_data
                
                if not order_data:
                    logger.warning(f"⚠️ الطلب {order.id} لا يحتوي على full_order_data")
                    order_data = {
                        'customer': {
                            'first_name': '',
                            'last_name': 'عميل غير معروف'
                        },
                        'reference_id': order.id,
                        'amounts': {
                            'total': {'amount': order.total_amount, 'currency': order.currency}
                        },
                        'status': {'name': 'غير معروف'}
                    }
                    items_data = []
                else:
                    items_data = order_data.get('items', [])
                
                # هنا تحتاج لإضافة دالة process_order_from_local_data إذا كانت موجودة
                # processed_order = process_order_from_local_data(order, order_data, items_data)
                processed_order = None  # مؤقت - تحتاج للتعديل
                
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