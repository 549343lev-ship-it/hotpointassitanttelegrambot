"""engine/order_context.py — Stage 1.5: розуміння замовлення цілком (п.1, 3–6, 12, 20)."""
from __future__ import annotations

import json
import logging
import os

import anthropic

from engine.search import claude, BRAND_TOKENS
from engine.kits import kits_prompt_block

log = logging.getLogger(__name__)

MODEL = os.getenv("ORDER_CTX_MODEL", "claude-sonnet-4-5")

SYSTEM_CAT: dict[str, str] = {
    "ppr": "plastic_ppr",
    "push": "push_systems",
    "sewage": "sewage",
    "underfloor": "underfloor_heating",
    "mp": "metal_plastic",
}

_PROMPT = """Ти — старший менеджер Hotpoint (сантехніка/опалення). Нижче ВСЕ замовлення (усі фото разом) після OCR
і повідомлення менеджера. Розбери його як ОДИН ОБ'ЄКТ, а не окремі рядки.

1. brand_map: система → виробник ТІЛЬКИ з повідомлення менеджера ("пуш рафтек" → push: raftec,
   "каналізація асг" → sewage: asg). Не вигадуй.
2. Для КОЖНОГО рядка визнач system: ppr | push | sewage | underfloor | mp | other. Правила:
   - "скло", "армована", "basalt" → ppr;
   - ф32/40/50/75/110 з кутами 15/30/45/67/87/90, редукції, хомути ф50/110, змазка, компенсаційний патрубок → sewage;
   - труба на т/п, ф16×2,0, євроконус, гребінка/колектор, змішувальний вузол, насос до вузла, скоби, плівка,
     шафа/люк під гребінку, торцеві/кінцеві елементи → underfloor;
   - якщо менеджер вказав PUSH-бренд: водопровідні ф16/20/25/32 БЕЗ "скло" → push;
   - PUSH має ТІЛЬКИ кути 90°: рядок PUSH-діаметра з 45°/30° → system=ppr, note="в PUSH немає <кут>";
   - "монтажка ф20" у PUSH → коліно настінне ф20×1/2" РВ.
3. Рядок-продовження ("0,5м – 3шт" під "Труба ф50 1м – 4шт", "ф110×30° – 1шт" під "Коліно ф110×45°")
   → допиши тип і діаметр попереднього рядка.
4. Комплекти (KITS нижче): якщо ≥3 рядки збігаються з ролями комплекту — вкажи kit та kit_role для КОЖНОГО
   такого рядка (включно з "гребінка", "насос", "торцеві", "шафа", "труба т/п", "скоби", "плівка").
   circuits — кількість контурів ("6к" → 6).
5. normalized — назва для пошуку в прайсі: тип, система (PPR / натяжний PUSH / внут. канал.), діаметри,
   кут, різьба (РВ/РЗ, МРЗ/МРВ), серія, бренд.
6. type — одним словом (труба/коліно/трійник/муфта/перехід/кран/заглушка/утеплювач/...); dia — список чисел;
   angle — число або null; thread — "1/2", "3/4" або null.
7. note — тільки якщо є сумнів (почерк, припущення). Інакше "".

KITS:
{kits}

ПОВІДОМЛЕННЯ МЕНЕДЖЕРА: {caption}

РЯДКИ (i. оригінал | OCR-нормалізація | к-сть | категорія OCR):
{lines}

Відповідь — ТІЛЬКИ JSON-об'єкт:
{{"brand_map": {{"push": "raftec"}}, "kits": [{{"id": "tp_v2", "circuits": 6}}],
 "lines": [{{"i": 0, "system": "push", "normalized": "...", "type": "коліно", "dia": [20], "angle": null,
            "thread": "1/2", "qty": "11", "kit": null, "kit_role": null, "note": ""}}]}}"""


def _json_obj(raw: str) -> dict:
    """Витягує перший JSON-об'єкт з тексту (підрахунок глибини дужок)."""
    start = raw.find("{")
    if start < 0:
        return {}
    depth, in_str, esc = 0, False, False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start:i + 1])
                except json.JSONDecodeError:
                    return {}
    return {}


_PROMPT_PROJECT = """Ти — старший менеджер Hotpoint. Нижче позиції, зняті з ПРОЕКТНОЇ СПЕЦИФІКАЦІЇ,
і повідомлення менеджера. Твоє завдання — не переписувати їх, а доповнити контекстом.

1. brand_map: система → виробник, якщо його назвав менеджер у повідомленні. Бренди з самого проекту
   сюди НЕ пиши — для них є поле brand у рядку.
2. Для КОЖНОГО рядка визнач system: ppr | push | sewage | underfloor | mp | other.
   ПВХ/ПП ф50 і ф110 з кутами 45/87/90, ревізії, хрестовини, заглушки → sewage;
   PP-R, PPR, поліпропілен → ppr; металопластик → mp.
3. add — коротке уточнення, яке треба ДОПИСАТИ до назви (матеріал, серія, бренд з проекту:
   "PP-R Ekoplastik", "Thermaflex FRZ 13мм"). Якщо дописувати нічого — "".
4. brand — виробник, якого ВИМАГАЄ проект для цього рядка, або "". Це побажання проекту,
   а не жорсткий фільтр: воно піде в примітку.
5. НЕ змінюй кількість, одиницю виміру і section. НЕ об'єднуй рядки. НЕ вигадуй нових.
6. note — сумнів або важливе уточнення з проекту ("під підлогою", "для людей з обмеженими можливостями").

ПОВІДОМЛЕННЯ МЕНЕДЖЕРА: {caption}

РЯДКИ (i. оригінал | нормалізація | к-сть | розділ | категорія):
{lines}

Відповідь — ТІЛЬКИ JSON-об'єкт:
{{"brand_map": {{}}, "kits": [],
 "lines": [{{"i": 0, "system": "ppr", "add": "PP-R Ekoplastik", "brand": "Ekoplastik", "note": ""}}]}}"""


def analyze_order(позиції: list[dict], caption: str, mode: str = "order") -> dict:
    """Один LLM-виклик на все замовлення. При помилці повертає {} (пайплайн працює як раніше)."""
    if not позиції:
        return {}
    if mode == "project":
        lines = "\n".join(
            f"{i}. {п.get('original', '')} | {п.get('normalized', '')} | {п.get('qty', '')} | "
            f"{п.get('section', '')} | {п.get('category', '')}"
            for i, п in enumerate(позиції)
        )
        prompt = _PROMPT_PROJECT.format(caption=caption or "—", lines=lines)
    else:
        lines = "\n".join(
            f"{i}. {п.get('original', '')} | {п.get('normalized', '')} | {п.get('qty', '')} | {п.get('category', '')}"
            for i, п in enumerate(позиції)
        )
        prompt = _PROMPT.format(kits=kits_prompt_block(), caption=caption or "—", lines=lines)
    try:
        resp = claude.messages.create(
            model=MODEL, max_tokens=16384, temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        return _json_obj(resp.content[0].text)
    except anthropic.APIError as e:
        log.warning("order_context: %s", e)
        return {}


def apply_order_context(позиції: list[dict], ctx: dict, mode: str = "order") -> list[dict]:
    """Переносить рішення контексту в позиції: категорія, бренд, нормалізація, комплект, примітки."""
    if not ctx:
        return позиції
    brand_by_cat: dict[str, list[str]] = {}
    for system, brand in (ctx.get("brand_map") or {}).items():
        tokens = BRAND_TOKENS.get(str(brand).lower().strip())
        if system in SYSTEM_CAT and tokens:
            brand_by_cat[SYSTEM_CAT[system]] = tokens
    kits = {k.get("id"): k for k in ctx.get("kits") or [] if k.get("id")}

    for ln in ctx.get("lines") or []:
        i = ln.get("i")
        if not isinstance(i, int) or not 0 <= i < len(позиції):
            continue
        п = позиції[i]
        system = ln.get("system")
        if mode == "project":                       # проект: тільки доповнюємо, нічого не переписуємо
            if system in SYSTEM_CAT:
                п.update(category=SYSTEM_CAT[system], _ctx_category=SYSTEM_CAT[system], _system=system)
                if SYSTEM_CAT[system] in brand_by_cat:
                    п["_ctx_brand"] = brand_by_cat[SYSTEM_CAT[system]]
            if ln.get("add") and ln["add"].lower() not in п.get("normalized", "").lower():
                п["normalized"] = f"{п.get('normalized', '')} {ln['add']}".strip()
                п.pop("_qa", None)
            if ln.get("brand"):
                п.setdefault("notes", []).append(f"⚠️ за проектом: {ln['brand']}")
            if ln.get("note"):
                п.setdefault("notes", []).append(f"⚠️ {ln['note']}")
            continue
        if system in SYSTEM_CAT:
            cat = SYSTEM_CAT[system]
            п.update(category=cat, _ctx_category=cat, _system=system)
            if cat in brand_by_cat:
                п["_ctx_brand"] = brand_by_cat[cat]
        for key in ("normalized", "type", "qty"):
            if ln.get(key):
                п[key] = ln[key]
        if isinstance(ln.get("dia"), list) and ln["dia"]:
            п["dia"] = ln["dia"]
        if "angle" in ln:
            п["angle"] = ln["angle"]
        if "thread" in ln:
            п["thread"] = ln["thread"]
        п.pop("_qa", None)                      # атрибути перебудуються з нових полів
        kit = kits.get(ln.get("kit"))
        if kit:
            п.update(_kit=kit["id"], _kit_role=ln.get("kit_role"), _kit_n=kit.get("circuits"))
        if ln.get("note"):
            п.setdefault("notes", []).append(f"⚠️ {ln['note']}")
    return позиції
