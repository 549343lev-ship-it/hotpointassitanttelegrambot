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
        active_slug = clients.get_active(message.chat.id)
        mk = InlineKeyboardMarkup(row_width=1)
        mk.add(InlineKeyboardButton('➕ Створити нового клієнта', callback_data='cl_create_new'))
        if not index:
            bot.reply_to(message, '👥 Клієнтів ще немає.', reply_markup=mk); return
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

        index = clients.list_clients()
        matches = [(s, n) for s, n in index.items()
                   if rest.lower() in n.lower() or rest.lower() == s]
        if not matches:
            bot.reply_to(message, f"❓ Клієнта '{rest}' не знайдено.\n"
                                   f"`новий клієнт {rest}` — щоб створити.",
                         parse_mode="Markdown"); return
        if len(matches) == 1:
            _activate_client(message.chat.id, matches[0][0], message, bot)
        else:
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            mk = InlineKeyboardMarkup(row_width=1)
            for s, n in matches:
                mk.add(InlineKeyboardButton(f'👤 {n}', callback_data=f'cl_use_{s}'))
            bot.reply_to(message, 'Оберіть клієнта:', reply_markup=mk)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('sel_client_'))
    def cb_select_client(call):
        slug = call.data[11:]
        bot.answer_callback_query(call.id)
        _activate_client(call.message.chat.id, slug, None, bot)

    # ── Кеш клієнта ───────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ('кеш клієнта', '👥 кеш клієнта'))
    def kb_client_cache(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        index = clients.list_clients()
        if not index:
            bot.reply_to(message, '❓ Клієнтів немає.'); return
        active_slug = clients.get_active(message.chat.id)
        mk = InlineKeyboardMarkup(row_width=1)
        for slug, name in sorted(index.items(), key=lambda x: x[1]):
            mark = ' ✅' if slug == active_slug else ''
            mk.add(InlineKeyboardButton(f'👤 {name}{mark}', callback_data=f'csh_show_{slug}'))
        bot.reply_to(message, '👤 Обери клієнта для перегляду кешу:', reply_markup=mk)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('csh_show_'))
    def cb_cache_show_client(call):
        slug = call.data[9:]
        bot.answer_callback_query(call.id)
        clients.set_active(call.message.chat.id, slug)
        p = clients.get_profile(slug)
        name = p['name'] if p else slug
        try:
            bot.edit_message_text(f'👤 *{name}* — кеш:', call.message.chat.id,
                                  call.message.message_id, parse_mode='Markdown')
        except Exception:
            pass
        _show_client_cache(call.message.chat.id, slug, 0, bot)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('cl_use_'))
    def cb_client_use(call):
        slug = call.data[7:]
        bot.answer_callback_query(call.id)
        _activate_client(call.message.chat.id, slug, None, bot)

    @bot.callback_query_handler(func=lambda c: c.data == 'cl_create_new')
    def cb_create_new(call):
        bot.answer_callback_query(call.id)
        state.setdefault('_manual_wait', {})[call.message.chat.id] = {'mode': 'new_client'}
        bot.send_message(call.message.chat.id, "👤 Введи ім'я нового клієнта:")

    @bot.callback_query_handler(func=lambda c: c.data.startswith('ccp|'))
    def cb_client_cache_page(call):
        bot.answer_callback_query(call.id)
        parts = call.data.split('|')
        if len(parts) < 4 or parts[1] == 'noop':
            return
        direction = parts[1]
        slug      = parts[2]
        page      = int(parts[3])
        new_page  = page - 1 if direction == 'prev' else page + 1
        _show_client_cache(call.message.chat.id, slug, new_page, bot)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('cck|'))
    def cb_client_cache_action(call):
        bot.answer_callback_query(call.id)
        # формат: cck|ok|slug|key  або  cck|all|slug
        parts  = call.data.split('|', 3)
        action = parts[1]   # ok / no / all

        if action == 'all':
            slug = parts[2]
            cache_items = clients.get_client_cache(slug)
            n = 0
            for k, d in cache_items.items():
                if d.get('status') == 'auto':
                    clients.client_cache_set_status(slug, k, d.get('catalog_name', d.get('name', '')), 'confirmed')
                    n += 1
            bot.edit_message_text(f"✅ Підтверджено {n} авто-записів.",
                                  call.message.chat.id, call.message.message_id)
            return

        # ok / no: parts[2]=slug, parts[3]=key
        slug = parts[2]
        key  = parts[3] if len(parts) > 3 else ''
        if not slug or not key:
            return
        cache_items = clients.get_client_cache(slug)
        # Шукаємо запис по початку ключа (key обрізаний до 60 символів)
        real_key = next((k for k in cache_items if k.startswith(key) or k == key), None)
        if not real_key:
            bot.edit_message_text("⚠️ Запис не знайдено (можливо вже оброблений).",
                                  call.message.chat.id, call.message.message_id); return
        data = cache_items[real_key]
        catalog_name = data.get('catalog_name', data.get('name', ''))
        if action == 'ok':
            clients.client_cache_set_status(slug, real_key, catalog_name, 'confirmed')
            bot.edit_message_text(f"✅ Підтверджено:\n`{real_key[:50]}`",
                                  call.message.chat.id, call.message.message_id,
                                  parse_mode='Markdown')
        elif action == 'no':
            clients.client_cache_set_status(slug, real_key, catalog_name, 'banned')
            bot.edit_message_text(f"🚫 Заблоковано:\n`{real_key[:50]}`",
                                  call.message.chat.id, call.message.message_id,
                                  parse_mode='Markdown')

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
    orders = p.get('orders_count', 0) if p else 0
    text   = (f"✅ Активний клієнт: *{name}*\n"
              f"📦 Замовлень: {orders}\n"
              f"Тепер кинь фото замовлення.")
    if message:
        bot.reply_to(message, text, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, parse_mode="Markdown")


def _create_client(chat_id: int, name: str, bot):
    from clients import clients
    ok, result = clients.create_client(name)
    if not ok and result.startswith('existing:'):
        slug = result[9:]
        bot.send_message(chat_id, f"❓ Клієнт '{name}' вже існує.")
        _activate_client(chat_id, slug, None, bot)
        return
    if not ok:
        bot.send_message(chat_id, f'⚠️ {result}'); return
    _activate_client(chat_id, result, None, bot)


def _show_client_cache(chat_id: int, slug: str, page: int, bot):
    from clients import clients
    from keyboards.client_keyboards import (cache_item_keyboard,
                                            cache_clear_keyboard,
                                            client_cache_page_keyboard)
    from config.settings import PAGE_SIZE

    cache_items = clients.get_client_cache(slug)
    if not cache_items:
        bot.send_message(chat_id, '📭 Кеш клієнта порожній.'); return

    keys = [k for k, v in cache_items.items() if v.get('status', 'auto') == 'auto']
    if not keys:
        total     = len(cache_items)
        confirmed = sum(1 for v in cache_items.values() if v.get('status') == 'confirmed')
        banned    = sum(1 for v in cache_items.values() if v.get('status') == 'banned')
        bot.send_message(chat_id,
            f'✅ Всі записи оброблені ({total} всього: {confirmed} підтверджено, {banned} заблоковано).')
        return
    total_pages = max(1, (len(keys) + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    start       = page * PAGE_SIZE
    chunk       = keys[start:start + PAGE_SIZE]

    for i, key in enumerate(chunk):
        data   = cache_items[key]
        status = data.get('status', 'auto')
        icon   = {'confirmed': '✅', 'banned': '🚫', 'auto': '🤖'}.get(status, '🤖')
        name   = data.get('catalog_name', '') or data.get('name', '')
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
