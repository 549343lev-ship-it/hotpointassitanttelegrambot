"""engine/bot.py — Ініціалізація бота і точка входу.

Реєструє всі handlers через окремі модулі.
Вся логіка — в handlers/, services/, keyboards/.
"""
import os
from catalog import storage
storage.restore()

from handlers.router import bot, state, register_all

register_all()

try:
    storage.start_autosave(60)
except Exception as e:
    print(f"⚠️ storage autosave: {e}", flush=True)

print("🤖 bot.py завантажено, хендлери зареєстровані", flush=True)
