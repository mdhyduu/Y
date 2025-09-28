from datetime import datetime
import os
import barcode
from barcode.writer import ImageWriter
from flask import current_app, request
from .models import db, User, Employee, CustomOrder, SallaOrder
import logging
from io import BytesIO
import base64
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

def generate_barcode(data):
    """إنشاء باركود مع معالجة الأخطاء وضمان استخدام رقم الطلب الصحيح"""
    try:
        # التأكد من أن البيانات هي رقم الطلب الحقيقي
        data_str = str(data).strip()
        if not data_str:
            logger.error("Empty data provided for barcode generation")
            return None
        
        # تنظيف البيانات من أي رموز غير مرغوب فيها
        # إزالة أي رموز غير رقمية باستثناء الأحرف الأساسية
        import re
        cleaned_data = re.sub(r'[^\w\s-]', '', data_str)  # إزالة الرموز الخاصة
        cleaned_data = cleaned_data.strip()
        
        if not cleaned_data:
            logger.error("Data is empty after cleaning")
            return None
            
        # تسجيل رقم الطلب المستخدم لإنشاء الباركود
        logger.info(f"Generating barcode for order ID: {cleaned_data} (original: {data_str})")
        
        # استخدام code128 كخيار أول (يدعم الأحرف الرقمية فقط بشكل أفضل)
        barcode_type = 'code128'
        
        try:
            code_class = barcode.get_barcode_class(barcode_type)
            writer = ImageWriter()
            
            # إعدادات محسنة للكاتب
            writer.set_options({
                'write_text': True,
                'module_width': 0.4,    # عرض الوحدات (أكثر تناسق)
                'module_height': 20,    # ارتفاع مناسب يخلي الشكل مستطيل
                'quiet_zone': 6,        # مسافة هادئة أكبر تعطي وضوح
                'font_size': 10,        # حجم خط مناسب للقراءة
                'text_distance': 2,     # المسافة بين النص والباركود
                'dpi': 300,             # دقة عالية للطباعة
                'text': cleaned_data    # النص اللي أسفل الباركود
            })
            
            # التأكد من أن البيانات مناسبة لنوع الباركود
            if barcode_type == 'code128':
                # code128 يدعم الأحرف الرقمية والأبجدية الرقمية بشكل أفضل
                if not re.match(r'^[\dA-Za-z\-\s]+$', cleaned_data):
                    logger.warning(f"Data may not be optimal for code128: {cleaned_data}")
            
            barcode_instance = code_class(cleaned_data, writer=writer)
            buffer = BytesIO()
            barcode_instance.write(buffer)
            
            buffer.seek(0)
            image_data = buffer.getvalue()
            
            if len(image_data) < 100:
                logger.error("Generated barcode image is too small")
                return None
                
            barcode_base64 = base64.b64encode(image_data).decode('utf-8')
            result = f"data:image/png;base64,{barcode_base64}"
            
            # التحقق من أن الباركود لا يحتوي على رموز إضافية
            if '+' in result[:100] or '/' in result[:100]:
                logger.warning("Barcode contains special characters, trying alternative method")
                return generate_barcode_alternative(cleaned_data)
            
            logger.info(f"Barcode generated successfully for order: {cleaned_data}")
            return result
            
        except Exception as barcode_error:
            logger.warning(f"Failed with {barcode_type}, trying code39: {barcode_error}")
            return generate_barcode_with_code39(cleaned_data)
                
    except Exception as e:
        logger.error(f"Error in generate_barcode: {str(e)}")
        return None

def generate_barcode_with_code39(data):
    """إنشاء باركود باستخدام code39 كبديل"""
    try:
        code_class = barcode.get_barcode_class('code39')
        writer = ImageWriter()
        
        writer.set_options({
            'write_text': True,
            'module_width': 0.4,    # عرض الوحدات (أكثر تناسق)
            'module_height': 20,    # ارتفاع مناسب يخلي الشكل مستطيل
            'quiet_zone': 6,        # مسافة هادئة أكبر تعطي وضوح
            'font_size': 10,        # حجم خط مناسب للقراءة
            'text_distance': 2,     # المسافة بين النص والباركود
            'dpi': 300,             # دقة عالية للطباعة
            'text': cleaned_data    # النص اللي أسفل الباركود
        })
        # استخدام add_checksum=False لمنع إضافة أحرف التحقق
        barcode_instance = code_class(data, writer=writer, add_checksum=False)
        buffer = BytesIO()
        barcode_instance.write(buffer)
        
        buffer.seek(0)
        barcode_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        result = f"data:image/png;base64,{barcode_base64}"
        
        logger.info(f"Barcode generated with code39 for order: {data}")
        return result
        
    except Exception as e:
        logger.error(f"Failed to generate barcode with code39: {str(e)}")
        return generate_barcode_alternative(data)

def generate_barcode_alternative(data):
    """طريقة بديلة لإنشاء الباركود باستخدام مكتبة مختلفة إذا لزم الأمر"""
    try:
        # محاولة استخدام مكتبة python-barcode مع إعدادات أكثر تحكماً
        from barcode import Code128
        from barcode.writer import ImageWriter as AltImageWriter
        
        writer = AltImageWriter()
        writer.set_options({
            'write_text': True,
            'module_width': 0.4,    # عرض الوحدات (أكثر تناسق)
            'module_height': 20,    # ارتفاع مناسب يخلي الشكل مستطيل
            'quiet_zone': 6,        # مسافة هادئة أكبر تعطي وضوح
            'font_size': 10,        # حجم خط مناسب للقراءة
            'text_distance': 2,     # المسافة بين النص والباركود
            'dpi': 300,             # دقة عالية للطباعة
            'text': cleaned_data    # النص اللي أسفل الباركود
        })
        code128 = Code128(data, writer=writer)
        buffer = BytesIO()
        code128.write(buffer)
        
        buffer.seek(0)
        barcode_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        result = f"data:image/png;base64,{barcode_base64}"
        
        logger.info(f"Barcode generated with alternative method for order: {data}")
        return result
        
    except Exception as e:
        logger.error(f"All barcode generation methods failed: {str(e)}")
        return None
def get_cached_barcode_data(order_id):
    """الحصول على بيانات الباركود من التخزين المؤقت"""
    try:
        with app_context():
            order_id_str = str(order_id).strip()
            if not order_id_str:
                return None
            
            order = SallaOrder.query.filter_by(id=order_id_str).first()
            
            if order and order.barcode_data:
                barcode_data = order.barcode_data
                
                if barcode_data.startswith('data:image'):
                    return barcode_data
                elif barcode_data.startswith('iVBOR'):
                    fixed_barcode = f"data:image/png;base64,{barcode_data}"
                    return fixed_barcode
                else:
                    return None
                    
            return None
            
    except Exception as e:
        logger.error(f"Error in get_cached_barcode_data: {str(e)}")
        return None

def get_barcodes_for_orders_optimized(order_ids):
    """نسخة محسنة من دالة جلب الباركودات باستخدام PostgreSQL Connection Pool"""
    try:
        if not order_ids:
            return {}
        
        order_ids_str = [str(oid).strip() for oid in order_ids if str(oid).strip()]
        
        if not order_ids_str:
            return {}
            
        # استخدام connection pool لتحسين الأداء
        engine = get_postgres_engine()
        with engine.connect() as conn:
            # استعلام واحد لجميع الطلبات باستخدام ANY بدلاً من IN للتعامل مع القوائم الكبيرة
            query = text("""
                SELECT id, barcode_data 
                FROM salla_orders 
                WHERE id = ANY(:order_ids)
            """)
            
            result = conn.execute(query, {'order_ids': order_ids_str})
            rows = result.fetchall()
            
            barcodes_map = {}
            for row in rows:
                barcode_data = row[1]
                if barcode_data:
                    if not barcode_data.startswith('data:image') and barcode_data.startswith('iVBOR'):
                        barcode_data = f"data:image/png;base64,{barcode_data}"
                    barcodes_map[str(row[0])] = barcode_data
            
            return barcodes_map
            
    except Exception as e:
        logger.error(f"Error in get_barcodes_for_orders_optimized: {str(e)}")
        # Fallback إلى الطريقة العادية
        return get_barcodes_for_orders_fallback(order_ids)

def get_barcodes_for_orders_fallback(order_ids):
    """طريقة احتياطية لجلب الباركودات"""
    try:
        with app_context():
            if not order_ids:
                return {}
            
            order_ids_str = [str(oid).strip() for oid in order_ids if str(oid).strip()]
            
            if not order_ids_str:
                return {}
                
            orders = SallaOrder.query.filter(SallaOrder.id.in_(order_ids_str)).all()
            
            barcodes_map = {}
            for order in orders:
                if order.barcode_data:
                    barcode_data = order.barcode_data
                    
                    if not barcode_data.startswith('data:image') and barcode_data.startswith('iVBOR'):
                        barcode_data = f"data:image/png;base64,{barcode_data}"
                    
                    barcodes_map[str(order.id)] = barcode_data
            
            return barcodes_map
            
    except Exception as e:
        logger.error(f"Error in get_barcodes_for_orders_fallback: {str(e)}")
        return {}

def get_barcodes_for_orders(order_ids):
    """واجهة موحدة لجلب الباركودات (تستخدم النسخة المحسنة)"""
    return get_barcodes_for_orders_optimized(order_ids)


def generate_and_store_barcode(order_id, order_type='salla', store_id=None):
    """إنشاء باركود مع التخزين - معدل لضمان استخدام رقم الطلب الصحيح"""
    try:
        with app_context():
            order_id_str = str(order_id).strip()
            if not order_id_str:
                logger.error("Empty order ID provided for barcode generation")
                return None
            
            # تسجيل محاولة إنشاء الباركود
            logger.info(f"Attempting to generate barcode for order: {order_id_str}, type: {order_type}")
            
            # استخدام رقم الطلب الحقيقي لإنشاء الباركود
            barcode_data = generate_barcode(order_id_str)
            
            if not barcode_data:
                logger.error(f"Failed to generate barcode for order: {order_id_str}")
                return None
            
            # محاولة التخزين في قاعدة البيانات
            try:
                engine = get_postgres_engine()
                with engine.connect() as conn:
                    if order_type == 'salla':
                        # البحث عن الطلب أولاً للتأكد من وجوده
                        existing_order_query = text("SELECT id, reference_id FROM salla_orders WHERE id = :id")
                        result = conn.execute(existing_order_query, {'id': order_id_str})
                        existing_order = result.fetchone()
                        
                        if existing_order:
                            # استخدام reference_id إذا كان متاحاً (رقم الطلب الحقيقي)
                            reference_id = existing_order[1] or order_id_str
                            logger.info(f"Using reference_id: {reference_id} for order: {order_id_str}")
                        else:
                            reference_id = order_id_str
                        
                        query = text("""
                            INSERT INTO salla_orders (id, store_id, barcode_data, barcode_generated_at, reference_id)
                            VALUES (:id, :store_id, :barcode_data, :barcode_generated_at, :reference_id)
                            ON CONFLICT (id) 
                            DO UPDATE SET 
                                barcode_data = EXCLUDED.barcode_data,
                                barcode_generated_at = EXCLUDED.barcode_generated_at,
                                reference_id = EXCLUDED.reference_id
                        """)
                        
                        params = {
                            'id': order_id_str,
                            'store_id': store_id,
                            'barcode_data': barcode_data,
                            'barcode_generated_at': datetime.utcnow(),
                            'reference_id': reference_id
                        }
                    
                    else:
                        query = text("""
                            INSERT INTO custom_order (id, barcode_data, barcode_generated_at)
                            VALUES (:id, :barcode_data, :barcode_generated_at)
                            ON CONFLICT (id) 
                            DO UPDATE SET 
                                barcode_data = EXCLUDED.barcode_data,
                                barcode_generated_at = EXCLUDED.barcode_generated_at
                        """)
                        
                        params = {
                            'id': order_id_str,
                            'barcode_data': barcode_data,
                            'barcode_generated_at': datetime.utcnow()
                        }
                    
                    conn.execute(query, params)
                    conn.commit()
                    logger.info(f"Barcode stored successfully for order: {order_id_str}")
                    
            except Exception as storage_error:
                logger.error(f"Error storing barcode: {str(storage_error)}")
                # محاولة التحديث فقط إذا فشل الإدراج
                try:
                    engine = get_postgres_engine()
                    with engine.connect() as conn:
                        if order_type == 'salla':
                            update_query = text("""
                                UPDATE salla_orders 
                                SET barcode_data = :barcode_data, 
                                    barcode_generated_at = :barcode_generated_at
                                WHERE id = :id
                            """)
                        else:
                            update_query = text("""
                                UPDATE custom_order 
                                SET barcode_data = :barcode_data, 
                                    barcode_generated_at = :barcode_generated_at
                                WHERE id = :id
                            """)
                        
                        conn.execute(update_query, {
                            'id': order_id_str,
                            'barcode_data': barcode_data,
                            'barcode_generated_at': datetime.utcnow()
                        })
                        conn.commit()
                        logger.info(f"Barcode updated successfully for order: {order_id_str}")
                except Exception as update_error:
                    logger.error(f"Error updating barcode: {str(update_error)}")
            
            return barcode_data
                
    except Exception as e:
        logger.error(f"Error in generate_and_store_barcode: {str(e)}")
        return None

def bulk_generate_and_store_barcodes(order_ids, order_type='salla', store_id=None):
    """إنشاء وتخزين الباركودات بشكل مجمع باستخدام الخيوط - معدل لضمان استخدام رقم الطلب الصحيح"""
    try:
        if not order_ids:
            logger.warning("No order IDs provided for bulk barcode generation")
            return {}
        
        # تسجيل بدء العملية
        logger.info(f"Starting bulk barcode generation for {len(order_ids)} orders, type: {order_type}")
        
        barcodes_map = {}
        records_to_update = []
        lock = Lock()
        
        # إنشاء الباركودات بشكل متزامن
        def generate_single_barcode(order_id):
            order_id_str = str(order_id).strip()
            if not order_id_str:
                logger.warning("Empty order ID in bulk generation")
                return None, None
                
            try:
                # تسجيل محاولة إنشاء الباركود لرقم طلب محدد
                logger.info(f"Generating barcode for order: {order_id_str}")
                
                # استخدام رقم الطلب الحقيقي لإنشاء الباركود
                barcode_data = generate_barcode(order_id_str)
                
                if barcode_data:
                    # التحقق من أن الباركود يحتوي على رقم الطلب الصحيح
                    if order_id_str in barcode_data:
                        logger.info(f"Barcode generated successfully for order: {order_id_str}")
                    else:
                        logger.warning(f"Barcode generated but order ID mismatch for: {order_id_str}")
                    
                    with lock:
                        records_to_update.append({
                            'id': order_id_str,
                            'barcode_data': barcode_data,
                            'barcode_generated_at': datetime.utcnow(),
                            'store_id': store_id
                        })
                    return order_id_str, barcode_data
                else:
                    logger.error(f"Failed to generate barcode for order: {order_id_str}")
                    return order_id_str, None
                    
            except Exception as e:
                logger.error(f"Error generating barcode for {order_id_str}: {str(e)}")
                return order_id_str, None
        
        # استخدام ThreadPoolExecutor لإنشاء الباركودات بشكل متزامن
        successful_generations = 0
        failed_generations = 0
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_id = {executor.submit(generate_single_barcode, order_id): order_id for order_id in order_ids}
            
            for future in as_completed(future_to_id):
                order_id_str, barcode_data = future.result()
                if barcode_data:
                    barcodes_map[order_id_str] = barcode_data
                    successful_generations += 1
                else:
                    failed_generations += 1
        
        # تسجيل نتائج الإنشاء
        logger.info(f"Barcode generation completed: {successful_generations} successful, {failed_generations} failed")
        
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
                                INSERT INTO salla_orders (id, store_id, barcode_data, barcode_generated_at, reference_id)
                                VALUES (:id, :store_id, :barcode_data, :barcode_generated_at, :reference_id)
                                ON CONFLICT (id) 
                                DO UPDATE SET 
                                    barcode_data = EXCLUDED.barcode_data,
                                    barcode_generated_at = EXCLUDED.barcode_generated_at,
                                    reference_id = COALESCE(EXCLUDED.reference_id, salla_orders.reference_id)
                            """)
                            
                            # إضافة reference_id لكل سجل
                            for record in records_to_update:
                                record['reference_id'] = existing_orders.get(record['id'], record['id'])
                                
                        else:
                            query = text("""
                                INSERT INTO custom_order (id, barcode_data, barcode_generated_at)
                                VALUES (:id, :barcode_data, :barcode_generated_at)
                                ON CONFLICT (id) 
                                DO UPDATE SET 
                                    barcode_data = EXCLUDED.barcode_data,
                                    barcode_generated_at = EXCLUDED.barcode_generated_at
                            """)
                        
                        # التخزين المجمع
                        result = conn.execute(query, records_to_update)
                        conn.commit()
                        
                        logger.info(f"Successfully stored {len(records_to_update)} barcodes in database")
                        
            except Exception as e:
                logger.error(f"Error in bulk barcode storage: {str(e)}")
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
                                    SET barcode_data = :barcode_data, 
                                        barcode_generated_at = :barcode_generated_at
                                    WHERE id = :id
                                """)
                            else:
                                update_query = text("""
                                    UPDATE custom_order 
                                    SET barcode_data = :barcode_data, 
                                        barcode_generated_at = :barcode_generated_at
                                    WHERE id = :id
                                """)
                            
                            for record in records_to_update:
                                try:
                                    conn.execute(update_query, {
                                        'id': record['id'],
                                        'barcode_data': record['barcode_data'],
                                        'barcode_generated_at': record['barcode_generated_at']
                                    })
                                    successful_storages += 1
                                except Exception as single_update_error:
                                    logger.error(f"Error updating barcode for {record['id']}: {str(single_update_error)}")
                                    failed_storages += 1
                            
                            conn.commit()
                            logger.info(f"Individual update completed: {successful_storages} successful, {failed_storages} failed")
                            
                except Exception as update_error:
                    logger.error(f"Error in bulk barcode update: {str(update_error)}")
        
        # التحقق النهائي من الباركودات المخزنة
        logger.info(f"Final barcodes map contains {len(barcodes_map)} entries")
        
        # تسجيل عينة من الباركودات للتأكد
        sample_orders = list(barcodes_map.keys())[:3] if barcodes_map else []
        for order_id in sample_orders:
            logger.info(f"Sample - Order {order_id}: Barcode generated successfully")
        
        return barcodes_map
            
    except Exception as e:
        logger.error(f"Error in bulk_generate_and_store_barcodes: {str(e)}")
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