"""handlers/client_handler.py — клієнт, новий клієнт, кеш клієнта."""
import re
from config.settings import ADMIN_ID, PAGE_SIZE


def register(bot, state: dict):
    from clients import clients

    # ── Список клієнтів ───────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'клієнти')
    @bot.message_handler(func=lambda m: m.text == "👥 Клієнти")
    def handle_clients_list(message):
        index = clients.list_clients()
        if not index:
            bot.reply_to(message, "📁 Клієнтів немає.\n`новий клієнт <ім'я>`",
                         parse_mode="Markdown"); return
        active = clients.get_active(message.chat.id)
        lines  = []
        for slug, name in sorted(index.items(), key=lambda x: x[1]):
            p   = clients.get_profile(slug)
            cnt = p.get('orders_count', 0) if p else 0
            mark = " ◀ активний" if slug == active else ""
            lines.append(f"• {name} ({cnt} зам.){mark}")
        bot.reply_to(message, f"📁 Клієнти ({len(index)}):\n" + "\n".join(lines))

    # ── Новий клієнт ──────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text == "➕ Новий клієнт")
    def kb_new_client(message):
        state.setdefault('_manual_wait', {})[message.chat.id] = {'mode': 'new_client'}
        bot.reply_to(message, "👤 Введи ім'я нового клієнта:")

    @bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('новий клієнт'))
    def handle_new_client(message):
        name = message.text[12:].strip()
        if not name:
            state.setdefault('_manual_wait', {})[message.chat.id] = {'mode': 'new_client'}
            bot.reply_to(message, "👤 Введи ім'я нового клієнта:"); return
        _create_client(message.chat.id, name, message, bot)

    # ── Клієнт <ім'я> ─────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('клієнт'))
    def handle_client(message):
        rest = message.text[6:].strip()
        if not rest:
            slug = clients.get_active(message.chat.id)
            if slug:
                p = clients.get_profile(slug)
                bot.reply_to(message,
                    f"👤 Активний: *{p['name'] if p else slug}*\n`клієнт стоп` — скинути",
                    parse_mode="Markdown")
            else:
                bot.reply_to(message, "Немає активного.\n`клієнт <ім'я>` — активувати",
                             parse_mode="Markdown")
            return
        if rest.lower() in ('стоп', 'скинути', 'off'):
            clients.clear_active(message.chat.id)
            bot.reply_to(message, "✅ Клієнта скинуто."); return

        # Точний збіг
        slug = clients.find_client(rest)
        if slug:
            _activate_client(message.chat.id, slug, message, bot); return

        # Нечіткий пошук
        similar = clients.find_similar_clients(rest, threshold=0.3)
        if similar:
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            mk = InlineKeyboardMarkup(row_width=1)
            for s, cname, score in similar:
                mk.add(InlineKeyboardButton(
                    f"👤 {cname} ({int(score*100)}%)", callback_data=f"cl_use_{s}"))
            mk.add(InlineKeyboardButton(f"➕ Новий «{rest}»", callback_data=f"cl_new__{rest}"))
            bot.reply_to(message, "🔍 Знайдено схожих:", reply_markup=mk)
        else:
            bot.reply_to(message,
                f"⚠️ '{rest}' не знайдено.\n`новий клієнт {rest}` — створити",
                parse_mode="Markdown")

    @bot.callback_query_handler(func=lambda c: c.data.startswith('cl_use_'))
    def cb_client_use(call):
        slug = call.data[7:]
        p    = clients.get_profile(slug)
        if not p:
            bot.answer_callback_query(call.id, "Клієнта не знайдено"); return
        clients.set_active(call.message.chat.id, slug)
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text(
                f"✅ Активовано: *{p['name']}*",
                call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        except Exception: pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith('cl_new__'))
    def cb_client_new(call):
        name = call.data[8:]
        bot.answer_callback_query(call.id)
        _create_client(call.message.chat.id, name, None, bot)

    @bot.callback_query_handler(func=lambda c: c.data == 'cl_rename')
    def cb_client_rename(call):
        state.setdefault('_manual_wait', {})[call.message.chat.id] = {'mode': 'new_client'}
        try:
            bot.edit_message_text("✏️ Введи ім'я знову:",
                call.message.chat.id, call.message.message_id)
        except Exception: pass
        bot.answer_callback_query(call.id)

    # ── sel_client_ (з client_list_keyboard) ──────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith('sel_client_'))
    def cb_select_client(call):
        slug = call.data[11:]
        bot.answer_callback_query(call.id)
        _activate_client(call.message.chat.id, slug, None, bot)

    # ── Кеш клієнта ───────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text == "👥 Кеш клієнта")
    def kb_client_cache(message):
        slug = clients.get_active(message.chat.id)
        if not slug:
            bot.reply_to(message,
                "❓ Активний клієнт не вибраний.\n`клієнт <ім'я>` — активувати",
                parse_mode="Markdown"); return
        _show_client_cache(message.chat.id, slug, 0, bot)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('ccp_'))
    def cb_client_cache_page(call):
        bot.answer_callback_query(call.id)
        # формат: ccp_{slug}_{page}
        parts = call.data[4:].rsplit('_', 1)
        if len(parts) != 2:
            return
        slug, page_s = parts
        try:
            page = int(page_s)
        except ValueError:
            return
        _show_client_cache(call.message.chat.id, slug, page, bot)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('cck_ok_')
                                               or c.data.startswith('cck_no_'))
    def cb_client_cache_action(call):
        bot.answer_callback_query(call.id)
        action  = 'confirmed' if call.data.startswith('cck_ok_') else 'banned'
        payload = call.data[7:]   # slug::key
        if '::' not in payload:
            return
        slug, key = payload.split('::', 1)
        cache = clients.get_client_cache(slug)
        entry = cache.get(key)
        if not entry:
            return
        cname = entry.get('catalog_name', '')
        clients.client_cache_set_status(slug, key, cname, action)
        icon = '✅' if action == 'confirmed' else '❌'
        try:
            bot.edit_message_text(
                f"{icon} {'Підтверджено' if action=='confirmed' else 'Заборонено'}: `{key[:40]}`",
                call.message.chat.id, call.message.message_id,
                parse_mode="Markdown")
        except Exception: pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith('cck_all_'))
    def cb_cache_confirm_all(call):
        slug  = call.data[8:]
        cache = clients.get_client_cache(slug)
        count = 0
        for k, v in cache.items():
            if '::ban' in k or v.get('status') != 'auto':
                continue
            clients.client_cache_set_status(slug, k, v.get('catalog_name', ''), 'confirmed')
            count += 1
        bot.answer_callback_query(call.id, f"✅ Підтверджено {count}")
        try:
            bot.edit_message_text(f"✅ Підтверджено {count} авто-записів.",
                call.message.chat.id, call.message.message_id)
        except Exception: pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith('ccl__'))
    def cb_cache_clear(call):
        uid  = call.from_user.id
        data = call.data[5:]  # slug__mode
        if '__' not in data:
            bot.answer_callback_query(call.id, "⚠️ Помилка"); return
        slug, mode = data.rsplit('__', 1)
        active = clients.get_active(call.message.chat.id)
        if uid != ADMIN_ID and active != slug:
            bot.answer_callback_query(call.id, "⛔ Не твій клієнт"); return
        if uid != ADMIN_ID and mode != 'auto':
            bot.answer_callback_query(call.id, "⛔ Менеджер може чистити тільки авто"); return

        if mode == 'auto':
            deleted = clients.client_cache_clear(slug, 'auto')
            bot.answer_callback_query(call.id, f"🧹 Видалено {deleted}")
            try:
                bot.edit_message_text(f"🧹 Видалено {deleted} авто-записів.",
                    call.message.chat.id, call.message.message_id)
            except Exception: pass
        else:
            from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
            mk = InlineKeyboardMarkup(row_width=2)
            mk.add(
                InlineKeyboardButton("⚠️ Так, видалити", callback_data=f"cclc__{slug}__{mode}"),
                InlineKeyboardButton("❌ Скасувати",     callback_data="cclx"),
            )
            bot.answer_callback_query(call.id)
            try:
                bot.edit_message_text(
                    f"⚠️ Видалити *{mode}* кеш? Це незворотньо!",
                    call.message.chat.id, call.message.message_id,
                    parse_mode="Markdown", reply_markup=mk)
            except Exception: pass

    @bot.callback_query_handler(func=lambda c: c.data.startswith('cclc__'))
    def cb_cache_clear_confirm(call):
        if call.from_user.id != ADMIN_ID:
            bot.answer_callback_query(call.id, "⛔ Тільки адмін"); return
        data = call.data[6:]
        if '__' not in data:
            bot.answer_callback_query(call.id); return
        slug, mode = data.rsplit('__', 1)
        flt     = None if mode == 'all' else mode
        deleted = clients.client_cache_clear(slug, flt)
        bot.answer_callback_query(call.id, f"✅ Видалено {deleted}")
        try:
            bot.edit_message_text(f"✅ Видалено {deleted} записів.",
                call.message.chat.id, call.message.message_id)
        except Exception: pass

    @bot.callback_query_handler(func=lambda c: c.data == 'cclx')
    def cb_cache_clear_cancel(call):
        bot.answer_callback_query(call.id)
        try:
            bot.edit_message_text("❌ Скасовано.",
                call.message.chat.id, call.message.message_id)
        except Exception: pass


# ─── Допоміжні функції ────────────────────────────────────────────────────────

def _activate_client(chat_id: int, slug: str, message, bot):
    from clients import clients
    clients.set_active(chat_id, slug)
    p      = clients.get_profile(slug)
    name   = p['name'] if p else slug
    stats  = clients.get_client_cache_stats(slug)
    prefs  = clients.get_preferences(slug)
    top    = ", ".join(b for b, _ in prefs.get('top_brands', [])[:3]) or "—"
    text   = (
        f"✅ Активовано: *{name}*\n"
        f"📦 Замовлень: {p.get('orders_count', 0) if p else 0}\n"
        f"💾 Кеш: ✅{stats['confirmed']} 🔹{stats['auto']} ❌{stats['banned']}\n"
        f"🏷 Топ виробники: {top}\n\n"
        f"Кидай фото або `навчання`"
    )
    if message:
        bot.reply_to(message, text, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, parse_mode="Markdown")


def _create_client(chat_id: int, name: str, message, bot):
    from clients import clients
    similar = clients.find_similar_clients(name, threshold=0.5)
    if similar:
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        mk = InlineKeyboardMarkup(row_width=1)
        for slug, cname, score in similar:
            mk.add(InlineKeyboardButton(
                f"👤 {cname} ({int(score*100)}% схожість)",
                callback_data=f"cl_use_{slug}"))
        mk.add(InlineKeyboardButton(f"➕ Створити нового «{name}»",
                                    callback_data=f"cl_new__{name}"))
        txt = f"⚠️ Знайдено схожих клієнтів. Оберіть або створіть нового:"
        if message:
            bot.reply_to(message, txt, reply_markup=mk)
        else:
            bot.send_message(chat_id, txt, reply_markup=mk)
        return

    ok, result = clients.create_client(name)
    if ok:
        clients.set_active(chat_id, result)
        _activate_client(chat_id, result, message, bot)
    else:
        txt = f"⚠️ {result}"
        if message:
            bot.reply_to(message, txt)
        else:
            bot.send_message(chat_id, txt)


def _show_client_cache(chat_id: int, slug: str, page: int, bot):
    from clients import clients
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    p     = clients.get_profile(slug)
    stats = clients.get_client_cache_stats(slug)
    cache = clients.get_client_cache(slug)
    name  = p['name'] if p else slug

    auto_items = [(k, v) for k, v in cache.items()
                  if '::ban' not in k and v.get('status', 'auto') == 'auto']
    conf_items = [(k, v) for k, v in cache.items()
                  if '::ban' not in k and v.get('status') == 'confirmed']

    total_pages = max(1, (len(auto_items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page        = max(0, min(page, total_pages - 1))
    page_items  = auto_items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    text = (
        f"👤 Кеш *{name}*\n"
        f"✅ підтв: {stats['confirmed']} | 🔹 авто: {stats['auto']} | ❌ бан: {stats['banned']}\n"
    )

    mk = InlineKeyboardMarkup(row_width=2)

    if page_items:
        text += f"\n_Авто-записи (стор. {page+1}/{total_pages}):_\n"
        for k, v in page_items:
            cname = v.get('catalog_name', '')
            text += f"\n🔹 `{k[:28]}` → _{cname[:38]}_"
            # payload: slug::key (обмежуємо до 64 символів для callback_data)
            cb_key = f"{slug}::{k}"[:60]
            mk.add(
                InlineKeyboardButton(f"✅ {k[:18]}", callback_data=f"cck_ok_{cb_key}"),
                InlineKeyboardButton("❌ бан",        callback_data=f"cck_no_{cb_key}"),
            )
    elif conf_items:
        text += f"\n_Підтверджені (перші 5):_\n"
        for k, v in conf_items[:5]:
            text += f"\n✅ `{k[:28]}` → _{v.get('catalog_name','')[:38]}_"
    else:
        text += "\n_Кеш порожній_"

    # Навігація
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️", callback_data=f"ccp_{slug}_{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️", callback_data=f"ccp_{slug}_{page+1}"))
    if nav:
        mk.row(*nav)

    mk.add(
        InlineKeyboardButton("✅ Підтвердити всі авто", callback_data=f"cck_all_{slug}"),
        InlineKeyboardButton("🧹 Очистити авто",        callback_data=f"ccl__{slug}__auto"),
    )
    from config.settings import ADMIN_ID as _ADMIN
    if chat_id == _ADMIN:
        mk.add(
            InlineKeyboardButton("⚠️ Очистити підтв",  callback_data=f"ccl__{slug}__confirmed"),
            InlineKeyboardButton("💥 Очистити ВСЕ",    callback_data=f"ccl__{slug}__all"),
        )

    bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=mk)
