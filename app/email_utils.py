import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import current_app
import random
import string

def generate_verification_code(length=6):
    """إنشاء كود تحقق عشوائي"""
    return ''.join(random.choices(string.digits, k=length))

def send_verification_email(email, code):
    """إرسال بريد التحقق بالكود"""
    try:
        # تكوين إعدادات البريد
        smtp_server = current_app.config['MAIL_SERVER']
        smtp_port = current_app.config['MAIL_PORT']
        smtp_username = current_app.config['MAIL_USERNAME']
        smtp_password = current_app.config['MAIL_PASSWORD']
        from_email = current_app.config['MAIL_DEFAULT_SENDER']

        # إنشاء الرسالة
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = email
        msg['Subject'] = 'كود التحقق - نظام إدارة الطلبات'

        # محتوى البريد
        html = f"""
        <div dir="rtl">
            <h2>مرحباً بك في نظام إدارة الطلبات</h2>
            <p>كود التحقق الخاص بك هو: <strong>{code}</strong></p>
            <p>استخدم هذا الكود لتأكيد بريدك الإلكتروني.</p>
            <p>ينتهي صلاحية الكود خلال 15 دقيقة.</p>
            <hr>
            <p>إذا لم تطلب هذا البريد، يرجى تجاهله.</p>
        </div>
        """
        
        msg.attach(MIMEText(html, 'html'))

        # إرسال البريد
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(msg)
            
        return True
    except Exception as e:
        current_app.logger.error(f"فشل إرسال بريد التحقق: {str(e)}")
        return False