"""services/process_service.py — process_batch: OCR → brand_selector → find_items → Excel."""
from engine.ocr import normalize_photo, normalize_text, normalize_pdf, parse_caption_brands
from engine.brand_selector import start_brand_selection, inject_brand_map_to_positions
from engine.search import find_items, build_qa
from engine.excel_builder import create_excel
from engine.logger import log_not_found, log_usage
from services.batch_service import (expand_push_marker, expand_insulation,
                                     expand_push_sleeves)
from clients import clients


def process_batch(chat_id: int, bot, state: dict):
    """Головний оркестратор: OCR → brand_selector → пошук → Excel."""
    batch = state['user_batches'].pop(chat_id, None)
    if not batch:
        return
    state.get('stop_flags', {}).pop(chat_id, None)
    items = batch['items']

    active_slug  = clients.get_active(chat_id)
    client_prefs = clients.get_preferences(active_slug) if active_slug else {}
    client_name  = ''
    if active_slug:
        p = clients.get_profile(active_slug)
        client_name = p['name'] if p else active_slug
    client_line = f"\n👤 Клієнт: {client_name}" if client_name else ""

    status = bot.send_message(chat_id,
                              f"⏳ Обробляю {len(items)} файл(ів)...{client_line}")
    msg_id = status.message_id

    всі_позиції, errors = [], []
    for idx, item in enumerate(items, 1):
        if state.get('stop_flags', {}).get(chat_id):
            _safe_edit(bot, chat_id, msg_id, "🛑 Зупинено."); return
        try:
            _safe_edit(bot, chat_id, msg_id, f"📖 Читаю файл {idx}/{len(items)}...")
            if item['type'] == 'photo':
                pos = normalize_photo(item['data'], item.get('caption', ''))
            elif item['type'] == 'pdf':
                pos = normalize_pdf(item['data'], item.get('caption', ''))
            else:
                pos = normalize_text(item['text'], item.get('caption', ''))
            всі_позиції.extend(pos)
        except Exception as e:
            errors.append(f"❌ Файл {idx}: {e}")

    if not всі_позиції:
        _safe_edit(bot, chat_id, msg_id,
                   "😕 Не розпізнано позицій.\n" + "\n".join(errors)); return

    всі_позиції = expand_push_marker(всі_позиції)
    всі_позиції = expand_insulation(всі_позиції, build_qa)
    всі_позиції = expand_push_sleeves(всі_позиції, build_qa)

    all_captions      = [it.get('caption', '') for it in items if it.get('caption')]
    caption           = ' | '.join(all_captions)
    caption_brand_map = parse_caption_brands(caption)

    for п in всі_позиції:
        п['_brand_map']    = dict(caption_brand_map)
        п['_client_slug']  = active_slug or ''
        п['_client_prefs'] = client_prefs

    start_brand_selection(
        chat_id       = chat_id,
        позиції       = всі_позиції,
        items         = items,
        caption       = caption,
        callback_fn   = _run_search,
        status_msg_id = msg_id,
        bot           = bot,
        _state        = state,
    )


def _run_search(chat_id: int, всі_позиції: list, items: list,
                caption: str, chosen_brand_map: dict, msg_id: int,
                bot=None, _state: dict = None):
    """Викликається brand_selector після вибору — запускає пошук і генерує Excel."""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    state        = _state or {}
    active_slug  = clients.get_active(chat_id)
    caption_brand_map = parse_caption_brands(caption)

    inject_brand_map_to_positions(всі_позиції, chosen_brand_map, caption_brand_map)

    _safe_edit(bot, chat_id, msg_id, f"🔍 Шукаю {len(всі_позиції)} позицій...")

    def progress(cur, total):
        if cur % 5 == 0 or cur == total:
            _safe_edit(bot, chat_id, msg_id, f"🔍 Пошук: {cur}/{total}...")

    результати = find_items(всі_позиції, progress_cb=progress)

    for r, п in zip(результати, всі_позиції):
        if r is not None:
            r.setdefault('розділ', п.get('section', ''))

    log_not_found([r for r in результати if r and not r.get('знайдено')])

    _safe_edit(bot, chat_id, msg_id, "📊 Формую Excel...")
    excel, not_found, warn = create_excel(результати)

    знайдено = [r for r in результати if r and r.get('знайдено')]
    total    = len([r for r in результати if r])

    bot.send_document(chat_id, excel, visible_file_name="замовлення.xlsx")

    звіт = f"✅ Знайдено: {len(знайдено)}/{total}\n"
    if not_found: звіт += f"🟥 Не знайдено: {len(not_found)}\n"
    if warn:      звіт += f"🟨 Перевір: {len(warn)}\n"
    _safe_edit(bot, chat_id, msg_id, звіт)

    from config.settings import ADMIN_ID
    mk = InlineKeyboardMarkup()
    mk.add(InlineKeyboardButton("🎓 Навчання", callback_data="tr_go"),
           InlineKeyboardButton("✖️ Закрити",  callback_data="tr_close"))
    note = "" if chat_id == ADMIN_ID else "\n(твої виправлення підтверджує адмін)"
    bot.send_message(chat_id,
                     f"Перевір файл. Якщо є помилки — тапни Навчання:{note}",
                     reply_markup=mk)

    state['last_results'] = state.get('last_results', {})
    state['last_results'][chat_id] = {
        'результати': [r for r in результати if r],
        'client_slug': active_slug,
    }

    if active_slug:
        try:
            clients.save_order(active_slug, результати, caption)
        except Exception as e:
            print(f"⚠️ Історія клієнта: {e}")

    username = items[0].get('username', str(chat_id)) if items else str(chat_id)
    log_usage(chat_id, username, total, len(знайдено), len(items))

    try:
        from catalog import storage
        storage.save_now()
    except Exception as e:
        print(f"⚠️ storage: {e}")


def _safe_edit(bot, chat_id, msg_id, text):
    """Редагує повідомлення — ковтає 'message is not modified'."""
    try:
        bot.edit_message_text(text, chat_id, msg_id)
    except Exception as e:
        if 'message is not modified' not in str(e) and 'message to edit not found' not in str(e):
            print(f"⚠️ safe_edit: {e}", flush=True)
