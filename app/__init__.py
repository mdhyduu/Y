from flask import Flask
import os 
from flask import current_app
from PIL import Image
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from .config import Config
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from flask import session, request
import webcolors
from flask import jsonify, render_template_string
from flask_mail import Mail  # إضافة استيراد Flask-Mail

# إنشاء كائنات الإضافات
db = SQLAlchemy()
migrate = Migrate()
csrf = CSRFProtect()
mail = Mail()

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or app.config.get('SQLALCHEMY_DATABASE_URI')
    app.secret_key = os.environ.get('SECRET_KEY'),
    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_HTTPONLY'] = True
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    
    app.config['WTF_CSRF_CHECK_DEFAULTS'] = True
    
    # إعداد CSRF مع استثناءات للـ Webhooks
    csrf.init_app(app)
    
    # استثناء endpoint الـ Webhooks من CSRF
    @app.before_request
    def check_csrf_exemptions():
        # استثناء Webhook endpoints من CSRF
        if request.path.startswith('/webhook/'):
            return None  # تخطي CSRF protection لهذه المسارات
    
    migrate.init_app(app, db)
    
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(
        app.wsgi_app, 
        x_for=1, 
        x_proto=1, 
        x_host=1
    )
    
    # تهيئة الإضافات مع التطبيق
    db.init_app(app)
    mail.init_app(app)
 
    app.mail = mail  # Attach the mail instance to the app for use in blueprints
    
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

    app.register_blueprint(employees_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(user_auth_bp)
    app.register_blueprint(auth_bp)
    


    csrf.exempt(orders_bp)
    app.register_blueprint(orders_bp)
    
    app.register_blueprint(categories_bp)
    app.register_blueprint(permissions_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(delivery_bp)

    # فلترات القوالب
    app.jinja_env.filters['format_date'] = format_date
    @app.after_request
    def add_csp_header(response):
        if request.path.startswith('/webhook/'):
            return response
            
        # سياسة أقل تقييداً للتحقق من المشكلة
        csp_policy = "default-src * 'unsafe-inline' 'unsafe-eval';"
        response.headers['Content-Security-Policy'] = csp_policy
        return response
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
    

    def get_text_color(hex_color):
        """
        Determines if text should be black or white based on background hex color.
        """
        try:
            # Normalize color if it's a known name
            if not hex_color.startswith('#'):
                hex_color = webcolors.name_to_hex(hex_color)
                
            hex_color = hex_color.lstrip('#')
            rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
            # Formula for perceived brightness (YIQ)
            yiq = ((rgb[0] * 299) + (rgb[1] * 587) + (rgb[2] * 114)) / 1000
            return '#212529' if yiq >= 128 else '#ffffff'
        except (ValueError, AttributeError):
            # Default for invalid colors
            return '#ffffff'
    
    def get_status_badge(status_slug):
        """
        Maps order status slugs to Bootstrap badge background colors.
        """
        mapping = {
            'under_review': 'warning text-dark',
            'processing': 'info text-dark',
            'completed': 'success',
            'awaiting_payment': 'warning text-dark',
            'shipped': 'primary',
            'delivered': 'success',
            'canceled': 'danger',
            'refunded': 'secondary',
        }
        return mapping.get(status_slug, 'light text-dark')
    
    # Register the filters with Jinja2 environment
    def hex_to_rgb(hex_code):
        """Converts a HEX color string to an RGB string 'R, G, B'."""
        hex_code = hex_code.lstrip('#')
        if len(hex_code) != 6:
            return "108, 117, 125" # Default to grey if format is wrong
        try:
            return f"{int(hex_code[0:2], 16)}, {int(hex_code[2:4], 16)}, {int(hex_code[4:6], 16)}"
        except ValueError:
            return "108, 117, 125" # Default to grey on error
    
    # Register the new filter along with the old ones
    app.jinja_env.filters['get_text_color'] = get_text_color
    app.jinja_env.filters['get_status_badge'] = get_status_badge
    app.jinja_env.filters['hex_to_rgb'] = hex_to_rgb

 
    def generate_pwa_icons(base_icon_path):
        """
        إنشاء أيقونات PWA بأحجام مختلفة من أيقونة أساسية
        """
        sizes = [72, 96, 128, 144, 152, 192, 384, 512]
        icons_dir = os.path.join(current_app.static_folder, 'icons')
        
        # إنشاء مجلد الأيقونات إذا لم يكن موجودًا
        if not os.path.exists(icons_dir):
            os.makedirs(icons_dir)
        
        # فتح الصورة الأساسية
        try:
            with Image.open(base_icon_path) as img:
                for size in sizes:
                    # تغيير حجم الصورة
                    resized_img = img.resize((size, size), Image.Resampling.LANCZOS)
                    
                    # حفظ الصورة بالحجم الجديد
                    icon_path = os.path.join(icons_dir, f'icon-{size}x{size}.png')
                    resized_img.save(icon_path)
                    print(f"تم إنشاء: {icon_path}")
                    
        except Exception as e:
            print(f"خطأ في إنشاء الأيقونات: {e}")
    
    # في دالة manifest، تحقق من وجود الأيقونات أو أنشئها
    @app.route('/manifest.json')
    def manifest():
        # المسار إلى الأيقونة الأساسية (افترض أنها موجودة في static/icon.png)
        base_icon_path = os.path.join(current_app.static_folder, 's.png')
        icons_dir = os.path.join(current_app.static_folder, 'icons')
        
        # إذا لم يوجد مجلد الأيقونات أو كان فارغًا، أنشئ الأيقونات
        if not os.path.exists(icons_dir) or not os.listdir(icons_dir):
            generate_pwa_icons(base_icon_path)
        
        manifest_json = '''
        {
          "short_name": "سلطانة",
          "name": "مرحبا بكم ",
          "description": "نظام متكامل لإدارة الطلبات والمبيعات",
          "lang": "ar",
          "dir": "rtl",
          "icons": [
            {
              "src": "/static/icons/icon-192x192.png",
              "sizes": "192x192",
              "type": "image/png",
              "purpose": "maskable any"
            },
            {
              "src": "/static/icons/s.png",
              "sizes": "192x192",
              "type": "image/png",
              "purpose": "maskable any"
            }
          ],
          "start_url": "/",
          "background_color": "#1e3a8a",
          "theme_color": "#1e3a8a",
          "display": "standalone",
          "orientation": "portrait",
          "scope": "/"
        }
        '''
        return render_template_string(manifest_json), 200, {'Content-Type': 'application/json'}
    
    
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()   # يغلق الجلسة ويرجع الاتصال للـ pool

    return app
    # إضافة هيدرز الأمان لكل الردود
    @app.after_request
    def add_security_headers(response):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=()"
        # لو تبغى Content-Security-Policy (CSP) أقدر أكتب لك نسخة تناسب موقعك
        return response
    return app
    
    
