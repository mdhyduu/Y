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

from app.models import SallaOrder, CustomOrder, OrderAddress # إضافة الاستيراد
from app.config import Config
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import json

# إعداد المسجل للإنتاج
logger = logging.getLogger('salla_app')

def get_orders_from_local_database(order_ids, store_id):
    """جلب الطلبات من قاعدة البيانات المحلية باستخدام full_order_data - محسن"""
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
        orders_without_items = 0
        
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
                    orders_without_items += 1
                    # يمكن محاولة جلب العناصر من API كحل بديل
                    continue
                
                # معالجة بيانات الطلب باستخدام البيانات المحلية
                processed_order = process_order_from_local_data(order, order_data, items_data)
                
                if processed_order and processed_order.get('order_items'):
                    processed_orders.append(processed_order)
                    logger.info(f"✅ تم معالجة الطلب {order.id} مع {len(processed_order['order_items'])} عنصر")
                else:
                    logger.warning(f"❌ الطلب {order.id} لا يحتوي على عناصر صالحة بعد المعالجة")
                    orders_without_items += 1
                    
            except Exception as e:
                logger.error(f"❌ خطأ في معالجة الطلب {order.id}: {str(e)}")
                continue
        
        logger.info(f"🎉 تم معالجة {len(processed_orders)} طلب بنجاح من البيانات المحلية، {orders_without_items} طلب بدون عناصر")
        return processed_orders
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب الطلبات من قاعدة البيانات: {str(e)}")
        return []

def process_order_from_local_data(order, order_data, items_data):
    """معالجة بيانات الطلب من البيانات المحلية - محسن"""
    try:
        # استخراج البيانات الأساسية
        customer = order_data.get('customer', {})
        customer_name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
        if not customer_name:
            customer_name = order_data.get('customer_name', 'عميل غير معروف')
        
        # معالجة العناصر مع تحسينات
        processed_items = []
        valid_items_count = 0
        
        for index, item in enumerate(items_data):
            try:
                # التحقق من صحة العنصر الأساسي
                if not item or not isinstance(item, dict):
                    logger.warning(f"⚠️ عنصر غير صالح في الفهرس {index} للطلب {order.id}")
                    continue
                
                item_id = item.get('id') or f"temp_{index}"
                item_name = item.get('name', '').strip()
                item_sku = item.get('sku', '').strip()
                item_quantity = item.get('quantity', 0)
                
                # تخطي العناصر بدون اسم أو SKU أو كمية
                if not item_name and not item_sku:
                    logger.warning(f"⚠️ عنصر بدون اسم أو SKU في الطلب {order.id}")
                    continue
                    
                if not item_quantity or item_quantity <= 0:
                    logger.warning(f"⚠️ عنصر بكمية غير صالحة في الطلب {order.id}: {item_quantity}")
                    continue
                
                # استخراج الصورة الرئيسية
                main_image = get_main_image_from_local(item)
                notes = item.get('notes', '') or item.get('note', '') or ''
                
                # معالجة الخيارات
                options = []
                item_options = item.get('options', [])
                if isinstance(item_options, list):
                    for option in item_options:
                        if not option or not isinstance(option, dict):
                            continue
                            
                        raw_value = option.get('value', '')
                        display_value = 'غير محدد'
                        
                        if isinstance(raw_value, dict):
                            display_value = raw_value.get('name') or raw_value.get('value') or str(raw_value)
                        elif isinstance(raw_value, list):
                            values_list = [str(opt.get('name') or opt.get('value') or str(opt)) 
                                         for opt in raw_value if isinstance(opt, (dict, str))]
                            display_value = ', '.join(values_list) if values_list else 'غير محدد'
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
                    'name': item_name or 'منتج بدون اسم',
                    'sku': item_sku or f"unknown_{item_id}",
                    'quantity': item_quantity,
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
                valid_items_count += 1
                
            except Exception as item_error:
                logger.error(f"❌ خطأ في معالجة العنصر {index} في الطلب {order.id}: {str(item_error)}")
                continue
        
        # إذا لم يكن هناك عناصر صالحة، نعيد None
        if valid_items_count == 0:
            logger.warning(f"⚠️ الطلب {order.id} لا يحتوي على عناصر صالحة بعد المعالجة")
            return None
        
        # الحصول على الباركود من قاعدة البيانات
        barcode_data = order.barcode_data if order else None
        
        # معالجة الباركود بشكل آمن
        if barcode_data:
            if isinstance(barcode_data, str):
                if barcode_data.startswith('iVBOR'):
                    barcode_data = f"data:image/png;base64,{barcode_data}"
                elif not barcode_data.startswith('data:image'):
                    # إذا كان الباركود ليس بصيغة صحيحة، نستخدم رقم الطلب لإنشاء باركود جديد
                    logger.warning(f"⚠️ تنسيق الباركود غير صحيح للطلب {order.id}")
                    barcode_data = generate_barcode(str(order.id))
            else:
                # إذا لم يكن الباركود نصاً، نستخدم رقم الطلب لإنشاء باركود جديد
                barcode_data = generate_barcode(str(order.id))
        else:
            # إذا لم يكن هناك باركود، ننشئ واحداً
            barcode_data = generate_barcode(str(order.id))
        
        # إنشاء كائن الطلب النهائي
        processed_order = {
            'id': str(order.id),
            'reference_id': order_data.get('reference_id', str(order.id)),
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
        
        logger.info(f"✅ تم معالجة الطلب {order.id} مع {valid_items_count} عنصر صالح")
        return processed_order
        
    except Exception as e:
        logger.error(f"❌ خطأ في معالجة البيانات المحلية للطلب {order.id if order else 'unknown'}: {str(e)}")
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
        orders_with_items = 0
        
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
                order_items = order.get('order_items', [])
                
                # تخطي الطلبات التي لا تحتوي على عناصر
                if not order_items:
                    logger.warning(f"⚠️ الطلب {order.get('id', '')} لا يحتوي على عناصر")
                    error_count += 1
                    continue
                
                # عد الطلبات التي تحتوي على عناصر
                orders_with_items += 1
                
                for item in order_items:
                    # التحقق من صحة بيانات العنصر
                    if not item or not isinstance(item, dict):
                        continue
                        
                    sku = item.get('sku', '').strip()
                    item_name = item.get('name', '').strip()
                    quantity = item.get('quantity', 0)
                    
                    # تخطي العناصر بدون اسم أو SKU
                    if not sku and not item_name:
                        continue
                        
                    # تخطي العناصر بكمية صفر أو غير صالحة
                    if not quantity or quantity <= 0:
                        continue
                    
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
                        'customer_mobile': order.get('customer', {}).get('mobile', ''),  # إضافة الجوال
                        'created_at': order.get('created_at', ''),
                        'quantity': quantity,
                        'options': item.get('options', []),
                        'barcode': order.get('barcode', ''),
                        'notes': item.get('notes', '')
                    }
                    
                    products_by_sku[sku]['order_appearances'].append(order_appearance)
                    products_by_sku[sku]['total_quantity'] += quantity
                
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
        
        logger.info(f"✅ تم تجميع {len(products_result)} منتج من {orders_with_items} طلب يحتوي على عناصر، إجمالي {success_count} طلب ناجح و {error_count} طلب فاشل")
        
        return jsonify({
            'success': True,
            'products': products_result,
            'stats': {
                'total_orders': len(order_ids),
                'successful_orders': success_count,
                'failed_orders': error_count,
                'orders_with_items': orders_with_items,
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
@orders_bp.route('/download_addresses_pdf')
def download_addresses_pdf():
    """تحميل عناوين الطلبات كملف PDF مع الباركود"""
    logger.info("بدء تحميل عناوين الطلبات كملف PDF")
    
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
        
        logger.info(f"🔄 معالجة {len(order_ids)} طلب لعناوين PDF")
        
        # استخدام البيانات المحلية
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
        
        # إضافة معلومات العنوان لكل طلب
        orders_with_addresses = []
        for order in orders:
            try:
                # جلب بيانات الطلب الكاملة للحصول على العنوان
                order_with_address = get_order_with_address(order['id'], user.store_id)
                if order_with_address:
                    orders_with_addresses.append(order_with_address)
            except Exception as e:
                logger.error(f"❌ خطأ في جلب عنوان الطلب {order.get('id', '')}: {str(e)}")
                continue
        
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # إنشاء HTML للعناوين
        html = render_template('print_addresses.html', 
                             orders=orders_with_addresses, 
                             current_time=current_time)
        
        # إنشاء PDF
        pdf = HTML(
            string=html,
            base_url=request.host_url
        ).write_pdf(
            optimize_size=(),
            jpeg_quality=80
        )
        
        filename = f"addresses_{current_time.replace(':', '-').replace(' ', '_')}.pdf"
        
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        response.headers['Content-Length'] = len(pdf)
        
        logger.info(f"✅ تم إنشاء عناوين PDF بنجاح: {filename} بحجم {len(pdf)} بايت")
        return response
        
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء عناوين PDF: {str(e)}")
        logger.error(traceback.format_exc())
        flash('حدث خطأ أثناء إنشاء ملف العناوين', 'error')
        return redirect(url_for('orders.index'))

def get_order_with_address(order_id, store_id):
    """جلب بيانات الطلب مع العنوان من نموذج OrderAddress الرسمي"""
    try:
        # جلب عنوان الطلب من نموذج OrderAddress مباشرة
        order_address = OrderAddress.query.filter_by(order_id=order_id).first()
        
        if not order_address:
            logger.warning(f"⚠️ لم يتم العثور على عنوان للطلب {order_id}")
            return None

        # استخدام البيانات مباشرة من نموذج OrderAddress
        address_info = {
            'name': order_address.name,
            'address': order_address.full_address,  # العنوان الكامل من الحقل المخصص
            'city': order_address.city,
            'state': order_address.country,  # لاحظ: في النموذج الحالي لا يوجد حقل state منفصل
            'country': order_address.country,
            'postal_code': '',  # يمكن إضافته إذا كان موجوداً في النموذج
            'mobile': order_address.phone,
            'additional_info': ''
        }

        # جلب بيانات الطلب الأساسية من SallaOrder
        order = SallaOrder.query.filter_by(id=order_id, store_id=store_id).first()
        if not order:
            logger.warning(f"⚠️ لم يتم العثور على الطلب {order_id} في SallaOrder")
            return None

        # الحصول على الباركود
        barcode_data = order.barcode_data
        if not barcode_data or not isinstance(barcode_data, str) or not barcode_data.startswith('data:image'):
            barcode_data = generate_barcode(str(order_id))

        return {
            'id': str(order.id),
            'reference_id': order.full_order_data.get('reference_id', str(order.id)) if order.full_order_data else str(order.id),
            'barcode': barcode_data,
            'address': address_info,
            'customer_name': order_address.name,
            'created_at': format_date(order.created_at)
        }
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب عنوان الطلب {order_id}: {str(e)}")
        return None

@orders_bp.route('/preview_addresses_html')
def preview_addresses_html():
    """معاينة عناوين الطلبات بتنسيق HTML"""
    logger.info("بدء معاينة عناوين الطلبات بتنسيق HTML")
    
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
        
        logger.info(f"🔄 معالجة {len(order_ids)} طلب لعناوين HTML")
        
        # استخدام البيانات المحلية
        orders_with_addresses = []
        for order_id in order_ids:
            order_with_address = get_order_with_address(order_id, user.store_id)
            if order_with_address:
                orders_with_addresses.append(order_with_address)
        
        if not orders_with_addresses:
            flash('لم يتم العثور على أي طلبات للمعاينة', 'error')
            return redirect(url_for('orders.index'))
        
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        return render_template('print_addresses.html', 
                             orders=orders_with_addresses, 
                             current_time=current_time)
        
    except Exception as e:
        logger.error(f"❌ خطأ في إنشاء معاينة العناوين: {str(e)}")
        logger.error(traceback.format_exc())
        flash('حدث خطأ أثناء إنشاء المعاينة', 'error')
        return redirect(url_for('orders.index'))
import urllib.parse

@orders_bp.route('/proxy-image')
def proxy_image():
    """خدمة Proxy محسنة لتحميل الصور وتجنب مشاكل CORS - تدعم البواليص"""
    try:  
        image_url = request.args.get('url')
        
        if not image_url:
            return redirect(url_for('static', filename='images/no-image.png'))
        
        # فك تشفير الرابط مرة واحدة فقط
        try:
            decoded_url = urllib.parse.unquote(image_url)
        except Exception as e:
            logger.warning(f"⚠️ لا يمكن فك تشفير الرابط، استخدام الرابط الأصلي: {image_url}")
            decoded_url = image_url
        
        # تنظيف الرابط وإصلاحه إذا لزم الأمر
        cleaned_url = clean_image_url(decoded_url)
        
        if not cleaned_url:
            return redirect(url_for('static', filename='images/no-image.png'))
        
        # ⭐⭐ إضافة headers إضافية للبواليص ⭐⭐
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'image/webp,image/apng,image/*,*/*;q=0.8',
            'Accept-Language': 'ar,en;q=0.9',
            'Referer': 'https://salla.sa/'
        }
        
        # إذا كان الرابط من سلة، نضيف المزيد من headers
        if 'salla.sa' in cleaned_url or 'cdn.salla.sa' in cleaned_url:
            headers.update({
                'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                'Sec-Fetch-Dest': 'image',
                'Sec-Fetch-Mode': 'no-cors',
                'Sec-Fetch-Site': 'same-site'
            })
        
        # تحميل الصورة من المصدر الأصلي
        response = requests.get(
            cleaned_url, 
            timeout=15,  # زيادة المهلة للبواليص
            headers=headers,
            stream=True  # لتحميل الملفات الكبيرة
        )
        
        if response.status_code == 200:
            # تحديد نوع المحتوى
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            
            # إرجاع الصورة مع الرأس المناسب
            proxy_response = make_response(response.content)
            proxy_response.headers.set('Content-Type', content_type)
            proxy_response.headers.set('Cache-Control', 'public, max-age=86400') # كاش لمدة يوم
            proxy_response.headers.set('Access-Control-Allow-Origin', '*')
            
            # ⭐⭐ إضافة headers إضافية للصور الكبيرة ⭐⭐
            content_length = response.headers.get('Content-Length')
            if content_length:
                proxy_response.headers.set('Content-Length', content_length)
                
            return proxy_response
        else:
            logger.warning(f"⚠️ فشل تحميل الصورة {cleaned_url}: {response.status_code}")
            return redirect(url_for('static', filename='images/no-image.png'))
            
    except requests.exceptions.Timeout:
        logger.error(f"⏰ انتهت مهلة تحميل الصورة: {image_url}")
        return redirect(url_for('static', filename='images/no-image.png'))
    except Exception as e:
        logger.error(f"❌ خطأ في proxy الصورة: {str(e)}")
        return redirect(url_for('static', filename='images/no-image.png'))

def clean_image_url(url):
    """تنظيف وإصلاح رابط الصورة"""
    if not url:
        return None
    
    try:
        # إزالة أي تشفير زائد
        cleaned = urllib.parse.unquote(url)
        
        # التأكد من أن الرابط يبدأ بـ http:// أو https://
        if not cleaned.startswith(('http://', 'https://')):
            # إذا كان الرابط نسبياً، نضيف domain سلة
            if cleaned.startswith('/'):
                cleaned = f"https://cdn.salla.sa{cleaned}"
            else:
                # إذا كان الرابط بدون scheme، نضيف https://
                cleaned = f"https://{cleaned}"
        
        # التحقق من صحة الرابط
        parsed = urllib.parse.urlparse(cleaned)
        if not parsed.netloc:
            return None
            
        return cleaned
        
    except Exception as e:
        logger.error(f"❌ خطأ في تنظيف الرابط {url}: {str(e)}")
        return None
import zipfile
from io import BytesIO
def extract_shipping_info(order_data):
    """استخراج معلومات الشحن من بيانات الطلب مع إضافة رابط البوليصة"""
    try:
        shipments_data = order_data.get('shipments', [])
        
        shipping_info = {
            'has_shipping': bool(shipments_data),
            'status': '',
            'tracking_number': None,
            'tracking_link': None,
            'has_tracking': False,
            'has_shipping_policy': False,
            'shipping_policy_url': None,  # رابط البوليصة
            'shipment_details': []
        }
        
        for shipment in shipments_data:
            shipment_tracking_link = shipment.get('tracking_link')
            shipment_tracking_number = shipment.get('tracking_number')
            shipment_label = shipment.get('label')
            
            # ⭐⭐ إضافة استخراج رابط البوليصة ⭐⭐
            shipment_policy_url = None
            if shipment_label and isinstance(shipment_label, dict):
                shipment_policy_url = shipment_label.get('url')
            
            shipment_has_tracking = False
            final_tracking_link = None
            
            if shipment_tracking_link and shipment_tracking_link not in ["", "0", "null", "None"]:
                if shipment_tracking_link.startswith(('http://', 'https://')):
                    final_tracking_link = shipment_tracking_link
                else:
                    final_tracking_link = f"https://track.salla.sa/track/{shipment_tracking_link}"
                shipment_has_tracking = True
            
            if not final_tracking_link and shipment_tracking_number:
                final_tracking_link = f"https://track.salla.sa/track/{shipment_tracking_number}"
                shipment_has_tracking = True
            
            shipment_info = {
                'id': shipment.get('id'),
                'courier_name': shipment.get('courier_name', ''),
                'courier_logo': shipment.get('courier_logo', ''),
                'tracking_number': shipment_tracking_number,
                'tracking_link': final_tracking_link,
                'has_tracking': shipment_has_tracking,
                'status': shipment.get('status', ''),
                'label': shipment_label,
                'has_label': bool(shipment_label and shipment_label not in ["", "0", "null"]),
                'shipping_policy_url': shipment_policy_url,  # إضافة رابط البوليصة
                'has_shipping_policy': bool(shipment_policy_url),  # التحقق من وجود بوليصة
                'shipping_number': shipment.get('shipping_number'),
                'total_weight': shipment.get('total_weight', {}),
                'packages': shipment.get('packages', [])
            }
            
            shipping_info['shipment_details'].append(shipment_info)
            
            # ⭐⭐ تحديث معلومات البوليصة العامة ⭐⭐
            if shipment_info['has_shipping_policy'] and not shipping_info['has_shipping_policy']:
                shipping_info['has_shipping_policy'] = True
                shipping_info['shipping_policy_url'] = shipment_policy_url
            
            if not shipping_info['status'] and shipment_info['status']:
                shipping_info['status'] = shipment_info['status']
            
            if shipment_has_tracking and not shipping_info['has_tracking']:
                shipping_info['tracking_link'] = final_tracking_link
                shipping_info['tracking_number'] = shipment_tracking_number
                shipping_info['has_tracking'] = True
        
        return shipping_info
    
    except Exception as e:
        logger.error(f"Error extracting shipping info: {str(e)}")
        return {}
@orders_bp.route('/get_single_order_data', methods=['POST'])
def get_single_order_data():
    """جلب بيانات طلب فردي للاستخدام في المتصفح مع معلومات الشحن"""
    try:
        user, employee = get_user_from_cookies()
        
        if not user:
            return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول أولاً'}), 401
        
        data = request.get_json()
        order_id = data.get('order_id')
        
        if not order_id:
            return jsonify({'success': False, 'error': 'لم يتم تحديد الطلب'}), 400
        
        # جلب الطلب من قاعدة البيانات
        orders = get_orders_from_local_database([order_id], user.store_id)
        
        if not orders:
            return jsonify({'success': False, 'error': 'لم يتم العثور على الطلب'}), 404
        
        order_data = orders[0]
        
        # ⭐⭐ إضافة معلومات الشحن والبوليصة ⭐⭐
        try:
            # جلب الطلب الكامل للحصول على بيانات الشحن
            order = SallaOrder.query.filter_by(id=str(order_id), store_id=user.store_id).first()
            if order and order.full_order_data:
                # استخراج معلومات الشحن
                shipping_info = extract_shipping_info(order.full_order_data)
                order_data['shipping'] = shipping_info
                
                # ⭐⭐ البحث عن رابط البوليصة في shipments ⭐⭐
                shipments = order.full_order_data.get('shipments', [])
                for shipment in shipments:
                    if isinstance(shipment, dict):
                        # البحث عن رابط البوليصة في shipment
                        label = shipment.get('label', {})
                        if isinstance(label, dict) and label.get('url'):
                            shipping_info['shipping_policy_url'] = label.get('url')
                            shipping_info['has_shipping_policy'] = True
                            break
                
        except Exception as e:
            logger.error(f"❌ خطأ في إضافة معلومات الشحن للطلب {order_id}: {str(e)}")
        
        return jsonify({
            'success': True,
            'order': order_data
        })
        
    except Exception as e:
        logger.error(f"❌ خطأ في جلب بيانات الطلب الفردي: {str(e)}")
        return jsonify({'success': False, 'error': 'حدث خطأ أثناء جلب البيانات'}), 500