"""
app.py — Flask сервер для webhook (Start Command: gunicorn app:flask_app ...).
Дедуплікація update_id + миттєвий 200 + обробка у фоновому потоці.
"""
import os, time, threading
import telebot
from flask import Flask, request
from telebot.types import Update

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")

flask_app = Flask(__name__)

# Імпорт бота ОДРАЗУ (каталог вантажиться при старті, не при першому запиті)
from bot import bot as tg_bot

_seen_ids = set()
_seen_lock = threading.Lock()

@flask_app.route("/", methods=["GET"])
def index():
    return "ok", 200

@flask_app.route("/health", methods=["GET"])
def health():
    return "ok", 200

@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.stream.read().decode("utf-8"))
    uid = getattr(update, 'update_id', None)
    if uid is not None:
        with _seen_lock:
            if uid in _seen_ids:
                print(f"🔁 Дубль update {uid} — ігнорую")
                return "ok", 200
            _seen_ids.add(uid)
            if len(_seen_ids) > 2000:
                for _o in sorted(_seen_ids)[:1000]:
                    _seen_ids.discard(_o)
    # Коротка діагностика що прийшло
    try:
        m = update.message
        if m:
            kind = 'photo' if m.photo else ('document' if m.document else 'text')
            print(f"📥 upd {uid}: {kind} від {m.chat.id}: {(m.text or m.caption or '')[:40]}")
        elif update.callback_query:
            print(f"📥 upd {uid}: callback {update.callback_query.data}")
    except Exception:
        pass
    # Миттєвий 200 → Telegram не ретраїть; обробка у фоні з логуванням помилок
    def _safe_process(upd):
        import traceback
        try:
            print(f"🔄 Починаю обробку upd {uid}...", flush=True)
            tg_bot.process_new_updates([upd])
            print(f"✅ Обробку upd {uid} завершено", flush=True)
        except Exception as e:
            print(f"❌ Помилка обробки upd {uid}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
    threading.Thread(target=_safe_process, args=(update,), daemon=True).start()
    return "ok", 200

# Webhook: сумісно зі старими версіями telebot
if WEBHOOK_URL and TELEGRAM_TOKEN:
    try:
        try:
            tg_bot.remove_webhook()
        except Exception:
            pass
        time.sleep(1)
        _wh = f"{WEBHOOK_URL}/webhook"
        try:
            tg_bot.set_webhook(url=_wh, drop_pending_updates=True)
        except TypeError:
            tg_bot.set_webhook(url=_wh)   # стара версія без параметра
        print(f"✅ Webhook: {_wh}")
    except Exception as e:
        print(f"⚠️ Webhook: {e}")

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
