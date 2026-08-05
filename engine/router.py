"""handlers/router.py — Ініціалізація bot і реєстрація всіх хендлерів."""
import telebot
import anthropic
from config.settings import TELEGRAM_TOKEN, ANTHROPIC_KEY

# ─── Спільний стан ────────────────────────────────────────────────────────────
state: dict = {
    'user_batches':  {},   # chat_id → {'items': [], 'timer': Timer}
    'stop_flags':    {},   # chat_id → True
    '_order_setup':  {},   # chat_id → setup dict
    '_pre_batch':    {},   # chat_id → pre-batch dict
    '_train_state':  {},   # chat_id → training state
    '_learn_state':  {},   # chat_id → learning state
    '_manual_wait':  {},   # chat_id → waiting state
    'pending_hints': {},   # chat_id → hint text
    '_pending_text': {},   # chat_id → {text, ts}
    'last_results':  {},   # chat_id → results
}

# ─── Ініціалізація ────────────────────────────────────────────────────────────
_ExcBase = getattr(telebot, 'ExceptionHandler', object)

class _LogExc(_ExcBase):
    def handle(self, exception):
        import traceback
        print(f"❌ ПОМИЛКА В ХЕНДЛЕРІ: {exception}", flush=True)
        traceback.print_exc()
        return True


try:
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False, exception_handler=_LogExc())
except TypeError:
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def register_all():
    """Реєструє всі хендлери бота."""
    from handlers import (commands, admin_handler, photo_handler,
                          text_handler, client_handler, callback_handler,
                          training_handler, rules_handler)

    commands.register(bot, state)
    admin_handler.register(bot, state)
    photo_handler.register(bot, state)
    text_handler.register(bot, state)
    client_handler.register(bot, state)
    callback_handler.register(bot, state)
    training_handler.register(bot, state)
    rules_handler.register(bot, state)

    # Додаємо load_pending_fixes в state для admin_handler
    from services.fix_service import load_pending_fixes
    state['load_pending_fixes'] = load_pending_fixes

    print(f"✅ bot.py OK, зареєстровано handlers: {len(bot.message_handlers)}",
          flush=True)
    return bot


def safe_edit(chat_id: int, msg_id: int, text: str,
              parse_mode: str = None, reply_markup=None) -> None:
    """Редагує повідомлення — ковтає 'message is not modified' і інші безпечні помилки."""
    try:
        kwargs = {}
        if parse_mode:   kwargs['parse_mode']   = parse_mode
        if reply_markup: kwargs['reply_markup'] = reply_markup
        bot.edit_message_text(text, chat_id, msg_id, **kwargs)
    except Exception as e:
        err = str(e)
        if any(x in err for x in ('message is not modified', 'message to edit not found',
                                   'MESSAGE_ID_INVALID', 'Bad Request: message can\'t be edited')):
            pass
        else:
            print(f"⚠️ safe_edit: {e}", flush=True)


def safe_edit(chat_id: int, msg_id: int, text: str,
              parse_mode: str = None, reply_markup=None) -> None:
    """Редагує повідомлення — ковтає 'message is not modified' і інші безпечні помилки."""
    try:
        kwargs = {}
        if parse_mode:   kwargs['parse_mode']   = parse_mode
        if reply_markup: kwargs['reply_markup'] = reply_markup
        bot.edit_message_text(text, chat_id, msg_id, **kwargs)
    except Exception as e:
        err = str(e)
        if any(x in err for x in ('message is not modified', 'message to edit not found',
                                   'MESSAGE_ID_INVALID')):
            pass
        else:
            print(f"⚠️ safe_edit: {e}", flush=True)
