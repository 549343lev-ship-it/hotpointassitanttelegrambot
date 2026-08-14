"""handlers/client_handler.py — 'клієнт', 'новий клієнт', вибір клієнта."""
import re
from config.settings import ADMIN_ID
from keyboards.client_keyboards import client_list_keyboard, cache_item_keyboard


def register(bot, state: dict):
    from clients import clients

    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ('клієнти', '👥 клієнти'))
    def handle_clients_list(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        index = clients.list_clients()
        mk = InlineKeyboardMarkup(row_width=1)
        mk.add(InlineKeyboardButton('➕ Створити нового клієнта', callback_data='cl_create_new'))
        if not index:
            bot.reply_to(message, '👥 Клієнтів ще немає.', reply_markup=mk); return
        active_slug = clients.get_active(message.chat.id)
        lines = ['👥 *Клієнти:*']
        for slug, name in sorted(index.items(), key=lambda x: x[1]):
            p = clients.get_profile(slug)
            orders = p.get('orders_count', 0) if p else 0
            active = ' ✅' if active_slug == slug else ''
            lines.append(f'• {name} ({orders} зам.){active}')
            mk.add(InlineKeyboardButton(f'👤 {name}', callback_data=f'cl_use_{slug}'))
        bot.reply_to(message, '\n'.join(lines), parse_mode='Markdown', reply_markup=mk)

    @bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('новий клієнт'))
    def handle_new_client(message):
        name = message.text[12:].strip()
        if not name:
            bot.reply_to(message, "Вкажи ім'я: `новий клієнт Петренко`",
                         parse_mode="Markdown"); return
        _create_client(message.chat.id, name, bot)

    @bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('клієнт'))
    def handle_client(message):
        rest = message.text[6:].strip()
        if not rest or rest.lower() in ('стоп', 'скинути', 'off'):
            clients.set_active(message.chat.id, None)
            bot.reply_to(message, "👤 Клієнта скинуто."); return

        profiles = clients.list_profiles()
        matches  = [p for p in profiles
                    if rest.lower() in p['name'].lower()
                    or rest.lower() == p['slug']]

        if not matches:
            bot.reply_to(message, f"❓ Клієнта '{rest}' не знайдено.\n"
                                   f"`новий клієнт {rest}` — щоб створити.",
                         parse_mode="Markdown"); return

        if len(matches) == 1:
            _activate_client(message.chat.id, matches[0]['slug'], message, bot)
        else:
            mk = client_list_keyboard(matches)
            bot.reply_to(message, "Оберіть клієнта:", reply_markup=mk)

    @bot.callback_query_handler(func=lambda c: c.data == 'cl_create_new')
    def cb_create_new(call):
        bot.answer_callback_query(call.id)
        state.setdefault('_manual_wait', {})[call.message.chat.id] = {'mode': 'new_client'}
        bot.send_message(call.message.chat.id, "👤 Введи ім'я нового клієнта:")

    @bot.callback_query_handler(func=lambda c: c.data.startswith('cl_use_'))
    def cb_client_use_inline(call):
        slug = call.data[7:]
        bot.answer_callback_query(call.id)
        _activate_client(call.message.chat.id, slug, None, bot)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('sel_client_'))
    def cb_select_client(call):
        slug = call.data[11:]
        bot.answer_callback_query(call.id)
        _activate_client(call.message.chat.id, slug, None, bot)

    # ── Кеш клієнта ───────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text == "👥 Кеш клієнта")
    def kb_client_cache(message):
        slug = clients.get_active(message.chat.id)
        if not slug:
            bot.reply_to(message, "❓ Активний клієнт не вибраний."); return
        _show_client_cache(message.chat.id, slug, 0, bot)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('ccp_'))
    def cb_client_cache_page(call):
        bot.answer_callback_query(call.id)
        parts = call.data.split('_')
        if len(parts) < 4:
            return
        direction = parts[1]  # prev / next
        slug      = parts[2]
        page      = int(parts[3])
        new_page  = page - 1 if direction == 'prev' else page + 1
        _show_client_cache(call.message.chat.id, slug, new_page, bot)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('cck_'))
    def cb_client_cache_action(call):
        from clients.cache import cache_set_status
        bot.answer_callback_query(call.id)
        parts = call.data.split('_')
        action = parts[1]  # ok / no / all
        idx    = int(parts[2]) if len(parts) > 2 else 0
        slug   = clients.get_active(call.message.chat.id)
        if not slug:
            return
        cache_items = clients.get_client_cache(slug)
        keys        = list(cache_items.keys())
        if idx >= len(keys):
            return
        key  = keys[idx]
        data = cache_items[key]
        if action == 'ok':
            clients.client_cache_set_status(slug, key, data.get('name', ''), 'confirmed')
            bot.edit_message_text(f"✅ Підтверджено: {key}",
                                  call.message.chat.id, call.message.message_id)
        elif action == 'no':
            clients.client_cache_set_status(slug, key, data.get('name', ''), 'banned')
            bot.edit_message_text(f"🚫 Заборонено: {key}",
                                  call.message.chat.id, call.message.message_id)
        elif action == 'all':
            n = 0
            for k, d in cache_items.items():
                if d.get('status') == 'auto':
                    clients.client_cache_set_status(slug, k, d.get('name',''), 'confirmed')
                    n += 1
            bot.edit_message_text(f"✅ Підтверджено {n} авто-записів.",
                                  call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('ccl__'))
    def cb_cache_clear(call):
        bot.answer_callback_query(call.id)
        parts = call.data[5:].split('__')
        if len(parts) < 2:
            return
        slug, mode = parts[0], parts[1]
        uid = call.from_user.id
        if uid != ADMIN_ID and clients.get_active(call.message.chat.id) != slug:
            bot.answer_callback_query(call.id, "⛔ Немає прав"); return
        from keyboards.client_keyboards import confirm_clear_keyboard
        bot.edit_message_text(
            f"❓ Видалити кеш клієнта ({mode})?",
            call.message.chat.id, call.message.message_id,
            reply_markup=confirm_clear_keyboard(slug, mode))

    @bot.callback_query_handler(func=lambda c: c.data.startswith('ccl_confirm__'))
    def cb_cache_clear_confirm(call):
        bot.answer_callback_query(call.id)
        parts = call.data[13:].split('__')
        if len(parts) < 2:
            return
        slug, mode = parts[0], parts[1]
        n = clients.clear_client_cache(slug, mode)
        bot.edit_message_text(f"🗑 Видалено {n} записів.",
                              call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == 'ccl_cancel')
    def cb_cache_clear_cancel(call):
        bot.answer_callback_query(call.id)
        bot.edit_message_text("Скасовано.",
                              call.message.chat.id, call.message.message_id)


def _activate_client(chat_id: int, slug: str, message, bot):
    from clients import clients
    clients.set_active(chat_id, slug)
    p    = clients.get_profile(slug)
    name = p['name'] if p else slug
    orders = clients.get_order_count(slug)
    text   = (f"✅ Активний клієнт: *{name}*\n"
              f"📦 Замовлень: {orders}\n"
              f"Тепер кинь фото замовлення.")
    if message:
        bot.reply_to(message, text, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, parse_mode="Markdown")


def _create_client(chat_id: int, name: str, bot):
    from clients import clients
    slug = re.sub(r'\s+', '_', name.strip().lower())
    if clients.get_profile(slug):
        bot.send_message(chat_id, f"❓ Клієнт '{name}' вже існує.")
        _activate_client(chat_id, slug, None, bot)
        return
    clients.create_profile(slug, name)
    _activate_client(chat_id, slug, None, bot)


def _show_client_cache(chat_id: int, slug: str, page: int, bot):
    from clients import clients
    from keyboards.client_keyboards import (cache_item_keyboard,
                                            cache_clear_keyboard,
                                            client_cache_page_keyboard)
    from config.settings import PAGE_SIZE

    cache_items = clients.get_client_cache(slug)
    if not cache_items:
        bot.send_message(chat_id, "📭 Кеш клієнта порожній."); return

    keys       = list(cache_items.keys())
    total_pages = max(1, (len(keys) + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    start       = page * PAGE_SIZE
    chunk       = keys[start:start + PAGE_SIZE]

    for i, key in enumerate(chunk):
        data   = cache_items[key]
        status = data.get('status', 'auto')
        icon   = {'confirmed': '✅', 'banned': '🚫', 'auto': '🤖'}.get(status, '🤖')
        name   = data.get('name', '')
        conf   = data.get('confidence', 0)
        text   = f"{icon} `{key[:35]}` → *{name[:45]}* ({conf}%)"
        idx    = start + i
        bot.send_message(chat_id, text, parse_mode="Markdown",
                         reply_markup=cache_item_keyboard(slug, key, idx))

    nav_mk = client_cache_page_keyboard(slug, page, total_pages)
    clear_mk = cache_clear_keyboard(slug)
    bot.send_message(chat_id, f"Сторінка {page+1}/{total_pages}",
                     reply_markup=nav_mk)
    bot.send_message(chat_id, "🗑 Очистити кеш:", reply_markup=clear_mk)
