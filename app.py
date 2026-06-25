"""
app.py — Flask сервер для webhook.
gunicorn запускає: gunicorn app:flask_app --bind 0.0.0.0:10000 --workers 1 --timeout 300
"""
import os
import time
import telebot
from flask import Flask, request
from telebot.types import Update

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")

# Створюємо Flask першим — до будь-якого важкого коду
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return "ok", 200

@flask_app.route("/health", methods=["GET"])
def health():
    return "ok", 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    from bot import bot as tg_bot
    update = Update.de_json(request.stream.read().decode("utf-8"))
    tg_bot.process_new_updates([update])
    return "ok", 200

# Встановлюємо webhook
if WEBHOOK_URL and TELEGRAM_TOKEN:
    try:
        _bot = telebot.TeleBot(TELEGRAM_TOKEN)
        _bot.remove_webhook()
        time.sleep(1)
        _wh = f"{WEBHOOK_URL}/webhook"
        _bot.set_webhook(url=_wh)
        print(f"✅ Webhook: {_wh}")
    except Exception as e:
        print(f"⚠️ {e}")

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
