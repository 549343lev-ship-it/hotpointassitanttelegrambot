"""
app.py — Flask webhook.
Start Command: gunicorn app:flask_app --bind 0.0.0.0:10000 --workers 1 --timeout 300
"""
import os, time, threading, traceback
import telebot
from flask import Flask, request
from telebot.types import Update

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
WEBHOOK_URL    = os.environ.get("WEBHOOK_URL", "").rstrip("/")

print("📦 Імпортую bot.py...", flush=True)
from bot import bot as tg_bot
print(f"✅ bot.py OK, зареєстровано handlers: {len(tg_bot.message_handlers)}", flush=True)

flask_app = Flask(__name__)

_seen = set()
_lock = threading.Lock()

@flask_app.route("/", methods=["GET"])
def index():
    return f"ok handlers={len(tg_bot.message_handlers)}", 200

@flask_app.route("/health", methods=["GET"])
def health():
    return "ok", 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.stream.read().decode("utf-8")
    update = Update.de_json(raw)
    uid = getattr(update, "update_id", None)

    # Дедуплікація ретраїв Telegram
    if uid is not None:
        with _lock:
            if uid in _seen:
                return "ok", 200
            _seen.add(uid)
            if len(_seen) > 2000:
                for o in sorted(_seen)[:1000]:
                    _seen.discard(o)

    # Лог що прийшло
    try:
        m = update.message
        if m:
            print(f"📥 upd {uid}: {m.content_type} chat={m.chat.id} "
                  f"text={(m.text or m.caption or '')[:40]!r}", flush=True)
        elif update.callback_query:
            print(f"📥 upd {uid}: callback {update.callback_query.data}", flush=True)
    except Exception:
        pass

    # Миттєвий 200, обробка у фоні; помилки хендлерів тепер ВИДНІ
    # (bot створений з threaded=False, тому винятки долітають сюди)
    def run(u):
        try:
            tg_bot.process_new_updates([u])
            print(f"✅ upd {uid} оброблено", flush=True)
        except Exception as e:
            print(f"❌ upd {uid} ПОМИЛКА: {e}", flush=True)
            traceback.print_exc()

    threading.Thread(target=run, args=(update,), daemon=True).start()
    return "ok", 200

# Webhook (сумісно зі старим telebot)
if WEBHOOK_URL and TELEGRAM_TOKEN:
    try:
        try:
            tg_bot.remove_webhook()
        except Exception:
            pass
        time.sleep(1)
        wh = f"{WEBHOOK_URL}/webhook"
        try:
            tg_bot.set_webhook(url=wh, drop_pending_updates=True)
        except TypeError:
            tg_bot.set_webhook(url=wh)
        print(f"✅ Webhook: {wh}", flush=True)
    except Exception as e:
        print(f"⚠️ Webhook: {e}", flush=True)

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
