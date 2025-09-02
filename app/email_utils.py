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
        smtp_server = current_app.config.get('MAIL_SERVER')
        smtp_port = current_app.config.get('MAIL_PORT', 587)
        smtp_username = current_app.config.get('MAIL_USERNAME')
        smtp_password = current_app.config.get('MAIL_PASSWORD')
        from_email = current_app.config.get('MAIL_DEFAULT_SENDER', smtp_username)
        
        # تسجيل معلومات الإعدادات للت debugging
        current_app.logger.info(f"إعدادات البريد: Server={smtp_server}, Port={smtp_port}, Username={smtp_username}")

        # التحقق من وجود جميع الإعدادات المطلوبة
        if not all([smtp_server, smtp_username, smtp_password]):
            current_app.logger.error("إعدادات البريد الإلكتروني غير مكتملة")
            return False

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
            
        current_app.logger.info(f"تم إرسال بريد التحقق بنجاح إلى: {email}")
        return True
        
    except smtplib.SMTPException as e:
        current_app.logger.error(f"خطأ في بروتوكول البريد: {str(e)}")
        return False
    except Exception as e:
        current_app.logger.error(f"فشل إرسال بريد التحقق: {str(e)}", exc_info=True)
        return False