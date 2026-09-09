"""
engine/invoice_match.py — Зіставлення рахунку з каталогом Hotpoint.

Без AI. Інвертований індекс (будується раз при старті) +
rapidfuzz token_sort_ratio тільки по кандидатах (~10мс на 100 рядків).
"""

import re
import io
import threading
from io import BytesIO
from typing import Optional

from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment
from rapidfuzz import fuzz

GREEN  = PatternFill('solid', fgColor='C6EFCE')
YELLOW = PatternFill('solid', fgColor='FFF3B0')
RED    = PatternFill('solid', fgColor='FFC7CE')

MATCH_THRESHOLD = 75

# ─── Глобальний індекс (будується один раз) ──────────────────────────────────

_index_lock  = threading.Lock()
_inv_index:  dict[str, set] = {}
_cat_names:  list[str]      = []
_catalog_ref: list          = []
_index_built = False


def _norm(text: str) -> str:
    t = text.lower()
    t = re.sub(r'\{[^}]+\}', ' ', t)
    t = re.sub(r'[*×]', 'x', t)
    t = re.sub(r'°', ' ', t)
    t = re.sub(r'[фfдd]\s*(\d)', r'\1', t)
    t = re.sub(r'\s+', ' ', t)
    return t.strip()


def _tok_set(normed: str) -> set[str]:
    return set(re.findall(r'[а-яёіїєґa-z]+|[0-9]+', normed))


def build_index():
    """Будує інвертований індекс по CATALOG. Викликається при першому запиті."""
    global _inv_index, _cat_names, _catalog_ref, _index_built
    with _index_lock:
        if _index_built:
            return
        try:
            from catalog.catalog import CATALOG
        except ImportError:
            try:
                from catalog import CATALOG
            except ImportError:
                print("⚠️ invoice_match: не вдалось імпортувати CATALOG", flush=True)
                return

        _catalog_ref = CATALOG
        _cat_names   = [_norm(item['name']) for item in CATALOG]
        _inv_index   = {}
        for i, name in enumerate(_cat_names):
            for tok in _tok_set(name):
                _inv_index.setdefault(tok, set()).add(i)
        _index_built = True
        print(f"✅ invoice_match: індекс {len(_cat_names)} позицій, "
              f"{len(_inv_index)} токенів", flush=True)


def _find_best(query: str) -> tuple[int, int]:
    """Повертає (індекс в CATALOG, score 0-100)."""
    if not _index_built:
        build_index()

    q_norm = _norm(query)
    qtoks  = _tok_set(q_norm)
    if not qtoks:
        return -1, 0

    candidates: set[int] = set()
    for t in qtoks:
        candidates |= _inv_index.get(t, set())

    if not candidates:
        return -1, 0

    best_score, best_idx = 0, -1
    for i in candidates:
        score = fuzz.token_sort_ratio(q_norm, _cat_names[i])
        if score > best_score:
            best_score, best_idx = score, i
            if score == 100:
                break

    return best_idx, int(best_score)


# ─── Парсинг рахунку ─────────────────────────────────────────────────────────

def _clean(name: str) -> str:
    """Прибирає {упаковку}, артикульні дужки, NEW!, переноси."""
    name = re.sub(r'\{[^}]+\}', '', name)
    name = re.sub(r'^\([^)]{1,20}\)\s*', '', name)           # (SUR03) на початку
    name = re.sub(r'\s*\([A-Z0-9\-]{3,20}\)\s*$', '', name)  # (EKRC-16-34-20) в кінці
    name = re.sub(r'^NEW!\s*', '', name)
    name = name.replace('\n', ' ')
    name = re.sub(r'\*', 'x', name)
    name = re.sub(r'\s+', ' ', name)
    return name.strip()


def parse_invoice_pdf(data: bytes) -> list[dict]:
    """PDF рахунок → [{n, name, qty, price_invoice}]."""
    try:
        import pdfplumber
        rows = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page in pdf.pages:
                for table in (page.extract_tables() or []):
                    for row in table:
                        if not row or len(row) < 2:
                            continue
                        cell0 = str(row[0] or '').strip()
                        if not cell0.isdigit():
                            continue
                        name = _clean(str(row[1] or ''))
                        if not name or len(name) < 4:
                            continue
                        qty   = _clean(str(row[2] or '')) if len(row) > 2 else ''
                        price = _clean(str(row[3] or '')) if len(row) > 3 else ''
                        rows.append({'n': cell0, 'name': name, 'qty': qty,
                                     'price_invoice': price})
        return rows
    except Exception as e:
        print(f"⚠️ parse_invoice_pdf: {e}", flush=True)
        return []


def parse_invoice_xlsx(data: bytes, ext: str = 'xlsx') -> list[dict]:
    """XLSX/XLS рахунок → [{n, name, qty, price_invoice}]."""
    try:
        import pandas as pd
        engine = 'xlrd' if ext == 'xls' else 'openpyxl'
        df = pd.read_excel(io.BytesIO(data), header=None, engine=engine)

        nom_col = qty_col = price_col = None
        header_row = 0
        for ri in range(min(20, len(df))):
            for ci in range(len(df.columns)):
                val = str(df.iloc[ri, ci]).strip().lower()
                if 'номенклатур' in val or val == 'товар':
                    nom_col, header_row = ci, ri
                if 'кількість' in val or 'кільк' in val:
                    qty_col = ci
                if val == 'ціна':
                    price_col = ci

        if nom_col is None:
            nom_col, header_row = 1, 0

        rows, n = [], 0
        for ri in range(header_row + 1, len(df)):
            val = _clean(str(df.iloc[ri, nom_col]))
            if not val or val in ('nan', 'None') or len(val) < 4:
                continue
            if any(s in val for s in ['Покупець', 'Виконавець', 'оплат', 'реквізит']):
                continue
            n += 1
            qty   = _clean(str(df.iloc[ri, qty_col]))   if qty_col   is not None else ''
            price = _clean(str(df.iloc[ri, price_col])) if price_col is not None else ''
            rows.append({'n': str(n), 'name': val, 'qty': qty, 'price_invoice': price})
        return rows
    except Exception as e:
        print(f"⚠️ parse_invoice_xlsx: {e}", flush=True)
        return []


# ─── Excel вивід ─────────────────────────────────────────────────────────────

def build_match_excel(results: list[dict]) -> BytesIO:
    # Використовуємо вже завантажений _catalog_ref (не імпортуємо повторно)
    cat = _catalog_ref

    wb = Workbook()
    ws = wb.active
    ws.title = "Зіставлення"

    headers = ['№', 'Назва в рахунку', 'Знайдено у нас', 'Артикул', 'Наша ціна', 'Схожість%']
    ws.append(headers)
    hfill = PatternFill('solid', fgColor='1F4E79')
    for cell in ws[1]:
        cell.fill      = hfill
        cell.font      = Font(bold=True, color='FFFFFF')
        cell.alignment = Alignment(horizontal='center')

    for r in results:
        idx       = r.get('cat_idx', -1)
        score     = r.get('score', 0)
        found     = cat[idx]['name']                        if 0 <= idx < len(cat) else ''
        artikul   = cat[idx].get('artikul', '')             if 0 <= idx < len(cat) else ''
        our_price = cat[idx].get('price', 0)                if 0 <= idx < len(cat) else 0
        price_str = f"{our_price:.2f}"                      if our_price else ''

        ws.append([r.get('n', ''), r.get('name', ''), found,
                   artikul, price_str, score if found else ''])

        fill = GREEN if found and score >= 90 else (YELLOW if found else RED)
        for cell in ws[ws.max_row]:
            cell.fill      = fill
            cell.alignment = Alignment(wrap_text=True)

    ws.column_dimensions['A'].width = 5
    ws.column_dimensions['B'].width = 52
    ws.column_dimensions['C'].width = 52
    ws.column_dimensions['D'].width = 14
    ws.column_dimensions['E'].width = 12
    ws.column_dimensions['F'].width = 11

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


# ─── Головна точка входу ─────────────────────────────────────────────────────

def process_invoice(data: bytes, filename: str) -> tuple[Optional[BytesIO], int, int]:
    """Парсить рахунок і зіставляє з каталогом. Повертає (excel, знайдено, всього)."""
    if not _index_built:
        build_index()

    if not _catalog_ref:
        print("⚠️ invoice_match: CATALOG порожній, пошук неможливий", flush=True)
        return None, 0, 0

    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext == 'pdf':
        rows = parse_invoice_pdf(data)
    elif ext in ('xls', 'xlsx'):
        rows = parse_invoice_xlsx(data, ext)
    else:
        return None, 0, 0

    if not rows:
        return None, 0, 0

    results = []
    for row in rows:
        idx, score = _find_best(row['name'])
        found = idx >= 0 and score >= MATCH_THRESHOLD
        results.append({**row, 'cat_idx': idx if found else -1, 'score': score})

    found_count = sum(1 for r in results if r['cat_idx'] >= 0)
    return build_match_excel(results), found_count, len(results)
