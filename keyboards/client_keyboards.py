"""keyboards/client_keyboards.py — Inline-кнопки для клієнтів і їх кешу."""
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton


def client_list_keyboard(profiles: list[dict]) -> InlineKeyboardMarkup:
    """Список клієнтів для вибору (до 10 кнопок)."""
    mk = InlineKeyboardMarkup(row_width=1)
    for p in profiles[:10]:
        slug = p['slug']
        name = p.get('name', slug)
        mk.add(InlineKeyboardButton(f"👤 {name}", callback_data=f"sel_client_{slug}"))
    return mk


def client_cache_page_keyboard(slug: str, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Навігація по сторінках кешу клієнта."""
    mk = InlineKeyboardMarkup(row_width=3)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"ccp|prev|{slug}|{page}"))
    nav.append(InlineKeyboardButton(f"{page+1}/{total_pages}", callback_data="ccp|noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"ccp|next|{slug}|{page}"))
    if nav:
        mk.add(*nav)
    return mk


def cache_item_keyboard(slug: str, key: str, idx: int) -> InlineKeyboardMarkup:
    """Кнопки для одного запису кешу: підтвердити / заборонити."""
    safe_key = key[:40].replace(':', '_')
    mk = InlineKeyboardMarkup(row_width=2)
    mk.add(
        InlineKeyboardButton("✅ OK",  callback_data=f"cck_ok_{idx}"),
        InlineKeyboardButton("❌ Бан", callback_data=f"cck_no_{idx}"),
    )
    mk.add(InlineKeyboardButton("✅ Всі авто → confirmed",
                                callback_data=f"cck_all_{idx}"))
    return mk


def cache_clear_keyboard(slug: str) -> InlineKeyboardMarkup:
    """Кнопки очищення кешу клієнта."""
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(
        InlineKeyboardButton("🗑 Очистити авто",      callback_data=f"ccl__{slug}__auto"),
        InlineKeyboardButton("🗑 Очистити confirmed",  callback_data=f"ccl__{slug}__confirmed"),
        InlineKeyboardButton("🗑 Очистити все",        callback_data=f"ccl__{slug}__all"),
    )
    return mk


def confirm_clear_keyboard(slug: str, mode: str) -> InlineKeyboardMarkup:
    """Підтвердження очищення кешу."""
    mk = InlineKeyboardMarkup(row_width=2)
    mk.add(
        InlineKeyboardButton("✅ Так, видалити", callback_data=f"ccl_confirm__{slug}__{mode}"),
        InlineKeyboardButton("❌ Скасувати",     callback_data="ccl_cancel"),
    )
    return mk
