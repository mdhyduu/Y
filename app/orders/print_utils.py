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
    get_postgres_engine
)
from app.config import Config
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# إعداد المسجل للإنتاج
logger = logging.getLogger('salla_app')

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
    """معاينة الطلبات بتنسيق HTML للإنتاج - نسخة محسنة"""
    logger.info("بدء معاينة الطلبات بتنسيق HTML (نسخة محسنة)")
    
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
        
        logger.info(f"معالجة {len(order_ids)} طلب للمعاينة باستخدام الخيوط")
        
        access_token = user.salla_access_token
        
        if not access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            return redirect(url_for('auth.link_store'))
        
        # استخدام المعالجة المتزامنة مع عدد عمال ديناميكي
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
        logger.error(f"خطأ في إنشاء معاينة HTML: {str(e)}")
        logger.error(traceback.format_exc())
        flash('حدث خطأ أثناء إنشاء المعاينة', 'error')
        return redirect(url_for('orders.index'))

@orders_bp.route('/get_quick_list_data', methods=['POST'])
def get_quick_list_data():
    """جلب بيانات القائمة السريعة مع إصلاح سياق التطبيق"""
    try:
        # Remove the manual app_context() call as it's not needed here
        user, employee = get_user_from_cookies()
        
        if not user:
            return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401
        
        data = request.get_json()
        
        if not data:
            return jsonify({'success': False, 'error': 'لا توجد بيانات في الطلب'}), 400
        
        order_ids = data.get('order_ids', [])
        
        if not order_ids:
            return jsonify({'success': False, 'error': 'لم يتم تحديد أي طلبات'}), 400
        
        access_token = user.salla_access_token
        if not access_token:
            return jsonify({'success': False, 'error': 'يجب ربط المتجر مع سلة أولاً'}), 400
        
        logger.info(f"معالجة {len(order_ids)} طلب للقائمة السريعة باستخدام الخيوط")
        
        # استخدام المعالجة المتزامنة مع عدد عمال ديناميكي
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
        
        # الحصول على كائن التطبيق للاستخدام في الخيوط
        app = current_app._get_current_object()
        
        def process_single_order(order):
            nonlocal success_count, error_count
            try:
                # استخدام سياق التطبيق في الخيط
                with app.app_context():
                    processed_items = []
                    for item in order.get('order_items', []):
                        processed_items.append({
                            'name': item.get('name', ''),
                            'quantity': item.get('quantity', 0),
                            'main_image': item.get('main_image', ''),
                            'sku': item.get('sku', ''),
                            'price': item.get('price', {}).get('amount', 0)
                        })
                    
                    order_data = {
                        'id': order.get('id', ''),
                        'reference_id': order.get('reference_id', order.get('id', '')),
                        'items': processed_items,
                        'customer_name': order.get('customer', {}).get('name', ''),
                        'created_at': order.get('created_at', '')
                    }
                    success_count += 1
                    return order_data
                    
            except Exception as e:
                error_count += 1
                logger.error(f"خطأ في معالجة الطلب {order.get('id', '')}: {str(e)}")
                return None
        
        # معالجة الطلبات بشكل متزامن
        max_workers_processing = max(1, min(5, len(orders)))
        with ThreadPoolExecutor(max_workers=max_workers_processing) as executor:
            future_to_order = {
                executor.submit(process_single_order, order): order 
                for order in orders
            }
            
            for future in as_completed(future_to_order):
                result = future.result()
                if result:
                    orders_result.append(result)
        
        logger.info(f"تم معالجة {success_count} طلب بنجاح، وفشل {error_count} طلب")
        
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
        logger.error(f"خطأ في جلب بيانات القائمة السريعة: {str(e)}")
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': 'حدث خطأ أثناء جلب البيانات'}), 500
@orders_bp.route('/download_pdf')
def download_pdf():
    """تحميل الطلبات كملف PDF للإنتاج - نسخة محسنة"""
    logger.info("بدء تحميل الطلبات كملف PDF (نسخة محسنة)")
    
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
        
        logger.info(f"معالجة {len(order_ids)} طلب لتحويل PDF باستخدام الخيوط")
        
        access_token = user.salla_access_token
        
        if not access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            return redirect(url_for('auth.link_store'))
        
        # استخدام المعالجة المتزامنة مع عدد عمال ديناميكي
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
            base_url=request.host_url  # لتحميل الموارد بشكل صحيح
        ).write_pdf(
            optimize_size=('fonts', 'images'),  # تحسين حجم الخطوط والصور
            jpeg_quality=80  # تقليل جودة الصور لتقليل الحجم
        )
        
        filename = f"orders_{current_time.replace(':', '-').replace(' ', '_')}.pdf"
        
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        response.headers['Content-Length'] = len(pdf)
        
        logger.info(f"تم إنشاء PDF بنجاح: {filename} بحجم {len(pdf)} بايت")
        return response
        
    except Exception as e:
        logger.error(f"خطأ في إنشاء PDF: {str(e)}")
        logger.error(traceback.format_exc())
        flash('حدث خطأ أثناء إنشاء PDF', 'error')
        return redirect(url_for('orders.index'))

@orders_bp.route('/bulk_operations_status', methods=['POST'])
def bulk_operations_status():
    """تتبع حالة العمليات المجمعة - وظيفة جديدة"""
    try:
        user, employee = get_user_from_cookies()
        
        if not user:
            return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401
        
        data = request.get_json()
        operation_type = data.get('operation_type', '')
        order_ids = data.get('order_ids', [])
        
        if not operation_type or not order_ids:
            return jsonify({'success': False, 'error': 'بيانات غير كافية'}), 400
        
        # هنا يمكنك إضافة منطق لتتبع حالة العمليات المجمعة
        # يمكن استخدام قاعدة البيانات أو Redis للتتبع
        
        return jsonify({
            'success': True,
            'operation_type': operation_type,
            'total_orders': len(order_ids),
            'status': 'processing',
            'progress': 0
        })
        
    except Exception as e:
        logger.error(f"خطأ في تتبع حالة العمليات المجمعة: {str(e)}")
        return jsonify({'success': False, 'error': 'حدث خطأ أثناء تتبع الحالة'}), 500

def preload_barcode_data(order_ids):
    """تحميل مسبق لبيانات الباركود - تحسين للأداء"""
    try:
        if not order_ids:
            return {}
        
        # استخدام الدالة المحسنة من utils.py
        barcodes_map = get_barcodes_for_orders(order_ids)
        
        # إذا كانت هناك طلبات بدون باركود، إنشاؤها بشكل مجمع
        missing_barcodes = [oid for oid in order_ids if str(oid) not in barcodes_map]
        
        if missing_barcodes:
            from app.utils import bulk_generate_and_store_barcodes
            new_barcodes = bulk_generate_and_store_barcodes(missing_barcodes, 'salla')
            barcodes_map.update(new_barcodes)
        
        return barcodes_map
        
    except Exception as e:
        logger.error(f"Error in preload_barcode_data: {str(e)}")
        return {}