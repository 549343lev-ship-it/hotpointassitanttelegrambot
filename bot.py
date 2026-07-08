"""
bot.py — Telegram бот для підбору сантехніки
Архітектура: Gemini 2.5 Flash (OCR) → keyword пошук по JSON → Claude Sonnet (фінальний вибір)

ВСТАНОВЛЕННЯ:
  pip install pytelegrambotapi anthropic google-genai openpyxl pandas flask

ЗМІННІ СЕРЕДОВИЩА:
  TELEGRAM_TOKEN  — токен бота
  ANTHROPIC_KEY   — ключ Claude
  GEMINI_KEY      — ключ Gemini
  WEBHOOK_URL     — публічний URL сервісу (наприклад https://назва.onrender.com)
  PORT            — порт (Render встановлює автоматично)

ПІДГОТОВКА КАТАЛОГУ:
  Запусти один раз: python build_catalog.py
  Це перетворить всі xlsx файли в catalog.json
"""

import os, json, re, base64, threading, time, zipfile
import xml.etree.ElementTree as ET
from io import BytesIO

import telebot
from telebot.types import Update
from flask import Flask, request as flask_request
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

# ═══════════════════════════════════════════════════════════════════════════════
# ПОСТ-ОЧИСТКА КАТАЛОГУ (незалежно від того як побудований catalog.json)
# 1. Вирізаємо {4/20} упаковку з назв — інакше "20" з упаковки дає фальшивий
#    збіг діаметра (труба ф40 {4/20} збігалась на 100% із запитом ф20!)
# 2. Викидаємо "(виведено з асортименту)" — їх не можна продавати
# ═══════════════════════════════════════════════════════════════════════════════
_before = len(CATALOG)
_cleaned = []
for item in CATALOG:
    name = item.get('name', '')
    if 'виведено з асортименту' in name.lower():
        continue  # не продаємо
    # Вирізаємо {N/M} упаковку
    item['name'] = re.sub(r'\s*\{[^}]+\}', '', name).strip()
    _cleaned.append(item)
CATALOG = _cleaned
print(f"🧹 Очистка: вирізано упаковку {{N/M}}, викинуто {_before - len(CATALOG)} виведених. Актуально: {len(CATALOG)}")

def tokenize(text: str) -> set:
    return set(re.findall(r'[а-яёіїєґa-z0-9]+', text.lower()))

# Токени будуються при першому пошуку (lazy) щоб gunicorn стартував швидко
_tokens_built = False

def ensure_tokens():
    global _tokens_built
    if not _tokens_built:
        print("🔨 Індексую токени...")
        for item in CATALOG:
            item['_tokens'] = tokenize(item['name'])
        print("✅ Індексація завершена")
        _tokens_built = True

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
# КЕШ НОРМАЛІЗАЦІЙ — винесено в cache.py
# ═══════════════════════════════════════════════════════════════════════════════
from cache import (cache_lookup, cache_save, cache_delete, get_cache,
                   cache_set_status, cache_ban_pair, is_banned as cache_is_banned,
                   _CACHE, _save_cache)
import clients

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
  Giacomini — автоматика, клапани (рідко)

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
  ⚠️  90° у запиті → пиши просто "87" БЕЗ ",5"! (в каталозі і 87° і 87,5° — токен 87 знайде обидва, а зайва "5" тягне трійники)
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
  бінокль            = Кран з нак. гайкою кутовий Hidros (вузол нижн. підключ. радіатора)
                       АБО "Вузол нижнього підключення" — шукати Hidros чи RAFTEC, НЕ Giacomini!
  компенсаційна муфта = "Муфта вставна" в каталозі (канал., ф110х110)
  надвижна муфта     = "Муфта вставна"
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
ФОРМАТ normalized — КОРОТКО, ТІЛЬКИ КЛЮЧОВІ ПАРАМЕТРИ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚠️ НЕ ВИГАДУЙ товщину стінки, PN, PP-RCT, упаковку — пошук сам знайде!
  ⚠️ Пиши МІНІМУМ: тип виробу + система + діаметр(и) + [кут/різьба] + [серія] + виробник

  Параметри у порядку важливості:
  1. Тип виробу (труба/коліно/трійник/муфта/кран/...)
  2. Система (PPR/канал/натяжний/металопласт)
  3. Діаметр труби (число!)
  4. Діаметр різьби якщо є (1/2, 3/4, 1)
  5. Тип різьби якщо є (РЗ/РВ, ВВ/ВЗ, МРЗ/МРВ)
  6. Кут якщо є (45, 87.5, 90)
  7. Серія якщо важлива (Fiber для опалення, HTR, HT Safe)
  8. Виробник (з підказки менеджера!)

  ✅ ПРАВИЛЬНО (коротко):
    "Труба PPR Fiber ф25 RAFTEC"              (опалення → Fiber, БЕЗ товщини!)
    "Коліно канал ф110 87,5 OSTENDORF"
    "Трійник канал 110х50 87,5 OSTENDORF"
    "Муфта PPR МРЗ 25х3/4 Ekoplastik"
    "Труба канал ф50 0,5м OSTENDORF"
    "Кутник натяжний 16 RAFTEC"               (PUSH)
    "Труба PEX-A 16 RAFTEC"                   (PUSH)

  ❌ НЕПРАВИЛЬНО (вигадані деталі ламають пошук):
    "Труба PPR Fiber Basalt ф 25x4,2 мм, PN25, PP-RCT, RAFTEC"  ← товщина вигадана!
    "Коліно внут. канал. ф110 х 87,5°, сіре, HT Safe, OSTENDORF" ← забагато слів

  СЕРІЇ ДЛЯ ТРУБ (єдине що треба знати):
    Опалення/гаряча PPR → "Fiber" (RAFTEC) | "Faser" (ASG) | "EVO" (Ekoplastik!)
    Холодна PPR         → "PN20" | Ekoplastik → теж "EVO"
    ⚠️ У Ekoplastik ВСІ труби = серія "EVO": "Труба PPR EVO ф25 Ekoplastik"
       (НЕ Fiber Basalt, НЕ STABI — їх виведено!)
    Каналізація ASG     → "HTR" | OSTENDORF → "Safe" | безшумна → "S-LINE"

  ПЕРЕХІД/РЕДУКЦІЯ PPR по виробниках:
    Ekoplastik → "Муфта перехідна PPR ВВ ф 25х20 Ekoplastik"  (слово "перехідна ВВ"!)
    RAFTEC     → "Муфта редукційна PPR ф 25х20 RAFTEC"
    ⚠️ Шрабер = інструмент зачистки, НІКОЛИ не фітинг!

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ПРІОРИТЕТИ ПРИ ПОШУКУ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  1. Діаметр         — ОБОВ'ЯЗКОВО повинен збігатися
  2. Кут / довжина   — якщо вказано, повинні збігатися
  3. Виробник        — якщо вказано в запиті, пріоритет йому
  Числа мають подвійну вагу над словами (діаметр, кут, довжина)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ТРУБИ — НЕСТАНДАРТНІ ДОВЖИНИ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  0,3м → шукати 0,25м (так заведено в каталозі)
  "EK" / "ЕК" на початку рядка = виробник Ekoplastik!
  Шафа колекторна (будь-яка, ALPHAHAT тощо) → шукати ASG: "Шафа колекторна N контурів ASG"
  Трійник перехідний 25х20х25 = редукційний → пиши "Трійник PPR 25х20х25 <виробник>"
  Мірелон/мірслон = утеплювач для труб (Thermaflex FRZ теж підходить, не тільки PLM)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PUSH-СИСТЕМА (натяжна, PEX-A) — АНАЛОГ ПАЙКИ:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PUSH = труба PEX-A (зшитий поліетилен) + натяжні латунні фітинги.
  Гільза натягується пресом на з'єднання — без пайки, без прокладок.
  PEX-A кращий за PPR: гнучкий, тонша стінка → більша прохідність.

  ⚠️ ВИЗНАЧЕННЯ СИСТЕМИ ПО ВСЬОМУ ЗАМОВЛЕННЮ:
  - Є труба PEX / ПЕКС / "пуш" / переважає діаметр 16 → ВСЕ замовлення PUSH,
    всі фітинги category=push_systems!
  - Труба PPR і діаметри 20+ → все пайка (plastic_ppr)
  - Об'єкт АБО на PPR АБО на PUSH — змішування мінімальне
    (максимум пару перехідників PPR при PUSH-об'єкті)

  ВІДПОВІДНІСТЬ ДІАМЕТРІВ (PEX тонша стінка → на розмір менше):
  PUSH 16 ≈ PPR 20 | PUSH 20 ≈ PPR 25 | PUSH 25 ≈ PPR 32

  РОЗМІРИ RAFTEC PUSH: 16х2,2 / 20х2,8 / 25х3,5 / 32х4,4

  НАЗВИ В КАТАЛОЗІ (маркер PUSH — слово "натяжний" + "PUSH"):
  Труба:        "Труба PЕХ-A, ф 25x3,5 мм, EVOH, SILVER, RAFTEC"
  Коліно/кут:   "Кутник натяжний, ф 25х25, PUSH, RAFTEC"
  Трійник:      "Трійник натяжний, ф 25х25х25, PUSH, RAFTEC"  ← ТРИ розміри!
  Трійник ред.: "Трійник натяжний, ф 25х16х25, PUSH, RAFTEC"
                ⚠️ НЕ пиши слово "редукційний" для натяжних — його немає в назвах!
                Просто три розміри: 25х16х25
  Муфта/перехід:"Муфта натяжна, ф 20х16, PUSH, RAFTEC"
                ⚠️ теж БЕЗ "редукційна" — просто два розміри
  Перехід різьба:"Муфта натяжна РЗ, ф 16х1/2, PUSH, RAFTEC" (або РВ)
  Гільза:       "Гільза натяжна, ф 16, PUSH, RAFTEC"  ← НЕ "Насувна"!
  Заглушка:     "Заглушка натяжна, ф 16, PUSH, RAFTEC"
  Настінне:     "Коліно натяжне настінне, ф 20х1/2, РВ, PUSH, RAFTEC"

  ⚠️ Якщо натяжного фітинга потрібного розміру НЕМАЄ в каталозі —
  краще "не знайдено" ніж PPR-заміна (PPR не з'єднається з PEX!)

  ⚠️ ГІЛЬЗИ ОБОВ'ЯЗКОВІ: кожне натяжне з'єднання потребує насувну гільзу
  відповідного діаметру! Якщо в замовленні PUSH-фітинги а гільз немає —
  додай гільзи (кількість ≈ кількість з'єднань фітингів × 2).

  ВИРОБНИКИ PUSH: RAFTEC (основний), REHAU (преміум, рідко).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
КОМПЛЕКТИ ТЕПЛОЇ ПІДЛОГИ (гребінка):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Якщо майстер пише "гребінка N контурів", "комплект ТП N контурів",
  "тепла підлога в зборі", "гребінка в зборі" — розгортай у повний список.
  N = кількість контурів. Євроконус × N×2 (по 2 на контур).

  ЛОГІКА РІЗЬБИ (важливо!):
  Кран ВВ (внутрішня різьба) → муфта МРЗ (зовнішня)
  Кран ВЗ (зовнішня різьба) → муфта МРВ (внутрішня)

  ВАРІАНТ 1 — Найдешевший (без змішувача, термоголовка розбірна):
  - Колектор Nх контр., 1"х3/4", витратоміри, без єврокон., нерж., RAFTEC STEEL × 1
  - Євроконус 3/4" × N×2
  - Кінцевий елемент 1", латунь, нікель, RAFTEC × 1
  - Термоголовка М30х1,5, з виносним датчиком, RAFTEC × 1
  - Кран кутовий під термоголовку 3/4", M30x1.5, з антипрот., RAFTEC × 1
  - Комплект підключення насоса 1" PCNR03, RAFTEC × 1
  - Насос APE 25/40/130 PWM1 AUTO, Termojet × 1 (ОБОВ'ЯЗКОВО!)
  - Кран кульовий з накид. гайкою ВВ, DN20, 3/4", RAFTEC × 2
  - Муфта PPR МРЗ ф 25х3/4", PP-RCT, Ekoplastik × 2

  ВАРІАНТ 2 — Середній/Популярний (SUR03 в зборі):
  - Змішувальний вузол 1", нікель, SUR03, RAFTEC × 1
  - Колектор Nх контр., 1"х3/4", витратоміри, без єврокон., нерж., RAFTEC STEEL × 1
  - Євроконус 3/4" × N×2
  - Кінцевий елемент 1", нікель, RAFTEC × 1
  - Насос APE 25/40/130 PWM1 AUTO, Termojet × 1 (ОБОВ'ЯЗКОВО!)
  - Напівзгін з накидною гайкою DN20, 3/4", пара, нікель, RAFTEC × 1
  - Кран кульовий ВВ, DN20, 3/4", RAFTEC BLACK × 1
  - Кран кульовий ВЗ, DN20, 3/4", RAFTEC BLACK × 1
  - Муфта PPR МРЗ ф 25х3/4", PP-RCT, Ekoplastik × 2

  ВАРІАНТ 3 — Найдорожчий (LSG-161H з трьохходовим):
  - Змішувальний вузол 1", латунь, LSG-161H, RAFTEC GOLD × 1
  - Колектор Nх контр., 1"х3/4", витратоміри, єврокон., латунь, RAFTEC GOLD × 1
  - Кінцевий елемент 1", латунь, BRASS, RAFTEC × 1
  - Насос APE 25/40/130 PWM1 AUTO, Termojet × 1 (ОБОВ'ЯЗКОВО!)
  - Кран кульовий з амер. ВЗ, DN25, 1", DRBS3, метелик, RAFTEC BLACK × 1
  - Муфта PPR МРЗ ф 32х1", PP-RCT, Ekoplastik × 1
  - Муфта PPR МРЗ ф 25х3/4", PP-RCT, Ekoplastik × 1

  Якщо варіант не вказано → використовувати ВАРІАНТ 2 (найпопулярніший).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ПІДКЛЮЧКА ДО КОТЛА (стандартний комплект):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  - Кран кут. кульовий з накид. гайкою ВВ, 1/2", DRBZ1, RAFTEC × 2
  - Кран кут. кульовий з накид. гайкою ВЗ, DN15, 1/2", PN40, DRBM1, RAFTEC × 2
  - Кран кут. кульовий з накид. гайкою ВВ, 3/4", DRBZ2, RAFTEC × 2
  - Кран кут. кульовий з накид. гайкою ВЗ, DN20, 3/4", PN40, DRBM2, RAFTEC × 2
  - Фільтр груб. очистки, DN15, 1/2", під пломбу, BLACK, RAFTEC × 1
  - Фільтр груб. очистки, DN20, 3/4", під пломбу, BLACK, RAFTEC × 1
  - Муфта PPR МРЗ ф 20х1/2", PP-RCT, Ekoplastik × 2
  - Муфта PPR МРЗ ф 25х3/4", PP-RCT, Ekoplastik × 2

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ПІДКЛЮЧЕННЯ РАДІАТОРА:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  БОКОВЕ підключення (K):
  - Клапан радіаторний кутовий, подача, 1/2"х1/2", верх, латунь, нікель, RAFTEC × 2
  - Комплект підключення радіатора, кутовий, 1/2", термостатичний, латунь, нікель, RAFTEC × 1
  - Муфта PPR МРЗ ф 20х1/2", PP-RCT, Ekoplastik × 1
  - Радіатор HIDROS тип 22

  НИЖНЄ підключення (VK):
  - Радіатор HIDROS тип 22 VK
  - Вентильна вставка для радіаторів Hidros VK × 1
  - Термоголовка М30х1,5, білий, WHITE, RAFTEC × 1
  - Вузол нижнього підключення радіатора, кутовий, 1/2"х3/4", латунь, нікель × 1
  - Муфта PPR з євроконусом ф 20х3/4", PP-RCT, Ekoplastik × 1
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
    # Push системи (натяжні, PEX)
    'пуш':        'push_systems',
    'push':       'push_systems',
    'прес':       'push_systems',
    'натяжн':     'push_systems',
    'пекс':       'push_systems',
    'pex':        'push_systems',
    'гільза':     'push_systems',
    'гільзи':     'push_systems',
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
    # Євроконус — в металопластику
    'євроконус':     'metal_plastic',
    'eurokonus':     'metal_plastic',
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
    'ecosofy':      ['ecosoft', 'Ecosoft', 'ecosofy'],
    'ecosoft':      ['ecosoft', 'Ecosoft'],
    'екософт':      ['ecosoft', 'Ecosoft'],
    'екософі':      ['ecosoft', 'Ecosoft', 'ecosofy'],
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
    # Бренди що майстри пишуть прямо в рядках замовлення
    'wilo':         ['wilo', 'WILO'],
    'віло':         ['wilo', 'WILO'],
    'grundfos':     ['grundfos', 'GRUNDFOS'],
    'грундфос':     ['grundfos', 'GRUNDFOS'],
    'bonomi':       ['bonomi', 'Bonomi'],
    'бономі':       ['bonomi', 'Bonomi'],
    'pattaroni':    ['pattaroni', 'Pattaroni'],
    'k-flex':       ['k-flex', 'K-FLEX'],
    'kflex':        ['k-flex', 'K-FLEX'],
    'кфлекс':       ['k-flex', 'K-FLEX'],
    'valsir':       ['valsir', 'Valsir'],
    'meibes':       ['meibes', 'Meibes'],
    'flamco':       ['flamco', 'Flamco'],
    'reflex':       ['reflex', 'Reflex'],
    'icma':         ['icma', 'Icma'],
    'drazice':      ['drazice', 'Drazice'],
    'дражице':      ['drazice', 'Drazice'],
    'vaillant':     ['vaillant', 'Vaillant'],
    'вайлант':      ['vaillant', 'Vaillant'],
    'alcaplast':    ['alcaplast', 'AlcaPlast'],
    'esbe':         ['esbe', 'ESBE'],
    'grohe':        ['grohe', 'Grohe'],
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
        ['ecosoft', 'Ecosoft'],  # A
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
def normalize_photo(image_b64: str, caption: str = "", client_prefs: dict = None) -> list[dict]:
    """OCR + нормалізація фото через Gemini 2.5 Flash. Повертає також category і brand_map."""
    rules = get_rules()
    rules_block = f"\nДодаткові правила від менеджера:\n{rules}" if rules else ""
    client_prefs = client_prefs or {}

    # Парсимо підказку менеджера → карту виробників
    brand_map = parse_caption_brands(caption)

    # ── Будуємо блок пріоритетів виробника для Gemini ────────────────────────
    # Рівень 1: менеджер (найвищий) | Рівень 2: профіль клієнта | без вказівки — не додавати
    brand_hint = ""
    manager_lines = []
    if brand_map:
        for cat, toks in brand_map.items():
            manager_lines.append(f"  {cat} → {toks[0]}")

    client_lines = []
    client_by_cat = client_prefs.get('by_category', {})
    for cat, brands in client_by_cat.items():
        if cat in brand_map:
            continue  # менеджер перебиває — не показуємо клієнтське
        if brands:
            client_lines.append(f"  {cat} → {brands[0][0]}")

    if manager_lines or client_lines:
        brand_hint = "\n\n╔══════════════════════════════════════════════╗\n"
        brand_hint += "║  ВИРОБНИКИ — ПРІОРИТЕТИ (суворо дотримуйся!)  ║\n"
        brand_hint += "╚══════════════════════════════════════════════╝"
        if manager_lines:
            brand_hint += "\n🥇 РІВЕНЬ 1 — МЕНЕДЖЕР ВКАЗАВ (обов'язково, перебиває все):\n"
            brand_hint += "\n".join(manager_lines)
        if client_lines:
            brand_hint += "\n🥈 РІВЕНЬ 2 — КЛІЄНТ ЗАЗВИЧАЙ БЕРЕ (якщо менеджер не вказав для категорії):\n"
            brand_hint += "\n".join(client_lines)
        brand_hint += """

ПРИКЛАД: якщо менеджер каже sewage → ostendorf, то ВСЯ каналізація:
  "Коліно 110 90°" → "Коліно вн. канал. ф110 х 87,5°, сіре, HT Safe, OSTENDORF"
  НЕ ASG! НЕ HTR! Менеджер сказав остендорф — значить тільки OSTENDORF."""

    prompt = f"""Ти — експерт із сантехніки України. На фото рукописний список замовлення від майстра.

ПІДКАЗКА МЕНЕДЖЕРА: {caption}{brand_hint}{rules_block}

БАЗА ЗНАНЬ:
{ЗНАННЯ_САНТЕХНІКИ}

ЗАВДАННЯ:
1. Прочитай кожен рядок (тільки сантехніка/опалення/водопостачання)
2. Нормалізуй до назви як в прайсі
   - РІВЕНЬ 1: якщо менеджер вказав виробника для категорії — ТІЛЬКИ він
   - РІВЕНЬ 2: якщо менеджер не вказав, але клієнт зазвичай бере певного — використай його
   - Якщо жодного — не додавай виробника взагалі
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
# КРОК 2: KEYWORD ПОШУК ПО КАТАЛОГУ — з відсотком точності
# ═══════════════════════════════════════════════════════════════════════════════
def keyword_search(query: str, top_n: int = 12, brand_tokens: list = None) -> list[dict]:
    """
    Пошук з багаторівневим скорингом.
    Повертає топ-N кандидатів відсортованих за точністю збігу.
    Кожен кандидат має поле _match_pct (0-100) — відсоток точності.

    brand_tokens: якщо задано — ЖОРСТКИЙ фільтр, скоряться ТІЛЬКИ товари
    цього виробника. Це гарантує що коли менеджер каже "остендорф" —
    топ-12 не заповниться ASG (яких у каталозі в рази більше).

    Логіка scoring:
    - Числа (діаметри, кути, довжини) — вага x3 (найважливіше)
    - Слова запиту що є в назві — вага x1
    - Штраф за зайві токени в назві кандидата
    """
    ensure_tokens()  # lazy індексація при першому запиті
    q_tokens  = tokenize(query)
    q_numbers = set(re.findall(r'\d+', query.lower()))
    q_words   = q_tokens - q_numbers

    if not q_tokens:
        return []

    # Токени виробника в нижньому регістрі для швидкої перевірки
    brand_lc = [t.lower() for t in brand_tokens] if brand_tokens else None

    scores = []
    for item in CATALOG:
        # ── ЖОРСТКИЙ фільтр виробника ПЕРЕД скорингом ────────────────────────
        if brand_lc:
            name_lower = item['name'].lower()
            if not any(t in name_lower for t in brand_lc):
                continue  # не той виробник — навіть не скоримо

        it = item['_tokens']

        # Числа важливіші за всі (діаметр, кут, довжина)
        num_hits   = len(q_numbers & it)
        word_hits  = len(q_words & it)

        if num_hits == 0 and word_hits == 0:
            continue

        # Базовий score
        raw_score = num_hits * 3 + word_hits

        # Штраф за зайві токени в назві кандидата
        extra_tokens = max(0, len(it) - len(q_tokens))
        penalty = extra_tokens * 0.1

        total = raw_score - penalty

        # (п/з) = під замовлення → в кінець списку (вибирається останнім)
        if '(п/з)' in item['name']:
            total -= 5

        # Відсоток: скільки токенів запиту знайдено в кандидаті
        max_possible = len(q_numbers) * 3 + len(q_words)
        match_pct = int((raw_score / max_possible * 100)) if max_possible > 0 else 0
        match_pct = min(match_pct, 100)

        scores.append((total, match_pct, item))

    scores.sort(key=lambda x: -x[0])
    result = []
    for _, pct, item in scores[:top_n]:
        item_copy = dict(item)
        item_copy['_match_pct'] = pct
        result.append(item_copy)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# КРОК 3: ФІНАЛЬНИЙ ВИБІР (Claude Sonnet)
# ═══════════════════════════════════════════════════════════════════════════════
CLAUDE_BATCH_SIZE = 8  # менший батч = надійніший JSON

def _parse_claude_json(raw: str, expected: int) -> list[dict] | None:
    """Парсить JSON відповідь Claude. Повертає None якщо не вдалось."""
    raw = re.sub(r'```\w*', '', raw).strip()
    start = raw.find('[')
    end   = raw.rfind(']') + 1
    if start == -1:
        return None
    # Спроба 1: весь масив
    if end > 0:
        try:
            parsed = json.loads(raw[start:end])
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
    # Спроба 2: витягуємо окремі об'єкти регуляркою (рятує обрізаний JSON)
    objects = []
    for m in re.finditer(r'\{[^{}]*\}', raw[start:]):
        try:
            objects.append(json.loads(m.group()))
        except Exception:
            continue
    return objects if objects else None


def claude_pick_one_batch(позиції: list[dict], _retry: bool = True) -> list[dict]:
    """Один запит Claude на невеликий батч позицій. З ретраєм поштучно при збої."""
    запити = []
    for i, пос in enumerate(позиції):
        brand_note = f"\n   ⚠️ ВИРОБНИК: тільки {пос['required_brand']}" if пос.get('required_brand') else ""
        кандидати = "\n".join(
            f"  {j+1}. [{c.get('_match_pct', 0)}%] {c['name']}"
            for j, c in enumerate(пос['candidates'])
        )
        запити.append(f"{i+1}. {пос['normalized']}{brand_note}\n{кандидати}")

    prompt = f"""Сантехнік. Для кожного запиту — номер кандидата що підходить найкраще.

{chr(10).join(запити)}

Правила (СУВОРО):
1. Діаметр ОБОВ'ЯЗКОВО збігається — якщо запит ф20, кандидат ф40 = знайдено:false!
2. Якщо є ВИРОБНИК — тільки він.
3. "(п/з)" = під замовлення: вибирай ТІЛЬКИ якщо звичайного аналога НЕМАЄ серед кандидатів (тоді в reason додай: пз під замовлення).
4. PUSH-запит (натяжний) ≠ PPR-товар: якщо шукаємо натяжний фітинг а є тільки PPR — знайдено:false.
confidence: 95=точний збіг, 80=майже точний, 60=схожий.
reason/fail_reason: КОРОТКО (до 8 слів), БЕЗ лапок і спецсимволів всередині тексту!

JSON масив рівно {len(позиції)} елементів, нічого крім JSON:
[{{"знайдено":true,"номер_кандидата":1,"confidence":95,"reason":"точний збіг діаметр виробник","fail_reason":""}},...]"""

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        parsed = _parse_claude_json(raw, len(позиції))
        if parsed is None:
            raise ValueError("JSON не розпарсився")
        # Доповнюємо якщо менше
        while len(parsed) < len(позиції):
            parsed.append({"знайдено": False, "confidence": 0, "reason": "",
                           "fail_reason": "Claude не повернув відповідь для цієї позиції"})
        return parsed[:len(позиції)]
    except Exception as e:
        # ── Ретрай поштучно: замість втрати всього батчу пробуємо по 1 позиції ──
        if _retry and len(позиції) > 1:
            print(f"⚠️ Батч {len(позиції)} впав ({e}), ретрай поштучно...")
            results = []
            for пос in позиції:
                results.extend(claude_pick_one_batch([пос], _retry=False))
            return results
        return [{"знайдено": False, "confidence": 0, "reason": "",
                 "fail_reason": f"помилка Claude: {e}"}] * len(позиції)


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
        original    = пос.get('original', '')
        normalized  = пос.get('normalized', '')
        category    = пос.get('category', 'other')
        brand_map   = пос.get('_brand_map', {})
        client_slug = пос.get('_client_slug')
        client_prefs = пос.get('_client_prefs', {})

        if progress_cb:
            progress_cb(i + 1, len(позиції))

        # ═══ ЧОТИРИРІВНЕВА ПРІОРИТЕТНІСТЬ ═══
        # Рівень 1: слова менеджера (brand_map) — найвищий, перебиває все
        # Рівень 2: кеш клієнта (тільки якщо не суперечить рівню 1)
        # Рівень 3: кеш бота (тільки якщо не суперечить рівню 1)
        # Рівень 4: преференції клієнта → DEFAULT_BRAND_PRIORITY

        manager_brand_tokens = brand_map.get(category)  # Рівень 1 активний?

        # ── Рівень 2: кеш клієнта ──────────────────────────────────────────────
        # Якщо менеджер вказав виробника — кеш приймається ТІЛЬКИ якщо
        # закешований товар містить цього виробника (інакше ігнорується).
        if client_slug:
            c_cached = clients.client_cache_lookup(
                client_slug, original,
                required_brand_tokens=manager_brand_tokens)
            if c_cached:
                результати[i] = {
                    'original':         original,
                    'normalized':       normalized,
                    'знайдено':         True,
                    'назва':            c_cached['catalog_name'],
                    'ціна':             '',
                    'qty':              пос.get('qty', ''),
                    'category':         c_cached.get('category', category),
                    'confidence':       c_cached['confidence'],
                    'джерело':          '👤 кеш клієнта' + (' ✅' if c_cached.get('status') == 'confirmed' else ''),
                    'reason':           f"З кешу клієнта ({c_cached['confidence']}%)",
                    'fail_reason':      '',
                    'candidates_debug': [],
                    '_from_cache':      True,
                }
                continue

        # ── Рівень 3: кеш бота ─────────────────────────────────────────────────
        cached = cache_lookup(original, brand_map)
        if cached:
            # Перевірка виробника: якщо менеджер вказав — кеш має збігатись
            cache_ok = True
            if manager_brand_tokens:
                name_lower = cached.get('catalog_name', '').lower()
                cache_ok = any(t.lower() in name_lower for t in manager_brand_tokens)
            if cache_ok:
                результати[i] = {
                    'original':         original,
                    'normalized':       cached['normalized'],
                    'знайдено':         True,
                    'назва':            cached['catalog_name'],
                    'ціна':             '',
                    'qty':              пос.get('qty', ''),
                    'category':         cached.get('category', category),
                    'confidence':       cached['confidence'],
                    'джерело':          '🤖 кеш бота' + (' ✅' if cached.get('status') == 'confirmed' else ''),
                    'reason':           f"З кешу ({cached['confidence']}%)",
                    'fail_reason':      '',
                    'candidates_debug': [],
                    '_from_cache':      True,
                    '_category':        cached.get('category', category),
                }
                continue
            # Кеш суперечить підказці менеджера → ігноруємо, шукаємо заново

        # ── Пошук з жорстким фільтром виробника ВСЕРЕДИНІ ──────────────────────
        # Пріоритети: менеджер → виробник З РЯДКА → клієнт → дефолти → без фільтру.
        required_brand = None
        brand_warning = ''
        джерело = ''
        кандидати = []

        # Виробник згаданий прямо в рядку майстра (WILO, Bonomi, Herz, K-FLEX...)
        line_brand_tokens = None
        line_brand_name = ''
        _text_lc = f"{original} {normalized}".lower()
        for _bkey, _btoks in BRAND_TOKENS.items():
            # межі слова: щоб 'eco' не збігалось з 'ecoflex', 'kan' з 'kanal'
            if re.search(r'(?<![a-zа-яёіїєґ0-9])' + re.escape(_bkey) + r'(?![a-zа-яёіїєґ0-9])', _text_lc):
                line_brand_tokens = _btoks
                line_brand_name = _btoks[0]
                break

        if manager_brand_tokens:
            # РІВЕНЬ 1: менеджер вказав у підказці — найвищий
            кандидати = keyword_search(normalized, top_n=12,
                                       brand_tokens=manager_brand_tokens)
            if кандидати:
                required_brand = manager_brand_tokens[0]
                джерело = '👨 менеджер'
            else:
                кандидати = keyword_search(normalized, top_n=12)
                brand_warning = f"⚠️ {manager_brand_tokens[0]} відсутній в каталозі для цієї позиції"
                джерело = '⚠️ fallback'
        elif line_brand_tokens:
            # РІВЕНЬ 1.5: виробник написаний У РЯДКУ майстра (WILO, Bonomi...)
            кандидати = keyword_search(normalized, top_n=12,
                                       brand_tokens=line_brand_tokens)
            if кандидати:
                required_brand = line_brand_name
                джерело = '📝 з рядка'
            else:
                кандидати = keyword_search(normalized, top_n=12)
                brand_warning = f"⚠️ {line_brand_name} відсутній в каталозі — підібрано аналог"
                джерело = '⚠️ fallback'
        else:
            # РІВЕНЬ 2: преференції клієнта — теж пошук всередині виробника
            client_by_cat = client_prefs.get('by_category', {}).get(category, [])
            for brand, _count in client_by_cat[:3]:
                tokens = BRAND_TOKENS.get(brand)
                if not tokens:
                    continue
                кандидати = keyword_search(normalized, top_n=12, brand_tokens=tokens)
                if кандидати:
                    required_brand = tokens[0]
                    джерело = '👤 профіль клієнта'
                    break
            # РІВЕНЬ 3: загальні дефолти
            if not кандидати:
                for priority_tokens in DEFAULT_BRAND_PRIORITY.get(category, []):
                    кандидати = keyword_search(normalized, top_n=12,
                                               brand_tokens=priority_tokens)
                    if кандидати:
                        required_brand = priority_tokens[0]
                        джерело = '⚙️ дефолт'
                        break
            # РІВЕНЬ 4: без фільтру взагалі
            if not кандидати:
                кандидати = keyword_search(normalized, top_n=12)
                джерело = '🔍 вільний пошук'

        if not кандидати:
            результати[i] = {
                **пос,
                'знайдено':    False,
                'назва':       '',
                'confidence':  0,
                'джерело':     '',
                'reason':      '',
                'fail_reason': 'keyword пошук не знайшов жодного кандидата — можливо товар відсутній в каталозі або назва занадто специфічна',
                'candidates_debug': [],
            }
            continue

        # Прибираємо кандидатів забанених адміном для цього оригіналу
        кандидати_до_бану = кандидати[:]
        кандидати = [c for c in кандидати if not cache_is_banned(original, c['name'])]

        # Шрабер = інструмент зачистки, НЕ фітинг! Вирізаємо якщо шукаємо муфту/перехід
        if re.search(r'муфт|перех|редукц', normalized.lower()):
            кандидати = [c for c in кандидати if 'шрабер' not in c['name'].lower()]

        if not кандидати:
            результати[i] = {
                **пос,
                'знайдено':    False,
                'назва':       '',
                'confidence':  0,
                'джерело':     '',
                'reason':      '',
                'fail_reason': 'всі кандидати забанені адміном для цього запиту',
                'candidates_debug': [c['name'] for c in кандидати_до_бану[:5]],
            }
            continue

        потребують_claude.append({
            'idx':              i,
            'normalized':       normalized,
            'original':         original,
            'candidates':       кандидати,
            'candidates_debug': [c['name'] for c in кандидати[:5]],
            'qty':              пос.get('qty', ''),
            'required_brand':   required_brand,
            'category':         category,
            'brand_map':        brand_map,
            'client_slug':      client_slug,
            'джерело':          джерело,
            'brand_warning':    brand_warning,
        })

    if потребують_claude:
        retry_позиції = []
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
                # Попередження якщо виробник з підказки не знайшовся
                reason_full = reason
                if пос.get('brand_warning'):
                    reason_full = f"{пос['brand_warning']}. {reason}"
                результати[idx] = {
                    'original':         пос['original'],
                    'normalized':       пос['normalized'],
                    'знайдено':         True,
                    'назва':            found['name'],
                    'артикул':          found.get('artikul', ''),
                    'ціна':             found.get('price', ''),
                    'qty':              пос['qty'],
                    'category':         пос['category'],
                    'confidence':       confidence,
                    'keyword_pct':      found.get('_match_pct', 0),
                    'джерело':          пос.get('джерело', ''),
                    'brand_warning':    пос.get('brand_warning', ''),
                    'reason':           reason_full,
                    'fail_reason':      '',
                    'candidates_debug': пос['candidates_debug'],
                }
                # ── Зберігаємо в загальний кеш ────────────────────────────────
                cache_save(
                    original=пос['original'],
                    brand_map=пос['brand_map'],
                    normalized=пос['normalized'],
                    catalog_name=found['name'],
                    category=пос['category'],
                    confidence=confidence,
                )
                # ── Зберігаємо в кеш клієнта якщо клієнт активний ─────────────
                if пос.get('client_slug'):
                    clients.client_cache_save(
                        slug=пос['client_slug'],
                        original=пос['original'],
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
                # Кандидат на другий шанс: був жорсткий виробник → спробуємо аналог
                if пос.get('required_brand'):
                    пос['_fail_reason_first'] = fail_reason
                    retry_позиції.append(пос)

    # ═══ ДРУГИЙ ШАНС: у вказаного виробника товару немає → шукаємо аналог ═══
    # Приклад: труб Ekoplastik немає в каталозі → знайти Fiber трубу RAFTEC/ASG
    # з явною позначкою "⚠️ аналог" замість чесного але марного "не знайдено".
    if retry_позиції:
        retry_batch = []
        for пос in retry_позиції:
            new_cands = keyword_search(пос['normalized'], top_n=12)
            # ті самі захисти: бан + шрабер
            new_cands = [c for c in new_cands
                         if not cache_is_banned(пос['original'], c['name'])]
            if re.search(r'муфт|перех|редукц', пос['normalized'].lower()):
                new_cands = [c for c in new_cands if 'шрабер' not in c['name'].lower()]
            # прибираємо кандидатів того ж виробника (їх Claude вже відхилив)
            _rb = пос['required_brand'].lower()
            new_cands = [c for c in new_cands if _rb not in c['name'].lower()] or new_cands
            if new_cands:
                retry_batch.append({
                    'idx':              пос['idx'],
                    'normalized':       пос['normalized'],
                    'original':         пос['original'],
                    'candidates':       new_cands,
                    'candidates_debug': [c['name'] for c in new_cands[:5]],
                    'qty':              пос['qty'],
                    'required_brand':   None,   # без жорсткого виробника
                    'category':         пос['category'],
                    'brand_map':        пос['brand_map'],
                    'client_slug':      None,   # аналоги НЕ кешуємо
                    'old_brand':        пос['required_brand'],
                })
        if retry_batch:
            відповіді2 = claude_pick_batch(retry_batch)
            for j, пос in enumerate(retry_batch):
                r = відповіді2[j] if j < len(відповіді2) else {'знайдено': False}
                if r.get('знайдено') and r.get('номер_кандидата'):
                    n = max(0, min(int(r['номер_кандидата']) - 1, len(пос['candidates'])-1))
                    found = пос['candidates'][n]
                    warn = f"⚠️ у {пос['old_brand']} немає — підібрано аналог"
                    результати[пос['idx']] = {
                        'original':         пос['original'],
                        'normalized':       пос['normalized'],
                        'знайдено':         True,
                        'назва':            found['name'],
                        'артикул':          found.get('artikul', ''),
                        'ціна':             found.get('price', ''),
                        'qty':              пос['qty'],
                        'category':         пос['category'],
                        'confidence':       int(r.get('confidence', 0)),
                        'keyword_pct':      found.get('_match_pct', 0),
                        'джерело':          '⚠️ аналог',
                        'brand_warning':    warn,
                        'reason':           f"{warn}. {r.get('reason', '')}",
                        'fail_reason':      '',
                        'candidates_debug': пос['candidates_debug'],
                    }
                # якщо і аналог не знайшовся — лишаємо перший fail-результат

    # ── Заповнюємо ціну для позицій з кешу ────────────────────────────────────
    for r in результати:
        if r and r.get('_from_cache') and r.get('назва'):
            # Шукаємо актуальну ціну в каталозі
            for item in CATALOG:
                if item['name'] == r['назва']:
                    r['ціна'] = item.get('price', '')
                    r['артикул'] = item.get('artikul', '')
                    break
            r.pop('_from_cache', None)
            r.pop('_category', None)

    return результати


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL
# ═══════════════════════════════════════════════════════════════════════════════
def parse_qty(qty_str) -> tuple:
    """
    Розділяє кількість на число і одиницю:
    '10 шт'  → (10, 'шт')
    '60 м.п.' → (60, 'м.п')
    '0,5 м'  → (0.5, 'м')
    '5'      → (5, '')
    Число повертається як number щоб Excel міг сумувати.
    """
    s = str(qty_str or '').strip()
    if not s:
        return '', ''
    m = re.match(r'^\s*(\d+(?:[.,]\d+)?)\s*(.*)$', s)
    if m:
        num_str = m.group(1).replace(',', '.')
        try:
            num = float(num_str)
            if num == int(num):
                num = int(num)
        except ValueError:
            num = num_str
        unit = m.group(2).strip().rstrip('.').strip()
        return num, unit
    return s, ''


def create_excel(результати: list[dict]) -> tuple[BytesIO, list[str], list[str]]:
    знайдено_rows  = []
    не_знайдено_rows = []
    low_conf_rows  = []

    for r in результати:
        if not r:
            continue
        conf = r.get('confidence', 0)
        kw   = r.get('keyword_pct', 0)

        if r.get('знайдено'):
            # Два показники: keyword пошук % і Claude впевненість %
            zbig_label = f"🔍{kw}% / 🤖{conf}%"
            qty_num, qty_unit = parse_qty(r.get('qty', ''))
            знайдено_rows.append({
                'Артикул':      r.get('артикул', ''),
                'Наименование': r.get('назва', ''),
                'Кількість':    qty_num,
                'Од.':          qty_unit,
                'Ціна':         r.get('ціна', ''),
                'Збіг':         zbig_label,
                'Джерело':      r.get('джерело', ''),
                'Чому знайшло': r.get('reason', ''),
                'Оригінал':     r.get('original', ''),
            })
            if conf < 70 or kw < 50 or r.get('brand_warning'):
                low_conf_rows.append(
                    f"{r.get('original','')} → {r.get('назва','')} ({zbig_label}): {r.get('reason','')}"
                )
        else:
            _cands = r.get('candidates_debug', [])
            не_знайдено_rows.append({
                'Оригінал':          r.get('original', ''),
                'Нормалізовано':     r.get('normalized', ''),
                'Найближчий аналог': _cands[0] if _cands else '',
                'Причина':           r.get('fail_reason', ''),
                'Топ кандидати':     ', '.join(_cands),
            })

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        # Лист 1: Знайдені товари
        df = pd.DataFrame(знайдено_rows) if знайдено_rows else pd.DataFrame(
            columns=['Артикул','Наименование','Кількість','Од.','Ціна','Збіг','Джерело','Чому знайшло','Оригінал'])
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
last_results  = {}  # chat_id -> список результатів останнього замовлення (для вірно/помилка N)

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

    # ── Активний клієнт (якщо є) ───────────────────────────────────────────────
    active_slug = clients.get_active(chat_id)
    client_prefs = clients.get_preferences(active_slug) if active_slug else {}
    client_name = ""
    if active_slug:
        profile = clients.get_profile(active_slug)
        client_name = profile['name'] if profile else active_slug

    client_line = f"👤 Клієнт: {client_name}\n" if client_name else ""

    # ── Живе повідомлення статусу ──────────────────────────────────────────────
    status = bot.send_message(chat_id,
        f"⏳ Починаю обробку {len(items)} файл(ів)...\n"
        f"{client_line}"
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
                позиції = normalize_photo(item['data'], item.get('caption', ''), client_prefs)
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

    # ── Прикріплюємо дані клієнта до кожної позиції ────────────────────────────
    if active_slug:
        for п in всі_позиції:
            п['_client_slug'] = active_slug
            п['_client_prefs'] = client_prefs

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

    # Зберігаємо результати для команд адміна "вірно N" / "помилка N"
    last_results[chat_id] = {
        'результати': [r for r in результати if r and r.get('знайдено')],
        'client_slug': active_slug,
    }

    # Логуємо використання для статистики адміна
    username = items[0].get('username', str(chat_id)) if items else str(chat_id)
    log_usage(chat_id, username, len(результати), len(знайдено), len(items))

    # Зберігаємо замовлення в історію клієнта (для навчання преференцій)
    if active_slug:
        caption = items[0].get('caption', '') if items else ''
        try:
            clients.save_order(active_slug, результати, caption)
        except Exception as e:
            print(f"⚠️ Історія клієнта не збережена: {e}")


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
# АДМІН-СИСТЕМА
# ═══════════════════════════════════════════════════════════════════════════════
ADMIN_ID = 395121797  # @Bhltaraslev

PENDING_RULES_FILE = "pending_rules.json"
USAGE_LOG_FILE     = "usage_log.json"

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def load_pending_rules() -> list:
    if os.path.exists(PENDING_RULES_FILE):
        try:
            with open(PENDING_RULES_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_pending_rules(rules: list):
    with open(PENDING_RULES_FILE, "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)

def add_pending_rule(rule_text: str, user_id: int, username: str):
    rules = load_pending_rules()
    rules.append({
        "rule":     rule_text,
        "user_id":  user_id,
        "username": username or str(user_id),
        "date":     time.strftime("%Y-%m-%d %H:%M"),
    })
    save_pending_rules(rules)
    return len(rules)

def log_usage(chat_id: int, username: str, total: int, found: int, files: int):
    try:
        log = []
        if os.path.exists(USAGE_LOG_FILE):
            with open(USAGE_LOG_FILE, encoding="utf-8") as f:
                log = json.load(f)
        log.append({
            "chat_id":  chat_id,
            "username": username or str(chat_id),
            "date":     time.strftime("%Y-%m-%d %H:%M"),
            "total":    total,
            "found":    found,
            "files":    files,
        })
        log = log[-1000:]
        with open(USAGE_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ Лог не записано: {e}")

def get_usage_stats() -> str:
    if not os.path.exists(USAGE_LOG_FILE):
        return "📊 Статистика порожня — ще не було замовлень."
    try:
        with open(USAGE_LOG_FILE, encoding="utf-8") as f:
            log = json.load(f)
    except Exception:
        return "⚠️ Помилка читання логу."
    if not log:
        return "📊 Статистика порожня."

    users = {}
    for rec in log:
        u = rec.get("username", "?")
        if u not in users:
            users[u] = {"orders": 0, "total": 0, "found": 0}
        users[u]["orders"] += 1
        users[u]["total"]  += rec.get("total", 0)
        users[u]["found"]  += rec.get("found", 0)

    lines = [f"📊 *Статистика* (останні {len(log)} замовлень)\n"]
    for u, s in sorted(users.items(), key=lambda x: -x[1]["orders"]):
        pct = int(s["found"] / s["total"] * 100) if s["total"] else 0
        lines.append(f"👤 {u}: {s['orders']} замовл., {s['total']} позицій, знайдено {pct}%")

    lines.append("\n🕐 Останні 5:")
    for rec in log[-5:]:
        lines.append(f"• {rec['date']} — {rec['username']}: {rec['found']}/{rec['total']}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM ХЕНДЛЕРИ
# ═══════════════════════════════════════════════════════════════════════════════
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton

def main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(KeyboardButton("📸 Як користуватись"), KeyboardButton("🛑 Стоп"))
    kb.add(KeyboardButton("📋 Запропонувати правило"), KeyboardButton("📊 Кеш"))
    kb.add(KeyboardButton("👥 Кеш клієнта"), KeyboardButton("👥 Клієнти"))
    if is_admin(user_id):
        kb.add(KeyboardButton("👑 Статистика"), KeyboardButton("👑 Правила на розгляд"))
        kb.add(KeyboardButton("👑 Логи"))
    return kb


@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    admin_note = ""
    if is_admin(message.from_user.id):
        admin_note = """

👑 *Адмін-команди:*
`вірно 3` — рядок 3 з Excel підтверджено (завжди так підбирати)
`помилка 5` — рядок 5 неправильний (ніколи так не підбирати)
👑 Статистика / Логи / Правила на розгляд — кнопки внизу"""
    bot.reply_to(message, f"""👋 Привіт! Бот для підбору сантехніки.

📸 Кинь фото рукописного списку — знайду в базі
📝 *пошук <текст>* — текстовий запит
📋 *правило <текст>* — запропонувати нове правило
🛑 /stop — зупинити обробку

👤 *Клієнти:*
`новий клієнт Петренко` — створити профіль
`клієнт Петренко` — активувати (бот врахує його уподобання)
`клієнт стоп` — працювати без профілю
`клієнти` — список{admin_note}""",
        parse_mode="Markdown",
        reply_markup=main_keyboard(message.from_user.id))


@bot.message_handler(func=lambda m: m.text == "📸 Як користуватись")
def kb_howto(message):
    bot.reply_to(message, """📸 *Як користуватись:*

1. Напиши підказку з виробниками:
`пайка - екопластик
крани - рафтек
каналізація - асг`

2. Кинь фото списку від майстра

3. Отримай Excel з підібраними товарами""", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "🛑 Стоп")
def kb_stop(message):
    handle_stop(message)

@bot.message_handler(func=lambda m: m.text == "📊 Кеш")
def kb_cache(message):
    handle_cache_info(message)

@bot.message_handler(func=lambda m: m.text == "👥 Кеш клієнта")
def kb_client_cache(message):
    """Показує кеш активного клієнта."""
    slug = clients.get_active(message.chat.id)
    if not slug:
        bot.reply_to(message,
            "Немає активного клієнта.\nАктивуй: `клієнт <ім'я>`",
            parse_mode="Markdown")
        return
    profile = clients.get_profile(slug)
    name = profile['name'] if profile else slug
    cache = clients.get_client_cache(slug)
    if not cache:
        bot.reply_to(message,
            f"👥 Кеш клієнта *{name}* порожній — заповниться після замовлень.",
            parse_mode="Markdown")
        return
    # Останні 10 записів зі статусами
    recent = list(cache.items())[-10:]
    lines = []
    status_icons = {'confirmed': '✅', 'banned': '❌', 'auto': '🔹'}
    for key, val in recent:
        icon = status_icons.get(val.get('status', 'auto'), '🔹')
        orig = key[:35]
        cname = val.get('catalog_name', '')[:45]
        lines.append(f"{icon} `{orig}` → {cname}")
    bot.reply_to(message,
        f"👥 Кеш клієнта *{name}*: {len(cache)} записів\n"
        f"✅ підтверджено | ❌ заборонено | 🔹 авто\n\n" + "\n".join(lines),
        parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👥 Клієнти")
def kb_clients_list(message):
    handle_clients_list(message)

@bot.message_handler(func=lambda m: m.text == "📋 Запропонувати правило")
def kb_propose(message):
    bot.reply_to(message, "Напиши правило у форматі:\n`правило рожон = трійник редукційний`", parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👑 Статистика")
def kb_stats(message):
    if not is_admin(message.from_user.id):
        return
    bot.reply_to(message, get_usage_stats(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == "👑 Логи")
def kb_logs(message):
    if not is_admin(message.from_user.id):
        return
    if not os.path.exists(USAGE_LOG_FILE):
        bot.reply_to(message, "Лог порожній.")
        return
    with open(USAGE_LOG_FILE, "rb") as f:
        bot.send_document(message.chat.id, f, visible_file_name="usage_log.json")

@bot.message_handler(func=lambda m: m.text == "👑 Правила на розгляд")
def kb_pending(message):
    if not is_admin(message.from_user.id):
        return
    show_pending_rules(message.chat.id)


def show_pending_rules(chat_id: int):
    rules = load_pending_rules()
    if not rules:
        bot.send_message(chat_id, "✅ Немає правил на розгляд.")
        return
    for i, r in enumerate(rules):
        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Підтвердити", callback_data=f"approve_{i}"),
            InlineKeyboardButton("❌ Відхилити",   callback_data=f"reject_{i}"),
        )
        bot.send_message(chat_id,
            f"📋 Правило #{i+1} від {r['username']} ({r['date']}):\n\n`{r['rule']}`",
            parse_mode="Markdown", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith(('approve_', 'reject_')))
def handle_rule_decision(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "⛔ Тільки адмін")
        return
    action, idx = call.data.split('_')
    idx = int(idx)
    rules = load_pending_rules()
    if idx >= len(rules):
        bot.answer_callback_query(call.id, "Правило вже оброблено")
        return
    rule = rules.pop(idx)
    save_pending_rules(rules)
    if action == 'approve':
        add_rule(rule['rule'])
        bot.edit_message_text(
            f"✅ ПІДТВЕРДЖЕНО:\n`{rule['rule']}`",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
        try:
            bot.send_message(rule['user_id'], f"✅ Твоє правило підтверджено:\n`{rule['rule']}`", parse_mode="Markdown")
        except Exception:
            pass
    else:
        bot.edit_message_text(
            f"❌ ВІДХИЛЕНО:\n`{rule['rule']}`",
            call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.answer_callback_query(call.id, "Готово")


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('правило'))
def handle_rule(message):
    rule = message.text[7:].strip()
    if not rule:
        bot.reply_to(message, "Напиши правило після слова 'правило'.")
        return

    if is_admin(message.from_user.id):
        add_rule(rule)
        bot.reply_to(message, f"✅ Записав (адмін):\n_{rule}_", parse_mode="Markdown")
    else:
        n = add_pending_rule(rule, message.from_user.id, message.from_user.username)
        bot.reply_to(message,
            f"📥 Правило відправлено на розгляд адміну (в черзі: {n}):\n_{rule}_",
            parse_mode="Markdown")
        try:
            bot.send_message(ADMIN_ID,
                f"🔔 Нове правило від @{message.from_user.username or message.from_user.id}:\n`{rule}`",
                parse_mode="Markdown")
        except Exception:
            pass


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('забудь'))
def handle_forget(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Видаляти з кешу може тільки адмін.")
        return
    text = message.text[6:].strip()
    if not text:
        bot.reply_to(message, "Напиши що забути після слова 'забудь'.", parse_mode="Markdown")
        return
    deleted = cache_delete(text, {})
    if not deleted:
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
            bot.reply_to(message, f"✅ Видалено {len(to_delete)} запис(ів)", parse_mode="Markdown")
        else:
            bot.reply_to(message, f"⚠️ Не знайдено в кеші", parse_mode="Markdown")
    else:
        bot.reply_to(message, f"✅ Видалено з кешу", parse_mode="Markdown")


@bot.message_handler(commands=['кеш', 'cache'])
def handle_cache_info(message):
    total = len(_CACHE)
    if total == 0:
        bot.reply_to(message, "📋 Кеш порожній — заповниться після перших замовлень.")
        return
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


@bot.message_handler(commands=['статистика', 'stats'])
def handle_stats(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Статистика доступна тільки адміну.")
        return
    bot.reply_to(message, get_usage_stats(), parse_mode="Markdown")


@bot.message_handler(commands=['логи', 'logs'])
def handle_logs(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Логи доступні тільки адміну.")
        return
    if not os.path.exists(USAGE_LOG_FILE):
        bot.reply_to(message, "Лог порожній.")
        return
    with open(USAGE_LOG_FILE, "rb") as f:
        bot.send_document(message.chat.id, f, visible_file_name="usage_log.json")


@bot.message_handler(commands=['правила', 'pending'])
def handle_pending(message):
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Тільки адмін.")
        return
    show_pending_rules(message.chat.id)


# ═══════════════════════════════════════════════════════════════════════════════
# КОМАНДИ АДМІНА: ПОЗНАЧЕННЯ ПРАВИЛЬНО/НЕПРАВИЛЬНО
# ═══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: m.text and re.match(r'^(вірно|помилка)\s+\d+', m.text.lower().strip()))
def handle_mark_result(message):
    """
    вірно 3   → рядок 3 останнього Excel позначається confirmed (✅)
    помилка 5 → рядок 5 позначається banned (❌, бот більше не видасть цю пару)
    Тільки адмін. Нумерація = номер рядка в листі 'Замовлення' (з 1).
    """
    if not is_admin(message.from_user.id):
        bot.reply_to(message, "⛔ Позначати результати може тільки адмін.")
        return

    m = re.match(r'^(вірно|помилка)\s+(\d+)', message.text.lower().strip())
    action, row_num = m.group(1), int(m.group(2))

    last = last_results.get(message.chat.id)
    if not last or not last['результати']:
        bot.reply_to(message, "⚠️ Немає останнього замовлення в пам'яті. Спочатку оброби фото.")
        return

    результати = last['результати']
    if row_num < 1 or row_num > len(результати):
        bot.reply_to(message, f"⚠️ Рядок {row_num} не існує. В останньому замовленні {len(результати)} позицій.")
        return

    r = результати[row_num - 1]
    original = r.get('original', '')
    catalog_name = r.get('назва', '')
    category = r.get('category', 'other')
    client_slug = last.get('client_slug')

    if action == 'вірно':
        # confirmed у загальному кеші + кеші клієнта
        updated = cache_set_status(original, catalog_name, 'confirmed')
        if not updated:
            # Запису ще немає — створюємо confirmed напряму
            cache_save(original, {}, r.get('normalized', original), catalog_name, category, 100)
            cache_set_status(original, catalog_name, 'confirmed')
        if client_slug:
            clients.client_cache_set_status(client_slug, original, catalog_name, 'confirmed')
        bot.reply_to(message,
            f"✅ Підтверджено (рядок {row_num}):\n`{original}` → {catalog_name[:60]}\n"
            f"Бот завжди видаватиме цей товар для цього запиту.",
            parse_mode="Markdown")
    else:
        # banned — бот ніколи не видасть цю пару
        cache_ban_pair(original, catalog_name, category)
        if client_slug:
            clients.client_cache_set_status(client_slug, original, catalog_name, 'banned')
        bot.reply_to(message,
            f"❌ Забанено (рядок {row_num}):\n`{original}` → {catalog_name[:60]}\n"
            f"Бот більше ніколи не видасть цей товар для цього запиту.",
            parse_mode="Markdown")


# ═══════════════════════════════════════════════════════════════════════════════
# КОМАНДИ КЛІЄНТІВ
# ═══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('новий клієнт'))
def handle_new_client(message):
    """новий клієнт Петренко, бере тільки рафтек"""
    rest = message.text[12:].strip()
    if not rest:
        bot.reply_to(message,
            "Формат: `новий клієнт Петренко, бере тільки рафтек`\n(примітка через кому — опційно)",
            parse_mode="Markdown")
        return
    parts = rest.split(',', 1)
    name = parts[0].strip()
    notes = parts[1].strip() if len(parts) > 1 else ""
    ok, result = clients.create_client(name, notes)
    if ok:
        clients.set_active(message.chat.id, result)
        bot.reply_to(message,
            f"✅ Створено профіль клієнта *{name}* і активовано.\nКидай фото!",
            parse_mode="Markdown")
    else:
        bot.reply_to(message, f"⚠️ {result}")


@bot.message_handler(func=lambda m: m.text and m.text.lower().strip() == 'клієнти')
def handle_clients_list(message):
    """Список всіх клієнтів."""
    index = clients.list_clients()
    if not index:
        bot.reply_to(message, "📁 Немає жодного клієнта.\nСтворити: `новий клієнт <ім'я>`", parse_mode="Markdown")
        return
    lines = []
    for slug, name in sorted(index.items(), key=lambda x: x[1]):
        profile = clients.get_profile(slug)
        orders = profile.get('orders_count', 0) if profile else 0
        lines.append(f"• {name} ({orders} замовл.)")
    bot.reply_to(message, f"📁 Клієнти ({len(index)}):\n" + "\n".join(lines))


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('клієнт'))
def handle_client(message):
    """
    клієнт            → показати активного
    клієнт Петренко   → активувати
    клієнт стоп       → скинути
    клієнт Петренко: примітка → додати примітку
    """
    rest = message.text[6:].strip()

    # Без аргументів — показати активного
    if not rest:
        slug = clients.get_active(message.chat.id)
        if slug:
            profile = clients.get_profile(slug)
            name = profile['name'] if profile else slug
            bot.reply_to(message, f"👤 Активний клієнт: *{name}*\nСкинути: `клієнт стоп`", parse_mode="Markdown")
        else:
            bot.reply_to(message, "Немає активного клієнта.\nАктивувати: `клієнт <ім'я>`", parse_mode="Markdown")
        return

    # Скидання
    if rest.lower() in ('стоп', 'скинути', 'вихід', 'off'):
        clients.clear_active(message.chat.id)
        bot.reply_to(message, "✅ Клієнта скинуто. Працюємо без профілю.")
        return

    # Примітка: клієнт Ім'я: текст
    if ':' in rest:
        name_part, note = rest.split(':', 1)
        slug = clients.find_client(name_part.strip())
        if not slug:
            bot.reply_to(message, f"⚠️ Клієнта '{name_part.strip()}' не знайдено.")
            return
        clients.add_note(slug, note.strip())
        bot.reply_to(message, f"✅ Примітку додано до профілю.")
        return

    # Активація
    slug = clients.find_client(rest)
    if not slug:
        bot.reply_to(message,
            f"⚠️ Клієнта '{rest}' не знайдено.\nСтворити: `новий клієнт {rest}`",
            parse_mode="Markdown")
        return

    clients.set_active(message.chat.id, slug)
    profile = clients.get_profile(slug)
    prefs = clients.get_preferences(slug)

    top = prefs.get('top_brands', [])[:3]
    brands_str = ", ".join(f"{b}" for b, c in top) if top else "ще немає даних"
    notes = profile.get('notes', []) if profile else []
    notes_str = "\n".join(f"  • {n}" for n in notes[-3:] if n) if notes else "  —"

    bot.reply_to(message,
        f"✅ Активовано: *{profile['name'] if profile else slug}*\n"
        f"📦 Замовлень: {profile.get('orders_count', 0) if profile else 0}\n"
        f"🏷 Топ виробники: {brands_str}\n"
        f"📝 Примітки:\n{notes_str}\n\n"
        f"Кидай фото — бот врахує уподобання клієнта!",
        parse_mode="Markdown")


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
                'type': 'photo', 'data': image_b64, 'caption': full_caption,
                'username': message.from_user.username or str(message.from_user.id),
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
        add_to_batch(message.chat.id, {
            'type': 'text', 'text': запит,
            'username': message.from_user.username or str(message.from_user.id),
        })
    else:
        bot.reply_to(message, "Напиши запит після слова 'пошук'.")


@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/')
                     and not m.text.lower().startswith('пошук')
                     and not m.text.lower().startswith('правило')
                     and not m.text.lower().startswith('забудь')
                     and not m.text.lower().startswith('клієнт')
                     and not m.text.lower().startswith('новий клієнт')
                     and not re.match(r'^(вірно|помилка)\s+\d+', m.text.lower().strip())
                     and not m.text.startswith('📸')
                     and not m.text.startswith('🛑')
                     and not m.text.startswith('📋')
                     and not m.text.startswith('📊')
                     and not m.text.startswith('👥')
                     and not m.text.startswith('👑'))
def handle_text_hint(message):
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



# ═══════════════════════════════════════════════════════════════════════════════
# FLASK APP — на рівні модуля (працює і з gunicorn і з python bot.py)
# ═══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def tg_webhook():
    update = Update.de_json(flask_request.stream.read().decode("utf-8"))
    bot.process_new_updates([update])
    return "ok", 200

@app.route("/", methods=["GET"])
def tg_index():
    return f"🤖 Бот працює | Каталог: {len(CATALOG)} | Кеш: {len(get_cache())}", 200

@app.route("/health", methods=["GET"])
def tg_health():
    return "ok", 200


# ═══════════════════════════════════════════════════════════════════════════════
# СТАРТ
# ═══════════════════════════════════════════════════════════════════════════════
_WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")
_PORT = int(os.environ.get("PORT", 10000))

if _WEBHOOK_URL:
    try:
        bot.remove_webhook()
        time.sleep(1)
        _wh = f"{_WEBHOOK_URL}/webhook"
        bot.set_webhook(url=_wh)
        print(f"✅ Webhook встановлено: {_wh}")
    except Exception as e:
        print(f"⚠️ Webhook помилка: {e}")

if __name__ == "__main__":
    if _WEBHOOK_URL:
        print(f"🤖 Запуск Flask на порту {_PORT}...")
        app.run(host="0.0.0.0", port=_PORT, debug=False, use_reloader=False)
    else:
        print("🔄 Polling режим (локальний)...")
        bot.polling(none_stop=True)
