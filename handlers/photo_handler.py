"""handlers/photo_handler.py — Прийом photo/document → батчинг → brand_selector."""
import base64
import time
import threading
from config.settings import BATCH_TIMEOUT


def register(bot, state: dict):
    """Реєструє хендлери фото і документів."""

    @bot.message_handler(content_types=['photo'])
    def handle_photo(message):
        chat_id = message.chat.id

        # Якщо це навчання — передаємо туди
        if chat_id in state.get('_learn_state', {}):
            st = state['_learn_state'].get(chat_id, {})
            if st.get('stage') in ('photos', 'invoice') and not st.get('invoice_received'):
                # forward до навчального хендлера
                state.get('_handle_learn_photo', lambda m: None)(message)
                return

        # Якщо чекаємо підказку — фото замість тексту → скасовуємо очікування
        setup = state.get('_order_setup', {}).get(chat_id)
        if setup and setup.get('waiting_hint'):
            state['_order_setup'].pop(chat_id, None)

        # Дедублікація фото
        fuid = message.photo[-1].file_unique_id
        batch = state.get('user_batches', {}).get(chat_id)
        if batch and any(it.get('fuid') == fuid for it in batch.get('items', [])):
            return

        for attempt in range(3):
            try:
                file_info  = bot.get_file(message.photo[-1].file_id)
                downloaded = bot.download_file(file_info.file_path)
                image_b64  = base64.b64encode(downloaded).decode('utf-8')
                caption    = message.caption or ""
                hint       = state.get('pending_hints', {}).pop(chat_id, '')
                full_cap   = ' | '.join(filter(None, [caption, hint]))

                _ask_order_setup(message, {
                    'type': 'photo', 'data': image_b64,
                    'caption': full_cap, 'fuid': fuid,
                    'username': message.from_user.username or str(message.from_user.id),
                }, hint_already=bool(hint), bot=bot, state=state)
                return
            except Exception as e:
                if attempt == 2:
                    bot.reply_to(message, f"❌ Не вдалося завантажити фото: {e}")
                else:
                    time.sleep(2)

    @bot.message_handler(content_types=['document'])
    def handle_document(message):
        chat_id = message.chat.id

        # Навчання — рахунок
        if chat_id in state.get('_learn_state', {}):
            st = state['_learn_state'].get(chat_id, {})
            if st.get('stage') == 'invoice' and not st.get('invoice_received'):
                state.get('_handle_learn_invoice', lambda m: None)(message)
                return

        doc  = message.document
        mime = doc.mime_type or ''
        is_image = mime in ('image/jpeg', 'image/png', 'image/webp')
        is_pdf   = mime == 'application/pdf'

        if not (is_image or is_pdf):
            bot.reply_to(message, "⚠️ Надсилай фото або PDF."); return

        for attempt in range(3):
            try:
                file_info  = bot.get_file(doc.file_id)
                downloaded = bot.download_file(file_info.file_path)
                data_b64   = base64.b64encode(downloaded).decode('utf-8')
                caption    = message.caption or ""
                hint       = state.get('pending_hints', {}).pop(chat_id, '')
                full_cap   = ' | '.join(filter(None, [caption, hint]))
                dtype      = 'photo' if is_image else 'pdf'

                _ask_order_setup(message, {
                    'type': dtype, 'data': data_b64,
                    'caption': full_cap, 'fuid': doc.file_unique_id,
                    'username': message.from_user.username or str(message.from_user.id),
                }, hint_already=bool(hint), bot=bot, state=state)
                return
            except Exception as e:
                if attempt == 2:
                    bot.reply_to(message, f"❌ Помилка: {e}")
                else:
                    time.sleep(2)


# ─── pre-батч і _ask_order_setup ─────────────────────────────────────────────

def _ask_order_setup(message, item: dict, hint_already: bool,
                     bot, state: dict):
    """Збирає фото в pre-батч (4 сек), потім питає клієнта/підказку."""
    from clients import clients
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    chat_id = message.chat.id

    # Якщо вже є активний батч — одразу додаємо
    if chat_id in state['user_batches'] and state['user_batches'][chat_id].get('items'):
        caption = item.get('caption', '')
        hint    = state.get('pending_hints', {}).pop(chat_id, '')
        if hint and hint not in caption:
            item['caption'] = ' | '.join(filter(None, [caption, hint]))
        _add_to_batch(chat_id, item, state, bot)
        return

    pre = state.setdefault('_pre_batch', {})
    if chat_id not in pre:
        pre[chat_id] = {'items': [], 'hint_already': hint_already}
    pre[chat_id]['items'].append(item)
    if hint_already:
        pre[chat_id]['hint_already'] = True

    old_timer = pre[chat_id].get('timer')
    if old_timer:
        old_timer.cancel()

    def _flush(cid, msg):
        p = state.get('_pre_batch', {}).pop(cid, None)
        if not p or not p['items']:
            return

        if cid in state['user_batches'] and state['user_batches'][cid].get('items'):
            for it in p['items']:
                _add_to_batch(cid, it, state, bot)
            return

        slug        = clients.get_active(cid)
        active_name = None
        if slug:
            prof = clients.get_profile(slug)
            active_name = prof['name'] if prof else None

        state.setdefault('_order_setup', {})[cid] = {
            'items':        p['items'],
            'hint_already': p.get('hint_already', False),
        }

        mk = InlineKeyboardMarkup(row_width=1)
        if active_name:
            mk.add(InlineKeyboardButton(
                f"✅ {active_name} (активний)", callback_data="osetup_active"))
        mk.add(
            InlineKeyboardButton("👤 Без клієнта",     callback_data="osetup_none"),
            InlineKeyboardButton("🔍 Вибрати клієнта", callback_data="osetup_pick"),
        )
        n_photos = len(p['items'])
        text = f"📋 *Для кого замовлення?* ({n_photos} фото)"
        bot.send_message(cid, text, parse_mode="Markdown", reply_markup=mk)

    t = threading.Timer(BATCH_TIMEOUT, _flush, args=[chat_id, message])
    t.daemon = True
    t.start()
    pre[chat_id]['timer'] = t


def _add_to_batch(chat_id: int, item: dict, state: dict, bot):
    """Додає item в батч і (пере)запускає таймер process_batch."""
    from services.process_service import process_batch

    if chat_id not in state['user_batches']:
        state['user_batches'][chat_id] = {'items': []}

    old_timer = state['user_batches'][chat_id].get('timer')
    if old_timer:
        old_timer.cancel()

    state['user_batches'][chat_id]['items'].append(item)
    # Позначаємо що батч зібраний — нові фото не запустять новий флоу
    state['user_batches'][chat_id]['ready'] = True

    t = threading.Timer(BATCH_TIMEOUT, process_batch,
                        args=[chat_id, bot, state])
    t.daemon = True
    t.start()
    state['user_batches'][chat_id]['timer'] = t
