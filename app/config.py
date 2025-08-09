import os
from cryptography.fernet import Fernet
from datetime import timedelta
from pathlib import Path

basedir = Path(__file__).parent.absolute()
from dotenv import load_dotenv
load_dotenv()  # تحميل المتغيرات

# في config.py

# تحديد مسار الملف بشكل مطلق
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)
class Config:

    # ------ الإعدادات الأساسية ------
    SECRET_KEY = os.environ.get('SECRET_KEY')
    ENCRYPTION_KEY = os.environ.get('ENCRYPTION_KEY')
    @staticmethod
    def init_app(app):
        """تهيئة إضافية للتطبيق"""
        # لا تقم بتوليد مفتاح جديد إذا كان موجوداً
        if not os.environ.get('ENCRYPTION_KEY'):
            key = Fernet.generate_key().decode()
            os.environ['ENCRYPTION_KEY'] = key
            app.config['ENCRYPTION_KEY'] = key
            print(f"تم توليد مفتاح تشفير جديد: {key}")
    # ------ إعدادات قاعدة البيانات ------
    SQLALCHEMY_DATABASE_URI = os.environ.get('SQLALCHEMY_DATABASE_URI') or \
                             f"sqlite:///{basedir/'app.db'}"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_pre_ping': True,
        'pool_recycle': 3600,
        'pool_size': 10,
        'max_overflow': 20
    }
    
    
    # ------ إعدادات الترحيل (Pagination) ------
    DEFAULT_PER_PAGE = 20
    MAX_PER_PAGE = 100
    
    # ------ إعدادات Salla API ------
    SALLA_CLIENT_ID = os.environ.get('SALLA_CLIENT_ID')
    SALLA_CLIENT_SECRET = os.environ.get('SALLA_CLIENT_SECRET')
    SALLA_AUTH_URL = os.environ.get('SALLA_AUTH_URL', 'https://accounts.salla.sa/oauth2/auth')
    SALLA_TOKEN_URL = os.environ.get('SALLA_TOKEN_URL', 'https://accounts.salla.sa/oauth2/token')
    SALLA_API_BASE_URL = os.environ.get('SALLA_API_BASE_URL', 'https://api.salla.dev/admin/v2')
    SALLA_BASE_URL = SALLA_API_BASE_URL  # للإبقاء على التوافق مع الكود القديم
    SALLA_ORDERS_API = os.environ.get('SALLA_ORDERS_API', f"{SALLA_API_BASE_URL}/orders")
    
    SALLA_ORDERS_ENDPOINT = f"{SALLA_API_BASE_URL}/orders"
    SALLA_PRODUCTS_ENDPOINT = f"{SALLA_API_BASE_URL}/products"
    SALLA_STORE_INFO_ENDPOINT = f"{SALLA_API_BASE_URL}/store/info"
    REDIRECT_URI = os.environ.get('REDIRECT_URI', 'http://localhost:5000/callback')
    # ------ إعدادات الباركود ------
    BARCODE_FOLDER = basedir / 'static' / 'barcodes'
    if not ENCRYPTION_KEY:
        ENCRYPTION_KEY = Fernet.generate_key().decode()
        # استبدال logger بـ print مؤقتاً
        print("WARNING: تم توليد مفتاح تشفير جديد. يجب حفظه في ملف .env!")
    # ------ إعدادات الجلسات والأمان ------
    SESSION_COOKIE_NAME = 'secure_session'
    SESSION_COOKIE_SECURE =False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    SESSION_REFRESH_EACH_REQUEST = True
    
    # ------ إعدادات CSRF ------
    WTF_CSRF_ENABLED = True
    WTF_CSRF_SECRET_KEY = os.environ.get('WTF_CSRF_SECRET_KEY') or os.urandom(24)
    WTF_CSRF_TIME_LIMIT = 3600  # ساعة واحدة بالثواني
    
    # ------ إعدادات التشفير ------
    DATA_ENCRYPTION_KEYS = {
        'default': ENCRYPTION_KEY,
        'fallback': Fernet.generate_key().decode()
    }
    
    # ------ إعدادات التطوير ------
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    TESTING = False
    
    # ------ إعدادات البريد الإلكتروني ------
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.example.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', 587))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'True').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', 'noreply@example.com')
    
    @staticmethod
    def init_app(app):
        """تهيئة إضافية للتطبيق"""
        # إنشاء مجلدات ضرورية إذا لم تكن موجودة
        required_folders = [Config.BARCODE_FOLDER]
        
        for folder in required_folders:
            if not folder.exists():
                folder.mkdir(parents=True, exist_ok=True)
        
        # إضافة رؤوس الأمان
        @app.after_request
        def add_security_headers(response):
            response.headers['X-Content-Type-Options'] = 'nosniff'
            response.headers['X-Frame-Options'] = 'DENY'
            response.headers['X-XSS-Protection'] = '1; mode=block'
            response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'"
            return response


class DevelopmentConfig(Config):
    """إعدادات بيئة التطوير"""
    DEBUG = True
    SQLALCHEMY_ECHO = True  # يعرض استعلامات SQL في السجلات


class TestingConfig(Config):
    """إعدادات بيئة الاختبار"""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False  # تعطيل CSRF للاختبارات


class ProductionConfig(Config):
    """إعدادات بيئة الإنتاج"""
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = 'Strict'
    PREFERRED_URL_SCHEME = 'https'
    
    @classmethod
    def init_app(cls, app):
        Config.init_app(app)
        
        # معالجة المشاكل في السجلات
        import logging
        from logging.handlers import SMTPHandler
        
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
    'default': DevelopmentConfig
}