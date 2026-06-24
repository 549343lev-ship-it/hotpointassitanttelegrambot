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
# КРОК 1: БАЗА ЗНАНЬ САНТЕХНІКИ
# ═══════════════════════════════════════════════════════════════════════════════
ЗНАННЯ_САНТЕХНІКИ = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
КАНАЛІЗАЦІЯ (HTR/ASG або HT Safe/OSTENDORF):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ⚠️  90° у запиті → завжди шукати 87,5° в каталозі!
  умивальник = ф40 | ванна/душ = ф50 | унітаз/стояк = ф110
  ASG / HTR          → сіра, каталог: HTR, ASG
  OSTENDORF / HT Safe → сіра, каталог: HT Safe
  S-LINE             → біла, безшумна

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PPR (ASG, RAFTEC, Wavin Ekoplastik):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PN20 / Faser HOT         → гаряча вода і опалення
  PN25 / Nano Ag Composite → армована скловолокном
  Stabi                    → тришарова, перфорована фольга
  Композитна               → тришарова, цільна фольга (менше розширення)

ФІТИНГИ PPR — АБРЕВІАТУРИ (з'єднання з латунною різьбою):
  МРЗ / МРН = Муфта різьбова зовнішня  (МРН — з рос. "наружная")
  МРВ       = Муфта різьбова внутрішня
  КРЗ / КРН = Коліно різьбове зовнішнє (КРН — з рос. "наружная")
  КРВ       = Коліно різьбове внутрішнє
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

  УТЕПЛЮВАЧ:
  +ізол              = додати утеплювач PLM (синій і червоний)
  ізол ф22           = PLM ф22х6мм
  ізол ф28           = PLM ф28х6мм
  гачки / хомут для труб = Дюбель гак подвійний Penoroll

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ФОРМАТ НОРМАЛІЗОВАНИХ НАЗВ (приклади БЕЗ прив'язки до виробника):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Труба PPR ф25 PN25           → "Труба PPR Nano Ag Composite, ф 25x4,2 мм, PP-RCT, PN25, {ВИРОБНИК}"
  Труба PPR ф20 PN20           → "Труба PPR Faser HOT ф 20х2,8 мм, PN20, PP-RCT, {ВИРОБНИК}"
  Коліно 25 90°                → "Коліно PPR 90° ф 25, PP-RCT, {ВИРОБНИК}"
  Трійник 25х20х25             → "Трійник редукційний PPR ф 25х20, PP-RCT, {ВИРОБНИК}"
  Труба канал ф50 1м сіра      → "Труба внут. канал. ф 50 х 1,8 мм, L = 1 м., сіра, HTR, {ВИРОБНИК}"
  Коліно канал 110 90°         → "Коліно внут. канал. ф110 х 87,5°, сіре, HTR, {ВИРОБНИК}"
  Трійник канал 110х50 87,5°   → "Трійник вн. канал. ф110 х 50 х 87,5°, сірий, HTR, {ВИРОБНИК}"
  Заглушка PPR ф25             → "Заглушка PPR ф 25, {ВИРОБНИК}"
  Утеплювач ф28 синій PLM      → "Утеплювач ламін. для труб ф 28х6 мм, синій, PLM"
  Вазелін                      → "Технічний вазелин вн. канал. 150 гр., Valrom"
  МРЗ 25*3/4                   → "Муфта PPR різьбова зовнішня ф 25 - 3/4, {ВИРОБНИК}"
  КРВ 25*1/2                   → "Коліно PPR різьбове внутрішнє ф 25 - 1/2, {ВИРОБНИК}"
  Перехідник 20*1/2 push       → шукати в категорії push_systems або metal_plastic

  де {ВИРОБНИК} = те що вказав менеджер у підказці для цієї категорії.
  Якщо виробника НЕ вказано — не пиши нічого замість {ВИРОБНИК}.

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
    # Опалення / радіаторна арматура
    'опалення':   'heating',
    'байпас':     'heating',
}

# Відомі виробники — токени для жорсткого фільтру і підстановки в normalized
BRAND_TOKENS = {
    'ekoplastik':  ['ekoplastik', 'екопластик', 'wavin'],
    'екопластик':  ['ekoplastik', 'екопластик', 'wavin'],
    'wavin':       ['ekoplastik', 'екопластик', 'wavin'],
    'raftec':      ['raftec', 'рафтек', 'Raftec', 'RAFTEC'],
    'рафтек':      ['raftec', 'рафтек', 'Raftec', 'RAFTEC'],
    'asg':         ['asg', 'асг', 'ASG'],
    'асг':         ['asg', 'асг', 'ASG'],
    'ostendorf':   ['ostendorf', 'остендорф'],
    'остендорф':   ['ostendorf', 'остендорф'],
    'valrom':      ['valrom', 'валром'],
    'unipak':      ['unipak', 'юніпак'],
    'plm':         ['plm', 'плм'],
    'hydros':      ['hydros', 'гідрос', 'Hydros'],
    'гідрос':      ['hydros', 'гідрос', 'Hydros'],
    'giacomini':   ['giacomini', 'джакоміні'],
    'purmo':       ['purmo', 'пурмо'],
    'пурмо':       ['purmo', 'пурмо'],
    'kan':         ['kan', 'кан'],
    'fado':        ['fado', 'фадо'],
    'gross':       ['gross', 'гросс'],
    'hummel':      ['hummel', 'хуммель'],
    'herz':        ['herz', 'херц'],
    'danfoss':     ['danfoss', 'данфос'],
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


def filter_by_brand(candidates: list[dict], brand_tokens: list[str]) -> list[dict]:
    """
    Фільтрує кандидатів — залишає тільки ті, де назва містить хоча б один токен виробника.
    Якщо після фільтрації список порожній — повертає оригінальний (щоб не втратити товар).
    """
    filtered = [
        c for c in candidates
        if any(tok in c['name'].lower() for tok in brand_tokens)
    ]
    return filtered if filtered else candidates  # fallback — краще знайти щось


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
def claude_pick_batch(позиції_з_кандидатами: list[dict]) -> list[dict]:
    """
    Один запит до Claude на весь батч.
    Повертає: знайдено, номер_кандидата, confidence (0-100).
    """
    запити = []
    for i, пос in enumerate(позиції_з_кандидатами):
        # Якщо є жорсткий фільтр виробника — додаємо в запит
        brand_note = ""
        if пос.get('required_brand'):
            brand_note = f"\n   ⚠️ ОБОВ'ЯЗКОВИЙ ВИРОБНИК: {пос['required_brand']} — вибирай ТІЛЬКИ з цим виробником!"
        кандидати = "\n".join(
            f"  {j+1}. {c['name']}"
            for j, c in enumerate(пос['candidates'])
        )
        запити.append(
            f"{i+1}. ЗАПИТ: {пос['normalized']}{brand_note}\n   КАНДИДАТИ:\n{кандидати}"
        )

    prompt = f"""Ти — експерт із сантехніки. Для кожного запиту обери ОДИН найкращий збіг з кандидатів.

{chr(10).join(запити)}

Правила вибору:
1. Діаметр ОБОВ'ЯЗКОВО повинен збігатися
2. Кут/довжина — якщо вказано, повинні збігатися
3. Якщо вказано ОБОВ'ЯЗКОВИЙ ВИРОБНИК — обирай тільки його, навіть якщо інший збігається краще за параметрами
4. confidence: 90-100 = точний збіг, 70-89 = майже точний, 50-69 = схожий, <50 = сумнівний
5. Якщо жоден не підходить — знайдено: false

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом (порядок як у запитах):
[
  {{"знайдено": true, "номер_кандидата": 1, "confidence": 95}},
  {{"знайдено": false, "confidence": 0}}
]"""

    resp = claude.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
    try:
        if '[' in raw and ']' in raw:
            raw = raw[raw.index('['):raw.rindex(']')+1]
        return json.loads(raw)
    except Exception:
        return [{"знайдено": False, "confidence": 0}] * len(позиції_з_кандидатами)


def find_items(позиції: list[dict]) -> list[dict]:
    """
    Головна функція пошуку.
    1. keyword_search → топ-8 кандидатів
    2. filter_by_brand → залишаємо тільки потрібного виробника (якщо вказано)
    3. claude_pick_batch → фінальний вибір + confidence
    """
    потребують_claude = []
    результати = [None] * len(позиції)

    for i, пос in enumerate(позиції):
        normalized = пос.get('normalized', '')
        category   = пос.get('category', 'other')
        brand_map  = пос.get('_brand_map', {})

        кандидати = keyword_search(normalized, top_n=12)

        if not кандидати:
            результати[i] = {**пос, 'знайдено': False, 'назва': '', 'бренд': '', 'confidence': 0}
            continue

        # Жорсткий фільтр по виробнику якщо менеджер вказав
        required_brand = None
        brand_tokens_for_filter = brand_map.get(category)
        if brand_tokens_for_filter:
            кандидати = filter_by_brand(кандидати, brand_tokens_for_filter)
            required_brand = brand_tokens_for_filter[0]  # для підказки Claude

        потребують_claude.append({
            'idx':            i,
            'normalized':     normalized,
            'candidates':     кандидати,
            'qty':            пос.get('qty', ''),
            'original':       пос.get('original', ''),
            'required_brand': required_brand,
        })

    if потребують_claude:
        відповіді = claude_pick_batch(потребують_claude)
        for j, пос in enumerate(потребують_claude):
            r = відповіді[j] if j < len(відповіді) else {'знайдено': False, 'confidence': 0}
            idx = пос['idx']
            confidence = int(r.get('confidence', 0))
            if r.get('знайдено') and r.get('номер_кандидата'):
                n = int(r['номер_кандидата']) - 1
                n = max(0, min(n, len(пос['candidates'])-1))
                found = пос['candidates'][n]
                результати[idx] = {
                    'original':   пос['original'],
                    'normalized': пос['normalized'],
                    'знайдено':   True,
                    'назва':      found['name'],
                    'бренд':      found.get('brand', ''),
                    'ціна':       found.get('price', ''),
                    'qty':        пос['qty'],
                    'confidence': confidence,
                }
            else:
                результати[idx] = {
                    'original':   пос['original'],
                    'normalized': пос['normalized'],
                    'знайдено':   False,
                    'назва':      '',
                    'qty':        пос['qty'],
                    'confidence': confidence,
                }

    return результати


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL
# ═══════════════════════════════════════════════════════════════════════════════
def create_excel(результати: list[dict]) -> tuple[BytesIO, list[str]]:
    rows = []
    not_found = []
    low_confidence = []  # знайдено але впевненість < 70

    for r in результати:
        if r and r.get('знайдено'):
            conf = r.get('confidence', 0)
            conf_label = f"{conf}%" if conf else "?"
            rows.append({
                'Наименование': r.get('назва', ''),
                'Кількість':    r.get('qty', ''),
                'Ціна':         r.get('ціна', ''),
                'Впевненість':  conf_label,
                'Оригінал':     r.get('original', ''),
            })
            if conf < 70:
                low_confidence.append(f"{r.get('original','')} → {r.get('назва','')} ({conf_label})")
        elif r:
            not_found.append(r.get('normalized') or r.get('original', ''))

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df = pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=['Наименование', 'Кількість', 'Ціна', 'Впевненість', 'Оригінал'])
        df.to_excel(writer, index=False, sheet_name='Замовлення')
        if not_found:
            pd.DataFrame({'Не знайдено': not_found}).to_excel(
                writer, index=False, sheet_name='Не знайдено')
        if low_confidence:
            pd.DataFrame({'Низька впевненість (перевір)': low_confidence}).to_excel(
                writer, index=False, sheet_name='Перевір')

    output.seek(0)
    return output, not_found, low_confidence


# ═══════════════════════════════════════════════════════════════════════════════
# БАТЧ-МЕНЕДЖЕР
# ═══════════════════════════════════════════════════════════════════════════════
user_batches  = {}
stop_flags    = {}
pending_hints = {}

def process_batch(chat_id: int):
    batch = user_batches.pop(chat_id, None)
    if not batch:
        return

    stop_flags.pop(chat_id, None)
    items = batch['items']
    status = bot.send_message(chat_id, f"🔄 Обробляю {len(items)} файл(ів)...")
    msg_id = status.message_id

    всі_позиції = []
    errors = []

    for idx, item in enumerate(items, 1):
        if stop_flags.get(chat_id):
            bot.edit_message_text("🛑 Зупинено.", chat_id=chat_id, message_id=msg_id)
            return
        try:
            if item['type'] == 'photo':
                bot.edit_message_text(
                    f"📖 Читаю фото {idx}/{len(items)}...",
                    chat_id=chat_id, message_id=msg_id)
                позиції = normalize_photo(item['data'], item.get('caption', ''))
                всі_позиції.extend(позиції)
            elif item['type'] == 'text':
                bot.edit_message_text(
                    f"📝 Нормалізую текст...",
                    chat_id=chat_id, message_id=msg_id)
                позиції = normalize_text(item['text'], item.get('caption', ''))
                всі_позиції.extend(позиції)
        except Exception as e:
            errors.append(f"❌ Помилка {idx}: {e}")

    if not всі_позиції:
        bot.edit_message_text(
            "😕 Не вдалося розпізнати позиції.\n" + "\n".join(errors),
            chat_id=chat_id, message_id=msg_id)
        return

    preview = "\n".join(
        f"• {п.get('original','')} → {п.get('normalized','')} ({п.get('qty','')})"
        for п in всі_позиції[:8]
    )
    if len(всі_позиції) > 8:
        preview += f"\n... та ще {len(всі_позиції)-8}"
    bot.send_message(chat_id, f"✅ Розпізнано {len(всі_позиції)} позицій:\n\n{preview}")

    bot.edit_message_text(
        f"🔍 Шукаю {len(всі_позиції)} позицій у базі...",
        chat_id=chat_id, message_id=msg_id)

    try:
        результати = find_items(всі_позиції)
    except Exception as e:
        bot.edit_message_text(f"❌ Помилка пошуку: {e}", chat_id=chat_id, message_id=msg_id)
        return

    bot.edit_message_text("📊 Формую Excel...", chat_id=chat_id, message_id=msg_id)
    excel, not_found, low_confidence = create_excel(результати)

    знайдено = [r for r in результати if r and r.get('знайдено')]
    bot.send_document(chat_id, excel, visible_file_name="замовлення.xlsx")

    звіт = f"✅ Знайдено: {len(знайдено)}/{len(результати)} позицій"
    if not_found:
        звіт += f"\n⚠️ Не знайдено ({len(not_found)} шт.):\n"
        звіт += "\n".join(f"• {n}" for n in not_found[:5])
        if len(not_found) > 5:
            звіт += f"\n... та ще {len(not_found)-5}"
    if low_confidence:
        звіт += f"\n\n🔶 Низька впевненість — перевір вручну ({len(low_confidence)} шт.):\n"
        звіт += "\n".join(f"• {n}" for n in low_confidence[:3])
        if len(low_confidence) > 3:
            звіт += f"\n... та ще {len(low_confidence)-3} (див. лист 'Перевір' у файлі)"
    if errors:
        звіт += "\n\n" + "\n".join(errors)

    bot.edit_message_text(звіт, chat_id=chat_id, message_id=msg_id)


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
🛑 /stop — зупинити обробку

Приклад: `правило рожон = трійник редукційний`""", parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('правило'))
def handle_rule(message):
    rule = message.text[7:].strip()
    if rule:
        add_rule(rule)
        bot.reply_to(message, f"✅ Записав:\n_{rule}_", parse_mode="Markdown")
    else:
        bot.reply_to(message, "Напиши правило після слова 'правило'.")


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
