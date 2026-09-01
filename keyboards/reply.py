"""keyboards/reply.py — ReplyKeyboardMarkup (головне меню)."""
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from config.settings import ADMIN_ID


def main_keyboard(uid: int) -> ReplyKeyboardMarkup:
    """Будує головну клавіатуру. Адміни бачать додаткові кнопки."""
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add(
        KeyboardButton("📸 Як користуватись"),
        KeyboardButton("📊 Кеш"),
        KeyboardButton("👥 Клієнти"),
    )
    kb.add(
        KeyboardButton("👥 Кеш клієнта"),
        KeyboardButton("📚 Навчання"),
        KeyboardButton("🛑 Стоп"),
    )
    kb.add(
        KeyboardButton("📋 Правило"),
        KeyboardButton("🌐 Навчання бота"),
    )
    if uid == ADMIN_ID:
        kb.add(
            KeyboardButton("👑 Статистика"),
            KeyboardButton("👑 Логи"),
            KeyboardButton("👑 Діри каталогу"),
        )
        kb.add(
            KeyboardButton("👑 Правила на розгляд"),
            KeyboardButton("👑 Перевір кеш"),
            KeyboardButton("📖 Словник"),
        )
    return kb
