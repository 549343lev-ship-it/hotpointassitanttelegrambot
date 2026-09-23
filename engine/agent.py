"""engine/agent.py — підбір замовлення так, як це робить Claude у чаті.

Чому окремий модуль. Класичний пайплайн — це ланцюг евристик (параметричний пошук, Voyage,
node_pool, attr, keyword, авто-прийоми за порогами), а модель лише вибирає з 8–12 кандидатів,
які їй підсунули. У чаті Claude працює навпаки: САМ читає фото, САМ шукає в прайсі скільки
завгодно разів, звужуючи запит, і САМ вирішує. Цей модуль відтворює саме такий спосіб роботи.

Дві фази:
  1. transcribe_order — модель дивиться на фото/текст і повертає розібране замовлення:
     рядки, кількості, система кожного рядка, бренди з підказки, комплекти.
  2. match_order — для кожної пачки рядків модель у циклі викликає інструменти пошуку
     по прайсу (regex, як grep), доки не знайде товар, і здає результат через submit_matches.

Вмикається ENV: PIPELINE=agent. Класичний пайплайн лишається як є.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic

from catalog.catalog import CATALOG
from engine.search import claude

log = logging.getLogger(__name__)

MODEL = os.getenv("AGENT_MODEL", "claude-sonnet-4-5")            # читання фото + складні рядки
FAST_MODEL = os.getenv("AGENT_FAST_MODEL", "claude-haiku-4-5")   # швидкий вибір з готових кандидатів
FAST_CANDIDATES = int(os.getenv("AGENT_FAST_CANDIDATES", "25"))

# $ за 1М токенів: (input, output). Кешоване читання = 10% input, запис у кеш = 125%.
PRICES = {"claude-sonnet-4-5": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0)}
_COST = {"usd": 0.0, "calls": 0}
_COST_LOCK = __import__("threading").Lock()
CHUNK = int(os.getenv("AGENT_CHUNK", "15"))           # рядків на один цикл пошуку
MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "30"))    # кроків циклу на пачку
PARALLEL = int(os.getenv("AGENT_PARALLEL", "3"))     # пачок одночасно
SEARCH_LIMIT = 30

_BAD = re.compile(r"ЗРАЗ(О|К)|ОБРАЗЕЦ|ЧАСТИНИ|КУСКИ|пошкоджен|виведено з асорт", re.I)
CATEGORIES = sorted({it.get("category", "") for it in CATALOG if it.get("category")})


# ─────────────────────────── знання ───────────────────────────

def _knowledge() -> str:
    parts = []
    try:
        from knowledge.knowledge import get_knowledge
        parts.append(get_knowledge())
    except Exception as e:                                  # знання не критичні для запуску
        log.warning("agent: knowledge: %s", e)
    try:
        from knowledge.rules import get_rules
        parts.append("# ПРАВИЛА МАГАЗИНУ (вищий пріоритет):\n" + get_rules())
    except Exception as e:
        log.warning("agent: rules: %s", e)
    return "\n\n".join(parts)


# ─────────────────────────── інструменти ───────────────────────────

def _rx(term: str) -> re.Pattern:
    try:
        return re.compile(term, re.I)
    except re.error:
        return re.compile(re.escape(term), re.I)


def tool_search_catalog(terms: list[str], category: str | None = None,
                        exclude: list[str] | None = None, limit: int = SEARCH_LIMIT) -> str:
    """Пошук по прайсу: усі terms мають знайтись у назві (regex, без регістру)."""
    rx = [_rx(t) for t in terms if t]
    ex = [_rx(t) for t in (exclude or []) if t]
    limit = max(1, min(int(limit or SEARCH_LIMIT), 80))
    hits = []
    for idx, it in enumerate(CATALOG):
        if category and it.get("category") != category:
            continue
        name = it.get("name", "")
        if _BAD.search(name):
            continue
        if all(r.search(name) for r in rx) and not any(e.search(name) for e in ex):
            hits.append(idx)
    hits.sort(key=lambda i: ("(п/з)" in CATALOG[i]["name"], len(CATALOG[i]["name"])))
    if not hits:
        return "0 результатів. Спробуй ширше: менше слів, синоніми, інше написання розміру (ф20 / ф 20 / 20х)."
    rows = [f"id={i} | {CATALOG[i].get('name_full') or CATALOG[i]['name']} | {CATALOG[i].get('price', '')}"
            for i in hits[:limit]]
    more = f"\n… ще {len(hits) - limit}. Звузь запит." if len(hits) > limit else ""
    return f"{len(hits)} результатів:\n" + "\n".join(rows) + more


def tool_get_kit(kit_id: str, circuits: int, wall: str = "2,0") -> str:
    """Готові позиції комплекту з kits.json."""
    from engine.kits import load_kits, resolve_kit_items
    kit = load_kits().get(kit_id)
    if not kit:
        return f"Немає комплекту {kit_id}. Є: {', '.join(load_kits())}"
    probe = [{"_kit": kit_id, "_kit_role": role, "_kit_n": circuits, "qty": "1",
              "original": f"16x{wall}"} for role in kit["roles"]]
    resolve_kit_items(probe, CATALOG)
    out = []
    for p in probe:
        f = p.get("_forced")
        if f:
            out.append(f"{p['_kit_role']}: id={CATALOG.index(f)} | {f.get('name_full') or f['name']}")
        else:
            out.append(f"{p['_kit_role']}: не знайдено")
    return "\n".join(out)


def tool_lookup_confirmed(originals: list[str]) -> str:
    """Що менеджери вже підтвердили для таких самих формулювань (кеш «Навчання»)."""
    try:
        from clients.cache import cache_lookup
    except ImportError:
        return "кеш недоступний"
    out = []
    for o in originals[:30]:
        hit = cache_lookup(o, {})
        if hit and hit.get("status") == "confirmed":
            out.append(f"{o} → {hit.get('catalog_name')}")
    return "\n".join(out) or "підтверджених відповідностей немає"


TOOLS = [
    {
        "name": "search_catalog",
        "description": (
            "Пошук у прайсі Hotpoint (~49 тис. товарів). УСІ terms мають знайтись у назві "
            "(regex, без регістру). Приклади terms: [\"Коліно\", \"PPR\", \"ф ?20\", \"45\"], "
            "[\"Трійник\", \"ф ?110\", \"50\", \"HTR\", \"ASG\"], [\"Євроконус\", \"16х2,0\"]. "
            "Шукай кілька разів: спочатку широко, потім звужуй брендом/серією. "
            "Роби кілька пошуків в одному кроці для різних рядків."),
        "input_schema": {
            "type": "object",
            "properties": {
                "terms": {"type": "array", "items": {"type": "string"}},
                "category": {"type": "string", "enum": CATEGORIES,
                             "description": "обмежити файлом прайсу (необов'язково)"},
                "exclude": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
            "required": ["terms"],
        },
    },
    {
        "name": "get_kit",
        "description": "Позиції готового комплекту (тепла підлога tp_v1/tp_v2/tp_v3) під N контурів.",
        "input_schema": {
            "type": "object",
            "properties": {"kit_id": {"type": "string"}, "circuits": {"type": "integer"},
                           "wall": {"type": "string"}},
            "required": ["kit_id", "circuits"],
        },
    },
    {
        "name": "lookup_confirmed",
        "description": "Які товари менеджери вже підтвердили для таких самих формулювань. Дивись першим.",
        "input_schema": {
            "type": "object",
            "properties": {"originals": {"type": "array", "items": {"type": "string"}}},
            "required": ["originals"],
        },
    },
    {
        "name": "submit_matches",
        "description": "Здати результат по ВСІХ рядках пачки. Викликати один раз у кінці.",
        "input_schema": {
            "type": "object",
            "properties": {
                "matches": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "i": {"type": "integer"},
                            "id": {"type": ["integer", "null"],
                                   "description": "id з результатів search_catalog / get_kit"},
                            "status": {"type": "string", "enum": ["exact", "analog", "not_found"]},
                            "note": {"type": "string",
                                     "description": "чому саме цей товар / що перевірити / що шукав"},
                        },
                        "required": ["i", "status"],
                    },
                },
            },
            "required": ["matches"],
        },
    },
]

_TRANSCRIBE_TOOL = {
    "name": "submit_order",
    "description": "Здати розібране замовлення.",
    "input_schema": {
        "type": "object",
        "properties": {
            "brand_map": {"type": "object", "description": "система → бренд з підказки менеджера"},
            "kits": {"type": "array", "items": {"type": "object"}},
            "lines": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "i": {"type": "integer"},
                        "original": {"type": "string", "description": "як написано"},
                        "read": {"type": "string", "description": "повна зрозуміла назва"},
                        "qty": {"type": "string"},
                        "unit": {"type": "string"},
                        "system": {"type": "string"},
                        "type": {"type": "string", "description": "одне слово: труба/коліно/трійник/муфта/..."},
                        "dia": {"type": "array", "items": {"type": "integer"},
                                "description": "діаметри в порядку запису: трійник 110/50 → [110, 50]"},
                        "angle": {"type": ["integer", "null"]},
                        "thread": {"type": ["string", "null"], "description": "1/2, 3/4, 1 ..."},
                        "brand": {"type": ["string", "null"], "description": "бренд для цього рядка"},
                        "kit": {"type": ["string", "null"]},
                        "kit_role": {"type": ["string", "null"]},
                        "note": {"type": "string"},
                    },
                    "required": ["i", "original", "read", "qty"],
                },
            },
        },
        "required": ["lines"],
    },
}

_METHOD = """ТИ — старший менеджер Hotpoint (сантехніка, опалення). Працюєш так:

1. НІКОЛИ не вигадуй товар. Кожен id — лише з результатів інструментів.
2. Для кожного рядка: спершу lookup_confirmed; потім search_catalog широко (тип + діаметр),
   потім звужуй системою/брендом/серією. 0 результатів → послаб: прибери бренд, синоніми
   (коліно/кутник, муфта/з'єднувач, МРЗ = муфта різьбова зовнішня, МРВ = внутрішня,
   РВ/РЗ, американка = напівзгін), інше написання розміру ("ф ?20", "20х", "20x").
3. Перевір кожен вибір: система (PPR/PUSH/канал./металопласт), діаметри в правильному порядку,
   кут, різьба РВ/РЗ і дюйми, PN/серія, довжина труби, одиниця виміру.
4. Бренд: якщо менеджер вказав бренд для системи — тільки він; немає в прайсі → analog з note.
   Не вказав — бери бренд, який уже є в цьому замовленні для тієї ж системи, інакше найпоширеніший.
5. Товар «(п/з)» бери лише якщо іншого немає.
6. Комплект (kit) — викликай get_kit і бери позиції звідти.
7. Чого справді немає в прайсі — not_found, у note напиши, що шукав.
8. Роби кілька пошуків за один крок (паралельно для різних рядків). Економ кроки.
9. Наприкінці — один виклик submit_matches по ВСІХ рядках пачки.
"""


# ─────────────────────────── виклики моделі ───────────────────────────

def _system(extra: str) -> list[dict]:
    return [{"type": "text", "text": f"{_METHOD}\n\n{extra}\n\n{_knowledge()}",
             "cache_control": {"type": "ephemeral"}}]


def _track(model: str, usage) -> None:
    """Рахує вартість кожного виклику — щоб бачити реальну ціну замовлення в логах."""
    pin, pout = PRICES.get(model, (3.0, 15.0))
    u = usage
    usd = (getattr(u, "input_tokens", 0) * pin
           + (getattr(u, "cache_read_input_tokens", 0) or 0) * pin * 0.1
           + (getattr(u, "cache_creation_input_tokens", 0) or 0) * pin * 1.25
           + getattr(u, "output_tokens", 0) * pout) / 1_000_000
    with _COST_LOCK:
        _COST["usd"] += usd
        _COST["calls"] += 1


def _create(model: str | None = None, **kw):
    model = model or MODEL
    for attempt in range(3):
        try:
            resp = claude.messages.create(model=model, **kw)
            if getattr(resp, "usage", None):
                _track(model, resp.usage)
            return resp
        except anthropic.RateLimitError:
            time.sleep(10 * (attempt + 1))
        except anthropic.APIStatusError as e:
            if e.status_code in (500, 529) and attempt < 2:
                time.sleep(5)
                continue
            raise
    return claude.messages.create(model=model, **kw)


def transcribe_order(images_b64: list[str], text: str, caption: str) -> dict:
    """Фаза 1: модель сама читає фото/текст і розбирає замовлення цілком."""
    content: list[dict] = [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}}
        for b in images_b64
    ]
    content.append({"type": "text", "text": (
        f"Фото — сторінки ОДНОГО замовлення по порядку.\nПІДКАЗКА МЕНЕДЖЕРА: {caption or '—'}\n"
        + (f"ТЕКСТ ЗАМОВЛЕННЯ:\n{text}\n" if text else "")
        + "Прочитай усі рядки. Рядок без типу — продовження попереднього. Для кожного рядка визнач "
          "систему (ppr/push/sewage/underfloor/mp/other) з контексту ВСЬОГО замовлення, "
          "бренди з підказки, комплекти. Для кожного рядка заповни type, dia, angle, thread, brand. "
          "Нерозбірливе — найімовірніше прочитання + note. "
          "Здай через submit_order.")})
    resp = _create(
        max_tokens=16384, temperature=0,
        system=_system("ФАЗА 1: розбір замовлення."),
        tools=[_TRANSCRIBE_TOOL], tool_choice={"type": "tool", "name": "submit_order"},
        messages=[{"role": "user", "content": content}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            return block.input
    return {}


def _run_tool(name: str, args: dict) -> str:
    try:
        if name == "search_catalog":
            return tool_search_catalog(**args)
        if name == "get_kit":
            return tool_get_kit(**args)
        if name == "lookup_confirmed":
            return tool_lookup_confirmed(**args)
    except TypeError as e:
        return f"помилка аргументів: {e}"
    return f"невідомий інструмент {name}"


def _match_chunk(order: dict, idxs: list[int]) -> dict[int, dict]:
    """Фаза 2 для однієї пачки рядків: цикл пошуку до submit_matches."""
    lines = order.get("lines", [])
    overview = "\n".join(
        f"{l['i']}. {l.get('read', '')} | {l.get('qty', '')} {l.get('unit', '')} | {l.get('system', '')}"
        for l in lines)
    todo = "\n".join(
        f"{l['i']}. оригінал: {l.get('original', '')} | прочитано: {l.get('read', '')} | "
        f"{l.get('qty', '')} {l.get('unit', '')} | система: {l.get('system', '')}"
        + (f" | комплект {l.get('kit')}: {l.get('kit_role')}" if l.get("kit") else "")
        + (f" | {l['note']}" if l.get("note") else "")
        for l in lines if l["i"] in idxs)
    messages = [{"role": "user", "content": (
        f"ВСЕ ЗАМОВЛЕННЯ (для контексту):\n{overview}\n\n"
        f"БРЕНДИ З ПІДКАЗКИ: {json.dumps(order.get('brand_map') or {}, ensure_ascii=False)}\n"
        f"КОМПЛЕКТИ: {json.dumps(order.get('kits') or [], ensure_ascii=False)}\n\n"
        f"ПІДБЕРИ ЦІ РЯДКИ:\n{todo}")}]
    system = _system("ФАЗА 2: підбір по прайсу.")

    for _ in range(MAX_TURNS):
        resp = _create(max_tokens=8192, temperature=0, system=system, tools=TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        results, submitted = [], None
        for block in resp.content:
            if block.type != "tool_use":
                continue
            if block.name == "submit_matches":
                submitted = block.input
                continue
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": _run_tool(block.name, block.input)})
        if submitted is not None:
            return {m["i"]: m for m in submitted.get("matches", []) if isinstance(m.get("i"), int)}
        if not results:                                        # модель зупинилась без здачі
            messages.append({"role": "user", "content": "Здай результат через submit_matches."})
            continue
        messages.append({"role": "user", "content": results})
    log.warning("agent: пачка %s не здана за %s кроків", idxs, MAX_TURNS)
    return {}


# ─────────────────────── дешевий шлях: кандидати кодом ───────────────────────

_SYS_CAT = {"ppr": "plastic_ppr", "push": "push_systems", "sewage": "sewage",
            "underfloor": "underfloor_heating", "mp": "metal_plastic"}
_TYPE_RX = {
    "коліно": r"Колін|Кутник|Відвід", "трійник": r"Трійник", "муфта": r"Муфт",
    "труба": r"^Труба", "кран": r"Кран", "перехід": r"Перех|Редук|перехідн",
    "заглушка": r"Заглушк", "хомут": r"Хомут", "гільза": r"Гільз", "клапан": r"Клапан",
    "фільтр": r"Фільтр", "ревізія": r"Ревізі", "хрестовина": r"Хрестовин",
    "утеплювач": r"Утеплювач|Ізоляці", "ізоляція": r"Утеплювач|Ізоляці",
    "напівзгін": r"півзгін|Американк", "американка": r"півзгін|Американк",
    "ніпель": r"Ніпель", "шланг": r"Шланг|підводк", "євроконус": r"Євроконус",
}


def _brand_rx(brand: str | None) -> str | None:
    if not brand:
        return None
    from engine.search import BRAND_TOKENS
    toks = BRAND_TOKENS.get(brand.lower().strip())
    return re.escape(toks[0]) if toks else re.escape(brand)


def _search_ids(terms: list[str], category: str | None) -> list[int]:
    rx = [_rx(t) for t in terms if t]
    out = []
    for idx, it in enumerate(CATALOG):
        if category and it.get("category") != category:
            continue
        name = it.get("name", "")                  # без {пакування}: інакше "20" ловиться в {20/300}
        if not _BAD.search(name) and all(r.search(name) for r in rx):
            out.append(idx)
    # спершу звичайні товари, потім «(п/з)»; коротша назва = базовіший товар
    out.sort(key=lambda i: ("(п/з)" in CATALOG[i]["name"], len(CATALOG[i]["name"])))
    return out


def candidates(line: dict, brand_map: dict) -> list[int]:
    """Те, що я робив першим пошуком: тип + розміри + кут + різьба + бренд, потім послаблюю."""
    typ = str(line.get("type") or "").lower().strip()
    t_rx = _TYPE_RX.get(typ) or (re.escape(typ.capitalize()) if typ else None)
    system = line.get("system") or ""
    cat = _SYS_CAT.get(system)
    dia = [rf"(?<!\d){int(d)}(?!\d)" for d in (line.get("dia") or []) if str(d).isdigit()]
    ang = line.get("angle")
    if ang and system == "sewage" and int(ang) == 90:
        ang = 87
    a_rx = rf"(?<!\d){int(ang)}(?!\d)" if ang else None
    th = re.escape(str(line["thread"]).strip('"')) if line.get("thread") else None
    brand = _brand_rx(line.get("brand") or (brand_map or {}).get(system))

    base = [x for x in [t_rx] + dia if x]
    if not base:
        return []
    b = [brand] if brand else []
    ladder = [                                     # бренд тримаємо найдовше
        (base + [x for x in (a_rx, th) if x] + b, cat),
        (base + [x for x in (th,) if x] + b, cat),   # кут часто не пишуть (PUSH 90°)
        (base + b, cat),
        (base + [x for x in (a_rx, th) if x], cat),
        (base + [x for x in (th,) if x], cat),
        (base, cat),
        (base + [x for x in (a_rx, th) if x], None),
    ]
    for terms, c in ladder:
        ids = _search_ids(terms, c)
        if ids:
            return ids[:FAST_CANDIDATES]
    return []


_FAST_PROMPT = """Ти — менеджер Hotpoint. Для кожного рядка замовлення вибери товар ТІЛЬКИ зі списку кандидатів.
Перевір: система, діаметри в правильному порядку, кут, різьба РВ/РЗ, дюйми, серія/PN, одиниця виміру.
Бренд: якщо заданий у рядку чи BRAND_MAP — лише він; інакше той самий, що в інших рядках тієї ж системи.
status: exact — впевнений; analog — інший бренд/серія, note чому; unsure — серед кандидатів немає точного
або сумніваєшся (такі рядки перешукає старша модель). Не вгадуй: краще unsure, ніж помилка.
Здай через submit_matches."""


def _pick_fast(order: dict, lines: list[dict], cands: dict[int, list[int]]) -> dict[int, dict]:
    """Один виклик дешевої моделі на пачку: вибір з готових кандидатів."""
    blocks = []
    for l in lines:
        rows = "\n".join(f"  id={i} | {CATALOG[i].get('name_full') or CATALOG[i]['name']}"
                         for i in cands.get(l["i"], []))
        blocks.append(f"{l['i']}. {l.get('read', '')} | {l.get('qty', '')} {l.get('unit', '')} | "
                      f"система: {l.get('system', '')}\n{rows}")
    resp = _create(
        model=FAST_MODEL, max_tokens=8192, temperature=0,
        system=[{"type": "text", "text": _FAST_PROMPT, "cache_control": {"type": "ephemeral"}}],
        tools=[{**TOOLS[-1], "input_schema": {
            **TOOLS[-1]["input_schema"],
            "properties": {"matches": {**TOOLS[-1]["input_schema"]["properties"]["matches"], "items": {
                **TOOLS[-1]["input_schema"]["properties"]["matches"]["items"],
                "properties": {**TOOLS[-1]["input_schema"]["properties"]["matches"]["items"]["properties"],
                               "status": {"type": "string",
                                          "enum": ["exact", "analog", "unsure"]}}}}}}}],
        tool_choice={"type": "tool", "name": "submit_matches"},
        messages=[{"role": "user", "content":
                   f"BRAND_MAP: {json.dumps(order.get('brand_map') or {}, ensure_ascii=False)}\n\n"
                   + "\n\n".join(blocks)}],
    )
    for block in resp.content:
        if block.type == "tool_use":
            out = {}
            for m in block.input.get("matches", []):
                i = m.get("i")
                if isinstance(i, int) and m.get("id") in cands.get(i, []):   # тільки з кандидатів
                    out[i] = m
                elif isinstance(i, int):
                    out[i] = {**m, "status": "unsure"}
            return out
    return {}


# ─────────────────────────── у формат бота ───────────────────────────

def _to_result(line: dict, m: dict | None) -> dict:
    qty = f"{line.get('qty', '')} {line.get('unit', '')}".strip()
    base = {
        "original": line.get("original", ""), "normalized": line.get("read", ""),
        "qty": qty, "category": "", "candidates_debug": [], "_prefix": "AG",
        "_routed_cat": "", "_node_id": "", "_catalog_node": "", "_used_brand": "",
        "розділ": line.get("section", ""),
    }
    item = None
    if m and isinstance(m.get("id"), int) and 0 <= m["id"] < len(CATALOG):
        item = CATALOG[m["id"]]
    if not item or (m or {}).get("status") == "not_found":
        return {**base, "знайдено": False, "назва": "", "confidence": 0, "keyword_pct": 0,
                "джерело": "🤖 агент", "brand_warning": "",
                "reason": (m or {}).get("note", ""), "fail_reason": (m or {}).get("note", "не знайдено")}
    analog = m.get("status") == "analog"
    note = m.get("note", "")
    return {**base, "знайдено": True, "назва": item["name"],
            "назва_повна": item.get("name_full", item["name"]),
            "артикул": item.get("artikul", ""), "ціна": item.get("price", ""),
            "category": item.get("category", ""), "confidence": 90 if analog else 97,
            "keyword_pct": 100, "джерело": "⚠️ аналог" if analog else "🤖 агент",
            "brand_warning": f"⚠️ {note}" if analog else "", "reason": note, "fail_reason": ""}


def _confirmed(line: dict) -> dict | None:
    """Те, що менеджер уже підтвердив через «Навчання» — без жодного виклику моделі."""
    try:
        from clients.cache import cache_lookup
        hit = cache_lookup(line.get("original", ""), {})
    except Exception:
        return None
    if not hit or hit.get("status") != "confirmed":
        return None
    name = hit.get("catalog_name")
    for idx, it in enumerate(CATALOG):
        if it["name"] == name or it.get("name_full") == name:
            return {"i": line["i"], "id": idx, "status": "exact", "note": "підтверджено раніше"}
    return None


def match_order(order: dict, progress_cb=None) -> list[dict]:
    """Фаза 2: кеш → кандидати кодом → дешева модель → старша модель лише для сумнівних."""
    lines = order.get("lines", [])
    for n, l in enumerate(lines):
        l.setdefault("i", n)
    cost_before = _COST["usd"]
    matched: dict[int, dict] = {}

    for l in lines:                                                # 1) підтверджене — безкоштовно
        c = _confirmed(l)
        if c:
            matched[l["i"]] = c
    rest = [l for l in lines if l["i"] not in matched]

    cands = {l["i"]: candidates(l, order.get("brand_map") or {}) for l in rest}   # 2) кодом
    with_c = [l for l in rest if cands[l["i"]]]
    for s0 in range(0, len(with_c), 40):                          # 3) дешева модель
        try:
            matched.update({i: m for i, m in _pick_fast(order, with_c[s0:s0 + 40], cands).items()
                            if m.get("status") in ("exact", "analog")})
        except anthropic.APIError as e:
            log.warning("agent fast: %s", e)
    if progress_cb:
        progress_cb(len(matched), len(lines))

    hard = [l["i"] for l in lines if l["i"] not in matched]      # 4) старша модель з пошуком
    fast_done = len(lines) - len(hard)
    idxs = hard
    parts = [idxs[s:s + CHUNK] for s in range(0, len(idxs), CHUNK)]
    done = 0
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:      # пачки паралельно
        futures = {pool.submit(_match_chunk, order, part): part for part in parts}
        for fut in as_completed(futures):
            part = futures[fut]
            try:
                matched.update(fut.result())
            except anthropic.APIError as e:
                log.warning("agent: пачка %s: %s", part, e)
            done += len(part)
            if progress_cb:
                progress_cb(fast_done + done, len(lines))
    print(f"💰 agent: {len(lines)} рядків, кеш+швидко {fast_done}, пошуком {len(hard)}, "
          f"${_COST['usd'] - cost_before:.3f}", flush=True)
    return [_to_result(l, matched.get(l["i"])) for l in lines]


def positions_to_order(позиції: list[dict], brand_map: dict | None = None) -> dict:
    """Позиції з класичного OCR / PDF → формат замовлення агента (для PDF і тексту)."""
    return {
        "brand_map": brand_map or {},
        "kits": [],
        "lines": [{"i": i, "original": п.get("original", ""), "read": п.get("normalized", ""),
                   "qty": str(п.get("qty", "")), "unit": "", "system": п.get("category", ""),
                   "section": п.get("section", ""),
                   "note": "; ".join(п.get("notes", []))} for i, п in enumerate(позиції)],
    }
