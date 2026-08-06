"""handlers/callback_handler.py — Загальний диспетчер callback_query."""


def _safe_edit(bot, chat_id, msg_id, text, parse_mode=None, reply_markup=None):
    """edit_message_text що ковтає 'message is not modified'."""
    try:
        kwargs = {}
        if parse_mode:   kwargs['parse_mode']   = parse_mode
        if reply_markup: kwargs['reply_markup'] = reply_markup
        bot.edit_message_text(text, chat_id, msg_id, **kwargs)
    except Exception as e:
        if 'message is not modified' not in str(e) and 'message to edit not found' not in str(e):
            print(f"⚠️ safe_edit: {e}", flush=True)


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
    @bot.callback_query_handler(func=lambda c: c.data.startswith('osetup_')
                                               and c.data != 'osetup_skip')
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
            index = clients.list_clients()
            if not index:
                _safe_edit(bot, chat_id, call.message.message_id,
                    "❓ Клієнтів немає. Спочатку: `новий клієнт Ім'я`",
                    parse_mode="Markdown"); return
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            mk2 = InlineKeyboardMarkup(row_width=1)
            for s, cname in sorted(index.items(), key=lambda x: x[1])[:12]:
                mk2.add(InlineKeyboardButton(f"👤 {cname}", callback_data=f"osetup_cl_{s}"))
            mk2.add(InlineKeyboardButton("❌ Без клієнта", callback_data="osetup_none"))
            _safe_edit(bot, chat_id, call.message.message_id,
                       "👤 Обери клієнта:", reply_markup=mk2); return
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
        _safe_edit(bot, chat_id, call.message.message_id,
                   f"✅ {label} | без підказки\n⏳ Обробляю...")
        for it in items:
            _add_to_batch(chat_id, it, state, bot)


def _finish_order_setup(msg, setup: dict, slug, bot, state: dict):
    """Завершує налаштування — якщо підказка є, одразу запускає батч."""
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
        # Підказка вже є (caption або pending_hints) — одразу обробляємо
        hint  = state.get('pending_hints', {}).pop(chat_id, '')
        label = f"👤 {name}" if name else "без клієнта"
        n     = len(items)
        _safe_edit(bot, chat_id, msg.message_id,
                   f"✅ Прийнято ({label}, {n} фото)\n⏳ Обробляю...")
        _add_all(hint)
    else:
        # Немає підказки — питаємо
        label = f"👤 *{name}*" if name else "без клієнта"
        mk    = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("⏩ Пропустити підказку", callback_data="osetup_skip"))
        state.setdefault('_order_setup', {})[chat_id] = {
            'items': items, 'slug': slug, 'waiting_hint': True}
        _safe_edit(bot, chat_id, msg.message_id,
                   f"✅ Клієнт: {label}\n\n"
                   f"💬 *Введи підказку виробника* (напр. _пайка екопластик_)\n"
                   f"або натисни Пропустити:",
                   parse_mode="Markdown", reply_markup=mk)
