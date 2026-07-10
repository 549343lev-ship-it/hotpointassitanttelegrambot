"""
app.py — Flask webhook. Start Command: gunicorn app:flask_app --bind 0.0.0.0:10000 --workers 1 --timeout 300
"""
import os, time, threading
import telebot
from flask import Flask, request
from telebot.types import Update

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
WEBHOOK_URL    = os.environ.get("WEBHOOK_URL", "").rstrip("/")

flask_app = Flask(__name__)

# Імпорт бота
print("📦 Імпортую bot.py...", flush=True)
from bot import bot as tg_bot
print("✅ bot.py імпортовано", flush=True)

_seen_ids  = set()
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
                return "ok", 200
            _seen_ids.add(uid)
            if len(_seen_ids) > 2000:
                for _o in sorted(_seen_ids)[:1000]:
                    _seen_ids.discard(_o)
    # Лог
    try:
        m = update.message
        if m:
            kind = 'photo' if m.photo else ('doc' if m.document else 'text')
            print(f"📥 upd {uid}: {kind} від {m.chat.id}: {repr((m.text or m.caption or '')[:40])}", flush=True)
        elif update.callback_query:
            print(f"📥 upd {uid}: callback={update.callback_query.data}", flush=True)
    except Exception:
        pass

    def _process(upd):
        try:
            print(f"🔄 обробка upd {uid}...", flush=True)
            tg_bot.process_new_updates([upd])
            print(f"✅ upd {uid} оброблено", flush=True)
        except Exception as e:
            import traceback
            print(f"❌ upd {uid} помилка: {e}", flush=True)
            traceback.print_exc()

    threading.Thread(target=_process, args=(update,), daemon=True).start()
    return "ok", 200

# Webhook
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
            tg_bot.set_webhook(url=_wh)
        print(f"✅ Webhook: {_wh}", flush=True)
    except Exception as e:
        print(f"⚠️ Webhook: {e}", flush=True)

if __name__ == "__main__":
    flask_app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
