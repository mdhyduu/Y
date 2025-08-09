from app import create_app

app = create_app()
application = app  # إضافة هذا السطر

if __name__ == "__main__":
    app.run()
