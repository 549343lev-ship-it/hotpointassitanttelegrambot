"""
engine/xlsx_reader.py — Витяг позицій замовлення з Excel (.xls / .xlsx).

Без AI: знаходить колонку назв, колонку кількості й одиниць,
відсіює службові рядки (шапка, підсумки, реквізити, підписи).

Повертає [{'name': str, 'qty': str}] у порядку файлу.
"""

import io
import re

MAX_ROWS   = 400   # запобіжник: більше позицій за раз не беремо
HEADER_SCAN = 30   # скільки верхніх рядків сканувати в пошуках шапки

# ─── Ключі шапки ─────────────────────────────────────────────────────────────

NAME_KEYS = ('номенклатур', 'найменуван', 'наименован', 'назва товару',
             'назва', 'товар', 'опис', 'описание', 'позиц',
             'матеріал', 'материал', 'product', 'name', 'description')

QTY_KEYS  = ('кількість', 'кільк', 'количество', 'к-ть', 'к-сть',
             'кол-во', 'колво', 'qty', 'quantity')

UNIT_KEYS = ('од.вим', 'од. вим', 'одиниц', 'од.', 'ед.изм', 'ед. изм',
             'единиц', 'ед.', 'unit')

# ─── Службові рядки, які не є товаром ────────────────────────────────────────

JUNK_PAT = re.compile(
    r'(всього|усього|разом|итого|подітог|подытог|у тому числі|в тому числі|'
    r'в т\.?ч\.?|без пдв|з пдв|сума пдв|сумма ндс|податок на додану|'
    r'покупец|покупчин|покупець|продавец|продавець|постачальник|поставщик|'
    r'виконавец|виконавець|исполнител|замовник|заказчик|отримувач|получател|'
    r'реквізит|реквизит|розрахунков|расчетн|банк|iban|єдрпоу|едрпоу|мфо|'
    r'призначення платеж|назначение платеж|'
    r'рахунок[ -]?фактур|видаткова накладна|рахунок на оплат|счет[ -]?фактур|'
    r'підпис|подпись|керівник|руководител|бухгалтер|печатк|печать|'
    r'виписав|выписал|склав|составил|отримав|получил|'
    r'усього найменувань|всего наименований|прописом|'
    r'^м\.?\s?п\.?$|^дата$|^№$|^n$)',
    re.IGNORECASE)

# Рядок-шапка, що повторюється на розривах сторінок
HEADER_ECHO = re.compile(
    r'^(номенклатур\w*|найменуван\w*|наименован\w*|назва\w*|товар|опис\w*|'
    r'кількість|количество|ціна|цена|сума|сумма|од\.?\s?вим\.?)$',
    re.IGNORECASE)


# ─── Дрібні хелпери ──────────────────────────────────────────────────────────

def _cell(df, ri: int, ci: int) -> str:
    """Безпечно дістає клітинку як рядок ('' для NaN/None)."""
    try:
        v = df.iloc[ri, ci]
    except Exception:
        return ''
    if v is None:
        return ''
    s = str(v).strip()
    if s.lower() in ('nan', 'none', 'nat', '<na>'):
        return ''
    return s


def _fmt_qty(raw: str) -> str:
    """'2.0' → '2', '2,50' → '2,5', '10 шт' → '10 шт', сміття → ''."""
    s = (raw or '').strip()
    if not s:
        return ''
    try:
        f = float(s.replace(' ', '').replace('\u00a0', '').replace(',', '.'))
    except ValueError:
        return re.sub(r'\s+', ' ', s)
    if f <= 0:
        return ''
    if abs(f - round(f)) < 1e-9:
        return str(int(round(f)))
    return f"{f:.3f}".rstrip('0').rstrip('.').replace('.', ',')


def _clean_name(raw: str) -> str:
    """Чистить назву: {упаковка}, 'NEW!', нумерація, переноси, зірочки."""
    s = raw.replace('\n', ' ').replace('\r', ' ')
    s = re.sub(r'\{[^}]*\}', ' ', s)          # {уп. 10 шт}
    s = re.sub(r'^\s*NEW!\s*', '', s, flags=re.IGNORECASE)
    s = re.sub(r'^\s*\d{1,3}\s*[.)]\s+', '', s)   # '12. ' або '12) ' на початку
    s = re.sub(r'[*×]', 'х', s)
    s = re.sub(r'\s+', ' ', s)
    return s.strip(' .;,-')


def _is_product_name(s: str) -> bool:
    """Чи схожа клітинка на назву товару."""
    if len(s) < 4:
        return False
    if HEADER_ECHO.match(s):
        return False
    if JUNK_PAT.search(s):
        return False
    letters = len(re.findall(r'[а-яёіїєґa-z]', s, re.IGNORECASE))
    if letters < 3:                    # число, дата, артикул без слів
        return False
    if re.fullmatch(r'[\d\s.,:/\\-]+', s):
        return False
    return True


def _is_numeric(s: str) -> bool:
    if not s:
        return False
    try:
        float(s.replace(' ', '').replace('\u00a0', '').replace(',', '.'))
        return True
    except ValueError:
        return bool(re.match(r'^\d+(?:[.,]\d+)?\s*[а-яa-z.]{1,6}$', s, re.IGNORECASE))


# ─── Пошук колонок ───────────────────────────────────────────────────────────

def _find_columns(df) -> tuple[int | None, int | None, int | None, int]:
    """Повертає (name_col, qty_col, unit_col, header_row)."""
    n_rows = len(df)
    n_cols = len(df.columns)
    name_col = qty_col = unit_col = None
    header_row = 0

    def _names_below(ci: int, ri: int) -> int:
        return sum(1 for r in range(ri + 1, n_rows)
                   if _is_product_name(_cell(df, r, ci)))

    # 1) За ключами шапки (з перевіркою, що під шапкою справді товари)
    for ri in range(min(HEADER_SCAN, n_rows)):
        row_name = row_qty = row_unit = None
        for ci in range(n_cols):
            v = _cell(df, ri, ci)
            if not v or len(v) > 40:
                continue
            low = v.lower()
            if row_name is None and any(k in low for k in NAME_KEYS):
                row_name = ci
            if row_qty is None and any(k in low for k in QTY_KEYS):
                row_qty = ci
            if row_unit is None and any(k in low for k in UNIT_KEYS):
                row_unit = ci
        # Заголовок приймаємо лише якщо нижче є хоч якісь назви товарів.
        # Інакше це був заголовок документа ("Рахунок на товар №5") — шукаємо далі.
        if row_name is not None and _names_below(row_name, ri) >= 1:
            name_col, qty_col, unit_col, header_row = row_name, row_qty, row_unit, ri
            break

    # 2) Якщо шапки нема — беремо колонку з найбільшою к-стю текстових назв
    if name_col is None:
        best_col, best_score = None, 0
        for ci in range(n_cols):
            score = sum(1 for ri in range(n_rows) if _is_product_name(_cell(df, ri, ci)))
            if score > best_score:
                best_col, best_score = ci, score
        if best_score < 1:
            return None, None, None, 0
        name_col, header_row = best_col, -1   # -1 → читаємо з рядка 0

    # 3) Кількість: якщо не знайшли за шапкою — перша числова колонка справа
    if qty_col is None:
        start = header_row + 1 if header_row >= 0 else 0
        best_col, best_score = None, 0
        for ci in range(name_col + 1, n_cols):
            score = sum(1 for ri in range(start, n_rows)
                        if _is_product_name(_cell(df, ri, name_col))
                        and _is_numeric(_cell(df, ri, ci)))
            if score > best_score:
                best_col, best_score = ci, score
        if best_score >= 1:
            qty_col = best_col

    return name_col, qty_col, unit_col, header_row


def _rows_from_df(df) -> list[dict]:
    """Витягує позиції з одного аркуша."""
    if df is None or len(df) == 0 or len(df.columns) == 0:
        return []

    name_col, qty_col, unit_col, header_row = _find_columns(df)
    if name_col is None:
        return []

    out = []
    start = header_row + 1 if header_row >= 0 else 0
    for ri in range(start, len(df)):
        raw = _cell(df, ri, name_col)
        if not _is_product_name(raw):
            continue
        name = _clean_name(raw)
        if not _is_product_name(name):
            continue

        qty = _fmt_qty(_cell(df, ri, qty_col)) if qty_col is not None else ''
        if unit_col is not None and qty:
            unit = _cell(df, ri, unit_col).strip(' .')
            if unit and len(unit) <= 8 and unit.lower() not in qty.lower():
                qty = f"{qty} {unit}"

        out.append({'name': name, 'qty': qty})
    return out


# ─── Точка входу ─────────────────────────────────────────────────────────────

def extract_order_rows(data: bytes, ext: str = 'xlsx',
                       limit: int = MAX_ROWS) -> list[dict]:
    """
    Excel-файл (bytes) → [{'name', 'qty'}].
    Бере аркуш, з якого вдалося витягти найбільше позицій.
    """
    try:
        import pandas as pd
    except Exception as e:
        print(f"⚠️ xlsx_reader: нема pandas: {e}", flush=True)
        return []

    engine = 'xlrd' if str(ext).lower() == 'xls' else 'openpyxl'

    sheets = None
    for eng in (engine, 'openpyxl', 'xlrd', None):
        try:
            kw = {'sheet_name': None, 'header': None}
            if eng:
                kw['engine'] = eng
            sheets = pd.read_excel(io.BytesIO(data), **kw)
            break
        except Exception as e:
            last = e
            continue

    if sheets is None:
        print(f"⚠️ xlsx_reader: не вдалося відкрити файл: {last}", flush=True)
        return []

    if not isinstance(sheets, dict):
        sheets = {'Sheet1': sheets}

    best: list[dict] = []
    for sname, df in sheets.items():
        try:
            rows = _rows_from_df(df)
        except Exception as e:
            print(f"⚠️ xlsx_reader: аркуш '{sname}': {e}", flush=True)
            continue
        if len(rows) > len(best):
            best = rows

    if len(best) > limit:
        print(f"⚠️ xlsx_reader: {len(best)} позицій, обрізано до {limit}", flush=True)
        best = best[:limit]

    print(f"📊 xlsx_reader: витягнуто {len(best)} позицій", flush=True)
    return best
