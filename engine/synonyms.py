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
    # лінійки RAFTEC — окремі суб-бренди (різна якість/ціна), тому
    # в ключі вони прибираються як серія, а тут дають окрему колонку
    ('raftec gold',   [r'raftec\s*gold']),
    ('raftec black',  [r'raftec\s*black']),
    ('raftec steel',  [r'raftec\s*steel']),
    ('raftec brass',  [r'raftec\s*brass']),
    ('raftec silver', [r'raftec\s*silver']),
    ('raftec',     [r'rafte[cс]']),   # 'с' може бути кириличною (помилка прайсу)
    ('asg',        [r'(?<![a-z])asg(?![a-z])']),
    ('ostendorf',  [r'ostendorf']),
    ('fv plast',   [r'fv\s*plast', r'(?<![a-z])fv(?![a-z])']),
    ('plm strong', [r'plm\s*strong']),
    ('plm base',   [r'plm\s*base']),
    ('plm',        [r'(?<![a-z])plm(?![a-z])']),
    ('eco',        [r'(?<![a-z])eco(?![a-z])']),
    ('rehau',      [r'rehau']),
    # KAN, FADO, VALTEC, HLV, ICMA, BUGATTI, TECE, KOER, Aquapex, HeatPex,
    # SD Forte, Sandi, Magnaplast — ЗАБОРОНЕНІ, не пропонуємо, не групуємо окремо
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
    # бренди з DEFAULT_BRAND_PRIORITY яких бракувало
    ('lider',      [r'(?<![a-z])lider(?![a-z])', r'лідер']),
    ('ecosoft',    [r'ecosoft', r'екософт']),
    ('ecostar',    [r'ecostar']),
    ('walraven',   [r'walraven']),
    ('gebo',       [r'(?<![a-z])gebo(?![a-z])']),
    # ── розширений реєстр (витягнутий з каталогу) ────────────────────────────
    # Радіатори / опалення
    ('korado',     [r'korado']),
    ('kermi',      [r'kermi']),
    ('korad',      [r'(?<![a-z])korad(?![a-z])']),
    ('vogel noot', [r'vogel\s*&?\s*noot']),
    ('global',     [r'(?<![a-z])global(?![a-z])']),
    ('altep',      [r'altep']),
    ('mirater',    [r'mirater']),
    ('deffi',      [r'deffi']),
    ('термобар',   [r'термобар']),
    # Котли / бойлери / водонагрівачі
    ('ariston',    [r'ariston']),
    ('tesy',       [r'(?<![a-z])tesy(?![a-z])']),
    ('drazice',    [r'drazi[cс]e']),
    ('bosch',      [r'(?<![a-z])bosch(?![a-z])']),
    ('atlantic',   [r'atlantic']),
    ('viessmann',  [r'viessmann']),
    ('protherm',   [r'protherm']),
    ('baxi',       [r'(?<![a-z])baxi(?![a-z])']),
    ('hitherm',    [r'hi\s*therm']),
    ('kospel',     [r'kospel']),
    ('тенко',      [r'тенко']),
    ('дтм',        [r'(?<![а-яіїєґ])дтм(?![а-яіїєґ])']),
    ('aton',       [r'(?<![a-z])aton(?![a-z])']),
    ('italtherm',  [r'italtherm']),
    ('nova florida',[r'nova\s*florida']),
    ('thermo alliance',[r'thermo\s*alliance']),
    ('eldom',      [r'eldom']),
    ('gorenje',    [r'gorenje|tiki']),
    # Насоси
    ('pedrollo',   [r'pedrollo']),
    ('dab',        [r'(?<![a-z])dab(?![a-z])']),
    ('sprut',      [r'sprut']),
    ('optima',     [r'(?<![a-z])optima(?![a-z])']),
    ('euroaqua',   [r'euroaqua']),
    # Сантехніка / змішувачі
    ('volle',      [r'volle']),
    ('qtap',       [r'q\s*tap']),
    ('cersanit',   [r'cersanit']),
    ('franke',     [r'franke|тека|teka']),
    ('fancy marble',[r'fancy\s*marble']),
    ('miraggio',   [r'miraggio']),
    ('globus lux', [r'globus\s*lux']),
    ('lidz',       [r'(?<![a-z])lidz(?![a-z])']),
    ('koller pool',[r'koller\s*pool']),
    ('kolo',       [r'(?<![a-z])kolo(?![a-z])']),
    ('ravak',      [r'ravak']),
    ('radaway',    [r'radaway']),
    ('devit',      [r'devit']),
    ('paffoni',    [r'paffoni']),
    ('kludi',      [r'kludi']),
    ('fabiano',    [r'fabiano']),
    ('besco',      [r'besco']),
    ('атем',       [r'(?<![а-яіїєґ])атем(?![а-яіїєґ])']),
    ('adamant',    [r'adamant']),
    ('kk pol',     [r'kk\s*pol']),
    ('arte',       [r'(?<![a-z])arte(?![a-z])']),
    ('liberta',    [r'liberta']),
    ('moreli',     [r'moreli']),
    ('epelli',     [r'epelli']),
    ('trinnity',   [r'trinnity']),
    ('waveglass',  [r'wave\s*glass']),
    ('wellss',     [r'wells+']),
    # Труби / фітинги / арматура
    ('uzкм',       [r'(?<![а-яіїєґ])узкм(?![а-яіїєґ])']),
    ('unidelta',   [r'unidelta']),
    ('uponor',     [r'uponor']),
    # valtec, icma, hlv — заборонені
    ('luxor',      [r'(?<![a-z])luxor(?![a-z])']),
    ('duker',      [r'd[uü]ker']),
    ('aniplast',   [r'ani\s*plast']),
    ('santehplast',[r'santeh\s*plast']),
    ('solo plast', [r'solo\s*plast']),
    ('інсталпласт',[r'інсталпласт|instalplast']),
    ('polmark',    [r'polmark']),
    ('kalde',      [r'kalde']),
    ('ovi',        [r'(?<![a-z])ovi(?![a-z])|evci']),
    ('candan',     [r'candan']),
    ('viega',      [r'viega']),
    ('sanha',      [r'sanha']),
    ('herz',       [r'(?<![a-z])herz(?![a-z])']),
    ('flamco',     [r'flamco']),
    ('salus',      [r'salus']),
    ('tega',       [r'(?<![a-z])tega(?![a-z])']),
    ('easyfloor',  [r'easy\s*floor']),
    ('argo',       [r'(?<![a-z])argo(?![a-z])']),
    ('fox',        [r'(?<![a-z])fox(?![a-z])']),
    ('rens',       [r'(?<![a-z])rens(?![a-z])']),
    ('terra teknik',[r'terra\s*teknik']),
    ('venta',      [r'(?<![a-z])venta(?![a-z])']),
    ('breeze',     [r'breeze']),
    ('weston',     [r'weston']),
    ('gross',      [r'(?<![a-z])gross(?![a-z])']),
    ('engel',      [r'(?<![a-z])engel(?![a-z])']),
    ('маріо',      [r'(?<![а-яіїєґ])маріо(?![а-яіїєґ])|класік\s*маріо']),
    ('водолій',    [r'водолій']),
    ('еверест',    [r'еверест']),
    # Ізоляція
    ('k-flex',     [r'k-?flex']),
    ('thermaflex', [r'thermaflex']),
    ('ecoflex',    [r'ecoflex']),
    ('теплоізол',  [r'теплоізол']),
    # Фільтрація
    ('atlas filtri',[r'atlas\s*filtri']),
    ('bwt',        [r'(?<![a-z])bwt(?![a-z])']),
    ('нова вода',  [r'нова\s*вода']),
    ('filtrons',   [r'filtrons']),
    ('fil-nox',    [r'fil-?nox']),
    ('topaz',      [r'topaz']),
    ('aquastream', [r'aqua\s*stream']),
    ('ajax',       [r'(?<![a-z])ajax(?![a-z])']),
    ('kvado',      [r'kvado']),
]

# Маркери товарних ліній — прибираємо, бо вони брендозалежні
_SERIES = [
    r'pp-?rct', r'ht\s*safe', r'(?<![a-z])htr(?![a-z])', r'kg\s*2000',
    r'rafte[cс]\s*(black|gold|brass|steel|silver)(\s+block)?',   # лінійки RAFTEC лише у зв'язці
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

# ─── Правила канонізації по категоріях (synonym_rules.json) ──────────────────

RULES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'synonym_rules.json')
_RULES: dict | None = None


def _rules() -> dict:
    """Лінива загрузка synonym_rules.json (правила поруч із модулем)."""
    global _RULES
    if _RULES is not None:
        return _RULES
    _RULES = {}
    for path in (RULES_FILE, os.path.join(DATA_DIR, 'synonym_rules.json')):
        if os.path.exists(path):
            try:
                with open(path, encoding='utf-8') as f:
                    _RULES = json.load(f)
                n = sum(len(v.get('rules', [])) for k, v in _RULES.items()
                        if isinstance(v, dict))
                print(f"📐 Synonyms: правил канонізації {n}", flush=True)
                break
            except Exception as e:
                print(f"⚠️ synonym_rules.json: {e}", flush=True)
    return _RULES


def reload_rules() -> int:
    """Перечитує правила з диска (після ручного редагування)."""
    global _RULES
    _RULES = None
    r = _rules()
    return sum(len(v.get('rules', [])) for v in r.values() if isinstance(v, dict))


def _apply_rules(s: str, category: str) -> str:
    """Застосовує drop/map правила категорії до канонізованого рядка."""
    rules = _rules()
    if not rules:
        return s
    chain = list((rules.get('_common', {}) or {}).get('rules', []))
    chain += list((rules.get(category, {}) or {}).get('rules', []))
    for r in chain:
        try:
            if r.get('when')   and not re.search(r['when'], s):   continue
            if r.get('unless') and     re.search(r['unless'], s): continue
            for d in r.get('drop', []):
                s = re.sub(d, ' ', s)
            for a, b in (r.get('map') or {}).items():
                s = re.sub(a, b, s)
        except re.error:
            continue
    return re.sub(r'\s+', ' ', s).strip()


def detect_family(s: str, category: str) -> str:
    """Мітка групи товару (читабельна, на підбір не впливає)."""
    for f in ((_rules().get(category, {}) or {}).get('family', [])):
        try:
            if re.search(f['match'], s):
                return f['label']
        except re.error:
            continue
    return ''


_CATALOG_IDX: dict | None = None   # {name.lower(): {'code':..., 'category':...}}


def _catalog_index() -> dict:
    """Лінивий індекс каталогу: назва товару → артикул + категорія."""
    global _CATALOG_IDX
    if _CATALOG_IDX is not None:
        return _CATALOG_IDX
    _CATALOG_IDX = {}
    try:
        try:
            from catalog.catalog import CATALOG
        except Exception:
            from catalog import CATALOG
        for it in CATALOG:
            meta = {
                'code':     it.get('artikul', '') or '',
                'category': it.get('category', '') or '',
            }
            for field in ('name', 'name_full'):
                nm = (it.get(field) or '').strip().lower()
                if nm:
                    _CATALOG_IDX.setdefault(nm, meta)
                    ck = canonical(nm)  # без категорії: сирий ключ
                    if ck:
                        _CATALOG_IDX.setdefault('~' + ck, meta)   # ~ = канонічний ключ
        print(f"📇 Synonyms: індекс каталогу {len(_CATALOG_IDX)} ключів", flush=True)
    except Exception as e:
        print(f"⚠️ synonyms: каталог недоступний ({e}) — коди будуть порожні", flush=True)
    return _CATALOG_IDX


def _catalog_meta(catalog_name: str) -> dict:
    """Артикул і категорія товару за назвою з каталогу."""
    idx = _catalog_index()
    if not idx:
        return {}
    nm = (catalog_name or '').strip().lower()
    hit = idx.get(nm) or idx.get(re.sub(r'\s+', ' ', nm))
    if hit:
        return hit
    # запасний варіант: збіг за канонічною формою
    ck = canonical(catalog_name)
    return idx.get('~' + ck, {}) if ck else {}


# ─── Канонізація ─────────────────────────────────────────────────────────────

def canonical(name: str, category: str = '') -> str:
    """
    catalog_name → канонічний brand-агностичний ключ.

    "Утеплювач ламін. для труб ф 22х6 мм, синій, PLM" → "утеплювач ламін для труб ф22х6 синій"
    "Коліно вн. канал. ф110 х 30°, сіре, HT Safe, OSTENDORF" → "коліно вн канал ф110х30 сіре"
    "Коліно внут. канал. ф110 х 30°, сіре, HTR, ASG"        → те саме
    """
    s = (name or '').lower()

    s = _SERIES_RE.sub(' ', s)   # серії першими: деякі прив'язані до назви бренду
    s = _BRAND_RE.sub(' ', s)

    for pat, repl in _LOOKALIKE:
        s = re.sub(pat, repl, s)
    for pat, repl in _ABBREV:
        s = re.sub(pat, repl, s)

    s = s.replace('x', 'х').replace('×', 'х')          # латинська x → кирилична
    s = re.sub(r'(\d),(\d)', r'\1.\2', s)              # 1,8 → 1.8
    s = re.sub(r'87\.5(?=\s*°|\s|$)', '87', s)         # 87,5° = 87°
    s = re.sub(r'(\d)\.0(?!\d)', r'\1', s)             # 1.0 → 1, L=0.50 → L=0.5
    s = re.sub(r'\bф\s*', 'ф', s)                      # "ф 22" → "ф22"
    s = re.sub(r'\bl\s*=\s*', 'l=', s)                 # "L = 0.5" → "l=0.5"
    s = re.sub(r'\bdn\s*', 'dn', s)
    s = re.sub(r'\s*х\s*', 'х', s)                     # "110 х 30" → "110х30"
    s = re.sub(r'(?<!\d)\.(?!\d)', ' ', s)             # крапка, крім десяткової
    s = re.sub(r'[°"\'`,;:()\[\]]+', ' ', s)
    s = re.sub(r'\s*/\s*', '/', s)
    s = re.sub(r'\s+', ' ', s).strip()
    if category:
        s = _apply_rules(s, category)
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
                migrated[k] = {'variants': v, 'aliases': [], 'category': '',
                               'family': '', 'attrs': {}}
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
                 code: str = '', category: str = '', autosave: bool = True,
                 lookup_catalog: bool = False) -> str:
    """
    Додає підбір у довідник. Ключ — з catalog_name, фраза менеджера йде в aliases.
    Повертає universal_key.
    """
    if not catalog_name or not catalog_name.strip():
        return ''

    # Каталог на гарячому шляху НЕ чіпаємо: побудова індексу займає ~14 с
    # і заблокувала б обробку замовлення менеджера. Категорію передає cache.py,
    # коди дозаповнює enrich_from_catalog() (адмінська операція).
    if lookup_catalog:
        meta = _catalog_meta(catalog_name)
        if not code:
            code = meta.get('code', '')
        if not category:
            category = meta.get('category', '')

    ukey  = canonical(catalog_name, category)
    if not ukey:
        return ''
    brand = _detect_brand(catalog_name)
    today = time.strftime('%Y-%m-%d')

    rec = _SYNONYMS.setdefault(ukey, {
        'variants': {}, 'aliases': [], 'category': category,
        'family': detect_family(ukey, category),
        'attrs': _parse_attrs(catalog_name),
    })
    if category and not rec.get('category'):
        rec['category'] = category
    if not rec.get('family'):
        rec['family'] = detect_family(ukey, rec.get('category', ''))

    v = rec['variants'].get(brand)
    if v and v.get('catalog_name') == catalog_name:
        v['hits']      = v.get('hits', 1) + 1
        v['last_seen'] = today
        if code and not v.get('code'):
            v['code'] = str(code)
    elif v and code and not v.get('code'):
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
    families      = len({r.get('family') for r in _SYNONYMS.values() if r.get('family')})
    return {
        'universal_keys': total_keys,
        'total_entries':  total_entries,
        'multi_brand':    multi_brand,
        'aliases':        total_aliases,
        'families':       families,
    }


# ─── Збагачення аналогами з каталогу ─────────────────────────────────────────

_CATALOG_GROUPS: dict | None = None


def _catalog_groups() -> dict:
    """Лінива група каталогу: canonical(name) → {brand: {catalog_name, code, category}}."""
    global _CATALOG_GROUPS
    if _CATALOG_GROUPS is not None:
        return _CATALOG_GROUPS
    _CATALOG_GROUPS = {}
    try:
        try:
            from catalog.catalog import CATALOG
        except Exception:
            from catalog import CATALOG
        for it in CATALOG:
            nm = (it.get('name') or '').strip()
            if not nm:
                continue
            ck = canonical(nm, it.get('category', ''))
            if not ck:
                continue
            brand = _detect_brand(nm)
            if brand == '_other':
                continue
            g = _CATALOG_GROUPS.setdefault(ck, {})
            if brand not in g:
                g[brand] = {
                    'catalog_name': nm,
                    'code':         it.get('artikul', '') or '',
                    'category':     it.get('category', '') or '',
                }
        multi = sum(1 for v in _CATALOG_GROUPS.values() if len(v) > 1)
        print(f"📇 Synonyms: груп каталогу {len(_CATALOG_GROUPS)} ({multi} з аналогами)", flush=True)
    except Exception as e:
        print(f"⚠️ synonyms: групи каталогу недоступні ({e})", flush=True)
    return _CATALOG_GROUPS


def enrich_from_catalog(autosave: bool = True) -> dict:
    """
    Дозаповнює кожну універсальну назву аналогами інших виробників з каталогу.
    Варіанти з каталогу отримують hits=0 і source='catalog' — щоб відрізнити
    від тих, що реально підбирались.
    """
    groups = _catalog_groups()
    if not groups:
        return {'enriched_keys': 0, 'added_variants': 0}

    enriched = added = 0
    for ukey, rec in _SYNONYMS.items():
        g = groups.get(ukey)
        if not g:
            continue
        touched = False
        for brand, info in g.items():
            if brand in rec['variants']:
                # дозаповнюємо код, якщо його бракувало
                if info['code'] and not rec['variants'][brand].get('code'):
                    rec['variants'][brand]['code'] = info['code']
                continue
            rec['variants'][brand] = {
                'catalog_name': info['catalog_name'],
                'code':         info['code'],
                'hits':         0,
                'source':       'catalog',
                'last_seen':    '',
            }
            added += 1
            touched = True
        if not rec.get('category'):
            rec['category'] = next(iter(g.values())).get('category', '')
        if not rec.get('family'):
            rec['family'] = detect_family(ukey, rec.get('category', ''))
        if touched:
            enriched += 1

    if autosave:
        _save()
    print(f"✅ Synonyms enrich: +{added} варіантів у {enriched} назвах", flush=True)
    return {'enriched_keys': enriched, 'added_variants': added}


def full_rebuild() -> dict:
    """
    Повний rebuild:
    1. build_from_catalog_groups — будує базу груп з каталогу (2+ бренди)
    2. rebuild_from_cache — поверх додає органічні aliases і hits з кешу
    """
    from clients.cache import get_cache
    from clients import clients as _clients

    # Крок 1: будуємо базу з каталогу
    r1 = build_from_catalog_groups(autosave=False)

    # Крок 2: органічні aliases і hits з кешу бота + кешів клієнтів
    try:
        client_caches = {}
        for slug, _ in _clients.list_clients().items():
            cc = _clients.get_client_cache(slug)
            if cc:
                client_caches[slug] = cc
    except Exception:
        client_caches = {}

    r2 = rebuild_from_cache(get_cache(), client_caches, reset=False)

    st = get_synonyms_stats()
    return {
        'catalog_keys':      r1.get('added_keys', 0),
        'catalog_added':     r1.get('added_variants', 0),
        'organic_keys':      r2.get('universal_keys', 0),
        'organic_entries':   r2.get('total_entries', 0),
        'universal_keys':    st['universal_keys'],
        'total_entries':     st['total_entries'],
        'multi_brand':       st['multi_brand'],
    }


def reset_to_organic(autosave: bool = True) -> dict:
    """
    Прибирає всі записи що були додані автоматично (hits=0, source='catalog')
    але НЕ були підтверджені реальними підборами.
    Залишає тільки органічні записи (hits > 0).
    """
    global _SYNONYMS
    removed_keys = removed_variants = kept_keys = 0

    for ukey in list(_SYNONYMS.keys()):
        rec = _SYNONYMS[ukey]
        variants = rec.get('variants', {})

        organic = {
            b: v for b, v in variants.items()
            if v.get('hits', 0) > 0 or v.get('source', '') != 'catalog'
        }
        removed_variants += len(variants) - len(organic)

        if not organic:
            del _SYNONYMS[ukey]
            removed_keys += 1
        else:
            rec['variants'] = organic
            kept_keys += 1

    if autosave:
        _save()

    st = get_synonyms_stats()
    print(
        f"✅ reset_to_organic: видалено {removed_keys} каталожних ключів, "
        f"{removed_variants} варіантів. Залишилось {kept_keys} органічних.",
        flush=True
    )
    return {'removed_keys': removed_keys, 'removed_variants': removed_variants,
            'kept_keys': kept_keys, **st}


def build_from_catalog_groups(autosave: bool = True) -> dict:
    """
    Будує synonyms з _catalog_groups() — так само як кнопка 'схожі',
    але для всього каталогу.

    Логіка:
    - canonical(name, category) групує товари різних брендів в один ключ
    - Тільки групи з 2+ брендами (реальні аналоги) або органічні записи
    - Органічні записи (hits > 0) зберігаються і мерджаться
    - Результат: ~2-3k ключів, ~10-20k варіантів
    """
    global _SYNONYMS

    groups = _catalog_groups()
    if not groups:
        print("⚠️ build_from_catalog_groups: CATALOG недоступний", flush=True)
        return {'added_keys': 0, 'added_variants': 0}

    # Зберігаємо органічні записи (реальні підбори)
    organic = {}
    for ukey, rec in _SYNONYMS.items():
        has_organic = any(
            v.get('hits', 0) > 0 or v.get('source', '') != 'catalog'
            for v in rec.get('variants', {}).values()
        )
        if has_organic:
            organic[ukey] = rec

    # Будуємо новий словник з груп каталогу
    new_synonyms = {}
    added_keys = added_variants = skipped = 0

    for ukey, variants_by_brand in groups.items():
        # Беремо тільки групи з 2+ брендами (справжні аналоги)
        # АБО якщо є органічний запис для цього ключа
        has_organic_match = ukey in organic
        if len(variants_by_brand) < 2 and not has_organic_match:
            skipped += 1
            continue

        # Беремо мета-дані з першого варіанту
        first = next(iter(variants_by_brand.values()))
        cat   = first.get('category', '')

        rec = new_synonyms.setdefault(ukey, {
            'variants': {},
            'aliases':  organic.get(ukey, {}).get('aliases', []),
            'category': cat,
            'family':   detect_family(ukey, cat),
            'attrs':    _parse_attrs(first.get('catalog_name', ukey)),
        })

        # Мерджимо органічні варіанти (пріоритет)
        if has_organic_match:
            for brand, v in organic[ukey]['variants'].items():
                rec['variants'][brand] = v

        # Додаємо каталожні варіанти
        for brand, info in variants_by_brand.items():
            if brand in rec['variants']:
                # Органічний вже є — тільки дозаповнюємо код
                if info['code'] and not rec['variants'][brand].get('code'):
                    rec['variants'][brand]['code'] = info['code']
                continue
            rec['variants'][brand] = {
                'catalog_name': info['catalog_name'],
                'code':         info['code'],
                'hits':         0,
                'source':       'catalog',
                'last_seen':    '',
            }
            added_variants += 1

        added_keys += 1

    # Додаємо органічні записи яких немає в каталожних групах
    for ukey, rec in organic.items():
        if ukey not in new_synonyms:
            new_synonyms[ukey] = rec

    _SYNONYMS = new_synonyms

    if autosave:
        _save()

    st = get_synonyms_stats()
    print(
        f"✅ build_from_catalog_groups: {added_keys} ключів з аналогами, "
        f"+{added_variants} каталожних варіантів, {skipped} одиночних пропущено. "
        f"Всього: {st['universal_keys']} ключів, {st['total_entries']} варіантів, "
        f"{st['multi_brand']} з кількома брендами.",
        flush=True
    )
    return {'added_keys': added_keys, 'added_variants': added_variants,
            'skipped': skipped, **st}


# ─── Пріоритет брендів для експорту ──────────────────────────────────────────

# Локальна копія пріоритетів (щоб не тягнути важкий імпорт engine.search).
# Має збігатися з DEFAULT_BRAND_PRIORITY у search.py.
BRAND_PRIORITY = {
    'plastic_ppr':             ['ekoplastik', 'asg', 'raftec', 'fv plast', 'plm'],
    'push_systems':            ['raftec', 'rehau', 'uponor'],
    'shutoff_valves':          ['raftec gold', 'raftec black', 'plm strong', 'plm base', 'asg', 'eco'],
    'adapters_reducers':       ['raftec', 'raftec gold', 'lexline', 'узкм'],
    'sewage':                  ['asg', 'ostendorf', 'plm', 'valrom'],
    'metal_plastic':           ['raftec', 'tweetop'],
    'pumps':                   ['termojet', 'tatra', 'grundfos', 'raftec', 'wilo', 'lider'],
    'radiators_radiatorsvalve':['idmar', 'biasi', 'hidros', 'purmo', 'mirado', 'korad'],
    'insulation':              ['plm', 'теплоізол', 'sanflex', 'k-flex'],
    'underfloor_heating':      ['raftec', 'plm', 'rehau', 'danfoss'],
    'filtration':              ['ecosoft', 'filtrons', 'bwt'],
    'safety_valves':           ['raftec', 'herz', 'flamco', 'caleffi', 'plm'],
    'heating':                 ['esbe', 'caleffi', 'honeywell', 'herz', 'afriso'],
    'fasteners_sealants':      ['eco', 'raftec', 'walraven'],
    'water_meters':            ['ecostar', 'gidrotek'],
}

_PRIORITY_SYNCED = False


def _sync_priority_from_search() -> None:
    """Одноразово підтягує DEFAULT_BRAND_PRIORITY із search.py, якщо доступний."""
    global _PRIORITY_SYNCED
    if _PRIORITY_SYNCED:
        return
    _PRIORITY_SYNCED = True
    dbp = None
    for mod in ('engine.search', 'search'):
        try:
            dbp = __import__(mod, fromlist=['DEFAULT_BRAND_PRIORITY']).DEFAULT_BRAND_PRIORITY
            break
        except Exception:
            continue
    if not dbp:
        return
    for cat, toks in dbp.items():
        BRAND_PRIORITY[cat] = [t[0].lower() for t in toks]


def _brand_order(category: str) -> list:
    """Порядок брендів для категорії."""
    _sync_priority_from_search()
    return BRAND_PRIORITY.get(category, [])


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

    MAX_BRANDS = 8
    header = ['категорія', 'група', 'універсальна назва']
    for i in range(1, MAX_BRANDS + 1):
        header += [f'{i} пріоритет', 'код']

    rows = [header]
    # сортуємо за групою, потім за назвою — однотипні товари поруч
    for ukey, rec in sorted(_SYNONYMS.items(),
                            key=lambda x: (x[1].get('category', '') or '',
                                          x[1].get('family', '') or 'яяя', x[0])):
        row = [rec.get('category', ''), rec.get('family', ''), ukey]
        for _brand, info in _sorted_variants(rec)[:MAX_BRANDS]:
            row += [info.get('catalog_name', ''), info.get('code', '')]
        while len(row) < 3 + MAX_BRANDS * 2:
            row += ['', '']
        rows.append(row)

    sheet.clear()
    try:
        sheet.update(values=rows, range_name='A1')   # gspread >= 6
    except TypeError:
        sheet.update('A1', rows)                     # gspread 5.x
    print(f"✅ Synonyms → Sheets: {len(rows)-1} рядків", flush=True)
    return len(rows) - 1


# ─── Перебудова з кешу ───────────────────────────────────────────────────────

def rebuild_from_cache(cache: dict, client_caches: dict = None, reset: bool = True) -> dict:
    """
    Перебудовує довідник з кешу бота (+ опційно клієнтських кешів).
    reset=True (дефолт) — скидає і будує з нуля.
    reset=False — мерджить поверх існуючих даних (використовується в full_rebuild).
    """
    global _SYNONYMS
    if reset:
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
                     category=entry.get('category', ''), autosave=False,
                     lookup_catalog=True)
        seen += 1

    for slug, ccache in (client_caches or {}).items():
        for key, entry in ccache.items():
            if entry.get('status') == 'banned':
                continue
            catalog_name = entry.get('catalog_name', '')
            if not catalog_name:
                continue
            synonyms_add(key.split('::')[0], catalog_name,
                         category=entry.get('category', ''), autosave=False,
                         lookup_catalog=True)
            seen += 1

    enr = enrich_from_catalog(autosave=False)
    _save()
    st = get_synonyms_stats()
    print(f"✅ Synonyms rebuild: {seen} записів → {st['universal_keys']} універсальних назв",
          flush=True)
    return {'processed': seen, **st, **enr}


_load()
