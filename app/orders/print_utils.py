# orders/print_utils.py
from flask import request, redirect, url_for, flash, make_response, current_app, render_template, jsonify
import requests
from datetime import datetime
from weasyprint import HTML
from . import orders_bp
from app.utils import get_user_from_cookies, process_order_data, format_date
from app.config import Config


# orders/print_utils.py


@orders_bp.route('/download_orders_html')
def download_orders_html():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    order_ids = request.args.get('order_ids', '').split(',')
    if not order_ids or order_ids == ['']:
        flash('لم يتم تحديد أي طلبات للتحميل', 'error')
        return redirect(url_for('orders.index'))
    
    try:
        # جلب بيانات الطلبات المحددة
        orders = []
        access_token = user.salla_access_token
        
        if not access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            return redirect(url_for('auth.link_store'))
            
        headers = {'Authorization': f'Bearer {access_token}'}
        
        for order_id in order_ids:
            try:
                # جلب بيانات الطلب الأساسية
                order_response = requests.get(
                    f"{Config.SALLA_ORDERS_API}/{order_id}",
                    headers=headers,
                    timeout=10
                )
                
                if order_response.status_code != 200:
                    continue
                    
                order_data = order_response.json().get('data', {})
                
                # جلب عناصر الطلب
                items_response = requests.get(
                    f"{Config.SALLA_BASE_URL}/orders/items",
                    params={'order_id': order_id},
                    headers=headers,
                    timeout=10
                )
                
                items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
                
                # معالجة بيانات الطلب بنفس طريقة PDF
                processed_order = process_order_data(order_id, items_data)
                
                # إضافة معلومات إضافية بنفس طريقة PDF
                processed_order['reference_id'] = order_data.get('reference_id', order_id)
                processed_order['customer'] = order_data.get('customer', {})
                processed_order['created_at'] = format_date(order_data.get('created_at', ''))
                
                orders.append(processed_order)
                
            except Exception as e:
                current_app.logger.error(f"Error fetching order {order_id}: {str(e)}")
                continue
        
        if not orders:
            flash('لم يتم العثور على أي طلبات للتحميل', 'error')
            return redirect(url_for('orders.index'))
        
        # إضافة الوقت الحالي للقالب
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # استخدام نفس قالب PDF لضمان التماثل في التصميم
        html_content = render_template('print_orders.html', 
                                     orders=orders, 
                                     current_time=current_time)
        
        # إنشاء اسم ملف فريد
        filename = f"orders_{'_'.join(order_ids)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        
        # إعداد response مع HTML للتحميل
        response = make_response(html_content)
        response.headers['Content-Type'] = 'text/html; charset=utf-8'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        
        return response
        
    except Exception as e:
        current_app.logger.error(f"Error generating HTML: {str(e)}")
        flash(f'حدث خطأ أثناء إنشاء الملف: {str(e)}', 'error')
        return redirect(url_for('orders.index'))

# باقي الكود يبقى كما هو...
@orders_bp.route('/print_orders')
def print_orders():
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    order_ids = request.args.get('order_ids', '').split(',')
    if not order_ids or order_ids == ['']:
        flash('لم يتم تحديد أي طلبات للطباعة', 'error')
        return redirect(url_for('orders.index'))
    
    try:
        # جلب بيانات الطلبات المحددة
        orders = []
        access_token = user.salla_access_token
        
        if not access_token:
            flash('يجب ربط المتجر مع سلة أولاً', 'error')
            return redirect(url_for('auth.link_store'))
            
        headers = {'Authorization': f'Bearer {access_token}'}
        
        for order_id in order_ids:
            try:
                # جلب بيانات الطلب الأساسية
                order_response = requests.get(
                    f"{Config.SALLA_ORDERS_API}/{order_id}",
                    headers=headers,
                    timeout=10
                )
                
                if order_response.status_code != 200:
                    continue
                    
                order_data = order_response.json().get('data', {})
                
                # جلب عناصر الطلب
                items_response = requests.get(
                    f"{Config.SALLA_BASE_URL}/orders/items",
                    params={'order_id': order_id},
                    headers=headers,
                    timeout=10
                )
                
                items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
                
                # معالجة بيانات الطلب
                processed_order = process_order_data(order_id, items_data)
                
                # إضافة معلومات إضافية
                processed_order['reference_id'] = order_data.get('reference_id', order_id)
                processed_order['customer'] = order_data.get('customer', {})
                processed_order['created_at'] = format_date(order_data.get('created_at', ''))
                
                orders.append(processed_order)
                
            except Exception as e:
                current_app.logger.error(f"Error fetching order {order_id}: {str(e)}")
                continue
        
        if not orders:
            flash('لم يتم العثور على أي طلبات للطباعة', 'error')
            return redirect(url_for('orders.index'))
        
        # إضافة الوقت الحالي للقالب
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # إنشاء HTML من template مخصص للطباعة
        html_content = render_template('print_orders.html', 
                                     orders=orders, 
                                     current_time=current_time)
        
        # إنشاء PDF من HTML
        pdf = HTML(string=html_content, base_url=request.base_url).write_pdf()
        
        # إنشاء اسم ملف فريد
        filename = f"orders_{'_'.join(order_ids)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        
        # إعداد response مع PDF للتحميل
        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        
        return response
        
    except Exception as e:
        current_app.logger.error(f"Error generating PDF: {str(e)}")
        flash(f'حدث خطأ أثناء إنشاء PDF: {str(e)}', 'error')
        return redirect(url_for('orders.index'))
@orders_bp.route('/get_quick_list_data', methods=['POST'])
def get_quick_list_data():
    """جلب بيانات القائمة السريعة للطلبات المحددة"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'success': False, 'error': 'الرجاء تسجيل الدخول'}), 401
    
    # التحقق من الصلاحيات
    
    
    data = request.get_json()
    order_ids = data.get('order_ids', [])
    
    if not order_ids:
        return jsonify({'success': False, 'error': 'لم يتم تحديد أي طلبات'}), 400
    
    access_token = user.salla_access_token
    if not access_token:
        return jsonify({'success': False, 'error': 'يجب ربط المتجر مع سلة أولاً'}), 400
    
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    orders_data = []
    
    for order_id in order_ids:
        try:
            # جلب بيانات الطلب الأساسية
            order_response = requests.get(
                f"{Config.SALLA_ORDERS_API}/{order_id}",
                headers=headers,
                timeout=10
            )
            
            if order_response.status_code != 200:
                continue
                
            order_data = order_response.json().get('data', {})
            
            # جلب عناصر الطلب
            items_response = requests.get(
                f"{Config.SALLA_BASE_URL}/orders/items",
                params={'order_id': order_id},
                headers=headers,
                timeout=10
            )
            
            items_data = items_response.json().get('data', []) if items_response.status_code == 200 else []
            
            # معالجة بيانات العناصر
            processed_items = []
            for item in items_data:
                # استخراج الصورة الرئيسية
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
                                base_domain = "https://cdn.salla.sa"
                                main_image = f"{base_domain}{image_url}"
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
            
            orders_data.append({
                'id': order_id,
                'reference_id': order_data.get('reference_id', order_id),
                'items': processed_items
            })
            
        except Exception as e:
            current_app.logger.error(f"Error processing order {order_id} for quick list: {str(e)}")
            continue
    
    return jsonify({
        'success': True,
        'orders': orders_data
    })