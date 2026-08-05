"""handlers/rules_handler.py — Правила, OCR корекції, pending fixes рішення адміна."""
import re
import os
from config.settings import ADMIN_ID, DATA_DIR


def register(bot, state: dict):
    from knowledge.rules import (get_rules, add_rule, delete_rule,
                                  load_pending_rules, add_pending_rule)
    from engine.ocr import save_ocr_correction, load_ocr_corrections
    from services.fix_service import (load_pending_fixes, save_pending_fixes,
                                       apply_fix, notify_admin_fix)
    from clients.pending_cache import (pending_count, pending_get_batch,
                                       pending_confirm, pending_reject,
                                       pending_confirm_all_batch, pending_reject_all_batch,
                                       pending_clear_all)

    def _adm(uid): return uid == ADMIN_ID

    # ── OCR корекції ──────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('ocr '))
    def handle_ocr_correction(message):
        rest = message.text[4:].strip()
        if '=' not in rest:
            bot.reply_to(message,
                '`ocr <неправильно> = <правильно>`\n`ocr список` — переглянути',
                parse_mode='Markdown'); return
        wrong, right = [x.strip() for x in rest.split('=', 1)]
        if not wrong or not right:
            bot.reply_to(message, '⚠️ Вкажи і неправильне і правильне.'); return
        save_ocr_correction(wrong, right)
        bot.reply_to(message, f'✅ Збережено: `{wrong}` → `{right}`', parse_mode='Markdown')

    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'ocr список')
    def handle_ocr_list(message):
        d = load_ocr_corrections()
        if not d:
            bot.reply_to(message, '📋 Корекцій OCR немає.\n`ocr слово = правильне`',
                         parse_mode='Markdown'); return
        lines = [f'  `{w}` → `{r}`' for w, r in d.items()]
        bot.reply_to(message, f"📋 OCR корекції ({len(d)}):\n" + "\n".join(lines),
                     parse_mode='Markdown')

    # ── Правила ───────────────────────────────────────────────────────────────
    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'правила')
    def handle_rules_show(message):
        text = get_rules()
        if not text.strip():
            bot.reply_to(message, '📖 База правил порожня.'); return
        lines = text.splitlines()
        chunk, out, total = [], [], 0
        for i, l in enumerate(lines, 1):
            row = f'{i}. {l}' if l.strip() and not l.startswith('#') else l
            if total + len(row) > 3500:
                out.append('\n'.join(chunk)); chunk, total = [], 0
            chunk.append(row); total += len(row) + 1
        if chunk: out.append('\n'.join(chunk))
        for part in out[:5]:
            bot.send_message(message.chat.id, part)
        bot.send_message(message.chat.id,
            f'📖 Всього {len(lines)} рядків.\n'
            '`правило <текст>` — додати | `правило видалити N` — прибрати\n'
            '`оновити правила` — підтягнути з GitHub',
            parse_mode='Markdown')

    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'оновити правила')
    def handle_rules_pull(message):
        if not _adm(message.from_user.id): return
        try:
            from catalog import storage as _st
            text, _sha = _st._get_remote('rules.txt')
            if text:
                rules_path = os.path.join(DATA_DIR, 'rules.txt')
                with open(rules_path, 'w', encoding='utf-8') as f:
                    f.write(text)
                bot.reply_to(message, f'✅ Підтягнуто: {len(text.splitlines())} рядків.')
            else:
                bot.reply_to(message, '⚠️ Не вдалося (немає токена або файлу).')
        except Exception as e:
            bot.reply_to(message, f'⚠️ {e}')

    @bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('правило'))
    def handle_rule(message):
        m_del = re.match(r'^правило\s+видалити\s+(\d+)$', message.text.lower().strip())
        if m_del:
            if not _adm(message.from_user.id):
                bot.reply_to(message, '⛔ Тільки адмін.'); return
            ok, msg = delete_rule(int(m_del.group(1)))
            bot.reply_to(message, f'🗑 {msg[:60]}' if ok else f'⚠️ {msg}'); return
        rule = message.text[7:].strip()
        if not rule:
            bot.reply_to(message, "Напиши правило після слова 'правило'."); return
        if _adm(message.from_user.id):
            add_rule(rule)
            bot.reply_to(message, f'✅ Записав:\n_{rule}_', parse_mode='Markdown')
        else:
            n = add_pending_rule(rule, message.from_user.id,
                                  message.from_user.username or str(message.from_user.id))
            bot.reply_to(message,
                f'📥 Правило відправлено на розгляд ({n} в черзі):\n_{rule}_',
                parse_mode='Markdown')
            try:
                bot.send_message(ADMIN_ID,
                    f'🔔 Нове правило від @{message.from_user.username}:\n`{rule}`',
                    parse_mode='Markdown')
            except Exception:
                pass

    # ── Pending fixes — рішення адміна ────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith(('approve_', 'reject_')))
    def handle_rule_decision(call):
        if not _adm(call.from_user.id):
            bot.answer_callback_query(call.id, '⛔ Тільки адмін'); return
        action, idx_s = call.data.split('_', 1)
        try:
            idx = int(idx_s)
        except ValueError:
            bot.answer_callback_query(call.id); return
        pending = load_pending_rules()
        if idx >= len(pending):
            bot.answer_callback_query(call.id, 'Застаріло'); return
        rule = pending[idx]
        if action == 'approve':
            add_rule(rule.get('text', ''))
            result = f"✅ Правило додано:\n_{rule.get('text', '')[:60]}_"
        else:
            result = f"❌ Відхилено:\n_{rule.get('text', '')[:60]}_"
        pending.pop(idx)
        from knowledge.rules import save_pending_rules
        save_pending_rules(pending)
        bot.answer_callback_query(call.id)
        bot.edit_message_text(result, call.message.chat.id, call.message.message_id,
                               parse_mode='Markdown')

    @bot.callback_query_handler(func=lambda c: c.data.startswith(('fixok_', 'fixno_')))
    def handle_fixq_decision(call):
        if not _adm(call.from_user.id):
            bot.answer_callback_query(call.id, '⛔ Тільки адмін'); return
        action, idx_s = call.data.split('_', 1)
        try:
            idx = int(idx_s)
        except ValueError:
            bot.answer_callback_query(call.id); return
        fixes = load_pending_fixes()
        if idx >= len(fixes):
            bot.answer_callback_query(call.id, 'Застаріло'); return
        fix = fixes[idx]
        if action == 'fixok':
            apply_fix(fix)
            result = (f"✅ Виправлення застосовано:\n"
                      f"`{fix.get('original','')[:40]}`\n"
                      f"❌ {fix.get('old_name','—')[:50]}\n"
                      f"✅ {fix.get('new_name','—')[:50]}")
        else:
            result = f"❌ Виправлення відхилено:\n`{fix.get('original','')[:40]}`"
        fixes.pop(idx)
        save_pending_fixes(fixes)
        bot.answer_callback_query(call.id)
        bot.edit_message_text(result, call.message.chat.id, call.message.message_id,
                               parse_mode='Markdown')

    # ── Pending кеш (pc_*) ────────────────────────────────────────────────────
    @bot.callback_query_handler(func=lambda c: c.data.startswith('pc_all:'))
    def handle_pc_all(call):
        if not _adm(call.from_user.id):
            bot.answer_callback_query(call.id, '⛔'); return
        batch = pending_get_batch(0)
        n = pending_confirm_all_batch(batch)
        bot.answer_callback_query(call.id, f'✅ Підтверджено {n}')
        bot.edit_message_text(f'✅ Підтверджено {n} збігів.',
                               call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('pc_rej:'))
    def handle_pc_rej(call):
        if not _adm(call.from_user.id):
            bot.answer_callback_query(call.id, '⛔'); return
        batch = pending_get_batch(0)
        n = pending_reject_all_batch(batch)
        bot.answer_callback_query(call.id, f'❌ Відхилено {n}')
        bot.edit_message_text(f'❌ Відхилено {n} збігів.',
                               call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('pc_pick:'))
    def handle_pc_pick(call):
        from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
        if not _adm(call.from_user.id):
            bot.answer_callback_query(call.id, '⛔'); return
        bot.answer_callback_query(call.id)
        batch = pending_get_batch(0)
        for i, item in enumerate(batch[:10]):
            orig = item.get('original','')[:30]
            name = item.get('name','')[:40]
            conf = item.get('confidence', 0)
            mk = InlineKeyboardMarkup(row_width=2)
            mk.add(
                InlineKeyboardButton('✅', callback_data=f'pc_one_yes:{i}'),
                InlineKeyboardButton('❌', callback_data=f'pc_one_no:{i}'),
            )
            bot.send_message(call.message.chat.id,
                             f'`{orig}` → *{name}* ({conf}%)',
                             parse_mode='Markdown', reply_markup=mk)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('pc_one_yes:'))
    def handle_pc_one_yes(call):
        if not _adm(call.from_user.id):
            bot.answer_callback_query(call.id, '⛔'); return
        idx   = int(call.data.split(':')[1])
        batch = pending_get_batch(0)
        if idx < len(batch):
            pending_confirm(batch[idx])
            bot.edit_message_text('✅ Збережено.',
                                   call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data.startswith('pc_one_no:'))
    def handle_pc_one_no(call):
        if not _adm(call.from_user.id):
            bot.answer_callback_query(call.id, '⛔'); return
        idx   = int(call.data.split(':')[1])
        batch = pending_get_batch(0)
        if idx < len(batch):
            pending_reject(batch[idx])
            bot.edit_message_text('❌ Відхилено.',
                                   call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    @bot.callback_query_handler(func=lambda c: c.data == 'pc_clear_all')
    def handle_pc_clear_all(call):
        if not _adm(call.from_user.id):
            bot.answer_callback_query(call.id, '⛔'); return
        n = pending_clear_all()
        bot.answer_callback_query(call.id, f'🗑 Видалено {n}')
        bot.edit_message_text(f'🗑 Очищено: {n} записів.',
                               call.message.chat.id, call.message.message_id)

    @bot.callback_query_handler(func=lambda c: c.data == 'pc_next')
    def handle_pc_next(call):
        if not _adm(call.from_user.id):
            bot.answer_callback_query(call.id); return
        count = pending_count()
        if count == 0:
            bot.edit_message_text('✅ Черга порожня.',
                                   call.message.chat.id, call.message.message_id)
        else:
            bot.answer_callback_query(call.id, f'{count} ще в черзі')
