"""
app.py — максимально простий webhook без зайвого.
"""
import os, time, threading, traceback
import telebot
from flask import Flask, request

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
WEBHOOK_URL    = os.environ.get("WEBHOOK_URL", "").rstrip("/")

# Імпортуємо бота
print("Імпортую bot...", flush=True)
from bot import bot as tg_bot
print(f"OK. Handlers: {len(tg_bot.message_handlers)}", flush=True)

flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return f"ok handlers={len(tg_bot.message_handlers)}", 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.stream.read().decode("utf-8")
    print(f"RAW: {raw[:200]}", flush=True)
    try:
        import json
        data = json.loads(raw)
        update = telebot.types.Update.de_json(data)
        print(f"UPDATE type={type(update)} msg={update.message}", flush=True)
        if update.message:
            m = update.message
            print(f"MSG chat={m.chat.id} text={m.text!r} content_type={m.content_type}", flush=True)
        tg_bot.process_new_updates([update])
        print("process_new_updates done", flush=True)
    except Exception as e:
        print(f"ERROR: {e}", flush=True)
        traceback.print_exc()
    return "ok", 200

# Встановлюємо webhook
if WEBHOOK_URL and TELEGRAM_TOKEN:
    try:
        tg_bot.remove_webhook()
        time.sleep(1)
        wh = f"{WEBHOOK_URL}/webhook"
        try:
            tg_bot.set_webhook(url=wh, drop_pending_updates=True)
        except TypeError:
            tg_bot.set_webhook(url=wh)
        print(f"Webhook: {wh}", flush=True)
    except Exception as e:
        print(f"Webhook error: {e}", flush=True)

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
