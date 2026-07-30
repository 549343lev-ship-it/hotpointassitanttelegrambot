"""
catalog/catalog.py — Каталог товарів.

Завантажує xlsx-прайси з папки prices/ → будує список товарів → індексує токени.
Singleton: будується один раз при старті і живе в пам'яті.
"""

import os
import re
import json
import pandas as pd

DATA_DIR     = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
CATALOG_PATH = os.path.join(DATA_DIR, "catalog.json")   # кешований каталог на диску

CATALOG_FILES = [   # пари (ім'я файлу без .xlsx, назва категорії)
    ("adapters_reducers",          "adapters_reducers"),
    ("automation",                 "automation"),
    ("boilers",                    "boilers"),
    ("fasteners_sealants",         "fasteners_sealants"),
    ("filtration",                 "filtration"),
    ("heating",                    "heating"),
    ("hoses",                      "hoses"),
    ("insulation",                 "insulation"),
    ("metal_plastic",              "metal_plastic"),
    ("mixers_faucets",             "mixers_faucets"),
    ("plastic_ppr",                "plastic_ppr"),
    ("pumps",                      "pumps"),
    ("push_systems",               "push_systems"),
    ("radiators_radiatorsvalve",   "radiators_radiatorsvalve"),
    ("safety_valves",              "safety_valves"),
    ("sanitary_ware",              "sanitary_ware"),
    ("sewage",                     "sewage"),
    ("shutoff_valves",             "shutoff_valves"),
    ("siphons_fittings",           "siphons_fittings"),
    ("towel_warmers",              "towel_warmers"),
    ("underfloor_heating",         "underfloor_heating"),
    ("water_heaters",              "water_heaters"),
    ("water_meters",               "water_meters"),
]

CATALOG: list[dict] = []   # глобальний список всіх товарів
_tokens_built = False      # прапорець індексації


# ─── Побудова каталогу ───────────────────────────────────────────────────────

def _is_header_row(name, artikul, or_val) -> bool:  # визначає чи є рядок xlsx заголовком або порожнім
    name = str(name).strip()
    if not name or name == 'nan':
        return True
    art = str(artikul).strip()
    if art and art not in ('nan', '0', ''):
        return False
    try:
        if float(or_val) > 0:
            return False
    except Exception:
        pass
    return True


def build_catalog_from_xlsx() -> list[dict]:    # читає всі xlsx з prices/ і збирає єдиний список товарів
    catalog  = []
    src_dir  = os.path.dirname(os.path.abspath(__file__))   # data/
    root_dir = os.path.dirname(src_dir)                      # корінь проекту
    search_dirs = [
        os.path.join(root_dir, 'prices'),   # абсолютний шлях до prices/
        'prices',                            # відносний від робочої директорії
        root_dir,                            # корінь проекту
        '.',                                 # поточна директорія
    ]

    for key, category in CATALOG_FILES:
        found = False
        for d in search_dirs:
            path = os.path.join(d, f"{key}.xlsx")
            if not os.path.exists(path):
                continue
            try:
                df   = pd.read_excel(path, header=0)
                cols = list(df.columns)
                rename = {}
                if len(cols) >= 1: rename[cols[0]] = 'name'
                if len(cols) >= 2: rename[cols[1]] = 'artikul'
                if len(cols) >= 3: rename[cols[2]] = 'price'
                df    = df.rename(columns=rename)
                count = 0
                for _, row in df.iterrows():
                    name    = str(row.get('name', '')).strip()
                    artikul = row.get('artikul', '')
                    price   = row.get('price', 0)
                    if _is_header_row(name, artikul, price):
                        continue
                    try:
                        p = float(price)
                    except Exception:
                        p = 0.0
                    art        = str(artikul).strip()
                    name_full  = name
                    name_clean = re.sub(r'\s*\{[^}]+\}', '', name).strip()
                    catalog.append({
                        'name':      name_clean,
                        'name_full': name_full,
                        'artikul':   art if art != 'nan' else '',
                        'category':  category,
                        'price':     p,
                    })
                    count += 1
                print(f"  ✅ {key}: {count} товарів")
                found = True
                break
            except Exception as e:
                print(f"  ❌ {key}: {e}")
        if not found:
            print(f"  ⚠️ {key}.xlsx не знайдено")
    return catalog


def load_catalog():     # завантажує каталог з JSON-кешу або будує заново з xlsx
    global CATALOG
    if os.path.exists(CATALOG_PATH):
        with open(CATALOG_PATH, encoding="utf-8") as f:
            CATALOG = json.load(f)
        print(f"✅ Каталог: {len(CATALOG)} позицій", flush=True)
    else:
        print("⚙️ Будую catalog.json...", flush=True)
        CATALOG = build_catalog_from_xlsx()
        if CATALOG:
            with open(CATALOG_PATH, "w", encoding="utf-8") as f:
                json.dump(CATALOG, f, ensure_ascii=False)
            print(f"✅ Збережено: {len(CATALOG)} позицій", flush=True)

    before  = len(CATALOG)
    CATALOG = [c for c in CATALOG if 'виведено з асортименту' not in c.get('name', '').lower()]
    if len(CATALOG) < before:
        print(f"🧹 Видалено {before - len(CATALOG)} виведених", flush=True)


# ─── Токенізація ─────────────────────────────────────────────────────────────

def tokenize(text: str) -> set:     # перетворює назву на набір токенів (нормалізує розміри, різьби, кути)
    t = text.lower()
    t = re.sub(r'(\d+)/(\d+)', r'\1_\2', t)
    # Хомути: діапазон діаметрів "ф15-19мм" → найближчий стандартний діаметр труби
    # ф108-114мм → 111 → стандарт 110 (хомут для труби ф110)
    # ф15-19мм   → 17  → стандарт 16  (хомут для труби ф16)
    _hm = re.search(r'[фf]\s*(\d+)\s*[-–]\s*(\d+)\s*мм', t)
    if _hm:
        _d1, _d2 = int(_hm.group(1)), int(_hm.group(2))
        _mid = (_d1 + _d2) // 2
        _stds = [16, 20, 25, 32, 40, 50, 63, 75, 90, 110, 125, 160, 200]
        _std = min(_stds, key=lambda x: abs(x - _mid))
        t = re.sub(r'[фf]\s*\d+\s*[-–]\s*\d+\s*мм', f' {_std} ', t)

    t = re.sub(r'[фfдd]\s*(\d)', r'\1', t)
    t = re.sub(r'(\d+)\s*[хxX×]\s*(\d+)/(\d+)', r'\1 \2_\3', t)

    def strip_thick(m):     # прибирає товщину стінки якщо менша 15мм
        d1, d2 = m.group(1), m.group(2)
        if '_' in d2:
            return m.group(0)
        try:
            if float(d2.replace(',', '.')) < 15:
                return d1
        except Exception:
            pass
        return m.group(0)

    t = re.sub(r'(\d+)\s*[хxX×]\s*(\d+(?:[.,]\d+)?)(?![\d_])', strip_thick, t)
    t = re.sub(r'(8[67])[.,]5', r'\1', t)

    # Зберігаємо довжини труб у форматі "l=3,0" або "l = 0,5" → токен "l300", "l50"
    # Перетворюємо дробові довжини на цілі (метри × 100): 3.0м → "l300", 0.5м → "l50"
    def encode_length(m):   # кодує довжину труби в мм: L=3м→3000, L=0,5м→500, L=1,5м→1500
        # Використовуємо міліметри щоб не конфліктувало з діаметрами (max 630мм)
        # L=0,5м=500мм, L=1м=1000мм, L=3м=3000мм — завжди більше за будь-який діаметр в мм
        raw = m.group(1).replace(',', '.')
        val = float(raw)
        mm  = int(round(val * 1000))
        return f" {mm} "

    # Ловимо L= і з дробовими (L=3,0м) і з цілими (L=3м) значеннями
    t = re.sub(r'l\s*[=:]?\s*(\d+[.,]\d+)\s*м?', encode_length, t)       # L=0,5м L=3,0м
    t = re.sub(r'l\s*[=:]?\s*(\d+)\s*м', lambda m: f" {int(m.group(1))*1000} ", t)  # L=3м L=1м

    # Зберігаємо вн/зовн як окремі токени (важливо для розрізнення внутрішніх/зовнішніх виробів)
    t = re.sub(r'\bвн\b', ' vn ', t)      # вн → vn
    t = re.sub(r'\bзовн\b', ' zovn ', t)  # зовн → zovn

    # Безшумна vs звичайна каналізація — КРИТИЧНО різні товари!
    # ТІЛЬКИ для труб! Фітинги (трійники, коліна, заглушки) не маркуємо —
    # бо запит від Gemini зазвичай не містить htr/sline для фітингів
    _is_pipe = re.search(r'\bтруба\b|\bpipe\b', t)
    if re.search(r'безшум|s.line|silent|sline', t):
        t = t + ' sline '   # безшумна (S-LINE, біла)
    elif _is_pipe and re.search(r'\bhtr\b|\bhtsafe\b|ht.safe|сіра|htr', t):
        t = t + ' htr '     # звичайна труба HTR/HT Safe

    # МРЗ/МРВ/РН/РЗ/РВ → окремі токени (зовнішня vs внутрішня різьба — різні товари!)
    t = re.sub(r'\bмрз\b', ' mrz ', t)    # МРЗ = муфта різьба зовнішня
    t = re.sub(r'\bмрв\b', ' mrv ', t)    # МРВ = муфта різьба внутрішня
    t = re.sub(r'\bрн\b',  ' rn ',  t)    # РН  = різьба нейтральна/накидна
    t = re.sub(r'\bрз\b',  ' rz ',  t)    # РЗ  = різьба зовнішня
    t = re.sub(r'\bрв\b',  ' rv ',  t)    # РВ  = різьба внутрішня
    t = re.sub(r'\bвв\b',  ' vv ',  t)    # ВВ  = внутр-внутр
    t = re.sub(r'\bвз\b',  ' vz ',  t)    # ВЗ  = внутр-зовн
    t = re.sub(r'\bзв\b',  ' zv ',  t)    # ЗВ  = зовн-внутр
    t = re.sub(r'\bзз\b',  ' zz ',  t)    # ЗЗ  = зовн-зовн

    # Для трійників зберігаємо впорядкований рядок діаметрів як окремий токен
    # ф25х16х25 → "t_25_16_25" ; ф25х16х16 → "t_25_16_16" — різні токени!
    tm = re.search(r'(\d{2,3})[хx×](\d{2,3})[хx×](\d{2,3})', t)
    if tm:
        d1, d2, d3 = tm.group(1), tm.group(2), tm.group(3)
        t = t + f' tdim_{d1}_{d2}_{d3} '   # впорядкований токен трійника

    # Видаляємо решту дробових (товщини стінок: 3.2мм, 1.8мм) — довжини вже закодовані вище
    t = re.sub(r'(?<![0-9_])\d+[.,]\d+(?![0-9_])', '', t)

    return set(re.findall(r'[а-яёіїєґa-z]+|[0-9]+_[0-9]+|[0-9]+', t))


def ensure_tokens():    # лінива індексація токенів і атрибутів для всіх товарів при першому пошуку
    global _tokens_built
    if not _tokens_built:
        from engine.search import parse_attrs   # lazy import щоб уникнути циклічного імпорту catalog↔search
        print("🔨 Індексую токени...", flush=True)
        for item in CATALOG:
            item['_tokens'] = tokenize(item['name'])
            item['_attrs']  = parse_attrs(item['name'])
        print("✅ Індексація завершена", flush=True)
        _tokens_built = True


# ─── Ініціалізація ───────────────────────────────────────────────────────────
print("📦 Завантажую каталог...", flush=True)
load_catalog()

# Voyage AI embeddings — тільки ЗАВАНТАЖУЄМО якщо файл є (НЕ будуємо!)
try:
    from engine.voyage_search import load_embeddings as _voyage_load
    import os as _os
    _emb_file = _os.path.join(DATA_DIR, "catalog_embeddings.npz")
    if _os.path.exists(_emb_file):
        _voyage_load()
    else:
        print("ℹ️ Voyage: embeddings не знайдено — пошук працює без Voyage", flush=True)
except ImportError:
    pass
except Exception as _e:
    print(f"⚠️ Voyage init: {_e}", flush=True)
