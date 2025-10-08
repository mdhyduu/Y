import boto3
from botocore.exceptions import ClientError
import os
from flask import current_app
from werkzeug.utils import secure_filename
import uuid

class DigitalOceanStorage:
    def __init__(self):
        self.s3_client = None
        self.bucket_name = None
        self.region = None
        
    def init_app(self, app):
        """تهيئة العميل مع التطبيق"""
        self.bucket_name = app.config.get('DO_SPACES_BUCKET')
        self.region = app.config.get('DO_SPACES_REGION')
        
        self.s3_client = boto3.client(
            's3',
            region_name=self.region,
            endpoint_url=f'https://{self.region}.digitaloceanspaces.com',
            aws_access_key_id=app.config.get('DO_SPACES_KEY'),
            aws_secret_access_key=app.config.get('DO_SPACES_SECRET')
        )
    
    def upload_file(self, file, folder='shipping-policies'):
        """رفع ملف إلى Spaces"""
        try:
            # توليد اسم فريد للملف
            filename = secure_filename(file.filename)
            unique_filename = f"{uuid.uuid4()}_{filename}"
            object_key = f"{folder}/{unique_filename}"
            
            # رفع الملف
            self.s3_client.upload_fileobj(
                file,
                self.bucket_name,
                object_key,
                ExtraArgs={
                    'ACL': 'public-read',
                    'ContentType': file.content_type
                }
            )
            
            # توليد الرابط العام
            file_url = f"https://{self.bucket_name}.{self.region}.digitaloceanspaces.com/{object_key}"
            return file_url
            
        except ClientError as e:
            current_app.logger.error(f"خطأ في رفع الملف: {str(e)}")
            return None

    def upload_qr_code(self, file_data, order_id, folder='qrcodes'):
        """رفع صورة QR Code إلى Spaces"""
        try:
            # توليد اسم فريد للملف بناءً على رقم الطلب
            filename = f"qr_{order_id}.png"
            unique_filename = f"{uuid.uuid4()}_{filename}"
            object_key = f"{folder}/{unique_filename}"
            
            # رفع الملف
            self.s3_client.upload_fileobj(
                file_data,
                self.bucket_name,
                object_key,
                ExtraArgs={
                    'ACL': 'public-read',
                    'ContentType': 'image/png'
                }
            )
            
            # توليد الرابط العام
            file_url = f"https://{self.bucket_name}.{self.region}.digitaloceanspaces.com/{object_key}"
            return file_url
            
        except ClientError as e:
            current_app.logger.error(f"خطأ في رفع QR Code: {str(e)}")
            return None
    
    def delete_file(self, file_url):
        """حذف ملف من Spaces"""
        try:
            # استخراج object_key من الرابط
            if f".digitaloceanspaces.com/" in file_url:
                object_key = file_url.split(f".digitaloceanspaces.com/")[1]
                self.s3_client.delete_object(
                    Bucket=self.bucket_name,
                    Key=object_key
                )
                return True
            return False
        except ClientError as e:
            current_app.logger.error(f"خطأ في حذف الملف: {str(e)}")
            return False

# إنشاء نسخة عامة من الخدمة
do_storage = DigitalOceanStorage()