"""engine/review.py — самоперевірка готового підбору (те, що я роблю в кінці: перечитую свій результат).

Один виклик моделі бачить УВЕСЬ готовий список поруч з оригіналом замовлення і шукає те,
чого не видно на рівні окремого рядка: не та система, не той діаметр, штуки замість метрів,
дубль, випадіння з логіки решти замовлення. Де модель знає кращий запит — робиться повторний
пошук саме по тих рядках.
"""
from __future__ import annotations

import logging
import os

import anthropic

from engine.order_context import _json_obj
from engine.search import claude, find_items

log = logging.getLogger(__name__)

MODEL = os.getenv("REVIEW_MODEL", os.getenv("ORDER_CTX_MODEL", "claude-sonnet-4-5"))
MAX_REQUERY = int(os.getenv("REVIEW_MAX_REQUERY", "25"))

_PROMPT = """Ти — старший менеджер Hotpoint. Нижче ГОТОВИЙ підбір за замовленням клієнта.
Твоє завдання — перечитати його ЦІЛКОМ, як перед відправкою клієнту, і знайти помилки,
які не видно на рівні одного рядка.

На що дивитись:
1. Система: рядок PUSH підібраний у PPR? Каналізаційний фітинг узятий з водопровідних?
2. Розмір, кут, різьба: 45° замість 90°, ф25 замість ф20, РВ замість РЗ, 3/4" замість 1/2".
3. Одиниця виміру: труба в метрах, а не в штуках; бухта на 100 м проти 200 м; кількість секцій радіатора.
4. Узгодженість із рештою: якщо вся розводка одного бренду й системи, один рядок з іншої — підозрілий.
   Якщо в замовленні є труба, а фітинги під неї іншого стандарту — це помилка.
5. Явно не те: підібрано товар іншого призначення, ніж написано в оригіналі.
6. Комплектність: чого бракує, щоб змонтувати (без вигадування — лише очевидне).

Для КОЖНОГО рядка дай verdict:
- "ok" — все добре (більшість рядків);
- "suspect" — може бути не те, потрібне око менеджера;
- "wrong" — точно не те.
Для suspect/wrong коротко напиши reason. Якщо знаєш, ЧИМ це замінити, дай requery —
точний пошуковий запит (тип + система + діаметри + кут + різьба + бренд), за яким треба
перешукати в прайсі. Без requery рядок просто отримає позначку.

ЗАМОВЛЕННЯ І ПІДБІР (i | оригінал | знайдено | к-сть | джерело):
{lines}

Відповідь — ТІЛЬКИ JSON-об'єкт, без пояснень:
{{"lines": [{{"i": 0, "verdict": "ok"}},
            {{"i": 7, "verdict": "wrong", "reason": "кут 45° у PUSH не існує",
              "requery": "Коліно PPR 45° ф 20 Ekoplastik"}}],
 "missing": ["чого бракує для монтажу — або порожній список"]}}"""


def _line(i: int, пос: dict, r: dict) -> str:
    found = r.get("назва", "—") if r and r.get("знайдено") else "❓ НЕ ЗНАЙДЕНО"
    return (f"{i}. {пос.get('original', '')} | {found} | "
            f"{пос.get('qty', '')} | {r.get('джерело', '') if r else ''}")


def review_order(позиції: list[dict], результати: list[dict]) -> dict:
    """Один виклик моделі. При будь-якій помилці повертає {} — підбір лишається як був."""
    if not позиції:
        return {}
    lines = "\n".join(_line(i, п, r) for i, (п, r) in enumerate(zip(позиції, результати)))
    try:
        resp = claude.messages.create(
            model=MODEL, max_tokens=16384, temperature=0,
            messages=[{"role": "user", "content": _PROMPT.format(lines=lines)}],
        )
        return _json_obj(resp.content[0].text)
    except anthropic.APIError as e:
        log.warning("review_order: %s", e)
        return {}


def review_and_fix(позиції: list[dict], результати: list[dict]) -> list[dict]:
    """Перевіряє готовий підбір, перешукує те, для чого модель дала кращий запит, і пише примітки."""
    verdicts = review_order(позиції, результати)
    if not verdicts:
        return результати

    requery: list[tuple[int, dict]] = []
    for ln in verdicts.get("lines") or []:
        i = ln.get("i")
        if not isinstance(i, int) or not 0 <= i < len(позиції):
            continue
        verdict = ln.get("verdict")
        if verdict not in ("wrong", "suspect"):
            continue
        mark = "❗" if verdict == "wrong" else "⚠️"
        reason = ln.get("reason") or "перевірити"
        r = результати[i]
        if r:
            r["reason"] = f"{mark} перевірка: {reason}. {r.get('reason', '')}".strip()
            r["brand_warning"] = r.get("brand_warning") or f"{mark} {reason}"
        if ln.get("requery") and len(requery) < MAX_REQUERY:
            пос = dict(позиції[i])
            пос["normalized"] = ln["requery"]
            пос.pop("_qa", None)
            пос.pop("_forced", None)
            пос["notes"] = list(пос.get("notes", [])) + [f"{mark} повторний пошук: {reason}"]
            requery.append((i, пос))

    if requery:
        нові = find_items([п for _, п in requery])
        for (i, _), new_r in zip(requery, нові):
            old_r = результати[i]
            if new_r and new_r.get("знайдено"):
                if old_r and new_r.get("назва") == old_r.get("назва"):
                    continue                      # той самий товар — підбір підтверджено
                new_r["джерело"] = "🔁 після перевірки"
                if old_r and old_r.get("знайдено"):
                    new_r["reason"] = f"{new_r.get('reason', '')} (було: {old_r['назва']})".strip()
                результати[i] = new_r

    missing = [m for m in (verdicts.get("missing") or []) if isinstance(m, str)][:10]
    if missing and результати:
        for r in результати:
            if r:
                r.setdefault("_order_note", "; ".join(missing))
                break
    return результати


def missing_note(результати: list[dict]) -> str:
    """Рядок для повідомлення менеджеру: чого, на думку перевірки, бракує в замовленні."""
    for r in результати:
        if r and r.get("_order_note"):
            return "🔎 Можливо, бракує: " + r["_order_note"]
    return ""
