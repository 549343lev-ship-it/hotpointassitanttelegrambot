"""services/fix_service.py — Управління виправленнями (pending fixes).

Відповідає за:
- Збереження виправлень від менеджерів у чергу
- Застосування підтверджених виправлень адміном
- Генерація правила Claude після навчання
"""
import json
import time
import os

from config.settings import PENDING_FIXES_FILE, ADMIN_ID, ANTHROPIC_KEY
from clients.cache import cache_ban_pair, cache_confirm


# ─── Зберігання черги виправлень ─────────────────────────────────────────────

def load_pending_fixes() -> list:
    """Завантажує список виправлень що чекають підтвердження адміна."""
    if os.path.exists(PENDING_FIXES_FILE):
        try:
            with open(PENDING_FIXES_FILE, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_pending_fixes(fixes: list) -> None:
    """Зберігає список виправлень на диск."""
    with open(PENDING_FIXES_FILE, 'w', encoding='utf-8') as f:
        json.dump(fixes, f, ensure_ascii=False, indent=2)


def add_pending_fix(fix: dict) -> int:
    """Додає виправлення від менеджера у чергу. Повертає розмір черги."""
    fixes = load_pending_fixes()
    fix['date'] = time.strftime('%Y-%m-%d %H:%M')
    fixes.append(fix)
    save_pending_fixes(fixes)
    return len(fixes)


# ─── Застосування виправлення ─────────────────────────────────────────────────

def apply_fix(fix: dict) -> None:
    """Застосовує підтверджене адміном виправлення: старе → banned, нове → confirmed."""
    from clients import clients as _clients

    original = fix.get('original', '')
    cat      = fix.get('category', 'other')
    old      = fix.get('old_name')
    new      = fix.get('new_name')
    slug     = fix.get('client_slug')

    if old:
        cache_ban_pair(original, old, cat)
        if slug:
            _clients.client_cache_set_status(slug, original, old, 'banned')
    if new:
        cache_confirm(original, {}, fix.get('normalized', original), new, cat, source="training")
        if slug:
            _clients.client_cache_save(slug, original, new, cat, 100)
            _clients.client_cache_set_status(slug, original, new, 'confirmed')


# ─── Сповіщення адміна ────────────────────────────────────────────────────────

def notify_admin_fix(bot, username: str, original: str,
                     old_name: str | None, new_name: str | None, n: int) -> None:
    """Надсилає адміну повідомлення про нове виправлення від менеджера."""
    try:
        bot.send_message(
            ADMIN_ID,
            f"🔔 Виправлення від @{username} (в черзі: {n})\n"
            f"«{(original or '')[:45]}»\n"
            f"❌ {(old_name or '—')[:55]}\n"
            f"✅ {(new_name or '(тільки заборонити старе)')[:55]}\n\n"
            f"Підтвердити: 👑 Правила на розгляд"
        )
    except Exception:
        pass


# ─── Claude: авто-генерація правила ──────────────────────────────────────────

_kn_pending: dict[int, str] = {}   # chat_id → згенероване правило


def suggest_knowledge_rule(bot, chat_id: int,
                            original: str, old_name: str | None,
                            new_name: str) -> None:
    """Просить Claude сформулювати правило з виправлення і пропонує адміну."""
    import anthropic
    from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
        prompt = (
            f"Менеджер-сантехнік виправив підбір товару.\n"
            f"Написано в замовленні: «{original}»\n"
            f"Бот вибрав (НЕПРАВИЛЬНО): «{old_name or '(не знайшов)'}»\n"
            f"Правильна відповідь: «{new_name}»\n\n"
            f"Сформулюй ОДНЕ коротке правило (до 15 слів) українською, яке допоможе "
            f"боту наступного разу зрозуміти такий запит правильно. Правило має бути "
            f"загальним (про термін/скорочення/тип), а не про цей конкретний рядок.\n"
            f"Якщо корисного загального правила сформулювати не можна — напиши SKIP.\n"
            f"Відповідь: тільки текст правила або SKIP."
        )
        resp = client.messages.create(
            model='claude-sonnet-4-6', max_tokens=100,
            messages=[{'role': 'user', 'content': prompt}]
        )
        rule = resp.content[0].text.strip().strip('"«»')
        if not rule or 'SKIP' in rule.upper() or len(rule) > 200:
            return

        _kn_pending[chat_id] = rule
        mk = InlineKeyboardMarkup()
        mk.add(
            InlineKeyboardButton('✅ Додати в базу правил', callback_data='knok'),
            InlineKeyboardButton('❌ Ні',                   callback_data='knno'),
        )
        bot.send_message(
            chat_id,
            f"💡 Бот пропонує нове правило з цього виправлення:\n\n_{rule}_\n\n"
            f"Додати? (потрапить у знання для всіх наступних розпізнавань)",
            parse_mode='Markdown', reply_markup=mk
        )
    except Exception as e:
        print(f'⚠️ suggest_rule: {e}')


def get_kn_pending(chat_id: int) -> str | None:
    return _kn_pending.get(chat_id)


def pop_kn_pending(chat_id: int) -> str | None:
    return _kn_pending.pop(chat_id, None)
