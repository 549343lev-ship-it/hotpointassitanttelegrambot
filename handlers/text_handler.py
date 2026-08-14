"""handlers/text_handler.py — Прийом тексту: підказки виробника, 'пошук ...', довільний текст."""
import time
import threading
from config.settings import HINT_TTL, BATCH_TIMEOUT
from keyboards.inline import text_choice_keyboard


def register(bot, state: dict):

    @bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('пошук'))
    def handle_text_search(message):
        query = message.text[6:].strip()
        if not query:
            bot.reply_to(message, "Напиши: `пошук назва товару`", parse_mode="Markdown")
            return
        from engine.ocr import normalize_text
        позиції = normalize_text(query, '')
        if not позиції:
            bot.reply_to(message, "😕 Не розпізнано."); return
        from services.process_service import _run_search
        status = bot.send_message(message.chat.id, "🔍 Шукаю...")
        _run_search(
            chat_id=message.chat.id,
            всі_позиції=позиції,
            items=[],
            caption='',
            chosen_brand_map={},
            msg_id=status.message_id,
            bot=bot,
            _state=state,
        )

    @bot.message_handler(func=lambda m: m.text and m.chat.id in state.get('_manual_wait', {}))
    def handle_manual_input(message):
        """Ловить текст коли чекає ручного вводу."""
        mw    = state.setdefault('_manual_wait', {})
        st    = mw.pop(message.chat.id, None)
        if not st:
            return
        mode  = st.get('mode')
        query = message.text.strip()

        # Підказка після вибору клієнта
        setup = state.get('_order_setup', {}).get(message.chat.id)
        if setup and setup.get('waiting_hint'):
            items = setup.get('items') or ([setup['item']] if setup.get('item') else [])
            slug  = setup.get('slug')
            state.get('_order_setup', {}).pop(message.chat.id, None)
            if slug:
                from clients import clients
                clients.set_active(message.chat.id, slug)
                p    = clients.get_profile(slug)
                name = p['name'] if p else "без клієнта"
            else:
                name = "без клієнта"
            bot.reply_to(message, f"✅ {name} | _{query}_\n⏳ Обробляю...",
                         parse_mode="Markdown")
            from handlers.photo_handler import _add_to_batch
            for it in items:
                cap = it.get('caption', '')
                if query and query not in cap:
                    it['caption'] = ' | '.join(filter(None, [cap, query]))
                _add_to_batch(message.chat.id, it, state, bot)
            return

        # Режим пошуку нового клієнта
        if mode == 'new_client':
            from handlers.client_handler import _create_client
            _create_client(message.chat.id, query, bot)
        elif mode == 'new_client_confirm':
            from handlers.client_handler import _confirm_client
            _confirm_client(message.chat.id, query, st, bot)

    @bot.message_handler(func=lambda m: m.text and not m.text.startswith('/')
                         and m.chat.id not in state.get('_manual_wait', {})
                         and m.chat.id not in state.get('_train_state', {})
                         and not any(m.text.lower().strip().startswith(p)
                                     for p in ('клієнт', 'клієнти', 'новий клієнт',
                                               'пошук', 'правило', 'правила',
                                               'ocr ', 'ocr список',
                                               '📸', '📊', '👥', '👑', '🛑', '📚',
                                               'навчання', 'словник')))
    def handle_text_hint(message):
        """Довільний текст: список → питаємо підбір чи підказка; інакше → підказка виробника."""
        text = message.text.strip()
        # Схожий на список (є цифри і переноси або крапки з комою)
        import re
        is_list = bool(re.search(r'\d', text)) and (
            '\n' in text or ';' in text or len(text.split()) > 5)

        if is_list:
            state.setdefault('_pending_text', {})[message.chat.id] = {
                'text': text, 'ts': time.time()}
            bot.reply_to(message,
                         "Це схоже на список товарів. Що робити?",
                         reply_markup=text_choice_keyboard())
            return

        # Зберігаємо як підказку виробника на HINT_TTL секунд
        state.setdefault('pending_hints', {})[message.chat.id] = text
        bot.reply_to(message,
                     f"💡 Підказка збережена: _{text}_\nТепер кинь фото.",
                     parse_mode="Markdown")

        def _expire():
            hints = state.get('pending_hints', {})
            if hints.get(message.chat.id) == text:
                hints.pop(message.chat.id, None)

        t = threading.Timer(HINT_TTL, _expire)
        t.daemon = True
        t.start()

    @bot.callback_query_handler(func=lambda c: c.data in ("txts", "txth"))
    def handle_text_choice(call):
        chat_id = call.message.chat.id
        pending = state.get('_pending_text', {}).pop(chat_id, {})
        text    = pending.get('text', '')
        bot.answer_callback_query(call.id)
        if call.data == "txts" and text:
            from engine.ocr import normalize_text
            from services.process_service import _run_search
            позиції = normalize_text(text, '')
            status  = bot.send_message(chat_id, "🔍 Шукаю...")
            _run_search(
                chat_id=chat_id, всі_позиції=позиції, items=[],
                caption='', chosen_brand_map={},
                msg_id=status.message_id, bot=bot, _state=state)
        elif call.data == "txth" and text:
            state.setdefault('pending_hints', {})[chat_id] = text
            bot.edit_message_text(
                f"💡 Підказка збережена: _{text}_\nТепер кинь фото.",
                chat_id, call.message.message_id, parse_mode="Markdown")
