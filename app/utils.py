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
from sqlalchemy import text
import threading
import queue
import time

# إعداد المسجل
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'static/uploads/custom_orders'

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
    """إنشاء باركود مع تحسين الأداء"""
    try:
        data_str = str(data)
        buffer = BytesIO()
        writer = ImageWriter()
        
        options = {
            'write_text': True,  # عرض النص تحت الباركود
            'module_width': 0.3,
            'module_height': 12,
            'quiet_zone': 6,
            'dpi': 96,
            'font_size': 8
        }
        
        code = barcode.get('code128', data_str, writer=writer)
        code.write(buffer, options=options)
        
        buffer.seek(0)
        barcode_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return f"data:image/png;base64,{barcode_base64}"
        
    except Exception as e:
        logger.error(f"Error generating barcode: {str(e)}")
        return None
def get_cached_barcode_data(order_id):
    """الحصول على بيانات الباركود من التخزين المؤقت"""
    try:
        order_id_str = str(order_id)
        # Fix: Use 'id' instead of 'order_id' since that's the primary key in SallaOrder
        order = SallaOrder.query.get(order_id_str)  # Use get() for primary key lookup
        if order and order.barcode_data:
            # التأكد من صحة تنسيق الباركود
            if not order.barcode_data.startswith('data:image'):
                if order.barcode_data.startswith('iVBOR'):
                    return f"data:image/png;base64,{order.barcode_data}"
            return order.barcode_data
        return None
    except Exception as e:
        logger.error(f"Error in get_cached_barcode_data: {str(e)}")
        return None
    finally:
        db.session.remove()

def get_barcodes_for_orders(order_ids):
    """جلب جميع الباركودات للطلبات المحددة في استعلام واحد"""
    try:
        order_ids_str = [str(oid) for oid in order_ids]
        # Fix: Filter by 'id' instead of 'order_id'
        orders = SallaOrder.query.filter(SallaOrder.id.in_(order_ids_str)).all()
        return {str(order.id): order.barcode_data for order in orders if order.barcode_data}
    except Exception as e:
        logger.error(f"Error in get_barcodes_for_orders: {str(e)}")
        return {}
    finally:
        db.session.remove()

def generate_and_store_barcode(order_id, order_type='salla'):
    """إنشاء باركود وحفظه في قاعدة البيانات تلقائيًا"""
    try:
        order_id_str = str(order_id)
        barcode_data = generate_barcode(order_id_str)
        
        if not barcode_data:
            return None
        
        if order_type == 'salla':
            # Fix: Use 'id' instead of 'order_id'
            order = SallaOrder.query.get(order_id_str)  # Use get() for primary key
            if not order:
                # If order doesn't exist, create a new one with the ID
                order = SallaOrder(id=order_id_str)
                db.session.add(order)
        else:
            order = CustomOrder.query.get(order_id_str)
            
        if order:
            order.barcode_data = barcode_data
            order.barcode_generated_at = datetime.utcnow()
            db.session.commit()
            return barcode_data
        return None
            
    except Exception as e:
        db.session.rollback()
        logger.error(f"خطأ في حفظ الباركود: {str(e)}")
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

    # الحصول على الباركود
    if not barcode_data:
        barcode_data = get_cached_barcode_data(order_id)
    
    if not barcode_data:
        barcode_data = generate_and_store_barcode(order_id, 'salla')
    
    if not barcode_data:
        barcode_data = generate_barcode(order_id)

    return {
        'id': order_id,
        'order_items': items,
        'barcode': barcode_data
    }

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
        db.session.remove()

def process_orders_in_parallel(order_ids, access_token, flask_app):
    """معالجة الطلبات بشكل متوازي باستخدام تطبيق Flask الموجود"""
    from .config import Config
    
    barcodes_map = get_barcodes_for_orders(order_ids)
    
    def fetch_order_data(order_id, result_queue, app):
        """دالة مساعدة لجلب بيانات الطلب باستخدام تطبيق Flask الموجود"""
        with app.app_context():  # استخدام سياق التطبيق الموجود
            session = create_session()
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            }
            
            try:
                order_response = session.get(
                    f"{Config.SALLA_ORDERS_API}/{order_id}",
                    headers=headers,
                    timeout=15
                )
                
                if order_response.status_code != 200:
                    logger.warning(f"Failed to fetch order {order_id}: {order_response.status_code}")
                    result_queue.put(None)
                    return
                    
                order_data = order_response.json().get('data', {})
                
                items_response = session.get(
                    f"{Config.SALLA_BASE_URL}/orders/items",
                    params={'order_id': order_id},
                    headers=headers,
                    timeout=15
                )
                
                items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
                
                barcode_data = barcodes_map.get(str(order_id))
                processed_order = process_order_data(order_id, items_data, barcode_data)
                
                processed_order['reference_id'] = order_data.get('reference_id', order_id)
                processed_order['customer'] = order_data.get('customer', {})
                processed_order['created_at'] = format_date(order_data.get('created_at', ''))
                
                result_queue.put(processed_order)
                
            except Exception as e:
                logger.error(f"Error fetching order {order_id}: {str(e)}")
                result_queue.put(None)
            finally:
                session.close()
    
    orders = []
    result_queue = queue.Queue()
    
    # استخدام ThreadPoolExecutor لإدارة أفضل للخيوط
    with ThreadPoolExecutor(max_workers=3) as executor:
        # تقديم جميع المهام
        future_to_order = {
            executor.submit(fetch_order_data, order_id, result_queue, flask_app): order_id 
            for order_id in order_ids
        }
        
        # جمع النتائج مع timeout
        for future in future_to_order:
            try:
                future.result(timeout=30)  # انتظار 30 ثانية كحد أقصى
            except Exception as e:
                order_id = future_to_order[future]
                logger.error(f"Thread timeout/error for order {order_id}: {str(e)}")
        
        # جمع النتائج من الطابور
        while not result_queue.empty():
            result = result_queue.get()
            if result:
                orders.append(result)
    
    logger.info(f"Successfully processed {len(orders)} out of {len(order_ids)} orders")
    return orders

# بديل أبسط بدون خيوط متعددة (أكثر أماناً)
def process_orders_sequentially(order_ids, access_token):
    """معالجة الطلبات بشكل تسلسلي - أكثر أماناً واستقراراً"""
    from .config import Config
    
    orders = []
    session = create_session()
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    barcodes_map = get_barcodes_for_orders(order_ids)
    
    for i, order_id in enumerate(order_ids):
        try:
            order_response = session.get(
                f"{Config.SALLA_ORDERS_API}/{order_id}",
                headers=headers,
                timeout=15
            )
            
            if order_response.status_code != 200:
                continue
                
            order_data = order_response.json().get('data', {})
            
            items_response = session.get(
                f"{Config.SALLA_BASE_URL}/orders/items",
                params={'order_id': order_id},
                headers=headers,
                timeout=15
            )
            
            items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
            
            barcode_data = barcodes_map.get(str(order_id))
            processed_order = process_order_data(order_id, items_data, barcode_data)
            
            processed_order['reference_id'] = order_data.get('reference_id', order_id)
            processed_order['customer'] = order_data.get('customer', {})
            processed_order['created_at'] = format_date(order_data.get('created_at', ''))
            
            orders.append(processed_order)
            
            # إعطاء فرصة للتنفس بين الطلبات
            if i % 5 == 0:
                time.sleep(0.1)
                
        except Exception as e:
            logger.error(f"Error processing order {order_id}: {str(e)}")
            continue
    
    session.close()
    return orders

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
def debug_barcode_generation():
    """تسجيل مفصل لعملية توليد الباركود (مصحح)"""
    import sys
    logger.info("=== BARCODE DEBUG INFO ===")
    logger.info(f"Python path: {sys.executable}")
    logger.info(f"Python version: {sys.version}")
    
    try:
        import barcode
        # طريقة آمنة للحصول على الإصدار
        if hasattr(barcode, '__version__'):
            logger.info(f"Barcode version: {barcode.__version__}")
        else:
            logger.info("Barcode module imported successfully (version attribute not available)")
    except ImportError as e:
        logger.error(f"Barcode import error: {e}")
    
    try:
        import PIL
        if hasattr(PIL, '__version__'):
            logger.info(f"PIL version: {PIL.__version__}")
        else:
            # حاول مع Image مباشرة
            from PIL import Image
            logger.info(f"PIL available via Image: {Image.__version__}")
    except ImportError as e:
        logger.error(f"PIL import error: {e}")
    
    # اختبار عملي مبسط
    try:
        test_result = generate_barcode_simple_test()
        if test_result:
            logger.info("✅ Test barcode generation: SUCCESS")
        else:
            logger.error("❌ Test barcode generation: FAILED")
    except Exception as e:
        logger.error(f"❌ Test barcode generation error: {e}")

def generate_barcode_simple_test():
    """اختبار مبسط لتوليد الباركود"""
    try:
        data_str = "TEST123"
        buffer = BytesIO()
        
        # استخدام code39 بدلاً من code128 (أكثر استقراراً)
        import barcode
        from barcode.writer import ImageWriter
        
        code = barcode.get('code39', data_str, writer=ImageWriter())
        code.write(buffer)
        
        buffer.seek(0)
        return len(buffer.getvalue()) > 100  # التأكد من أن الصورة ليست فارغة
        
    except Exception as e:
        logger.error(f"Simple barcode test failed: {e}")
        return False

# إزالة الدوال المسببة للمشاكل
# periodic_connection_cleanup() - تم إزالتها
# close_db_connection() - تم إزالتها