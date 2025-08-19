from datetime import datetime
import os
import barcode
from barcode.writer import ImageWriter
from flask import current_app
import logging

# إعداد المسجل
logger = logging.getLogger(__name__)

def generate_barcode(data, prefix=""):
    """إنشاء باركود مخصص مع إمكانية إضافة بادئة"""
    try:
        # مسار لحفظ صور الباركود
        barcode_folder = current_app.config['BARCODE_FOLDER']
        if not os.path.exists(barcode_folder):
            os.makedirs(barcode_folder)
        
        # نستخدم نوع Code128 للباركود
        writer = ImageWriter()
        code = barcode.get('code128', str(data), writer=writer)
        
        # إنشاء اسم ملف فريد
        filename = f"{prefix}{data}"
        filepath = os.path.join(barcode_folder, filename)
        
        # حفظ الصورة
        code.save(filepath, options={
            'write_text': False,  # عدم عرض النص تحت الباركود
            'module_width': 0.4,  # عرض العناصر في الباركود
            'module_height': 15,   # ارتفاع الباركود
            'quiet_zone': 10       # المساحة الفارغة حول الباركود
        })
        
        return f"{filename}.png"
    except Exception as e:
        logger.error(f"Error generating barcode: {str(e)}", exc_info=True)
        return None

def generate_order_barcode(order_id):
    """إنشاء باركود للطلب"""
    return generate_barcode(order_id, prefix="order_")

def generate_item_barcode(order_id, item_id):
    """إنشاء باركود خاص بكل عنصر في الطلب"""
    return generate_barcode(f"{order_id}-{item_id}", prefix="item_")

def format_date(date_str):
    try:
        # تحويل التاريخ من تنسيق سلة
        dt = datetime.strptime(date_str.split('.')[0], '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%Y-%m-%d %H:%M')
    except:
        return date_str if date_str else 'غير معروف'
def process_order_data(order_id, items_data):
    """معالجة بيانات الطلب لتتناسب مع القالب مع إضافة الباركود لكل منتج"""
    items = []
    logger.info(f"Processing order items: {len(items_data)} items")
    
    for index, item in enumerate(items_data):
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
        
        # ... بقية الكود كما هو ...
                logger.info(f"Item images data: {item.get('images')}")
        logger.info(f"Processed image URL: {main_image}")
        # معالجة الخيارات
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
                    for item in raw_value:
                        if isinstance(item, dict):
                            # للعناصر القاموسية في القائمة
                            value_str = item.get('name') or item.get('value') or str(item)
                            values_list.append(value_str)
                        else:
                            values_list.append(str(item))
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
        
        # إنشاء باركود للعنصر
        item_id = item.get('id')
        item_barcode = generate_item_barcode(order_id, item_id) if item_id else None
        
        product_info = {
            'id': item.get('id'),
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
            'barcode': item_barcode,  # الباركود الخاص بالمنتج
            'codes': digital_codes,
            'files': digital_files,
            'reservations': reservations,
            'product': product_info
        }
        
        items.append(item_data)
        logger.info(f"Processed item: {item_data['name']} with barcode: {item_barcode or 'None'}")

    processed_order = {
        'reference_id': order_id,  # تغيير id إلى reference_id
        'order_items': items,
        'barcode': generate_order_barcode(order_id)  # الباركود الرئيسي للطلب
    }
    
    logger.info(f"Processed order with {len(items)} items and barcode: {processed_order['barcode']}")
    return processed_order
def get_salla_categories(access_token):
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    try:
        response = requests.get(Config.SALLA_CATEGORIES_API, headers=headers)
        response.raise_for_status()
        return response.json().get('data', [])
    except requests.exceptions.RequestException as e:
        app.logger.error(f"Error fetching categories from Salla: {e}")
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