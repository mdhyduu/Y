from flask import request, redirect, url_for, flash, make_response, current_app, render_template, jsonify, session
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
from app.models import SallaOrder, CustomOrder
from app.config import Config
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import json
import base64
from io import BytesIO
import gc
import time

logger = logging.getLogger('salla_app')

# ===== إعدادات الأداء =====
MAX_ORDERS_FOR_PRINT = 200  # الحد الأقصى للطلبات في الطباعة الواحدة
BATCH_SIZE = 50  # حجم الدفعة للمعالجة
MAX_PDF_SIZE_MB = 50  # الحد الأقصى لحجم PDF

def get_orders_from_local_database(order_ids, store_id):
    """جلب الطلبات من قاعدة البيانات المحلية - محسن للأداء"""
    try:
        logger.info(f"🔍 جلب {len(order_ids)} طلب من قاعدة البيانات المحلية")
        
        # التحقق من الحد الأقصى
        if len(order_ids) > MAX_ORDERS_FOR_PRINT:
            logger.warning(f"⚠️ عدد الطلبات ({len(order_ids)}) يتجاوز الحد الأقصى ({MAX_ORDERS_FOR_PRINT})")
            order_ids = order_ids[:MAX_ORDERS_FOR_PRINT]
        
        order_ids_str = [str(oid).strip() for oid in order_ids if str(oid).strip()]
        
        if not order_ids_str:
            logger.warning("❌ لا توجد معرفات طلبات صالحة")
            return []
        
        # تقسيم الطلبات إلى دفعات للمعالجة
        batches = [order_ids_str[i:i + BATCH_SIZE] for i in range(0, len(order_ids_str), BATCH_SIZE)]
        
        processed_orders = []
        
        for batch_index, batch in enumerate(batches):
            try:
                logger.info(f"🔧 معالجة الدفعة {batch_index + 1}/{len(batches)} ({len(batch)} طلب)")
                
                salla_orders = SallaOrder.query.filter(
                    SallaOrder.id.in_(batch),
                    SallaOrder.store_id == store_id,
                    SallaOrder.full_order_data.isnot(None)
                ).all()
                
                for order in salla_orders:
                    try:
                        order_data = order.full_order_data
                        
                        if not order_data:
                            continue
                        
                        items_data = order_data.get('items', [])
                        
                        if not items_data:
                            continue
                        
                        processed_order = process_order_from_local_data(order, order_data, items_data)
                        
                        if processed_order:
                            processed_orders.append(processed_order)
                            
                    except Exception as e:
                        logger.error(f"❌ خطأ في معالجة الطلب {order.id}: {str(e)}")
                        continue
                
                # تنظيف الذاكرة بعد كل دفعة
                gc.collect()
                
            except Exception as batch_error:
                logger.error(f"❌ خطأ في معالجة الدفعة {batch_index + 1}: {str(batch_error)}")
                continue
        
        logger.info(f"🎉 تم معالجة {len(processed_orders)} طلب بنجاح من البيانات المحلية")
        return processed_orders
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب الطلبات من قاعدة البيانات: {str(e)}")
        return []

def process_order_from_local_data(order, order_data, items_data):
    """معالجة بيانات الطلب من البيانات المحلية - محسنة للأداء"""
    try:
        customer = order_data.get('customer', {})
        customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        if not customer_name:
            customer_name = order_data.get('customer_name', 'عميل غير معروف')
        
        processed_items = []
        for index, item in enumerate(items_data):
            try:
                item_id = item.get('id') or f"temp_{index}"
                
                # تحسين استخراج الصور - استخدام الصور المصغرة فقط
                main_image = get_optimized_image_from_local(item)
                notes = item.get('notes', '') or item.get('note', '') or ''
                
                options = []
                item_options = item.get('options', [])
                if isinstance(item_options, list):
                    for option in item_options[:5]:  # الحد الأقصى لخيارات
                        raw_value = option.get('value', '')
                        display_value = 'غير محدد'
                        
                        if isinstance(raw_value, dict):
                            display_value = raw_value.get('name') or raw_value.get('value') or str(raw_value)
                        elif isinstance(raw_value, list):
                            values_list = [str(opt.get('name') or opt.get('value') or str(opt)) 
                                         for opt in raw_value[:3] if isinstance(opt, (dict, str))]  # الحد لـ 3 قيم
                            display_value = ', '.join(values_list)
                        else:
                            display_value = str(raw_value) if raw_value else 'غير محدد'
                        
                        options.append({
                            'name': option.get('name', '')[:50],  # تقليل طول النص
                            'value': display_value[:100],  # تقليل طول النص
                            'type': option.get('type', '')
                        })
                
                item_data = {
                    'id': item_id,
                    'name': item.get('name', '')[:100],  # تقليل طول النص
                    'sku': item.get('sku', '')[:50],
                    'quantity': item.get('quantity', 0),
                    'currency': item.get('currency', 'SAR'),
                    'price': {
                        'amount': item.get('amounts', {}).get('price_without_tax', {}).get('amount', 0),
                        'currency': item.get('currency', 'SAR')
                    },
                    'main_image': main_image,
                    'options': options,
                    'notes': notes[:200]  # تقليل طول الملاحظات
                }
                
                processed_items.append(item_data)
                
            except Exception as item_error:
                logger.error(f"❌ خطأ في معالجة العنصر {index}: {str(item_error)}")
                continue
        
        # تحسين معالجة الباركود
        barcode_data = None
        if order and order.barcode_data:
            try:
                barcode_data = order.barcode_data
                if isinstance(barcode_data, str) and barcode_data.startswith('iVBOR'):
                    barcode_data = f"data:image/png;base64,{barcode_data}"
                elif not (isinstance(barcode_data, str) and barcode_data.startswith('data:image')):
                    barcode_data = generate_barcode(str(order.id))
            except Exception as barcode_error:
                logger.warning(f"⚠️ خطأ في معالجة الباركود: {str(barcode_error)}")
                barcode_data = generate_barcode(str(order.id))
        else:
            barcode_data = generate_barcode(str(order.id) if order else 'unknown')
        
        processed_order = {
            'id': order.id if order else 'unknown',
            'reference_id': order_data.get('reference_id', order.id if order else 'unknown'),
            'order_items': processed_items,
            'barcode': barcode_data,
            'customer': {
                'name': customer_name[:50],
                'email': customer.get('email', '')[:50],
                'mobile': customer.get('mobile', '')[:20]
            },
            'created_at': format_date(order_data.get('created_at', order.created_at if order else None)),
            'amounts': order_data.get('amounts', {}),
            'status': order_data.get('status', {})
        }
        
        return processed_order
        
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة البيانات المحلية: {str(e)}")
        return None

def get_optimized_image_from_local(item):
    """استخراج الصورة الرئيسية مع تحسينات الأداء"""
    try:
        # أولوية للصور المصغرة لتقليل حجم البيانات
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
        
        # إذا لم توجد صور مصغرة، ابحث في مصفوفة الصور
        images = item.get('images', [])
        if images and isinstance(images, list):
            for image in images[:2]:  # الحد لصورة واحدة أو اثنتين فقط
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
def aggregate_products_for_printing(orders):
    """تجميع المنتجات من جميع الطلبات حسب SKU - مع إصلاح الخطأ"""
    try:
        products_by_sku = {}
        order_count = 0
        
        for order in orders:
            order_count += 1
            
            if order_count % 10 == 0:
                logger.info(f"📊 تجميع المنتجات: معالجة الطلب {order_count}/{len(orders)}")
            
            # التحقق من أن order هو قاموس وليس عدد
            if not isinstance(order, dict):
                logger.warning(f"⚠️ الطلب ليس قاموسًا: {type(order)} - {order}")
                continue
            
            # التحقق من وجود order_items وأنها قائمة
            order_items = order.get('order_items')
            if not order_items or not isinstance(order_items, list):
                logger.warning(f"⚠️ لا توجد order_items في الطلب {order.get('id', 'unknown')}")
                continue
            
            for item in order_items:
                # التحقق من أن item هو قاموس
                if not isinstance(item, dict):
                    logger.warning(f"⚠️ العنصر ليس قاموسًا في الطلب {order.get('id', 'unknown')}: {type(item)}")
                    continue
                
                try:
                    # استخراج SKU مع التحقق من الأنواع
                    sku = str(item.get('sku', '')) if item.get('sku') is not None else ''
                    item_name = str(item.get('name', '')) if item.get('name') is not None else ''
                    
                    if not sku:
                        sku = item_name
                    if not sku:
                        sku = f"item_{item.get('id', 'unknown')}"
                    
                    # التحقق من أن SKU نصي
                    sku = str(sku)
                    
                    if sku not in products_by_sku:
                        products_by_sku[sku] = {
                            'sku': sku,
                            'name': item_name[:100] if item_name else 'منتج غير معروف',
                            'main_image': item.get('main_image', ''),
                            'price': 0,
                            'total_quantity': 0,
                            'orders': []
                        }
                    
                    # استخراج السعر بشكل آمن
                    price_data = item.get('price', {})
                    if isinstance(price_data, dict):
                        price_amount = price_data.get('amount', 0)
                    else:
                        price_amount = 0
                    
                    # تحويل السعر إلى عدد إذا كان نصاً
                    if isinstance(price_amount, str):
                        try:
                            price_amount = float(price_amount.replace(',', ''))
                        except (ValueError, AttributeError):
                            price_amount = 0
                    
                    products_by_sku[sku]['price'] = price_amount
                    
                    # استخراج الكمية بشكل آمن
                    quantity = item.get('quantity', 0)
                    if isinstance(quantity, str):
                        try:
                            quantity = int(quantity)
                        except (ValueError, AttributeError):
                            quantity = 0
                    
                    # إنشاء بيانات الطلب بشكل آمن
                    order_appearance = {
                        'order_id': str(order.get('id', '')) if order.get('id') is not None else '',
                        'reference_id': str(order.get('reference_id', order.get('id', ''))) if order.get('reference_id') is not None else '',
                        'customer_name': str(order.get('customer', {}).get('name', 'غير محدد')) if order.get('customer') else 'غير محدد',
                        'customer_mobile': str(order.get('customer', {}).get('mobile', '')) if order.get('customer') else '',
                        'created_at': str(order.get('created_at', '')),
                        'quantity': quantity,
                        'options': item.get('options', []),
                        'barcode': order.get('barcode', ''),
                        'notes': str(item.get('notes', '')) if item.get('notes') is not None else ''
                    }
                    
                    products_by_sku[sku]['orders'].append(order_appearance)
                    products_by_sku[sku]['total_quantity'] += quantity
                    
                except Exception as item_error:
                    logger.error(f"❌ خطأ في معالجة عنصر في الطلب {order.get('id', 'unknown')}: {str(item_error)}")
                    continue
        
        # تحويل إلى قائمة وترتيب
        products_list = []
        for sku, product_data in products_by_sku.items():
            try:
                # ترتيب الطلبات بحيث تكون الأحدث أولاً
                product_data['orders'].sort(key=lambda x: x.get('created_at', ''), reverse=True)
                
                products_list.append({
                    'sku': product_data['sku'],
                    'name': product_data['name'],
                    'main_image': product_data['main_image'],
                    'price': product_data['price'],
                    'total_quantity': product_data['total_quantity'],
                    'orders': product_data['orders'][:100]  # الحد الأقصى لعدد الطلبات لكل منتج
                })
            except Exception as list_error:
                logger.error(f"❌ خطأ في إضافة المنتج {sku} إلى القائمة: {str(list_error)}")
                continue
        
        # ترتيب المنتجات حسب الكمية الإجمالية
        products_list.sort(key=lambda x: x.get('total_quantity', 0), reverse=True)
        
        logger.info(f"✅ تم تجميع {len(products_list)} منتج من {order_count} طلب")
        return products_list
        
    except Exception as e:
        logger.error(f"❌ خطأ في تجميع المنتجات: {str(e)}")
        logger.error(traceback.format_exc())
        return []
def get_print_data_from_server(order_ids, user):
    """جلب بيانات الطباعة من الخادم بالكامل - محسن للأداء"""
    try:
        start_time = time.time()
        logger.info(f"🔄 بدء معالجة {len(order_ids)} طلب على الخادم")
        
        # التحقق من الحد الأقصى
        if len(order_ids) > MAX_ORDERS_FOR_PRINT:
            original_count = len(order_ids)
            order_ids = order_ids[:MAX_ORDERS_FOR_PRINT]
            logger.warning(f"⚠️ تم تقليل عدد الطلبات من {original_count} إلى {MAX_ORDERS_FOR_PRINT}")
        
        orders = get_orders_from_local_database(order_ids, user.store_id)
        
        if not orders:
            logger.warning("⚠️ لم يتم العثور على طلبات في البيانات المحلية، جاري استخدام API")
            access_token = user.salla_access_token
            if not access_token:
                return None
            
            max_workers = max(1, min(current_app.config.get('MAX_WORKERS', 5), len(order_ids)))  # تقليل العمال
            orders = process_orders_concurrently(order_ids, access_token, max_workers)
        
        if not orders:
            return None
        
        products = aggregate_products_for_printing(orders)
        
        total_orders = len(orders)
        total_products = len(products)
        total_quantity = sum(product['total_quantity'] for product in products)
        total_items = total_quantity
        
        print_data = {
            'products': products,
            'summary': {
                'totalProducts': total_products,
                'totalOrders': total_orders,
                'totalQuantity': total_quantity,
                'totalItems': total_items,
                'originalRequestCount': len(order_ids),
                'processingTime': round(time.time() - start_time, 2)
            },
            'timestamp': datetime.now().isoformat()
        }
        
        logger.info(f"✅ تم تجميع {total_products} منتج من {total_orders} طلب في {print_data['summary']['processingTime']} ثانية")
        return print_data
        
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة البيانات على الخادم: {str(e)}")
        return None

@orders_bp.route('/server_quick_list_print')
def server_quick_list_print():
    """عرض صفحة الطباعة مع البيانات المعالجة على الخادم - محسنة للأداء"""
    try:
        user, employee = get_user_from_cookies()
        
        if not user:
            flash('الرجاء تسجيل الدخول أولاً', 'error')
            return redirect(url_for('user_auth.login'))
        
        order_ids = request.args.get('order_ids', '').split(',')
        order_ids = [order_id.strip() for order_id in order_ids if order_id.strip()]
        
        if not order_ids:
            flash('لم يتم تحديد أي طلبات للطباعة', 'error')
            return redirect(url_for('orders.index'))
        
        # التحقق من الحد الأقصى وإظهار تحذير
        if len(order_ids) > MAX_ORDERS_FOR_PRINT:
            flash(f'تم تحديد {len(order_ids)} طلب، سيتم معالجة أول {MAX_ORDERS_FOR_PRINT} طلب فقط لأسباب أدائية', 'warning')
        
        logger.info(f"🔄 معالجة {len(order_ids)} طلب للطباعة على الخادم")
        
        print_data = get_print_data_from_server(order_ids, user)
        
        if not print_data:
            flash('لم يتم العثور على بيانات للطباعة', 'error')
            return redirect(url_for('orders.index'))
        
        # إضافة معلومات الأداء للقالب
        print_data['performance'] = {
            'max_orders': MAX_ORDERS_FOR_PRINT,
            'is_truncated': len(order_ids) > MAX_ORDERS_FOR_PRINT
        }
        
        # حفظ البيانات في الجلسة للوصول السريع
        session_key = f"print_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        session[session_key] = print_data
        session['last_print_session'] = session_key
        
        return render_template('server_quick_list_print.html', 
                             print_data=print_data,
                             session_key=session_key)
        
    except Exception as e:
        logger.error(f"❌ خطأ في عرض صفحة الطباعة: {str(e)}")
        logger.error(traceback.format_exc())
        flash('حدث خطأ أثناء تحضير صفحة الطباعة', 'error')
        return redirect(url_for('orders.index'))

@orders_bp.route('/download_server_pdf')
def download_server_pdf():
    """تحميل PDF مع البيانات المعالجة على الخادم - محسن للأداء"""
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
        
        # التحقق من الحد الأقصى
        if len(order_ids) > MAX_ORDERS_FOR_PRINT:
            order_ids = order_ids[:MAX_ORDERS_FOR_PRINT]
            flash(f'تم تقليل عدد الطلبات إلى {MAX_ORDERS_FOR_PRINT} لأسباب أدائية', 'warning')
        
        logger.info(f"🔄 معالجة {len(order_ids)} طلب لتحويل PDF على الخادم")
        
        print_data = get_print_data_from_server(order_ids, user)
        
        if not print_data:
            flash('لم يتم العثور على بيانات للتحميل', 'error')
            return redirect(url_for('orders.index'))
        
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # استخدام قالب مبسط للPDF
        html = render_template('optimized_pdf_template.html', 
                             print_data=print_data,
                             current_time=current_time)
        
        # إعدادات WeasyPrint محسنة للأداء
        pdf = HTML(
            string=html,
            base_url=request.host_url
        ).write_pdf(
            optimize_size=('images', 'fonts', 'pdf'),
            jpeg_quality=60,  # تقليل جودة الصور
            full_document=False,
            uncompressed_pdf=True,
            attachments=None
        )
        
        # التحقق من حجم PDF
        pdf_size_mb = len(pdf) / (1024 * 1024)
        if pdf_size_mb > MAX_PDF_SIZE_MB:
            logger.warning(f"⚠️ حجم PDF كبير جداً: {pdf_size_mb:.2f} MB")
            flash(f'ملف PDF كبير جداً ({pdf_size_mb:.1f} MB). يرجى تقليل عدد الطلبات.', 'warning')
        
        filename = f"orders_{current_time.replace(':', '-').replace(' ', '_')}.pdf"
        
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        response.headers['Content-Length'] = len(pdf)
        
        logger.info(f"✅ تم إنشاء PDF بنجاح: {filename} ({pdf_size_mb:.2f} MB)")
        return response
        
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء PDF: {str(e)}")
        logger.error(traceback.format_exc())
        flash('حدث خطأ أثناء إنشاء PDF. يرجى تقليل عدد الطلبات.', 'error')
        return redirect(url_for('orders.index'))

# ===== دوال التحكم في الذاكرة =====

def cleanup_memory():
    """تنظيف الذاكرة"""
    gc.collect()

def validate_order_count(order_ids, max_allowed=MAX_ORDERS_FOR_PRINT):
    """التحقق من عدد الطلبات"""
    if len(order_ids) > max_allowed:
        return order_ids[:max_allowed], f"تم تقليل العدد من {len(order_ids)} إلى {max_allowed}"
    return order_ids, None

# ... [باقي الدوال الحالية تبقى كما هي] ...

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
                notes = item.get('notes', '') or item.get('note', '') or ''
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
                    'options': options,
                    'notes': notes
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
    """جلب بيانات القائمة السريعة مع تجميع المنتجات حسب SKU من جميع الطلبات"""
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
        
        # تجميع المنتجات حسب SKU من جميع الطلبات
        products_by_sku = {}
        success_count = 0
        error_count = 0
        
        if not orders:
            return jsonify({
                'success': True,
                'products': [],
                'stats': {
                    'total': len(order_ids),
                    'successful': 0,
                    'failed': len(order_ids)
                }
            })
        
        # تجميع جميع المنتجات من جميع الطلبات حسب SKU
        for order in orders:
            try:
                for item in order.get('order_items', []):
                    sku = item.get('sku', '')
                    item_name = item.get('name', '')
                    
                    # استخدام الاسم إذا لم يكن هناك SKU
                    if not sku:
                        sku = item_name
                    
                    # إذا كان SKU لا يزال فارغاً، استخدم معرف العنصر
                    if not sku:
                        sku = f"item_{item.get('id', 'unknown')}"
                    
                    if sku not in products_by_sku:
                        products_by_sku[sku] = {
                            'sku': sku,
                            'name': item_name,
                            'main_image': item.get('main_image', ''),
                            'price': item.get('price', {}).get('amount', 0),
                            'total_quantity': 0,
                            'order_appearances': []  # ظهور المنتج في الطلبات المختلفة
                        }
                    
                    # إضافة ظهور المنتج في هذا الطلب
                    order_appearance = {
                        'order_id': order.get('id', ''),
                        'reference_id': order.get('reference_id', order.get('id', '')),
                        'customer_name': order.get('customer', {}).get('name', ''),
                        'created_at': order.get('created_at', ''),
                        'quantity': item.get('quantity', 0),
                        'options': item.get('options', []),
                        'barcode': order.get('barcode', ''),
                        'notes': item.get('notes', '')
                        
                    }
                    
                    products_by_sku[sku]['order_appearances'].append(order_appearance)
                    products_by_sku[sku]['total_quantity'] += item.get('quantity', 0)
                
                success_count += 1
                
            except Exception as e:
                error_count += 1
                logger.error(f"❌ خطأ في معالجة الطلب {order.get('id', '')}: {str(e)}")
                continue
        
        # تحويل القاموس إلى قائمة منتجات
        products_result = []
        for sku, product_data in products_by_sku.items():
            # ترتيب الطلبات بحيث تكون الأحدث أولاً
            product_data['order_appearances'].sort(key=lambda x: x.get('created_at', ''), reverse=True)
            
            products_result.append({
                'sku': product_data['sku'],
                'name': product_data['name'],
                'main_image': product_data['main_image'],
                'price': product_data['price'],
                'total_quantity': product_data['total_quantity'],
                'appearances_count': len(product_data['order_appearances']),
                'order_appearances': product_data['order_appearances']
            })
        
        # ترتيب المنتجات حسب الكمية الإجمالية (من الأكبر إلى الأصغر)
        products_result.sort(key=lambda x: x['total_quantity'], reverse=True)
        
        logger.info(f"✅ تم تجميع {len(products_result)} منتج من {success_count} طلب بنجاح، وفشل {error_count} طلب")
        
        return jsonify({
            'success': True,
            'products': products_result,
            'stats': {
                'total_orders': len(order_ids),
                'successful_orders': success_count,
                'failed_orders': error_count,
                'total_products': len(products_result),
                'total_items': sum(product['total_quantity'] for product in products_result)
            }
        })
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب بيانات القائمة السريعة: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': 'حدث خطأ أثناء جلب البيانات'}), 500
import re
import unicodedata
@orders_bp.route('/quick_list_print')
def quick_list_print():
    """عرض صفحة الطباعة للقائمة السريعة"""
    return render_template('quick_list_print.html')
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
