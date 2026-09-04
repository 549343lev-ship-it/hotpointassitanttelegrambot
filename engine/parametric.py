"""
engine/parametric.py — Параметричний (детерміністичний) пошук.

ІДЕЯ:
  Каталог Hotpoint має сталу граматику назв:
      Тип характеристики, розмір, матеріал, система, БРЕНД {упаковка}
  Векторний пошук (Voyage) добре бачить ТИП і БРЕНД, але майже сліпий
  до ЧИСЕЛ. 18% каталогу — це кластери товарів, ідентичних усім,
  крім розмірів (напр. 186 радіаторів HIDROS).

  Тому: спочатку парсимо назву в атрибути, робимо ЖОРСТКИЙ фільтр
  по атрибутах, і тільки залишок віддаємо у Voyage/Claude.

ВИКОРИСТАННЯ:
    from engine.parametric import parse_parametric, parametric_search, ensure_pindex

    ensure_pindex()                      # один раз при старті (лениво)
    hits = parametric_search(qa_text, node_id='pp.a.f', brand_tokens=['asg'])
"""

from __future__ import annotations

import re
from typing import Optional, Any

# ─── Лексикони ───────────────────────────────────────────────────────────────

# Нормалізація типу виробу. Ключ — підрядок у назві (lower), значення — канон.
# Порядок ВАЖЛИВИЙ: довші/специфічніші ключі йдуть першими.
TYPE_LEXICON: list[tuple[str, str]] = [
    ('трійник',        'трійник'),
    ('тройник',        'трійник'),
    ('хрестовин',      'хрестовина'),
    ('крестовин',      'хрестовина'),
    ('редукц',         'редукція'),
    ('перехідник',     'перехід'),
    ('переходник',     'перехід'),
    ('перехід',        'перехід'),
    ('американк',      'згін'),
    ('згін',           'згін'),
    ('сгон',           'згін'),
    ('коліно',         'коліно'),
    ('колено',         'коліно'),
    ('відвід',         'коліно'),
    ('отвод',          'коліно'),
    ('кут ',           'коліно'),
    ('заглушк',        'заглушка'),
    ('ревізі',         'ревізія'),
    ('ревизи',         'ревізія'),
    ('компенсатор',    'компенсатор'),
    ('муфт',           'муфта'),
    ('трубa',          'труба'),
    ('труба',          'труба'),
    ('патрубок',       'патрубок'),
    ('колектор',       'колектор'),
    ('коллектор',      'колектор'),
    ('гільза',         'гільза'),
    ('гильза',         'гільза'),
    ('євроконус',      'євроконус'),
    ('евроконус',      'євроконус'),
    ('ніпель',         'ніпель'),
    ('ниппель',        'ніпель'),
    ('штуцер',         'штуцер'),
    ('бочат',          'бочата'),
    ('контргайк',      'контргайка'),
    ('футорк',         'футорка'),
    ('хомут',          'хомут'),
    ('кліпс',          'кліпса'),
    ('клипс',          'кліпса'),
    ('радіатор',       'радіатор'),
    ('радиатор',       'радіатор'),
    ('рушникосуш',     'рушникосушка'),
    ('полотенцесуш',   'рушникосушка'),
    ('змішувач',       'змішувач'),
    ('смеситель',      'змішувач'),
    ('сифон',          'сифон'),
    ('трап',           'трап'),
    ('унітаз',         'унітаз'),
    ('унитаз',         'унітаз'),
    ('умивальник',     'умивальник'),
    ('мийк',           'мийка'),
    ('насос',          'насос'),
    ('котел',          'котел'),
    ('котл',           'котел'),
    ('бойлер',         'бойлер'),
    ('водонагрівач',   'бойлер'),
    ('лічильник',      'лічильник'),
    ('счетчик',        'лічильник'),
    ('фільтр',         'фільтр'),
    ('фильтр',         'фільтр'),
    ('колб',           'колба'),
    ('клапан',         'клапан'),
    ('вентил',         'вентиль'),
    ('засувк',         'засувка'),
    ('кран',           'кран'),
    ('утеплювач',      'утеплювач'),
    ('утеплитель',     'утеплювач'),
    ('ізоляц',         'утеплювач'),
    ('підведення',     'підведення'),
    ('шланг',          'шланг'),
    ('манометр',       'манометр'),
    ('термометр',      'термометр'),
    ('термоголовк',    'термоголовка'),
    ('термостат',      'термостат'),
    ('бак ',           'бак'),
    ('гідроакумулятор','бак'),
    ('гидроаккумулятор','бак'),
]

# Система/матеріал труби — жорсткий розділювач (PPR ≠ PUSH ≠ каналізація)
SYSTEM_LEXICON: list[tuple[str, str]] = [
    ('pp-rct',      'ppr'),
    ('pp-r',        'ppr'),
    ('ppr',         'ppr'),
    ('ппр',         'ppr'),
    ('канал',       'канал'),
    ('ht safe',     'канал'),
    ('htsafe',      'канал'),
    ('kg-',         'канал'),
    ('push',        'push'),
    ('пуш',         'push'),
    ('pe-x',        'push'),
    ('pex',         'push'),
    ('зшитого поліетилену', 'push'),
    ('металопласт', 'мп'),
    ('м/п',         'мп'),
    ('pe-rt',       'мп'),
    ('пнд',         'pe'),
    ('пе100',       'pe'),
    ('pe100',       'pe'),
    ('pe 100',      'pe'),
]

# Тип різьби — МРЗ (зовнішня) vs МРВ (внутрішня) НЕ взаємозамінні
THREAD_TYPE_LEXICON: list[tuple[str, str]] = [
    ('мрз',  'mrz'),   # муфта різьба зовнішня
    ('мрв',  'mrv'),   # муфта різьба внутрішня
    ('мрн',  'mrn'),   # накидна
    ('врн',  'mrn'),
    ('зр',   'mrz'),
    ('вр',   'mrv'),
]

# ─── Regex ───────────────────────────────────────────────────────────────────

_PACK   = re.compile(r'\s*\{[^}]+\}')
_PAREN  = re.compile(r'\s*\([^)]*\)')
_NUM    = r'\d+(?:[.,]\d+)?(?!\s*[/"”])'   # не частина дробу 3/4 і не дюйми 2"
# ф 25х20х20 / Ø32х3,0 / d 110
_DIM_F  = re.compile(rf'(?:ф|Ø|d)\s*({_NUM})'
                     rf'(?:\s*[хx×*]\s*({_NUM}))?'
                     rf'(?:\s*[хx×*]\s*({_NUM}))?', re.I)
# 500x1000 (радіатори) — без ф
_DIM_X  = re.compile(rf'(?<![\d/."])({_NUM})\s*[хx×*]\s*({_NUM})'
                     rf'(?:\s*[хx×*]\s*({_NUM}))?')
_DN     = re.compile(r'\bDN\s*(\d+)', re.I)
_PN     = re.compile(r'\bPN\s*(\d+)', re.I)
# 1/2"  3/4"  1 1/4"  2"
_THREAD = re.compile(r'(\d+\s+\d+/\d+|\d+/\d+|\d+)\s*["”]')
_ANGLE  = re.compile(r'(\d{2,3})(?:[.,]\d)?\s*[°º]')
_LEN    = re.compile(rf'L\s*[=:]?\s*({_NUM})\s*(мм|см|м)\b', re.I)
_TYPESZ = re.compile(r'тип\s*(\d{1,2})(?![\d])', re.I)   # радіатори: тип 22, тип 11

# Тип з'єднання ВВ / ВЗ / ЗВ / НВ. \b НЕ працює з кирилицею — тільки lookaround.
_NOTLET = r'(?<![а-яіїєґА-ЯІЇЄҐa-zA-Z])'
_NOTLET_R = r'(?![а-яіїєґА-ЯІЇЄҐa-zA-Z])'
_CONN_RE: list[tuple[str, str]] = [
    (rf'{_NOTLET}(?:вв|вн\.вн|в/в){_NOTLET_R}',                    'vv'),   # внутр-внутр
    (rf'{_NOTLET}(?:вз|зв|нв|вн|в/з|з/в){_NOTLET_R}',               'vz'),   # внутр-зовн
]
_CONN_COMPILED = [(re.compile(p, re.I), v) for p, v in _CONN_RE]

# Різьба МРЗ/МРВ — теж тільки через lookaround, інакше «резеРВуар» → mrv
_THREAD_TYPE_RE: list[tuple[str, str]] = [
    (rf'{_NOTLET}(?:мрз|mrz|зр|рз|нр){_NOTLET_R}',  'mrz'),   # зовнішня різьба
    (rf'{_NOTLET}(?:мрв|mrv|вр|рв){_NOTLET_R}',     'mrv'),   # внутрішня різьба
    (rf'{_NOTLET}(?:мрн|mrn|врн|нг){_NOTLET_R}',    'mrn'),   # накидна гайка
]
_THREAD_TYPE_COMPILED = [(re.compile(p, re.I), v) for p, v in _THREAD_TYPE_RE]
# різьба БЕЗ лапок — тільки для query_mode: "25х3/4", "перехід 1/2 на 3/4"
_THREAD_BARE = re.compile(r'(?<![\d.,])(\d+\s+\d+/\d+|\d/\d)(?![\d.,])')
_ANGLE_SET   = {15, 30, 45, 67, 87, 88, 90}

# Кириличні написання систем — менеджери пишуть "ппр", "пуш", "канашка"
SYSTEM_ALIASES_UA: list[tuple[str, str]] = [
    ('ппр',      'ppr'),
    ('пропілен', 'ppr'),
    ('пайк',     'ppr'),
    ('пуш',      'push'),
    ('канаш',    'канал'),
    ('каналі',   'канал'),
    ('канализ',  'канал'),
    ('фанов',    'канал'),
    ('металопл', 'мп'),
    ('м/п',      'мп'),
    ('пнд',      'pe'),
]


def _f(s: Optional[str]) -> Optional[float]:
    return float(s.replace(',', '.')) if s else None


def _norm_num(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    return int(v) if float(v).is_integer() else round(float(v), 2)


def _find_re(text: str, compiled: list[tuple]) -> Optional[str]:
    """Перший регекс зі списку, що знайшовся. Порядок = пріоритет."""
    for rx, val in compiled:
        if rx.search(text):
            return val
    return None


def _find_lex(low: str, lex: list[tuple[str, str]]) -> Optional[str]:
    for key, val in lex:
        if key in low:
            return val
    return None


def parse_parametric(raw: str, query_mode: bool = False) -> dict[str, Any]:
    """
    Розбирає назву на структуровані атрибути.

    query_mode=False → назва з каталогу ("Труба PPR ф 20х2,8 мм, PN20, ASG")
    query_mode=True  → рядок менеджера ("труба ппр 20 асг") — м'якші правила:
                       голі числа = діаметри, дроби без лапок = різьба.
    """
    name = _PACK.sub('', str(raw or '')).strip()
    core = _PAREN.sub('', name)
    low  = core.lower()

    # кут вирізаємо ДО парсингу розмірів: "ф 32х32х87,5°" → dims=[32,32], angle=87
    ang = _ANGLE.search(core)
    core_nd = _ANGLE.sub(' ', core)
    core_nd = re.sub(r'[хx×]\s*$', '', core_nd.strip())

    # ── бренд: останнє поле після коми без цифр ─────────────────────────────
    brand = None
    fields = [f.strip(' .') for f in core.split(',') if f.strip(' .')]
    if len(fields) >= 2:
        cand = fields[-1]
        if cand and not re.search(r'\d', cand) and 1 < len(cand) <= 28:
            brand = cand.lower()

    # ── розміри: порядок ЗБЕРІГАЄТЬСЯ (25×20×20 ≠ 20×25×20) ─────────────────
    dims: list[float] = []
    m = _DIM_F.search(core_nd)
    if m:
        dims = [_norm_num(_f(g)) for g in m.groups() if g]
    else:
        m2 = _DIM_X.search(core_nd)
        if m2:
            dims = [_norm_num(_f(g)) for g in m2.groups() if g]

    dn  = _DN.search(core)
    pn  = _PN.search(core)
    ln  = _LEN.search(core)
    tsz = _TYPESZ.search(core)

    length_mm = None
    if ln:
        mult = {'мм': 1, 'см': 10, 'м': 1000}[ln.group(2).lower()]
        length_mm = int(round(_f(ln.group(1)) * mult))

    threads = [t.strip() for t in _THREAD.findall(core)]

    angle = int(ang.group(1)) if ang else None
    if angle is not None and not (10 <= angle <= 180):
        angle = None

    ptype  = _find_lex(low, TYPE_LEXICON)
    system = _find_lex(low, SYSTEM_LEXICON)

    # ── QUERY MODE: менеджер пише без "ф", без лапок, кирилицею ─────────────
    if query_mode:
        if system is None:
            system = _find_lex(low, SYSTEM_ALIASES_UA)
        # різьба без лапок: "25х3/4" → 3/4
        if not threads:
            threads = [t.strip() for t in _THREAD_BARE.findall(core_nd)]
        # голі числа як діаметри: "труба ппр 20" → dims=[20]
        if not dims:
            used = {str(angle)} if angle else set()
            if dn:  used.add(dn.group(1))
            if pn:  used.add(pn.group(1))
            bare = [int(x) for x in re.findall(r'(?<![\d/.,"”])(\d{2,3})(?![\d/.,"”])', core_nd)
                    if x not in used and 10 <= int(x) <= 630]
            # у коліні/трійнику останнє число з набору кутів — це кут, не діаметр
            if (angle is None and len(bare) >= 2
                    and ptype in ('коліно', 'трійник', 'хрестовина')
                    and bare[-1] in _ANGLE_SET):
                angle = bare.pop()
            dims = [float(b) for b in bare[:3]]
            dims = [_norm_num(d) for d in dims]

    return {
        'name':        name,
        'type':        ptype,
        'brand':       brand,
        'system':      system,
        'thread_type': _find_re(core, _THREAD_TYPE_COMPILED),
        'conn':        _find_re(core, _CONN_COMPILED),
        'dims':        dims,
        'dn':          int(dn.group(1)) if dn else None,
        'pn':          int(pn.group(1)) if pn else None,
        'angle':       angle,
        'threads':     threads,
        'length_mm':   length_mm,
        'rad_type':    int(tsz.group(1)) if tsz else None,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  ІНДЕКС КАТАЛОГУ
# ═══════════════════════════════════════════════════════════════════════════

_pindex_built = False


def ensure_pindex() -> None:
    """Додає кожному товару каталогу поле `_pattrs`. Лінива, ідемпотентна."""
    global _pindex_built
    if _pindex_built:
        return
    from catalog import catalog as _cat
    print("🔧 Будую параметричний індекс...", flush=True)
    for item in _cat.CATALOG:
        item['_pattrs'] = parse_parametric(item.get('name', ''))
    _pindex_built = True
    print(f"✅ Параметричний індекс: {len(_cat.CATALOG)} товарів", flush=True)


def pindex_stats() -> dict[str, Any]:
    """Статистика покриття парсера — для /pstats і моніторингу якості."""
    from catalog import catalog as _cat
    ensure_pindex()
    total = len(_cat.CATALOG) or 1
    keys = ('type', 'brand', 'system', 'dims', 'dn', 'threads', 'angle', 'pn', 'length_mm')
    out = {'total': total}
    for k in keys:
        n = sum(1 for i in _cat.CATALOG if i.get('_pattrs', {}).get(k) not in (None, [], ''))
        out[k] = round(n / total * 100, 1)
    return out


# ═══════════════════════════════════════════════════════════════════════════
#  ЗІСТАВЛЕННЯ
# ═══════════════════════════════════════════════════════════════════════════

def _dims_match(q: list, c: list) -> bool:
    """Розміри збігаються за префіксом. Порядок КРИТИЧНИЙ (25×20×20 ≠ 20×25×20)."""
    if not q or not c:
        return True                      # немає даних — не блокуємо
    n = min(len(q), len(c))
    return q[:n] == c[:n]


def _threads_match(q: list, c: list) -> bool:
    if not q or not c:
        return True
    return q == c or set(q) == set(c)


def score_pair(q: dict, c: dict) -> float:
    """
    Скоринг «запит vs товар».
    Повертає -1.0 якщо товар НЕСУМІСНИЙ (жорсткий фільтр).
    """
    # ── ЖОРСТКІ ФІЛЬТРИ ─────────────────────────────────────────────────────
    if q['type'] and c['type'] and q['type'] != c['type']:
        return -1.0
    if q['system'] and c['system'] and q['system'] != c['system']:
        return -1.0
    if not _dims_match(q['dims'], c['dims']):
        return -1.0
    if q['dn'] and c['dn'] and q['dn'] != c['dn']:
        return -1.0
    if q['angle'] and c['angle'] and q['angle'] != c['angle']:
        return -1.0
    if q['thread_type'] and c['thread_type'] and q['thread_type'] != c['thread_type']:
        return -1.0
    if q['rad_type'] and c['rad_type'] and q['rad_type'] != c['rad_type']:
        return -1.0
    if q['length_mm'] and c['length_mm'] and q['length_mm'] != c['length_mm']:
        return -1.0
    if not _threads_match(q['threads'], c['threads']):
        return -1.0

    # ── М'ЯКІ БАЛИ ──────────────────────────────────────────────────────────
    s = 0.0
    if q['type'] and q['type'] == c['type']:
        s += 2.0
    if q['dims'] and c['dims']:
        s += 3.0 if q['dims'] == c['dims'] else 1.5
    if q['brand'] and c['brand']:
        s += 3.0 if q['brand'] == c['brand'] else -2.0
    if q['dn'] and q['dn'] == c['dn']:
        s += 2.0
    if q['threads'] and q['threads'] == c['threads']:
        s += 2.0
    if q['angle'] and q['angle'] == c['angle']:
        s += 1.5
    if q['length_mm'] and q['length_mm'] == c['length_mm']:
        s += 1.5
    if q['pn'] and q['pn'] == c['pn']:
        s += 1.0
    if q['system'] and q['system'] == c['system']:
        s += 1.0
    if q['rad_type'] and q['rad_type'] == c['rad_type']:
        s += 2.0
    return s


def query_strength(q: dict) -> int:
    """Скільки дискримінуючих атрибутів має запит. <2 → параметриці не вірити."""
    n = 0
    if q['type']:      n += 1
    if q['dims']:      n += 1
    if q['dn']:        n += 1
    if q['threads']:   n += 1
    if q['angle']:     n += 1
    if q['length_mm']: n += 1
    if q['rad_type']:  n += 1
    return n


def parametric_search(text: str,
                      node_id: Optional[str] = None,
                      category: Optional[str] = None,
                      brand_tokens: Optional[list[str]] = None,
                      top_n: int = 12,
                      qa: Optional[dict] = None) -> list[dict]:
    """
    Жорсткий параметричний пошук по каталогу.

    text         — нормалізований рядок запиту
    node_id      — вузол з router (напр. 'pp.a.f'); шукаємо в ньому і нижче
    category     — код категорії (fallback якщо node_id порожній)
    brand_tokens — жорстке обмеження виробника
    qa           — вже готові атрибути (щоб не парсити двічі)

    Повертає товари каталогу з полями `_pscore` і `_ptier`.
    Порожній список = параметрика не спрацювала → передати далі у Voyage.
    """
    from catalog import catalog as _cat
    ensure_pindex()

    q = qa or parse_parametric(text, query_mode=True)
    if query_strength(q) < 2:
        return []                        # надто мало ознак — не наш випадок

    if brand_tokens:
        bt_low = [str(b).lower() for b in brand_tokens]
    else:
        bt_low = None

    pool = _cat.CATALOG
    if node_id:
        sub = [i for i in pool if str(i.get('_node_id', '')).startswith(node_id)]
        if sub:
            pool = sub
    elif category:
        sub = [i for i in pool if i.get('category') == category]
        if sub:
            pool = sub

    hits: list[tuple[float, dict]] = []
    for item in pool:
        c = item.get('_pattrs')
        if not c:
            continue
        if bt_low:
            nl = item.get('name', '').lower()
            if not any(b in nl for b in bt_low):
                continue
        s = score_pair(q, c)
        if s < 0:
            continue
        hits.append((s, item))

    if not hits:
        return []

    hits.sort(key=lambda x: -x[0])
    best = hits[0][0]

    out: list[dict] = []
    for s, item in hits[:top_n]:
        r = dict(item)
        r['_pscore'] = round(s, 2)
        # _match_pct — для сумісності з існуючим кодом find_items/pick_batch
        r['_match_pct'] = min(99, int(60 + s * 4))
        r['score'] = round(min(0.99, 0.60 + s * 0.04), 3)
        # tier 1 = єдиний найкращий, з відривом, і всі розміри збіглись точно
        r['_ptier'] = 1 if (s == best
                            and sum(1 for x, _ in hits if x == best) == 1
                            and q['dims'] and q['dims'] == item['_pattrs']['dims']) else 2
        out.append(r)
    return out
