"""handlers/admin_handler.py — Команди адміна (статистика, логи, діри, кеш)."""
import os
import time
from config.settings import ADMIN_ID, DATA_DIR, USAGE_LOG_FILE
from keyboards.inline import gaps_excel_keyboard


def register(bot, state: dict):
    from engine.logger import get_usage_stats, get_catalog_gaps, get_catalog_gaps_excel
    from clients.cache import (get_cache_stats, get_cache, cache_cleanup_expired,
                                cache_set_status)
    from clients.pending_cache import (pending_count, pending_get_batch, pending_confirm,
                                       pending_reject, pending_confirm_all_batch,
                                       pending_reject_all_batch, pending_clear_all)

    def _admin(uid): return uid == ADMIN_ID

    # ── Статистика ─────────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text == "👑 Статистика")
    def kb_stats(message):
        if not _admin(message.from_user.id): return
        bot.reply_to(message, get_usage_stats(), parse_mode="Markdown")

    @bot.message_handler(func=lambda m: m.text == "👑 Логи")
    def kb_logs(message):
        if not _admin(message.from_user.id): return
        if not os.path.exists(USAGE_LOG_FILE):
            bot.reply_to(message, "Лог порожній."); return
        with open(USAGE_LOG_FILE, "rb") as f:
            bot.send_document(message.chat.id, f, visible_file_name="usage_log.json")

    # ── Діри каталогу ──────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text == "👑 Діри каталогу")
    @bot.message_handler(commands=['діри', 'gaps'])
    def kb_gaps(message):
        if not _admin(message.from_user.id): return
        bot.reply_to(message, get_catalog_gaps(), reply_markup=gaps_excel_keyboard())

    @bot.callback_query_handler(func=lambda c: c.data == "gaps_excel")
    def cb_gaps_excel(call):
        if not _admin(call.from_user.id):
            bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
        bot.answer_callback_query(call.id, "⏳ Генерую Excel...")
        tmp = os.path.join(DATA_DIR, "gaps_export.xlsx")
        try:
            count = get_catalog_gaps_excel(tmp)
            if count == 0:
                bot.send_message(call.message.chat.id, "🕳 Порожньо."); return
            with open(tmp, 'rb') as f:
                bot.send_document(
                    call.message.chat.id, f,
                    caption=(f"🕳 Діри каталогу: *{count}* позицій\n"
                             f"🔴 ≥5 разів | 🟡 2-4 рази"),
                    visible_file_name=f"діри_{time.strftime('%Y%m%d')}.xlsx",
                    parse_mode="Markdown")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ {e}")
        finally:
            if os.path.exists(tmp): os.remove(tmp)

    # ── Кеш ────────────────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text == "📊 Кеш")
    @bot.message_handler(commands=['кеш', 'cache'])
    def kb_cache(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        stats  = get_cache_stats()
        items  = get_cache()
        recent = list(items.items())[-8:]
        lines  = [f"📊 *Кеш нормалізацій*\n{stats}\n\n*Останні записи:*"]
        for orig, data in recent:
            status = data.get('status', 'auto')
            icon   = {'confirmed': '✅', 'banned': '🚫', 'auto': '🤖'}.get(status, '🤖')
            name   = data.get('name', '')[:35]
            lines.append(f"{icon} `{orig[:30]}` → {name}")
        mk = InlineKeyboardMarkup(row_width=2)
        mk.add(
            InlineKeyboardButton("🗑 Прострочені", callback_data="cache_clean_expired"),
            InlineKeyboardButton("🗑 Авто",        callback_data="cache_clean_auto"),
        )
        bot.reply_to(message, "\n".join(lines), parse_mode="Markdown", reply_markup=mk)

    @bot.callback_query_handler(func=lambda c: c.data in ("cache_clean_expired", "cache_clean_auto"))
    def handle_cache_clean_btn(call):
        if call.data == "cache_clean_expired":
            n = cache_cleanup_expired()
            bot.answer_callback_query(call.id, f"Видалено {n} прострочених")
        else:
            items = get_cache()
            n = 0
            for orig, data in list(items.items()):
                if data.get('status') == 'auto':
                    cache_set_status(orig, data.get('name', ''), data.get('category', ''), 'deleted')
                    n += 1
            bot.answer_callback_query(call.id, f"Видалено {n} авто-записів")
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id)

    # ── Pending кеш ────────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text and (
        m.text == "👑 Перевір кеш" or m.text.lower().strip() == "перевір кеш"
    ))
    def handle_check_cache(message):
        if not _admin(message.from_user.id): return
        count = pending_count()
        if count == 0:
            bot.reply_to(message, "✅ Черга порожня."); return
        batch_id = "b0"
        batch    = pending_get_batch(0)
        _send_pending_batch(message.chat.id, batch, batch_id, bot, pending_confirm,
                            pending_reject, pending_confirm_all_batch,
                            pending_reject_all_batch, pending_clear_all, pending_get_batch)

    # ── Правила на розгляд ─────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text == "👑 Правила на розгляд")
    def kb_pending(message):
        if not _admin(message.from_user.id): return
        _show_pending(message.chat.id, bot, state)


def _send_pending_batch(chat_id, batch, batch_id, bot,
                        confirm_fn, reject_fn, confirm_all_fn,
                        reject_all_fn, clear_fn, get_batch_fn):
    """Надсилає пачку pending збігів з кнопками."""
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
    if not batch:
        bot.send_message(chat_id, "✅ Черга порожня."); return
    lines = ["📋 *Нові збіги для підтвердження:*\n"]
    for i, item in enumerate(batch[:10]):
        orig = item.get('original', '')[:30]
        name = item.get('name', '')[:40]
        conf = item.get('confidence', 0)
        lines.append(f"{i+1}. `{orig}` → *{name}* ({conf}%)")
    mk = InlineKeyboardMarkup(row_width=3)
    mk.add(
        InlineKeyboardButton("✅ Всі",       callback_data=f"pc_all:{batch_id}"),
        InlineKeyboardButton("❌ Відхилити", callback_data=f"pc_rej:{batch_id}"),
        InlineKeyboardButton("👀 Вибрати",  callback_data=f"pc_pick:{batch_id}"),
    )
    mk.add(
        InlineKeyboardButton("🗑 Очистити все", callback_data="pc_clear_all"),
        InlineKeyboardButton("➡️ Далі",         callback_data="pc_next"),
    )
    bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown", reply_markup=mk)


def _show_pending(chat_id, bot, state):
    """Показує pending rules і fixes."""
    from knowledge.rules import load_pending_rules
    rules = load_pending_rules()
    fixes = state.get('load_pending_fixes', lambda: [])()
    lines = []
    if rules:
        lines.append("📝 *Правила на розгляд:*")
        for i, r in enumerate(rules[:10]):
            lines.append(f"{i+1}. `{r.get('text','')[:60]}`")
    if fixes:
        lines.append("\n🔧 *Виправлення на розгляд:*")
        for i, f in enumerate(fixes[:10]):
            lines.append(f"{i+1}. `{f.get('original','')[:25]}` → *{f.get('new_name','')[:35]}*")
    if not lines:
        bot.send_message(chat_id, "✅ Нічого на розгляд."); return
    bot.send_message(chat_id, "\n".join(lines), parse_mode="Markdown")
