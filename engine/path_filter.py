"""
engine/path_filter.py — Фільтр кандидатів за справжньою гілкою 1С.

ПРОБЛЕМА:
  Один бренд живе в кількох гілках дерева. Наприклад RAFTEC GOLD:
      ЗАПІРНА АРМАТУРА → Краны с американкой → RAFTEC → RAFTEC GOLD   (6)
      ЗАПІРНА АРМАТУРА → Краны с НГ          → RAFTEC → RAFTEC GOLD   (4)
      ЗАПІРНА АРМАТУРА → Краны шаровые → Вода → RAFTEC → RAFTEC GOLD  (8)
  Пошук повертає всі 18 вперемішку, і Claude/Voyage мусить вгадувати.

РІШЕННЯ:
  Після збору кандидатів групуємо їх за `path`, оцінюємо кожну гілку
  проти запиту і лишаємо тільки найкращу. 18 → 8.

  Фільтр НІКОЛИ не повертає порожній список: якщо жодна гілка
  не виграла — віддаємо вхід без змін.

ВИКОРИСТАННЯ:
    from engine.path_filter import filter_by_path
    кандидати = filter_by_path(f"{normalized} {original}", кандидати)
"""

from __future__ import annotations

import re
from typing import Any

# ─── Правила: (регекс запиту, регекс сегмента шляху, вага) ───────────────────
# Позитивна вага — гілка підходить. Негативна — гілка суперечить запиту.
#
# Порядок не важливий, спрацьовують усі відповідні.

_FITTING_Q = (r'колін|кут|відвід|отвод|трійник|тройник|муфт|перехід|переходник|'
              r'заглушк|хрестовин|крестовин|ревізі|ревизи|американк|згін|сгон|'
              r'ніпель|ниппель|штуцер|бочат|футорк|контргайк')

# (регекс запиту, регекс сегмента, вага, guard)
# guard — якщо цей патерн знайдено в сегменті, правило пропускається.
# Потрібен для штрафів: сегмент «Крапельна трубка + фітинг» містить
# і «труб», і «фітинг» — штрафувати його не можна.
RULES: list[tuple[str, str, float, str | None]] = [
    # ── ТРУБА vs ФІТИНГ — головний роздільник, що зараз втрачається ──────────
    (r'\bтруб',                     r'труб',                        4.0, None),
    (r'\bтруб',                     r'фитинг|фітинг',              -5.0, r'труб'),

    (_FITTING_Q,                    r'фитинг|фітинг',               4.0, None),
    (_FITTING_Q,                    r'^\*?труба$|^труба\b',        -5.0, r'фитинг|фітинг'),

    # ── КАНАЛІЗАЦІЯ: внутрішня / зовнішня / безшумна ─────────────────────────
    (r'внутр|вн\.|внут',            r'внутренн',                    3.0, None),
    (r'внутр|вн\.|внут',            r'наружн|зовнішн',             -4.0, None),
    (r'зовнішн|наружн|нар\.|руда|коричнев|помаранчев|оранжев',
                                    r'наружн',                      3.0, None),
    (r'зовнішн|наружн|нар\.',       r'внутренн',                   -4.0, None),
    (r'безшум|бесшум|тих|s-?line|skolan|raupiano|db20',
                                    r'безшум|бесшум',               4.0, None),
    (r'\bhtr?\b|ht safe|сір[аиі]|сер[аыои]',
                                    r'безшум|бесшум',              -2.5, None),

    # ── ЗАПІРНА АРМАТУРА: тип крана ──────────────────────────────────────────
    (r'кульов|шаров|шар\b',         r'шаров|кульов',                4.0, None),
    (r'американк|з\s*амер|с\s*амер', r'американк',                  4.0, None),
    (r'\bнг\b|з\s*нг|с\s*нг|напівгайк',
                                    r'\bнг\b',                      3.5, None),
    (r'поливал|поливн|для\s*полив',  r'полив',                      4.0, None),
    (r'трьохход|трехход|3-?х\s*ход', r'трехход|трьохход',           4.0, None),
    (r'дренаж',                     r'дренаж',                      3.5, None),
    (r'засувк|задвижк|батерфляй|butterfly',
                                    r'засувк|задвижк|батерфляй',    3.5, None),
    (r'\bгаз\b|газов',              r'\bгаз\b|газов',               3.5, None),
    (r'\bгаз\b|газов',              r'\bвода\b|водян',             -3.0, None),
    (r'\bвод[аиуо]\b|питн',         r'\bвода\b',                    2.0, None),

    # ── ФІЛЬТРИ ──────────────────────────────────────────────────────────────
    (r'самопромив|самоочис|промивн',
                                    r'самопромив|самоочис',         4.0, None),
    (r'груб(ої|ой)\s*очист',        r'груб',                        4.0, None),

    # ── МАТЕРІАЛ / СИСТЕМА ───────────────────────────────────────────────────
    (r'нержав|inox|нерж',           r'нержав|inox',                 3.0, None),
    (r'латун',                      r'латун',                       2.5, None),
    (r'нікел|никел',                r'никел|нікел',                 2.5, None),
    (r'\bхром',                     r'хром',                        2.5, None),
    (r'\bмідь|медн|copper',         r'мед[ьн]|copper',              3.0, None),
    (r'\bсталь|стальн|сталев',      r'сталь|стальн',                2.5, None),
    (r'\bм/?п\b|металопласт',       r'м/?п\b',                      3.0, None),
    (r'\bппр\b|ppr|пропілен|пайк',  r'ppr|ппр',                     2.5, None),
    (r'\bпуш\b|push|pex',           r'push|пуш',                    3.0, None),
    (r'(?<![a-zа-я])прес|(?<![a-z])press',
                                    r'прес',                        2.5, None),
    (r'\bпнд\b|пе\s*100|pe\s*100',  r'пнт|пнд|pe\s*100',            3.0, None),

    # ── ТЕПЛА ПІДЛОГА ────────────────────────────────────────────────────────
    (r'тепл(ої|ой|а|ый)\s*підлог|тепл(ый|ого)\s*пол',
                                    r'тепл(ый|ого)\s*пол|тепл',     2.5, None),
    (r'термоізоляц|термоизоляц|в\s*ізоляц',
                                    r'с\s*термоизоляц',             3.5, None),
]

_COMPILED = [(re.compile(q, re.I), re.compile(p, re.I), w,
              re.compile(g, re.I) if g else None) for q, p, w, g in RULES]

# Гілки «під замовлення» — беремо лише якщо запит прямо про це
_ORDER_ONLY = re.compile(r'під\s*замовл|под\s*заказ|заказн|уточнят', re.I)
_ORDER_ASK  = re.compile(r'під\s*замовл|под\s*заказ|заказ', re.I)

_TOKEN = re.compile(r'[a-zа-яёіїєґ]{3,}', re.I)

_index: dict[str, tuple] = {}
_ready = False


# ─── Індекс назва → шлях ─────────────────────────────────────────────────────

def ensure_path_index() -> None:
    """Будує індекс назва товару → шлях. Лінива, ідемпотентна."""
    global _ready
    if _ready:
        return
    from catalog.catalog import CATALOG
    for it in CATALOG:
        p = it.get('path')
        if p:
            _index[it['name']] = tuple(p)
    _ready = True
    print(f"🌳 Індекс шляхів: {len(_index)} товарів", flush=True)


def get_path(item: Any) -> tuple:
    """Шлях товару: з самого кандидата або з індексу за назвою."""
    if isinstance(item, dict):
        p = item.get('path')
        if p:
            return tuple(p)
        name = item.get('name', '')
    else:
        name = str(item)
    ensure_path_index()
    return _index.get(name, ())


# ─── Оцінка гілки ────────────────────────────────────────────────────────────

def score_branch(query: str, path: tuple) -> float:
    """Наскільки гілка дерева відповідає запиту. Може бути від'ємною."""
    if not path:
        return 0.0
    segs = ' | '.join(path[1:])      # корінь (категорія) не інформативний
    if not segs:
        return 0.0

    score = 0.0
    for q_re, p_re, w, g_re in _COMPILED:
        if not (q_re.search(query) and p_re.search(segs)):
            continue
        if g_re is not None and g_re.search(segs):
            continue        # сегмент містить і те, і те — не штрафуємо
        score += w

    # «Під замовлення» — тільки якщо явно попросили
    if _ORDER_ONLY.search(segs) and not _ORDER_ASK.search(query):
        score -= 2.5

    # Збіг слів запиту з назвами папок (переважно бренди)
    q_tok = set(t.lower() for t in _TOKEN.findall(query))
    s_tok = set(t.lower() for t in _TOKEN.findall(segs))
    score += 0.6 * len(q_tok & s_tok)

    return score


# ─── Головна функція ─────────────────────────────────────────────────────────

# Мінімальний бал, щоб вважати гілку впевнено визначеною.
# 2.0 = спрацювало хоча б одне справжнє правило, а не лише збіг слів.
MIN_SCORE = 2.0
# Гілки в межах цього допуску від найкращої теж проходять
# (напр. три різні гілки «Фитинг» з однаковим сенсом).
TOLERANCE = 0.75


def filter_by_path(query: str, candidates: list[dict],
                   min_score: float = MIN_SCORE,
                   tolerance: float = TOLERANCE) -> list[dict]:
    """
    Лишає кандидатів тільки з гілок, що відповідають запиту.

    query     — текст запиту (нормалізований + оригінал)
    min_score — мінімальний бал найкращої гілки, щоб фільтрувати взагалі
    tolerance — гілки в межах цього відступу від найкращої теж лишаються

    Повертає вхідний список без змін якщо:
      • кандидатів < 2
      • усі з однієї гілки
      • жодна гілка не набрала min_score (запит неоднозначний)
    """
    if not candidates or len(candidates) < 2:
        return candidates

    groups: dict[tuple, list[dict]] = {}
    for c in candidates:
        groups.setdefault(get_path(c), []).append(c)

    if len(groups) < 2:
        return candidates

    scores = {p: score_branch(query, p) for p in groups}
    best   = max(scores.values())

    if best < min_score:
        return candidates            # запит не вказує на гілку — не ріжемо

    winners = {p for p, s in scores.items() if s >= best - tolerance}
    out = [c for c in candidates if get_path(c) in winners]

    return out or candidates


def explain(query: str, candidates: list[dict]) -> str:
    """Діагностика: які гілки і з якими балами. Для налагодження."""
    groups: dict[tuple, int] = {}
    for c in candidates:
        p = get_path(c)
        groups[p] = groups.get(p, 0) + 1
    lines = []
    for p, n in sorted(groups.items(), key=lambda x: -score_branch(query, x[0])):
        lines.append(f"  {score_branch(query, p):+6.1f}  [{n:3}]  {' → '.join(p) or '—'}")
    return '\n'.join(lines)
