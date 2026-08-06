"""
handlers/router.py — Збирає всі хендлери, повертає bot.

Експортує на рівні модуля:
    bot          — telebot.TeleBot instance
    state        — спільний dict стану
    register_all — функція що реєструє хендлери і повертає bot
"""
import os
import telebot
from config.settings import TELEGRAM_TOKEN


# ── Bot та state — модульні змінні (для `from handlers.router import bot, state`) ──

class _LogExc(telebot.ExceptionHandler):
    def handle(self, exc):
        import traceback
        traceback.print_exc()
        return True

try:
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False, exception_handler=_LogExc())
except TypeError:
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

# Спільний dict стану — передається в кожен хендлер
state: dict = {}


def register_all() -> telebot.TeleBot:
    """Реєструє всі хендлери і повертає bot."""
    from handlers import (
        commands,
        admin_handler,
        rules_handler,
        training_handler,
        client_handler,
        photo_handler,
        text_handler,
        callback_handler,
    )

    commands.register(bot, state)
    admin_handler.register(bot, state)
    rules_handler.register(bot, state)
    training_handler.register(bot, state)
    client_handler.register(bot, state)
    photo_handler.register(bot, state)
    text_handler.register(bot, state)
    callback_handler.register(bot, state)

    print(
        f"✅ handlers.router: {len(bot.message_handlers)} msg handlers, "
        f"{len(bot.callback_query_handlers)} callback handlers",
        flush=True,
    )
    return bot
