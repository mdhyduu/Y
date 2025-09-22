from flask import request, redirect, url_for, flash, make_response, current_app, render_template, jsonify
import requests
from datetime import datetime
from weasyprint import HTML
from . import orders_bp
from app.utils import get_user_from_cookies, process_order_data, format_date, create_session, db_session_scope, process_orders_sequentially, get_barcodes_for_orders
from app.config import Config
import logging

# إعداد المسجل للإنتاج
logger = logging.getLogger('salla_app')

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
        
        # استخدام الدالة المحسنة من utils.py
        orders = process_orders_sequentially(order_ids, access_token)
        
        if not orders:
            flash('لم يتم العثور على أي طلبات للمعاينة', 'error')
            return redirect(url_for('orders.index'))
        
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
    
    try:
        # استخدام الدالة المحسنة من utils.py
        orders = process_orders_sequentially(order_ids, access_token)
        
        orders_result = []
        
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
                
            except Exception as e:
                logger.error(f"Error processing order {order.get('id', '')} for quick list: {str(e)}")
                continue
        
        return jsonify({
            'success': True,
            'orders': orders_result
        })
        
    except Exception as e:
        logger.error(f"Error in get_quick_list_data: {str(e)}")
        return jsonify({'success': False, 'error': 'حدث خطأ أثناء جلب البيانات'}), 500

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
        
        # استخدام الدالة المحسنة من utils.py
        orders = process_orders_sequentially(order_ids, access_token)
        
        if not orders:
            flash('لم يتم العثور على أي طلبات للتحميل', 'error')
            return redirect(url_for('orders.index'))
        
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