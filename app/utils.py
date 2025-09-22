# ملاحظة هامة: لكي تعمل هذه التعديلات بشكل صحيح، تأكد من إضافة الإعدادات التالية
# في ملف إعدادات Flask (config.py) لضمان إعادة تدوير اتصالات قاعدة البيانات تلقائيًا.
#
# class Config:
#     # ... other configs
#     SQLALCHEMY_ENGINE_OPTIONS = {
#         'pool_recycle': 280,  # يعيد استخدام الاتصال كل 280 ثانية
#         'pool_pre_ping': True # يتأكد من أن الاتصال صالح قبل استخدامه
#     }
#
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
import threading
import queue
import time
from contextlib import contextmanager
from sqlalchemy import text

# إعداد المسجل
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'static/uploads/custom_orders'

def allowed_file(filename):
    """التحقق من امتداد الملف المسموح به"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@contextmanager
def db_session_scope():
    """
    مدير سياق مبسط لإدارة جلسات قاعدة البيانات.
    يضمن تنفيذ commit أو rollback بشكل آمن.
    """
    try:
        yield db.session
        db.session.commit()
    except Exception as e:
        logger.error(f"Database session error: {str(e)}")
        db.session.rollback()
        raise # إعادة إرسال الخطأ للمعالجة في المستوى الأعلى

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
    # لا حاجة لـ db.session.remove() هنا، Flask-SQLAlchemy تدير الجلسة

def get_user_from_cookies():
    """استخراج بيانات المستخدم من الكوكيز"""
    user_id = request.cookies.get('user_id')
    is_admin = request.cookies.get('is_admin') == 'true'
    
    if not user_id:
        return None, None
    
    try:
        user_id_int = int(user_id)
        if is_admin:
            user = User.query.get(user_id_int)
            return user, None
        else:
            employee = Employee.query.get(user_id_int)
            if employee:
                user = User.query.filter_by(store_id=employee.store_id).first()
                return user, employee
            return None, None
    except (ValueError, TypeError) as e:
        logger.error(f"Error parsing user ID from cookies: {str(e)}")
        return None, None
    # لا حاجة لـ db.session.remove() هنا

def create_session():
    """إنشاء جلسة طلبات (requests session) مع إعدادات إعادة المحاولة"""
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
    """إنشاء باركود بصيغة base64"""
    try:
        buffer = BytesIO()
        # تم تحسين خيارات الكتابة لتقليل حجم الصورة
        options = {
            'write_text': False, 'module_width': 0.4, 'module_height': 15.0,
            'quiet_zone': 6.0, 'font_size': 0, 'text_distance': 0
        }
        code = barcode.get('code128', str(data), writer=ImageWriter())
        code.write(buffer, options=options)
        return base64.b64encode(buffer.getvalue()).decode('utf-8')
    except Exception as e:
        logger.error(f"Error generating barcode for data '{data}': {str(e)}")
        return None

def get_barcodes_for_orders(order_ids):
    """جلب جميع الباركودات للطلبات المحددة في استعلام واحد لتحسين الأداء"""
    if not order_ids:
        return {}
    try:
        orders = SallaOrder.query.filter(SallaOrder.order_id.in_(order_ids)).all()
        return {str(order.order_id): order.barcode_data for order in orders if order.barcode_data}
    except Exception as e:
        logger.error(f"Error in get_barcodes_for_orders: {str(e)}")
        return {}

def generate_and_store_barcodes_bulk(order_ids, order_type='salla'):
    """
    إنشاء وحفظ الباركودات بشكل جماعي باستخدام عملية تحديث واحدة (bulk update).
    هذا أكثر كفاءة من تحديث كل سجل على حدة.
    """
    try:
        updates_to_perform = []
        barcode_map = {}

        for order_id in order_ids:
            order_id_str = str(order_id)
            barcode_data = generate_barcode(order_id_str)
            if barcode_data:
                barcode_map[order_id_str] = barcode_data
                update_payload = {
                    'barcode_data': barcode_data,
                    'barcode_generated_at': datetime.utcnow()
                }
                # يجب إضافة المفتاح الرئيسي للنموذج لكي يعمل التحديث الجماعي
                if order_type == 'salla':
                    update_payload['order_id'] = order_id_str
                else:
                    update_payload['id'] = int(order_id_str) # افترض أن id هو integer
                updates_to_perform.append(update_payload)
        
        if not updates_to_perform:
            return barcode_map

        with db_session_scope() as session:
            model = SallaOrder if order_type == 'salla' else CustomOrder
            session.bulk_update_mappings(model, updates_to_perform)
        
        return barcode_map
            
    except Exception as e:
        logger.error(f"خطأ في الحفظ الجماعي للباركود: {str(e)}")
        return {}

def generate_and_store_barcode(order_id, order_type='salla'):
    """إنشاء باركود وحفظه في قاعدة البيانات لسجل واحد"""
    order_id_str = str(order_id)
    barcode_data = generate_barcode(order_id_str)
    if not barcode_data:
        return None

    try:
        with db_session_scope():
            if order_type == 'salla':
                order = SallaOrder.query.get(order_id_str)
            else:
                order = CustomOrder.query.get(int(order_id_str))
            
            if order:
                order.barcode_data = barcode_data
                order.barcode_generated_at = datetime.utcnow()
                return barcode_data
        return None # في حال لم يتم العثور على الطلب
    except Exception as e:
        logger.error(f"خطأ في حفظ الباركود تلقائيًا للطلب {order_id_str}: {str(e)}")
        return None

def process_order_data(order_data, items_data, barcode_data):
    """معالجة ودمج بيانات الطلب والمنتجات والباركود"""
    order_id = str(order_data.get('id'))
    items = []
    
    for item in items_data:
        options = []
        if isinstance(item.get('options'), list):
            for option in item.get('options', []):
                options.append({
                    'name': option.get('name', ''),
                    'value': option.get('value', {}).get('name', 'غير محدد')
                })
        
        items.append({
            'id': item.get('id'),
            'name': item.get('name', ''),
            'sku': item.get('sku', ''),
            'quantity': item.get('quantity', 0),
            'main_image': item.get('product', {}).get('main_image', ''),
            'notes': item.get('notes', ''),
            'options': options,
        })
    
    return {
        'id': order_id,
        'reference_id': order_data.get('reference_id', order_id),
        'customer': order_data.get('customer', {}),
        'created_at': format_date(order_data.get('date', {}).get('iso')),
        'order_items': items,
        'barcode': barcode_data
    }

def format_date(date_str):
    """تنسيق التاريخ بشكل مقروء"""
    if not date_str:
        return 'غير معروف'
    try:
        # ISO 8601 format like "2023-09-21T10:30:00.000Z"
        dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        return dt.strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return date_str

def process_orders_in_parallel(order_ids, access_token):
    """
    معالجة الطلبات بشكل متوازٍ باستخدام Threads.
    تم التعديل لاستخدام سياق التطبيق بدلاً من إنشاء تطبيق جديد لكل خيط.
    """
    if not order_ids:
        return []
        
    barcodes_map = get_barcodes_for_orders(order_ids)
    app = current_app._get_current_object() # الحصول على نسخة من التطبيق الحالي مرة واحدة

    def fetch_order_data(order_id, result_queue):
        """دالة مساعدة لجلب بيانات الطلب، تعمل داخل كل خيط"""
        with app.app_context(): # استخدام سياق التطبيق لتمكين الوصول لقاعدة البيانات
            session = create_session()
            headers = {'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'}
            config = app.config
            
            try:
                order_response = session.get(
                    f"{config['SALLA_ORDERS_API']}/{order_id}",
                    headers=headers, timeout=15
                )
                order_response.raise_for_status() # التأكد من نجاح الطلب
                order_data = order_response.json().get('data', {})
                items_data = order_data.get('items', []) # غالبًا ما تكون المنتجات مضمنة

                order_id_str = str(order_id)
                barcode_data = barcodes_map.get(order_id_str)
                if not barcode_data:
                    barcode_data = generate_and_store_barcode(order_id_str, 'salla')

                if barcode_data:
                    processed_order = process_order_data(order_data, items_data, barcode_data)
                    result_queue.put(processed_order)
                else:
                    result_queue.put(None)

            except requests.exceptions.RequestException as e:
                logger.error(f"Network error fetching order {order_id}: {str(e)}")
                result_queue.put(None)
            except Exception as e:
                logger.error(f"Unexpected error processing order {order_id}: {str(e)}")
                result_queue.put(None)
            finally:
                # هذا مهم جداً: يعيد الاتصال إلى المجمع (pool) بعد انتهاء الخيط من عمله
                db.session.remove()

    orders = []
    result_queue = queue.Queue()
    threads = []
    max_workers = min(10, len(order_ids)) # تحديد عدد الخيوط بحد أقصى 10

    for order_id in order_ids:
        thread = threading.Thread(target=fetch_order_data, args=(order_id, result_queue))
        threads.append(thread)
        thread.start()
    
    for thread in threads:
        thread.join(timeout=45) # انتظار كل خيط لمدة أقصاها 45 ثانية

    while not result_queue.empty():
        result = result_queue.get()
        if result:
            orders.append(result)
            
    return orders

def get_salla_categories(access_token):
    """جلب التصنيفات من منصة سلة"""
    from .config import Config
    session = create_session()
    headers = {'Authorization': f'Bearer {access_token}', 'Accept': 'application/json'}
    try:
        response = session.get(Config.SALLA_CATEGORIES_API, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get('data', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching categories from Salla: {e}")
        return []

def humanize_time(dt):
    """تحويل التاريخ إلى نص مقروء مثل 'منذ دقيقة'"""
    if not isinstance(dt, datetime):
        return "" # التعامل مع المدخلات غير الصالحة
    now = datetime.utcnow()
    diff = now - dt
    
    seconds = diff.total_seconds()
    if seconds < 60:
        return "الآن"
    minutes = seconds / 60
    if minutes < 60:
        return f"منذ {int(minutes)} دقيقة"
    hours = minutes / 60
    if hours < 24:
        return f"منذ {int(hours)} ساعة"
    days = hours / 24
    return f"منذ {int(days)} يوم"
