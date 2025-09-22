import os
from cryptography.fernet import Fernet
from datetime import timedelta
from pathlib import Path

basedir = Path(__file__).parent.absolute()
from dotenv import load_dotenv
load_dotenv()  # تحميل المتغيرات

# تحديد مسار الملف بشكل مطلق
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

class Config:
    # ------ الإعدادات الأساسية ------
    SECRET_KEY = os.environ.get('SECRET_KEY')
    if not SECRET_KEY:
        raise ValueError("يجب تعيين SECRET_KEY في متغيرات البيئة للإنتاج")
    WEBHOOK_SECRET = os.environ.get('WEBHOOK_SECRET')
    BASE_URL = os.environ.get('BASE_URL', 'https://plankton-app-9im8u.ondigitalocean.app')   
    ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY')
    if not ENCRYPTION_KEY:
        raise ValueError("يجب تعيين ENCRYPTION_KEY في متغيرات البيئة للإنتاج")

    # ------ إعدادات قاعدة البيانات ------
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or os.environ.get('SQLALCHEMY_DATABASE_URI')
    if not SQLALCHEMY_DATABASE_URI:
        raise ValueError("يجب تعيين DATABASE_URL أو SQLALCHEMY_DATABASE_URI في متغيرات البيئة")
    
    # استبدال بداية الرابط إذا كان من Heroku (لتوافقية مع DigitalOcean)
    if SQLALCHEMY_DATABASE_URI and SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgres://", "postgresql://", 1)
    

    # إعدادات PostgreSQL المحسنة

    # إعدادات الأداء المحسنة لـ PDF والطباعة
    MAX_WORKERS = 3
    PDF_GENERATION_WORKERS = 3
    REQUEST_TIMEOUT = 15
    PDF_JPEG_QUALITY = 80
    PDF_OPTIMIZE_SIZE = True
    BARCODE_GENERATION_WORKERS = 5
    # إعدادات PostgreSQL المحسنة
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 20,
        'max_overflow': 30,
        'pool_pre_ping': True,
        'pool_recycle': 3600
    }
    
    
    # ------ إعدادات الترحيل (Pagination) ------
    DEFAULT_PER_PAGE = 20
    MAX_PER_PAGE = 100
    
    # ------ إعدادات Salla API ------
    SALLA_CLIENT_ID = os.environ.get('SALLA_CLIENT_ID')
    SALLA_CLIENT_SECRET = os.environ.get('SALLA_CLIENT_SECRET')
    if not SALLA_CLIENT_ID or not SALLA_CLIENT_SECRET:
        raise ValueError("يجب تعيين SALLA_CLIENT_ID و SALLA_CLIENT_SECRET في متغيرات البيئة")
    
    SALLA_AUTH_URL = os.environ.get('SALLA_AUTH_URL', 'https://accounts.salla.sa/oauth2/auth')
    SALLA_TOKEN_URL = os.environ.get('SALLA_TOKEN_URL', 'https://accounts.salla.sa/oauth2/token')
    SALLA_API_BASE_URL = os.environ.get('SALLA_API_BASE_URL', 'https://api.salla.dev/admin/v2')
    SALLA_BASE_URL = SALLA_API_BASE_URL
    SALLA_ORDERS_API = os.environ.get('SALLA_ORDERS_API', f"{SALLA_API_BASE_URL}/orders")
    SALLA_SHIPMENTS_API = f"{SALLA_API_BASE_URL}/shipments"
    SALLA_ORDERS_ENDPOINT = f"{SALLA_API_BASE_URL}/orders"
    SALLA_ORDER_STATUSES_API = f"{SALLA_API_BASE_URL}/orders/statuses"
    SALLA_PRODUCTS_ENDPOINT = f"{SALLA_API_BASE_URL}/products"
    SALLA_STORE_INFO_ENDPOINT = f"{SALLA_API_BASE_URL}/store/info"
    REDIRECT_URI = os.environ.get('REDIRECT_URI')
    if not REDIRECT_URI:
        raise ValueError("يجب تعيين REDIRECT_URI في متغيرات البيئة للإنتاج")
    
    # ------ إعدادات الباركود ------
    BARCODE_FOLDER = basedir / 'static' / 'barcodes'
    
    # ... الإعدادات الأخرى
    UPLOAD_FOLDER = 'static/uploads/custom_orders'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    
    # ------ إعدادات الكوكيز والأمان ------
    COOKIE_NAME = 'app_session'
    COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'True').lower() == 'true'
    COOKIE_HTTPONLY = True
    COOKIE_SAMESITE = 'Lax'
    COOKIE_LIFETIME = timedelta(days=30)
    COOKIE_REFRESH_EACH_REQUEST = False
    COOKIE_PATH = '/'
    COOKIE_DOMAIN = os.environ.get('COOKIE_DOMAIN', None)
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'True').lower() == 'true'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    
    # ------ إعدادات CSRF ------
    WTF_CSRF_ENABLED = True
    WTF_CSRF_SECRET_KEY = os.environ.get('WTF_CSRF_SECRET_KEY')
    if not WTF_CSRF_SECRET_KEY:
        raise ValueError("يجب تعيين WTF_CSRF_SECRET_KEY في متغيرات البيئة للإنتاج")
    WTF_CSRF_TIME_LIMIT = 3600
    
    # ------ إعدادات التشفير ------
    DATA_ENCRYPTION_KEYS = {
        'default': ENCRYPTION_KEY,
        'fallback': os.environ.get('ENCRYPTION_KEY_FALLBACK') or Fernet.generate_key().decode()
    }
    
    # ------ إعدادات التطوير ------
    DEBUG = os.environ.get('DEBUG', 'False').lower() == 'true'
    TESTING = os.environ.get('TESTING', 'False').lower() == 'true'
    
    # ------ إعدادات البريد الإلكتروني ------
    MAIL_SERVER = os.environ.get('MAIL_SERVER')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')
    ADMINS = [email.strip() for email in os.environ.get('ADMINS', '').split(',') if email.strip()]
    
    # ------ إعدادات الجلسات ------
    SESSION_TYPE = 'filesystem'
    SESSION_FILE_DIR = basedir / 'flask_session'
    SESSION_PERMANENT = False
    SESSION_USE_SIGNER = True
    SESSION_KEY_PREFIX = 'session:'
    
    @staticmethod
    def init_app(app):
        """تهيئة إضافية للتطبيق"""
        # إنشاء مجلدات ضرورية إذا لم تكن موجودة
        required_folders = [Config.BARCODE_FOLDER, Config.SESSION_FILE_DIR]
        
        for folder in required_folders:
            if not folder.exists():
                folder.mkdir(parents=True, exist_ok=True)
        
       


class DevelopmentConfig(Config):
    """إعدادات بيئة التطوير"""
    DEBUG = True
    SQLALCHEMY_ECHO = False
    COOKIE_SECURE = False
    WTF_CSRF_ENABLED = True


class TestingConfig(Config):
    """إعدادات بيئة الاختبار"""
    TESTING = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False
    COOKIE_SECURE = False


class ProductionConfig(Config):
    """إعدادات بيئة الإنتاج"""
    PREFERRED_URL_SCHEME = 'https'
    DEBUG = True
    TESTING = False

    WEASYPRINT_OPTIONS = {
        'optimize_images': True,
        'dpi': 96,
        'image_cache_dir': '/tmp/weasyprint_cache'
    }
    @classmethod
    def init_app(cls, app):
        Config.init_app(app)
        
        # معالجة المشاكل في السجلات
        import logging
        from logging.handlers import SMTPHandler, RotatingFileHandler
        
        # إنشاء مجلد السجلات إذا لم يكن موجوداً
        if not os.path.exists('logs'):
            os.makedirs('logs')
            
        # تسجيل الأخطاء في ملف
        file_handler = RotatingFileHandler(
            'logs/app.log',
            maxBytes=1024 * 1024 * 10,  # 10MB
            backupCount=10
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO)
        app.logger.addHandler(file_handler)
        
        # إعدادات الجلسة للإنتاج
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        
        # إرسال الأخطاء بالبريد
        if app.config.get('MAIL_SERVER') and app.config.get('ADMINS'):
            credentials = None
            secure = None
            
            if app.config.get('MAIL_USERNAME'):
                credentials = (app.config['MAIL_USERNAME'], app.config['MAIL_PASSWORD'])
                if app.config.get('MAIL_USE_TLS'):
                    secure = ()
            
            mail_handler = SMTPHandler(
                mailhost=(app.config['MAIL_SERVER'], app.config['MAIL_PORT']),
                fromaddr=app.config['MAIL_DEFAULT_SENDER'],
                toaddrs=app.config['ADMINS'],
                subject='Application Error',
                credentials=credentials,
                secure=secure
            )
            mail_handler.setLevel(logging.ERROR)
            app.logger.addHandler(mail_handler)


# تهيئة إعدادات البيئات المختلفة
config = {
    'development': DevelopmentConfig,
    'testing': TestingConfig,
    'production': ProductionConfig,
    'default': ProductionConfig  # تغيير الافتراضي إلى الإنتاج
}