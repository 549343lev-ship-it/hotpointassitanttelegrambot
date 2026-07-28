"""
catalog.py — Каталог товарів.

Завантажує xlsx-прайси → будує список товарів → індексує токени для пошуку.
Singleton: каталог будується один раз при старті і живе в пам'яті.
"""

import os
import re
import json
import pandas as pd

CATALOG_PATH = "catalog.json"   # кешований каталог на диску (будується з xlsx один раз)

CATALOG_FILES = [   # пари (ім'я файлу, назва категорії) для всіх прайсів
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

CATALOG: list[dict] = []   # глобальний список всіх товарів (завантажується при старті)
_tokens_built = False      # прапорець: чи вже проіндексовані токени і атрибути


# ─── Побудова каталогу ───────────────────────────────────────────────────────

def _is_header_row(name, artikul, or_val) -> bool:  # визначає чи є рядок xlsx заголовком або порожнім (такі пропускаємо)
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


def build_catalog_from_xlsx() -> list[dict]:    # читає всі xlsx-прайси і збирає єдиний список товарів із категоріями
    catalog = []
    bot_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
    search_dirs = ['.', 'src', bot_dir]

    for key, category in CATALOG_FILES:
        found = False
        for d in search_dirs:
            path = os.path.join(d, f"{key}.xlsx")
            if not os.path.exists(path):
                continue
            try:
                df = pd.read_excel(path, header=0)
                cols = list(df.columns)
                rename = {}
                if len(cols) >= 1: rename[cols[0]] = 'name'
                if len(cols) >= 2: rename[cols[1]] = 'artikul'
                if len(cols) >= 3: rename[cols[2]] = 'price'
                df = df.rename(columns=rename)
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
                    name_clean = re.sub(r'\s*\{[^}]+\}', '', name).strip()  # прибираємо теги виду {артикул}
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


def load_catalog():     # завантажує каталог з JSON-кешу якщо є, або будує заново з xlsx-файлів
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

    # видаляємо виведені з асортименту позиції
    before = len(CATALOG)
    CATALOG = [c for c in CATALOG if 'виведено з асортименту' not in c.get('name', '').lower()]
    if len(CATALOG) < before:
        print(f"🧹 Видалено {before - len(CATALOG)} виведених", flush=True)


# ─── Токенізація ─────────────────────────────────────────────────────────────

def tokenize(text: str) -> set:     # перетворює назву товару або запит на набір токенів для пошуку (нормалізує розміри, різьби, кути)
    t = text.lower()
    t = re.sub(r'(\d+)/(\d+)', r'\1_\2', t)                                    # різьба 1/2 → 1_2
    t = re.sub(r'[фfдd]\s*(\d)', r'\1', t)                                     # ф25 → 25
    t = re.sub(r'(\d+)\s*[хxX×]\s*(\d+)/(\d+)', r'\1 \2_\3', t)

    def strip_thick(m):     # прибирає товщину стінки якщо вона менша 15мм (напр. 25х3,2 → 25)
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
    t = re.sub(r'(8[67])[.,]5', r'\1', t)                                      # 87,5 → 87
    t = re.sub(r'(?<![0-9_])\d+[.,]\d+(?![0-9_])', '', t)                      # прибираємо дробові числа
    return set(re.findall(r'[а-яёіїєґa-z]+|[0-9]+_[0-9]+|[0-9]+', t))


def ensure_tokens():    # лінива індексація: будує _tokens і _attrs для кожного товару каталогу при першому пошуку
    global _tokens_built
    if not _tokens_built:
        from search import parse_attrs  # lazy import щоб уникнути циклічного імпорту catalog↔search
        print("🔨 Індексую токени...", flush=True)
        for item in CATALOG:
            item['_tokens'] = tokenize(item['name'])
            item['_attrs']  = parse_attrs(item['name'])
        print("✅ Індексація завершена", flush=True)
        _tokens_built = True


# ─── Ініціалізація ───────────────────────────────────────────────────────────
print("📦 Завантажую каталог...", flush=True)
load_catalog()
