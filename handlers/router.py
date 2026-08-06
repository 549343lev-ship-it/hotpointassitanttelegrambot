"""
handlers/router.py
"""
import telebot
from config.settings import TELEGRAM_TOKEN


class _LogExc(telebot.ExceptionHandler):
    def handle(self, exc):
        import traceback
        traceback.print_exc()
        return True

try:
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False, exception_handler=_LogExc())
except TypeError:
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

state: dict = {}


def register_all() -> telebot.TeleBot:
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
    print(f"✅ handlers.router: {len(bot.message_handlers)} msg, "
          f"{len(bot.callback_query_handlers)} cb", flush=True)
    return bot
