# Standard library imports
from datetime import datetime, timedelta, timezone
from typing import Optional
import re

# Third-party imports
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import (
    Column, Integer, String, Boolean, 
    DateTime, LargeBinary, ForeignKey,
    func, event
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, backref, validates

# Local application imports
from . import db

def repair_encrypted_token(token):
    """إصلاح تام للتوكنات التالفة"""
    if token is None:
        return None
        
    if isinstance(token, bytes):
        try:
            token_str = token.decode('utf-8')
        except UnicodeDecodeError:
            return token
    else:
        token_str = str(token)
    
    token_str = re.sub(r'[^a-zA-Z0-9+/=]', '', token_str)
    
    if len(token_str) % 4 != 0:
        padding = '=' * (4 - len(token_str) % 4)
        token_str = token_str + padding
    
    return token_str

class User(db.Model):
    __tablename__ = 'users'
    
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, server_default=func.now())
    updated_at = db.Column(db.DateTime, onupdate=func.now())
    
    # حقول التوكنات
    _salla_access_token = db.Column('salla_access_token', LargeBinary)
    _salla_refresh_token = db.Column('salla_refresh_token', LargeBinary)
    token_expires_at = db.Column(db.DateTime)
    token_refreshed_at = db.Column(db.DateTime)
    
    # حقول إضافية
    store_id = db.Column(db.Integer, default=1, index=True)
    last_sync = db.Column(db.DateTime)
    remember_token = db.Column(db.String(100))
    
    # العلاقات
    status_notes = relationship('OrderStatusNote', back_populates='user', 
                              foreign_keys='OrderStatusNote.created_by')
    # === دوال إدارة التوكنات ===
    
    @property
    def salla_access_token(self):
        """فك تشفير توكن الوصول عند الطلب"""
        return self._decrypt_token(self._salla_access_token)
    
    @salla_access_token.setter
    def salla_access_token(self, value):
        """تشفير وحفظ توكن الوصول"""
        self._salla_access_token = self._encrypt_token(value)
    
    @property
    def salla_refresh_token(self):
        """فك تشفير توكن التحديث عند الطلب"""
        return self._decrypt_token(self._salla_refresh_token)
    
    @salla_refresh_token.setter
    def salla_refresh_token(self, value):
        """تشفير وحفظ توكن التحديث"""
        self._salla_refresh_token = self._encrypt_token(value)
    
    def _encrypt_token(self, token):
        """دالة مساعدة للتشفير"""
        if not token:
            return None
        try:
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            return fernet.encrypt(token.encode('utf-8'))
        except Exception as e:
            current_app.logger.error(f"فشل تشفير التوكن: {str(e)}")
            raise
    # أضف هذه الدوال داخل class User
    
    def get_refresh_token(self):
        """دالة متوافقة مع الإصدارات القديمة"""
        return self.salla_refresh_token
    
    @property
    def has_valid_tokens(self):
        """التحقق من صلاحية التوكنات مع هامش أمان 5 دقائق"""
        if not all([self._salla_access_token, self._salla_refresh_token, self.token_expires_at]):
            return False
            
        return datetime.utcnow() < (self.token_expires_at - timedelta(minutes=5))

    def get_access_token(self):
        """دالة متوافقة مع الإصدارات القديمة"""
        return self.salla_access_token
    def _decrypt_token(self, encrypted_token):
        """دالة مساعدة لفك التشفير"""
        if not encrypted_token:
            return None
        try:
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            return fernet.decrypt(encrypted_token).decode('utf-8')
        except InvalidToken:
            current_app.logger.error("توكن غير صالح أو مفتاح تشفير خاطئ")
            return None
        except Exception as e:
            current_app.logger.error(f"خطأ في فك التشفير: {str(e)}")
            return None
    
    def set_tokens(self, access_token, refresh_token, expires_in=3600):
        """دالة معدلة مع قيمة افتراضية لـ expires_in"""
        try:
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            self._salla_access_token = fernet.encrypt(access_token.encode('utf-8'))
            self._salla_refresh_token = fernet.encrypt(refresh_token.encode('utf-8'))
            self.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            db.session.commit()
            
            
            return True
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"خطأ في حفظ التوكنات: {str(e)}")
            return False
        def refresh_access_token(self, new_access_token, new_expires_in):
            """تحديث توكن الوصول فقط"""
            try:
                self.salla_access_token = new_access_token
                self.token_expires_at = datetime.utcnow() + timedelta(seconds=new_expires_in)
                self.token_refreshed_at = datetime.utcnow()
                db.session.commit()
                return True
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"فشل تحديث توكن الوصول: {str(e)}")
                return False
        
    @hybrid_property
    def tokens_are_valid(self):
        """التحقق من صلاحية التوكنات مع هامش أمان 5 دقائق"""
        return (
            self._salla_access_token is not None and
            self._salla_refresh_token is not None and
            self.token_expires_at is not None and
            datetime.utcnow() < (self.token_expires_at - timedelta(minutes=5))
        )
    
    # === دوال إدارة الحساب ===
    
    @validates('email')
    def validate_email(self, key, email):
        """التحقق من صحة البريد الإلكتروني"""
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            raise ValueError("بريد إلكتروني غير صالح")
        return email.lower()
    
    def set_password(self, password):
        """تشفير وحفظ كلمة المرور"""
        if len(password) < 8:
            raise ValueError("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        """التحقق من تطابق كلمة المرور"""
        return check_password_hash(self.password_hash, password)
    
    # === دوال Remember Me ===
    
    def generate_remember_token(self, expires_days=30):
        """إنشاء توكن تذكرني"""
        try:
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            token_data = f"{self.id}:{datetime.utcnow().isoformat()}"
            self.remember_token = fernet.encrypt(token_data.encode('utf-8')).decode('utf-8')
            db.session.commit()
            return self.remember_token
        except Exception as e:
            current_app.logger.error(f"فشل إنشاء توكن تذكرني: {str(e)}")
            return None
    
    @classmethod
    def verify_remember_token(cls, token):
        """التحقق من توكن تذكرني"""
        if not token:
            return None
            
        try:
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            decrypted = fernet.decrypt(token.encode('utf-8')).decode('utf-8')
            user_id, timestamp = decrypted.split(':')
            
            user = cls.query.get(int(user_id))
            if not user or user.remember_token != token:
                return None
                
            if (datetime.utcnow() - datetime.fromisoformat(timestamp)).days > 30:
                return None
                
            return user
        except Exception:
            return None
    
    # === دوال مساعدة ===
    
    def __repr__(self):
        return f'<User {self.email}>'
    
    def get_auth_data(self):
        return {
            'id': self.id,
            'email': self.email,
            'is_admin': True,
            'store_id': self.store_id
        }
    def to_dict(self):
        """تحويل بيانات المستخدم لقاموس (بدون معلومات حساسة)"""
        return {
            'id': self.id,
            'email': self.email,
            'is_admin': self.is_admin,
            'store_id': self.store_id,
            'last_sync': self.last_sync.isoformat() if self.last_sync else None,
            'has_valid_tokens': self.tokens_are_valid
        }
class Employee(db.Model):
    __tablename__ = 'employees'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    store_id = db.Column(db.Integer, nullable=False, index=True)
    is_active = db.Column(db.Boolean, default=True)
    role = db.Column(db.String(50), default='general')
    is_delivery_manager = db.Column(db.Boolean, default=False)
    region = db.Column(db.String(100))
    deactivated_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    remember_token = db.Column(db.String(100))  # New field for remember me functionality
    
    permissions = relationship('EmployeePermission', back_populates='employee')
    custom_statuses = relationship('EmployeeCustomStatus', back_populates='employee')
    assignments = relationship('OrderAssignment', back_populates='employee')
    
    def set_password(self, password: str):
        if len(password) < 8:
            raise ValueError("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)
    def get_auth_data(self):
        return {
            'id': self.id,
            'email': self.email,
            'is_admin': False,
            'role': self.role,
            'store_id': self.store_id
        }
    def generate_remember_token(self):
        """Generate a token for 'remember me' functionality"""
        fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
        token = fernet.encrypt(f"{self.id}:{datetime.utcnow().isoformat()}".encode('utf-8'))
        self.remember_token = token
        db.session.commit()
        return token
    
    @staticmethod
    def verify_remember_token(token):
        """Verify remember token from cookie"""
        if not token:
            return None
            
        try:
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            decrypted = fernet.decrypt(token).decode('utf-8')
            emp_id, timestamp = decrypted.split(':')
            employee = Employee.query.get(int(emp_id))
            
            # Check if token is still valid (e.g., not expired)
            token_time = datetime.fromisoformat(timestamp)
            if (datetime.utcnow() - token_time).days > 30:  # 30 days expiration
                return None
                
            if employee and employee.remember_token == token:
                return employee
        except Exception:
            return None
        return None
    
    def get_access_token(self):
        """الحصول على توكن الوصول من المستخدم الرئيسي للمتجر"""
        user = User.query.filter_by(store_id=self.store_id).first()
        if user:
            return user.get_access_token()
        return None
    
    def get_refresh_token(self):
        """الحصول على توكن التحديث من المستخدم الرئيسي للمتجر"""
        user = User.query.filter_by(store_id=self.store_id).first()
        if user:
            return user.get_refresh_token()
        return None

# Other models remain the same as in the original file
class Department(db.Model):
    __tablename__ = 'departments'
    
    id = db.Column(db.Integer, primary_key=True)
    salla_id = db.Column(db.Integer, unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    store_id = db.Column(db.Integer, ForeignKey('users.store_id'), nullable=False)
    parent_id = db.Column(db.Integer, ForeignKey('departments.id'), nullable=True)
    
    children = relationship('Department', backref=backref('parent', remote_side=[id]))
    permissions = relationship('EmployeePermission', back_populates='department')

class EmployeePermission(db.Model):
    __tablename__ = 'employee_permissions'
    
    id = db.Column(db.Integer, primary_key=True)
    employee_id = db.Column(db.Integer, ForeignKey('employees.id'), nullable=False)
    department_id = db.Column(db.Integer, ForeignKey('departments.id'), nullable=False)
    
    employee = relationship('Employee', back_populates='permissions')
    department = relationship('Department', back_populates='permissions')

class SallaOrder(db.Model):
    __tablename__ = 'salla_orders'
    
    id = db.Column(db.String(50), primary_key=True)
    store_id = db.Column(db.Integer, nullable=False)
    customer_name = db.Column(db.String(255))
    created_at = db.Column(db.DateTime)
    total_amount = db.Column(db.Float)
    currency = db.Column(db.String(10), default='SAR')
    payment_method = db.Column(db.String(100))
    status = db.Column(db.String(50))
    status_slug = db.Column(db.String(50))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    raw_data = db.Column(db.JSON)
    
    status_notes = relationship('OrderStatusNote', back_populates='order')
    employee_statuses = relationship('OrderEmployeeStatus', back_populates='order')
    assignments = relationship('OrderAssignment', back_populates='order')

class OrderStatusNote(db.Model):
    __tablename__ = 'order_status_notes'
    
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(50), ForeignKey('salla_orders.id'), nullable=False)
    status_flag = db.Column(db.String(20), nullable=False)
    note = db.Column(db.Text)
    created_by = db.Column(db.Integer, ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = relationship('User', back_populates='status_notes')
    order = relationship('SallaOrder', back_populates='status_notes')

class EmployeeCustomStatus(db.Model):
    __tablename__ = 'employee_custom_statuses'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    color = db.Column(db.String(20), default='#6c757d')
    employee_id = db.Column(db.Integer, ForeignKey('employees.id'), nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    
    employee = relationship('Employee', back_populates='custom_statuses')
    order_statuses = relationship('OrderEmployeeStatus', back_populates='status')

class Product(db.Model):
    __tablename__ = 'product'
    id = db.Column(db.Integer, primary_key=True)
    salla_id = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    price = db.Column(db.Float)
    currency = db.Column(db.String(10), default='SAR')
    sku = db.Column(db.String(100))
    stock = db.Column(db.Integer)
    main_image = db.Column(db.String(255))
    store_id = db.Column(db.Integer, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

class OrderAssignment(db.Model):
    __tablename__ = 'order_assignment'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(50), db.ForeignKey('salla_orders.id'), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    assigned_by = db.Column(db.Integer, nullable=False, default=0)
    assigned_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    employee = relationship('Employee', back_populates='assignments')
    order = relationship('SallaOrder', back_populates='assignments')

class OrderDelivery(db.Model):
    __tablename__ = 'order_delivery'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(50), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)
    delivered_at = db.Column(db.DateTime, default=db.func.current_timestamp())

class OrderEmployeeStatus(db.Model):
    __tablename__ = 'order_employee_status'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(50), db.ForeignKey('salla_orders.id'), nullable=False)
    status_id = db.Column(db.Integer, db.ForeignKey('employee_custom_statuses.id'), nullable=False)
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    status = relationship('EmployeeCustomStatus', back_populates='order_statuses')
    order = relationship('SallaOrder', back_populates='employee_statuses')
# أضف هذا في نهاية models.py قبل @event.listens_for



# تبقى أحداث SQLAlchemy كما هي
@event.listens_for(User, 'before_insert')
def validate_user(mapper, connection, target):
    if not target.email:
        raise ValueError("البريد الإلكتروني مطلوب")

@event.listens_for(Employee, 'before_insert')
def validate_employee(mapper, connection, target):
    if not target.email:
        raise ValueError("البريد الإلكتروني مطلوب")