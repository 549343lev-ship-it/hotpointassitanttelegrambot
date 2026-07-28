"""
bot.py — Telegram бот підбору сантехніки (Hotpoint).

Тільки Telegram-логіка: хендлери, кнопки, process_batch.
Вся бізнес-логіка в окремих модулях:

  knowledge.py    — база знань (думає як менеджер)
  rules.py        — динамічні правила магазину
  ocr.py          — Gemini: читання фото/тексту/PDF
  search.py       — пошук у каталозі + Claude вибір
  catalog.py      — завантаження xlsx, токени
  excel_builder.py— генерація Excel
  cache.py        — кеш нормалізацій (TTL 60д, confidence≥95)
  clients.py      — профілі клієнтів
  storage.py      — збереження на GitHub
  logger.py       — статистика, діри каталогу
"""

import os
import re
import base64
import threading
import time

import telebot
from telebot.types import (InlineKeyboardMarkup, InlineKeyboardButton,
                            ReplyKeyboardMarkup, KeyboardButton)
import anthropic

# ─── Модулі проекту ──────────────────────────────────────────────────────────
from catalog import storage
storage.restore()

from clients.cache import (cache_lookup, cache_save, cache_confirm, cache_delete,
                          get_cache, get_cache_stats, cache_set_status,
                          cache_ban_pair, cache_cleanup_expired,
                          is_banned as cache_is_banned)
from clients import clients
from clients.pending_cache import (pending_add, pending_count, pending_get_batch,
                                   pending_confirm, pending_reject,
                                   pending_confirm_all_batch, pending_reject_all_batch,
                                   pending_clear_all)
from knowledge import rules as rules_module
from knowledge.rules import (get_rules, add_rule, delete_rule,
                             load_pending_rules, save_pending_rules, add_pending_rule)
from engine.ocr import (normalize_photo, normalize_text, normalize_pdf,
                        save_ocr_correction, load_ocr_corrections, parse_caption_brands)
from engine.search import (find_items, keyword_search, smart_search, build_qa,
                            BRAND_TOKENS, claude_pick_batch)
from engine.excel_builder import create_excel, parse_qty
from engine.logger import (log_usage, get_usage_stats, log_not_found, get_catalog_gaps)

# ─── Ініціалізація бота ──────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY", "")
ADMIN_ID       = 395121797

DATA_DIR           = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
PENDING_FIXES_FILE = os.path.join(DATA_DIR, "pending_fixes.json")
USAGE_LOG_FILE     = os.path.join(DATA_DIR, "usage_log.json")

_ExcBase = getattr(telebot, 'ExceptionHandler', object)

class _LogExc(_ExcBase):
    def handle(self, exception):    # перехоплює та логує винятки з хендлерів (без цього вони зникають у webhook-режимі)
        import traceback
        print(f"❌ ПОМИЛКА В ХЕНДЛЕРІ: {exception}", flush=True)
        traceback.print_exc()
        return True

try:
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False, exception_handler=_LogExc())
except TypeError:
    bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

claude = anthropic.Anthropic(api_key=ANTHROPIC_KEY)


def is_admin(uid: int) -> bool:     # перевіряє чи є користувач адміном за його Telegram ID
    return uid == ADMIN_ID


# ─── Виправлення (pending fixes) ─────────────────────────────────────────────

def load_pending_fixes() -> list:               # завантажує список виправлень що чекають підтвердження адміна
    import json
    if os.path.exists(PENDING_FIXES_FILE):
        try:
            with open(PENDING_FIXES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_pending_fixes(fixes: list):            # зберігає оновлений список виправлень на диск
    import json
    with open(PENDING_FIXES_FILE, "w", encoding="utf-8") as f:
        json.dump(fixes, f, ensure_ascii=False, indent=2)


def add_pending_fix(fix: dict) -> int:          # додає виправлення від користувача в чергу; повертає розмір черги
    fixes = load_pending_fixes()
    fix["date"] = time.strftime("%Y-%m-%d %H:%M")
    fixes.append(fix)
    save_pending_fixes(fixes)
    return len(fixes)


def apply_fix(fix: dict):   # застосовує підтверджене адміном виправлення: старий товар → banned, новий → confirmed
    """Застосовує підтверджене виправлення: старе → banned, нове → confirmed."""
    original = fix.get('original', '')
    cat      = fix.get('category', 'other')
    old      = fix.get('old_name')
    new      = fix.get('new_name')
    slug     = fix.get('client_slug')
    if old:
        cache_ban_pair(original, old, cat)
        if slug:
            clients.client_cache_set_status(slug, original, old, 'banned')
    if new:
        cache_confirm(original, {}, fix.get('normalized', original), new, cat)
        if slug:
            clients.client_cache_save(slug, original, new, cat, 100)
            clients.client_cache_set_status(slug, original, new, 'confirmed')


def notify_admin_fix(username, original, old_name, new_name, n):    # надсилає адміну повідомлення про нове виправлення від користувача
    try:
        bot.send_message(ADMIN_ID,
            f"🔔 Виправлення від @{username} (в черзі: {n})\n"
            f"«{(original or '')[:45]}»\n"
            f"❌ {(old_name or '—')[:55]}\n"
            f"✅ {(new_name or '(тільки заборонити старе)')[:55]}\n\n"
            f"Підтвердити: 👑 Правила на розгляд")
    except Exception:
        pass


# ─── Авто-генерація правила після навчання ───────────────────────────────────

_kn_pending = {}  # chat_id → згенероване правило що чекає ✅/❌


def suggest_knowledge_rule(chat_id, original, old_name, new_name):  # просить Claude сформулювати загальне правило з виправлення і пропонує адміну додати його в rules.txt
    try:
        prompt = (f"Менеджер-сантехнік виправив підбір товару.\n"
                  f"Написано в замовленні: «{original}»\n"
                  f"Бот вибрав (НЕПРАВИЛЬНО): «{old_name or '(не знайшов)'}»\n"
                  f"Правильна відповідь: «{new_name}»\n\n"
                  f"Сформулюй ОДНЕ коротке правило (до 15 слів) українською, яке допоможе "
                  f"боту наступного разу зрозуміти такий запит правильно. Правило має бути "
                  f"загальним (про термін/скорочення/тип), а не про цей конкретний рядок.\n"
                  f"Якщо корисного загального правила сформулювати не можна — напиши SKIP.\n"
                  f"Відповідь: тільки текст правила або SKIP.")
        resp = claude.messages.create(
            model="claude-sonnet-4-5", max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        rule = resp.content[0].text.strip().strip('"«»')
        if not rule or 'SKIP' in rule.upper() or len(rule) > 200:
            return
        _kn_pending[chat_id] = rule
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Додати в базу правил", callback_data="knok"),
               InlineKeyboardButton("❌ Ні", callback_data="knno"))
        bot.send_message(chat_id,
            f"💡 Бот пропонує нове правило з цього виправлення:\n\n_{rule}_\n\n"
            f"Додати? (потрапить у знання для всіх наступних розпізнавань)",
            parse_mode="Markdown", reply_markup=mk)
    except Exception as e:
        print(f"⚠️ suggest_rule: {e}")


# ─── Стани ───────────────────────────────────────────────────────────────────

user_batches  = {}
stop_flags    = {}
pending_hints = {}
last_results  = {}
_fix_state    = {}
_train_state  = {}
_text_pending = {}
_manual_wait  = {}
BATCH_TIMEOUT = 4


def safe_edit(chat_id, msg_id, text):   # редагує повідомлення-статус; ковтає помилки якщо повідомлення вже видалено або не змінилось
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass


# ─── Авто-розгортання PUSH / +ізол / гільзи ──────────────────────────────────

INSUL_DIA_MAP = {16: 18, 20: 22, 25: 28, 32: 35, 40: 42}  # мапінг: діаметр труби → діаметр утеплювача


def _qty_num(qty_str):  # витягує число з рядка кількості ("10 шт" → 10.0); повертає 0 якщо не вдалось
    m = re.search(r'(\d+(?:[.,]\d+)?)', str(qty_str or ''))
    return float(m.group(1).replace(',', '.')) if m else 0


def expand_push_marker(позиції):    # якщо в списку є слово "гільзи" — переводить всю PPR/metal_plastic категорію у push_systems
    has_sleeve = any('гільз' in (п.get('original', '') + п.get('normalized', '')).lower()
                     for п in позиції)
    if not has_sleeve:
        return позиції
    for п in позиції:
        if п.get('category') in ('plastic_ppr', 'metal_plastic', 'other'):
            п['category'] = 'push_systems'
            n = п.get('normalized', '')
            if 'push' not in n.lower() and 'натяжн' not in n.lower():
                п['normalized'] = (n + ' натяжний PUSH').strip()
    return позиції


def expand_insulation(позиції):     # розгортає "+ ізол" в окремі позиції утеплювача PLM (синій + червоний по половині метражу)
    out = []
    for п in позиції:
        out.append(п)
        orig = п.get('original', '').lower()
        qa   = п.get('_qa') or build_qa(п)
        п['_qa'] = qa
        if qa.get('type') == 'труба' and re.search(r'ізол|изол|утепл', orig):
            dia     = (qa.get('dia') or [None])[0]
            ins_dia = INSUL_DIA_MAP.get(dia)
            m_total = _qty_num(п.get('qty'))
            if ins_dia and m_total > 0:
                half   = m_total / 2
                half_s = str(int(half)) if half == int(half) else f"{half:.1f}"
                for color in ('синій', 'червоний'):
                    out.append({
                        'original':   f"(авто +ізол) утеплювач ф{ins_dia} {color}",
                        'normalized': f"Утеплювач ламін. для труб ф {ins_dia}х6 {color} PLM",
                        'qty':        f"{half_s} м",
                        'category':   'insulation',
                        'type':       'утеплювач',
                        'dia':        [ins_dia],
                        'section':    п.get('section', ''),
                    })
    return out


def _push_outlets(п):   # визначає скільки трубних виходів має PUSH-фітинг (коліно=2, трійник=3 тощо) для розрахунку гільз
    from engine.search import TYPE_SYNONYMS
    qa  = п.get('_qa') or build_qa(п)
    п['_qa'] = qa
    typ = qa.get('type')
    text = f"{п.get('normalized', '')} {п.get('original', '')}"
    g   = re.search(r'(\d{2})\s*[хx×]\s*(\d{2})(?:\s*[хx×]\s*(\d{2}))?', text)
    dims = [int(x) for x in g.groups() if x] if g else list(qa.get('dia') or [])
    has_thread = bool(qa.get('thread')) or bool(re.search(r'мрз|мрв|рз|вр|різьб', text.lower()))
    if typ == 'трійник':
        outs = dims if len(dims) == 3 else (dims * 3)[:3] if dims else []
    elif typ in ('коліно',):
        outs = dims if len(dims) == 2 else (dims * 2)[:2] if dims else []
    elif typ in ('муфта', 'перехід'):
        outs = dims[:1] if has_thread else (dims if len(dims) == 2 else (dims * 2)[:2] if dims else [])
    elif typ == 'заглушка':
        outs = dims[:1]
    else:
        outs = []
    return outs


def expand_push_sleeves(позиції):   # автоматично додає гільзи до PUSH-фітингів якщо майстер їх не вписав окремо
    if any('гільз' in (п.get('original', '') + п.get('normalized', '')).lower()
           and (п.get('_qa') or build_qa(п)).get('type') == 'гільза'
           for п in позиції):
        return позиції
    sleeves = {}
    for п in позиції:
        if п.get('category') != 'push_systems':
            continue
        outs = _push_outlets(п)
        if not outs:
            continue
        n_fit = int(_qty_num(п.get('qty')) or 1)
        for d in outs:
            sleeves[d] = sleeves.get(d, 0) + n_fit
    for d in sorted(sleeves):
        позиції.append({
            'original':   f"(авто) гільзи ф{d} до PUSH-фітингів",
            'normalized': f"Гільза натяжна ф {d} PUSH",
            'qty':        f"{sleeves[d]} шт",
            'category':   'push_systems',
            'type':       'гільза',
            'dia':        [d],
        })
    return позиції


# ─── process_batch ────────────────────────────────────────────────────────────

def process_batch(chat_id: int):    # головний оркестратор: збирає всі файли батчу, нормалізує OCR, розгортає PUSH/ізол, шукає товари, формує Excel
    batch = user_batches.pop(chat_id, None)
    if not batch:
        return
    stop_flags.pop(chat_id, None)
    items = batch['items']

    active_slug  = clients.get_active(chat_id)
    client_prefs = clients.get_preferences(active_slug) if active_slug else {}
    client_name  = ''
    if active_slug:
        _p = clients.get_profile(active_slug)
        client_name = _p['name'] if _p else active_slug
    client_line = f"\n👤 Клієнт: {client_name}" if client_name else ""

    status = bot.send_message(chat_id, f"⏳ Обробляю {len(items)} файл(ів)...{client_line}")
    msg_id = status.message_id

    всі_позиції, errors = [], []

    for idx, item in enumerate(items, 1):
        if stop_flags.get(chat_id):
            safe_edit(chat_id, msg_id, "🛑 Зупинено.")
            return
        try:
            safe_edit(chat_id, msg_id, f"📖 Читаю файл {idx}/{len(items)}...")
            if item['type'] == 'photo':
                позиції = normalize_photo(item['data'], item.get('caption', ''))
            elif item['type'] == 'pdf':
                позиції = normalize_pdf(item['data'], item.get('caption', ''))
            elif item['type'] == 'text':
                позиції = normalize_text(item['text'], item.get('caption', ''))
            else:
                позиції = []
            всі_позиції.extend(позиції)
        except Exception as e:
            errors.append(f"❌ Файл {idx}: {e}")

    if not всі_позиції:
        safe_edit(chat_id, msg_id, "😕 Не розпізнано позицій.\n" + "\n".join(errors))
        return

    всі_позиції = expand_push_marker(всі_позиції)
    всі_позиції = expand_insulation(всі_позиції)
    всі_позиції = expand_push_sleeves(всі_позиції)

    all_captions = [it.get('caption', '') for it in items if it.get('caption', '')]
    caption      = ' | '.join(all_captions)
    brand_map    = parse_caption_brands(caption)
    for п in всі_позиції:
        п['_brand_map'] = brand_map
        if active_slug:
            п['_client_slug']  = active_slug
            п['_client_prefs'] = client_prefs

    safe_edit(chat_id, msg_id, f"🔍 Шукаю {len(всі_позиції)} позицій...")

    def progress(cur, total):   # колбек прогресу: оновлює статус-повідомлення кожні 5 позицій
        if cur % 5 == 0 or cur == total:
            safe_edit(chat_id, msg_id, f"🔍 Пошук: {cur}/{total}...")

    результати = find_items(всі_позиції, progress_cb=progress)

    for _r, _п in zip(результати, всі_позиції):
        if _r is not None:
            _r.setdefault('розділ', _п.get('section', ''))

    log_not_found([r for r in результати if r and not r.get('знайдено')])

    safe_edit(chat_id, msg_id, "📊 Формую Excel...")
    excel, not_found, warn = create_excel(результати)

    знайдено = [r for r in результати if r and r.get('знайдено')]
    total    = len([r for r in результати if r])

    bot.send_document(chat_id, excel, visible_file_name="замовлення.xlsx")

    звіт = f"✅ Знайдено: {len(знайдено)}/{total}\n"
    if not_found: звіт += f"🟥 Не знайдено: {len(not_found)}\n"
    if warn:      звіт += f"🟨 Перевір: {len(warn)}\n"
    if errors:    звіт += "\n" + "\n".join(errors)
    safe_edit(chat_id, msg_id, звіт)

    mk = InlineKeyboardMarkup()
    mk.add(InlineKeyboardButton("🎓 Навчання", callback_data="tr_go"),
           InlineKeyboardButton("✖️ Закрити",  callback_data="tr_close"))
    note = "" if chat_id == ADMIN_ID else "\n(твої виправлення підтверджує адмін)"
    bot.send_message(chat_id, f"Перевір файл. Якщо є помилки — тапни Навчання:{note}",
                     reply_markup=mk)

    last_results[chat_id] = {'результати': [r for r in результати if r], 'client_slug': active_slug}
    if active_slug:
        try:
            clients.save_order(active_slug, результати, caption)
        except Exception as e:
            print(f"⚠️ Історія клієнта: {e}")
    username = items[0].get('username', str(chat_id)) if items else str(chat_id)
    log_usage(chat_id, username, total, len(знайдено), len(items))
    try:
        storage.save_now()
    except Exception as _e:
        print(f"⚠️ storage: {_e}")


def add_to_batch(chat_id: int, item: dict):     # додає файл/текст у батч і (пере)запускає таймер 4 сек; після таймауту запускає process_batch
    if chat_id not in user_batches:
        user_batches[chat_id] = {'items': []}
        bot.send_message(chat_id, "📥 Отримав! Чекаю ще файли (4 сек)...")
    if 'timer' in user_batches[chat_id]:
        user_batches[chat_id]['timer'].cancel()
    user_batches[chat_id]['items'].append(item)
    t = threading.Timer(BATCH_TIMEOUT, process_batch, args=[chat_id])
    user_batches[chat_id]['timer'] = t
    t.daemon = True
    t.start()


# ─── Клавіатура ──────────────────────────────────────────────────────────────

def main_keyboard(uid: int) -> ReplyKeyboardMarkup:     # будує головну клавіатуру; адміни бачать додаткові кнопки статистики і логів
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("📸 Як користуватись"), KeyboardButton("🛑 Стоп"))
    kb.add(KeyboardButton("📋 Правило"), KeyboardButton("📊 Кеш"))
    kb.add(KeyboardButton("👥 Клієнти"), KeyboardButton("👥 Кеш клієнта"))
    if is_admin(uid):
        kb.add(KeyboardButton("👑 Статистика"), KeyboardButton("👑 Правила на розгляд"))
        kb.add(KeyboardButton("👑 Логи"),       KeyboardButton("👑 Діри каталогу"))
        kb.add(KeyboardButton("👑 Перевір кеш"))
    return kb


# ═══════════════════════════════════════════════════════════════════════════════
# ХЕНДЛЕРИ
# ═══════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['start', 'help'])
def handle_start(message):      # відповідає на /start і /help: показує інструкцію і головну клавіатуру
    admin      = is_admin(message.from_user.id)
    admin_note = (
        "\n\n👑 *Адмін:* твої виправлення застосовуються одразу. "
        "Чужі — чекають у «👑 Правила на розгляд»."
        if admin else
        "\n\n_Твої виправлення і правила підтверджує адмін._"
    )
    bot.reply_to(message, f"""👋 Привіт! Я підбираю сантехніку з бази по фото списку.

*📋 ЯК ПРАЦЮВАТИ (3 кроки):*
1️⃣ Напиши виробників (кожен рядок = категорія):
`каналізація остендорф`
`пайка екопластик`
`крани рафтек`
2️⃣ Кинь фото рукописного списку (можна кілька)
3️⃣ Отримай Excel: 🟥 не знайдено, 🟨 перевір

*👤 ПОСТІЙНІ КЛІЄНТИ:*
`новий клієнт Петренко` — створити профіль
`клієнт Петренко` — активуй ПЕРЕД фото
`клієнт стоп` — вимкнути | `клієнти` — список

*🎓 ЯКЩО БОТ ПОМИЛИВСЯ:*
Тапни «🎓 Навчання» → номери рядків → причину → правильний варіант.{admin_note}

`пошук <текст>` — підбір без фото | /stop — зупинити""",
        parse_mode="Markdown",
        reply_markup=main_keyboard(message.from_user.id))


@bot.message_handler(func=lambda m: m.text == "📸 Як користуватись")
def kb_howto(message):      # показує повну покрокову інструкцію з клавіатури
    bot.reply_to(message, """📸 *ПОВНА ІНСТРУКЦІЯ*

*Крок 1. Клієнт (якщо постійний):*
`клієнт Петренко` — бот згадає всі його минулі замовлення.
Новий? → `новий клієнт Петренко`

*Крок 2. Виробники:*
`каналізація остендорф`
`пайка екопластик`
Або: `усе рафтек`

*Крок 3. Фото:*
Кинь фото (можна кілька — почекай 4 сек).

*Крок 4. Перевір Excel:*
🟥 червоний = не знайдено
🟨 жовтий = перевір
Колонка «Джерело» показує звідки вибір.

*Крок 5. Навчи якщо є помилки:*
Тапни «🎓 Навчання» → номери рядків → причину → правильний товар.""",
    parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text in ("🛑 Стоп", "🛑 стоп"))
def kb_stop(message):       # кнопка "Стоп" — зупиняє поточну обробку
    handle_stop(message)


@bot.message_handler(func=lambda m: m.text in ("📋 Правило",))
def kb_rule_btn(message):   # підказує синтаксис команди "правило"
    bot.reply_to(message, "Напиши: `правило <текст>`\nПриклад: `правило рожон = трійник`",
                 parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == "📊 Кеш")
def kb_cache(message):      # кнопка "Кеш" — показує статистику і останні записи кешу
    handle_cache_info(message)


@bot.message_handler(func=lambda m: m.text == "👥 Клієнти")
def kb_clients(message):    # кнопка "Клієнти" — показує список всіх клієнтів
    handle_clients_list(message)


@bot.message_handler(func=lambda m: m.text == "👥 Кеш клієнта")
def kb_client_cache(message):   # показує останні 10 записів кешу активного клієнта
    slug = clients.get_active(message.chat.id)
    if not slug:
        bot.reply_to(message, "Немає активного клієнта.\nАктивуй: `клієнт <ім'я>`",
                     parse_mode="Markdown")
        return
    p     = clients.get_profile(slug)
    name  = p['name'] if p else slug
    cache = clients.get_client_cache(slug)
    if not cache:
        bot.reply_to(message, f"👥 Кеш *{name}* порожній.", parse_mode="Markdown")
        return
    icons = {'confirmed': '✅', 'banned': '❌', 'auto': '🔹'}
    lines = []
    for k, v in list(cache.items())[-10:]:
        lines.append(f"{icons.get(v.get('status', 'auto'), '🔹')} `{k[:35]}` → {v.get('catalog_name', '')[:45]}")
    bot.reply_to(message,
        f"👥 Кеш *{name}*: {len(cache)} записів\n✅ підтв | ❌ бан | 🔹 авто\n\n" + "\n".join(lines),
        parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == "👑 Статистика")
def kb_stats(message):      # адмін: показує статистику використання по всіх користувачах
    if not is_admin(message.from_user.id): return
    bot.reply_to(message, get_usage_stats(), parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text == "👑 Логи")
def kb_logs(message):       # адмін: надсилає файл usage_log.json для аналізу
    if not is_admin(message.from_user.id): return
    import os as _os
    if not _os.path.exists(USAGE_LOG_FILE):
        bot.reply_to(message, "Лог порожній.")
        return
    with open(USAGE_LOG_FILE, "rb") as f:
        bot.send_document(message.chat.id, f, visible_file_name="usage_log.json")


@bot.message_handler(func=lambda m: m.text == "👑 Діри каталогу")
def kb_gaps(message):       # адмін: показує топ-20 незнайдених позицій — що варто додати в прайси
    if not is_admin(message.from_user.id): return
    bot.reply_to(message, get_catalog_gaps())


@bot.message_handler(commands=['діри', 'gaps'])
def handle_gaps(message):   # /діри або /gaps — те саме що кнопка "Діри каталогу"
    if not is_admin(message.from_user.id): return
    bot.reply_to(message, get_catalog_gaps())


@bot.message_handler(func=lambda m: m.text == "👑 Правила на розгляд")
def kb_pending(message):    # адмін: показує всі виправлення і правила що очікують підтвердження
    if not is_admin(message.from_user.id): return
    show_pending_rules(message.chat.id)


def show_pending_rules(chat_id):    # надсилає список pending rules і pending fixes з кнопками підтвердити/відхилити
    rules  = load_pending_rules()
    fixes  = load_pending_fixes()
    if not rules and not fixes:
        bot.send_message(chat_id, "✅ Немає нічого на розгляд.")
        return
    for i, r in enumerate(rules):
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Підтвердити", callback_data=f"approve_{i}"),
               InlineKeyboardButton("❌ Відхилити",   callback_data=f"reject_{i}"))
        bot.send_message(chat_id,
            f"📋 Правило #{i+1} від {r['username']} ({r['date']}):\n{r['rule']}",
            reply_markup=mk)
    for i, f in enumerate(fixes):
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("✅ Застосувати", callback_data=f"fixok_{i}"),
               InlineKeyboardButton("❌ Відхилити",   callback_data=f"fixno_{i}"))
        bot.send_message(chat_id,
            f"🎓 Виправлення #{i+1} від {f['username']} ({f.get('date', '')}):\n"
            f"«{f.get('original', '')[:45]}»\n"
            f"❌ {(f.get('old_name') or '—')[:55]}\n"
            f"✅ {(f.get('new_name') or '(тільки заборонити старе)')[:55]}",
            reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith(('approve_', 'reject_')))
def handle_rule_decision(call):     # адмін підтверджує або відхиляє правило від користувача; підтверджене додається в rules.txt
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    action, idx = call.data.split('_')
    idx   = int(idx)
    rules = load_pending_rules()
    if idx >= len(rules):
        bot.answer_callback_query(call.id, "Вже оброблено"); return
    rule = rules.pop(idx)
    save_pending_rules(rules)
    if action == 'approve':
        add_rule(rule['rule'])
        bot.edit_message_text(f"✅ ПІДТВЕРДЖЕНО:\n`{rule['rule']}`",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        try:
            bot.send_message(rule['user_id'], f"✅ Твоє правило підтверджено:\n`{rule['rule']}`",
                             parse_mode="Markdown")
        except Exception: pass
    else:
        bot.edit_message_text(f"❌ ВІДХИЛЕНО:\n`{rule['rule']}`",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.answer_callback_query(call.id, "Готово")


@bot.callback_query_handler(func=lambda c: c.data.startswith(('fixok_', 'fixno_')))
def handle_fixq_decision(call):     # адмін підтверджує або відхиляє виправлення товару від користувача
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    action, idx = call.data.split('_')
    idx   = int(idx)
    fixes = load_pending_fixes()
    if idx >= len(fixes):
        bot.answer_callback_query(call.id, "Вже оброблено"); return
    fix = fixes.pop(idx)
    save_pending_fixes(fixes)
    who = fix.get('username', '?')
    if action == 'fixok':
        apply_fix(fix)
        bot.edit_message_text(
            f"✅ ЗАСТОСОВАНО (від @{who}):\n«{fix.get('original', '')[:40]}»\n"
            f"❌ {(fix.get('old_name') or '—')[:50]}\n✅ {(fix.get('new_name') or 'бан')[:50]}",
            call.message.chat.id, call.message.message_id)
        try:
            bot.send_message(fix['user_id'],
                f"✅ Твоє виправлення підтверджено адміном:\n"
                f"«{fix.get('original', '')[:40]}» → {(fix.get('new_name') or 'заборонено')[:55]}")
        except Exception: pass
    else:
        bot.edit_message_text(
            f"❌ ВІДХИЛЕНО (від @{who}):\n«{fix.get('original', '')[:40]}»",
            call.message.chat.id, call.message.message_id)
        try:
            bot.send_message(fix['user_id'],
                f"❌ Твоє виправлення відхилено адміном:\n«{fix.get('original', '')[:40]}»")
        except Exception: pass
    bot.answer_callback_query(call.id, "Готово")


@bot.callback_query_handler(func=lambda c: c.data in ("knok", "knno"))
def handle_knowledge_decision(call):    # адмін приймає або відхиляє правило запропоноване Claude після навчання
    rule = _kn_pending.pop(call.message.chat.id, None)
    if call.data == "knok" and rule:
        add_rule(rule)
        try:
            bot.edit_message_text(f"✅ Додано в базу правил:\n_{rule}_",
                call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        except Exception: pass
        bot.answer_callback_query(call.id, "Збережено")
    else:
        try:
            bot.edit_message_text("❌ Пропущено.", call.message.chat.id, call.message.message_id)
        except Exception: pass
        bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ocrs_"))
def handle_ocr_pair_save(call):     # зберігає пару корекції почерку запропоновану після OCR-помилки
    st    = _train_state.get(call.message.chat.id)
    pairs = (st or {}).get('ocr_pairs') or []
    idx   = int(call.data[5:])
    if idx >= len(pairs):
        bot.answer_callback_query(call.id, "Застаріло"); return
    w, rt = pairs[idx]
    save_ocr_correction(w, rt)
    bot.answer_callback_query(call.id, f"Збережено: {w}→{rt}")
    try:
        bot.edit_message_text(f"✅ Корекція почерку збережена: «{w}» → «{rt}»",
            call.message.chat.id, call.message.message_id)
    except Exception: pass


# ─── Навчання (tr_*) ─────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data in ("tr_go", "tr_close"))
def tr_start(call):     # запускає сесію навчання: просить ввести номери неправильних рядків
    if call.data == "tr_close":
        try: bot.edit_message_text("✖️ Закрито.", call.message.chat.id, call.message.message_id)
        except Exception: pass
        bot.answer_callback_query(call.id); return
    _train_state[call.message.chat.id] = {'stage': 'rows'}
    try:
        bot.edit_message_text("✍️ Напиши номери НЕПРАВИЛЬНИХ рядків через пробіл (напр: 3 7 12):",
            call.message.chat.id, call.message.message_id)
    except Exception: pass
    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda m: m.text and m.chat.id in _train_state
                     and _train_state[m.chat.id].get('stage') == 'rows'
                     and re.fullmatch(r'[\d\s,]+', m.text.strip()))
def tr_rows(message):   # отримує номери рядків від користувача і переходить до класифікації помилок
    last  = last_results.get(message.chat.id)
    total = len(last['результати']) if last else 0
    nums  = sorted({int(x) for x in re.findall(r'\d+', message.text)
                    if 1 <= int(x) <= total})
    if not nums:
        bot.reply_to(message, f"⚠️ Валідних номерів немає (всього {total} рядків).")
        return
    _train_state[message.chat.id] = {'stage': 'classify', 'rows': nums, 'i': 0}
    _tr_show_row(message.chat.id)


def _tr_show_row(chat_id):  # показує поточний рядок навчання з кнопками причини помилки (OCR/товар не той/пропустити)
    st   = _train_state.get(chat_id)
    last = last_results.get(chat_id)
    if not st or not last: return
    if st['i'] >= len(st['rows']):
        bot.send_message(chat_id, f"✅ Навчання завершено! Оброблено {len(st['rows'])} рядків.")
        _train_state.pop(chat_id, None); return
    row = st['rows'][st['i']]
    r   = last['результати'][row - 1]
    mk  = InlineKeyboardMarkup(row_width=1)
    mk.add(InlineKeyboardButton("📖 Бот неправильно ПРОЧИТАВ рядок", callback_data="tro"),
           InlineKeyboardButton("🎯 Прочитав вірно, товар НЕ ТОЙ",   callback_data="trg"),
           InlineKeyboardButton("⏭ Пропустити",                      callback_data="trs"))
    m = bot.send_message(chat_id,
        f"🎓 Рядок {row} ({st['i']+1}/{len(st['rows'])})\n"
        f"Написано: {r.get('original', '')[:50]}\n"
        f"Бот дав: {(r.get('назва', '') or '❓ не знайдено')[:60]}\n\nЩо не так?",
        reply_markup=mk)
    st['msg_id'] = m.message_id


@bot.callback_query_handler(func=lambda c: c.data in ("tro", "trg", "trw", "trb", "trc", "trs", "trn", "trm")
                             or c.data.startswith("trp_"))
def tr_classify(call):  # обробляє вибір причини помилки в навчанні: OCR-помилка / товар не той / пропустити / вибрати кандидата
    chat_id = call.message.chat.id
    st      = _train_state.get(chat_id)
    last    = last_results.get(chat_id)
    if not st or not last:
        bot.answer_callback_query(call.id, "Сесія застаріла"); return
    admin    = is_admin(call.from_user.id)
    row      = st['rows'][st['i']]
    r        = last['результати'][row - 1]
    original = r.get('original', '')
    old_name = r.get('назва', '')
    cat      = r.get('category', 'other')
    cslug    = last.get('client_slug')
    uname    = call.from_user.username or str(call.from_user.id)

    def advance():  # переходить до наступного рядка навчання або завершує сесію
        st['i'] += 1
        _tr_show_row(chat_id)

    if call.data == "trs":
        bot.answer_callback_query(call.id, "Пропущено"); advance(); return

    if call.data == "trm":
        _manual_wait[chat_id] = {'mode': 'train'}
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id,
            "✍️ Напиши назву товару (як у прайсі, можна частину):\n"
            "напр: `коліно 110 45 остендорф`", parse_mode="Markdown")
        return

    if call.data == "trn":
        if old_name:
            if admin:
                cache_ban_pair(original, old_name, cat)
                if cslug: clients.client_cache_set_status(cslug, original, old_name, 'banned')
                bot.answer_callback_query(call.id, "❌ Забанено")
            else:
                n = add_pending_fix({'original': original, 'old_name': old_name,
                                     'new_name': None, 'category': cat, 'client_slug': cslug,
                                     'normalized': r.get('normalized', ''),
                                     'user_id': call.from_user.id, 'username': uname})
                notify_admin_fix(uname, original, old_name, None, n)
                bot.answer_callback_query(call.id, "📥 Надіслано адміну")
        else:
            bot.answer_callback_query(call.id, "Ок")
        advance(); return

    if call.data.startswith("trp_"):
        idx   = int(call.data[4:])
        cands = st.get('cands', [])
        if idx >= len(cands):
            bot.answer_callback_query(call.id, "Застаріло"); return
        new_name  = cands[idx]
        save_orig = st.pop('ocr_new_original', None) or original
        if admin:
            if old_name:
                cache_ban_pair(original, old_name, cat)
                if cslug: clients.client_cache_set_status(cslug, original, old_name, 'banned')
            cache_confirm(save_orig, {}, r.get('normalized', save_orig), new_name, cat)
            if save_orig != original:
                cache_confirm(original, {}, save_orig, new_name, cat)
            if cslug:
                clients.client_cache_save(cslug, save_orig, new_name, cat, 100)
                clients.client_cache_set_status(cslug, save_orig, new_name, 'confirmed')
            r['назва'] = new_name
            try:
                bot.edit_message_text(
                    f"✅ Навчено!\n{original[:40]}\n❌ {old_name[:50] or '—'}\n✅ {new_name[:60]}",
                    chat_id, st.get('msg_id', call.message.message_id))
            except Exception: pass
            bot.answer_callback_query(call.id, "✅ Збережено")
            suggest_knowledge_rule(chat_id, original, old_name, new_name)
        else:
            n = add_pending_fix({'original': original, 'old_name': old_name or None,
                                 'new_name': new_name, 'category': cat, 'client_slug': cslug,
                                 'normalized': r.get('normalized', ''),
                                 'user_id': call.from_user.id, 'username': uname})
            notify_admin_fix(uname, original, old_name, new_name, n)
            try:
                bot.edit_message_text(
                    f"📥 Надіслано адміну (черга: {n})\n{original[:40]}\n"
                    f"❌ {old_name[:50] or '—'}\n✅ {new_name[:60]}",
                    chat_id, st.get('msg_id', call.message.message_id))
            except Exception: pass
            bot.answer_callback_query(call.id, "📥 На розгляді")
        advance(); return

    if call.data == "tro":
        _manual_wait[chat_id] = {'mode': 'ocr_fix'}
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id,
            f"📖 Бот прочитав з фото:\n«{original[:60]}»\n\n"
            f"✍️ Напиши як НАСПРАВДІ написано в списку:")
        return

    if call.data in ("trg", "trw", "trb", "trc"):
        if old_name and admin:
            cache_ban_pair(original, old_name, cat)
            if cslug: clients.client_cache_set_status(cslug, original, old_name, 'banned')
        seen = [c for c in (r.get('candidates_debug') or []) if c and c != old_name][:6]
        if not seen:
            _manual_wait[chat_id] = {'mode': 'train'}
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, "✍️ Бот не мав кандидатів. Напиши назву товару з бази:")
            return
        st['cands'] = seen
        mk = InlineKeyboardMarkup(row_width=1)
        for i2, name in enumerate(seen):
            mk.add(InlineKeyboardButton(f"{i2+1}. {name[:55]}", callback_data=f"trp_{i2}"))
        mk.add(InlineKeyboardButton("✍️ Немає тут — ввести з бази", callback_data="trm"))
        mk.add(InlineKeyboardButton("❌ Немає правильного (бан)",     callback_data="trn"))
        hdr = "❌ Забанено" if admin else "❌ Позначено"
        try:
            bot.edit_message_text(
                f"🎯 Рядок {row}: {original[:45]}\n{hdr}: {old_name[:55] or '—'}\n\n"
                f"Бот розглядав ці варіанти — тапни ПРАВИЛЬНИЙ:",
                chat_id, st.get('msg_id', call.message.message_id), reply_markup=mk)
        except Exception:
            m2 = bot.send_message(chat_id, "Тапни правильний:", reply_markup=mk)
            st['msg_id'] = m2.message_id
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)


# ─── Вірно / Помилка / Виправ ────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text and re.match(r'^вірно\s+\d+', m.text.lower()))
def handle_virno(message):  # "вірно N" — підтверджує правильність рядка N; адмін одразу, інші → черга
    row  = int(re.search(r'\d+', message.text).group())
    last = last_results.get(message.chat.id)
    if not last:
        bot.reply_to(message, "⚠️ Немає замовлення в пам'яті."); return
    if row < 1 or row > len(last['результати']):
        bot.reply_to(message, f"⚠️ Рядок {row} не існує."); return
    r        = last['результати'][row - 1]
    original = r.get('original', '')
    назва    = r.get('назва', '')
    cat      = r.get('category', 'other')
    if not назва:
        bot.reply_to(message, "⚠️ Цей рядок не знайдено — використай `виправ N`",
                     parse_mode="Markdown"); return
    if is_admin(message.from_user.id):
        if not cache_set_status(original, назва, 'confirmed'):
            cache_confirm(original, {}, r.get('normalized', original), назва, cat)
        if last.get('client_slug'):
            clients.client_cache_save(last['client_slug'], original, назва, cat, 100)
            clients.client_cache_set_status(last['client_slug'], original, назва, 'confirmed')
        bot.reply_to(message, f"✅ Рядок {row} підтверджено:\n{назва[:60]}")
    else:
        uname = message.from_user.username or str(message.from_user.id)
        n = add_pending_fix({'original': original, 'old_name': None, 'new_name': назва,
                             'category': cat, 'client_slug': last.get('client_slug'),
                             'normalized': r.get('normalized', ''),
                             'user_id': message.from_user.id, 'username': uname})
        notify_admin_fix(uname, original, None, назва, n)
        bot.reply_to(message, f"📥 Підтвердження рядка {row} надіслано адміну (черга: {n})")


@bot.message_handler(func=lambda m: m.text and re.match(r'^помилка\s+\d+', m.text.lower()))
def handle_pomylka(message):    # "помилка N" — банить товар рядка N; адмін одразу, інші → черга
    row  = int(re.search(r'\d+', message.text).group())
    last = last_results.get(message.chat.id)
    if not last:
        bot.reply_to(message, "⚠️ Немає замовлення в пам'яті."); return
    if row < 1 or row > len(last['результати']):
        bot.reply_to(message, f"⚠️ Рядок {row} не існує."); return
    r        = last['результати'][row - 1]
    original = r.get('original', '')
    назва    = r.get('назва', '')
    cat      = r.get('category', 'other')
    if not назва:
        bot.reply_to(message, "⚠️ Рядок і так не знайдено."); return
    if is_admin(message.from_user.id):
        cache_ban_pair(original, назва, cat)
        if last.get('client_slug'):
            clients.client_cache_set_status(last['client_slug'], original, назва, 'banned')
        bot.reply_to(message, f"❌ Рядок {row} забанено:\n{назва[:60]}")
    else:
        uname = message.from_user.username or str(message.from_user.id)
        n = add_pending_fix({'original': original, 'old_name': назва, 'new_name': None,
                             'category': cat, 'client_slug': last.get('client_slug'),
                             'normalized': r.get('normalized', ''),
                             'user_id': message.from_user.id, 'username': uname})
        notify_admin_fix(uname, original, назва, None, n)
        bot.reply_to(message, f"📥 Позначку помилки рядка {row} надіслано адміну (черга: {n})")


@bot.message_handler(func=lambda m: m.text and re.match(r'^виправ\s+\d+', m.text.lower().strip()))
def handle_fix(message):    # "виправ N" або "виправ N = текст" — показує альтернативні кандидати для рядка N
    m_re   = re.match(r'^виправ\s+(\d+)(?:\s*=\s*(.+))?$', message.text.strip(), re.IGNORECASE)
    row    = int(m_re.group(1))
    manual = (m_re.group(2) or '').strip()
    last   = last_results.get(message.chat.id)
    if not last or not last['результати']:
        bot.reply_to(message, "⚠️ Немає замовлення в пам'яті."); return
    if row < 1 or row > len(last['результати']):
        bot.reply_to(message, f"⚠️ Рядок {row} не існує (всього {len(last['результати'])})."); return
    r     = last['результати'][row - 1]
    query = manual or r.get('normalized') or r.get('original', '')
    cur   = r.get('назва', '')
    cands = [c['name'] for c in keyword_search(query, top_n=9) if c['name'] != cur][:8]
    if not cands:
        bot.reply_to(message, "😕 Кандидатів немає. Спробуй: `виправ N = інший текст`",
                     parse_mode="Markdown"); return
    _fix_state[message.chat.id] = {'row': row, 'cands': cands}
    mk = InlineKeyboardMarkup(row_width=1)
    for i, name in enumerate(cands):
        mk.add(InlineKeyboardButton(f"{i+1}. {name[:55]}", callback_data=f"fx_{i}"))
    mk.add(InlineKeyboardButton("✍️ Ввести назву вручну", callback_data="fx_man"))
    mk.add(InlineKeyboardButton("❌ Немає правильного (тільки бан)", callback_data="fx_ban"))
    bot.reply_to(message,
        f"🎓 Рядок {row}: `{r.get('original', '')[:45]}`\n"
        f"Зараз: {cur[:60] or '(не знайдено)'}\n\nТапни ПРАВИЛЬНИЙ:",
        parse_mode="Markdown", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith('fx_'))
def handle_fix_pick(call):  # обробляє вибір правильного товару з кандидатів команди "виправ"; банить старий, зберігає новий
    if call.data == "fx_man":
        st0 = _fix_state.get(call.message.chat.id)
        if not st0:
            bot.answer_callback_query(call.id, "Сесія застаріла"); return
        _manual_wait[call.message.chat.id] = {'mode': 'fix'}
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id,
            "✍️ Напиши назву товару (як у прайсі, можна частину):\n"
            "напр: `коліно 110 45 остендорф`", parse_mode="Markdown")
        return

    st   = _fix_state.pop(call.message.chat.id, None)
    last = last_results.get(call.message.chat.id)
    if not st or not last:
        bot.answer_callback_query(call.id, "Сесія застаріла"); return

    admin    = is_admin(call.from_user.id)
    r        = last['результати'][st['row'] - 1]
    original = r.get('original', '')
    old_name = r.get('назва', '')
    cat      = r.get('category', 'other')
    cslug    = last.get('client_slug')
    uname    = call.from_user.username or str(call.from_user.id)

    if call.data == "fx_ban":
        if not old_name:
            bot.answer_callback_query(call.id, "Нема чого банити"); return
        if admin:
            cache_ban_pair(original, old_name, cat)
            if cslug: clients.client_cache_set_status(cslug, original, old_name, 'banned')
            bot.edit_message_text(f"❌ Забанено: {original[:40]} → {old_name[:50]}",
                call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id, "Забанено")
        else:
            n = add_pending_fix({'original': original, 'old_name': old_name, 'new_name': None,
                                 'category': cat, 'client_slug': cslug,
                                 'normalized': r.get('normalized', ''),
                                 'user_id': call.from_user.id, 'username': uname})
            notify_admin_fix(uname, original, old_name, None, n)
            bot.edit_message_text(f"📥 Надіслано адміну (черга: {n}):\nзаборонити {old_name[:50]}",
                call.message.chat.id, call.message.message_id)
            bot.answer_callback_query(call.id, "📥 На розгляді")
        return

    idx = int(call.data[3:])
    if idx >= len(st.get('cands', [])):
        bot.answer_callback_query(call.id, "Застаріло"); return
    new_name = st['cands'][idx]

    if admin:
        if old_name:
            cache_ban_pair(original, old_name, cat)
            if cslug: clients.client_cache_set_status(cslug, original, old_name, 'banned')
        cache_confirm(original, {}, r.get('normalized', original), new_name, cat)
        if cslug:
            clients.client_cache_save(cslug, original, new_name, cat, 100)
            clients.client_cache_set_status(cslug, original, new_name, 'confirmed')
        r['назва'] = new_name
        bot.edit_message_text(
            f"✅ Навчено!\n{original[:40]}\n❌ {old_name[:50] or '—'}\n✅ {new_name[:60]}",
            call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "Збережено")
        suggest_knowledge_rule(call.message.chat.id, original, old_name, new_name)
        # Пропонуємо виправити OCR
        orig_words = set(re.findall(r'[а-яёіїєґa-z]+', original.lower()))
        new_words  = set(re.findall(r'[а-яёіїєґa-z]+', new_name.lower()))
        ocr_diff   = orig_words - new_words - {'шт', 'м', 'мп', 'компл', 'рул', 'пог'}
        if ocr_diff and admin:
            bot.send_message(call.message.chat.id,
                f"🔤 OCR-помилка? Слова з запиту яких немає в результаті: "
                f"`{'`, `'.join(list(ocr_diff)[:4])}`\n\n"
                f"Виправити: `ocr <неправильне> = <правильне>`",
                parse_mode="Markdown")
    else:
        n = add_pending_fix({'original': original, 'old_name': old_name or None,
                             'new_name': new_name, 'category': cat, 'client_slug': cslug,
                             'normalized': r.get('normalized', ''),
                             'user_id': call.from_user.id, 'username': uname})
        notify_admin_fix(uname, original, old_name, new_name, n)
        bot.edit_message_text(
            f"📥 Надіслано адміну (черга: {n})\n{original[:40]}\n"
            f"❌ {old_name[:50] or '—'}\n✅ {new_name[:60]}",
            call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "📥 На розгляді")


# ─── Клієнти ─────────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('новий клієнт'))
def handle_new_client(message):     # "новий клієнт Ім'я" — створює профіль і одразу активує клієнта
    rest  = message.text[12:].strip()
    if not rest:
        bot.reply_to(message, "Формат: `новий клієнт Петренко, примітка`",
                     parse_mode="Markdown"); return
    parts = rest.split(',', 1)
    name  = parts[0].strip()
    notes = parts[1].strip() if len(parts) > 1 else ""
    ok, result = clients.create_client(name, notes)
    if ok:
        clients.set_active(message.chat.id, result)
        bot.reply_to(message, f"✅ Створено *{name}* і активовано. Кидай фото!",
                     parse_mode="Markdown")
    else:
        bot.reply_to(message, f"⚠️ {result}")


@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'клієнти')
def handle_clients_list(message):   # показує список всіх клієнтів з кількістю замовлень кожного
    index = clients.list_clients()
    if not index:
        bot.reply_to(message, "📁 Клієнтів немає.\n`новий клієнт <ім'я>`",
                     parse_mode="Markdown"); return
    lines = []
    for slug, name in sorted(index.items(), key=lambda x: x[1]):
        p = clients.get_profile(slug)
        lines.append(f"• {name} ({p.get('orders_count', 0) if p else 0} зам.)")
    bot.reply_to(message, f"📁 Клієнти ({len(index)}):\n" + "\n".join(lines))


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('клієнт'))
def handle_client(message):     # "клієнт Ім'я" — активує клієнта; "клієнт стоп" — скидає; "клієнт Ім'я: нотатка" — додає примітку
    rest = message.text[6:].strip()
    if not rest:
        slug = clients.get_active(message.chat.id)
        if slug:
            p = clients.get_profile(slug)
            bot.reply_to(message, f"👤 Активний: *{p['name'] if p else slug}*\n`клієнт стоп` — скинути",
                         parse_mode="Markdown")
        else:
            bot.reply_to(message, "Немає активного.\n`клієнт <ім'я>` — активувати",
                         parse_mode="Markdown")
        return
    if rest.lower() in ('стоп', 'скинути', 'off'):
        clients.clear_active(message.chat.id)
        bot.reply_to(message, "✅ Клієнта скинуто.")
        return
    if ':' in rest:
        name_part, note = rest.split(':', 1)
        slug = clients.find_client(name_part.strip())
        if not slug:
            bot.reply_to(message, f"⚠️ '{name_part.strip()}' не знайдено."); return
        clients.add_note(slug, note.strip())
        bot.reply_to(message, "✅ Примітку додано.")
        return
    slug = clients.find_client(rest)
    if not slug:
        bot.reply_to(message, f"⚠️ '{rest}' не знайдено.\n`новий клієнт {rest}`",
                     parse_mode="Markdown"); return
    clients.set_active(message.chat.id, slug)
    p     = clients.get_profile(slug)
    prefs = clients.get_preferences(slug)
    top   = ", ".join(b for b, _ in prefs.get('top_brands', [])[:3]) or "ще немає даних"
    notes = p.get('notes', []) if p else []
    notes_s = "\n".join(f"  • {n}" for n in notes[-3:] if n) or "  —"
    bot.reply_to(message,
        f"✅ Активовано: *{p['name'] if p else slug}*\n"
        f"📦 Замовлень: {p.get('orders_count', 0) if p else 0}\n"
        f"🏷 Топ виробники: {top}\n📝 Примітки:\n{notes_s}\n\nКидай фото!",
        parse_mode="Markdown")


# ─── OCR / правила / кеш ─────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('ocr '))
def handle_ocr_correction(message):     # "ocr слово = правильне" — зберігає корекцію почерку для майбутніх фото
    rest = message.text[4:].strip()
    if '=' not in rest:
        bot.reply_to(message,
            "Формат: `ocr <неправильно> = <правильно>`\n"
            "Дивитись збережені: `ocr список`", parse_mode="Markdown"); return
    parts = rest.split('=', 1)
    wrong, right = parts[0].strip(), parts[1].strip()
    if not wrong or not right:
        bot.reply_to(message, "⚠️ Вкажи і неправильне і правильне слово."); return
    save_ocr_correction(wrong, right)
    bot.reply_to(message, f"✅ OCR корекцію збережено:\n`{wrong}` → `{right}`",
                 parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'ocr список')
def handle_ocr_list(message):   # показує всі збережені корекції почерку
    d = load_ocr_corrections()
    if not d:
        bot.reply_to(message, "📋 Корекцій OCR поки немає.\nДодати: `ocr кільця = гільза`",
                     parse_mode="Markdown"); return
    lines = [f"  `{w}` → `{r}`" for w, r in d.items()]
    bot.reply_to(message, f"📋 OCR корекції ({len(d)}):\n" + "\n".join(lines),
                 parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'правила')
def handle_rules_show(message):     # показує вміст rules.txt посторінково
    text = get_rules()
    if not text.strip():
        bot.reply_to(message, "📖 База правил порожня."); return
    lines = text.splitlines()
    chunk, out, total = [], [], 0
    for i, l in enumerate(lines, 1):
        row = f"{i}. {l}" if l.strip() and not l.startswith('#') else l
        if total + len(row) > 3500:
            out.append("\n".join(chunk)); chunk, total = [], 0
        chunk.append(row); total += len(row) + 1
    if chunk: out.append("\n".join(chunk))
    for part in out[:5]:
        bot.send_message(message.chat.id, part)
    bot.send_message(message.chat.id,
        f"📖 Всього {len(lines)} рядків.\n"
        f"`правило <текст>` — додати | `правило видалити N` — прибрати\n"
        f"`оновити правила` — підтягнути з GitHub",
        parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'оновити правила')
def handle_rules_pull(message):     # адмін: підтягує актуальний rules.txt з гілки botdata GitHub
    if not is_admin(message.from_user.id): return
    try:
        import storage as _st
        text, _sha = _st._get_remote("rules.txt")
        if text:
            import os as _os
            rules_path = _os.path.join(DATA_DIR, "rules.txt")
            with open(rules_path, "w", encoding="utf-8") as f:
                f.write(text)
            bot.reply_to(message, f"✅ Підтягнуто: {len(text.splitlines())} рядків. Діє одразу.")
        else:
            bot.reply_to(message, "⚠️ Не вдалося (нема токена або файла в гілці).")
    except Exception as e:
        bot.reply_to(message, f"⚠️ {e}")


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('правило'))
def handle_rule(message):   # "правило текст" — додає правило (адмін одразу, інші → черга); "правило видалити N" — видаляє рядок N
    _m = re.match(r'^правило\s+видалити\s+(\d+)$', message.text.lower().strip())
    if _m:
        if not is_admin(message.from_user.id):
            bot.reply_to(message, "⛔ Тільки адмін."); return
        ok, msg = delete_rule(int(_m.group(1)))
        bot.reply_to(message, f"🗑 Видалено: {msg[:60]}" if ok else f"⚠️ {msg}")
        return
    rule = message.text[7:].strip()
    if not rule:
        bot.reply_to(message, "Напиши правило після слова 'правило'."); return
    if is_admin(message.from_user.id):
        add_rule(rule)
        bot.reply_to(message, f"✅ Записав:\n_{rule}_", parse_mode="Markdown")
    else:
        n = add_pending_rule(rule, message.from_user.id,
                             message.from_user.username or str(message.from_user.id))
        bot.reply_to(message, f"📥 Правило відправлено на розгляд ({n} в черзі):\n_{rule}_",
                     parse_mode="Markdown")
        try:
            bot.send_message(ADMIN_ID,
                f"🔔 Нове правило від @{message.from_user.username}:\n`{rule}`",
                parse_mode="Markdown")
        except Exception: pass


@bot.message_handler(commands=['кеш', 'cache'])
def handle_cache_info(message):     # /кеш або /cache — показує статистику кешу і останні 8 записів з кнопками керування
    stats = get_cache_stats()
    cache = get_cache()
    if not cache:
        bot.reply_to(message, "📋 Кеш порожній — заповниться після замовлень."); return
    icons = {'confirmed': '✅', 'banned': '❌', 'auto': '🔹'}
    lines = []
    for k, v in list(cache.items())[-8:]:
        orig = k.split("::")[0][:35]
        lines.append(f"{icons.get(v.get('status', 'auto'), '🔹')} `{orig}` → {v.get('catalog_name', '')[:45]}")
    text = (
        f"📋 *Кеш нормалізацій*\n\n"
        f"Всього: {stats['total']} | ✅ {stats['confirmed']} | 🔹 {stats['auto']} | ❌ {stats['banned']}\n"
        f"⏰ Прострочених (>{stats['ttl_days']}д): {stats['expired']}\n"
        f"Мін. confidence: {stats['min_conf']}%\n\n"
        f"Останні записи:\n" + "\n".join(lines)
    )
    mk = InlineKeyboardMarkup()
    mk.add(InlineKeyboardButton("🧹 Очистити прострочені", callback_data="cache_clean_expired"))
    if is_admin(message.from_user.id):
        mk.add(InlineKeyboardButton("🗑 Очистити всі auto", callback_data="cache_clean_auto"))
    bot.reply_to(message, text, parse_mode="Markdown", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data in ("cache_clean_expired", "cache_clean_auto"))
def handle_cache_clean_btn(call):   # обробляє натискання кнопок очищення кешу
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return

    if call.data == "cache_clean_expired":
        deleted = cache_cleanup_expired()
        bot.answer_callback_query(call.id, f"🧹 Видалено {deleted} прострочених")
        try:
            bot.edit_message_text(
                f"🧹 Видалено *{deleted}* прострочених записів з кешу.",
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown")
        except Exception:
            pass

    elif call.data == "cache_clean_auto":
        cache = get_cache()
        # видаляємо всі auto записи (не confirmed і не banned)
        from clients.cache import _CACHE, _save_cache
        to_del = [k for k, v in list(_CACHE.items()) if v.get('status', 'auto') == 'auto']
        for k in to_del:
            del _CACHE[k]
        _save_cache()
        bot.answer_callback_query(call.id, f"🗑 Видалено {len(to_del)} auto-записів")
        try:
            bot.edit_message_text(
                f"🗑 Видалено *{len(to_del)}* auto-записів з кешу.\n"
                f"✅ confirmed і ❌ banned — збережено.",
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown")
        except Exception:
            pass


@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'кеш очистити')
def handle_cache_cleanup(message):  # текстова команда очищення (залишаємо для сумісності)
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Тільки адмін."); return
    deleted = cache_cleanup_expired()
    bot.reply_to(message, f"🧹 Видалено {deleted} прострочених записів з кешу.")


# ─── Перевірка кешу (підтвердження нових збігів) ────────────────────────────

def _send_pending_batch(chat_id: int, batch: list):     # надсилає пачку pending збігів адміну з кнопками підтвердження
    if not batch:
        bot.send_message(chat_id, "✅ Немає нових збігів для підтвердження.")
        return

    # формуємо текст пачки
    lines = []
    for i, r in enumerate(batch, 1):
        src   = "⚡" if r.get('source') == 'auto' else "🤖"
        conf  = r.get('confidence', 0)
        brand = ""
        bm    = r.get('brand_map', {})
        if bm:
            brands = list(set(v[0] if isinstance(v, list) else v for v in bm.values()))
            brand  = f" [{', '.join(brands[:2])}]"
        lines.append(
            f"{i}. {src} «{r.get('original', '')[:35]}»\n"
            f"   → {r.get('catalog_name', '')[:50]}\n"
            f"   {conf}%{brand}"
        )

    total = pending_count()
    text  = (
        f"📋 *Нові збіги на підтвердження* ({total} всього)\n\n" +
        "\n\n".join(lines)
    )

    # кнопки: підтвердити всю пачку / є помилки / пропустити
    ids_str = ",".join(r["id"] for r in batch)
    mk = InlineKeyboardMarkup(row_width=1)
    mk.add(
        InlineKeyboardButton("✅ Всі вірно — зберегти", callback_data=f"pc_all:{ids_str}"),
        InlineKeyboardButton("❌ Є помилки — вибрати", callback_data=f"pc_pick:{ids_str}"),
        InlineKeyboardButton("🗑 Відхилити всі",        callback_data=f"pc_rej:{ids_str}"),
    )
    if total > len(batch):
        mk.add(InlineKeyboardButton(f"➡️ Наступні ({total - len(batch)} залишилось)",
                                    callback_data="pc_next"))
    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=mk)


@bot.message_handler(func=lambda m: m.text and m.text in ("👑 Перевір кеш",))
def kb_check_cache(message):    # кнопка клавіатури "Перевір кеш" — відкриває підтвердження збігів
    handle_check_cache(message)


@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ('перевір кеш', 'перевир кеш'))
def handle_check_cache(message):    # команда "перевір кеш" — показує адміну нові збіги на підтвердження
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Тільки адмін."); return
    count = pending_count()
    if count == 0:
        bot.reply_to(message, "✅ Немає нових збігів для перевірки."); return
    batch = pending_get_batch(5)
    _send_pending_batch(message.chat.id, batch)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pc_all:"))
def handle_pc_all(call):    # підтверджує всю пачку збігів — зберігає всі в кеш
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    ids   = call.data[7:].split(",")
    saved = pending_confirm(ids)
    bot.answer_callback_query(call.id, f"✅ Збережено {saved}")
    try:
        remaining = pending_count()
        extra     = f"\n\nЗалишилось: {remaining}" if remaining > 0 else "\n\n✅ Черга порожня!"
        bot.edit_message_text(
            f"✅ Збережено в кеш: *{saved}* збігів{extra}",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("pc_rej:"))
def handle_pc_rej(call):    # відхиляє всю пачку збігів — видаляє з черги
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    ids      = call.data[7:].split(",")
    rejected = pending_reject(ids)
    bot.answer_callback_query(call.id, f"🗑 Відхилено {rejected}")
    try:
        remaining = pending_count()
        extra     = f"\n\nЗалишилось: {remaining}" if remaining > 0 else "\n\n✅ Черга порожня!"
        bot.edit_message_text(
            f"🗑 Відхилено: *{rejected}* збігів{extra}",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("pc_pick:"))
def handle_pc_pick(call):   # показує кожен збіг окремо щоб адмін вибрав які зберегти
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    ids   = call.data[8:].split(",")
    batch = pending_get_batch(5)
    # залишаємо тільки ті що в поточній пачці
    batch = [r for r in batch if r["id"] in ids]
    if not batch:
        bot.answer_callback_query(call.id, "Застаріло"); return
    bot.answer_callback_query(call.id)
    # показуємо кожен збіг окремо
    for r in batch:
        src  = "⚡ точний збіг" if r.get('source') == 'auto' else "🤖 Claude"
        bm   = r.get('brand_map', {})
        brand = ""
        if bm:
            brands = list(set(v[0] if isinstance(v, list) else v for v in bm.values()))
            brand  = f"\nПідказка: {', '.join(brands[:2])}"
        text = (
            f"📌 {src} [{r.get('confidence', 0)}%]\n"
            f"Написано: `{r.get('original', '')}`\n"
            f"Нормалізовано: `{r.get('normalized', '')}`\n"
            f"Товар: *{r.get('catalog_name', '')}*{brand}"
        )
        mk = InlineKeyboardMarkup()
        mk.add(
            InlineKeyboardButton("✅ Зберегти", callback_data=f"pc_one_yes:{r['id']}"),
            InlineKeyboardButton("❌ Відхилити", callback_data=f"pc_one_no:{r['id']}"),
        )
        bot.send_message(call.message.chat.id, text, parse_mode="Markdown", reply_markup=mk)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pc_one_yes:"))
def handle_pc_one_yes(call):    # зберігає один конкретний збіг в кеш
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    rid   = call.data[11:]
    saved = pending_confirm([rid])
    bot.answer_callback_query(call.id, "✅ Збережено")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                      reply_markup=None)
        bot.edit_message_text(
            call.message.text + "\n\n✅ *Збережено в кеш*",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data.startswith("pc_one_no:"))
def handle_pc_one_no(call):     # відхиляє один конкретний збіг
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    rid      = call.data[10:]
    rejected = pending_reject([rid])
    bot.answer_callback_query(call.id, "❌ Відхилено")
    try:
        bot.edit_message_text(
            call.message.text + "\n\n❌ *Відхилено*",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    except Exception:
        pass


@bot.callback_query_handler(func=lambda c: c.data == "pc_next")
def handle_pc_next(call):   # показує наступну пачку збігів
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
    bot.answer_callback_query(call.id)
    batch = pending_get_batch(5)
    if not batch:
        bot.send_message(call.message.chat.id, "✅ Черга порожня!")
        return
    _send_pending_batch(call.message.chat.id, batch)


# ─── Фото / документи / текст ────────────────────────────────────────────────

@bot.message_handler(content_types=['photo'])
def handle_photo(message):  # приймає фото від користувача і додає в батч; дедуплікує по file_unique_id; 3 спроби завантаження
    fuid = message.photo[-1].file_unique_id
    _b   = user_batches.get(message.chat.id)
    if _b and any(it.get('fuid') == fuid for it in _b.get('items', [])):
        return
    for attempt in range(3):
        try:
            file_info  = bot.get_file(message.photo[-1].file_id)
            downloaded = bot.download_file(file_info.file_path)
            image_b64  = base64.b64encode(downloaded).decode('utf-8')
            caption    = message.caption or ""
            hint       = pending_hints.pop(message.chat.id, "")
            full_caption = " | ".join(filter(None, [caption, hint]))
            add_to_batch(message.chat.id, {
                'type': 'photo', 'data': image_b64, 'caption': full_caption, 'fuid': fuid,
                'username': message.from_user.username or str(message.from_user.id),
            })
            return
        except Exception as e:
            if attempt == 2:
                bot.reply_to(message, f"❌ Не вдалося завантажити фото: {e}")
            else:
                time.sleep(2)


@bot.message_handler(content_types=['document'])
def handle_document(message):   # приймає документ (jpg/png/pdf); PDF передає як специфікацію, зображення як фото
    doc   = message.document
    mime  = (doc.mime_type or '').lower()
    fname = (doc.file_name or '').lower()
    is_pdf = mime == 'application/pdf' or fname.endswith('.pdf')
    if not (is_pdf or mime.startswith('image/') or fname.endswith(('.jpg', '.jpeg', '.png', '.webp'))):
        bot.reply_to(message, "📎 Приймаю фото (jpg/png) або PDF-специфікацію.")
        return
    fuid = doc.file_unique_id
    _b   = user_batches.get(message.chat.id)
    if _b and any(it.get('fuid') == fuid for it in _b.get('items', [])):
        return
    for attempt in range(3):
        try:
            file_info  = bot.get_file(doc.file_id)
            downloaded = bot.download_file(file_info.file_path)
            image_b64  = base64.b64encode(downloaded).decode('utf-8')
            caption    = message.caption or ""
            hint       = pending_hints.pop(message.chat.id, "")
            full_caption = " | ".join(filter(None, [caption, hint]))
            add_to_batch(message.chat.id, {
                'type': 'pdf' if is_pdf else 'photo',
                'data': image_b64, 'caption': full_caption, 'fuid': fuid,
                'username': message.from_user.username or str(message.from_user.id),
            })
            return
        except Exception as e:
            if attempt == 2:
                bot.reply_to(message, f"❌ Не вдалося завантажити: {e}")
            else:
                time.sleep(2)


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('пошук'))
def handle_text_search(message):    # "пошук ..." — підбирає товари за текстовим запитом без фото
    запит = message.text[5:].strip()
    if запит:
        hint = pending_hints.pop(message.chat.id, "")
        add_to_batch(message.chat.id, {
            'type': 'text', 'text': запит, 'caption': hint,
            'username': message.from_user.username or str(message.from_user.id),
        })
    else:
        bot.reply_to(message, "Напиши запит після слова 'пошук'.")


@bot.message_handler(commands=['stop'])
def handle_stop(message):   # /stop — скасовує поточний батч і знімає прапорець обробки
    chat_id = message.chat.id
    stop_flags[chat_id] = True
    if chat_id in user_batches:
        if 'timer' in user_batches[chat_id]:
            user_batches[chat_id]['timer'].cancel()
        user_batches.pop(chat_id, None)
    bot.reply_to(message, "🛑 Зупинено.")


@bot.message_handler(func=lambda m: m.text and m.chat.id in _manual_wait)
def handle_manual_input(message):   # ловить текст коли чекає ручного вводу (режими: fix — пошук виправлення, ocr_fix — виправлення OCR, train — ручний вибір товару)
    state = _manual_wait.pop(message.chat.id, None)
    if not state: return
    mode  = state.get('mode')
    query = message.text.strip()
    cands = [c['name'] for c in keyword_search(query, top_n=9)]
    if not cands:
        bot.reply_to(message, "😕 Нічого не знайдено. Спробуй іншу назву або частину.")
        return

    if mode == 'fix':
        st = _fix_state.get(message.chat.id)
        if not st:
            bot.reply_to(message, "⚠️ Сесія виправлення завершена."); return
        st['cands'] = cands
        mk = InlineKeyboardMarkup(row_width=1)
        for i, name in enumerate(cands):
            mk.add(InlineKeyboardButton(f"{i+1}. {name[:55]}", callback_data=f"fx_{i}"))
        mk.add(InlineKeyboardButton("✍️ Ввести іншу назву", callback_data="fx_man"))
        mk.add(InlineKeyboardButton("❌ Немає правильного (тільки бан)", callback_data="fx_ban"))
        bot.reply_to(message, f"🔍 Знайдено за '{query}':\nТапни правильний:", reply_markup=mk)

    elif mode == 'ocr_fix':
        st   = _train_state.get(message.chat.id)
        last = last_results.get(message.chat.id)
        if not st or not last:
            bot.reply_to(message, "⚠️ Сесія навчання завершена."); return
        row  = st['rows'][st['i']]
        r    = last['результати'][row - 1]
        old_original = r.get('original', '')
        corrected    = query

        w_re   = r'[а-яёіїєґa-z]+|\d+(?:[.,/]\d+)?'
        old_w  = re.findall(w_re, old_original.lower())
        new_w  = re.findall(w_re, corrected.lower())
        wrongs = [w for w in old_w if w not in new_w and len(w) > 1]
        rights = [w for w in new_w if w not in old_w and len(w) > 1]
        pairs  = list(zip(wrongs, rights))[:3]
        if pairs:
            st['ocr_pairs'] = pairs
            mk = InlineKeyboardMarkup(row_width=1)
            for pi, (w, rt) in enumerate(pairs):
                mk.add(InlineKeyboardButton(f"🔤 Запам'ятати: «{w}» → «{rt}»",
                                            callback_data=f"ocrs_{pi}"))
            bot.send_message(message.chat.id,
                "Схоже на помилки почерку — збережу корекції для наступних фото?",
                reply_markup=mk)

        псевдо = {'original': corrected, 'normalized': corrected,
                  'category': r.get('category', 'other')}
        cands2 = [c['name'] for c in smart_search(псевдо, top_n=8)]
        cands2 = [c for c in cands2 if c != r.get('назва', '')][:7]
        if not cands2:
            bot.reply_to(message, "😕 По новому тексту нічого. Спробуй ще раз.")
            _manual_wait[message.chat.id] = {'mode': 'ocr_fix'}
            return
        st['cands'] = cands2
        st['ocr_new_original'] = corrected
        mk = InlineKeyboardMarkup(row_width=1)
        for i, name in enumerate(cands2):
            mk.add(InlineKeyboardButton(f"{i+1}. {name[:55]}", callback_data=f"trp_{i}"))
        mk.add(InlineKeyboardButton("✍️ Ввести з бази точніше", callback_data="trm"))
        mk.add(InlineKeyboardButton("❌ Немає правильного",      callback_data="trn"))
        bot.reply_to(message, f"🔍 Пошук за «{corrected[:45]}»:\nТапни ПРАВИЛЬНИЙ товар:",
                     reply_markup=mk)

    elif mode == 'train':
        st = _train_state.get(message.chat.id)
        if not st:
            bot.reply_to(message, "⚠️ Сесія навчання завершена."); return
        st['cands'] = cands
        mk = InlineKeyboardMarkup(row_width=1)
        for i, name in enumerate(cands):
            mk.add(InlineKeyboardButton(f"{i+1}. {name[:55]}", callback_data=f"trp_{i}"))
        mk.add(InlineKeyboardButton("✍️ Ввести іншу назву", callback_data="trm"))
        mk.add(InlineKeyboardButton("❌ Немає правильного", callback_data="trn"))
        bot.reply_to(message, f"🔍 Знайдено за '{query}':\nТапни правильний:", reply_markup=mk)


@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/')
                     and not m.text.lower().startswith('пошук')
                     and not m.text.lower().startswith('правило')
                     and not re.match(r'^(вірно|помилка|виправ)\s+\d+', m.text.lower().strip())
                     and not m.text.lower().startswith('клієнт')
                     and not m.text.lower().startswith('новий клієнт')
                     and not m.text.startswith(('📸', '🛑', '📋', '📊', '👥', '👑')))
def handle_text_hint(message):  # ловить довільний текст: якщо схожий на список — питає "підібрати чи підказка до фото"; інакше зберігає як підказку виробника на 2 хв
    text = message.text.strip()
    if not text: return

    lines   = [l.strip() for l in text.splitlines() if l.strip()]
    has_qty = sum(1 for l in lines if re.search(r'\d+\s*(шт|м\b|пог|компл|рул)', l.lower()))
    is_list = len(lines) >= 3 or has_qty >= 2

    if is_list:
        _text_pending[message.chat.id] = text
        mk = InlineKeyboardMarkup()
        mk.add(InlineKeyboardButton("🔍 Підібрати список", callback_data="txts"),
               InlineKeyboardButton("💬 Це підказка до фото", callback_data="txth"))
        bot.reply_to(message, f"Схоже на список ({len(lines)} рядків). Що робити?",
                     reply_markup=mk)
    else:
        pending_hints[message.chat.id] = text
        bot.reply_to(message, f"💬 Підказка: _{text}_\nТепер кидай фото!", parse_mode="Markdown")
        def clear(cid): pending_hints.pop(cid, None)     # видаляє підказку після 2 хв якщо фото так і не надійшло
        t = threading.Timer(120.0, clear, args=[message.chat.id])
        t.daemon = True
        t.start()


@bot.callback_query_handler(func=lambda c: c.data in ("txts", "txth"))
def handle_text_choice(call):   # обробляє вибір користувача: підібрати список зараз або зберегти як підказку до наступного фото
    chat_id = call.message.chat.id
    text    = _text_pending.pop(chat_id, None)
    if not text:
        bot.answer_callback_query(call.id, "Сесія застаріла"); return
    if call.data == "txts":
        add_to_batch(chat_id, {
            'type': 'text', 'text': text, 'caption': '',
            'username': call.from_user.username or str(call.from_user.id),
        })
        try:
            bot.edit_message_text("✅ Прийнято! Обробляю список...", chat_id, call.message.message_id)
        except Exception: pass
    else:
        pending_hints[chat_id] = text
        try:
            bot.edit_message_text("💬 Підказка збережена.\nТепер кидай фото!",
                chat_id, call.message.message_id)
        except Exception: pass
    bot.answer_callback_query(call.id)


# ─── Старт ───────────────────────────────────────────────────────────────────
try:
    storage.start_autosave(60)
except Exception as _e:
    print(f"⚠️ storage autosave: {_e}", flush=True)

print("🤖 bot.py завантажено, хендлери зареєстровані", flush=True)
