"""
build_catalog.py — збирає catalog.json з усіх xlsx/xls файлів прайсу.

ПРОБЛЕМА ЯКУ ВИРІШУЄ:
  Файли прайсу генеруються системою яка зберігає SharedStrings.xml
  з ВЕЛИКОЇ літери (SharedStrings.xml замість sharedStrings.xml).
  Стандартний openpyxl не знаходить цей файл і повертає порожні назви.
  Цей скрипт читає xlsx напряму через zipfile+xml, обходячи баг.

ЗАПУСК:
  python build_catalog.py --src ./  (або вкажи папку з xlsx файлами)
  python build_catalog.py --src ./prais/

ВИВОДИТЬ:
  catalog.json у поточній директорії
"""

import os, re, json, zipfile, xml.etree.ElementTree as ET, argparse
import xlrd  # pip install xlrd  (для старих .xls)

# ─── Відповідність імен файлів → категорія ──────────────────────────────────
FILE_CATEGORIES = {
    'пластик':       'plastic_ppr',
    'ppr':           'plastic_ppr',
    'ппр':           'plastic_ppr',
    'металопластик': 'metal_plastic',
    'push':          'push_systems',
    'пуш':           'push_systems',
    'каналізація':   'sewage',
    'канализ':       'sewage',
    'запірна':       'shutoff_valves',
    'запорн':        'shutoff_valves',
    'арматура':      'safety_valves',
    'переходники':   'adapters_reducers',
    'перехідники':   'adapters_reducers',
    'насос':         'pumps',
    'котли':         'boilers',
    'котел':         'boilers',
    'водонагр':      'water_heaters',
    'бойлер':        'water_heaters',
    'опалення':      'heating',
    'автоматика':    'automation',
    'очистка':       'filtration',
    'фільтр':        'filtration',
    'водомір':       'water_meters',
    'водолічильник': 'water_meters',
    'кріплення':     'fasteners_sealants',
    'ущільнювач':    'fasteners_sealants',
    'розхідник':     'fasteners_sealants',
    'радіатор':      'radiators_radiatorsvalve',
    'сифон':         'siphons_fittings',
    'змішувач':      'mixers_faucets',
    'санфаянс':      'sanitary_ware',
    'підлога':       'underfloor_heating',
    'тепла':         'underfloor_heating',
    'рушникосушіл':  'towel_warmers',
    'полотенце':     'towel_warmers',
    'шланг':         'hoses',
    'ізоляц':        'insulation',
    'утеплювач':     'insulation',
}

def guess_category(filename: str) -> str:
    fn = filename.lower()
    for key, cat in FILE_CATEGORIES.items():
        if key in fn:
            return cat
    return 'other'

def clean_name(name: str) -> str:
    """Прибирає {4/100} та зайві пробіли з назви"""
    name = re.sub(r'\s*\{[^}]+\}', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def is_product_row(name: str, price_str: str, artikul: str) -> bool:
    """Визначає чи рядок є товаром (не заголовком/категорією)"""
    if not name or len(name) < 5:
        return False
    # Заголовки і групи: коротко, великими, або починаються з *
    if name.startswith('*'):
        return False
    # Якщо всі слова великими і немає цифр — скоріш за все заголовок
    words = name.split()
    if all(w.isupper() for w in words if w.isalpha()) and not any(c.isdigit() for c in name):
        return False
    # Є ціна > 0 або є артикул — товар
    try:
        price = float(str(price_str).replace(',', '.').strip())
        if price > 0:
            return True
    except (ValueError, TypeError):
        pass
    if artikul and str(artikul).strip() not in ('', 'nan', '0'):
        return True
    return False

# ─── Читання .xlsx (з виправленням регістру SharedStrings) ──────────────────
def read_xlsx(path: str) -> list[dict]:
    items = []
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            # Знаходимо SharedStrings незалежно від регістру
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

            # Читаємо worksheet
            ws_path = next((n for n in names if n.lower() == 'xl/worksheets/sheet1.xml'), 'xl/worksheets/sheet1.xml')
            with z.open(ws_path) as f:
                tree = ET.parse(f)
                root = tree.getroot()
                ns = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                for row in root.findall('.//x:row', ns):
                    row_vals = []
                    for c in row.findall('x:c', ns):
                        t = c.get('t', '')
                        v = c.find('x:v', ns)
                        if v is None:
                            row_vals.append('')
                        elif t == 's':
                            idx = int(v.text)
                            row_vals.append(strings[idx] if idx < len(strings) else '')
                        elif t == 'e':
                            row_vals.append('')
                        else:
                            row_vals.append(v.text or '')
                    if row_vals:
                        items.append(row_vals)
    except Exception as e:
        print(f"  ⚠️  Помилка читання {path}: {e}")
    return items

# ─── Читання .xls (старий формат) ───────────────────────────────────────────
def read_xls(path: str) -> list[list]:
    items = []
    try:
        wb = xlrd.open_workbook(path)
        ws = wb.sheet_by_index(0)
        for i in range(ws.nrows):
            items.append([str(ws.cell_value(i, j)) for j in range(ws.ncols)])
    except Exception as e:
        print(f"  ⚠️  Помилка читання {path}: {e}")
    return items

# ─── Головна функція ─────────────────────────────────────────────────────────
def build_catalog(src_dir: str) -> list[dict]:
    catalog = []
    total_files = 0

    for fname in sorted(os.listdir(src_dir)):
        ext = fname.lower()
        if not (ext.endswith('.xlsx') or ext.endswith('.xls')):
            continue
        if fname.lower().startswith('замовлення') or fname.lower().startswith('catalog'):
            continue

        path = os.path.join(src_dir, fname)
        category = guess_category(fname)
        print(f"📄 {fname} → [{category}]", end=' ')

        if ext.endswith('.xlsx'):
            rows = read_xlsx(path)
        else:
            rows = read_xls(path)

        count = 0
        for row in rows:
            # Забезпечуємо мінімум 4 колонки
            while len(row) < 4:
                row.append('')
            name_raw = str(row[0]).strip()
            artikul   = str(row[1]).strip()
            price_str = str(row[2]).strip()
            kod       = str(row[3]).strip()

            name = clean_name(name_raw)
            if not is_product_row(name, price_str, artikul):
                continue

            try:
                price = float(price_str.replace(',', '.'))
            except (ValueError, TypeError):
                price = 0.0

            catalog.append({
                'name':      name,
                'name_full': name_raw,
                'artikul':   artikul if artikul not in ('nan', '0', '0.0', '') else '',
                'kod':      kod.rstrip('.0') if kod not in ('nan', '') else '',
                'category': category,
                'price':    price,
            })
            count += 1

        print(f"{count} товарів")
        total_files += 1

    print(f"\n✅ Всього: {len(catalog)} товарів з {total_files} файлів")
    return catalog


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--src', default='.', help='Папка з xlsx/xls файлами')
    parser.add_argument('--out', default='catalog.json', help='Вихідний файл')
    args = parser.parse_args()

    catalog = build_catalog(args.src)
    if catalog:
        with open(args.out, 'w', encoding='utf-8') as f:
            json.dump(catalog, f, ensure_ascii=False, indent=None)
        print(f"💾 Збережено: {args.out} ({os.path.getsize(args.out) // 1024} KB)")
    else:
        print("❌ Каталог порожній!")
