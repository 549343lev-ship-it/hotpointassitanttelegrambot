"""
wsgi.py — точка входу для gunicorn (лежить у корені репозиторію).

Start Command:
  gunicorn wsgi:flask_app --bind 0.0.0.0:10000 --workers 1 --timeout 300
"""
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# Додаємо корінь щоб Python знайшов папки engine/, knowledge/, clients/, data/
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.app import flask_app  # noqa: F401
