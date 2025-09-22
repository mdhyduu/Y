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

# إعداد المسجل للإنتاج
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'static/uploads/custom_orders'

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
    """إنشاء باركود مع معالجة الأخطاء"""
    try:
        data_str = str(data).strip()
        if not data_str:
            logger.error("Empty data provided for barcode generation")
            return None
        
        # اختيار نوع الباركود المناسب
        barcode_type = 'code128'
        
        try:
            code_class = barcode.get_barcode_class(barcode_type)
            writer = ImageWriter()
            
            writer.set_options({
                'write_text': True,
                'module_width': 0.4,
                'module_height': 15,
                'quiet_zone': 4,
                'font_size': 10,
                'text_distance': 5,
                'dpi': 72
            })
            
            barcode_instance = code_class(data_str, writer=writer)
            buffer = BytesIO()
            barcode_instance.write(buffer)
            
            buffer.seek(0)
            image_data = buffer.getvalue()
            
            if len(image_data) < 100:
                logger.error("Generated barcode image is too small")
                return None
                
            barcode_base64 = base64.b64encode(image_data).decode('utf-8')
            result = f"data:image/png;base64,{barcode_base64}"
            
            return result
            
        except Exception as barcode_error:
            logger.warning(f"Failed with {barcode_type}, trying code39: {barcode_error}")
            
            try:
                code_class = barcode.get_barcode_class('code39')
                writer = ImageWriter()
                
                writer.set_options({
                    'write_text': True,
                    'module_width': 0.4,
                    'module_height': 15,
                    'quiet_zone': 4,
                    'font_size': 10
                })
                
                barcode_instance = code_class(data_str, writer=writer)
                buffer = BytesIO()
                barcode_instance.write(buffer)
                
                buffer.seek(0)
                barcode_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                result = f"data:image/png;base64,{barcode_base64}"
                
                return result
                
            except Exception as fallback_error:
                logger.error(f"All barcode generation methods failed: {fallback_error}")
                return None
                
    except Exception as e:
        logger.error(f"Error in generate_barcode: {str(e)}")
        return None

def get_cached_barcode_data(order_id):
    """الحصول على بيانات الباركود من التخزين المؤقت"""
    try:
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
    finally:
        db.session.remove()

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
    finally:
        db.session.remove()

def get_barcodes_for_orders(order_ids):
    """واجهة موحدة لجلب الباركودات (تستخدم النسخة المحسنة)"""
    return get_barcodes_for_orders_optimized(order_ids)

def generate_and_store_barcode(order_id, order_type='salla'):
    """إنشاء باركود مع التخزين"""
    try:
        order_id_str = str(order_id).strip()
        if not order_id_str:
            return None
        
        barcode_data = generate_barcode(order_id_str)
        
        if not barcode_data:
            return None
        
        # محاولة التخزين في قاعدة البيانات باستخدام Connection Pool
        try:
            engine = get_postgres_engine()
            with engine.connect() as conn:
                if order_type == 'salla':
                    # استخدام INSERT ON CONFLICT لتحديث السجلات الموجودة
                    query = text("""
                        INSERT INTO salla_orders (id, barcode_data, barcode_generated_at)
                        VALUES (:id, :barcode_data, :barcode_generated_at)
                        ON CONFLICT (id) 
                        DO UPDATE SET 
                            barcode_data = EXCLUDED.barcode_data,
                            barcode_generated_at = EXCLUDED.barcode_generated_at
                    """)
                else:
                    query = text("""
                        INSERT INTO custom_order (id, barcode_data, barcode_generated_at)
                        VALUES (:id, :barcode_data, :barcode_generated_at)
                        ON CONFLICT (id) 
                        DO UPDATE SET 
                            barcode_data = EXCLUDED.barcode_data,
                            barcode_generated_at = EXCLUDED.barcode_generated_at
                    """)
                
                conn.execute(query, {
                    'id': order_id_str,
                    'barcode_data': barcode_data,
                    'barcode_generated_at': datetime.utcnow()
                })
                conn.commit()
                
        except Exception as storage_error:
            logger.error(f"Error storing barcode: {str(storage_error)}")
        
        return barcode_data
            
    except Exception as e:
        logger.error(f"Error in generate_and_store_barcode: {str(e)}")
        return None

def bulk_generate_and_store_barcodes(order_ids, order_type='salla'):
    """إنشاء وتخزين الباركودات بشكل مجمع باستخدام الخيوط"""
    try:
        if not order_ids:
            return {}
        
        barcodes_map = {}
        records_to_update = []
        lock = Lock()
        
        # إنشاء الباركودات بشكل متزامن
        def generate_single_barcode(order_id):
            order_id_str = str(order_id).strip()
            if not order_id_str:
                return None, None
                
            try:
                barcode_data = generate_barcode(order_id_str)
                if barcode_data:
                    with lock:
                        records_to_update.append({
                            'id': order_id_str,
                            'barcode_data': barcode_data,
                            'barcode_generated_at': datetime.utcnow()
                        })
                    return order_id_str, barcode_data
            except Exception as e:
                logger.error(f"Error generating barcode for {order_id_str}: {str(e)}")
            return order_id_str, None
        
        # استخدام ThreadPoolExecutor لإنشاء الباركودات بشكل متزامن
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_id = {executor.submit(generate_single_barcode, order_id): order_id for order_id in order_ids}
            
            for future in as_completed(future_to_id):
                order_id_str, barcode_data = future.result()
                if barcode_data:
                    barcodes_map[order_id_str] = barcode_data
        
        # تخزين مجمع في PostgreSQL
        if records_to_update:
            try:
                engine = get_postgres_engine()
                with engine.connect() as conn:
                    if order_type == 'salla':
                        query = text("""
                            INSERT INTO salla_orders (id, barcode_data, barcode_generated_at)
                            VALUES (:id, :barcode_data, :barcode_generated_at)
                            ON CONFLICT (id) 
                            DO UPDATE SET 
                                barcode_data = EXCLUDED.barcode_data,
                                barcode_generated_at = EXCLUDED.barcode_generated_at
                        """)
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
                    conn.execute(query, records_to_update)
                    conn.commit()
                    
            except Exception as e:
                logger.error(f"Error in bulk barcode storage: {str(e)}")
        
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

def format_date(date_str):
    """تنسيق التاريخ مع معالجة الأخطاء"""
    try:
        if not date_str:
            return 'غير معروف'
            
        date_parts = date_str.split('.')[0]
        dt = datetime.strptime(date_parts, '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%Y-%m-%d %H:%M')
        
    except Exception as e:
        logger.warning(f"Failed to format date: {str(e)}")
        return date_str if date_str else 'غير معروف'

def process_order_data(order_id, items_data, barcode_data=None):
    """معالجة بيانات الطلب مع استخدام الباركود المخزن"""
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

        # الحصول على الباركود
        final_barcode_data = barcode_data
        
        if not final_barcode_data:
            final_barcode_data = get_cached_barcode_data(order_id_str)
        
        if not final_barcode_data:
            final_barcode_data = generate_and_store_barcode(order_id_str, 'salla')
        
        if not final_barcode_data:
            final_barcode_data = generate_barcode(order_id_str)

        result = {
            'id': order_id_str,
            'order_items': items,
            'barcode': final_barcode_data
        }
        
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

def process_orders_concurrently(order_ids, access_token, max_workers=10):
    """معالجة الطلبات بشكل متزامن باستخدام ThreadPoolExecutor"""
    from .config import Config
    
    if not order_ids:
        return []
    
    # جلب جميع الباركودات مسبقاً في استعلام واحد
    barcodes_map = get_barcodes_for_orders(order_ids)
    
    orders = []
    successful_orders = 0
    failed_orders = 0
    lock = Lock()  # لحماية المتغيرات المشتركة

    def process_single_order(order_id):
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
                f"{Config.salla_ordersS_API}/{order_id_str}",
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
            
            # معالجة بيانات الطلب
            processed_order = process_order_data(order_id_str, items_data, barcode_data)
            
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