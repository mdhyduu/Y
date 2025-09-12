from datetime import datetime
import os
import barcode
from barcode.writer import ImageWriter
from flask import current_app, redirect, request
from .models import db, User, Employee, CustomOrder, SallaOrder
import logging
from io import BytesIO
import base64

# إعداد المسجل
logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
UPLOAD_FOLDER = 'static/uploads/custom_orders'

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_next_order_number():
    """إنشاء رقم طلب تلقائي يبدأ من 1000"""
    last_order = CustomOrder.query.order_by(CustomOrder.id.desc()).first()
    if last_order and last_order.order_number:
        try:
            last_number = int(last_order.order_number)
            return str(last_number + 1)
        except ValueError:
            # إذا كان order_number ليس رقماً، نعود لاستخدام ID
            return str(last_order.id + 1000)
    return "1000"

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
                # الحصول على المستخدم الرئيسي للمتجر
                user = User.query.filter_by(store_id=employee.store_id).first()
                return user, employee
            return None, None
    except (ValueError, TypeError):
        # إذا كان user_id غير رقمي
        return None, None

def generate_barcode(data):
    """إنشاء باركود وإرجاعه كـ base64"""
    try:
        # إنشاء الباركود في الذاكرة
        buffer = BytesIO()
        writer = ImageWriter()
        code = barcode.get('code128', str(data), writer=writer)
        
        # حفظ في buffer بدلاً من ملف
        code.write(buffer, options={
            'write_text': False,
            'module_width': 0.4,
            'module_height': 15,
            'quiet_zone': 10
        })
        
        # تحويل إلى base64
        barcode_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return barcode_base64
        
    except Exception as e:
        logger.error(f"Error generating barcode: {str(e)}", exc_info=True)
        return None

def generate_and_store_barcode(order_id, order_type='salla'):
    """إنشاء باركود وحفظه في قاعدة البيانات تلقائيًا"""
    try:
        # إنشاء الباركود
        barcode_data = generate_barcode(order_id)
        
        if not barcode_data:
            logger.error(f"فشل في إنشاء الباركود للطلب {order_id}")
            return None
        
        # حفظ الباركود في قاعدة البيانات
        if order_type == 'salla':
            order = SallaOrder.query.get(order_id)
        else:
            order = CustomOrder.query.get(order_id)
            
        if order:
            order.barcode_data = barcode_data
            order.barcode_generated_at = datetime.utcnow()
            db.session.commit()
            logger.info(f"تم حفظ الباركود تلقائيًا للطلب {order_id}")
            return barcode_data
        else:
            logger.error(f"لم يتم العثور على الطلب {order_id} في قاعدة البيانات")
            return None
            
    except Exception as e:
        logger.error(f"خطأ في حفظ الباركود تلقائيًا: {str(e)}")
        return None

def format_date(date_str):
    try:
        # تحويل التاريخ من تنسيق سلة
        dt = datetime.strptime(date_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return date_str if date_str else 'غير معروف'

def process_order_data(order_id, items_data):
    """معالجة بيانات الطلب مع استخدام الباركود المخزن في قاعدة البيانات"""
    items = []
    logger.info(f"Processing order items: {len(items_data)} items")
    
    for index, item in enumerate(items_data):
        # تأكد من وجود معرف للمنتج، وإلا أنشئ واحدًا مؤقتًا
        item_id = item.get('id')
        if not item_id:
            item_id = f"temp_{index}"
            logger.warning(f"Item missing ID, using temporary ID: {item_id}")
        
        # معالجة الصور - التعديل هنا
        main_image = ''
        
        # المحاولة 1: استخدام product_thumbnail إذا موجود
        if not main_image:
            thumbnail_url = item.get('product_thumbnail') or item.get('thumbnail')
            if thumbnail_url and isinstance(thumbnail_url, str):
                main_image = thumbnail_url
                logger.info(f"Using product_thumbnail: {main_image}")
        
        # المحاولة 2: استخدام images إذا احتوت على بيانات
        if not main_image:
            images = item.get('images', [])
            if images and isinstance(images, list) and len(images) > 0:
                first_image = images[0]
                image_url = first_image.get('image', '')
                if image_url:
                    if not image_url.startswith(('http://', 'https://')):
                        base_domain = "https://cdn.salla.sa"
                        main_image = f"{base_domain}{image_url}"
                    else:
                        main_image = image_url
                    logger.info(f"Using images[0]: {main_image}")
        
        # المحاولة 3: استخدام الحقول الاحتياطية
        if not main_image:
            for field in ['image', 'url', 'image_url', 'picture']:
                if item.get(field):
                    main_image = item[field]
                    logger.info(f"Using backup field {field}: {main_image}")
                    break
        
        # معالجة الخيارات
        options = []
        item_options = item.get('options', [])
        if isinstance(item_options, list):
            for option in item_options:
                # استخراج القيمة الأساسية
                raw_value = option.get('value', '')
                display_value = 'غير محدد'
                
                # محاولة استخراج القيمة الرئيسية من الهيكل المعقد
                if isinstance(raw_value, dict):
                    # إذا كانت القيمة قاموساً، نبحث عن الحقل 'name' أو 'value'
                    display_value = raw_value.get('name') or raw_value.get('value') or str(raw_value)
                elif isinstance(raw_value, list):
                    # إذا كانت القيمة قائمة، نعالج كل عنصر فيها
                    values_list = []
                    for option_item in raw_value:  # تغيير اسم المتغير لتجنب التعارض
                        if isinstance(option_item, dict):
                            # للعناصر القاموسية في القائمة
                            value_str = option_item.get('name') or option_item.get('value') or str(option_item)
                            values_list.append(value_str)
                        else:
                            values_list.append(str(option_item))
                    display_value = ', '.join(values_list)
                else:
                    # في الحالات الأخرى نستخدم القيمة مباشرة
                    display_value = str(raw_value) if raw_value else 'غير محدد'
                
                # إضافة الخيار إلى القائمة
                options.append({
                    'name': option.get('name', ''),
                    'value': display_value,
                    'type': option.get('type', '')
                })
        
        # معالجة الأكواد الرقمية
        digital_codes = []
        for code in item.get('codes', []):
            if isinstance(code, dict):
                digital_codes.append({
                    'code': code.get('code', ''),
                    'status': code.get('status', 'غير معروف')
                })
        
        # معالجة الملفات الرقمية
        digital_files = []
        for file in item.get('files', []):
            if isinstance(file, dict):
                digital_files.append({
                    'url': file.get('url', ''),
                    'name': file.get('name', ''),
                    'size': file.get('size', 0)
                })
        
        # معالجة الحجوزات
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
            'id': item_id,  # استخدام item_id بدلاً من item.get('id')
            'name': item.get('name', ''),
            'description': item.get('notes', '')
        }
        
        item_data = {
            'id': item_id,  # استخدام item_id
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
        logger.info(f"Processed item: {item_data['name']} (ID: {item_id})")

    # البحث عن الباركود المخزن في قاعدة البيانات
        order = SallaOrder.query.get(order_id)
        barcode_data = None
        
        if order and order.barcode_data:
            barcode_data = order.barcode_data
            logger.info(f"Using existing barcode for order {order_id}")
        else:
            # إنشاء باركود جديد وحفظه في قاعدة البيانات
            barcode_data = generate_and_store_barcode(order_id, 'salla')
            if barcode_data:
                logger.info(f"Generated and stored new barcode for order {order_id}")
            else:
                logger.error(f"Failed to generate barcode for order {order_id}")
                # إنشاء باركود مؤقت دون تخزينه
                barcode_data = generate_barcode(order_id)

    processed_order = {
        'id': order_id,
        'order_items': items,
        'barcode': barcode_data  # استخدام الباركود من قاعدة البيانات
    }
    
    logger.info(f"Processed order with {len(items)} items and barcode")
    return processed_order

def get_salla_categories(access_token):
    import requests
    from .config import Config
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    try:
        response = requests.get(Config.SALLA_CATEGORIES_API, headers=headers)
        response.raise_for_status()
        return response.json().get('data', [])
    except requests.exceptions.RequestException as e:
        current_app.logger.error(f"Error fetching categories from Salla: {e}")
        return []    
        
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