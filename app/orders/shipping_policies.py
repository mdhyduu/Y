
from flask import request, jsonify, current_app
from werkzeug.utils import secure_filename
import os
from . import orders_bp
from ..models import SallaOrder, db
from ..services.storage_service import do_storage
from flask import render_template

def allowed_file(filename):
    """التحقق من نوع الملف المسموح به"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']

@orders_bp.route('/orders/<order_id>/shipping-policy', methods=['POST'])
def upload_shipping_policy(order_id):
    """رفع صورة البوليصة لطلب معين"""
    try:
        order = SallaOrder.query.get_or_404(order_id)
        
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

@orders_bp.route('/<order_id>/shipping-policy', methods=['DELETE'])
def delete_shipping_policy(order_id):
    """حذف صورة البوليصة"""
    try:
        order = SallaOrder.query.get_or_404(order_id)
        
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
    try:
        order = SallaOrder.query.get_or_404(order_id)
        
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
    # جلب جميع الطلبات من قاعدة البيانات
    all_orders = SallaOrder.query.order_by(SallaOrder.created_at.desc()).all()
    
    return render_template(
        'upload_shipping_policy.html', 
        all_orders=all_orders
    )

@orders_bp.route('/shipping-policies/manage', methods=['GET'])
def manage_shipping_policies():
    """عرض صفحة إدارة بواليص الشحن"""
    # جلب جميع الطلبات التي تحتوي على صور بواليص
    orders_with_policies = SallaOrder.query.filter(
        SallaOrder.shipping_policy_image.isnot(None)
    ).order_by(SallaOrder.created_at.desc()).all()
    
    return render_template(
        'manage_shipping_policies.html', 
        orders_with_policies=orders_with_policies
    )
    