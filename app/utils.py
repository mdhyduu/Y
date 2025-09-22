from datetime import datetime
import os
import barcode
from barcode.writer import ImageWriter
from flask import current_app, request
from .models import db, User, Employee, CustomOrder, SallaOrder
import logging
from io import BytesIO
import base64
from functools import lru_cache
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from sqlalchemy import text
from flask import current_app
# إعداد المسجل
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'static/uploads/custom_orders'

# إعدادات اتصال قاعدة البيانات المحسنة
MAX_DB_RETRIES = 3
DB_RETRY_DELAY = 0.5

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
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=50, pool_maxsize=50)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def generate_barcode(data):
    """إنشاء باركود مع تحسين الأداء"""
    try:
        buffer = BytesIO()
        writer = ImageWriter()
        
        options = {
            'write_text': False,
            'module_width': 0.4,
            'module_height': 15,
            'quiet_zone': 10,
            'dpi': 96,
            'compress': True
        }
        
        code = barcode.get('code128', str(data), writer=writer)
        code.write(buffer, options=options)
        
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
        
    except Exception as e:
        logger.error(f"Error generating barcode: {str(e)}")
        return None

# عدل الدوال التالية لإضافة سياق التطبيق
def get_cached_barcode_data(order_id):
    """الحصول على بيانات الباركود من التخزين المؤقت"""
    try:
        with current_app.app_context():  # أضف هذا السطر
            order = SallaOrder.query.get(str(order_id))
            return order.barcode_data if order else None
    except Exception as e:
        logger.error(f"Error in get_cached_barcode_data: {str(e)}")
        return None

def get_barcodes_for_orders(order_ids):
    """جلب جميع الباركودات للطلبات المحددة في استعلام واحد"""
    try:
        with current_app.app_context():  # أضف هذا السطر
            orders = SallaOrder.query.filter(SallaOrder.order_id.in_(order_ids)).all()
            return {str(order.order_id): order.barcode_data for order in orders}
    except Exception as e:
        logger.error(f"Error in get_barcodes_for_orders: {str(e)}")
        return {}

def generate_and_store_barcodes_bulk(order_ids, order_type='salla'):
    """إنشاء وحفظ الباركودات بشكل جماعي مع تحسين الأداء"""
    try:
        barcode_map = {}
        orders_to_update = []
        
        for order_id in order_ids:
            order_id_str = str(order_id)
            barcode_data = generate_barcode(order_id_str)
            if barcode_data:
                barcode_map[order_id_str] = barcode_data
                orders_to_update.append({
                    'id': order_id_str,
                    'barcode_data': barcode_data,
                    'barcode_generated_at': datetime.utcnow()
                })
        
        # تحديث قاعدة البيانات بشكل جماعي مع إدارة الجلسة بشكل صحيح
        with db_session_scope() as session:
            if order_type == 'salla':
                for update_data in orders_to_update:
                    session.query(SallaOrder).filter(
                        SallaOrder.order_id == update_data['id']
                    ).update(update_data)
            else:
                for update_data in orders_to_update:
                    session.query(CustomOrder).filter(
                        CustomOrder.id == update_data['id']
                    ).update(update_data)
                
        return barcode_map
            
    except Exception as e:
        logger.error(f"خطأ في الحفظ الجماعي للباركود: {str(e)}")
        return {}
def generate_and_store_barcode(order_id, order_type='salla'):
    """إنشاء باركود وحفظه في قاعدة البيانات تلقائيًا"""
    try:
        order_id_str = str(order_id)
        barcode_data = generate_barcode(order_id_str)
        
        if not barcode_data:
            return None
        
        if order_type == 'salla':
            order = SallaOrder.query.get(order_id_str)
        else:
            order = CustomOrder.query.get(order_id_str)
            
        if order:
            order.barcode_data = barcode_data
            order.barcode_generated_at = datetime.utcnow()
            db.session.commit()
            return barcode_data
        else:
            return None
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"خطأ في حفظ الباركود تلقائيًا: {str(e)}")
        return None
    finally:
        db.session.remove()

def get_main_image(item):
    """استخراج الصورة الرئيسية بشكل أكثر كفاءة"""
    thumbnail_url = item.get('product_thumbnail') or item.get('thumbnail')
    if thumbnail_url and isinstance(thumbnail_url, str):
        return thumbnail_url
    
    images = item.get('images', [])
    if images and isinstance(images, list) and len(images) > 0:
        first_image = images[0]
        image_url = first_image.get('image', '')
        if image_url:
            if not image_url.startswith(('http://', 'https://')):
                return f"https://cdn.salla.sa{image_url}"
            return image_url
    
    for field in ['image', 'url', 'image_url', 'picture']:
        if item.get(field):
            return item[field]
    
    return ''

def format_date(date_str):
    try:
        dt = datetime.strptime(date_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return date_str if date_str else 'غير معروف'

def process_order_data(order_id, items_data, barcode_data=None):
    """معالجة بيانات الطلب مع استخدام الباركود المخزن في قاعدة البيانات"""
    order_id = str(order_id)
    items = []
    
    for index, item in enumerate(items_data):
        item_id = item.get('id')
        if not item_id:
            item_id = f"temp_{index}"
        
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
                    values_list = []
                    for option_item in raw_value:
                        if isinstance(option_item, dict):
                            value_str = option_item.get('name') or option_item.get('value') or str(option_item)
                            values_list.append(value_str)
                        else:
                            values_list.append(str(option_item))
                    display_value = ', '.join(values_list)
                else:
                    display_value = str(raw_value) if raw_value else 'غير محدد'
                
                options.append({
                    'name': option.get('name', ''),
                    'value': display_value,
                    'type': option.get('type', '')
                })
        
        digital_codes = []
        for code in item.get('codes', []):
            if isinstance(code, dict):
                digital_codes.append({
                    'code': code.get('code', ''),
                    'status': code.get('status', 'غير معروف')
                })
        
        digital_files = []
        for file in item.get('files', []):
            if isinstance(file, dict):
                digital_files.append({
                    'url': file.get('url', ''),
                    'name': file.get('name', ''),
                    'size': file.get('size', 0)
                })
        
        reservations = []
        for reservation in item.get('reservations', []):
            if isinstance(reservation, dict):
                reservations.append({
                    'id': reservation.get('id'),
                    'from': reservation.get('from', ''),
                    'to': reservation.get('to', ''),
                    'date': reservation.get('date', '')
                })
        
        product_info = {
            'id': item_id,
            'name': item.get('name', ''),
            'description': item.get('notes', '')
        }
        
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
            'product': product_info
        }
        
        items.append(item_data)

    if not barcode_data:
        barcode_data = get_cached_barcode_data(order_id)
        
        if not barcode_data:
            barcode_data = generate_and_store_barcode(order_id, 'salla')
            if not barcode_data:
                barcode_data = generate_barcode(order_id)

    processed_order = {
        'id': order_id,
        'order_items': items,
        'barcode': barcode_data
    }
    
    return processed_order

@contextmanager
def db_session_scope():
    """مدير سياق محسن لإدارة جلسات قاعدة البيانات"""
    try:
        yield db.session
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Database session error: {str(e)}")
        raise
    finally:
        # تنظيف شامل للجلسة
        try:
            db.session.expunge_all()
            db.session.close()
        except:
            pass
        finally:
            db.session.remove()
from sqlalchemy import exc
from sqlalchemy.pool import NullPool
import time

def close_db_connection():
    """إغلاق فعلي لاتصالات قاعدة البيانات"""
    try:
        # إغلاق جميع الاتصالات في pool
        db.engine.dispose()
        logger.info("تم إغلاق اتصالات قاعدة البيانات بنجاح")
    except Exception as e:
        logger.error(f"خطأ في إغلاق اتصالات قاعدة البيانات: {str(e)}")

def check_db_connection():
    """فحص صحة اتصال قاعدة البيانات مع إغلاق الاتصالات المعطلة"""
    try:
        # محاولة إغلاق الاتصالات القديمة أولاً
        close_db_connection()
        
        # فحص الاتصال مع مهلة زمنية
        result = db.session.execute(text('SELECT 1')).scalar()
        return result == 1
    except Exception as e:
        logger.error(f"فشل في التحقق من اتصال قاعدة البيانات: {str(e)}")
        # محاولة إعادة الاتصال
        try:
            close_db_connection()
            time.sleep(1)
            db.session.execute(text('SELECT 1')).scalar()
            return True
        except:
            return False
    finally:
        db.session.remove()
import threading
import queue

# ... (بقية الاستيرادات الحالية)

def process_orders_in_parallel(order_ids, access_token):
    """معالجة الطلبات بشكل متوازي مع إدارة محسنة للاتصالات"""
    from .config import Config
    
    barcodes_map = get_barcodes_for_orders(order_ids)
    
    def fetch_order_data(order_id, result_queue):
        """دالة مساعدة لجلب بيانات الطلب مع إدارة محكمة للاتصالات"""
        # إنشاء سياق تطبيق جديد لكل خيط
        app = create_app()
        with app.app_context():
            session = create_session()
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            }
            
            try:
                # فحص وإصلاح اتصال قاعدة البيانات قبل المتابعة
                if not check_db_connection():
                    logger.error(f"فشل اتصال قاعدة البيانات للطلب {order_id}")
                    result_queue.put(None)
                    return
                    
                order_response = session.get(
                    f"{Config.SALLA_ORDERS_API}/{order_id}",
                    headers=headers,
                    timeout=10
                )
                
                if order_response.status_code != 200:
                    result_queue.put(None)
                    return
                    
                order_data = order_response.json().get('data', {})
                
                items_response = session.get(
                    f"{Config.SALLA_BASE_URL}/orders/items",
                    params={'order_id': order_id},
                    headers=headers,
                    timeout=10
                )
                
                items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
                
                barcode_data = barcodes_map.get(order_id)
                processed_order = process_order_data(order_id, items_data, barcode_data)
                
                processed_order['reference_id'] = order_data.get('reference_id', order_id)
                processed_order['customer'] = order_data.get('customer', {})
                processed_order['created_at'] = format_date(order_data.get('created_at', ''))
                
                result_queue.put(processed_order)
                
            except Exception as e:
                logger.error(f"Error fetching order {order_id}: {str(e)}")
                result_queue.put(None)
            finally:
                # تنظيف شامل
                try:
                    session.close()
                except:
                    pass
                finally:
                    close_db_connection()  # إغلاق فعلي للاتصالات
    
    orders = []
    result_queue = queue.Queue()
    threads = []
    max_workers = 2  # تقليل عدد العمال لتقليل الضغط على قاعدة البيانات

    # تقسيم order_ids إلى مجموعات أصغر
    chunk_size = min(5, (len(order_ids) + max_workers - 1) // max_workers)
    order_chunks = [order_ids[i:i+chunk_size] for i in range(0, len(order_ids), chunk_size)]
    
    for chunk in order_chunks:
        threads = []
        for order_id in chunk:
            thread = threading.Thread(
                target=fetch_order_data,
                args=(order_id, result_queue),
                daemon=True  # جعل الخيوط daemon لتجنب التعلق
            )
            threads.append(thread)
            thread.start()
        
        # انتظار انتهاء الخيوط مع timeout
        for thread in threads:
            thread.join(timeout=30)  # timeout 30 ثانية
        
        # جمع النتائج
        while not result_queue.empty():
            result = result_queue.get()
            if result:
                orders.append(result)
        
        # إعطاء وقت للتنظيف بين المجموعات
        time.sleep(0.5)
    
    # تنظيف نهائي بعد انتهاء جميع الخيوط
    close_db_connection()
    return orders
        
def humanize_time(dt):
    """تحويل التاريخ إلى نص مقروء مثل 'منذ دقيقة'"""
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
import threading
import schedule

def periodic_connection_cleanup():
    """تنظيف دوري لاتصالات قاعدة البيانات"""
    def cleanup():
        while True:
            time.sleep(300)  # كل 5 دقائق
            close_db_connection()
    
    # تشغيل التنظيف في خلفية
    cleanup_thread = threading.Thread(target=cleanup, daemon=True)
    cleanup_thread.start()

# تشغيل التنظيف الدوري عند بدء التطبيق
periodic_connection_cleanup()