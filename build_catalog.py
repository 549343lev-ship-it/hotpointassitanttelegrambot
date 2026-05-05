"""
build_catalog.py — одноразовий скрипт для побудови catalog.json

Запускай ОДИН РАЗ після оновлення xlsx файлів:
  python build_catalog.py

Читає всі xlsx файли → зберігає в catalog.json
Формат: [{"name": "...", "brand": "...", "category": "...", "price": 0.0}, ...]
"""

import json, os
import pandas as pd
import openpyxl

# ─── СПИСОК ФАЙЛІВ ────────────────────────────────────────────────────────────
# key = назва файлу xlsx (без .xlsx)
# label = людська назва категорії
CATALOG_FILES = [
    ("adapters_reducers",        "Перехідники та редуктори"),
    ("automation",               "Автоматика опалення"),
    ("boilers",                  "Котли"),
    ("fasteners_sealants",       "Кріплення та ущільнювачі"),
    ("filtration",               "Фільтри та очистка"),
    ("heating",                  "Опалення"),
    ("hoses",                    "Шланги"),
    ("insulation",               "Утеплювач"),
    ("metal_plastic",            "Металопластик"),
    ("mixers_faucets",           "Змішувачі та крани"),
    ("plastic_ppr",              "Пластик ППР"),
    ("pumps",                    "Насоси"),
    ("push_systems",             "Системи PUSH"),
    ("radiators_radiatorsvalve", "Радіатори та арматура"),
    ("safety_valves",            "Арматура безпеки"),
    ("sanitary_ware",            "Санфаянс"),
    ("sewage",                   "Каналізація"),
    ("shutoff_valves",           "Запірна арматура"),
    ("siphons_fittings",         "Сифони та арматура"),
    ("towel_warmers",            "Полотенцесушителі"),
    ("underfloor_heating",       "Тепла підлога"),
    ("water_heaters",            "Водонагрівачі"),
    ("water_meters",             "Водолічильники"),
]

# ─── ЯКЩО У ВАС ОДИН ВЕЛИКИЙ XLS/XLSX ПРАЙС ─────────────────────────────────
# Розкоментуй цей блок і закоментуй блок вище + цикл нижче
#
# SINGLE_FILE = "прай.xls"   # або "прай.xlsx"
#
# def build_from_single_file():
#     """Читає єдиний прайс-файл і будує catalog.json"""
#     import xlrd  # pip install xlrd для .xls
#     # або engine='openpyxl' для .xlsx
#
#     print(f"📖 Читаю {SINGLE_FILE}...")
#     # Визначаємо engine
#     if SINGLE_FILE.endswith('.xls'):
#         # Конвертуємо через LibreOffice
#         import subprocess
#         subprocess.run(['soffice', '--headless', '--convert-to', 'xlsx',
#                        SINGLE_FILE, '--outdir', '/tmp/'], check=True)
#         xlsx_path = f"/tmp/{SINGLE_FILE.replace('.xls', '.xlsx')}"
#     else:
#         xlsx_path = SINGLE_FILE
#
#     df = pd.read_excel(xlsx_path, header=None)
#
#     catalog = []
#     current_category = ''
#     current_brand = ''
#
#     for _, row in df.iterrows():
#         col_b, col_c, col_d = row[1], row[2], row[3]
#
#         if pd.isna(col_b) and pd.isna(col_d) and not pd.isna(col_c):
#             col_c_str = str(col_c).strip()
#             if col_c_str.isupper() and len(col_c_str) > 3:
#                 current_category = col_c_str
#             else:
#                 current_brand = col_c_str
#             continue
#
#         if not pd.isna(col_c) and not pd.isna(col_d) and isinstance(col_d, (int, float)):
#             catalog.append({
#                 'name':     str(col_c).strip(),
#                 'brand':    current_brand,
#                 'category': current_category,
#                 'price':    float(col_d),
#             })
#
#     return catalog


def load_xlsx(filename: str, label: str) -> list[dict]:
    """Читає один xlsx файл і повертає список товарів"""
    path = f"{filename}.xlsx"
    if not os.path.exists(path):
        print(f"  ⚠️  {path} не знайдено, пропускаю")
        return []

    try:
        df = pd.read_excel(path, header=0)
        cols = list(df.columns)

        # Перейменовуємо перші 4 колонки стандартно
        rename = {}
        if len(cols) >= 1: rename[cols[0]] = 'Наименование'
        if len(cols) >= 2: rename[cols[1]] = 'Артикул'
        if len(cols) >= 3: rename[cols[2]] = 'ОР'
        if len(cols) >= 4: rename[cols[3]] = 'Код'
        df = df.rename(columns=rename)

        items = []
        for _, row in df.iterrows():
            name = str(row.get('Наименование', '')).strip()
            if not name or name == 'nan':
                continue
            items.append({
                'name':     name,
                'brand':    '',     # у xlsx файлах бренд зазвичай у назві
                'category': label,
                'price':    0.0,    # ціна не важлива для пошуку
            })

        print(f"  ✅ {path}: {len(items)} позицій")
        return items

    except Exception as e:
        print(f"  ❌ {path}: {e}")
        return []


def main():
    print("🔨 Будую catalog.json...\n")

    # ── Варіант 1: багато xlsx файлів ──
    catalog = []
    for key, label in CATALOG_FILES:
        items = load_xlsx(key, label)
        catalog.extend(items)

    # ── Варіант 2: один великий прайс xls ──
    # Розкоментуй якщо у тебе один файл:
    # catalog = build_from_single_file()

    if not catalog:
        print("\n❌ Каталог порожній! Перевір наявність xlsx файлів.")
        return

    with open("catalog.json", "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)

    print(f"\n✅ catalog.json збережено: {len(catalog)} позицій")
    print("Тепер запускай: python bot.py")


if __name__ == "__main__":
    main()
