"""
handlers/router.py — Збирає всі хендлери і повертає сконфігурований bot.

Використовується з engine/app.py:
    from handlers.router import register_all
    tg_bot = register_all()
"""
import os
import telebot
from config.settings import TELEGRAM_TOKEN


def register_all() -> telebot.TeleBot:
    """Створює bot, спільний state і реєструє всі хендлери."""

    class _LogExc(telebot.ExceptionHandler):
        def handle(self, exc):
            import traceback
            traceback.print_exc()
            return True

    try:
        bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False,
                              exception_handler=_LogExc())
    except TypeError:
        bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

    # Спільний state — передається в кожен хендлер
    state: dict = {}

    # Реєстрація хендлерів (порядок важливий — специфічніші вище)
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
