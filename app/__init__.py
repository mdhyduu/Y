from flask import Flask
import os
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from .config import Config
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from flask import session

# إنشاء كائنات الإضافات
db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    # في ملف الإعدادات الرئيسي لتطبيقك
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')


    app.secret_key = os.environ.get('SECRET_KEY'),
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
    
    
    
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(
        app.wsgi_app, 
        x_for=1, 
        x_proto=1, 
        x_host=1
    )
    
    # تهيئة الإضافات مع التطبيق
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)

    # تسجيل النماذج مع سياق التطبيق
    with app.app_context():
        from . import models
        db.create_all()

    # استيراد الوظائف المطلوبة
    from .token_utils import refresh_salla_token
    from .models import User

    # تسجيل البلوبيرنتات
    from .employees import employees_bp
    from .dashboard import dashboard_bp
    from .user_auth import user_auth_bp
    from .auth import auth_bp
    from .orders import orders_bp
    from .utils import format_date
    from .categories import categories_bp
    from .permissions import permissions_bp
    from .products import products_bp
    from .delivery_orders import delivery_bp
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.register_blueprint(employees_bp)

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(user_auth_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(categories_bp)
    app.register_blueprint(permissions_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(delivery_bp)

    # فلترات القوالب
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

    def refresh_tokens_job():
        """مهمة مجدولة لتجديد التوكنات إذا قربت تنتهي"""
        with app.app_context():
            users = User.query.filter(User.salla_refresh_token.isnot(None)).all()
            for user in users:
                try:
                    # إذا باقي أقل من ساعة على انتهاء التوكن
                    if not user.token_expires_at or (user.token_expires_at - datetime.utcnow()).total_seconds() < 3600:
                        new_token = refresh_salla_token(user)
                        if new_token:
                            app.logger.info(f"تم تجديد التوكن للمستخدم {user.id}")
                except Exception as e:
                    app.logger.error(f"فشل تجديد التوكن للمستخدم {user.id}: {str(e)}")

    # تشغيل الـ scheduler
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        scheduler = BackgroundScheduler()
        scheduler.add_job(refresh_tokens_job, 'interval', hours=1)  # تحقق كل ساعة
        scheduler.start()

    return app