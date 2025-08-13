from flask import Flask, request
import os
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from .config import Config
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix

# Initialize extensions
db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)
    
    # Load configuration
    app.config.from_object(Config)
    
    # Enhanced security settings
    app.config.update(
        SECRET_KEY=os.environ.get('SECRET_KEY', os.urandom(24)),
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Strict',
        PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
        SQLALCHEMY_TRACK_MODIFICATIONS=False
    )

    # Proxy configuration
    app.wsgi_app = ProxyFix(
    app.wsgi_app,
    x_for=1,
    x_proto=1,
    x_host=1,
    x_prefix=1
    
    )

    # Initialize extensions with app
    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    from .utils import format_date
    app.jinja_env.filters['format_date'] = format_date
    # Create database tables
    with app.app_context():
        from . import models
        db.create_all()
    
    # Register blueprints
    from .employees import employees_bp
    from .dashboard import dashboard_bp
    from .user_auth import user_auth_bp

    from .orders import orders_bp
    from .categories import categories_bp
    from .permissions import permissions_bp
    from .products import products_bp
    from .delivery_orders import delivery_bp
    
    blueprints = [
    user_auth_bp,  # أو auth_bp (الاختيار الذي اخترته أعلاه)
    dashboard_bp,
    employees_bp,
    orders_bp,
    categories_bp,
    permissions_bp,
    products_bp,
    delivery_bp
        ]
    
    for bp in blueprints:
        app.register_blueprint(bp)

    # Template filters
  

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

    # Security headers
    @app.after_request
    def add_security_headers(response):
        path = request.path if request else ''
        
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['X-XSS-Protection'] = '1; mode=block'
        
        if path.startswith('/dashboard') or path.startswith('/auth'):
            response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
            response.headers['Pragma'] = 'no-cache'
            response.headers['Expires'] = '0'
        
        return response

    # Token refresh job
    def refresh_tokens_job():
        with app.app_context():
            from .models import User
            from .token_utils import refresh_salla_token
            
            users = User.query.filter(
                User._salla_refresh_token.isnot(None),
                User.token_expires_at < datetime.utcnow() + timedelta(hours=1)
            ).all()
            
            for user in users:
                try:
                    if refresh_salla_token(user):
                        app.logger.info(f"Token refreshed for user: {user.email}")
                except Exception as e:
                    app.logger.error(f"Token refresh failed: {str(e)}")

    # Start scheduler
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            func=refresh_tokens_job,
            trigger='interval',
            minutes=30
        )
        scheduler.start()
# تأكيد إعدادات الجلسة
@app.before_request
def ensure_session_settings():
    session.permanent = True
    app.permanent_session_lifetime = timedelta(hours=2)
    if 'user_id' not in session and request.cookies.get('user_id'):
        # مزامنة الكوكيز مع الجلسة
        session['user_id'] = request.cookies.get('user_id')
        session['is_admin'] = request.cookies.get('is_admin') == 'true'
    return app