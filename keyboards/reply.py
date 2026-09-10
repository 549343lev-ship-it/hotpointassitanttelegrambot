"""keyboards/reply.py — ReplyKeyboardMarkup (головне меню)."""
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from config.settings import ADMIN_ID


def main_keyboard(uid: int) -> ReplyKeyboardMarkup:
    """Будує головну клавіатуру. Адміни бачать додаткові кнопки."""
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add(
        KeyboardButton("📊 Кеш"),
        KeyboardButton("👥 Клієнти"),
        KeyboardButton("👥 Кеш клієнта"),
    )
    kb.add(
        KeyboardButton("📚 Навчання"),
        KeyboardButton("🛑 Стоп"),
    )
    if uid == ADMIN_ID:
        kb.add(
            KeyboardButton("👑 Статистика"),
            KeyboardButton("👑 Логи"),
            KeyboardButton("👑 Діри каталогу"),
        )
        kb.add(
            KeyboardButton("👑 Перевір кеш"),
        )
        kb.add(
            KeyboardButton("🔄 Схожі: rebuild"),
            KeyboardButton("🧩 Схожі: аналоги"),
            KeyboardButton("📤 Схожі: export"),
        )
        kb.add(
            KeyboardButton("🏗 Схожі: build"),
            KeyboardButton("🗑 Схожі: reset"),
            KeyboardButton("🔄 Схожі: повний rebuild"),
        )
    return kb
