from flask import Blueprint, render_template, redirect, url_for, flash, session
from .models import db, Department, User

import requests
from .config import Config

categories_bp = Blueprint('categories', __name__, url_prefix='/dashboard/categories')

@categories_bp.route('/sync')
def sync_categories():
    if 'user_id' not in session or not session.get('is_admin'):
        flash('غير مصرح لك بالوصول', 'error')
        return redirect(url_for('user_auth.login'))
    
    user = User.query.get(session['user_id'])
    
    if not user.salla_access_token:
        flash('يرجى ربط المتجر أولاً', 'error')
        return redirect(url_for('auth.link_store'))
    
    try:
        # جلب الأقسام من سلة
        headers = {
            'Authorization': f'Bearer {user.salla_access_token}',
            'Accept': 'application/json'
        }
        response = requests.get(Config.SALLA_CATEGORIES_API, headers=headers)
        response.raise_for_status()
        categories_data = response.json().get('data', [])
        
        # دالة لمعالجة كل تصنيف وأطفاله
        def process_category(category_data, parent_id=None, store_id=user.store_id):
            # البحث عن التصنيف بواسطة salla_id
            existing = Department.query.filter_by(salla_id=category_data['id'], store_id=store_id).first()
            if existing:
                # تحديث الاسم والأب إذا تغير
                existing.name = category_data['name']
                existing.parent_id = parent_id
            else:
                # إنشاء تصنيف جديد
                new_category = Department(
                    salla_id=category_data['id'],
                    name=category_data['name'],
                    store_id=store_id,
                    parent_id=parent_id
                )
                db.session.add(new_category)
                existing = new_category
            
            # معالجة الأطفال إن وجدوا
            children = category_data.get('children', [])
            for child in children:
                process_category(child, parent_id=existing.id, store_id=store_id)
        
        # البدء بالتصنيفات الرئيسية (التي ليس لها أب)
        for category in categories_data:
            process_category(category, parent_id=None, store_id=user.store_id)
        
        db.session.commit()
        flash('تم مزامنة الأقسام بنجاح', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'خطأ في مزامنة الأقسام: {str(e)}', 'error')
    
    return redirect(url_for('permissions.manage_departments'))