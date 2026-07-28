"""
wsgi.py — точка входу для gunicorn (лежить у корені репозиторію).

Render запускає саме цей файл:
  gunicorn wsgi:flask_app --bind 0.0.0.0:10000 --workers 1 --timeout 300

Він просто імпортує flask_app з src/app.py і передає gunicorn.
Увесь код бота лежить у папці src/.
"""
import sys
import os

# Додаємо src/ до шляху щоб Python знайшов bot.py, search.py і т.д.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from app import flask_app  # noqa: F401  — gunicorn шукає саме цю змінну
