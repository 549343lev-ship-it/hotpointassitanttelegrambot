"""
synonyms.py — Таблиця "схожих товарів".

КОНЦЕПЦІЯ:
  Окремий незалежний блок. НЕ впливає на роботу бота.
  Накопичує: universal_key → {brand: {catalog_name, code, hits}}
  Експортує в Google Sheets з структурою:
    A: універсальна назва | B: 1 пріоритет | C: код | D: 2 пріоритет | E: код | ...

UNIVERSAL KEY:
  = normalized без бренду, нижній регістр, нормалізовані пробіли.
  "Муфта PPR МРЗ ф25х3/4, RAFTEC" → "муфта ppr мрз ф25х3/4"
  "Коліно PPR РН ф25х3/4, Ekoplastik" → "коліно ppr рн ф25х3/4"

ІНТЕГРАЦІЯ (майбутня):
  Щоб підключити до пошуку — в search.py перед Voyage викликати:
    result = synonyms_lookup(universal_key, brand)
    if result: return result

ФАЙЛ:
  DATA_DIR/synonyms.json
  {
    "муфта ppr мрз ф25х3/4": {
      "ekoplastik": {"catalog_name": "Муфта PPR MP3 ф25х3/4 Ekoplastik", "code": "42977", "hits": 5},
      "raftec":     {"catalog_name": "Муфта PPR МРЗ ф25 3/4 RAFTEC",     "code": "88519", "hits": 2}
    }
  }
"""

import os
import re
import json
import time

DATA_DIR      = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
SYNONYMS_FILE = os.path.join(DATA_DIR, "synonyms.json")

# Всі відомі бренди (з BRAND_TOKENS) — для стрипінгу з normalized
_BRAND_PATTERNS = [
    r'\b(raftec|RAFTEC)\b',
    r'\b(ekoplastik|Ekoplastik|PP-RCT)\b',
    r'\b(ECO\s+PPR|ECO)\b',
    r'\b(asg|ASG)\b',
    r'\b(fv\s*plast|FV\s*Plast)\b',
    r'\b(ostendorf|OSTENDORF)\b',
    r'\b(plm|PLM)\b',
    r'\b(rehau|REHAU)\b',
    r'\b(kan|KAN)\b',
    r'\b(fado|FADO)\b',
    r'\b(hidros|HIDROS|Hidros)\b',
    r'\b(idmar|IDMAR)\b',
    r'\b(mirado|MIRADO)\b',
    r'\b(purmo|Purmo)\b',
    r'\b(tatra|TATRA)\b',
    r'\b(wilo|WILO)\b',
    r'\b(grundfos|GRUNDFOS)\b',
    r'\b(biasi|BIASI)\b',
    r'\b(vaillant|Vaillant)\b',
    r'\b(giacomini|Giacomini)\b',
    r'\b(general\s*fittings?)\b',
    r'\b(aquapex|AQUAPEX)\b',
    r'\b(valrom|VALROM)\b',
    r'\b(wavin|Wavin)\b',
    r'\b(lexline|LEXLINE)\b',
    r'\b(pattaroni|Pattaroni)\b',
    r'\b(solomon|SOLOMON)\b',
]
_BRAND_RE = re.compile('|'.join(_BRAND_PATTERNS), re.IGNORECASE)

_SYNONYMS: dict = {}


# ─── Universal key ───────────────────────────────────────────────────────────

def make_universal_key(normalized: str) -> str:
    """
    Будує brand-агностичний ключ з normalized назви.

    "Муфта PPR МРЗ ф25х3/4, RAFTEC"  → "муфта ppr мрз ф25х3/4"
    "Коліно PPR РН ф25х3/4 Ekoplastik" → "коліно ppr рн ф25х3/4"
    """
    s = normalized.lower()
    s = _BRAND_RE.sub('', s)            # прибираємо бренди
    s = re.sub(r'[,;]+', ' ', s)       # коми/крапки з комою → пробіл
    s = re.sub(r'\s+', ' ', s).strip() # нормалізуємо пробіли
    return s


def _detect_brand(catalog_name: str) -> str:
    """Визначає бренд з назви каталогу → ключ для synonyms."""
    low = catalog_name.lower()
    BRAND_MAP = [
        ('ekoplastik', ['ekoplastik', 'pp-rct']),
        ('raftec',     ['raftec']),
        ('eco',        ['eco ppr', '\beco\b']),
        ('asg',        ['\basg\b']),
        ('fv plast',   ['fv plast']),
        ('ostendorf',  ['ostendorf']),
        ('plm',        ['\bplm\b']),
        ('rehau',      ['rehau']),
        ('kan',        ['\bkan\b']),
        ('fado',       ['fado']),
        ('hidros',     ['hidros']),
        ('idmar',      ['idmar']),
        ('mirado',     ['mirado']),
        ('tatra',      ['tatra']),
        ('wilo',       ['wilo']),
        ('grundfos',   ['grundfos']),
        ('biasi',      ['biasi']),
        ('giacomini',  ['giacomini']),
        ('valrom',     ['valrom']),
        ('lexline',    ['lexline']),
    ]
    for brand_key, tokens in BRAND_MAP:
        for t in tokens:
            if re.search(t, low):
                return brand_key
    return '_other'


# ─── Завантаження / збереження ───────────────────────────────────────────────

def _load():
    global _SYNONYMS
    if os.path.exists(SYNONYMS_FILE):
        try:
            with open(SYNONYMS_FILE, encoding='utf-8') as f:
                _SYNONYMS = json.load(f)
            print(f"📖 Synonyms: {len(_SYNONYMS)} universal keys", flush=True)
        except Exception as e:
            print(f"⚠️ synonyms load: {e}", flush=True)
            _SYNONYMS = {}
    else:
        _SYNONYMS = {}


def _save():
    try:
        with open(SYNONYMS_FILE, 'w', encoding='utf-8') as f:
            json.dump(_SYNONYMS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ synonyms save: {e}", flush=True)


# ─── Публічний API ───────────────────────────────────────────────────────────

def synonyms_add(normalized: str, catalog_name: str, code: str = '') -> str:
    """
    Додає запис у synonyms. Викликати після кожного успішного confirmed підбору.

    normalized:   brand-агностична назва від Gemini ("Муфта PPR МРЗ ф25х3/4")
    catalog_name: назва з каталогу ("Муфта PPR MP3, ф 25х3/4\", PP-RCT, Ekoplastik")
    code:         артикул товару (якщо є)

    Повертає universal_key.
    """
    ukey  = make_universal_key(normalized)
    brand = _detect_brand(catalog_name)

    if ukey not in _SYNONYMS:
        _SYNONYMS[ukey] = {}

    existing = _SYNONYMS[ukey].get(brand)
    if existing and existing.get('catalog_name') == catalog_name:
        # Той самий товар — просто збільшуємо hits
        existing['hits'] = existing.get('hits', 1) + 1
        existing['last_seen'] = time.strftime('%Y-%m-%d')
    else:
        _SYNONYMS[ukey][brand] = {
            'catalog_name': catalog_name,
            'code':         str(code) if code else '',
            'hits':         1,
            'last_seen':    time.strftime('%Y-%m-%d'),
        }

    _save()
    return ukey


def synonyms_lookup(normalized: str, brand_key: str = '') -> dict | None:
    """
    Шукає товар за universal_key і брендом.
    Якщо brand_key не вказано — повертає варіант з найбільшим hits.

    Повертає: {'catalog_name': ..., 'code': ..., 'brand': ...} або None.
    """
    ukey = make_universal_key(normalized)
    variants = _SYNONYMS.get(ukey)
    if not variants:
        return None

    if brand_key and brand_key in variants:
        v = variants[brand_key]
        return {'catalog_name': v['catalog_name'], 'code': v['code'], 'brand': brand_key}

    # Без бренду — найпопулярніший
    best = max(variants.items(), key=lambda x: x[1].get('hits', 0))
    return {'catalog_name': best[1]['catalog_name'], 'code': best[1]['code'], 'brand': best[0]}


def get_synonyms_stats() -> dict:
    """Статистика для адмін-команди."""
    total_keys    = len(_SYNONYMS)
    total_entries = sum(len(v) for v in _SYNONYMS.values())
    multi_brand   = sum(1 for v in _SYNONYMS.values() if len(v) > 1)
    return {
        'universal_keys': total_keys,
        'total_entries':  total_entries,
        'multi_brand':    multi_brand,
    }


# ─── Google Sheets Export ────────────────────────────────────────────────────

def export_to_sheets(spreadsheet_id: str, credentials_path: str = None) -> int:
    """
    Експортує synonyms у Google Sheets.
    Структура: A=унів.назва | B=1пріор | C=код | D=2пріор | E=код | ...

    Повертає кількість записаних рядків.
    """
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        print("⚠️ synonyms export: pip install gspread google-auth", flush=True)
        return 0

    scopes = ['https://www.googleapis.com/auth/spreadsheets']

    creds_path = credentials_path or os.environ.get('GOOGLE_CREDENTIALS_PATH', '')
    if not creds_path or not os.path.exists(creds_path):
        # Спроба взяти з env як JSON рядок
        creds_json = os.environ.get('GOOGLE_CREDENTIALS_JSON', '')
        if creds_json:
            import tempfile
            tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
            tmp.write(creds_json)
            tmp.close()
            creds_path = tmp.name
        else:
            print("⚠️ synonyms export: GOOGLE_CREDENTIALS_PATH або GOOGLE_CREDENTIALS_JSON не задано", flush=True)
            return 0

    creds  = Credentials.from_service_account_file(creds_path, scopes=scopes)
    client = gspread.authorize(creds)
    sheet  = client.open_by_key(spreadsheet_id).sheet1

    MAX_BRANDS = 5
    header = ['універсальна назва']
    for i in range(1, MAX_BRANDS + 1):
        header += [f'{i} пріоритет', 'код']

    rows = [header]

    for ukey, variants in sorted(_SYNONYMS.items()):
        # Сортуємо бренди по hits (спадаючий)
        sorted_brands = sorted(variants.items(),
                               key=lambda x: x[1].get('hits', 0), reverse=True)
        row = [ukey]
        for i, (brand, info) in enumerate(sorted_brands[:MAX_BRANDS]):
            row += [info.get('catalog_name', ''), info.get('code', '')]
        # Доповнюємо порожніми якщо < MAX_BRANDS
        while len(row) < 1 + MAX_BRANDS * 2:
            row += ['', '']
        rows.append(row)

    sheet.clear()
    sheet.update('A1', rows)
    print(f"✅ Synonyms → Sheets: {len(rows)-1} рядків", flush=True)
    return len(rows) - 1


# ─── Авто-наповнення зі confirmed кешу ──────────────────────────────────────

def rebuild_from_cache(cache: dict) -> int:
    """
    Одноразово перебудовує synonyms.json з існуючого кешу бота.
    Викликати вручну через /synonyms_rebuild (адмін-команда).

    Повертає кількість доданих записів.
    """
    added = 0
    for key, entry in cache.items():
        if entry.get('status') not in ('confirmed', 'auto'):
            continue
        if entry.get('confidence', 0) < 90:
            continue
        normalized   = entry.get('normalized', '')
        catalog_name = entry.get('catalog_name', '')
        if not normalized or not catalog_name:
            continue
        synonyms_add(normalized, catalog_name)
        added += 1
    print(f"✅ Synonyms rebuild: {added} записів", flush=True)
    return added


# ─── Ініціалізація ───────────────────────────────────────────────────────────
_load()
