"""handlers/photo_handler.py — Прийом photo/document → батчинг."""
import base64
import time
import threading
from config.settings import BATCH_TIMEOUT


def register(bot, state: dict):

    @bot.message_handler(content_types=['photo'])
    def handle_photo(message):
        chat_id = message.chat.id

        # Навчання — передаємо туди
        if chat_id in state.get('_learn_state', {}):
            st = state['_learn_state'].get(chat_id, {})
            if st.get('stage') in ('photos', 'invoice') and not st.get('invoice_received'):
                state.get('_handle_learn_photo', lambda m: None)(message)
                return

        # waiting_hint: менеджер кинув фото замість підказки
        setup = state.get('_order_setup', {}).get(chat_id)
        if setup and setup.get('waiting_hint'):
            state['_order_setup'].pop(chat_id, None)
            slug = setup.get('slug')
            if slug:
                from clients import clients
                clients.set_active(chat_id, slug)
            # Кладемо накопичені items у батч — тепер батч відкритий
            for it in (setup.get('items') or []):
                _add_to_batch(chat_id, it, state, bot)
            # Нове фото нижче потрапить в _ask_order_setup, який побачить
            # відкритий user_batches і додасть без питання клієнта

        # Дедублікація
        fuid  = message.photo[-1].file_unique_id
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
                }, hint_already=bool(hint or caption), bot=bot, state=state)
                return
            except Exception as e:
                if attempt == 2:
                    bot.reply_to(message, f"❌ Не вдалося завантажити фото: {e}")
                else:
                    time.sleep(2)

    @bot.message_handler(content_types=['document'])
    def handle_document(message):
        chat_id = message.chat.id

        doc  = message.document
        mime = (doc.mime_type or '').lower()
        fname = (doc.file_name or '').lower()

        # Навчання — рахунок
        if chat_id in state.get('_learn_state', {}):
            st = state['_learn_state'].get(chat_id, {})
            if st.get('stage') == 'invoice' and not st.get('invoice_received'):
                state.get('_handle_learn_invoice', lambda m: None)(message)
                return
            # Якщо stage=photos і це не фото/pdf — ігноруємо
            if st.get('stage') == 'photos':
                return

        is_pdf   = mime == 'application/pdf' or fname.endswith('.pdf')
        is_image = (mime.startswith('image/') or
                    fname.endswith(('.jpg', '.jpeg', '.png', '.webp')))

        if not (is_pdf or is_image):
            bot.reply_to(message, "📎 Приймаю фото (jpg/png) або PDF."); return

        fuid  = doc.file_unique_id
        batch = state.get('user_batches', {}).get(chat_id)
        if batch and any(it.get('fuid') == fuid for it in batch.get('items', [])):
            return

        for attempt in range(3):
            try:
                file_info  = bot.get_file(doc.file_id)
                downloaded = bot.download_file(file_info.file_path)
                data_b64   = base64.b64encode(downloaded).decode('utf-8')
                caption    = message.caption or ""
                hint       = state.get('pending_hints', {}).pop(chat_id, '')
                full_cap   = ' | '.join(filter(None, [caption, hint]))
                _ask_order_setup(message, {
                    'type': 'pdf' if is_pdf else 'photo',
                    'data': data_b64,
                    'caption': full_cap, 'fuid': fuid,
                    'username': message.from_user.username or str(message.from_user.id),
                }, hint_already=bool(hint or caption), bot=bot, state=state)
                return
            except Exception as e:
                if attempt == 2:
                    bot.reply_to(message, f"❌ Помилка: {e}")
                else:
                    time.sleep(2)


# ─── _ask_order_setup ─────────────────────────────────────────────────────────

def _ask_order_setup(message, item: dict, hint_already: bool, bot, state: dict):
    """Збирає фото в pre-батч (4 сек), потім питає клієнта/підказку ОДИН РАЗ."""
    from clients import clients
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    chat_id = message.chat.id

    # 1. Вже є активний батч (клієнт вже вибраний) — одразу додаємо
    if chat_id in state['user_batches'] and state['user_batches'][chat_id].get('items'):
        hint = state.get('pending_hints', {}).pop(chat_id, '')
        if hint:
            cap = item.get('caption', '')
            if hint not in cap:
                item['caption'] = ' | '.join(filter(None, [cap, hint]))
        _add_to_batch(chat_id, item, state, bot)
        return

    # 2. Вже є відкрите питання клієнта (_order_setup без waiting_hint)
    #    — просто додаємо фото в існуючий пакет, не надсилаємо нове питання
    existing = state.get('_order_setup', {}).get(chat_id)
    if existing and not existing.get('waiting_hint'):
        existing.setdefault('items', []).append(item)
        if hint_already:
            existing['hint_already'] = True
        return

    # 3. Pre-batch буфер: збираємо 4 сек, потім питаємо клієнта
    pre = state.setdefault('_pre_batch', {})
    if chat_id not in pre:
        pre[chat_id] = {'items': [], 'hint_already': hint_already}
    pre[chat_id]['items'].append(item)
    if hint_already:
        pre[chat_id]['hint_already'] = True

    old_timer = pre[chat_id].get('timer')
    if old_timer:
        old_timer.cancel()

    def _flush(cid):
        p = state.get('_pre_batch', {}).pop(cid, None)
        if not p or not p['items']:
            return

        # Якщо за цей час з'явився активний батч — одразу кидаємо туди
        if cid in state['user_batches'] and state['user_batches'][cid].get('items'):
            for it in p['items']:
                _add_to_batch(cid, it, state, bot)
            return

        slug        = clients.get_active(cid)
        active_name = None
        if slug:
            prof = clients.get_profile(slug)
            active_name = prof['name'] if prof else None

        hint_ready = p.get('hint_already', False) or bool(
            state.get('pending_hints', {}).get(cid))

        state.setdefault('_order_setup', {})[cid] = {
            'items':        p['items'],
            'hint_already': hint_ready,
        }

        mk = InlineKeyboardMarkup(row_width=1)
        if active_name:
            mk.add(InlineKeyboardButton(
                f"✅ {active_name} (активний)", callback_data="osetup_active"))
        mk.add(
            InlineKeyboardButton("👤 Без клієнта",     callback_data="osetup_none"),
            InlineKeyboardButton("🔍 Вибрати клієнта", callback_data="osetup_pick"),
        )
        n = len(p['items'])
        bot.send_message(cid,
            f"📋 *Для кого замовлення?* ({n} фото)",
            parse_mode="Markdown", reply_markup=mk)

    t = threading.Timer(BATCH_TIMEOUT, _flush, args=[chat_id])
    t.daemon = True
    t.start()
    pre[chat_id]['timer'] = t


# ─── _add_to_batch ────────────────────────────────────────────────────────────

def _add_to_batch(chat_id: int, item: dict, state: dict, bot):
    """Додає item у батч і (пере)запускає таймер process_batch."""
    from services.process_service import process_batch

    batches = state.setdefault('user_batches', {})
    if chat_id not in batches:
        batches[chat_id] = {'items': []}
        bot.send_message(chat_id, "📥 Отримав! Чекаю ще файли (4 сек)...")

    old = batches[chat_id].get('timer')
    if old:
        old.cancel()

    batches[chat_id]['items'].append(item)

    t = threading.Timer(BATCH_TIMEOUT, process_batch, args=[chat_id, bot, state])
    t.daemon = True
    t.start()
    batches[chat_id]['timer'] = t
