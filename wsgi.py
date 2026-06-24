"""
wsgi.py — точка входу для gunicorn
Запуск: gunicorn wsgi:app --bind 0.0.0.0:10000 --workers 1 --timeout 120
"""
from bot import app

if __name__ == "__main__":
    app.run()
