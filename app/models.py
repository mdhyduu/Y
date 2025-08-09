from . import db
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import relationship, backref, validates
from datetime import datetime, timezone
from flask import current_app
from cryptography.fernet import Fernet, InvalidToken
import re
from sqlalchemy import event, ForeignKey
from typing import Optional

from datetime import datetime
# في models.py بعد استيرادات المكتبات
 

def repair_encrypted_token(token):
    """إصلاح تام للتوكنات التالفة"""
    if token is None:
        return None
        
    # التحويل إلى سلسلة إذا كانت bytes
    if isinstance(token, bytes):
        try:
            token_str = token.decode('utf-8')
        except UnicodeDecodeError:
            # إذا فشل التحويل، قد تكون بيانات ثنائية مشفرة
            return token
    else:
        token_str = str(token)
    
    # إزالة أي أحرف غير صالحة
    token_str = re.sub(r'[^a-zA-Z0-9+/=]', '', token_str)
    
    # إضافة الحشو المفقود
    if len(token_str) % 4 != 0:
        padding = '=' * (4 - len(token_str) % 4)
        token_str = token_str + padding
    
    return token_str

# ====================== نموذج المستخدم======================
class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(128), nullable=False)
    salla_access_token = db.Column(db.LargeBinary)
    salla_refresh_token = db.Column(db.LargeBinary)
    store_id = db.Column(db.Integer, nullable=False, default=1, index=True)
    is_admin = db.Column(db.Boolean, default=False)
    last_sync = db.Column(db.DateTime)
    token_expires_at = db.Column(db.DateTime)
    token_refreshed_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # العلاقات المعدلة
    status_notes = relationship('OrderStatusNote', back_populates='user', 
                              foreign_keys='OrderStatusNote.created_by')

    @validates('email')
    def validate_email(self, key, email):
        if not re.match(r"[^@]+@[^@]+\.[^@]+", email):
            raise ValueError("بريد إلكتروني غير صالح")
        return email.lower()
    
    def set_password(self, password: str):
        if len(password) < 8:
            raise ValueError("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)
    
   
    def set_tokens(self, access_token, refresh_token):
        try:
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            
            current_app.logger.info(f"مفتاح التشفير المستخدم: {current_app.config['ENCRYPTION_KEY']}")
            current_app.logger.info(f"نوع access_token: {type(access_token)}، نوع refresh_token: {type(refresh_token)}")
            
            encrypted_access = fernet.encrypt(access_token.encode('utf-8'))
            encrypted_refresh = fernet.encrypt(refresh_token.encode('utf-8'))
            
            self.salla_access_token = encrypted_access
            self.salla_refresh_token = encrypted_refresh
            db.session.commit()
            
            current_app.logger.info("تم حفظ التوكنات بنجاح")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"فشل حفظ التوكنات: {str(e)}", exc_info=True)
            raise
    def get_refresh_token(self):
        if not self.salla_refresh_token:
            return None
        
        try:
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            
            # تحويل التوكن إلى bytes إذا كان نصاً
            encrypted_token = self.salla_refresh_token
            if isinstance(encrypted_token, str):
                try:
                    encrypted_token = encrypted_token.encode('utf-8')
                except Exception as e:
                    current_app.logger.error(f"فشل تحويل التوكن إلى bytes: {str(e)}")
                    return None
            
            # فك التشفير
            try:
                decrypted_token = fernet.decrypt(encrypted_token).decode('utf-8')
                return decrypted_token
            except InvalidToken:
                current_app.logger.error("فشل فك التشفير: التوكن غير صالح أو مفتاح التشفير خاطئ")
                return None
                
        except Exception as e:
            current_app.logger.error(f"خطأ غير متوقع في فك التشفير: {str(e)}")
            return None
    def get_access_token(self):
        if not self.salla_access_token:
            return None
        
        try:
            fernet = Fernet(current_app.config['ENCRYPTION_KEY'])
            
            # تحويل التوكن إلى bytes إذا كان نصاً
            encrypted_token = self.salla_access_token
            if isinstance(encrypted_token, str):
                try:
                    encrypted_token = encrypted_token.encode('utf-8')
                except Exception as e:
                    current_app.logger.error(f"فشل تحويل التوكن إلى bytes: {str(e)}")
                    return None
            
            # فك التشفير
            try:
                decrypted_token = fernet.decrypt(encrypted_token).decode('utf-8')
                return decrypted_token
            except InvalidToken:
                current_app.logger.error("فشل فك التشفير: التوكن غير صالح أو مفتاح التشفير خاطئ")
                return None
                
        except Exception as e:
            current_app.logger.error(f"خطأ غير متوقع في فك التشفير: {str(e)}")
            return None        
# ====================== نموذج الموظف ======================
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
    
    # العلاقات المعدلة
    permissions = relationship('EmployeePermission', back_populates='employee')
    custom_statuses = relationship('EmployeeCustomStatus', back_populates='employee')
    assignments = relationship('OrderAssignment', back_populates='employee')
    def set_password(self, password: str):
        if len(password) < 8:
            raise ValueError("كلمة المرور يجب أن تكون 8 أحرف على الأقل")
        self.password_hash = generate_password_hash(password)
    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)    
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
# ====================== نماذج أخرى ======================
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
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)  # Changed from 'employee.id'
    assigned_by = db.Column(db.Integer, nullable=False, default=0)
    assigned_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    employee = relationship('Employee', back_populates='assignments')
    order = relationship('SallaOrder', back_populates='assignments')

class OrderDelivery(db.Model):
    __tablename__ = 'order_delivery'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(50), nullable=False)
    employee_id = db.Column(db.Integer, db.ForeignKey('employees.id'), nullable=False)  # Changed from 'employee.id'
    delivered_at = db.Column(db.DateTime, default=db.func.current_timestamp())
class OrderEmployeeStatus(db.Model):
    __tablename__ = 'order_employee_status'
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.String(50), db.ForeignKey('salla_orders.id'), nullable=False)
    status_id = db.Column(db.Integer, db.ForeignKey('employee_custom_statuses.id'), nullable=False)  # Changed from 'employee_custom_status.id'
    note = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    status = relationship('EmployeeCustomStatus', back_populates='order_statuses')
    order = relationship('SallaOrder', back_populates='employee_statuses')
# ====================== أحداث النماذج ======================
@event.listens_for(User, 'before_insert')
def validate_user(mapper, connection, target):
    if not target.email:
        raise ValueError("البريد الإلكتروني مطلوب")

@event.listens_for(Employee, 'before_insert')
def validate_employee(mapper, connection, target):
    if not target.email:
        raise ValueError("البريد الإلكتروني مطلوب")