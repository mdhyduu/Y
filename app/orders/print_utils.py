from flask import request, redirect, url_for, flash, make_response, current_app, render_template, jsonify
import requests
from datetime import datetime
from weasyprint import HTML
from . import orders_bp
from app.utils import (
    get_user_from_cookies, 
    process_order_data, 
    format_date, 
    create_session, 
    db_session_scope, 
    process_orders_concurrently,
    get_barcodes_for_orders,
    get_postgres_engine,
    generate_barcode
)
from app.models import SallaOrder, CustomOrder  # إضافة الاستيراد
from app.config import Config
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import json

# إعداد المسجل للإنتاج
logger = logging.getLogger('salla_app')

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
            SallaOrder.store_id == store_id,
            SallaOrder.full_order_data.isnot(None)
        ).all()
        
        logger.info(f"✅ تم العثور على {len(salla_orders)} طلب في قاعدة البيانات")
        
        processed_orders = []
        
        for order in salla_orders:
            try:
                # استخدام full_order_data المخزن محلياً
                order_data = order.full_order_data
                
                if not order_data:
                    logger.warning(f"⚠️ الطلب {order.id} لا يحتوي على full_order_data")
                    continue
                
                # استخراج العناصر من البيانات المحلية
                items_data = order_data.get('items', [])
                
                if not items_data:
                    logger.warning(f"⚠️ الطلب {order.id} لا يحتوي على عناصر في full_order_data")
                    # يمكن محاولة جلب العناصر من API كحل بديل
                    continue
                
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

def process_order_from_local_data(order, order_data, items_data):
    """معالجة بيانات الطلب من البيانات المحلية"""
    try:
        # استخراج البيانات الأساسية
        customer = order_data.get('customer', {})
        customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        if not customer_name:
            customer_name = order_data.get('customer_name', 'عميل غير معروف')
        
        # معالجة العناصر
        processed_items = []
        for index, item in enumerate(items_data):
            try:
                item_id = item.get('id') or f"temp_{index}"
                
                # استخراج الصورة الرئيسية
                main_image = get_main_image_from_local(item)
                
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
                
                # إنشاء بيانات العنصر
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
                    'options': options
                }
                
                processed_items.append(item_data)
                
            except Exception as item_error:
                logger.error(f"❌ خطأ في معالجة العنصر {index}: {str(item_error)}")
                continue
        
        # الحصول على الباركود من قاعدة البيانات - التصحيح هنا
        barcode_data = order.barcode_data if order else None
        
        # معالجة الباركود بشكل آمن
        if barcode_data:
            if isinstance(barcode_data, str):
                if barcode_data.startswith('iVBOR'):
                    barcode_data = f"data:image/png;base64,{barcode_data}"
                elif not barcode_data.startswith('data:image'):
                    # إذا كان الباركود ليس بصيغة صحيحة، نستخدم رقم الطلب لإنشاء باركود جديد
                    logger.warning(f"⚠️ تنسيق الباركود غير صحيح للطلب {order.id if order else 'unknown'}")
                    barcode_data = generate_barcode(order.id if order else 'unknown')
            else:
                # إذا لم يكن الباركود نصاً، نستخدم رقم الطلب لإنشاء باركود جديد
                barcode_data = generate_barcode(order.id if order else 'unknown')
        else:
            # إذا لم يكن هناك باركود، ننشئ واحداً
            barcode_data = generate_barcode(order.id if order else 'unknown')
        
        # إنشاء كائن الطلب النهائي
        processed_order = {
            'id': order.id if order else 'unknown',
            'reference_id': order_data.get('reference_id', order.id if order else 'unknown'),
            'order_items': processed_items,
            'barcode': barcode_data,
            'customer': {
                'name': customer_name,
                'email': customer.get('email', ''),
                'mobile': customer.get('mobile', '')
            },
            'created_at': format_date(order_data.get('created_at', order.created_at if order else None)),
            'amounts': order_data.get('amounts', {}),
            'status': order_data.get('status', {})
        }
        
        return processed_order
        
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة البيانات المحلية: {str(e)}")
        return None

def get_main_image_from_local(item):
    """استخراج الصورة الرئيسية من البيانات المحلية"""
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
        logger.error(f"❌ خطأ في استخراج الصورة: {str(e)}")
        return ''

def optimize_pdf_generation(orders):
    """تحسين أداء إنشاء PDF باستخدام الخيوط"""
    try:
        if not orders:
            return []
            
        # تقسيم الطلبات إلى مجموعات للمعالجة المتوازية
        def process_order_group(order_group):
            processed_orders = []
            for order in order_group:
                try:
                    # معالجة إضافية للطلاب إذا لزم الأمر
                    processed_order = {
                        'id': order.get('id', ''),
                        'reference_id': order.get('reference_id', order.get('id', '')),
                        'order_items': order.get('order_items', []),
                        'barcode': order.get('barcode', ''),
                        'customer': order.get('customer', {}),
                        'created_at': order.get('created_at', '')
                    }
                    processed_orders.append(processed_order)
                except Exception as e:
                    logger.error(f"Error processing order {order.get('id', '')}: {str(e)}")
                    continue
            return processed_orders
        
        # تقسيم الطلبات إلى مجموعات أصغر
        group_size = max(1, len(orders) // 4)  # 4 مجموعات كحد أقصى
        order_groups = [orders[i:i + group_size] for i in range(0, len(orders), group_size)]
        
        processed_orders = []
        lock = Lock()
        
        # معالجة المجموعات بشكل متزامن فقط إذا كانت هناك مجموعات
        if order_groups:
            # تأكد من أن max_workers لا يكون صفراً
            max_workers = max(1, min(4, len(order_groups)))
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_group = {
                    executor.submit(process_order_group, group): group 
                    for group in order_groups
                }
                
                for future in as_completed(future_to_group):
                    try:
                        result = future.result()
                        with lock:
                            processed_orders.extend(result)
                    except Exception as e:
                        logger.error(f"Error processing order group: {str(e)}")
        
        return processed_orders
        
    except Exception as e:
        logger.error(f"Error in optimize_pdf_generation: {str(e)}")
        return orders

@orders_bp.route('/download_orders_html')
def download_orders_html():
    """معاينة الطلبات بتنسيق HTML باستخدام البيانات المحلية"""
    logger.info("بدء معاينة الطلبات بتنسيق HTML (باستخدام البيانات المحلية)")
    
    try:
        user, employee = get_user_from_cookies()
        
        if not user:
            flash('الرجاء تسجيل الدخول أولاً', 'error')
            return redirect(url_for('user_auth.login'))
        
        order_ids = request.args.get('order_ids', '').split(',')
        
        # تصفية القائمة من القيم الفارغة
        order_ids = [order_id.strip() for order_id in order_ids if order_id.strip()]
        
        if not order_ids:
            flash('لم يتم تحديد أي طلبات للمعاينة', 'error')
            return redirect(url_for('orders.index'))
        
        logger.info(f"🔄 معالجة {len(order_ids)} طلب من البيانات المحلية")
        
        # استخدام البيانات المحلية بدلاً من API
        orders = get_orders_from_local_database(order_ids, user.store_id)
        
        if not orders:
            logger.warning("⚠️ لم يتم العثور على طلبات في البيانات المحلية، جاري استخدام API كبديل")
            # العودة إلى الطريقة القديمة كبديل
            access_token = user.salla_access_token
            if not access_token:
                flash('يجب ربط المتجر مع سلة أولاً', 'error')
                return redirect(url_for('auth.link_store'))
            
            max_workers = max(1, min(current_app.config.get('MAX_WORKERS', 10), len(order_ids)))
            orders = process_orders_concurrently(order_ids, access_token, max_workers)
        
        if not orders:
            flash('لم يتم العثور على أي طلبات للمعاينة', 'error')
            return redirect(url_for('orders.index'))
        
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # تحسين أداء العرض
        optimized_orders = optimize_pdf_generation(orders)
        
        return render_template('print_orders.html', 
                             orders=optimized_orders, 
                             current_time=current_time)
        
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء معاينة HTML: {str(e)}")
        logger.error(traceback.format_exc())
        flash('حدث خطأ أثناء إنشاء المعاينة', 'error')
        return redirect(url_for('orders.index'))

@orders_bp.route('/get_quick_list_data', methods=['POST'])
def get_quick_list_data():
    """جلب بيانات القائمة السريعة باستخدام البيانات المحلية"""
    try:
        with current_app.app_context():
            user, employee = get_user_from_cookies()
            
            if not user:
                return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401
        
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'error': 'لا توجد بيانات في الطلب'}), 400
        
        order_ids = data.get('order_ids', [])
        
        if not order_ids:
            return jsonify({'success': False, 'error': 'لم يتم تحديد أي طلبات'}), 400
        
        logger.info(f"🔄 جلب بيانات {len(order_ids)} طلب للقائمة السريعة من البيانات المحلية")
        
        # استخدام البيانات المحلية أولاً
        orders = get_orders_from_local_database(order_ids, user.store_id)
        
        if not orders:
            logger.warning("⚠️ لم يتم العثور على طلبات في البيانات المحلية، جاري استخدام API")
            # العودة إلى API كبديل
            access_token = user.salla_access_token
            if not access_token:
                return jsonify({'success': False, 'error': 'يجب ربط المتجر مع سلة أولاً'}), 400
            
            max_workers = max(1, min(current_app.config.get('MAX_WORKERS', 10), len(order_ids)))
            orders = process_orders_concurrently(order_ids, access_token, max_workers)
        
        orders_result = []
        success_count = 0
        error_count = 0
        
        if not orders:
            return jsonify({
                'success': True,
                'orders': [],
                'stats': {
                    'total': len(order_ids),
                    'successful': 0,
                    'failed': len(order_ids)
                }
            })
        
        # معالجة النتائج
        for order in orders:
            try:
                processed_items = []
                for item in order.get('order_items', []):
                    processed_items.append({
                        'name': item.get('name', ''),
                        'quantity': item.get('quantity', 0),
                        'main_image': item.get('main_image', ''),
                        'price': item.get('price', {}).get('amount', 0)
                    })
                
                order_data = {
                    'id': order.get('id', ''),
                    'reference_id': order.get('reference_id', order.get('id', '')),
                    'items': processed_items,
                    'customer_name': order.get('customer', {}).get('name', ''),
                    'created_at': order.get('created_at', '')
                }
                
                orders_result.append(order_data)
                success_count += 1
                
            except Exception as e:
                error_count += 1
                logger.error(f"❌ خطأ في معالجة الطلب {order.get('id', '')}: {str(e)}")
                continue
        
        logger.info(f"✅ تم معالجة {success_count} طلب بنجاح، وفشل {error_count} طلب")
        
        return jsonify({
            'success': True,
            'orders': orders_result,
            'stats': {
                'total': len(order_ids),
                'successful': success_count,
                'failed': error_count
            }
        })
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب بيانات القائمة السريعة: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': 'حدث خطأ أثناء جلب البيانات'}), 500

@orders_bp.route('/download_pdf')
def download_pdf():
    """تحميل الطلبات كملف PDF باستخدام البيانات المحلية"""
    logger.info("بدء تحميل الطلبات كملف PDF (باستخدام البيانات المحلية)")
    
    try:
        user, employee = get_user_from_cookies()
        
        if not user:
            flash('الرجاء تسجيل الدخول أولاً', 'error')
            return redirect(url_for('user_auth.login'))
        
        order_ids = request.args.get('order_ids', '').split(',')
        
        # تصفية القائمة من القيم الفارغة
        order_ids = [order_id.strip() for order_id in order_ids if order_id.strip()]
        
        if not order_ids:
            flash('لم يتم تحديد أي طلبات للتحميل', 'error')
            return redirect(url_for('orders.index'))
        
        logger.info(f"🔄 معالجة {len(order_ids)} طلب لتحويل PDF من البيانات المحلية")
        
        # استخدام البيانات المحلية بدلاً من API
        orders = get_orders_from_local_database(order_ids, user.store_id)
        
        if not orders:
            logger.warning("⚠️ لم يتم العثور على طلبات في البيانات المحلية، جاري استخدام API كبديل")
            # العودة إلى الطريقة القديمة كبديل
            access_token = user.salla_access_token
            if not access_token:
                flash('يجب ربط المتجر مع سلة أولاً', 'error')
                return redirect(url_for('auth.link_store'))
            
            max_workers = max(1, min(current_app.config.get('MAX_WORKERS', 10), len(order_ids)))
            orders = process_orders_concurrently(order_ids, access_token, max_workers)
        
        if not orders:
            flash('لم يتم العثور على أي طلبات للتحميل', 'error')
            return redirect(url_for('orders.index'))
        
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # تحسين أداء إنشاء PDF
        optimized_orders = optimize_pdf_generation(orders)
        
        # إنشاء HTML مع تحسينات الأداء
        html = render_template('print_orders.html', 
                             orders=optimized_orders, 
                             current_time=current_time)
        
        # تحسين إعدادات WeasyPrint للأداء
        pdf = HTML(
            string=html,
            base_url=request.host_url
        ).write_pdf(
            optimize_size=(),
            jpeg_quality=80
        )
        
        filename = f"orders_{current_time.replace(':', '-').replace(' ', '_')}.pdf"
        
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        response.headers['Content-Length'] = len(pdf)
        
        logger.info(f"✅ تم إنشاء PDF بنجاح: {filename} بحجم {len(pdf)} بايت")
        return response
        
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء PDF: {str(e)}")
        logger.error(traceback.format_exc())
        flash('حدث خطأ أثناء إنشاء PDF', 'error')
        return redirect(url_for('orders.index'))

# باقي الدوال تبقى كما هي...
import re
import unicodedata
from urllib.parse import quote

from sqlalchemy.sql import text

def group_products_by_sku_db(order_ids, store_id):
    """تجميع المنتجات مباشرة من قاعدة البيانات (PostgreSQL JSONB)"""
    engine = get_postgres_engine()
    
    # استعلام معدل مع GROUP BY صحيح
    query = text("""
        SELECT 
            COALESCE(item->>'sku', 'unknown_' || (item->>'id')) AS sku,
            item->>'name' AS name,
            COALESCE(item->>'product_thumbnail', item->>'thumbnail', '') AS thumbnail,
            SUM(COALESCE((item->>'quantity')::integer, 0)) AS total_quantity,
            COUNT(DISTINCT o.id) AS order_count,
            json_agg(
                json_build_object(
                    'order_id', o.id,
                    'customer_name', COALESCE(o.customer_name, ''),
                    'quantity', COALESCE((item->>'quantity')::integer, 0),
                    'created_at', o.created_at,
                    'barcode', o.barcode_data,
                    'options_text', (
                        SELECT string_agg(
                            COALESCE(opt->>'name', '') || ': ' || COALESCE(opt->>'value', ''), 
                            ' | '
                        )
                        FROM jsonb_array_elements(
                            CASE 
                                WHEN jsonb_typeof(COALESCE(item->'options', '[]'::jsonb)) = 'array' 
                                THEN COALESCE(item->'options', '[]'::jsonb)
                                ELSE '[]'::jsonb
                            END
                        ) AS opt
                    )
                )
            ) AS orders
        FROM salla_orders o,
        LATERAL jsonb_array_elements(
            CASE 
                WHEN jsonb_typeof(o.full_order_data->'items') = 'array' 
                THEN o.full_order_data->'items'
                ELSE '[]'::jsonb
            END
        ) AS item
        WHERE o.id = ANY(:order_ids) AND o.store_id = :store_id
        GROUP BY 
            item->>'sku',
            item->>'id',
            item->>'name',
            item->>'product_thumbnail',
            item->>'thumbnail'
    """)
    
    try:
        with engine.connect() as conn:
            result = conn.execute(query, {"order_ids": order_ids, "store_id": store_id}).mappings().all()
        
        return [dict(row) for row in result]
        
    except Exception as e:
        logger.error(f"❌ خطأ في تجميع المنتجات من قاعدة البيانات: {str(e)}")
        logger.error(traceback.format_exc())
        return []
def safe_filename(filename):
    """إنشاء اسم ملف آمن بدون أحرف خاصة"""
    try:
        # تحويل الأحرف العربية إلى مقابلاتها اللاتينية إن أمكن
        normalized = unicodedata.normalize('NFKD', filename)
        ascii_name = normalized.encode('ascii', 'ignore').decode('ascii')
        
        if not ascii_name:
            # إذا فشل التحويل، نستخدم تاريخ ووقت عادي
            return f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # إزالة أي أحرف غير مرغوب فيها
        safe_name = re.sub(r'[^\w\s-]', '', ascii_name).strip()
        return safe_name if safe_name else f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    except Exception as e:
        logger.error(f"Error creating safe filename: {str(e)}")
        return f"file_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


@orders_bp.route('/download_products_pdf')
def download_products_pdf():
    """تحميل الطلبات مجمعة حسب المنتج كملف ZIP يحتوي على PDF لكل منتج"""
    logger.info("بدء تحميل الطلبات مجمعة حسب المنتج")

    try:
        user, employee = get_user_from_cookies()
        
        if not user:
            flash('الرجاء تسجيل الدخول أولاً', 'error')
            return redirect(url_for('user_auth.login'))
        
        order_ids = request.args.get('order_ids', '').split(',')
        order_ids = [order_id.strip() for order_id in order_ids if order_id.strip()]
        
        if not order_ids:
            flash('لم يتم تحديد أي طلبات للتحميل', 'error')
            return redirect(url_for('orders.index'))
        
        logger.info(f"🔄 معالجة {len(order_ids)} طلب لتجميع المنتج من قاعدة البيانات")
        
        # ✅ استخدام الدالة المحسنة
        products_list = group_products_by_sku_db(order_ids, user.store_id)
        
        if not products_list:
            flash('لم يتم العثور على أي منتجات في الطلبات المحددة', 'error')
            return redirect(url_for('orders.index'))
        
        # تحويل القائمة إلى قاموس للتطابق مع الكود الحالي
        products_dict = {product['sku']: product for product in products_list}
        
        logger.info(f"📊 تم تجميع {len(products_dict)} منتج مختلف")
        
        # باقي الكود يبقى كما هو...
        import zipfile
        import tempfile
        import os
        
        with tempfile.TemporaryDirectory() as temp_dir:
            zip_filename = f"products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            zip_path = os.path.join(temp_dir, zip_filename)
            
            with zipfile.ZipFile(zip_path, 'w') as zip_file:
                for sku, product_data in products_dict.items():
                    pdf_content = generate_product_pdf(product_data, sku)
                    
                    if pdf_content:
                        pdf_filename = f"product_{safe_filename(sku)}_{len(product_data['orders'])}_orders.pdf"
                        zip_file.writestr(pdf_filename, pdf_content)
                        logger.info(f"✅ تم إنشاء PDF للمنتج {sku}")
            
            with open(zip_path, 'rb') as f:
                zip_data = f.read()
            
            response = make_response(zip_data)
            response.headers['Content-Type'] = 'application/zip'
            response.headers['Content-Disposition'] = f'attachment; filename="{zip_filename}"'
            response.headers['Content-Length'] = len(zip_data)
            
            logger.info(f"✅ تم إنشاء ZIP بنجاح يحتوي على {len(products_dict)} ملف PDF")
            return response
        
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء ZIP للمنتجات: {str(e)}")
        logger.error(traceback.format_exc())
        flash('حدث خطأ أثناء إنشاء ملف المنتجات', 'error')
        return redirect(url_for('orders.index'))
def generate_product_pdf(product_data, product_sku):
    """إنشاء PDF للمنتج بالتخطيط المضغوط"""
    try:
        # تحسين البيانات للتخطيط الجديد
        enhanced_product = {
            'name': product_data.get('name', 'غير معروف'),
            'sku': product_data.get('sku', product_sku),
            'thumbnail': product_data.get('thumbnail', ''),
            'total_quantity': product_data.get('total_quantity', 0),
            'orders': product_data.get('orders', [])
        }
        
        # إنشاء HTML بالتخطيط الجديد
        html_content = render_template('print_product.html', 
                                     product=enhanced_product,
                                     current_time=datetime.now().strftime('%H:%M:%S %d-%m-%Y'))
        
        # إعدادات PDF محسنة للكثافة
        pdf = HTML(
            string=html_content,
            base_url=request.host_url
        ).write_pdf(
            optimize_size=(),
            jpeg_quality=95,
            dpi=300,
            presentational_hints=True
        )
        
        logger.info(f"✅ تم إنشاء PDF مضغوط للمنتج {product_sku}")
        return pdf
        
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء PDF مضغوط: {str(e)}")
        return None
def optimize_orders_layout(orders, orders_per_page=8):
    """تحسين تخطيط الطلبات للعرض الشبكي"""
    try:
        if not orders:
            return []
            
        # تجميع الطلبات في صفحات
        optimized_orders = []
        current_page = []
        
        for i, order in enumerate(orders):
            current_page.append(order)
            
            # عند الوصول إلى العدد المطلوب لكل صفحة، نضيف فاصل
            if len(current_page) >= orders_per_page:
                optimized_orders.extend(current_page)
                if i < len(orders) - 1:  # لا نضيف فاصل بعد آخر صفحة
                    optimized_orders.append({'page_break': True})
                current_page = []
        
        # إضافة أي طلبات متبقية
        if current_page:
            optimized_orders.extend(current_page)
        
        return optimized_orders
        
    except Exception as e:
        logger.error(f"❌ خطأ في تحسين التخطيط: {str(e)}")
        return orders
# باقي الدوال تبقى كما هي...