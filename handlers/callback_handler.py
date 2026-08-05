"""handlers/callback_handler.py — Загальний диспетчер callback_query."""


def register(bot, state: dict):
    from clients import clients

    # ── brand_selector callbacks ───────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith('bs_'))
    def cb_brand_selector(call):
        from engine.brand_selector import handle_callback as bs_handle
        if bs_handle(call.message.chat.id, call.data, bot):
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "⏱ Сесія закінчилась")

    # ── osetup: вибір клієнта і підказки ─────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith('osetup_'))
    def cb_order_setup(call):
        from handlers.photo_handler import _add_to_batch
        chat_id = call.message.chat.id
        data    = call.data
        setup   = state.get('_order_setup', {}).get(chat_id)

        if not setup:
            bot.answer_callback_query(call.id, "⏱ Сесія закінчилась"); return
        bot.answer_callback_query(call.id)

        if data == 'osetup_active':
            slug = clients.get_active(chat_id)
        elif data == 'osetup_none':
            slug = None
        elif data == 'osetup_pick':
            profiles = clients.list_profiles()
            if not profiles:
                bot.edit_message_text(
                    "❓ Немає клієнтів. Спочатку створи: `новий клієнт Ім'я`",
                    chat_id, call.message.message_id, parse_mode="Markdown"); return
            from keyboards.client_keyboards import client_list_keyboard
            bot.edit_message_text(
                "Оберіть клієнта:", chat_id, call.message.message_id,
                reply_markup=client_list_keyboard(profiles)); return
        else:
            slug = None

        if slug:
            clients.set_active(chat_id, slug)

        _finish_order_setup(call.message, setup, slug, bot, state)

    @bot.callback_query_handler(func=lambda c: c.data == 'osetup_skip')
    def cb_order_setup_skip(call):
        from handlers.photo_handler import _add_to_batch
        chat_id = call.message.chat.id
        setup   = state.get('_order_setup', {}).pop(chat_id, {})
        items   = setup.get('items') or ([setup['item']] if setup.get('item') else [])
        if not items:
            bot.answer_callback_query(call.id, "⏱ Сесія закінчилась"); return
        slug = setup.get('slug')
        if slug:
            clients.set_active(chat_id, slug)
        bot.answer_callback_query(call.id)
        p     = clients.get_profile(slug) if slug else None
        label = p['name'] if p else "без клієнта"
        bot.edit_message_text(
            f"✅ {label} | без підказки\n⏳ Обробляю...",
            chat_id, call.message.message_id)
        for it in items:
            _add_to_batch(chat_id, it, state, bot)


def _finish_order_setup(msg, setup: dict, slug, bot, state: dict):
    """Завершує налаштування — питає підказку або одразу запускає батч."""
    from handlers.photo_handler import _add_to_batch
    from clients import clients
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    chat_id      = msg.chat.id
    items        = setup.get('items') or ([setup['item']] if setup.get('item') else [])
    hint_already = setup.get('hint_already', False)
    state.get('_order_setup', {}).pop(chat_id, None)

    if slug:
        p    = clients.get_profile(slug)
        name = p['name'] if p else slug
    else:
        name = None

    def _add_all(hint=''):
        for it in items:
            cap = it.get('caption', '')
            if hint and hint not in cap:
                it['caption'] = ' | '.join(filter(None, [cap, hint]))
            _add_to_batch(chat_id, it, state, bot)

    if hint_already:
        hint  = state.get('pending_hints', {}).pop(chat_id, '')
        label = f"👤 {name}" if name else "без клієнта"
        n     = len(items)
        bot.edit_message_text(
            f"✅ Прийнято ({label}, {n} фото)\n⏳ Обробляю...",
            chat_id, msg.message_id)
        _add_all(hint)
    else:
        label = f"👤 *{name}*" if name else "без клієнта"
        mk    = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("⏩ Пропустити підказку", callback_data="osetup_skip"))
        state.setdefault('_order_setup', {})[chat_id] = {
            'items': items, 'slug': slug, 'waiting_hint': True}
        bot.edit_message_text(
            f"✅ Клієнт: {label}\n\n"
            f"💬 *Введи підказку виробника* (напр. _пайка екопластик_)\n"
            f"або натисни Пропустити:",
            chat_id, msg.message_id,
            parse_mode="Markdown", reply_markup=mk)
