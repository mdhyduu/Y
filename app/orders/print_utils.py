from flask import request, redirect, url_for, flash, make_response, current_app, render_template, jsonify
import requests
from datetime import datetime
from weasyprint import HTML
from . import orders_bp
from app.utils import get_user_from_cookies, process_order_data, format_date, create_session, db_session_scope, process_orders_sequentially, get_barcodes_for_orders
from app.config import Config
import logging
import traceback

# إعداد المسجل للإنتاج
logger = logging.getLogger('salla_app')

@orders_bp.route('/download_orders_html')
def download_orders_html():
    """معاينة الطلبات بتنسيق HTML مع تصحيح الأخطاء المفصل"""
    logger.info("=== بدء معاينة الطلبات بتنسيق HTML ===")
    
    try:
        user, employee = get_user_from_cookies()
        logger.debug(f"بيانات المستخدم: {user is not None}, بيانات الموظف: {employee is not None}")
        
        if not user:
            flash('الرجاء تسجيل الدخول أولاً', 'error')
            logger.warning("محاولة وصول بدون تسجيل دخول")
            return redirect(url_for('user_auth.login'))
        
        order_ids = request.args.get('order_ids', '').split(',')
        logger.debug(f"معرفات الطلبات المستلمة: {order_ids}")
        
        # تصفية القائمة من القيم الفارغة
        order_ids = [order_id.strip() for order_id in order_ids if order_id.strip()]
        
        if not order_ids:
            flash('لم يتم تحديد أي طلبات للمعاينة', 'error')
            logger.warning("طلب معاينة بدون تحديد طلبات")
            return redirect(url_for('orders.index'))
        
        logger.info(f"عدد الطلبات المحددة: {len(order_ids)}")
        
        access_token = user.salla_access_token
        
        if not access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            logger.warning("المستخدم ليس لديه token وصول لسلة")
            return redirect(url_for('auth.link_store'))
        
        logger.debug("بدء معالجة الطلبات بشكل تسلسلي...")
        
        # استخدام الدالة المحسنة من utils.py
        orders = process_orders_sequentially(order_ids, access_token)
        logger.debug(f"عدد الطلبات التي تم جلبها: {len(orders) if orders else 0}")
        
        if not orders:
            flash('لم يتم العثور على أي طلبات للمعاينة', 'error')
            logger.warning("لم يتم العثور على أي طلبات بعد المعالجة")
            return redirect(url_for('orders.index'))
        
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.info(f"تم إنشاء المعاينة بنجاح في: {current_time}")
        
        return render_template('print_orders.html', 
                             orders=orders, 
                             current_time=current_time)
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"خطأ في إنشاء معاينة HTML: {str(e)}")
        logger.debug(f"تفاصيل الخطأ: {error_details}")
        flash('حدث خطأ أثناء إنشاء المعاينة', 'error')
        return redirect(url_for('orders.index'))

@orders_bp.route('/get_quick_list_data', methods=['POST'])
def get_quick_list_data():
    """جلب بيانات القائمة السريعة للطلبات المحددة مع تصحيح الأخطاء"""
    logger.info("=== بدء جلب بيانات القائمة السريعة ===")
    
    try:
        user, employee = get_user_from_cookies()
        logger.debug(f"بيانات المستخدم: {user is not None}")
        
        if not user:
            logger.warning("محاولة وصول بدون تسجيل دخول للقائمة السريعة")
            return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401
        
        data = request.get_json()
        logger.debug(f"بيانات الطلب المستلمة: {data}")
        
        if not data:
            logger.warning("لا توجد بيانات في الطلب")
            return jsonify({'success': False, 'error': 'لا توجد بيانات في الطلب'}), 400
        
        order_ids = data.get('order_ids', [])
        logger.debug(f"معرفات الطلبات: {order_ids}")
        
        if not order_ids:
            logger.warning("لم يتم تحديد أي طلبات في القائمة السريعة")
            return jsonify({'success': False, 'error': 'لم يتم تحديد أي طلبات'}), 400
        
        access_token = user.salla_access_token
        if not access_token:
            logger.warning("لا يوجد token وصول لسلة للقائمة السريعة")
            return jsonify({'success': False, 'error': 'يجب ربط المتجر مع سلة أولاً'}), 400
        
        logger.info(f"بدء معالجة {len(order_ids)} طلب للقائمة السريعة...")
        
        # استخدام الدالة المحسنة من utils.py
        orders = process_orders_sequentially(order_ids, access_token)
        logger.debug(f"عدد الطلبات المعالجة: {len(orders) if orders else 0}")
        
        orders_result = []
        success_count = 0
        error_count = 0
        
        for order in orders:
            try:
                processed_items = []
                for item in order.get('order_items', []):
                    processed_items.append({
                        'name': item.get('name', ''),
                        'quantity': item.get('quantity', 0),
                        'main_image': item.get('main_image', '')
                    })
                
                orders_result.append({
                    'id': order.get('id', ''),
                    'reference_id': order.get('reference_id', order.get('id', '')),
                    'items': processed_items
                })
                success_count += 1
                
            except Exception as e:
                error_count += 1
                logger.error(f"خطأ في معالجة الطلب {order.get('id', '')} للقائمة السريعة: {str(e)}")
                logger.debug(f"تفاصيل الخطأ: {traceback.format_exc()}")
                continue
        
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
        error_details = traceback.format_exc()
        logger.error(f"خطأ في جلب بيانات القائمة السريعة: {str(e)}")
        logger.debug(f"تفاصيل الخطأ: {error_details}")
        return jsonify({'success': False, 'error': 'حدث خطأ أثناء جلب البيانات'}), 500

@orders_bp.route('/download_pdf')
def download_pdf():
    """تحميل الطلبات كملف PDF مع تصحيح الأخطاء"""
    logger.info("=== بدء تحميل الطلبات كملف PDF ===")
    
    try:
        user, employee = get_user_from_cookies()
        logger.debug(f"بيانات المستخدم: {user is not None}")
        
        if not user:
            flash('الرجاء تسجيل الدخول أولاً', 'error')
            logger.warning("محاولة تحميل PDF بدون تسجيل دخول")
            return redirect(url_for('user_auth.login'))
        
        order_ids = request.args.get('order_ids', '').split(',')
        logger.debug(f"معرفات الطلبات المستلمة: {order_ids}")
        
        # تصفية القائمة من القيم الفارغة
        order_ids = [order_id.strip() for order_id in order_ids if order_id.strip()]
        
        if not order_ids:
            flash('لم يتم تحديد أي طلبات للتحميل', 'error')
            logger.warning("طلب تحميل PDF بدون تحديد طلبات")
            return redirect(url_for('orders.index'))
        
        logger.info(f"عدد الطلبات المحددة للPDF: {len(order_ids)}")
        
        access_token = user.salla_access_token
        
        if not access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            logger.warning("المستخدم ليس لديه token وصول لسلة لتحميل PDF")
            return redirect(url_for('auth.link_store'))
        
        logger.debug("بدء معالجة الطلبات لتحويل PDF...")
        
        # استخدام الدالة المحسنة من utils.py
        orders = process_orders_sequentially(order_ids, access_token)
        logger.debug(f"عدد الطلبات التي تم جلبها للPDF: {len(orders) if orders else 0}")
        
        if not orders:
            flash('لم يتم العثور على أي طلبات للتحميل', 'error')
            logger.warning("لم يتم العثور على أي طلبات لتحويل PDF")
            return redirect(url_for('orders.index'))
        
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        logger.debug("بدء إنشاء HTML لتحويل PDF...")
        
        html = render_template('print_orders.html', 
                             orders=orders, 
                             current_time=current_time)
        
        logger.debug("بدء تحويل HTML إلى PDF...")
        pdf = HTML(string=html).write_pdf()
        
        filename = f"orders_{current_time.replace(':', '-').replace(' ', '_')}.pdf"
        
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        
        logger.info(f"تم إنشاء PDF بنجاح: {filename}")
        return response
        
    except Exception as e:
        error_details = traceback.format_exc()
        logger.error(f"خطأ في إنشاء PDF: {str(e)}")
        logger.debug(f"تفاصيل الخطأ: {error_details}")
        flash('حدث خطأ أثناء إنشاء PDF', 'error')
        return redirect(url_for('orders.index'))