"""handlers/learn_handler.py — Навчання бота на парах замовлення+рахунок."""
import os
import re
import threading
import json
import base64

from config.settings import BATCH_TIMEOUT


def register(bot, state: dict):
    from clients import clients

    _learn_state        = state.setdefault('_learn_state', {})
    _learn_photo_batch  = {}
    _learn_photo_timers = {}

    # ── Запуск навчання ───────────────────────────────────────────────────────

    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ('🌐 навчання бота', 'навчання бота'))
    def handle_learn_global(message):
        _start_learn_session(message.chat.id, slug='_global', reply_to=message)

    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ('навчання', '📚 навчання', '📚 навчання клієнта', 'навчання клієнта'))
    def handle_learn_start(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        slug = clients.get_active(message.chat.id)
        if not slug:
            index = clients.list_clients()
            if not index:
                bot.reply_to(message,
                    "⚠️ Немає жодного клієнта.\nСпочатку створи: `новий клієнт Ім'я`",
                    parse_mode="Markdown"); return
            mk = InlineKeyboardMarkup(row_width=1)
            for s, cname in sorted(index.items(), key=lambda x: x[1])[:10]:
                mk.add(InlineKeyboardButton(f"👤 {cname}", callback_data=f"lrn_{s}"))
            bot.reply_to(message, "📚 Кого навчаємо? Обери клієнта:", reply_markup=mk)
            return
        _start_learn_session(message.chat.id, slug, reply_to=message)

    # ── Вибір клієнта ─────────────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith('lrn_') and c.data != 'lrn_order_done')
    def cb_learn_pick_client(call):
        slug = call.data[4:]
        p    = clients.get_profile(slug)
        if not p:
            bot.answer_callback_query(call.id, "Клієнта не знайдено"); return
        clients.set_active(call.message.chat.id, slug)
        bot.edit_message_text(
            f"👤 Обрано: *{p['name']}*",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        bot.answer_callback_query(call.id)
        _start_learn_session(call.message.chat.id, slug)

    # ── Ініціалізація сесії ───────────────────────────────────────────────────

    def _start_learn_session(chat_id: int, slug: str, reply_to=None):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        is_global = (slug == '_global')
        p         = clients.get_profile(slug) if not is_global else None
        _, ex_n   = clients.get_next_example_dir(slug) if not is_global else (None, 1)
        _learn_state[chat_id] = {
            'slug':             slug,
            'example_n':        ex_n,
            'stage':            'order',      # order → invoice
            # Замовлення від майстра — три можливих джерела
            'photo_paths':      [],           # фото
            'photo_count':      0,
            'order_text':       '',           # текст
            'order_file_path':  None,         # xlsx/pdf файл
            'order_file_type':  None,         # 'xlsx' / 'pdf'
            'invoice_received': False,
        }
        client_label = 'ВЕСЬ БОТ (для всіх клієнтів)' if is_global else (p['name'] if p else slug)
        text = (
            f"📚 Навчання *{client_label}*\n"
            f"Приклад #{ex_n}\n\n"
            f"Крок 1️⃣: Кидай замовлення від майстра у будь-якому форматі:\n"
            f"  📸 Фото (можна кілька)\n"
            f"  💬 Текст (просто надішли)\n"
            f"  📄 Excel або PDF файл\n\n"
            f"_Коли готово — натисни кнопку або одразу кидай рахунок_"
        )
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Замовлення готове — кидай рахунок", callback_data="lrn_order_done"))
        if reply_to:
            bot.reply_to(reply_to, text, parse_mode="Markdown", reply_markup=mk)
        else:
            bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=mk)

    # ── Крок 1А: Фото замовлення ──────────────────────────────────────────────

    @bot.message_handler(content_types=['photo'],
                         func=lambda m: m.chat.id in state.get('_learn_state', {})
                         and state['_learn_state'][m.chat.id].get('stage') == 'order')
    def handle_learn_photo(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        st = _learn_state.get(message.chat.id)
        if not st: return

        chat_id = message.chat.id
        ex_n    = st['example_n']

        file_info = bot.get_file(message.photo[-1].file_id)
        file_data = bot.download_file(file_info.file_path)
        ext       = (file_info.file_path.split('.')[-1] or 'jpg').lower()
        _learn_photo_batch.setdefault(chat_id, []).append((file_data, ext))

        if chat_id in _learn_photo_timers:
            _learn_photo_timers[chat_id].cancel()

        def _flush(cid):
            batch  = _learn_photo_batch.pop(cid, [])
            _learn_photo_timers.pop(cid, None)
            lstate = _learn_state.get(cid)
            if not lstate: return
            count_before = lstate.get('photo_count', 0)
            for i, (fdata, fext) in enumerate(batch, start=count_before + 1):
                fpath = os.path.join(
                    clients.CLIENTS_DIR, lstate['slug'], "examples",
                    f"приклад_{lstate['example_n']}", f"photo_{i}.{fext}"
                )
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                with open(fpath, 'wb') as f:
                    f.write(fdata)
                lstate.setdefault('photo_paths', []).append(fpath)
            lstate['photo_count'] = count_before + len(batch)
            total = lstate['photo_count']
            mk = InlineKeyboardMarkup()
            mk.add(InlineKeyboardButton("✅ Замовлення готове — кидай рахунок",
                                        callback_data="lrn_order_done"))
            bot.send_message(cid,
                f"✅ Збережено фото: *{total}* шт.\nКидай ще або натисни кнопку.",
                parse_mode="Markdown", reply_markup=mk)

        t = threading.Timer(BATCH_TIMEOUT, _flush, args=[chat_id])
        t.daemon = True
        t.start()
        _learn_photo_timers[chat_id] = t

    # ── Крок 1Б: Текст замовлення ─────────────────────────────────────────────

    @bot.message_handler(content_types=['text'],
                         func=lambda m: m.chat.id in state.get('_learn_state', {})
                         and state['_learn_state'][m.chat.id].get('stage') == 'order'
                         and m.text and not m.text.startswith('/')
                         and m.text.lower().strip() not in (
                             'навчання', '📚 навчання', '📚 навчання клієнта',
                             '🌐 навчання бота', 'навчання клієнта', '🛑 стоп'))
    def handle_learn_text(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        st = _learn_state.get(message.chat.id)
        if not st: return
        # Додаємо текст (може бути кілька повідомлень)
        existing = st.get('order_text', '')
        st['order_text'] = (existing + '\n' + message.text).strip()
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Замовлення готове — кидай рахунок",
                                    callback_data="lrn_order_done"))
        bot.reply_to(message,
            f"✅ Текст збережено ({len(st['order_text'])} символів).\n"
            f"Можеш дописати ще або натисни кнопку.",
            reply_markup=mk)

    # ── Крок 1В: Excel/PDF замовлення ────────────────────────────────────────

    @bot.message_handler(content_types=['document'],
                         func=lambda m: m.chat.id in state.get('_learn_state', {})
                         and state['_learn_state'][m.chat.id].get('stage') == 'order')
    def handle_learn_order_file(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        st    = _learn_state.get(message.chat.id)
        if not st: return
        doc   = message.document
        fname = doc.file_name or ''
        ext   = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''
        mime  = doc.mime_type or ''

        if ext not in ('xls', 'xlsx', 'pdf') and not mime.startswith('image/'):
            bot.reply_to(message, "⚠️ Підтримуються: .xlsx, .xls, .pdf або фото"); return

        file_info = bot.get_file(doc.file_id)
        file_data = bot.download_file(file_info.file_path)

        # Зберігаємо файл замовлення
        ex_n = st['example_n']
        slug = st['slug']
        fpath = os.path.join(
            clients.CLIENTS_DIR, slug, "examples",
            f"приклад_{ex_n}", f"order.{ext}"
        )
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        with open(fpath, 'wb') as f:
            f.write(file_data)

        st['order_file_path'] = fpath
        st['order_file_type'] = ext

        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Замовлення готове — кидай рахунок",
                                    callback_data="lrn_order_done"))
        bot.reply_to(message,
            f"✅ Файл замовлення збережено ({fname}).\nТепер кидай рахунок Excel.",
            reply_markup=mk)

    # ── Кнопка "замовлення готове" ────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == 'lrn_order_done')
    def cb_learn_order_done(call):
        chat_id = call.message.chat.id
        st = _learn_state.get(chat_id)
        if not st:
            bot.answer_callback_query(call.id, "Сесія завершена"); return

        # Примусово скидаємо батч фото
        if chat_id in _learn_photo_timers:
            _learn_photo_timers[chat_id].cancel()
            _learn_photo_timers.pop(chat_id, None)
        pending = _learn_photo_batch.pop(chat_id, [])
        if pending:
            count_before = st.get('photo_count', 0)
            for i, (fdata, fext) in enumerate(pending, start=count_before + 1):
                fpath = os.path.join(
                    clients.CLIENTS_DIR, st['slug'], "examples",
                    f"приклад_{st['example_n']}", f"photo_{i}.{fext}"
                )
                os.makedirs(os.path.dirname(fpath), exist_ok=True)
                with open(fpath, 'wb') as f:
                    f.write(fdata)
                st.setdefault('photo_paths', []).append(fpath)
            st['photo_count'] = count_before + len(pending)

        # Перевіряємо що є хоч щось
        has_photos = st.get('photo_count', 0) > 0
        has_text   = bool(st.get('order_text', '').strip())
        has_file   = bool(st.get('order_file_path'))

        if not (has_photos or has_text or has_file):
            bot.answer_callback_query(call.id, "⚠️ Спочатку кинь замовлення!"); return

        st['stage'] = 'invoice'
        sources = []
        if has_photos: sources.append(f"📸 {st['photo_count']} фото")
        if has_text:   sources.append(f"💬 текст")
        if has_file:   sources.append(f"📄 файл ({st['order_file_type']})")

        bot.edit_message_text(
            f"✅ Замовлення збережено: {', '.join(sources)}\n\n"
            f"Крок 2️⃣: Кидай рахунок Excel (.xls або .xlsx)",
            call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ── Крок 2: Рахунок Excel ─────────────────────────────────────────────────

    @bot.message_handler(content_types=['document'],
                         func=lambda m: m.chat.id in state.get('_learn_state', {})
                         and state['_learn_state'][m.chat.id].get('stage') == 'invoice'
                         and not state['_learn_state'][m.chat.id].get('invoice_received'))
    def handle_learn_invoice(message):
        st    = _learn_state.get(message.chat.id)
        if not st: return
        fname = message.document.file_name or ''
        ext   = fname.rsplit('.', 1)[-1].lower() if '.' in fname else ''

        if ext not in ('xls', 'xlsx'):
            bot.reply_to(message, "⚠️ Рахунок має бути Excel (.xls або .xlsx)"); return

        file_info = bot.get_file(message.document.file_id)
        file_data = bot.download_file(file_info.file_path)

        st['invoice_received'] = True
        slug = st['slug']
        ex_n = st['example_n']

        invoice_path  = clients.save_example_invoice(slug, ex_n, file_data, ext)
        invoice_items = clients.parse_invoice(invoice_path)

        print(f"📄 parse_invoice: {len(invoice_items)} позицій", flush=True)
        if not invoice_items:
            st['invoice_received'] = False
            bot.reply_to(message, "❌ Не вдалося прочитати рахунок. Перевір формат файлу.")
            return

        status_msg = bot.reply_to(message, "⏳ Зіставляю замовлення з рахунком через Gemini...")

        # Збираємо матеріали замовлення
        photos_bytes = []
        for pp in st.get('photo_paths', []):
            if os.path.exists(pp):
                with open(pp, 'rb') as f:
                    photos_bytes.append(('image', f.read()))

        order_text      = st.get('order_text', '').strip()
        order_file_path = st.get('order_file_path')
        order_file_type = st.get('order_file_type', '')

        order_file_bytes = None
        if order_file_path and os.path.exists(order_file_path):
            with open(order_file_path, 'rb') as f:
                order_file_bytes = f.read()

        if not photos_bytes and not order_text and not order_file_bytes:
            bot.edit_message_text(
                "❌ Матеріали замовлення не знайдені. Спробуй знову.",
                message.chat.id, status_msg.message_id); return

        bot.edit_message_text(
            f"⏳ Gemini аналізує замовлення та {len(invoice_items)} позицій рахунку...",
            message.chat.id, status_msg.message_id)

        try:
            pairs, raw_response = _gemini_match(
                photos_bytes=photos_bytes,
                order_text=order_text,
                order_file_bytes=order_file_bytes,
                order_file_type=order_file_type,
                invoice_items=invoice_items,
            )
            print(f"🤖 Gemini (перші 500):\n{raw_response[:500]}", flush=True)
        except Exception as e:
            import traceback
            print(f"❌ Gemini exception:\n{traceback.format_exc()}", flush=True)
            safe_e = str(e)[:200].replace('`', "'")
            bot.edit_message_text(
                f"❌ Помилка Gemini:\n{safe_e}",
                message.chat.id, status_msg.message_id); return

        if not pairs:
            safe_raw = raw_response[:300].replace('`', "'").replace('_', '').replace('*', '')
            bot.edit_message_text(
                f"⚠️ Gemini не знайшов збігів між замовленням і рахунком.\n\n"
                f"Можливі причини:\n"
                f"• Замовлення і рахунок від різних об'єктів\n"
                f"• Фото нечітке або погано освітлене\n"
                f"• Gemini не зміг розібрати почерк\n\n"
                f"Відповідь Gemini:\n{safe_raw}",
                message.chat.id, status_msg.message_id); return

        if slug == '_global':
            from clients.cache import cache_confirm
            saved = 0
            for pair in pairs:
                orig = (pair.get('original') or '').strip()
                name = (pair.get('catalog_name') or '').strip()
                cat  = pair.get('category', 'other')
                if orig and name:
                    cache_confirm(orig, {}, orig, name, cat, source='global_train')
                    saved += 1
        else:
            saved = clients.learn_from_example(slug, ex_n, pairs)

        _learn_state.pop(message.chat.id, None)
        print(f"✅ Навчання: збережено {saved}/{len(pairs)} пар", flush=True)

        p = clients.get_profile(slug) if slug != '_global' else None
        client_label = 'ВЕСЬ БОТ' if slug == '_global' else (p['name'] if p else slug)
        bot.edit_message_text(
            f"✅ Навчання завершено!\n"
            f"👤 Клієнт: *{client_label}*\n"
            f"📚 Приклад #{ex_n}\n"
            f"🔗 Знайдено збігів: *{len(pairs)}*\n"
            f"💾 Збережено в кеш: *{saved}*\n\n"
            f"Для ще одного прикладу: натисни *📚 Навчання клієнта*",
            message.chat.id, status_msg.message_id, parse_mode="Markdown")

    # Expose handlers до photo_handler через state
    state['_handle_learn_photo']      = handle_learn_photo
    state['_handle_learn_invoice']    = handle_learn_invoice
    state['_handle_learn_order_file'] = handle_learn_order_file


# ── Gemini зіставлення ────────────────────────────────────────────────────────

def _extract_order_items_from_xlsx(file_bytes: bytes, ext: str) -> list[str]:
    """Витягує список позицій замовлення з xlsx/xls файлу."""
    try:
        import pandas as pd, io, re
        engine = 'xlrd' if ext == 'xls' else 'openpyxl'
        df = pd.read_excel(io.BytesIO(file_bytes), header=None, engine=engine)

        # Шукаємо колонку з назвами (Номенклатура, Найменування, Назва)
        nom_col = header_row = None
        for ri in range(min(20, len(df))):
            for ci in range(len(df.columns)):
                val = str(df.iloc[ri, ci] or '').strip().lower()
                if any(k in val for k in ('номенклатур', 'найменуван', 'назва', 'товар')):
                    nom_col, header_row = ci, ri
                    break
            if nom_col is not None:
                break

        if nom_col is None:
            nom_col, header_row = 1, 0

        items = []
        for ri in range((header_row or 0) + 1, len(df)):
            val = str(df.iloc[ri, nom_col] or '').strip()
            if not val or val in ('nan', 'None') or len(val) < 4:
                continue
            if any(s in val for s in ['Покупець', 'Виконавець', 'оплат', 'реквізит']):
                continue
            val = re.sub(r'\s*\{[^}]+\}', '', val).strip()
            val = re.sub(r'^NEW!\s*', '', val).strip()
            if val and len(val) > 3:
                items.append(val)
        return items
    except Exception as e:
        print(f"⚠️ _extract_order_items_from_xlsx: {e}", flush=True)
        return []


def _gemini_match(
    photos_bytes: list[tuple],
    order_text: str,
    order_file_bytes: bytes | None,
    order_file_type: str,
    invoice_items: list[str],
) -> tuple[list[dict], str]:
    from google import genai as _genai
    from google.genai import types as _gtypes
    import re, json

    GEMINI_KEY = os.environ.get("GEMINI_KEY", "")
    client     = _genai.Client(api_key=GEMINI_KEY)
    invoice_text = "\n".join(f"{i+1}. {name}" for i, name in enumerate(invoice_items))

    contents = []

    # Фото замовлення
    for _, pb in photos_bytes:
        contents.append(_gtypes.Part.from_bytes(data=pb, mime_type="image/jpeg"))

    # Файл замовлення
    order_items_from_file = []
    if order_file_bytes:
        if order_file_type == 'pdf':
            contents.append(_gtypes.Part.from_bytes(
                data=order_file_bytes, mime_type="application/pdf"))
        elif order_file_type in ('xls', 'xlsx'):
            # xlsx → витягуємо позиції правильно
            order_items_from_file = _extract_order_items_from_xlsx(
                order_file_bytes, order_file_type)
            print(f"📄 order xlsx: {len(order_items_from_file)} позицій", flush=True)

    # Будуємо секцію замовлення
    order_parts = []
    if order_items_from_file:
        order_text_from_file = "\n".join(
            f"{i+1}. {name}" for i, name in enumerate(order_items_from_file))
        order_parts.append(f"ФАЙЛ ЗАМОВЛЕННЯ ({len(order_items_from_file)} позицій):\n{order_text_from_file}")
    if order_text.strip():
        order_parts.append(f"ТЕКСТ ЗАМОВЛЕННЯ:\n{order_text.strip()}")
    if photos_bytes:
        order_parts.append(f"(+ {len(photos_bytes)} фото замовлення вище)")

    order_section = "\n\n".join(order_parts) if order_parts else "(дивись фото вище)"

    # Якщо є файл замовлення — змінюємо промпт: зіставляємо позиція до позиції
    if order_items_from_file:
        prompt = f"""Ти — експерт з комерційних пропозицій і кошторисів сантехніки.

СПИСОК ПІДІБРАНИХ ТОВАРІВ (наша комерційна пропозиція, {len(order_items_from_file)} позицій):
{order_text_from_file}

КОШТОРИС ЗАМОВНИКА (еталонні назви, {len(invoice_items)} позицій):
{invoice_text}

ЗАВДАННЯ:
Для кожної позиції з нашої КП знайди відповідну позицію в кошторисі замовника.
Це потрібно щоб навчити бота: "коли замовник пише X — підбирай Y".

ПРАВИЛА зіставлення:
- Труба PPR ф75 (КП) ↔ Труба поліпропіленова SDR 7,4 75 мм (кошторис) ✓
- Кран кульовий DN50 (КП) ↔ Кран кульковий 2' ВН (кошторис) ✓
- Ізоляція каучукова Ø64х9 (КП) ↔ Теплоізоляція Kaiflex 9мм 76мм (кошторис) ✓
- Хомут DN10 ф15-19 (КП) ↔ Матеріали для кріплення (кошторис) ✗ (занадто загально)
- Якщо в кошторисі лише загальна назва розділу — НЕ включай
- Включай тільки конкретні товари, не заголовки розділів

Поверни ТІЛЬКИ JSON масив де:
- "original" = назва з КОШТОРИСУ ЗАМОВНИКА (що він пише)
- "catalog_name" = назва з нашої КП (що підбираємо)
[
  {{"original": "назва з кошторису", "catalog_name": "назва з КП", "category": "категорія"}},
  ...
]

Категорії: plastic_ppr, push_systems, sewage, adapters_reducers, shutoff_valves, heating,
metal_plastic, filtration, insulation, radiators_radiatorsvalve, underfloor_heating,
water_heaters, boilers, pumps, mixers_faucets, sanitary_ware, siphons_fittings,
hoses, water_meters, towel_warmers, safety_valves, automation, other"""
    else:
        prompt = f"""Ти — експерт з читання замовлень сантехніки українською мовою.

ЗАМОВЛЕННЯ від майстра:
{order_section}

РАХУНОК (правильні назви товарів з бази, {len(invoice_items)} позицій):
{invoice_text}

ЗАВДАННЯ:
1. Прочитай кожен рядок замовлення (скорочення, абревіатури, каракулі — все читай)
2. Знайди найближчий товар з рахунку
3. Якщо рядок з замовлення відповідає товару з рахунку — включай

ПРАВИЛА:
- "Труба ф25" → "Труба PPR..." ✓
- "Трійник ф25" → "Трійник PPR ф 25..." ✓
- "Кол ф25 90" → "Коліно PPR 90° ф 25..." ✓
- Скорочення: "Тр"=Трійник, "Кол/Кут"=Коліно, "Тр-ба"=Труба
- Ігноруй кількість (шт, м)
- Якщо немає конкретного відповідника — НЕ включай

Поверни ТІЛЬКИ JSON масив:
[
  {{"original": "що написано в замовленні", "catalog_name": "точна назва з рахунку", "category": "категорія"}},
  ...
]

Категорії: plastic_ppr, push_systems, sewage, adapters_reducers, shutoff_valves, heating,
metal_plastic, filtration, insulation, radiators_radiatorsvalve, underfloor_heating,
water_heaters, boilers, pumps, mixers_faucets, sanitary_ware, siphons_fittings,
hoses, water_meters, towel_warmers, safety_valves, automation, other"""

    contents.append(_gtypes.Part.from_text(text=prompt))

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=_gtypes.GenerateContentConfig(
            temperature=0,
            max_output_tokens=32768,
        ),
    )
    raw  = (resp.text or '').strip()

    try:
        from engine.ocr import _extract_json
        pairs = _extract_json(raw)
    except Exception:
        pairs = []
        try:
            import re as _re
            text = _re.sub(r'^```json\s*', '', raw)
            text = _re.sub(r'\s*```$', '', text).strip()
            pairs = json.loads(text)
        except Exception:
            pass

    if not isinstance(pairs, list):
        pairs = []

    return pairs, raw
