"""
synonyms.py — Довідник "схожих товарів" (універсальна назва → варіанти по брендах).

КЛЮЧОВА ІДЕЯ:
  Ключ будується з catalog_name (що підібрали), а НЕ з normalized (як спитали).
  Тому всі формулювання менеджера сходяться в один рядок:

    "ізоляція синя ф22"        ┐
    "утеплювач 22/6 синій 2м"  ├─→ "утеплювач ламін для труб ф22х6 синій"
    "утеплювач для труб ф22"   ┘        └─ {plm: {...}, теплоізол: {...}}

  Фрази менеджера зберігаються як aliases — основа для майбутнього
  етапу "normalized → універсальна назва" перед пошуком.

ФАЙЛ: DATA_DIR/synonyms.json
  {
    "утеплювач ламін для труб ф22х6 синій": {
      "variants": {
        "plm": {"catalog_name": "Утеплювач ламін. для труб ф 22х6 мм, синій, PLM",
                "code": "", "hits": 6, "last_seen": "2026-09-08"}
      },
      "aliases":  ["ізоляція синя ф22", "утеплювач 22/6 синій 2м"],
      "category": "insulation",
      "attrs":    {"dia": [22], "angle": null, "color": "синій"}
    }
  }

ІНТЕГРАЦІЯ (майбутній Крок 2):
  ukey = match_universal(normalized)   # fuzzy по aliases + жорсткий фільтр атрибутів
  item = synonyms_lookup(ukey, brand)
"""

import os
import re
import json
import time

DATA_DIR      = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
SYNONYMS_FILE = os.path.join(DATA_DIR, "synonyms.json")

MAX_ALIASES = 20   # скільки формулювань зберігати на один ключ


# ─── Бренди ──────────────────────────────────────────────────────────────────
# Порядок важливий: довші назви перші, щоб не з'їдало частинами.

_BRANDS = [
    ('ekoplastik', [r'ekoplastik']),
    ('raftec',     [r'raftec']),
    ('asg',        [r'(?<![a-z])asg(?![a-z])']),
    ('ostendorf',  [r'ostendorf']),
    ('fv plast',   [r'fv\s*plast']),
    ('plm',        [r'(?<![a-z])plm(?![a-z])']),
    ('eco',        [r'(?<![a-z])eco(?![a-z])']),
    ('rehau',      [r'rehau']),
    ('kan',        [r'kan-?therm', r'(?<![a-z])kan(?![a-z])']),
    ('fado',       [r'fado']),
    ('valrom',     [r'valrom']),
    ('wavin',      [r'wavin']),
    ('hidros',     [r'hidros']),
    ('idmar',      [r'idmar']),
    ('mirado',     [r'mirado']),
    ('purmo',      [r'purmo']),
    ('tatra',      [r'tatra(-line)?']),
    ('termojet',   [r'termojet']),
    ('wilo',       [r'wilo']),
    ('grundfos',   [r'grundfos']),
    ('biasi',      [r'biasi']),
    ('vaillant',   [r'vaillant']),
    ('giacomini',  [r'giacomini']),
    ('lexline',    [r'lexline']),
    ('pattaroni',  [r'pattaroni']),
    ('solomon',    [r'solomon']),
    ('unipak',     [r'unipak']),
    ('grohe',      [r'grohe']),
    ('geberit',    [r'geberit']),
    ('alcaplast',  [r'alcaplast']),
    ('herz',       [r'(?<![a-z])herz(?![a-z])']),
    ('imprese',    [r'imprese']),
    ('navin',      [r'navin']),
    ('teploizol',  [r'теплоізол']),
    ('general fittings', [r'general\s*fittings?']),
]

# Маркери товарних ліній — прибираємо, бо вони брендозалежні
_SERIES = [
    r'pp-?rct', r'ht\s*safe', r'(?<![a-z])htr(?![a-z])', r'kg\s*2000',
    r'(?<![a-z])(black|gold|steel|brass|silver|white)(\s+block)?(?![a-z])',
    r'(?<![a-z])profi(?![a-z])', r'rautitan', r'raubasic', r'aquapex',
    r'heat-?pex', r'lizoflex(\s+stabil)?(\s+red)?', r'k-?flex',
    r'unigarn', r'glidex', r'sanitary\s+silicone', r'extra(?![a-z])',
    r'compress', r'\(п/з\)', r'\(упаковка\)', r'\(з\s+кабелем\)',
    r'cw617n', r'sdr\d+',
]

_BRAND_RE  = re.compile('|'.join(p for _, pats in _BRANDS for p in pats), re.IGNORECASE)
_SERIES_RE = re.compile('|'.join(_SERIES), re.IGNORECASE)

# Абревіатури → канонічна форма
_ABBREV = [
    (r'внутрішн\w*',                      'вн'),
    (r'внутр\.?(?![а-я])',                'вн'),
    (r'внут\.?(?![а-я])',                 'вн'),
    (r'вн\.?(?![а-я])',                   'вн'),
    (r'зовнішн\w*',                       'зовн'),
    (r'каналізаційн\w*',                  'канал'),
    (r'каналіз\.?(?![а-я])',              'канал'),
    (r'канал\.?(?![а-я])',                'канал'),
    (r'ламінов\w*',                       'ламін'),
    (r'ламін\.?(?![а-я])',                'ламін'),
    (r'ексцентричн\w*',                   'ексц'),
    (r'ексентричн\w*',                    'ексц'),
    (r'редукційн\w*',                     'редукц'),
    (r'перехідн\w*',                      'перехід'),
    (r'ізоляц\w*',                        'утеплювач'),
    (r'ізол\.?(?![а-я])',                 'утеплювач'),
    (r'очистки',                          'очистк'),
    (r'кульов\w*',                        'кульов'),
    (r'підключенн\w*',                    'підключ'),
    (r'контурн\w*|контр\.?(?![а-я])',     'контр'),
    (r'витратомір\w*',                    'витратомір'),
    (r'\bмм\b|\bм\.\b',                   ''),
]

# Латинські двійники кирилиці у фітингових абревіатурах
_LOOKALIKE = [
    (r'\bmp3\b|\bмр3\b|\bmpз\b',  'мрз'),
    (r'\bmpb\b|\bмрb\b|\bmpв\b',  'мрв'),
    (r'\bpb\b(?=\s|$)',           'рв'),
    (r'\bp[\.\s]?в\b',            'рв'),
    (r'\bp[\.\s]?з\b',            'рз'),
]

_COLORS = ['синій', 'синя', 'червоний', 'червона', 'сірий', 'сіра', 'сіре',
           'білий', 'біла', 'біле', 'чорний', 'хром', 'нікель', 'оц']

_SYNONYMS: dict = {}


# ─── Канонізація ─────────────────────────────────────────────────────────────

def canonical(name: str) -> str:
    """
    catalog_name → канонічний brand-агностичний ключ.

    "Утеплювач ламін. для труб ф 22х6 мм, синій, PLM" → "утеплювач ламін для труб ф22х6 синій"
    "Коліно вн. канал. ф110 х 30°, сіре, HT Safe, OSTENDORF" → "коліно вн канал ф110х30 сіре"
    "Коліно внут. канал. ф110 х 30°, сіре, HTR, ASG"        → те саме
    """
    s = (name or '').lower()

    s = _BRAND_RE.sub(' ', s)
    s = _SERIES_RE.sub(' ', s)

    for pat, repl in _LOOKALIKE:
        s = re.sub(pat, repl, s)
    for pat, repl in _ABBREV:
        s = re.sub(pat, repl, s)

    s = s.replace('x', 'х').replace('×', 'х')          # латинська x → кирилична
    s = re.sub(r'(\d),(\d)', r'\1.\2', s)              # 1,8 → 1.8
    s = re.sub(r'87\.5(?=\s*°|\s|$)', '87', s)         # 87,5° = 87°
    s = re.sub(r'\bф\s*', 'ф', s)                      # "ф 22" → "ф22"
    s = re.sub(r'\bl\s*=\s*', 'l=', s)                 # "L = 0.5" → "l=0.5"
    s = re.sub(r'\bdn\s*', 'dn', s)
    s = re.sub(r'\s*х\s*', 'х', s)                     # "110 х 30" → "110х30"
    s = re.sub(r'(?<!\d)\.(?!\d)', ' ', s)             # крапка, крім десяткової
    s = re.sub(r'[°"\'`,;:()\[\]]+', ' ', s)
    s = re.sub(r'\s*/\s*', '/', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _detect_brand(catalog_name: str) -> str:
    """Визначає бренд з назви каталогу."""
    low = (catalog_name or '').lower()
    for brand_key, pats in _BRANDS:
        for p in pats:
            if re.search(p, low, re.IGNORECASE):
                return brand_key
    return '_other'


def _parse_attrs(name: str) -> dict:
    """Легкий парсер атрибутів для майбутнього жорсткого фільтра (Крок 2)."""
    low = (name or '').lower()
    dia = [int(d) for d in re.findall(r'ф\s*(\d{2,3})', low)]
    if not dia:
        dia = [int(d) for d in re.findall(r'dn\s*(\d{2,3})', low)]
    m_ang = re.search(r'(\d{2,3})(?:[.,]\d)?\s*°', low)
    angle = int(m_ang.group(1)) if m_ang else None
    if angle == 87 or angle == 88:
        angle = 87
    color = next((c for c in _COLORS if re.search(r'(?<![а-я])' + c + r'(?![а-я])', low)), None)
    return {'dia': dia, 'angle': angle, 'color': color}


# ─── Завантаження / збереження ───────────────────────────────────────────────

def _load():
    global _SYNONYMS
    if not os.path.exists(SYNONYMS_FILE):
        _SYNONYMS = {}
        return
    try:
        with open(SYNONYMS_FILE, encoding='utf-8') as f:
            raw = json.load(f)
        # Міграція старого формату {ukey: {brand: {...}}} → новий
        migrated = {}
        for k, v in raw.items():
            if isinstance(v, dict) and 'variants' in v:
                migrated[k] = v
            elif isinstance(v, dict):
                migrated[k] = {'variants': v, 'aliases': [], 'category': '', 'attrs': {}}
        _SYNONYMS = migrated
        print(f"📖 Synonyms: {len(_SYNONYMS)} універсальних назв", flush=True)
    except Exception as e:
        print(f"⚠️ synonyms load: {e}", flush=True)
        _SYNONYMS = {}


def _save():
    try:
        with open(SYNONYMS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_SYNONYMS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ synonyms save: {e}", flush=True)


# ─── Публічний API ───────────────────────────────────────────────────────────

def synonyms_add(normalized: str, catalog_name: str,
                 code: str = '', category: str = '', autosave: bool = True) -> str:
    """
    Додає підбір у довідник. Ключ — з catalog_name, фраза менеджера йде в aliases.
    Повертає universal_key.
    """
    if not catalog_name or not catalog_name.strip():
        return ''

    ukey  = canonical(catalog_name)
    if not ukey:
        return ''
    brand = _detect_brand(catalog_name)
    today = time.strftime('%Y-%m-%d')

    rec = _SYNONYMS.setdefault(ukey, {
        'variants': {}, 'aliases': [], 'category': category,
        'attrs': _parse_attrs(catalog_name),
    })
    if category and not rec.get('category'):
        rec['category'] = category

    v = rec['variants'].get(brand)
    if v and v.get('catalog_name') == catalog_name:
        v['hits']      = v.get('hits', 1) + 1
        v['last_seen'] = today
        if code and not v.get('code'):
            v['code'] = str(code)
    else:
        rec['variants'][brand] = {
            'catalog_name': catalog_name,
            'code':         str(code) if code else '',
            'hits':         1,
            'last_seen':    today,
        }

    alias = re.sub(r'\s+', ' ', (normalized or '').lower().strip())
    if alias and alias != ukey and alias not in rec['aliases']:
        rec['aliases'].append(alias)
        if len(rec['aliases']) > MAX_ALIASES:
            rec['aliases'] = rec['aliases'][-MAX_ALIASES:]

    if autosave:
        _save()
    return ukey


def synonyms_lookup(universal_key: str, brand_key: str = '') -> dict | None:
    """Шукає товар за універсальною назвою і брендом."""
    rec = _SYNONYMS.get(universal_key)
    if not rec or not rec.get('variants'):
        return None
    variants = rec['variants']

    if brand_key and brand_key in variants:
        v = variants[brand_key]
        return {'catalog_name': v['catalog_name'], 'code': v['code'], 'brand': brand_key}

    best = max(variants.items(), key=lambda x: x[1].get('hits', 0))
    return {'catalog_name': best[1]['catalog_name'], 'code': best[1]['code'], 'brand': best[0]}


def get_universal_index() -> dict:
    """Повертає весь довідник — для Кроку 2 (матчинг normalized → universal)."""
    return _SYNONYMS


def get_synonyms_stats() -> dict:
    total_keys    = len(_SYNONYMS)
    total_entries = sum(len(r.get('variants', {})) for r in _SYNONYMS.values())
    multi_brand   = sum(1 for r in _SYNONYMS.values() if len(r.get('variants', {})) > 1)
    total_aliases = sum(len(r.get('aliases', [])) for r in _SYNONYMS.values())
    return {
        'universal_keys': total_keys,
        'total_entries':  total_entries,
        'multi_brand':    multi_brand,
        'aliases':        total_aliases,
    }


# ─── Пріоритет брендів для експорту ──────────────────────────────────────────

def _brand_order(category: str) -> list:
    """Порядок брендів для категорії з DEFAULT_BRAND_PRIORITY."""
    try:
        from engine.search import DEFAULT_BRAND_PRIORITY
    except Exception:
        try:
            from search import DEFAULT_BRAND_PRIORITY
        except Exception:
            return []
    return [tok[0].lower() for tok in DEFAULT_BRAND_PRIORITY.get(category, [])]


def _sorted_variants(rec: dict) -> list:
    """Варіанти у порядку пріоритету бренду, потім за hits."""
    order = _brand_order(rec.get('category', ''))
    def key(item):
        brand, info = item
        rank = order.index(brand) if brand in order else len(order)
        return (rank, -info.get('hits', 0))
    return sorted(rec.get('variants', {}).items(), key=key)


# ─── Google Sheets Export ────────────────────────────────────────────────────

def export_to_sheets(spreadsheet_id: str, credentials_path: str = None) -> int:
    """Експорт: A=універсальна назва | B=1 пріоритет | C=код | D=2 пріоритет | ..."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("⚠️ synonyms export: pip install gspread google-auth", flush=True)
        return 0

    scopes     = ['https://www.googleapis.com/auth/spreadsheets']
    creds_path = credentials_path or os.environ.get('GOOGLE_CREDENTIALS_PATH', '')
    if not creds_path or not os.path.exists(creds_path):
        creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON', '')
        if not creds_json:
            print("⚠️ synonyms export: немає GOOGLE_CREDENTIALS_JSON", flush=True)
            return 0
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        tmp.write(creds_json)
        tmp.close()
        creds_path = tmp.name

    creds  = Credentials.from_service_account_file(creds_path, scopes=scopes)
    client = gspread.authorize(creds)
    sheet  = client.open_by_key(spreadsheet_id).sheet1

    MAX_BRANDS = 5
    header = ['універсальна назва']
    for i in range(1, MAX_BRANDS + 1):
        header += [f'{i} пріоритет', 'код']

    rows = [header]
    for ukey, rec in sorted(_SYNONYMS.items()):
        row = [ukey]
        for _brand, info in _sorted_variants(rec)[:MAX_BRANDS]:
            row += [info.get('catalog_name', ''), info.get('code', '')]
        while len(row) < 1 + MAX_BRANDS * 2:
            row += ['', '']
        rows.append(row)

    sheet.clear()
    sheet.update(values=rows, range_name='A1')
    print(f"✅ Synonyms → Sheets: {len(rows)-1} рядків", flush=True)
    return len(rows) - 1


# ─── Перебудова з кешу ───────────────────────────────────────────────────────

def rebuild_from_cache(cache: dict, client_caches: dict = None) -> dict:
    """
    Повністю перебудовує довідник з кешу бота (+ опційно клієнтських кешів).
    Повертає статистику.
    """
    global _SYNONYMS
    _SYNONYMS = {}

    seen = 0
    for key, entry in cache.items():
        if entry.get('status') == 'banned':
            continue
        if entry.get('confidence', 0) < 90:
            continue
        catalog_name = entry.get('catalog_name', '')
        normalized   = entry.get('normalized', '') or key.split('::')[0]
        if not catalog_name:
            continue
        synonyms_add(normalized, catalog_name,
                     category=entry.get('category', ''), autosave=False)
        seen += 1

    for slug, ccache in (client_caches or {}).items():
        for key, entry in ccache.items():
            if entry.get('status') == 'banned':
                continue
            catalog_name = entry.get('catalog_name', '')
            if not catalog_name:
                continue
            synonyms_add(key.split('::')[0], catalog_name,
                         category=entry.get('category', ''), autosave=False)
            seen += 1

    _save()
    st = get_synonyms_stats()
    print(f"✅ Synonyms rebuild: {seen} записів → {st['universal_keys']} універсальних назв",
          flush=True)
    return {'processed': seen, **st}


_load()
