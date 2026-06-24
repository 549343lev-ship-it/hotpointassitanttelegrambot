"""
bot.py — Telegram бот для підбору сантехніки
Архітектура: Gemini 2.5 Flash (OCR) → keyword пошук по JSON → Claude Sonnet (фінальний вибір)

ВСТАНОВЛЕННЯ:
  pip install pytelegrambotapi anthropic google-genai openpyxl pandas

ЗМІННІ СЕРЕДОВИЩА:
  TELEGRAM_TOKEN  — токен бота
  ANTHROPIC_KEY   — ключ Claude
  GEMINI_KEY      — ключ Gemini

ПІДГОТОВКА КАТАЛОГУ:
  Запусти один раз: python build_catalog.py
  Це перетворить всі xlsx файли в catalog.json
"""

import os, json, re, base64, threading, time, zipfile
import xml.etree.ElementTree as ET
from io import BytesIO

import telebot
import anthropic
import pandas as pd
from google import genai as genai_new
from google.genai import types as genai_types

# ═══════════════════════════════════════════════════════════════════════════════
# ІНІЦІАЛІЗАЦІЯ
# ═══════════════════════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY")
GEMINI_KEY     = os.environ.get("GEMINI_KEY")

bot           = telebot.TeleBot(TELEGRAM_TOKEN)
claude        = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
gemini_client = genai_new.Client(api_key=GEMINI_KEY)

CATALOG_PATH = "catalog.json"
RULES_FILE   = "rules.txt"

# Фіксовані файли каталогу (англійські імена як на github)
CATALOG_FILES = [
    ("adapters_reducers",        "adapters_reducers"),
    ("automation",               "automation"),
    ("boilers",                  "boilers"),
    ("fasteners_sealants",       "fasteners_sealants"),
    ("filtration",               "filtration"),
    ("heating",                  "heating"),
    ("hoses",                    "hoses"),
    ("insulation",               "insulation"),
    ("metal_plastic",            "metal_plastic"),
    ("mixers_faucets",           "mixers_faucets"),
    ("plastic_ppr",              "plastic_ppr"),
    ("pumps",                    "pumps"),
    ("push_systems",             "push_systems"),
    ("radiators_radiatorsvalve", "radiators_radiatorsvalve"),
    ("safety_valves",            "safety_valves"),
    ("sanitary_ware",            "sanitary_ware"),
    ("sewage",                   "sewage"),
    ("shutoff_valves",           "shutoff_valves"),
    ("siphons_fittings",         "siphons_fittings"),
    ("towel_warmers",            "towel_warmers"),
    ("underfloor_heating",       "underfloor_heating"),
    ("water_heaters",            "water_heaters"),
    ("water_meters",             "water_meters"),
]

# ═══════════════════════════════════════════════════════════════════════════════
# АВТОБУДОВА КАТАЛОГУ
# УВАГА: файли прайсу мають SharedStrings.xml з ВЕЛИКОЇ літери —
# стандартний openpyxl їх не читає. Використовуємо zipfile+xml напряму.
# ═══════════════════════════════════════════════════════════════════════════════

FILE_CATEGORIES = {
    'пластик': 'plastic_ppr', 'ppr': 'plastic_ppr', 'ппр': 'plastic_ppr',
    'металопластик': 'metal_plastic', 'push': 'push_systems', 'пуш': 'push_systems',
    'каналізація': 'sewage', 'канализ': 'sewage',
    'запірна': 'shutoff_valves', 'запорн': 'shutoff_valves',
    'арматура': 'safety_valves',
    'переходники': 'adapters_reducers', 'перехідники': 'adapters_reducers',
    'насос': 'pumps', 'котли': 'boilers', 'котел': 'boilers',
    'водонагр': 'water_heaters', 'бойлер': 'water_heaters',
    'опалення': 'heating', 'автоматика': 'automation',
    'очистка': 'filtration', 'фільтр': 'filtration',
    'водомір': 'water_meters', 'водолічильник': 'water_meters',
    'кріплення': 'fasteners_sealants', 'ущільнювач': 'fasteners_sealants',
    'радіатор': 'radiators_radiatorsvalve',
    'сифон': 'siphons_fittings', 'змішувач': 'mixers_faucets',
    'санфаянс': 'sanitary_ware', 'підлога': 'underfloor_heating',
    'рушникосушіл': 'towel_warmers', 'шланг': 'hoses', 'ізоляц': 'insulation',
}

def guess_category(filename: str) -> str:
    fn = filename.lower()
    for key, cat in FILE_CATEGORIES.items():
        if key in fn:
            return cat
    return 'other'

def clean_name(name: str) -> str:
    name = re.sub(r'\s*\{[^}]+\}', '', name)  # прибираємо {4/100}
    return re.sub(r'\s+', ' ', name).strip()

def is_product_row(name: str, price_str: str, artikul: str) -> bool:
    if not name or len(name) < 5:
        return False
    if name.startswith('*'):
        return False
    words = name.split()
    if all(w.isupper() for w in words if w.isalpha()) and not any(c.isdigit() for c in name):
        return False
    try:
        if float(str(price_str).replace(',', '.').strip()) > 0:
            return True
    except (ValueError, TypeError):
        pass
    if artikul and str(artikul).strip() not in ('', 'nan', '0', '0.0'):
        return True
    return False

def _read_xlsx_rows(path: str) -> list[list]:
    """Читає xlsx через zipfile щоб обійти баг з регістром SharedStrings.xml"""
    rows = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            ss_path = next((n for n in names if n.lower() == 'xl/sharedstrings.xml'), None)
            strings = []
            if ss_path:
                with z.open(ss_path) as f:
                    tree = ET.parse(f)
                    root = tree.getroot()
                    ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                    for si in root.findall('.//x:si', ns):
                        texts = [t.text or '' for t in si.findall('.//x:t', ns)]
                        strings.append(''.join(texts))
            ws_path = next((n for n in names if n.lower() == 'xl/worksheets/sheet1.xml'), 'xl/worksheets/sheet1.xml')
            with z.open(ws_path) as f:
                tree = ET.parse(f)
                root = tree.getroot()
                ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                for row in root.findall('.//x:row', ns):
                    rv = []
                    for c in row.findall('x:c', ns):
                        t = c.get('t', '')
                        v = c.find('x:v', ns)
                        if v is None:       rv.append('')
                        elif t == 's':      rv.append(strings[int(v.text)] if int(v.text) < len(strings) else '')
                        elif t == 'e':      rv.append('')
                        else:               rv.append(v.text or '')
                    if rv:
                        rows.append(rv)
    except Exception as e:
        print(f"  ⚠️  xlsx помилка {path}: {e}")
    return rows

def _read_xls_rows(path: str) -> list[list]:
    """Читає старий .xls формат через xlrd"""
    rows = []
    try:
        import xlrd
        wb = xlrd.open_workbook(path)
        ws = wb.sheet_by_index(0)
        for i in range(ws.nrows):
            rows.append([str(ws.cell_value(i, j)) for j in range(ws.ncols)])
    except ImportError:
        print("  ⚠️  xlrd не встановлено: pip install xlrd")
    except Exception as e:
        print(f"  ⚠️  xls помилка {path}: {e}")
    return rows

def build_catalog_from_xlsx() -> list[dict]:
    """
    Читає xlsx файли по списку CATALOG_FILES.
    Кожен файл = окрема категорія товарів.
    Використовує zipfile напряму через баг openpyxl з регістром SharedStrings.xml.
    """
    catalog = []
    search_dirs = ['.', 'src', os.path.dirname(os.path.abspath(__file__))]

    for key, category in CATALOG_FILES:
        found = False
        for d in search_dirs:
            path = os.path.join(d, f"{key}.xlsx")
            if not os.path.exists(path):
                path = os.path.join(d, f"{key}.xls")
                if not os.path.exists(path):
                    continue

            try:
                if path.endswith('.xls'):
                    rows = _read_xls_rows(path)
                else:
                    rows = _read_xlsx_rows(path)

                count = 0
                for row in rows:
                    while len(row) < 4:
                        row.append('')
                    name    = clean_name(str(row[0]).strip())
                    artikul = str(row[1]).strip()
                    price_s = str(row[2]).strip()
                    kod     = str(row[3]).strip().rstrip('.0')
                    if not is_product_row(name, price_s, artikul):
                        continue
                    try:
                        price = float(price_s.replace(',', '.'))
                    except (ValueError, TypeError):
                        price = 0.0
                    catalog.append({
                        'name':     name,
                        'artikul':  artikul if artikul not in ('nan','0','0.0','') else '',
                        'kod':      kod if kod not in ('nan','') else '',
                        'category': category,
                        'price':    price,
                    })
                    count += 1
                print(f"  ✅ {key}.xlsx [{category}]: {count} товарів")
                found = True
                break
            except Exception as e:
                print(f"  ❌ {key}.xlsx: {e}")

        if not found:
            print(f"  ⚠️  {key}.xlsx не знайдено")

    return catalog

# ═══════════════════════════════════════════════════════════════════════════════
# ЗАВАНТАЖЕННЯ КАТАЛОГУ
# ═══════════════════════════════════════════════════════════════════════════════
print("📦 Завантажую каталог...")
if not os.path.exists(CATALOG_PATH):
    print("⚙️  catalog.json не знайдено — будую з xlsx файлів...")
    CATALOG = build_catalog_from_xlsx()
    if CATALOG:
        with open(CATALOG_PATH, "w", encoding="utf-8") as f:
            json.dump(CATALOG, f, ensure_ascii=False)
        print(f"✅ catalog.json збережено: {len(CATALOG)} позицій")
    else:
        print("❌ Не знайдено жодного xlsx файлу!")
        CATALOG = []
else:
    with open(CATALOG_PATH, encoding="utf-8") as f:
        CATALOG = json.load(f)
    print(f"✅ Каталог завантажено: {len(CATALOG)} позицій")

def tokenize(text: str) -> set:
    return set(re.findall(r'[а-яёіїєґa-z0-9]+', text.lower()))

print("🔨 Індексую токени...")
for item in CATALOG:
    item['_tokens'] = tokenize(item['name'])
print("✅ Індексація завершена")

# ═══════════════════════════════════════════════════════════════════════════════
# ПРАВИЛА КОРИСТУВАЧА
# ═══════════════════════════════════════════════════════════════════════════════
def get_rules() -> str:
    if not os.path.exists(RULES_FILE):
        return ""
    with open(RULES_FILE, encoding="utf-8") as f:
        return f.read().strip()

def add_rule(new_rule: str):
    with open(RULES_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {new_rule}\n")

# ═══════════════════════════════════════════════════════════════════════════════
# КЕШ НОРМАЛІЗАЦІЙ
# Зберігає пари: "що написав майстер" → "назва товару в каталозі"
# Росте з кожним замовленням. Перевірка займає мілісекунди, нічого не коштує.
# ═══════════════════════════════════════════════════════════════════════════════
CACHE_FILE = "normalization_cache.json"
_CACHE: dict = {}  # original_key → {normalized, catalog_name, category, confidence}

def _load_cache():
    global _CACHE
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                _CACHE = json.load(f)
            print(f"📋 Кеш нормалізацій завантажено: {len(_CACHE)} записів")
        except Exception:
            _CACHE = {}

def _save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не вдалося зберегти кеш: {e}")

def _cache_key(original: str, brand_map: dict) -> str:
    """Ключ кешу = оригінальний текст + виробники (щоб різні підказки давали різні записи)."""
    brands_str = "|".join(f"{k}:{v[0]}" for k, v in sorted(brand_map.items()))
    # Нормалізуємо ключ: нижній регістр, прибираємо зайві пробіли
    key = re.sub(r'\s+', ' ', original.lower().strip())
    return f"{key}::{brands_str}"

def _fuzzy_match(original: str, brand_map: dict, threshold: float = 0.82) -> dict | None:
    """
    Нечіткий пошук в кеші.
    Порівнює токени оригінального тексту з ключами кешу.
    Повертає запис якщо схожість > threshold.
    """
    orig_tokens = tokenize(original)
    if not orig_tokens:
        return None

    best_score = 0.0
    best_entry = None

    for cached_key, entry in _CACHE.items():
        # Беремо тільки текстову частину ключа (до ::)
        cached_orig = cached_key.split("::")[0]
        cached_brands = cached_key.split("::", 1)[1] if "::" in cached_key else ""

        # Виробники мають збігатись
        current_brands = "|".join(f"{k}:{v[0]}" for k, v in sorted(brand_map.items()))
        if cached_brands != current_brands:
            continue

        cached_tokens = tokenize(cached_orig)
        if not cached_tokens:
            continue

        # Jaccard similarity
        intersection = len(orig_tokens & cached_tokens)
        union = len(orig_tokens | cached_tokens)
        score = intersection / union if union > 0 else 0.0

        if score > best_score:
            best_score = score
            best_entry = entry

    if best_score >= threshold:
        return best_entry
    return None

def cache_lookup(original: str, brand_map: dict) -> dict | None:
    """Шукає оригінальний текст в кеші. Повертає запис або None."""
    # Спочатку точний збіг
    key = _cache_key(original, brand_map)
    if key in _CACHE:
        return _CACHE[key]
    # Потім нечіткий
    return _fuzzy_match(original, brand_map)

def cache_save(original: str, brand_map: dict, normalized: str,
               catalog_name: str, category: str, confidence: int):
    """Зберігає успішний збіг в кеш. Тільки якщо confidence >= 85."""
    if confidence < 85:
        return
    key = _cache_key(original, brand_map)
    _CACHE[key] = {
        "normalized":   normalized,
        "catalog_name": catalog_name,
        "category":     category,
        "confidence":   confidence,
    }
    _save_cache()

def cache_delete(original: str, brand_map: dict):
    """Видаляє запис з кешу (якщо менеджер сказав що результат неправильний)."""
    key = _cache_key(original, brand_map)
    if key in _CACHE:
        del _CACHE[key]
        _save_cache()
        return True
    return False

# Завантажуємо кеш при старті
_load_cache()

# ═══════════════════════════════════════════════════════════════════════════════
# КРОК 1: БАЗА ЗНАНЬ САНТЕХНІКИ
# ═══════════════════════════════════════════════════════════════════════════════
ЗНАННЯ_САНТЕХНІКИ = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ВИРОБНИКИ — СПЕЦІАЛІЗАЦІЯ І ПРАВИЛЬНЕ НАПИСАННЯ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  RAFTEC    — крани, перехідники, тепла підлога, запірна арматура (все часто)
  ASG       — каналізація (основний!), PPR пайка
  Ekoplastik — PPR пайка (основний!)
  PLM       — утеплювач (основний!), крани, тепла підлога
  Ostendorf — каналізація (другий після ASG)
  Hidros    — радіатори  ← ПРАВИЛЬНО "Hidros" (не "Hydros"!)
  IDMAR     — радіатори преміум
  Biasi     — газові котли, радіатори
  Tatra-line — електричні котли, насоси, гідроакумулятори
  Termojet  — насоси циркуляційні
  Ecosofy   — фільтри, колби очистки води
  ECO       — ТІЛЬКИ хомути для труб, більше нічого
  Rehau     — PUSH системи
  Venta     — змішувачі, чистова сантехніка
  HERZ      — крани, арматура
  UNIPAK    — льон, паста-ущільнювач
  LEXLINE   — перехідники латунь
  Giacomini — бінокль (вузол підключення радіатора)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
КРАНИ — ТИПИ І КОНТЕКСТ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  "Кран кутовий 3/4" — два різних товари залежно від контексту:

  1. ПРИЛАДОВИЙ (чистовий монтаж, під батарею/змішувач/умивальник):
     "Кран прил. кут. 1/2\"х 3/4\", VKE80102, з керам. зап. ел., Relo Silver, RAFTEC"
     Ознаки: поруч з радіатором, змішувачем, "чистовий", "приладовий", "підводка"

  2. З НАКИДНОЮ ГАЙКОЮ (під котел, бойлер, обв'язка):
     "Кран кут. кульовий з накид. гайкою ЗВ, DN20, 3/4\", PN50, RAFTEC GOLD"
     Ознаки: котел, бойлер, "накидна", "під котел", обв'язка

  Якщо контекст не зрозумілий — нормалізуй як кран кутовий кульовий 3/4" RAFTEC.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
КАНАЛІЗАЦІЯ — РЕАЛЬНІ НАЗВИ З КАТАЛОГУ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚠️  90° у запиті → завжди шукати 87,5° в каталозі!
  умивальник = ф40 | ванна/душ = ф50 | унітаз/стояк = ф110

  ASG сіра (HTR):
    Труба:    "Труба внут. канал. ф110 х 2,7 мм, L = 1 м., сіра, HTR, ASG"
    Коліно:   "Коліно внут. канал. ф110 х 87,5°, сіре, HTR, ASG"
    Трійник:  "Трійник вн. канал. ф110 х 50 х 87,5°, сірий, HTR, ASG"
    ⚠️ Товщина стінки: ф50=1,8мм, ф110=2,7мм (НЕ 2,2мм для HTR!)

  OSTENDORF сіра (HT Safe):
    Труба:    "Труба вн. канал. ф110 x 2,7 мм, L = 1,0 м, сіра, HT Safe, OSTENDORF"
    Коліно:   "Коліно вн. канал. ф110 х 87,5°, сіре, HT Safe, OSTENDORF"
    Трійник:  "Трійник вн. канал. ф110 х 50 х 87,5°, сірий, HT Safe, OSTENDORF"
    ⚠️ Формат: "вн. канал." (не "внут. канал."), L = 1,0 м (не 1 м.)

  ASG біла безшумна (S-LINE):
    "Труба вн. канал. безшум. ф110 х 5,3 мм, L = 1 м, біла, S-LINE, ASG"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PPR ТРУБИ — ЛОГІКА ВИБОРУ ТИПУ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Є три типи PPR труби:
  1. PN20 (звичайна, без армування) — дешевша, тільки холодна вода
  2. Fiber / Fiber Glass (армована скловолокном або базальтом) — гаряча вода + опалення ✅
  3. Composite (армована алюмінієм) — рідко, тільки великі тендерні проекти

  ПРАВИЛО ПІДБОРУ (якщо тип не вказаний явно):
  ┌─────────────────────────────────────────────────────┐
  │ Опалення або гаряча вода → ЗАВЖДИ Fiber (PN25)      │
  │ Тільки холодна вода → PN20                          │
  │ Не зрозуміло → вибирати Fiber (PN25), бо безпечніше│
  │ Composite → ТІЛЬКИ якщо клієнт явно просить        │
  └─────────────────────────────────────────────────────┘

  ⚠️ Fiber дорожча за PN20, але клієнту важко пояснити чому рахунок вийшов
     дорожче якщо спочатку рахувалось по PN20. Тому якщо невідомо призначення —
     беремо Fiber і не помилимось (ліпше переплатити за якість ніж помилитись).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PPR ТРУБИ — СЕРІЇ ПО ВИРОБНИКАХ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ASG:
    PN20  → "Труба PPR Faser HOT ф 20х2,8 мм, PN20, PP-RCT, ASG"
    Fiber → "Труба PPR Nano Ag Composite, ф 25x4,2 мм, PP-RCT, PN25, ASG"

  RAFTEC (НЕ Faser HOT! У RAFTEC інші серії):
    PN20  → "Труба PPR PN20 ф 20x3,4 мм, RAFTEC"
    Fiber → "Труба PPR Fiber Glass ф 25х4,2 мм, PN25, RAFTEC"  ← за замовчуванням
    Composite (алюм., рідко) → "Труба PPR Composite ф 20x3,4 мм, PN25, RAFTEC"
    ⚠️ Розміри RAFTEC: ф20=3,4мм | ф25=4,2мм | ф32=5,4мм
    Якщо просто "ф25 RAFTEC" — Fiber Glass ф 25х4,2 мм, PN25

  Ekoplastik (Wavin):
    PN20  → "Труба PPR ф 20х1,9 мм, PN10, PP-R, Ekoplastik"
    Fiber → "Труба STABI PPR ф 20х3,4 мм, PN25, Ekoplastik"

  ⚠️ НЕ пиши "Faser HOT" якщо виробник RAFTEC!
     НЕ пиши Composite якщо не просять явно!

PPR ФІТИНГИ — формат по виробниках:
  ASG:    "Коліно PPR 90° ф 25, PP-RCT, ASG"
  RAFTEC: "Коліно PPR 90° ф 25, RAFTEC"         (без PP-RCT)
  Ekoplastik: "Коліно PPR 90°, ф 25, PP-RCT, Ekoplastik"

  Трійник рівний RAFTEC: "Трійник рівний PPR ф 25, RAFTEC"   (слово "рівний" є в каталозі!)
  Муфта різьбова RAFTEC: "Муфта PPR МРВ ф 25х3/4\", RAFTEC"  (скорочення МРВ/МРЗ є в каталозі)
  КРЗ/МРЗ кутовий = Коліно PPR РН (зовнішня різьба)
  КРВ/МРВ кутовий = Коліно PPR РВ (внутрішня різьба)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ТЕПЛА ПІДЛОГА — РЕАЛЬНІ НАЗВИ RAFTEC:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Труба PEX:      "Труба PEX-A, ф 16x2,0 мм, RED, EVOH, Spain, RAFTEC"  (RED = гаряча)
                  "Труба PEX-B-AL-PEX-B, ф 16х2,0 мм, RAFTEC"           (металопластик)
  Євроконус:      "Євроконус 3/4\" х ф 16х2,0 мм, хром, RAFTEC"
  RTL клапан:     "003L1081 Комплект RTL DN 15, угловой Danfoss"  ← RTL це Danfoss!
                  (або шукати "Клапан обратного потока RTL")
  Колектор:       "Колектор 3-х контр., 1\"х3/4\", витратоміри, єврокон., RAFTEC GOLD"
  Кінцевий елем.: "Кінцевий елемент 1\", латунь, BRASS, RAFTEC"
  Скоба якірна:   "Скоба якірна, 42 мм, під такер, PLM"
  Плівка розмітка:"Плівка з розміткою 105мкм"
  Демпферна:      "Демпферна стрічка шир.16,5 см, товщ.8 мм, EXTRA"
  ⚠️ Євроконус і труба PEX шукати в категорії metal_plastic або adapters_reducers,
     НЕ в underfloor_heating (там тільки терморегулятори)!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ФІЛЬТРИ — РІЗНИЦЯ МІЖ ТИПАМИ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  "Косий фільтр" / "фільтр У" / "фільтр грубої очистки" =
    "Фільтр груб. очистки, DN20, 3/4\", BLACK, RAFTEC"  ← простий сітчастий
    НЕ самопромивний! НЕ з редуктором!

  "Самопромивний фільтр" = окремий дорогий товар з промивкою
  "Фільтр + редуктор тиску" = Самопромивний фільтр з редуктором RCFR02-C RAFTEC
  "Магніт. фільтр" / "магнітний" = Фільтр магнітний RAFTEC BLACK

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
УТЕПЛЮВАЧ — КОЛІР:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  "+ізол" або просто "ізол" без уточнення кольору →
    шукати І синій (холодна) І червоний (гаряча):
    "Утеплювач ламін. для труб ф 22х6 мм, синій, PLM"
    "Утеплювач ламін. для труб ф 22х6 мм, червоний, PLM"

ФІТИНГИ PPR — АБРЕВІАТУРИ (з'єднання з латунною різьбою):
  МРЗ / МРН = Муфта різьбова зовнішня  (МРН — з рос. "наружная")
  МРВ       = Муфта різьбова внутрішня
  КРЗ / КРН = Коліно різьбове зовнішнє → "Коліно PPR РН ф 25 мм, 3/4\", RAFTEC" (або KAN)
  КРВ       = Коліно різьбове внутрішнє → "Коліно PPR РВ ф 25 мм, 3/4\", RAFTEC" (або KAN)
  РЗ        = з зовнішньою різьбою
  РВ        = з внутрішньою різьбою (настінне)
  Параметри: діаметр труби * діаметр різьби → напр. 25*3/4

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ВИЗНАЧЕННЯ ТИПУ ФІТИНГУ ЗА ПАРАМЕТРАМИ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  2 параметри (напр. 25*3/4)    → муфта або коліно з переходом на різьбу
  3 параметри (напр. 20*16*20)  → трійник
  діаметр 16                    → PUSH-система (рідше — металопластик)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
МЕТАЛОПЛАСТИК (RAFTEC):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Розміри: 16x2, 20x2, 26x3 мм — прес або компресійні фітинги
  "Перехідник" (напр. 25*3/4) — також може називатись МРЗ/МРВ/КРЗ/КРВ

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
СЛЕНГ → ОФІЦІЙНА НАЗВА:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ФІТИНГИ І З'ЄДНАННЯ:
  кол / кут          = Коліно
  трій / рожон       = Трійник
  муф                = Муфта
  пер / перехід      = Перехідник-редукція
  амер / американка  = Американка (рознімне з'єднання)
  батерфляй          = Різновид механізму засувки
  футорка            = Фітинг з зовн. та внутр. різьбою
  розтруб            = Розширена частина труби для з'єднання
  бутилка            = Фітинг-редукція у формі пляшки
  ревізія            = Трійник зі знімною кришкою (прочистка каналізації)
  фанова труба       = З'єднання стояка каналізації з атмосферою
  патрубок           = Коротка з'єднувальна частина труби

  КРАНИ ТА АРМАТУРА:
  кр / шар           = Кран кульовий
  кран "Пиво"        = Поливальний кран
  косий / "У"        = Фільтр грубої очистки
  хлопушка           = Зворотний клапан пелюстковий
  зворотній клапан   = Клапан зворотного ходу
  під термо          = Кран кутовий під термоголовку
  бінокль            = Вузол нижнього підключення радіатора
  елька              = Трубка для декоративного підключення радіатора
  вухастий           = Настінне коліно
  єврокон            = Євроконус для теплої підлоги
  байпас             = Обвідна лінія в системі опалення

  КОТЛИ ТА НАСОСИ:
  бойлер             = Електричний водонагрівач
  запальничка        = Побутовий газовий котел
  АОГВ               = Апарат опалювальний газовий водонагрівний
  АКГВ               = Апарат комбінований газовий водонагрівний
  "мізки" котла      = Плата/панель керування котлом або бойлером
  циркуль            = Насос циркуляційний
  барно              = Блок насоса (корпус електродвигуна + статор)
  сололіфт           = Каналізаційна насосна станція
  РМ п'яті           = Механічне реле тиску
  KVS                = Коефіцієнт пропускної здатності
  ТЕН                = Трубчастий електронагрівник
  демферна           = Демпферна стрічка теплої підлоги
  шамот              = Вогнетривка глина (твердопаливні котли)

  МАТЕРІАЛИ ТА УЩІЛЬНЕННЯ:
  пакля / льон       = Льон сантехнічний UNIPAK
  мультипак / паста  = Паста для різьб UNIPAK
  мастило / вазелін  = Технічний вазелін вн. канал. Valrom 150 гр.
  пакувати           = використовувати ущільнювач для різьбового з'єднання
  PEX / пекс         = Зшитий поліпропілен

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
УТЕПЛЮВАЧ PLM — ВІДПОВІДНІСТЬ ДІАМЕТРІВ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Труба ф16 → утеплювач ф 18х6 мм
  Труба ф20 → утеплювач ф 22х6 мм  (НЕ ф20! такого немає)
  Труба ф25 → утеплювач ф 28х6 мм  (НЕ ф25! такого немає)
  Труба ф32 → утеплювач ф 35х6 мм
  Труба ф40 → утеплювач ф 42х6 мм
  Синій = холодна вода, Червоний = гаряча вода
  Формат: "Утеплювач ламін. для труб ф 22х6 мм, синій, PLM"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ФОРМАТ НОРМАЛІЗОВАНИХ НАЗВ (з реального каталогу):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PPR ASG:
    Труба ф20 PN20   → "Труба PPR Faser HOT ф 20х2,8 мм, PN20, PP-RCT, ASG"
    Коліно 25 90°    → "Коліно PPR 90° ф 25, PP-RCT, ASG"
    Трійник 25       → "Трійник рівний PPR ф 25, PP-RCT, ASG"
    Муфта 25         → "Муфта PPR ф 25, PP-RCT, ASG"
    МРЗ 25*3/4       → "Муфта PPR МРЗ ф 25х3/4\", PP-RCT, ASG"
    МРВ 25*3/4       → "Муфта PPR МРВ ф 25х3/4\", PP-RCT, ASG"

  PPR RAFTEC (НЕ "Faser HOT"! У RAFTEC інша назва серії):
    Труба ф20 PN20   → "Труба PPR PN20 ф 20x3,4 мм, RAFTEC"
    Труба ф20 PN25   → "Труба PPR Composite ф 20x3,4 мм, PN25, RAFTEC"
    Коліно 25 90°    → "Коліно PPR 90° ф 25, RAFTEC"
    Трійник 25       → "Трійник рівний PPR ф 25, RAFTEC"
    Муфта 25         → "Муфта PPR ф 25, RAFTEC"
    МРВ 25*3/4       → "Муфта PPR МРВ ф 25х3/4\", RAFTEC"

  Каналізація ASG (сіра, HTR):
    Труба ф110 1м    → "Труба внут. канал. ф110 х 2,7 мм, L = 1 м., сіра, HTR, ASG"
    Труба ф50 1м     → "Труба внут. канал. ф 50 х 1,8 мм, L = 1 м., сіра, HTR, ASG"
    Коліно 110 90°   → "Коліно внут. канал. ф110 х 87,5°, сіре, HTR, ASG"
    Коліно 50 45°    → "Коліно внут. канал. ф 50 х 45°, сіре, HTR, ASG"
    Трійник 110х50   → "Трійник вн. канал. ф110 х 50 х 87,5°, сірий, HTR, ASG"

  Каналізація OSTENDORF (сіра, HT Safe):
    Труба ф110 1м    → "Труба вн. канал. ф110 x 2,7 мм, L = 1,0 м, сіра, HT Safe, OSTENDORF"
    Коліно 110 90°   → "Коліно вн. канал. ф110 х 87,5°, сіре, HT Safe, OSTENDORF"
    Трійник 110х50   → "Трійник вн. канал. ф110 х 50 х 87,5°, сірий, HT Safe, OSTENDORF"

  Утеплювач:  "Утеплювач ламін. для труб ф 28х6 мм, синій, PLM"
  Вазелін:    "Технічний вазелин вн. канал. 150 гр., Valrom"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ПРІОРИТЕТИ ПРИ ПОШУКУ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Діаметр         — ОБОВ'ЯЗКОВО повинен збігатися
  2. Кут / довжина   — якщо вказано, повинні збігатися
  3. Виробник        — якщо вказано в запиті, пріоритет йому
  Числа мають подвійну вагу над словами (діаметр, кут, довжина)
"""

# ═══════════════════════════════════════════════════════════════════════════════
# ПАРСИНГ ПІДКАЗКИ МЕНЕДЖЕРА → КАРТА "група товару → виробник"
# ═══════════════════════════════════════════════════════════════════════════════
# Відомі синоніми груп товарів (що може написати менеджер)
CATEGORY_ALIASES = {
    # PPR пластик / пайка
    'пластик':    'plastic_ppr',
    'ппр':        'plastic_ppr',
    'ppr':        'plastic_ppr',
    'труби':      'plastic_ppr',
    'труба':      'plastic_ppr',
    'пайка':      'plastic_ppr',   # ← майстри часто кажуть "пайка" маючи на увазі PPR
    'паяні':      'plastic_ppr',
    'полікор':    'plastic_ppr',
    # Push системи
    'пуш':        'push_systems',
    'push':       'push_systems',
    'прес':       'push_systems',  # прес-фітинги
    # Металопластик
    'металопластик': 'metal_plastic',
    'мп':            'metal_plastic',
    'метал':         'metal_plastic',
    # Крани / запірна арматура
    'крани':      'shutoff_valves',
    'кран':       'shutoff_valves',
    'арматура':   'shutoff_valves',
    'запірна':    'shutoff_valves',
    'вентил':     'shutoff_valves',
    # Фільтри
    'фільтри':    'filtration',
    'фільтр':     'filtration',
    'очистка':    'filtration',
    # Радіатори
    'радіатори':  'radiators_radiatorsvalve',
    'радіатор':   'radiators_radiatorsvalve',
    'батареї':    'radiators_radiatorsvalve',
    'батарея':    'radiators_radiatorsvalve',
    # Насоси
    'насоси':     'pumps',
    'насос':      'pumps',
    # Котли
    'котли':      'boilers',
    'котел':      'boilers',
    # Каналізація
    'каналізація': 'sewage',
    'каналіз':     'sewage',
    'каналіза':    'sewage',
    'сірі труби':  'sewage',
    # Водонагрівачі
    'бойлери':       'water_heaters',
    'бойлер':        'water_heaters',
    'водонагрівач':  'water_heaters',
    'водонагрів':    'water_heaters',
    # Тепла підлога
    'тепла підлога': 'underfloor_heating',
    'підлога':       'underfloor_heating',
    'тп':            'underfloor_heating',
    # Євроконус і PEX труба — в металопластику, не в underfloor!
    'євроконус':     'metal_plastic',
    'eurokonus':     'metal_plastic',
    'pex':           'metal_plastic',
    'пекс':          'metal_plastic',
    # RTL — в опаленні (Danfoss)
    'rtl':           'heating',
    # Колектор теплої підлоги — в металопластику
    'колектор тп':   'metal_plastic',
    'колектор підлоги': 'metal_plastic',
    # Опалення / радіаторна арматура
    'опалення':   'heating',
    'байпас':     'heating',
}

# Відомі виробники — токени для фільтру і підстановки в normalized
# Ключ = що може написати менеджер, значення = токени пошуку в каталозі
BRAND_TOKENS = {
    # RAFTEC
    'raftec':       ['raftec', 'рафтек', 'RAFTEC'],
    'рафтек':       ['raftec', 'рафтек', 'RAFTEC'],
    # ASG
    'asg':          ['asg', 'ASG'],
    'асг':          ['asg', 'ASG'],
    # Ekoplastik / Wavin
    'ekoplastik':   ['ekoplastik', 'Ekoplastik'],
    'екопластик':   ['ekoplastik', 'Ekoplastik'],
    'wavin':        ['ekoplastik', 'Ekoplastik', 'wavin'],
    # PLM
    'plm':          ['plm', 'PLM'],
    'плм':          ['plm', 'PLM'],
    # Ostendorf
    'ostendorf':    ['ostendorf', 'OSTENDORF'],
    'остендорф':    ['ostendorf', 'OSTENDORF'],
    # Hidros (правильно через "i", не "y")
    'hidros':       ['hidros', 'Hidros', 'гідрос'],
    'гідрос':       ['hidros', 'Hidros', 'гідрос'],
    'hydros':       ['hidros', 'Hidros', 'гідрос'],  # виправляємо помилку написання
    # IDMAR
    'idmar':        ['idmar', 'IDMAR'],
    'ідмар':        ['idmar', 'IDMAR'],
    # Biasi
    'biasi':        ['biasi', 'BIASI'],
    'біасі':        ['biasi', 'BIASI'],
    # Tatra-line
    'tatra':        ['tatra', 'Tatra'],
    'татра':        ['tatra', 'Tatra'],
    'tatra-line':   ['tatra', 'Tatra'],
    # Termojet
    'termojet':     ['termojet', 'Termojet'],
    'термоджет':    ['termojet', 'Termojet'],
    # Ecosofy
    'ecosofy':      ['ecosofy', 'Ecosofy'],
    'екософі':      ['ecosofy', 'Ecosofy'],
    # ECO (тільки хомути!)
    'eco':          ['eco', 'ECO'],
    # Venta
    'venta':        ['venta', 'Venta'],
    'вента':        ['venta', 'Venta'],
    # Rehau
    'rehau':        ['rehau', 'REHAU'],
    'рехау':        ['rehau', 'REHAU'],
    # HERZ
    'herz':         ['herz', 'HERZ'],
    'херц':         ['herz', 'HERZ'],
    # Giacomini
    'giacomini':    ['giacomini', 'Giacomini'],
    'джакоміні':    ['giacomini', 'Giacomini'],
    # Danfoss
    'danfoss':      ['danfoss', 'Danfoss'],
    'данфос':       ['danfoss', 'Danfoss'],
    # Інші
    'valrom':       ['valrom', 'Valrom'],
    'unipak':       ['unipak', 'UNIPAK'],
    'kan':          ['kan', 'KAN'],
    'fado':         ['fado', 'FADO'],
    'lexline':      ['lexline', 'LEXLINE'],
    'purmo':        ['purmo', 'PURMO'],
    'пурмо':        ['purmo', 'PURMO'],
    'raftec gold':  ['raftec gold', 'RAFTEC GOLD'],
}

# ═══════════════════════════════════════════════════════════════════════════════
# ПРІОРИТЕТИ ВИРОБНИКІВ ЗА ЗАМОВЧУВАННЯМ
# Використовуються коли менеджер НЕ вказав конкретного виробника.
# Порядок = пріоритет (перший = найвищий).
# A = дуже часто, B = часто, C = рідко
# ═══════════════════════════════════════════════════════════════════════════════
DEFAULT_BRAND_PRIORITY = {
    # Каналізація: ASG першочерговий, потім Ostendorf
    'sewage': [
        ['asg', 'ASG'],          # A — основний
        ['ostendorf'],           # B — другий
        ['plm', 'PLM'],          # C — рідко
        ['valrom'],              # C
    ],
    # PPR / Пайка: Ekoplastik першочерговий, ASG другий, RAFTEC рідко
    'plastic_ppr': [
        ['ekoplastik'],          # A
        ['asg', 'ASG'],          # B
        ['raftec'],              # C
    ],
    # Крани / Запірна арматура: RAFTEC першочерговий
    'shutoff_valves': [
        ['raftec'],              # A
        ['plm', 'PLM'],          # B
        ['herz', 'HERZ'],        # B
        ['asg', 'ASG'],          # C
    ],
    # Арматура безпеки: RAFTEC
    'safety_valves': [
        ['raftec'],              # A
        ['asg', 'ASG'],          # B
    ],
    # Перехідники: RAFTEC
    'adapters_reducers': [
        ['raftec'],              # A
        ['lexline', 'LEXLINE'],  # B
        ['asg', 'ASG'],          # C
    ],
    # Тепла підлога: RAFTEC
    'underfloor_heating': [
        ['raftec'],              # A
        ['plm', 'PLM'],          # B
    ],
    # PUSH системи: RAFTEC, потім Rehau
    'push_systems': [
        ['raftec'],              # B
        ['rehau', 'REHAU'],      # C
    ],
    # Металопластик: RAFTEC
    'metal_plastic': [
        ['raftec'],              # A
    ],
    # Насоси: Tatra-line, Termojet
    'pumps': [
        ['tatra'],               # A
        ['termojet'],            # A
        ['raftec'],              # B
    ],
    # Котли: Biasi (газові), Tatra-line (електричні)
    'boilers': [
        ['biasi', 'BIASI'],      # A газові
        ['tatra'],               # A електричні
    ],
    # Радіатори: Hidros, потім IDMAR
    'radiators_radiatorsvalve': [
        ['hidros', 'Hidros'],    # A
        ['idmar', 'IDMAR'],      # A дорожчі
        ['biasi', 'BIASI'],      # B
    ],
    # Фільтри: Ecosofy, RAFTEC
    'filtration': [
        ['ecosofy'],             # A
        ['raftec'],              # B
        ['plm', 'PLM'],          # B
    ],
    # Опалення (арматура): RAFTEC, PLM
    'heating': [
        ['raftec'],              # A
        ['plm', 'PLM'],          # B
        ['herz', 'HERZ'],        # B
    ],
    # Утеплювач: PLM
    'insulation': [
        ['plm', 'PLM'],          # A
    ],
    # Кріплення: ECO (тільки хомути!), RAFTEC
    'fasteners_sealants': [
        ['eco', 'ECO'],          # A хомути
        ['raftec'],              # B решта
        ['unipak', 'UNIPAK'],    # A ущільнювачі
    ],
}

def parse_caption_brands(caption: str) -> dict:
    """
    Парсить підказку менеджера і повертає словник:
    { category_key: [brand_tokens] }

    Приклад підказки: "пластик - екопластик\nкрани - рафтек"
    Результат: { 'plastic_ppr': ['ekoplastik','екопластик','wavin'],
                 'shutoff_valves': ['raftec','рафтек'] }
    """
    brand_map = {}
    if not caption:
        return brand_map

    # Розбиваємо по рядках і крапках з комою
    lines = re.split(r'[\n;,]+', caption.lower())
    for line in lines:
        # Шукаємо розділювач: "-", "=", ":"
        parts = re.split(r'[-=:]', line, maxsplit=1)
        if len(parts) != 2:
            continue
        group_raw = parts[0].strip()
        brand_raw = parts[1].strip()

        # Знаходимо категорію
        category = None
        for alias, cat_key in CATEGORY_ALIASES.items():
            if alias in group_raw:
                category = cat_key
                break

        # Знаходимо токени виробника
        tokens = None
        for brand_key, brand_toks in BRAND_TOKENS.items():
            if brand_key in brand_raw:
                tokens = brand_toks
                break

        if category and tokens:
            brand_map[category] = tokens

    return brand_map


def filter_by_brand(candidates: list[dict], brand_tokens: list[str]) -> tuple[list[dict], bool]:
    """
    Фільтрує кандидатів по виробнику.
    Повертає (відфільтрований_список, знайшов_виробника).
    Якщо виробника немає — повертає всіх кандидатів з brand_found=False.
    """
    filtered = [
        c for c in candidates
        if any(tok.lower() in c['name'].lower() for tok in brand_tokens)
    ]
    if filtered:
        return filtered, True
    # Виробника немає в каталозі — повертаємо всіх (fallback)
    return candidates, False


# ═══════════════════════════════════════════════════════════════════════════════
# КРОК 1: OCR + НОРМАЛІЗАЦІЯ (Gemini 2.5 Flash)
# ═══════════════════════════════════════════════════════════════════════════════
def normalize_photo(image_b64: str, caption: str = "") -> list[dict]:
    """OCR + нормалізація фото через Gemini 2.5 Flash. Повертає також category і brand_map."""
    rules = get_rules()
    rules_block = f"\nДодаткові правила від менеджера:\n{rules}" if rules else ""

    # Парсимо підказку менеджера → карту виробників
    brand_map = parse_caption_brands(caption)

    # Будуємо чіткий список правил виробника для промпту
    brand_hint = ""
    if brand_map:
        lines = []
        for cat, toks in brand_map.items():
            brand_display = toks[0]  # перший токен — це читабельна назва
            lines.append(f"  {cat} → {brand_display}")
        brand_hint = f"""

╔══════════════════════════════════════════════╗
║  ВИРОБНИКИ ВІД МЕНЕДЖЕРА — СУВОРО ОБОВ'ЯЗКОВО ║
║  Підстав у normalized ЗАМІСТЬ дефолтного ASG  ║
╚══════════════════════════════════════════════╝
{chr(10).join(lines)}

ПРИКЛАД: якщо plastic_ppr → raftec, то:
  "Коліно 25 90°" → "Коліно PPR 90° ф 25, PP-RCT, RAFTEC"  (НЕ ASG!)
  "Труба ф20 PN20" → "Труба PPR Faser HOT ф 20х2,8 мм, PN20, PP-RCT, RAFTEC"  (НЕ ASG!)"""

    prompt = f"""Ти — експерт із сантехніки України. На фото рукописний список замовлення від майстра.

ПІДКАЗКА МЕНЕДЖЕРА: {caption}{brand_hint}{rules_block}

БАЗА ЗНАНЬ:
{ЗНАННЯ_САНТЕХНІКИ}

ЗАВДАННЯ:
1. Прочитай кожен рядок (тільки сантехніка/опалення/водопостачання)
2. Нормалізуй до назви як в прайсі
   - Якщо менеджер вказав виробника для цієї категорії — ОБОВ'ЯЗКОВО використай його
   - Якщо виробника НЕ вказано — не додавай дефолтного, просто пиши назву
3. Визнач category: plastic_ppr / push_systems / metal_plastic / shutoff_valves / sewage / pumps / boilers / water_heaters / filtration / radiators_radiatorsvalve / underfloor_heating / other
4. Витягни кількість (число + одиниця: шт, м, м.п., пак)
5. Якщо рядок нечитабельний або не сантехніка — пропусти

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом:
[
  {{"original": "що написано на фото", "normalized": "нормалізована назва для пошуку", "qty": "кількість", "category": "категорія"}}
]"""

    image_bytes = base64.b64decode(image_b64)
    resp = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            genai_types.Part.from_text(text=prompt)
        ]
    )
    raw = resp.text.strip().replace('```json', '').replace('```', '').strip()
    try:
        if '[' in raw and ']' in raw:
            raw = raw[raw.index('['):raw.rindex(']')+1]
        items = json.loads(raw)
        # Прикріплюємо brand_map до кожної позиції для подальшого фільтру
        for item in items:
            item['_brand_map'] = brand_map
        return items
    except Exception:
        return []


def normalize_text(text: str, caption: str = "") -> list[dict]:
    """Нормалізація текстового запиту через Claude."""
    rules = get_rules()
    rules_block = f"\nДодаткові правила:\n{rules}" if rules else ""

    brand_map = parse_caption_brands(caption)
    brand_hint = ""
    if brand_map:
        lines = [f"  {cat} → {toks[0]}" for cat, toks in brand_map.items()]
        brand_hint = f"""

╔══════════════════════════════════════════════╗
║  ВИРОБНИКИ ВІД МЕНЕДЖЕРА — СУВОРО ОБОВ'ЯЗКОВО ║
╚══════════════════════════════════════════════╝
{chr(10).join(lines)}
Підстав у normalized ЗАМІСТЬ дефолтного ASG."""

    prompt = f"""Ти — експерт із сантехніки України. Нормалізуй список товарів.{brand_hint}{rules_block}

БАЗА ЗНАНЬ:
{ЗНАННЯ_САНТЕХНІКИ}

ТЕКСТ:
{text}

Нормалізуй кожну позицію до назви як в прайсі.
- Якщо менеджер вказав виробника для категорії — використай його, НЕ ASG за замовчуванням
- Якщо виробника не вказано — не додавай нічого
Визнач category: plastic_ppr / push_systems / metal_plastic / shutoff_valves / sewage / pumps / boilers / water_heaters / filtration / radiators_radiatorsvalve / underfloor_heating / other
Витягни кількість.

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом:
[{{"original": "...", "normalized": "...", "qty": "...", "category": "..."}}]"""

    resp = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
    try:
        if '[' in raw and ']' in raw:
            raw = raw[raw.index('['):raw.rindex(']')+1]
        items = json.loads(raw)
        for item in items:
            item['_brand_map'] = brand_map
        return items
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════════
# КРОК 2: KEYWORD ПОШУК ПО КАТАЛОГУ
# ═══════════════════════════════════════════════════════════════════════════════
def keyword_search(query: str, top_n: int = 8) -> list[dict]:
    q_tokens = tokenize(query)
    q_numbers = set(re.findall(r'\d+', query.lower()))
    q_words   = q_tokens - q_numbers

    scores = []
    for item in CATALOG:
        it = item['_tokens']
        num_score  = len(q_numbers & it) * 2
        word_score = len(q_words & it)
        total = num_score + word_score
        if total > 0:
            precision = total / max(len(q_tokens), 1)
            scores.append((total + precision, item))

    scores.sort(key=lambda x: -x[0])
    return [item for _, item in scores[:top_n]]


# ═══════════════════════════════════════════════════════════════════════════════
# КРОК 3: ФІНАЛЬНИЙ ВИБІР (Claude Sonnet)
# ═══════════════════════════════════════════════════════════════════════════════
CLAUDE_BATCH_SIZE = 8  # менший батч = надійніший JSON

def claude_pick_one_batch(позиції: list[dict]) -> list[dict]:
    """Один запит Claude на невеликий батч позицій."""
    запити = []
    for i, пос in enumerate(позиції):
        brand_note = f"\n   ⚠️ ВИРОБНИК: тільки {пос['required_brand']}" if пос.get('required_brand') else ""
        кандидати = "\n".join(
            f"  {j+1}. {c['name']}"
            for j, c in enumerate(пос['candidates'])
        )
        запити.append(f"{i+1}. {пос['normalized']}{brand_note}\n{кандидати}")

    prompt = f"""Сантехнік. Для кожного запиту — номер кандидата що підходить найкраще.

{chr(10).join(запити)}

Правила: діаметр обов'язково збігається; якщо є ВИРОБНИК — тільки він.
confidence: 95=точний збіг, 80=майже точний, 60=схожий.
fail_reason: якщо не знайдено — одне речення чому.
reason: якщо знайдено — одне речення чому.

JSON масив рівно {len(позиції)} елементів:
[{{"знайдено":true,"номер_кандидата":1,"confidence":95,"reason":"...","fail_reason":""}},...]"""

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        # Агресивне очищення
        raw = re.sub(r'```\w*', '', raw).strip()
        # Витягуємо JSON масив
        start = raw.find('[')
        end   = raw.rfind(']') + 1
        if start == -1 or end == 0:
            raise ValueError("JSON масив не знайдено")
        parsed = json.loads(raw[start:end])
        # Якщо Claude повернув менше елементів — доповнюємо
        while len(parsed) < len(позиції):
            parsed.append({"знайдено": False, "confidence": 0, "reason": "", "fail_reason": "Claude не повернув відповідь для цієї позиції"})
        return parsed[:len(позиції)]
    except Exception as e:
        return [{"знайдено": False, "confidence": 0, "reason": "", "fail_reason": f"помилка Claude: {e}"}] * len(позиції)


def claude_pick_batch(позиції_з_кандидатами: list[dict]) -> list[dict]:
    """Розбиває на малі батчі і збирає результати."""
    all_results = []
    for i in range(0, len(позиції_з_кандидатами), CLAUDE_BATCH_SIZE):
        chunk = позиції_з_кандидатами[i:i + CLAUDE_BATCH_SIZE]
        results = claude_pick_one_batch(chunk)
        all_results.extend(results)
    return all_results


def find_items(позиції: list[dict], progress_cb=None) -> list[dict]:
    """
    Головна функція пошуку з кешем і діагностикою.
    1. Перевіряємо кеш → якщо є точний збіг, пропускаємо keyword+Claude
    2. keyword_search → топ-12 кандидатів
    3. filter_by_brand → виробник
    4. claude_pick_batch → фінальний вибір
    5. Зберігаємо успішні результати в кеш
    """
    потребують_claude = []
    результати = [None] * len(позиції)

    for i, пос in enumerate(позиції):
        original   = пос.get('original', '')
        normalized = пос.get('normalized', '')
        category   = пос.get('category', 'other')
        brand_map  = пос.get('_brand_map', {})

        if progress_cb:
            progress_cb(i + 1, len(позиції))

        # ── Крок 1: перевіряємо кеш ───────────────────────────────────────────
        cached = cache_lookup(original, brand_map)
        if cached:
            результати[i] = {
                'original':         original,
                'normalized':       cached['normalized'],
                'знайдено':         True,
                'назва':            cached['catalog_name'],
                'ціна':             '',   # ціна може змінитись — заповнимо нижче
                'qty':              пос.get('qty', ''),
                'confidence':       cached['confidence'],
                'reason':           f"З кешу (раніше знайдено з впевненістю {cached['confidence']}%)",
                'fail_reason':      '',
                'candidates_debug': [],
                '_from_cache':      True,
                '_category':        cached['category'],
            }
            continue

        # ── Крок 2: keyword search ─────────────────────────────────────────────
        кандидати = keyword_search(normalized, top_n=12)

        if not кандидати:
            результати[i] = {
                **пос,
                'знайдено':    False,
                'назва':       '',
                'confidence':  0,
                'reason':      '',
                'fail_reason': 'keyword пошук не знайшов жодного кандидата — можливо товар відсутній в каталозі або назва занадто специфічна',
                'candidates_debug': [],
            }
            continue

        required_brand = None
        brand_found_in_catalog = True
        brand_tokens_for_filter = brand_map.get(category)
        кандидати_до_фільтру = кандидати[:]

        if brand_tokens_for_filter:
            кандидати, brand_found_in_catalog = filter_by_brand(кандидати, brand_tokens_for_filter)
            required_brand = brand_tokens_for_filter[0] if brand_found_in_catalog else None
        else:
            priority_list = DEFAULT_BRAND_PRIORITY.get(category, [])
            for priority_tokens in priority_list:
                filtered, found = filter_by_brand(кандидати, priority_tokens)
                if found:
                    кандидати = filtered
                    required_brand = priority_tokens[0]
                    break

        потребують_claude.append({
            'idx':              i,
            'normalized':       normalized,
            'original':         original,
            'candidates':       кандидати,
            'candidates_debug': [c['name'] for c in кандидати_до_фільтру[:5]],
            'qty':              пос.get('qty', ''),
            'required_brand':   required_brand,
            'category':         category,
            'brand_map':        brand_map,
        })

    if потребують_claude:
        відповіді = claude_pick_batch(потребують_claude)
        for j, пос in enumerate(потребують_claude):
            r = відповіді[j] if j < len(відповіді) else {'знайдено': False, 'confidence': 0}
            idx = пос['idx']
            confidence  = int(r.get('confidence', 0))
            reason      = r.get('reason', '')
            fail_reason = r.get('fail_reason', '')

            if r.get('знайдено') and r.get('номер_кандидата'):
                n = int(r['номер_кандидата']) - 1
                n = max(0, min(n, len(пос['candidates'])-1))
                found = пос['candidates'][n]
                результати[idx] = {
                    'original':         пос['original'],
                    'normalized':       пос['normalized'],
                    'знайдено':         True,
                    'назва':            found['name'],
                    'ціна':             found.get('price', ''),
                    'qty':              пос['qty'],
                    'confidence':       confidence,
                    'reason':           reason,
                    'fail_reason':      '',
                    'candidates_debug': пос['candidates_debug'],
                }
                # ── Зберігаємо в кеш якщо впевненість висока ─────────────────
                cache_save(
                    original=пос['original'],
                    brand_map=пос['brand_map'],
                    normalized=пос['normalized'],
                    catalog_name=found['name'],
                    category=пос['category'],
                    confidence=confidence,
                )
            else:
                топ_канд = ', '.join(c['name'][:50] for c in пос['candidates'][:3])
                auto_fail = f"Топ кандидати: [{топ_канд}]"
                результати[idx] = {
                    'original':         пос['original'],
                    'normalized':       пос['normalized'],
                    'знайдено':         False,
                    'назва':            '',
                    'qty':              пос['qty'],
                    'confidence':       confidence,
                    'reason':           '',
                    'fail_reason':      f"{fail_reason} | {auto_fail}" if fail_reason else auto_fail,
                    'candidates_debug': пос['candidates_debug'],
                }

    # ── Заповнюємо ціну для позицій з кешу ────────────────────────────────────
    for r in результати:
        if r and r.get('_from_cache') and r.get('назва'):
            # Шукаємо актуальну ціну в каталозі
            for item in CATALOG:
                if item['name'] == r['назва']:
                    r['ціна'] = item.get('price', '')
                    break
            r.pop('_from_cache', None)
            r.pop('_category', None)

    return результати


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL
# ═══════════════════════════════════════════════════════════════════════════════
def create_excel(результати: list[dict]) -> tuple[BytesIO, list[str], list[str]]:
    знайдено_rows  = []
    не_знайдено_rows = []
    low_conf_rows  = []

    for r in результати:
        if not r:
            continue
        conf = r.get('confidence', 0)
        conf_label = f"{conf}%" if conf else "?"

        if r.get('знайдено'):
            знайдено_rows.append({
                'Наименование': r.get('назва', ''),
                'Кількість':    r.get('qty', ''),
                'Ціна':         r.get('ціна', ''),
                'Впевненість':  conf_label,
                'Чому знайшло': r.get('reason', ''),
                'Оригінал':     r.get('original', ''),
            })
            if conf < 70:
                low_conf_rows.append(f"{r.get('original','')} → {r.get('назва','')} ({conf_label}): {r.get('reason','')}")
        else:
            не_знайдено_rows.append({
                'Оригінал':          r.get('original', ''),
                'Нормалізовано':     r.get('normalized', ''),
                'Причина':           r.get('fail_reason', ''),
                'Топ кандидати':     ', '.join(r.get('candidates_debug', [])),
            })

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Лист 1: Знайдені товари
        df = pd.DataFrame(знайдено_rows) if знайдено_rows else pd.DataFrame(
            columns=['Наименование','Кількість','Ціна','Впевненість','Чому знайшло','Оригінал'])
        df.to_excel(writer, index=False, sheet_name='Замовлення')

        # Лист 2: Не знайдено з діагностикою
        if не_знайдено_rows:
            df2 = pd.DataFrame(не_знайдено_rows)
            df2.to_excel(writer, index=False, sheet_name='Не знайдено (діагностика)')

        # Лист 3: Низька впевненість
        if low_conf_rows:
            df3 = pd.DataFrame({'Перевір вручну': low_conf_rows})
            df3.to_excel(writer, index=False, sheet_name='Перевір')

    output.seek(0)
    not_found_list = [r.get('original','') or r.get('normalized','') for r in результати if r and not r.get('знайдено')]
    return output, not_found_list, low_conf_rows


# ═══════════════════════════════════════════════════════════════════════════════
# БАТЧ-МЕНЕДЖЕР
# ═══════════════════════════════════════════════════════════════════════════════
user_batches  = {}
stop_flags    = {}
pending_hints = {}

def safe_edit(chat_id, msg_id, text):
    """Оновлює повідомлення, ігнорує помилку якщо текст не змінився"""
    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass


def process_batch(chat_id: int):
    batch = user_batches.pop(chat_id, None)
    if not batch:
        return

    stop_flags.pop(chat_id, None)
    items = batch['items']

    # ── Живе повідомлення статусу ──────────────────────────────────────────────
    status = bot.send_message(chat_id,
        f"⏳ Починаю обробку {len(items)} файл(ів)...\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📖 Крок 1/4: Читання фото\n"
        f"🔍 Крок 2/4: Пошук в каталозі\n"
        f"🤖 Крок 3/4: Вибір Claude\n"
        f"📊 Крок 4/4: Формування Excel"
    )
    msg_id = status.message_id

    def update(step: int, detail: str = "", знайдено: int = 0, всього: int = 0):
        icons = ["⏳","⏳","⏳","⏳"]
        icons[step - 1] = "🔄"
        for i in range(step - 1):
            icons[i] = "✅"
        labels = [
            f"{icons[0]} Крок 1/4: Читання фото",
            f"{icons[1]} Крок 2/4: Пошук в каталозі",
            f"{icons[2]} Крок 3/4: Вибір Claude",
            f"{icons[3]} Крок 4/4: Формування Excel",
        ]
        progress = ""
        if всього > 0:
            pct = int(знайдено / всього * 100)
            bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
            progress = f"\n[{bar}] {знайдено}/{всього}"
        text = (
            f"━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(labels)
            + (f"\n\n💬 {detail}" if detail else "")
            + progress
        )
        safe_edit(chat_id, msg_id, text)

    # ── КРОК 1: OCR ────────────────────────────────────────────────────────────
    всі_позиції = []
    errors = []

    for idx, item in enumerate(items, 1):
        if stop_flags.get(chat_id):
            safe_edit(chat_id, msg_id, "🛑 Зупинено.")
            return
        update(1, f"Файл {idx}/{len(items)}...")
        try:
            if item['type'] == 'photo':
                позиції = normalize_photo(item['data'], item.get('caption', ''))
                всі_позиції.extend(позиції)
                update(1, f"Файл {idx}/{len(items)}: розпізнано {len(позиції)} позицій")
            elif item['type'] == 'text':
                позиції = normalize_text(item['text'], item.get('caption', ''))
                всі_позиції.extend(позиції)
        except Exception as e:
            errors.append(f"❌ Файл {idx}: {e}")

    if not всі_позиції:
        safe_edit(chat_id, msg_id,
            "😕 Не вдалося розпізнати жодної позиції.\n" + "\n".join(errors))
        return

    # Прев'ю розпізнаного
    preview = "\n".join(
        f"• {п.get('original','')[:40]} → {п.get('normalized','')[:50]}"
        for п in всі_позиції[:6]
    )
    if len(всі_позиції) > 6:
        preview += f"\n... та ще {len(всі_позиції)-6}"
    bot.send_message(chat_id,
        f"✅ Розпізнано {len(всі_позиції)} позицій:\n\n{preview}")

    # ── КРОК 2+3: Пошук + Claude ───────────────────────────────────────────────
    update(2, f"Шукаю {len(всі_позиції)} позицій...")

    знайдено_count = [0]

    def progress_cb(current, total):
        if current % 5 == 0 or current == total:
            update(2, f"Keyword пошук: {current}/{total}", знайдено_count[0], total)

    try:
        update(2, f"Keyword пошук по {len(всі_позиції)} позиціях...")
        результати = find_items(всі_позиції, progress_cb=progress_cb)
        знайдено_count[0] = sum(1 for r in результати if r and r.get('знайдено'))
        update(3, f"Claude вибрав {знайдено_count[0]}/{len(результати)}",
               знайдено_count[0], len(результати))
    except Exception as e:
        safe_edit(chat_id, msg_id, f"❌ Помилка пошуку: {e}")
        return

    # ── КРОК 4: Excel ──────────────────────────────────────────────────────────
    update(4, "Формую Excel з діагностикою...")
    excel, not_found, low_confidence = create_excel(результати)

    знайдено = [r for r in результати if r and r.get('знайдено')]
    bot.send_document(chat_id, excel, visible_file_name="замовлення.xlsx")

    # Фінальний звіт
    звіт = (
        f"✅ Готово!\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Знайдено: {len(знайдено)}/{len(результати)}\n"
    )
    if not_found:
        звіт += f"⚠️ Не знайдено: {len(not_found)} шт.\n"
        звіт += "\n".join(f"  • {n[:50]}" for n in not_found[:4])
        if len(not_found) > 4:
            звіт += f"\n  ... та ще {len(not_found)-4}"
        звіт += "\n📋 Детальна діагностика — лист 'Не знайдено (діагностика)' у файлі\n"
    if low_confidence:
        звіт += f"\n🔶 Низька впевненість: {len(low_confidence)} шт. — лист 'Перевір'\n"
    if errors:
        звіт += "\n" + "\n".join(errors)

    safe_edit(chat_id, msg_id, звіт)


def add_to_batch(chat_id: int, item: dict):
    if chat_id not in user_batches:
        user_batches[chat_id] = {'items': []}
        bot.send_message(chat_id, "📥 Отримав! Чекаю 4 сек на наступні файли...")

    if 'timer' in user_batches[chat_id]:
        user_batches[chat_id]['timer'].cancel()

    user_batches[chat_id]['items'].append(item)
    timer = threading.Timer(4.0, process_batch, args=[chat_id])
    user_batches[chat_id]['timer'] = timer
    timer.start()


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM ХЕНДЛЕРИ
# ═══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    bot.reply_to(message, """👋 Привіт! Бот для підбору сантехніки.

📸 Кинь фото рукописного списку — знайду в базі
📝 *пошук <текст>* — текстовий запит  
📋 *правило <текст>* — навчи мене новому сленгу
❌ *забудь <текст>* — видалити неправильний запис з кешу
📊 /кеш — скільки записів в кеші
🛑 /stop — зупинити обробку

Приклад: `правило рожон = трійник редукційний`
Приклад: `забудь МРЗ кут. - 2 шт`""", parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('правило'))
def handle_rule(message):
    rule = message.text[7:].strip()
    if rule:
        add_rule(rule)
        bot.reply_to(message, f"✅ Записав:\n_{rule}_", parse_mode="Markdown")
    else:
        bot.reply_to(message, "Напиши правило після слова 'правило'.")


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('забудь'))
def handle_forget(message):
    """Видаляє запис з кешу нормалізацій."""
    text = message.text[6:].strip()
    if not text:
        bot.reply_to(message, "Напиши що забути після слова 'забудь'.\nПриклад: `забудь МРЗ кут. - 2 шт`", parse_mode="Markdown")
        return
    # Видаляємо без прив'язки до виробника (пробуємо з пустим brand_map)
    deleted = cache_delete(text, {})
    if not deleted:
        # Пробуємо нечіткий пошук — знаходимо схожі ключі і видаляємо їх
        text_tokens = tokenize(text)
        to_delete = []
        for key in list(_CACHE.keys()):
            cached_orig = key.split("::")[0]
            cached_tokens = tokenize(cached_orig)
            if cached_tokens:
                intersection = len(text_tokens & cached_tokens)
                union = len(text_tokens | cached_tokens)
                if union > 0 and intersection / union >= 0.75:
                    to_delete.append(key)
        if to_delete:
            for key in to_delete:
                del _CACHE[key]
            _save_cache()
            bot.reply_to(message, f"✅ Видалено {len(to_delete)} запис(ів) схожих на:\n`{text}`", parse_mode="Markdown")
        else:
            bot.reply_to(message, f"⚠️ Не знайдено в кеші:\n`{text}`\n\nМожливо цей товар ще не кешувався.", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"✅ Видалено з кешу:\n`{text}`", parse_mode="Markdown")


@bot.message_handler(commands=['кеш', 'cache'])
def handle_cache_info(message):
    """Показує інформацію про кеш."""
    total = len(_CACHE)
    if total == 0:
        bot.reply_to(message, "📋 Кеш порожній — заповниться після перших замовлень.")
        return
    # Показуємо останні 5 записів
    recent = list(_CACHE.items())[-5:]
    lines = []
    for key, val in recent:
        orig = key.split("::")[0][:40]
        name = val.get('catalog_name', '')[:50]
        conf = val.get('confidence', 0)
        lines.append(f"• `{orig}` → {name} ({conf}%)")
    bot.reply_to(message,
        f"📋 Кеш нормалізацій: *{total}* записів\n\n"
        f"Останні {len(recent)}:\n" + "\n".join(lines),
        parse_mode="Markdown"
    )


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    for attempt in range(3):
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded = bot.download_file(file_info.file_path)
            image_b64 = base64.b64encode(downloaded).decode('utf-8')
            caption = message.caption or ""
            hint = pending_hints.pop(message.chat.id, "")
            full_caption = " | ".join(filter(None, [caption, hint]))
            add_to_batch(message.chat.id, {
                'type': 'photo', 'data': image_b64, 'caption': full_caption
            })
            return
        except Exception as e:
            if attempt == 2:
                bot.reply_to(message, f"❌ Не вдалося завантажити фото: {e}")
            else:
                time.sleep(2)


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('пошук'))
def handle_text_search(message):
    запит = message.text[5:].strip()
    if запит:
        add_to_batch(message.chat.id, {'type': 'text', 'text': запит})
    else:
        bot.reply_to(message, "Напиши запит після слова 'пошук'.")


@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/')
                     and not m.text.lower().startswith('пошук')
                     and not m.text.lower().startswith('правило'))
def handle_text_hint(message):
    """Звичайний текст зберігаємо як підказку до наступного фото"""
    text = message.text.strip()
    if text:
        pending_hints[message.chat.id] = text
        bot.reply_to(message, f"💬 Підказка збережена: _{text}_\nТепер кидай фото!",
                     parse_mode="Markdown")
        def clear(cid): pending_hints.pop(cid, None)
        t = threading.Timer(120.0, clear, args=[message.chat.id])
        t.daemon = True
        t.start()


@bot.message_handler(commands=['stop'])
def handle_stop(message):
    chat_id = message.chat.id
    stop_flags[chat_id] = True
    if chat_id in user_batches:
        if 'timer' in user_batches[chat_id]:
            user_batches[chat_id]['timer'].cancel()
        user_batches.pop(chat_id, None)
    bot.reply_to(message, "🛑 Зупинено.")


if __name__ == "__main__":
    print("🤖 Бот запущено!")
    bot.polling(none_stop=True)
