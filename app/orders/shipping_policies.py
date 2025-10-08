from flask import request, jsonify, current_app, flash, redirect
from werkzeug.utils import secure_filename
import os
from . import orders_bp
from ..models import SallaOrder, db
from ..storage_service import do_storage
from flask import render_template
from sqlalchemy import or_
from app.utils import get_user_from_cookies  # استيراد نفس الدالة المستخدمة في routes

def allowed_file(filename):
    """التحقق من نوع الملف المسموح به"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

@orders_bp.route('/orders/<order_id>/shipping-policy', methods=['POST'])
def upload_shipping_policy(order_id):
    """رفع صورة البوليصة لطلب معين"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'error': 'الرجاء تسجيل الدخول أولاً'}), 401
    
    store_id = user.store_id
    if not store_id:
        return jsonify({'error': 'غير مصرح بالوصول'}), 403
    
    try:
        # البحث عن الطلب مع التحقق من أنه ينتمي للمتجر الحالي
        order = SallaOrder.query.filter_by(
            id=order_id, 
            store_id=store_id
        ).first_or_404()
        
        # التحقق من وجود ملف في الطلب
        if 'shipping_policy_image' not in request.files:
            return jsonify({'error': 'لم يتم تقديم ملف'}), 400
         
        file = request.files['shipping_policy_image']
        
        # التحقق من اختيار ملف
        if file.filename == '':
            return jsonify({'error': 'لم يتم اختيار ملف'}), 400
        
        # التحقق من نوع الملف
        if not allowed_file(file.filename):
            return jsonify({
                'error': 'نوع الملف غير مسموح به. الأنواع المسموحة: ' + 
                        ', '.join(current_app.config['ALLOWED_EXTENSIONS'])
            }), 400
        
        # التحقق من حجم الملف
        if request.content_length > current_app.config['MAX_FILE_SIZE']:
            return jsonify({'error': 'حجم الملف كبير جداً'}), 400
        
        # رفع الملف إلى DigitalOcean Spaces
        image_url = do_storage.upload_file(file, 'shipping-policies')
        
        if not image_url:
            return jsonify({'error': 'فشل في رفع الملف'}), 500
        
        # حفظ رابط الصورة في قاعدة البيانات
        order.shipping_policy_image = image_url
        db.session.commit()
        
        return jsonify({
            'message': 'تم رفع صورة البوليصة بنجاح',
            'image_url': image_url,
            'order_id': order_id
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"خطأ في رفع صورة البوليصة: {str(e)}")
        return jsonify({'error': 'حدث خطأ أثناء رفع الملف'}), 500

@orders_bp.route('/orders/<order_id>/shipping-policy', methods=['DELETE'])
def delete_shipping_policy(order_id):
    """حذف صورة البوليصة"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'error': 'الرجاء تسجيل الدخول أولاً'}), 401
    
    store_id = user.store_id
    if not store_id:
        return jsonify({'error': 'غير مصرح بالوصول'}), 403
    
    try:
        order = SallaOrder.query.filter_by(
            id=order_id, 
            store_id=store_id
        ).first_or_404()
        
        if not order.shipping_policy_image:
            return jsonify({'error': 'لا توجد صورة بوليصة لحذفها'}), 404
        
        # حذف الملف من DigitalOcean Spaces
        success = do_storage.delete_file(order.shipping_policy_image)
        
        if success:
            order.shipping_policy_image = None
            db.session.commit()
            return jsonify({'message': 'تم حذف صورة البوليصة بنجاح'}), 200
        else:
            return jsonify({'error': 'فشل في حذف الملف من التخزين'}), 500
            
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"خطأ في حذف صورة البوليصة: {str(e)}")
        return jsonify({'error': 'حدث خطأ أثناء حذف الملف'}), 500

@orders_bp.route('/<order_id>/shipping-policy', methods=['GET'])
def get_shipping_policy(order_id):
    """الحصول على معلومات صورة البوليصة"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'error': 'الرجاء تسجيل الدخول أولاً'}), 401
    
    store_id = user.store_id
    if not store_id:
        return jsonify({'error': 'غير مصرح بالوصول'}), 403
    
    try:
        order = SallaOrder.query.filter_by(
            id=order_id, 
            store_id=store_id
        ).first_or_404()
        
        if not order.shipping_policy_image:
            return jsonify({'error': 'لا توجد صورة بوليصة'}), 404
        
        return jsonify({
            'order_id': order_id,
            'image_url': order.shipping_policy_image,
            'has_image': True
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"خطأ في جلب صورة البوليصة: {str(e)}")
        return jsonify({'error': 'حدث خطأ أثناء جلب معلومات الملف'}), 500

@orders_bp.route('/shipping-policies/upload', methods=['GET'])
def upload_shipping_policy_page():
    """عرض صفحة رفع بواليص الشحن"""
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    store_id = user.store_id
    if not store_id:
        flash('غير مصرح بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    return render_template('upload_shipping_policy.html', store_id=store_id)

@orders_bp.route('/shipping-policies/manage', methods=['GET'])
def manage_shipping_policies():
    """عرض صفحة إدارة بواليص الشحن"""
    user, employee = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    store_id = user.store_id
    if not store_id:
        flash('غير مصرح بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    try:
        # جلب جميع الطلبات التي تحتوي على صور بواليص للمتجر الحالي فقط
        orders_with_policies = SallaOrder.query.filter(
            SallaOrder.store_id == store_id,
            SallaOrder.shipping_policy_image.isnot(None)
        ).order_by(SallaOrder.created_at.desc()).all()
        
        # حساب الإحصائيات الأساسية
        total_policies = len(orders_with_policies)
        
        return render_template(
            'manage_shipping_policies.html', 
            orders_with_policies=orders_with_policies,
            total_policies=total_policies,
            store_id=store_id
        )
        
    except Exception as e:
        current_app.logger.error(f"خطأ في جلب بيانات إدارة البواليص: {str(e)}")
        return render_template('manage_shipping_policies.html', 
                             orders_with_policies=[],
                             total_policies=0,
                             store_id=store_id)

@orders_bp.route('/api/search-orders', methods=['GET'])
def search_orders():
    """بحث الطلبات برقم الطلب أو المرجع للمتجر الحالي فقط"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'error': 'الرجاء تسجيل الدخول أولاً'}), 401
    
    store_id = user.store_id
    if not store_id:
        return jsonify({'error': 'غير مصرح بالوصول'}), 403
    
    search_term = request.args.get('q', '').strip()
    
    if not search_term:
        return jsonify({'orders': []})
    
    try:
        # البحث في id و reference_id للطلبات الخاصة بالمتجر الحالي فقط
        orders = SallaOrder.query.filter(
            SallaOrder.store_id == store_id,
            or_(
                SallaOrder.id.ilike(f'%{search_term}%'),
                SallaOrder.reference_id.ilike(f'%{search_term}%')
            )
        ).order_by(SallaOrder.created_at.desc()).limit(50).all()
        
        orders_data = []
        for order in orders:
            orders_data.append({
                'id': order.id,
                'reference_id': order.reference_id or '',
                'customer_name': order.customer_name or 'غير محدد',
                'total_amount': order.total_amount or 0,
                'currency': order.currency or 'SAR',
                'created_at': order.created_at.strftime('%Y-%m-%d %H:%M') if order.created_at else 'غير محدد',
                'store_id': order.store_id
            })
        
        return jsonify({'orders': orders_data})
        
    except Exception as e:
        current_app.logger.error(f"خطأ في البحث: {str(e)}")
        return jsonify({'error': 'حدث خطأ أثناء البحث'}), 500

@orders_bp.route('/orders/shipping-policy-by-number', methods=['POST'])
def upload_shipping_policy_by_number():
    """رفع صورة البوليصة باستخدام رقم الطلب للمتجر الحالي فقط"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'error': 'الرجاء تسجيل الدخول أولاً'}), 401
    
    store_id = user.store_id
    if not store_id:
        return jsonify({'error': 'غير مصرح بالوصول'}), 403
    
    try:
        # الحصول على رقم الطلب من النموذج
        order_number = request.form.get('order_number')
        if not order_number:
            return jsonify({'error': 'لم يتم تقديم رقم الطلب'}), 400

        # البحث عن الطلب باستخدام رقم الطلب أو المرجع للمتجر الحالي فقط
        order = SallaOrder.query.filter(
            SallaOrder.store_id == store_id,
            or_(
                SallaOrder.id == order_number,
                SallaOrder.reference_id == order_number
            )
        ).first()

        if not order:
            return jsonify({'error': f'لم يتم العثور على طلب بالرقم {order_number} في متجرك'}), 404
        
        # التحقق من وجود ملف في الطلب
        if 'shipping_policy_image' not in request.files:
            return jsonify({'error': 'لم يتم تقديم ملف'}), 400
         
        file = request.files['shipping_policy_image']
        
        # التحقق من اختيار ملف
        if file.filename == '':
            return jsonify({'error': 'لم يتم اختيار ملف'}), 400
        
        # التحقق من نوع الملف
        if not allowed_file(file.filename):
            return jsonify({
                'error': 'نوع الملف غير مسموح به. الأنواع المسموحة: ' + 
                        ', '.join(current_app.config['ALLOWED_EXTENSIONS'])
            }), 400
        
        # التحقق من حجم الملف
        if request.content_length > current_app.config['MAX_FILE_SIZE']:
            return jsonify({'error': 'حجم الملف كبير جداً'}), 400
        
        # رفع الملف إلى DigitalOcean Spaces
        image_url = do_storage.upload_file(file, 'shipping-policies')
        
        if not image_url:
            return jsonify({'error': 'فشل في رفع الملف'}), 500
        
        # حفظ رابط الصورة في قاعدة البيانات
        order.shipping_policy_image = image_url
        db.session.commit()
        
        return jsonify({
            'message': f'تم رفع صورة البوليصة بنجاح للطلب {order_number}',
            'image_url': image_url,
            'order_id': order.id,
            'order_number': order_number
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"خطأ في رفع صورة البوليصة للطلب {order_number}: {str(e)}")
        return jsonify({'error': 'حدث خطأ أثناء رفع الملف'}), 500

@orders_bp.route('/orders/bulk-shipping-policies', methods=['POST'])
def bulk_upload_shipping_policies():
    """رفع جماعي للبواليص للطلبات المستخرجة من PDF للمتجر الحالي فقط"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'error': 'الرجاء تسجيل الدخول أولاً'}), 401
    
    store_id = user.store_id
    if not store_id:
        return jsonify({'error': 'غير مصرح بالوصول'}), 403
    
    try:
        orders_data = request.json.get('orders', [])
        
        if not orders_data:
            return jsonify({'error': 'لم يتم تقديم بيانات الطلبات'}), 400
        
        results = {
            'successful': [],
            'failed': []
        }
        
        for order_data in orders_data:
            order_number = order_data.get('order_number')
            image_data = order_data.get('image_data')  # base64 encoded image
            
            if not order_number or not image_data:
                results['failed'].append({
                    'order_number': order_number,
                    'error': 'بيانات ناقصة'
                })
                continue
            
            # البحث عن الطلب في قاعدة البيانات للمتجر الحالي فقط
            order = SallaOrder.query.filter(
                SallaOrder.store_id == store_id,
                or_(
                    SallaOrder.id == order_number,
                    SallaOrder.reference_id == order_number
                )
            ).first()
            
            if not order:
                results['failed'].append({
                    'order_number': order_number,
                    'error': 'الطلب غير موجود في قاعدة البيانات أو لا ينتمي لمتجرك'
                })
                continue
            
            try:
                # تحويل base64 إلى ملف
                import base64
                from io import BytesIO
                
                # إزالة header إذا موجود
                if ',' in image_data:
                    image_data = image_data.split(',')[1]
                
                image_binary = base64.b64decode(image_data)
                image_file = BytesIO(image_binary)
                image_file.filename = f"{order_number}.png"
                
                # رفع الملف إلى التخزين
                image_url = do_storage.upload_file(image_file, 'shipping-policies')
                
                if not image_url:
                    results['failed'].append({
                        'order_number': order_number,
                        'error': 'فشل في رفع الملف'
                    })
                    continue
                
                # حفظ في قاعدة البيانات
                order.shipping_policy_image = image_url
                db.session.commit()
                
                results['successful'].append({
                    'order_number': order_number,
                    'order_id': order.id,
                    'image_url': image_url
                })
                
            except Exception as e:
                db.session.rollback()
                results['failed'].append({
                    'order_number': order_number,
                    'error': f'خطأ في المعالجة: {str(e)}'
                })
        
        return jsonify({
            'message': f'تم معالجة {len(orders_data)} طلب',
            'results': results,
            'store_id': store_id
        }), 200
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"خطأ في الرفع الجماعي للبواليص: {str(e)}")
        return jsonify({'error': 'حدث خطأ أثناء المعالجة'}), 500

@orders_bp.route('/api/store-orders', methods=['GET'])
def get_store_orders():
    """جلب الطلبات الخاصة بالمتجر الحالي فقط للإحصائيات"""
    user, employee = get_user_from_cookies()
    
    if not user:
        return jsonify({'error': 'الرجاء تسجيل الدخول أولاً'}), 401
    
    store_id = user.store_id
    if not store_id:
        return jsonify({'error': 'غير مصرح بالوصول'}), 403
    
    try:
        # إحصائيات الطلبات
        total_orders = SallaOrder.query.filter_by(store_id=store_id).count()
        orders_with_policies = SallaOrder.query.filter(
            SallaOrder.store_id == store_id,
            SallaOrder.shipping_policy_image.isnot(None)
        ).count()
        
        # أحدث الطلبات مع البواليص
        recent_orders_with_policies = SallaOrder.query.filter(
            SallaOrder.store_id == store_id,
            SallaOrder.shipping_policy_image.isnot(None)
        ).order_by(SallaOrder.created_at.desc()).limit(10).all()
        
        recent_orders_data = []
        for order in recent_orders_with_policies:
            recent_orders_data.append({
                'id': order.id,
                'customer_name': order.customer_name or 'غير محدد',
                'created_at': order.created_at.strftime('%Y-%m-%d %H:%M') if order.created_at else 'غير محدد',
                'image_url': order.shipping_policy_image
            })
        
        return jsonify({
            'store_id': store_id,
            'statistics': {
                'total_orders': total_orders,
                'orders_with_policies': orders_with_policies,
                'coverage_percentage': round((orders_with_policies / total_orders * 100), 2) if total_orders > 0 else 0
            },
            'recent_orders': recent_orders_data
        }), 200
        
    except Exception as e:
        current_app.logger.error(f"خطأ في جلب إحصائيات المتجر: {str(e)}")
        return jsonify({'error': 'حدث خطأ أثناء جلب البيانات'}), 500