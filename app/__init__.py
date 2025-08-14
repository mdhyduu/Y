from flask import Flask
import os
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from .config import Config
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from flask_session import Session
import logging

# إنشاء كائنات الإضافات
db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # إعدادات الجلسة المحسنة
    app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(24).hex()
    app.config.update(
        SESSION_TYPE='filesystem',
        SESSION_PERMANENT=False,
        SESSION_USE_SIGNER=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={
            'pool_pre_ping': True,
            'pool_recycle': 300
        }
    )
    
    # إصلاح البروكسي
    app.wsgi_app = ProxyFix(
        app.wsgi_app, 
        x_for=1, 
        x_proto=1, 
        x_host=1
    )
    
    # تهيئة الإضافات
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    Session(app)
    
    # تسجيل النماذج مع سياق التطبيق
    with app.app_context():
        from . import models
        db.create_all()

    # تسجيل البلوبيرنتات
    register_blueprints(app)
    
    # تسجيل الفلاتر
    register_template_filters(app)
    
    # إعداد المجدول
    setup_scheduler(app)
    
    return app

def register_blueprints(app):
    """تسجيل جميع البلوبيرنتات"""
    from .employees import employees_bp
    from .dashboard import dashboard_bp
    from .user_auth import user_auth_bp
    from .auth import auth_bp
    from .orders import orders_bp
    from .categories import categories_bp
    from .permissions import permissions_bp
    from .products import products_bp
    from .delivery_orders import delivery_bp
    
    blueprints = [
        employees_bp,
        dashboard_bp,
        user_auth_bp,
        auth_bp,
        orders_bp,
        categories_bp,
        permissions_bp,
        products_bp,
        delivery_bp
    ]
    
    for bp in blueprints:
        app.register_blueprint(bp)

def register_template_filters(app):
    """تسجيل فلاتر القوالب"""
    from .utils import format_date
    
    app.jinja_env.filters['format_date'] = format_date
    
    @app.template_filter('time_ago')
    def time_ago_filter(dt):
        if not dt:
            return "بدون تاريخ"
        now = datetime.utcnow()
        diff = now - dt
        seconds = diff.total_seconds()
        if seconds < 60:
            return "الآن"
        minutes = seconds // 60
        if minutes < 60:
            return f"منذ {int(minutes)} دقيقة"
        hours = minutes // 60
        if hours < 24:
            return f"منذ {int(hours)} ساعة"
        days = hours // 24
        if days < 30:
            return f"منذ {int(days)} يوم"
        months = days // 30
        if months < 12:
            return f"منذ {int(months)} شهر"
        years = months // 12
        return f"منذ {int(years)} سنة"

def setup_scheduler(app):
    """إعداد المهمات المجدولة"""
    from .token_utils import refresh_salla_token
    from .models import User
    
    def refresh_tokens_job():
        """مهمة مجدولة لتجديد التوكنات"""
        with app.app_context():
            users = User.query.filter(User.salla_refresh_token.isnot(None)).all()
            for user in users:
                try:
                    if not user.token_expires_at or (user.token_expires_at - datetime.utcnow()).total_seconds() < 3600:
                        new_token = refresh_salla_token(user)
                        if new_token:
                            app.logger.info(f"تم تجديد التوكن للمستخدم {user.id}")
                except Exception as e:
                    app.logger.error(f"فشل تجديد التوكن للمستخدم {user.id}: {str(e)}")

    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        if 'scheduler' not in app.extensions:
            scheduler = BackgroundScheduler()
            scheduler.add_job(refresh_tokens_job, 'interval', hours=1)
            scheduler.start()
            app.extensions['scheduler'] = scheduler