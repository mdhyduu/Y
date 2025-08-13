from flask import Flask
import os
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from .config import Config
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from flask import session
from .session_manager import init_session_manager  # <-- استيراد جديد
from flask import request
# إنشاء كائنات الإضافات
db = SQLAlchemy() 
migrate = Migrate()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # تحسين إعدادات الجلسات والأمان
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY', os.urandom(24)),
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Strict',  # تغيير من Lax إلى Strict
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2),  # مدة صلاحية الجلسة
        SESSION_REFRESH_EACH_REQUEST=True,
        SQLALCHEMY_TRACK_MODIFICATIONS=False
    )
    
    # إصلاح البروكسي
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(
        app.wsgi_app, 
        x_for=1, 
        x_proto=1, 
        x_host=1,
        x_prefix=1
    )
    
    # تهيئة الإضافات
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    init_session_manager(app)  # <-- تهيئة مدير الجلسات الجديد

    # إنشاء الجداول
    with app.app_context():
        from . import models
        db.create_all()

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
        # ... (ابقى الكود كما هو)
        ...

    # إضافة رؤوس أمان
    @app.after_request
    def add_security_headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        
        # منع التخزين المؤقت للصفحات الحساسة
        if request.path.startswith('/dashboard') or request.path.startswith('/auth'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        
        return response
 
    # مهمة تجديد التوكنات
    def refresh_tokens_job():
        """مهمة مجدولة لتجديد التوكنات"""
        with app.app_context():
            from .models import User
            from .token_utils import refresh_salla_token
            
            users = User.query.filter(
                User._salla_refresh_token.isnot(None),
                User.token_expires_at < datetime.utcnow() + timedelta(hours=1)
            ).all()
            
            for user in users:
                try:
                    new_token = refresh_salla_token(user)
                    if new_token:
                        app.logger.info(f"تم تجديد التوكن للمستخدم {user.email}")
                except Exception as e:
                    app.logger.error(f"خطأ في تجديد التوكن: {str(e)}")

    # تشغيل المهمة المجدولة
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            func=refresh_tokens_job,
            trigger='interval',
            minutes=30,  # التحقق كل 30 دقيقة
            next_run_time=datetime.now() + timedelta(minutes=1)
        )
        scheduler.start()

    return app