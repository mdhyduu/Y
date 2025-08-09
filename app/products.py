from flask import Blueprint, render_template, flash, redirect, url_for, session
import requests
from .models import User
from .config import Config

products_bp = Blueprint('products', __name__)

@products_bp.route('/')
def list_products():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    user = User.query.get(session['user_id'])
    if not user or not user.salla_access_token:
        flash('يجب ربط المتجر مع سلة أولاً', 'error')
        return redirect(url_for('auth.link_store'))
    
    try:
        headers = {
            'Authorization': f'Bearer {user.salla_access_token}',
            'Content-Type': 'application/json'
        }
        
        # جلب المنتجات مباشرة من سلة
        response = requests.get(Config.SALLA_PRODUCTS_API, headers=headers)
        response.raise_for_status()
        products_data = response.json().get('data', [])
        
        # معالجة بسيطة للبيانات
        products = []
        for product in products_data:
            products.append({
                'id': product.get('id'),
                'name': product.get('name'),
                'description': product.get('description'),
                'price': product.get('price', {}).get('amount', 0),
                'currency': product.get('price', {}).get('currency', 'SAR'),
                'sku': product.get('sku'),
                'stock': product.get('quantity'),
                'main_image': product.get('main_image')
            })
        
        return render_template('products/list.html', products=products)
    
    except requests.exceptions.HTTPError as e:
        flash(f'فشل في جلب المنتجات من سلة: {str(e)}', 'error')
    except Exception as e:
        flash(f'حدث خطأ أثناء جلب المنتجات: {str(e)}', 'error')
    
    return render_template('products/list.html', products=[])

@products_bp.route('/<int:product_id>')
def product_details(product_id):
    if 'user_id' not in session:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        return redirect(url_for('user_auth.login'))
    
    user = User.query.get(session['user_id'])
    if not user or not user.salla_access_token:
        flash('يجب ربط المتجر مع سلة أولاً', 'error')
        return redirect(url_for('auth.link_store'))
    
    try:
        headers = {
            'Authorization': f'Bearer {user.salla_access_token}',
            'Content-Type': 'application/json'
        }
        
        # جلب تفاصيل المنتج مباشرة من سلة
        response = requests.get(f"{Config.SALLA_PRODUCTS_API}/{product_id}", headers=headers)
        response.raise_for_status()
        product_data = response.json().get('data', {})
        
        if not product_data:
            flash('لم يتم العثور على المنتج', 'error')
            return redirect(url_for('products.list_products'))
        
        # معالجة بيانات المنتج
        product = {
            'id': product_data.get('id'),
            'name': product_data.get('name'),
            'description': product_data.get('description'),
            'price': product_data.get('price', {}).get('amount', 0),
            'currency': product_data.get('price', {}).get('currency', 'SAR'),
            'sku': product_data.get('sku'),
            'stock': product_data.get('quantity'),
            'main_image': product_data.get('main_image'),
            # يمكن إضافة المزيد من الحقول حسب الحاجة
        }
        
        return render_template('products/details.html', product=product)
    
    except requests.exceptions.HTTPError as e:
        flash(f'فشل في جلب تفاصيل المنتج: {str(e)}', 'error')
    except Exception as e:
        flash(f'حدث خطأ: {str(e)}', 'error')
    
    return redirect(url_for('products.list_products'))