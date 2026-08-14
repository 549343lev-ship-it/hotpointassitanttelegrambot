"""handlers/training_handler.py — Навчання бота (tr_*, вірно/помилка/виправ N)."""
import re
from config.settings import ADMIN_ID


def register(bot, state: dict):
    from clients.cache import (cache_ban_pair, cache_confirm, cache_set_status)
    from clients import clients
    from engine.search import keyword_search
    from services.fix_service import (add_pending_fix, notify_admin_fix,
                                       suggest_knowledge_rule,
                                       get_kn_pending, pop_kn_pending)
    from knowledge.rules import add_rule

    _train_state = state.setdefault('_train_state', {})
    _fix_state   = state.setdefault('_fix_state',   {})
    _manual_wait = state.setdefault('_manual_wait',  {})
    last_results = state.setdefault('last_results',  {})

    def _adm(uid): return uid == ADMIN_ID

    def safe_edit(cid, mid, txt):
        try: bot.edit_message_text(txt, cid, mid)
        except Exception: pass

    @bot.callback_query_handler(func=lambda c: c.data in ('tr_go', 'tr_close'))
    def tr_start(call):
        if call.data == 'tr_close':
            safe_edit(call.message.chat.id, call.message.message_id, '✖️ Закрито.')
            bot.answer_callback_query(call.id); return
        _train_state[call.message.chat.id] = {'stage': 'rows'}
        safe_edit(call.message.chat.id, call.message.message_id,
                  '✍️ Напиши номери НЕПРАВИЛЬНИХ рядків через пробіл (напр: 3 7 12):')
        bot.answer_callback_query(call.id)

    @bot.message_handler(
        func=lambda m: m.text and m.chat.id in _train_state
                       and _train_state[m.chat.id].get('stage') == 'rows'
                       and re.fullmatch(r'[\d\s,]+', m.text.strip()))
    def tr_rows(message):
        last  = last_results.get(message.chat.id)
        total = len(last['результати']) if last else 0
        nums  = sorted({int(x) for x in re.findall(r'\d+', message.text)
                        if 1 <= int(x) <= total})
        if not nums:
            bot.reply_to(message, f'⚠️ Валідних номерів немає (всього {total} рядків).')
            return
        _train_state[message.chat.id] = {'stage': 'classify', 'rows': nums, 'i': 0}
        _tr_show_row(message.chat.id)

    def _tr_show_row(chat_id):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        st   = _train_state.get(chat_id)
        last = last_results.get(chat_id)
        if not st or not last: return
        if st['i'] >= len(st['rows']):
            bot.send_message(chat_id, f"✅ Навчання завершено! Оброблено {len(st['rows'])} рядків.")
            _train_state.pop(chat_id, None); return
        row = st['rows'][st['i']]
        r   = last['результати'][row - 1]
        mk  = InlineKeyboardMarkup(row_width=1)
        mk.add(
            InlineKeyboardButton('📖 Бот неправильно ПРОЧИТАВ рядок', callback_data='tro'),
            InlineKeyboardButton('🎯 Прочитав вірно, товар НЕ ТОЙ',   callback_data='trg'),
            InlineKeyboardButton('⏭ Пропустити',                      callback_data='trs'),
        )
        m = bot.send_message(
            chat_id,
            f"🎓 Рядок {row} ({st['i']+1}/{len(st['rows'])})\n"
            f"Написано: {r.get('original','')[:50]}\n"
            f"Бот дав: {(r.get('назва','') or '❓ не знайдено')[:60]}\n\nЩо не так?",
            reply_markup=mk)
        st['msg_id'] = m.message_id

    @bot.callback_query_handler(
        func=lambda c: c.data in ('tro','trg','trw','trb','trc','trs','trn','trm')
                       or c.data.startswith('trp_'))
    def tr_classify(call):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        chat_id  = call.message.chat.id
        st       = _train_state.get(chat_id)
        last     = last_results.get(chat_id)
        if not st or not last:
            bot.answer_callback_query(call.id, 'Сесія застаріла'); return
        admin    = _adm(call.from_user.id)
        row      = st['rows'][st['i']]
        r        = last['результати'][row - 1]
        original = r.get('original','')
        old_name = r.get('назва','')
        cat      = r.get('category','other')
        cslug    = last.get('client_slug')
        uname    = call.from_user.username or str(call.from_user.id)

        def advance():
            st['i'] += 1; _tr_show_row(chat_id)

        if call.data == 'trs':
            bot.answer_callback_query(call.id, 'Пропущено'); advance(); return

        if call.data == 'trm':
            _manual_wait[chat_id] = {'mode': 'train'}
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id,
                '✍️ Напиши назву товару (як у прайсі):\n`коліно 110 45 остендорф`',
                parse_mode='Markdown')
            return

        if call.data == 'trn':
            if old_name:
                if admin:
                    cache_ban_pair(original, old_name, cat)
                    if cslug: clients.client_cache_set_status(cslug, original, old_name, 'banned')
                    bot.answer_callback_query(call.id, '❌ Забанено')
                else:
                    n = add_pending_fix({'original':original,'old_name':old_name,'new_name':None,
                                         'category':cat,'client_slug':cslug,
                                         'normalized':r.get('normalized',''),
                                         'user_id':call.from_user.id,'username':uname})
                    notify_admin_fix(bot, uname, original, old_name, None, n)
                    bot.answer_callback_query(call.id, '📥 Надіслано адміну')
            else:
                bot.answer_callback_query(call.id, 'Ок')
            advance(); return

        if call.data.startswith('trp_'):
            idx   = int(call.data[4:])
            cands = st.get('cands', [])
            if idx >= len(cands):
                bot.answer_callback_query(call.id, 'Застаріло'); return
            new_name  = cands[idx]
            save_orig = st.pop('ocr_new_original', None) or original
            if admin:
                if old_name:
                    cache_ban_pair(original, old_name, cat)
                    if cslug: clients.client_cache_set_status(cslug,original,old_name,'banned')
                cache_confirm(save_orig, {}, r.get('normalized', save_orig), new_name, cat, source="training")
                if save_orig != original:
                    cache_confirm(original, {}, save_orig, new_name, cat, source="training")
                if cslug:
                    clients.client_cache_save(cslug, save_orig, new_name, cat, 100)
                    clients.client_cache_set_status(cslug, save_orig, new_name, 'confirmed')
                r['назва'] = new_name
                safe_edit(chat_id, st.get('msg_id', call.message.message_id),
                          f"✅ Навчено!\n{original[:40]}\n❌ {old_name[:50] or '—'}\n✅ {new_name[:60]}")
                bot.answer_callback_query(call.id, '✅ Збережено')
                suggest_knowledge_rule(bot, chat_id, original, old_name, new_name)
            else:
                n = add_pending_fix({'original':original,'old_name':old_name or None,
                                     'new_name':new_name,'category':cat,'client_slug':cslug,
                                     'normalized':r.get('normalized',''),
                                     'user_id':call.from_user.id,'username':uname})
                notify_admin_fix(bot, uname, original, old_name, new_name, n)
                safe_edit(chat_id, st.get('msg_id', call.message.message_id),
                          f"📥 Надіслано адміну (черга: {n})\n{original[:40]}\n"
                          f"❌ {old_name[:50] or '—'}\n✅ {new_name[:60]}")
                bot.answer_callback_query(call.id, '📥 На розгляді')
            advance(); return

        if call.data == 'tro':
            _manual_wait[chat_id] = {'mode': 'ocr_fix'}
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id,
                f"📖 Бот прочитав з фото:\n«{original[:60]}»\n\n✍️ Напиши як НАСПРАВДІ написано:")
            return

        if call.data in ('trg','trw','trb','trc'):
            if old_name and admin:
                cache_ban_pair(original, old_name, cat)
                if cslug: clients.client_cache_set_status(cslug, original, old_name, 'banned')
            seen = [c for c in (r.get('candidates_debug') or []) if c and c != old_name][:6]
            if not seen:
                _manual_wait[chat_id] = {'mode': 'train'}
                bot.answer_callback_query(call.id)
                bot.send_message(chat_id, '✍️ Бот не мав кандидатів. Напиши назву товару:')
                return
            st['cands'] = seen
            mk = InlineKeyboardMarkup(row_width=1)
            for i2, name in enumerate(seen):
                mk.add(InlineKeyboardButton(f'{i2+1}. {name[:55]}', callback_data=f'trp_{i2}'))
            mk.add(InlineKeyboardButton('✍️ Немає тут — ввести з бази', callback_data='trm'))
            mk.add(InlineKeyboardButton('❌ Немає правильного (бан)',    callback_data='trn'))
            hdr = '❌ Забанено' if admin else '❌ Позначено'
            safe_edit(chat_id, st.get('msg_id', call.message.message_id),
                      f"🎯 Рядок {row}: {original[:45]}\n{hdr}: {old_name[:55] or '—'}\n\n"
                      f"Тапни ПРАВИЛЬНИЙ:")
            try:
                bot.edit_message_reply_markup(chat_id,
                    st.get('msg_id', call.message.message_id), reply_markup=mk)
            except Exception:
                m2 = bot.send_message(chat_id, 'Тапни правильний:', reply_markup=mk)
                st['msg_id'] = m2.message_id
            bot.answer_callback_query(call.id); return
        bot.answer_callback_query(call.id)

    # knok / knno
    @bot.callback_query_handler(func=lambda c: c.data in ('knok','knno'))
    def handle_knowledge_decision(call):
        chat_id = call.message.chat.id
        rule = pop_kn_pending(chat_id)
        bot.answer_callback_query(call.id)
        if call.data == 'knok' and rule:
            add_rule(rule)
            bot.edit_message_text(f'✅ Правило додано:\n_{rule}_',
                                  chat_id, call.message.message_id, parse_mode='Markdown')
        else:
            bot.edit_message_text('❌ Правило відхилено.',
                                  chat_id, call.message.message_id)

    # OCR save
    @bot.callback_query_handler(func=lambda c: c.data.startswith('ocrs_'))
    def handle_ocr_pair_save(call):
        from engine.ocr import save_ocr_correction
        bot.answer_callback_query(call.id)
        _, wrong, right = call.data.split(':', 2) if ':' in call.data else (None,None,None)
        if wrong and right:
            save_ocr_correction(wrong, right)
            bot.edit_message_text(f'✅ OCR пару збережено: `{wrong}` → `{right}`',
                                  call.message.chat.id, call.message.message_id,
                                  parse_mode='Markdown')

    # вірно N
    @bot.message_handler(func=lambda m: m.text and re.match(r'^вірно\s+\d+', m.text.lower()))
    def handle_virno(message):
        row  = int(re.search(r'\d+', message.text).group())
        last = last_results.get(message.chat.id)
        if not last: bot.reply_to(message, "⚠️ Немає замовлення в пам'яті."); return
        if row < 1 or row > len(last['результати']):
            bot.reply_to(message, f'⚠️ Рядок {row} не існує.'); return
        r        = last['результати'][row-1]
        original = r.get('original','')
        назва    = r.get('назва','')
        cat      = r.get('category','other')
        if not назва:
            bot.reply_to(message, '⚠️ Рядок не знайдено — використай `виправ N`',
                         parse_mode='Markdown'); return
        if _adm(message.from_user.id):
            if not cache_set_status(original, назва, 'confirmed'):
                cache_confirm(original, {}, r.get('normalized', original), назва, cat, source="training")
            if last.get('client_slug'):
                clients.client_cache_save(last['client_slug'], original, назва, cat, 100)
                clients.client_cache_set_status(last['client_slug'], original, назва, 'confirmed')
            bot.reply_to(message, f'✅ Рядок {row} підтверджено:\n{назва[:60]}')
        else:
            uname = message.from_user.username or str(message.from_user.id)
            n = add_pending_fix({'original':original,'old_name':None,'new_name':назва,
                                  'category':cat,'client_slug':last.get('client_slug'),
                                  'normalized':r.get('normalized',''),
                                  'user_id':message.from_user.id,'username':uname})
            notify_admin_fix(bot, uname, original, None, назва, n)
            bot.reply_to(message, f'📥 Підтвердження рядка {row} надіслано адміну (черга: {n})')

    # помилка N
    @bot.message_handler(func=lambda m: m.text and re.match(r'^помилка\s+\d+', m.text.lower()))
    def handle_pomylka(message):
        row  = int(re.search(r'\d+', message.text).group())
        last = last_results.get(message.chat.id)
        if not last: bot.reply_to(message, "⚠️ Немає замовлення в пам'яті."); return
        if row < 1 or row > len(last['результати']):
            bot.reply_to(message, f'⚠️ Рядок {row} не існує.'); return
        r        = last['результати'][row-1]
        original = r.get('original','')
        назва    = r.get('назва','')
        cat      = r.get('category','other')
        if not назва: bot.reply_to(message, '⚠️ Рядок і так не знайдено.'); return
        if _adm(message.from_user.id):
            cache_ban_pair(original, назва, cat)
            if last.get('client_slug'):
                clients.client_cache_set_status(last['client_slug'], original, назва, 'banned')
            bot.reply_to(message, f'❌ Рядок {row} забанено:\n{назва[:60]}')
        else:
            uname = message.from_user.username or str(message.from_user.id)
            n = add_pending_fix({'original':original,'old_name':назва,'new_name':None,
                                  'category':cat,'client_slug':last.get('client_slug'),
                                  'normalized':r.get('normalized',''),
                                  'user_id':message.from_user.id,'username':uname})
            notify_admin_fix(bot, uname, original, назва, None, n)
            bot.reply_to(message, f'📥 Позначку помилки рядка {row} надіслано адміну (черга: {n})')

    # виправ N
    @bot.message_handler(func=lambda m: m.text and re.match(r'^виправ\s+\d+', m.text.lower().strip()))
    def handle_fix(message):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        m_re   = re.match(r'^виправ\s+(\d+)(?:\s*=\s*(.+))?$', message.text.strip(), re.IGNORECASE)
        row    = int(m_re.group(1))
        manual = (m_re.group(2) or '').strip()
        last   = last_results.get(message.chat.id)
        if not last or not last['результати']:
            bot.reply_to(message, "⚠️ Немає замовлення в пам'яті."); return
        if row < 1 or row > len(last['результати']):
            bot.reply_to(message, f'⚠️ Рядок {row} не існує.'); return
        r     = last['результати'][row-1]
        query = manual or r.get('normalized') or r.get('original','')
        cur   = r.get('назва','')
        cands = [c['name'] for c in keyword_search(query, top_n=9) if c['name'] != cur][:8]
        if not cands:
            bot.reply_to(message, '😕 Кандидатів немає. `виправ N = інший текст`',
                         parse_mode='Markdown'); return
        _fix_state[message.chat.id] = {'row': row, 'cands': cands}
        mk = InlineKeyboardMarkup(row_width=1)
        for i, name in enumerate(cands):
            mk.add(InlineKeyboardButton(f'{i+1}. {name[:55]}', callback_data=f'fx_{i}'))
        mk.add(InlineKeyboardButton('✍️ Інший варіант', callback_data='fx_m'))
        bot.reply_to(message,
            f'🔍 Рядок {row}: `{r.get("original","")[:40]}`\n'
            f'Поточний: {cur[:50] or "—"}\n\nОбери правильний:',
            parse_mode='Markdown', reply_markup=mk)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('fx_'))
    def handle_fix_pick(call):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        chat_id = call.message.chat.id
        fs      = _fix_state.get(chat_id)
        last    = last_results.get(chat_id)
        if not fs or not last:
            bot.answer_callback_query(call.id, 'Застаріло'); return
        if call.data == 'fx_m':
            _manual_wait[chat_id] = {'mode': 'fix', 'row': fs['row']}
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, '✍️ Напиши назву товару (як у прайсі):'); return
        idx  = int(call.data[3:])
        cands = fs.get('cands', [])
        if idx >= len(cands):
            bot.answer_callback_query(call.id, 'Застаріло'); return
        new_name = cands[idx]
        row      = fs['row']
        r        = last['результати'][row-1]
        original = r.get('original','')
        old_name = r.get('назва','')
        cat      = r.get('category','other')
        cslug    = last.get('client_slug')
        admin    = _adm(call.from_user.id)
        uname    = call.from_user.username or str(call.from_user.id)
        if admin:
            if old_name:
                cache_ban_pair(original, old_name, cat)
                if cslug: clients.client_cache_set_status(cslug, original, old_name, 'banned')
            cache_confirm(original, {}, r.get('normalized', original), new_name, cat, source="training")
            if cslug:
                clients.client_cache_save(cslug, original, new_name, cat, 100)
                clients.client_cache_set_status(cslug, original, new_name, 'confirmed')
            r['назва'] = new_name
            bot.edit_message_text(
                f'✅ Рядок {row} виправлено:\n❌ {old_name[:55] or "—"}\n✅ {new_name[:60]}',
                chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, '✅')
            suggest_knowledge_rule(bot, chat_id, original, old_name, new_name)
        else:
            n = add_pending_fix({'original':original,'old_name':old_name or None,
                                  'new_name':new_name,'category':cat,'client_slug':cslug,
                                  'normalized':r.get('normalized',''),
                                  'user_id':call.from_user.id,'username':uname})
            notify_admin_fix(bot, uname, original, old_name, new_name, n)
            bot.edit_message_text(
                f'📥 Надіслано адміну (черга: {n})\n'
                f'❌ {old_name[:55] or "—"}\n✅ {new_name[:60]}',
                chat_id, call.message.message_id)
            bot.answer_callback_query(call.id, '📥 На розгляді')
        _fix_state.pop(chat_id, None)
