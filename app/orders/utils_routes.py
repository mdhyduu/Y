# orders/utils_routes.py
from flask import render_template, redirect, url_for, make_response, flash, send_from_directory
from . import orders_bp
from app.utils import get_user_from_cookies
from app.config import Config

@orders_bp.route('/static/barcodes/<filename>')
def serve_barcode(filename):
    """تخدم ملفات الباركود"""
    barcode_folder = Config.BARCODE_FOLDER
    return send_from_directory(barcode_folder, filename)

@orders_bp.route('/scan')
def scan_barcode():
    """صفحة مسح الباركود"""
    user, _ = get_user_from_cookies()
    
    if not user:
        flash('الرجاء تسجيل الدخول أولاً', 'error')
        response = make_response(redirect(url_for('user_auth.login')))
        response.set_cookie('user_id', '', expires=0)
        response.set_cookie('is_admin', '', expires=0)
        return response
    return render_template('scan_barcode.html')

