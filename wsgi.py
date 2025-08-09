from app import create_app

application = create_app()  # تغيير الاسم إلى `application`

if __name__ == "__main__":
    application.run()  # تحديث هنا أيضًا إذا كنت تستخدم التشغيل المباشر
