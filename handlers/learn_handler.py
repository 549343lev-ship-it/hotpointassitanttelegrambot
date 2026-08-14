"""handlers/learn_handler.py — Навчання бота на парах фото+рахунок клієнта."""
import os
import re
import threading
import json

from config.settings import BATCH_TIMEOUT


def register(bot, state: dict):
    from clients import clients

    _learn_state       = state.setdefault('_learn_state', {})
    _learn_photo_batch  = {}
    _learn_photo_timers = {}

    # ── Запуск навчання ───────────────────────────────────────────────────────

    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ('навчання', '📚 навчання'))
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

    # ── Вибір клієнта для навчання ────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data.startswith('lrn_') and c.data != 'lrn_photos_done')
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
        p       = clients.get_profile(slug)
        _, ex_n = clients.get_next_example_dir(slug)
        _learn_state[chat_id] = {
            'slug':            slug,
            'example_n':       ex_n,
            'stage':           'photos',
            'photo_paths':     [],
            'photo_count':     0,
            'invoice_received': False,
        }
        text = (
            f"📚 Навчання клієнта *{p['name'] if p else slug}*\n"
            f"Приклад #{ex_n}\n\n"
            f"Крок 1️⃣: Кидай фото замовлення від майстра\n"
            f"_(можна кілька — коли всі кинув, натисни_ *Готово* _або одразу кидай рахунок)_"
        )
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Фото готові — кидай рахунок", callback_data="lrn_photos_done"))
        if reply_to:
            bot.reply_to(reply_to, text, parse_mode="Markdown", reply_markup=mk)
        else:
            bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=mk)

    # ── Прийом фото ───────────────────────────────────────────────────────────

    @bot.message_handler(content_types=['photo'],
                         func=lambda m: m.chat.id in state.get('_learn_state', {})
                         and state['_learn_state'][m.chat.id].get('stage') in ('photos', 'invoice')
                         and not state['_learn_state'][m.chat.id].get('invoice_received'))
    def handle_learn_photo(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        st = _learn_state.get(message.chat.id)
        if not st or st.get('invoice_received'):
            return
        st['stage'] = 'photos'

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
            if not lstate:
                return
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
            mk.add(InlineKeyboardButton("✅ Фото готові — кидай рахунок",
                                        callback_data="lrn_photos_done"))
            bot.send_message(cid,
                f"✅ Збережено фото: *{total}* шт.\nКидай ще або натисни кнопку і кидай рахунок.",
                parse_mode="Markdown", reply_markup=mk)

        t = threading.Timer(BATCH_TIMEOUT, _flush, args=[chat_id])
        t.daemon = True
        t.start()
        _learn_photo_timers[chat_id] = t

    # ── Кнопка "фото готові" ──────────────────────────────────────────────────

    @bot.callback_query_handler(func=lambda c: c.data == 'lrn_photos_done')
    def cb_learn_photos_done(call):
        st = _learn_state.get(call.message.chat.id)
        if not st:
            bot.answer_callback_query(call.id, "Сесія завершена"); return
        if st.get('photo_count', 0) == 0:
            bot.answer_callback_query(call.id, "⚠️ Спочатку кинь хоча б одне фото!"); return
        st['stage'] = 'invoice'
        bot.edit_message_text(
            f"✅ Фото збережено: {st['photo_count']} шт.\n\n"
            f"Крок 2️⃣: Кидай файл рахунку (.xls або .xlsx)",
            call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    # ── Прийом рахунку ────────────────────────────────────────────────────────

    @bot.message_handler(content_types=['document'],
                         func=lambda m: m.chat.id in state.get('_learn_state', {})
                         and state['_learn_state'][m.chat.id].get('stage') == 'invoice')
    def handle_learn_invoice(message):
        st   = _learn_state.get(message.chat.id)
        slug = st['slug']
        ex_n = st['example_n']

        fname = message.document.file_name or ''
        ext   = fname.rsplit('.', 1)[-1].lower() if '.' in fname else 'xlsx'
        if ext not in ('xls', 'xlsx'):
            bot.reply_to(message, "⚠️ Потрібен файл .xls або .xlsx"); return

        file_info    = bot.get_file(message.document.file_id)
        file_data    = bot.download_file(file_info.file_path)
        invoice_path = clients.save_example_invoice(slug, ex_n, file_data, ext)

        st['stage']            = 'invoice'
        st['invoice_received'] = True

        status_msg = bot.reply_to(message, "⏳ Зіставляю фото з рахунком через Gemini...")

        invoice_items = clients.parse_invoice(invoice_path)
        print(f"📄 parse_invoice: {len(invoice_items)} позицій з {invoice_path}", flush=True)
        if not invoice_items:
            bot.edit_message_text(
                "❌ Не вдалося прочитати рахунок. Перевір формат файлу.",
                message.chat.id, status_msg.message_id); return

        photo_paths = st.get('photo_paths', [])
        print(f"📸 Фото для навчання: {photo_paths}", flush=True)
        if not photo_paths:
            bot.edit_message_text(
                "❌ Фото не знайдено. Почни навчання знову: `навчання`",
                message.chat.id, status_msg.message_id, parse_mode="Markdown"); return

        photos_bytes = []
        for pp in photo_paths:
            if os.path.exists(pp):
                with open(pp, 'rb') as f:
                    photos_bytes.append(f.read())
                print(f"  ✅ {pp} ({os.path.getsize(pp)} байт)", flush=True)
            else:
                print(f"  ❌ Не знайдено: {pp}", flush=True)

        if not photos_bytes:
            bot.edit_message_text("❌ Файли фото не читаються. Спробуй знову.",
                                  message.chat.id, status_msg.message_id); return

        bot.edit_message_text(
            f"⏳ Gemini аналізує {len(photos_bytes)} фото та {len(invoice_items)} позицій...",
            message.chat.id, status_msg.message_id)

        try:
            pairs, raw_response = _gemini_match(photos_bytes, invoice_items)
            print(f"🤖 Gemini (перші 500):\n{raw_response[:500]}", flush=True)
        except Exception as e:
            import traceback
            print(f"❌ Gemini exception:\n{traceback.format_exc()}", flush=True)
            bot.edit_message_text(
                f"❌ Помилка Gemini:\n`{str(e)[:200]}`",
                message.chat.id, status_msg.message_id, parse_mode="Markdown"); return

        if not pairs:
            bot.edit_message_text(
                f"⚠️ Gemini не знайшов збігів між фото і рахунком.\n\n"
                f"Можливі причини:\n"
                f"• Фото і рахунок від різних замовлень\n"
                f"• Фото нечітке або погано освітлене\n"
                f"• Gemini не зміг розібрати почерк\n\n"
                f"_Відповідь Gemini:_\n`{raw_response[:300]}`",
                message.chat.id, status_msg.message_id, parse_mode="Markdown"); return

        saved = clients.learn_from_example(slug, ex_n, pairs)
        _learn_state.pop(message.chat.id, None)
        print(f"✅ Навчання: збережено {saved}/{len(pairs)} пар", flush=True)

        p = clients.get_profile(slug)
        bot.edit_message_text(
            f"✅ Навчання завершено!\n"
            f"👤 Клієнт: *{p['name'] if p else slug}*\n"
            f"📚 Приклад #{ex_n}\n"
            f"📸 Фото: {len(photos_bytes)} шт.\n"
            f"🔗 Знайдено збігів: *{len(pairs)}*\n"
            f"💾 Збережено в кеш: *{saved}*\n\n"
            f"Для ще одного прикладу: натисни *📚 Навчання*",
            message.chat.id, status_msg.message_id, parse_mode="Markdown")


# ── Gemini зіставлення ────────────────────────────────────────────────────────

def _gemini_match(photos_bytes: list[bytes],
                  invoice_items: list[str]) -> tuple[list[dict], str]:
    from google import genai as _genai
    from google.genai import types as _gtypes

    GEMINI_KEY   = os.environ.get("GEMINI_KEY", "")
    client       = _genai.Client(api_key=GEMINI_KEY)
    invoice_text = "\n".join(f"{i+1}. {name}" for i, name in enumerate(invoice_items))

    prompt = f"""Ти — експерт з читання рукописних замовлень сантехніки українською мовою.

На фото — рукописний список замовлення від майстра-сантехніка (може бути кілька сторінок).
Нижче — рахунок з правильними назвами товарів з бази.

РАХУНОК (товари з бази, {len(invoice_items)} позицій):
{invoice_text}

ЗАВДАННЯ:
1. Прочитай кожен рядок з фото (скорочення, абревіатури, каракулі — все читай)
2. Знайди найближчий товар з рахунку
3. Якщо рядок з фото точно відповідає товару з рахунку — включай в результат

ПРАВИЛА зіставлення:
- "Труба ф25" на фото → "Труба PPR..." в рахунку ✓
- "Трійник ф25" → "Трійник однозначний рівний PPR ф 25..." ✓
- "Кол ф25 90" → "Коліно PPR 90° ф 25..." ✓
- Скорочення: "Тр" = Трійник, "Кол/Кут" = Коліно, "Тр-ба" = Труба
- Ігноруй кількість (шт, м) — вона не є назвою товару
- Якщо немає відповідника — НЕ включай

Поверни ТІЛЬКИ JSON масив (без пояснень, без markdown):
[
  {{"original": "що написано на фото", "catalog_name": "точна назва з рахунку", "category": "категорія"}},
  ...
]

Категорії: plastic_ppr, push_systems, sewage, adapters_reducers, shutoff_valves, heating,
metal_plastic, filtration, insulation, radiators_radiatorsvalve, underfloor_heating,
water_heaters, boilers, pumps, mixers_faucets, sanitary_ware, siphons_fittings,
hoses, water_meters, towel_warmers, safety_valves, automation, other"""

    contents = [_gtypes.Part.from_bytes(data=pb, mime_type="image/jpeg")
                for pb in photos_bytes]
    contents.append(_gtypes.Part.from_text(text=prompt))

    resp = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=contents,
        config=_gtypes.GenerateContentConfig(temperature=0),
    )
    raw  = (resp.text or '').strip()
    text = re.sub(r'^```json\s*', '', raw)
    text = re.sub(r'\s*```$', '', text).strip()

    try:
        pairs = json.loads(text)
        return (pairs if isinstance(pairs, list) else []), raw
    except json.JSONDecodeError as e:
        print(f"❌ JSON parse: {e}\nRaw: {raw[:500]}", flush=True)
        return [], raw
