"""keyboards/inline.py — InlineKeyboardMarkup загальні кнопки."""
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


def training_keyboard() -> InlineKeyboardMarkup:
    """Кнопки після результату пошуку."""
    mk = InlineKeyboardMarkup()
    mk.add(
        InlineKeyboardButton("🎓 Навчання", callback_data="tr_go"),
        InlineKeyboardButton("✖️ Закрити",  callback_data="tr_close"),
    )
    return mk


def cache_actions_keyboard() -> InlineKeyboardMarkup:
    """Кнопки керування кешем."""
    mk = InlineKeyboardMarkup(row_width=2)
    mk.add(
        InlineKeyboardButton("🗑 Очистити прострочені", callback_data="cache_clean_expired"),
        InlineKeyboardButton("🗑 Очистити авто",        callback_data="cache_clean_auto"),
    )
    return mk


def gaps_excel_keyboard() -> InlineKeyboardMarkup:
    mk = InlineKeyboardMarkup()
    mk.add(InlineKeyboardButton("📥 Завантажити Excel", callback_data="gaps_excel"))
    return mk


def knowledge_decision_keyboard() -> InlineKeyboardMarkup:
    mk = InlineKeyboardMarkup()
    mk.add(
        InlineKeyboardButton("✅ Додати правило", callback_data="knok"),
        InlineKeyboardButton("❌ Пропустити",     callback_data="knno"),
    )
    return mk


def order_setup_keyboard(active_name: str | None = None) -> InlineKeyboardMarkup:
    """Кнопки вибору клієнта для замовлення."""
    mk = InlineKeyboardMarkup(row_width=1)
    if active_name:
        mk.add(InlineKeyboardButton(
            f"✅ {active_name} (активний)", callback_data="osetup_active"))
    mk.add(
        InlineKeyboardButton("👤 Без клієнта",     callback_data="osetup_none"),
        InlineKeyboardButton("🔍 Вибрати клієнта", callback_data="osetup_pick"),
    )
    return mk


def hint_skip_keyboard() -> InlineKeyboardMarkup:
    mk = InlineKeyboardMarkup()
    mk.add(InlineKeyboardButton("⏩ Пропустити підказку", callback_data="osetup_skip"))
    return mk


def text_choice_keyboard() -> InlineKeyboardMarkup:
    mk = InlineKeyboardMarkup(row_width=2)
    mk.add(
        InlineKeyboardButton("🔍 Підібрати зараз", callback_data="txts"),
        InlineKeyboardButton("💡 Підказка до фото", callback_data="txth"),
    )
    return mk


def pc_batch_keyboard(batch_id: str) -> InlineKeyboardMarkup:
    mk = InlineKeyboardMarkup(row_width=3)
    mk.add(
        InlineKeyboardButton("✅ Всі",    callback_data=f"pc_all:{batch_id}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"pc_rej:{batch_id}"),
        InlineKeyboardButton("👀 Вибрати", callback_data=f"pc_pick:{batch_id}"),
    )
    mk.add(
        InlineKeyboardButton("🗑 Очистити все", callback_data="pc_clear_all"),
        InlineKeyboardButton("➡️ Далі",         callback_data="pc_next"),
    )
    return mk
