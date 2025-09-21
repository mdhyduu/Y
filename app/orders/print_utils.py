from flask import request, redirect, url_for, flash, make_response, current_app, render_template, jsonify
import requests
from datetime import datetime
from weasyprint import HTML
from . import orders_bp
from app.utils import get_user_from_cookies, process_order_data, format_date, create_session, db_session_scope, check_db_connection
from app.config import Config
import concurrent.futures
from app import create_app, db
import logging

# إعداد المسجل للإنتاج
logger = logging.getLogger('salla_app')

# عدد العمال الافتراضي للطلبات المتوازية
DEFAULT_WORKERS = 3  # تقليل عدد العمال لتقليل الضغط على قاعدة البيانات

def fetch_order_data(order_id, access_token):
    """جلب بيانات طلب واحدة باستخدام الجلسة المحسنة"""
    app = create_app()
    with app.app_context():
        try:
            # فحص اتصال قاعدة البيانات قبل المتابعة
            if not check_db_connection():
                logger.error(f"فشل اتصال قاعدة البيانات للطلب {order_id}")
                return None
                
            session = create_session()
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Accept': 'application/json'
            }
            
            # جلب بيانات الطلب الأساسية
            order_response = session.get(
                f"{Config.SALLA_ORDERS_API}/{order_id}",
                headers=headers,
                timeout=10
            )
            
            if order_response.status_code != 200:
                return None
                
            order_data = order_response.json().get('data', {})
            
            # جلب عناصر الطلب
            items_response = session.get(
                f"{Config.SALLA_BASE_URL}/orders/items",
                params={'order_id': order_id},
                headers=headers,
                timeout=10
            )
            
            items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
            
            return {
                'order_id': order_id,
                'order_data': order_data,
                'items_data': items_data
            }
            
        except Exception as e:
            logger.error(f"Error fetching order {order_id}: {str(e)}")
            return None
        finally:
            # التأكد من إغلاق اتصال قاعدة البيانات
            db.session.close()

def fetch_orders_parallel(order_ids, access_token, max_workers=DEFAULT_WORKERS):
    """جلب بيانات الطلبات بشكل متوازي باستخدام الجلسة المحسنة"""
    orders_data = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_order = {
            executor.submit(fetch_order_data, order_id, access_token): order_id 
            for order_id in order_ids
        }
        
        for future in concurrent.futures.as_completed(future_to_order):
            order_id = future_to_order[future]
            try:
                result = future.result()
                if result:
                    orders_data.append(result)
            except Exception as e:
                logger.error(f"Order {order_id} generated an exception: {str(e)}")
    
    return orders_data

@orders_bp.route('/download_orders_html')
def download_orders_html():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    order_ids = request.args.get('order_ids', '').split(',')
    if not order_ids or order_ids == ['']:
        flash('لم يتم تحديد أي طلبات للمعاينة', 'error')
        return redirect(url_for('orders.index'))
    
    try:
        access_token = user.salla_access_token
        
        if not access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            return redirect(url_for('auth.link_store'))
        
        orders_data = fetch_orders_parallel(order_ids, access_token)
        
        if not orders_data:
            flash('لم يتم العثور على أي طلبات للمعاينة', 'error')
            return redirect(url_for('orders.index'))
        
        orders = []
        with db_session_scope():
            for data in orders_data:
                try:
                    processed_order = process_order_data(data['order_id'], data['items_data'])
                    
                    processed_order['reference_id'] = data['order_data'].get('reference_id', data['order_id'])
                    processed_order['customer'] = data['order_data'].get('customer', {})
                    processed_order['created_at'] = format_date(data['order_data'].get('created_at', ''))
                    
                    orders.append(processed_order)
                except Exception as e:
                    logger.error(f"Error processing order {data['order_id']}: {str(e)}")
                    continue
        
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        return render_template('print_orders.html', 
                             orders=orders, 
                             current_time=current_time)
        
    except Exception as e:
        logger.error(f"Error generating HTML: {str(e)}")
        flash('حدث خطأ أثناء إنشاء المعاينة', 'error')
        return redirect(url_for('orders.index'))    

@orders_bp.route('/get_quick_list_data', methods=['POST'])
def get_quick_list_data():
    """جلب بيانات القائمة السريعة للطلبات المحددة"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401
    
    data = request.get_json()
    order_ids = data.get('order_ids', [])
    
    if not order_ids:
        return jsonify({'success': False, 'error': 'لم يتم تحديد أي طلبات'}), 400
    
    access_token = user.salla_access_token
    if not access_token:
        return jsonify({'success': False, 'error': 'يجب ربط المتجر مع سلة أولاً'}), 400
    
    orders_data = fetch_orders_parallel(order_ids, access_token, max_workers=min(5, len(order_ids)))  # تقليل الحد الأقصى للعمال
    
    orders_result = []
    
    for data in orders_data:
        try:
            order_data = data['order_data']
            items_data = data['items_data']
            
            processed_items = []
            for item in items_data:
                main_image = ''
                thumbnail_url = item.get('product_thumbnail') or item.get('thumbnail')
                if thumbnail_url and isinstance(thumbnail_url, str):
                    main_image = thumbnail_url
                else:
                    images = item.get('images', [])
                    if images and isinstance(images, list) and len(images) > 0:
                        first_image = images[0]
                        image_url = first_image.get('image', '')
                        if image_url:
                            if not image_url.startswith(('http://', 'https://')):
                                main_image = f"https://cdn.salla.sa{image_url}"
                            else:
                                main_image = image_url
                    else:
                        for field in ['image', 'url', 'image_url', 'picture']:
                            if item.get(field):
                                main_image = item[field]
                                break
                
                processed_items.append({
                    'name': item.get('name', ''),
                    'quantity': item.get('quantity', 0),
                    'main_image': main_image
                })
            
            orders_result.append({
                'id': data['order_id'],
                'reference_id': order_data.get('reference_id', data['order_id']),
                'items': processed_items
            })
            
        except Exception as e:
            logger.error(f"Error processing order {data['order_id']} for quick list: {str(e)}")
            continue
    
    return jsonify({
        'success': True,
        'orders': orders_result
    })

@orders_bp.route('/download_pdf')
def download_pdf():
    """تحميل الطلبات كملف PDF"""
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    order_ids = request.args.get('order_ids', '').split(',')
    if not order_ids or order_ids == ['']:
        flash('لم يتم تحديد أي طلبات للتحميل', 'error')
        return redirect(url_for('orders.index'))
    
    try:
        access_token = user.salla_access_token
        
        if not access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            return redirect(url_for('auth.link_store'))
        
        orders_data = fetch_orders_parallel(order_ids, access_token)
        
        if not orders_data:
            flash('لم يتم العثور على أي طلبات للتحميل', 'error')
            return redirect(url_for('orders.index'))
        
        orders = []
        with db_session_scope():
            for data in orders_data:
                try:
                    processed_order = process_order_data(data['order_id'], data['items_data'])
                    
                    processed_order['reference_id'] = data['order_data'].get('reference_id', data['order_id'])
                    processed_order['customer'] = data['order_data'].get('customer', {})
                    processed_order['created_at'] = format_date(data['order_data'].get('created_at', ''))
                    
                    orders.append(processed_order)
                except Exception as e:
                    logger.error(f"Error processing order {data['order_id']}: {str(e)}")
                    continue
        
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        html = render_template('print_orders.html', 
                             orders=orders, 
                             current_time=current_time)
        
        pdf = HTML(string=html).write_pdf()
        
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename=orders_{current_time.replace(":", "-")}.pdf'
        
        return response
        
    except Exception as e:
        logger.error(f"Error generating PDF: {str(e)}")
        flash('حدث خطأ أثناء إنشاء PDF', 'error')
        return redirect(url_for('orders.index'))