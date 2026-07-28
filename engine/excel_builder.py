"""
excel_builder.py — Генерація Excel-файлу замовлення.

Вхід:  список результатів пошуку (з find_items)
Вихід: BytesIO з xlsx-файлом + списки незнайдених і сумнівних

Кольори:
  🟥 червоний (RED)   — не знайдено
  🟨 жовтий (YELLOW)  — знайдено, але сумнівно (низький confidence або fallback)
"""

import re
from io import BytesIO
import pandas as pd
from openpyxl.styles import PatternFill

from data.catalog import CATALOG

RED    = PatternFill('solid', fgColor='FFC7CE')   # заливка для незнайдених рядків
YELLOW = PatternFill('solid', fgColor='FFF3B0')   # заливка для сумнівних рядків


def parse_qty(s) -> tuple:   # розбиває рядок кількості на (число, одиниця): "10 шт" → (10, "шт")
    s = str(s or '').strip()
    if not s:
        return '', ''
    m = re.match(r'(\d+(?:[.,]\d+)?)\s*(.*)', s)
    if m:
        try:
            num = float(m.group(1).replace(',', '.'))
            num = int(num) if num == int(num) else num
        except Exception:
            num = m.group(1)
        return num, m.group(2).strip().rstrip('.').strip()
    return s, ''


def create_excel(результати: list[dict]) -> tuple[BytesIO, list, list]:  # будує xlsx-файл із результатів пошуку; повертає (файл, незнайдені, сумнівні)
    rows, flags = [], []
    not_found, warn = [], []

    for r in результати:
        if not r:
            continue
        conf     = r.get('confidence', 0)
        kw       = r.get('keyword_pct', 0)
        qty_num, qty_unit = parse_qty(r.get('qty', ''))

        if r.get('знайдено'):
            # вважаємо сумнівним якщо впевненість низька або джерело ненадійне
            suspicious = (
                conf < 70 or kw < 50
                or r.get('brand_warning')
                or r.get('джерело', '') in ('⚠️ fallback', '🔍 вільний', '⚠️ аналог')
            )
            rows.append({
                '№':            len(rows) + 1,
                'Артикул':      r.get('артикул', ''),
                'Наименование': r.get('назва_повна') or r.get('назва', ''),
                'Кількість':    qty_num,
                'Од.':          qty_unit,
                'Ціна':         r.get('ціна', ''),
                'Збіг':         f"🔍{kw}%/🤖{conf}%",
                'Джерело':      r.get('джерело', ''),
                'Розділ':       r.get('розділ', ''),
                'Чому знайшло': r.get('reason', ''),
                'Оригінал':     r.get('original', ''),
            })
            flags.append('warn' if suspicious else '')
            if suspicious:
                warn.append(r.get('original', ''))
        else:
            # для незнайдених — ставимо найближчий кандидат щоб менеджер міг оцінити
            cands = r.get('candidates_debug', [])
            best  = cands[0] if cands else ''
            art, full, price = '', best, ''
            for it in CATALOG:
                if it['name'] == best:
                    art   = it.get('artikul', '')
                    full  = it.get('name_full', best)
                    price = it.get('price', '')
                    break
            rows.append({
                '№':            len(rows) + 1,
                'Артикул':      art,
                'Наименование': full,
                'Кількість':    qty_num,
                'Од.':          qty_unit,
                'Ціна':         price,
                'Збіг':         '—',
                'Джерело':      '❓ НЕ ЗНАЙДЕНО',
                'Розділ':       r.get('розділ', ''),
                'Чому знайшло': (r.get('fail_reason', '') or '')[:100],
                'Оригінал':     r.get('original', ''),
            })
            flags.append('nf')
            not_found.append(r.get('original', ''))

    output = BytesIO()
    cols   = ['№', 'Артикул', 'Наименование', 'Кількість', 'Од.',
              'Ціна', 'Збіг', 'Джерело', 'Розділ', 'Чому знайшло', 'Оригінал']

    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=cols)
        df.to_excel(writer, index=False, sheet_name='Замовлення')
        ws = writer.sheets['Замовлення']
        ws.column_dimensions['A'].width = 4    # стовпець №
        ws.column_dimensions['C'].width = 55   # стовпець Найменування — широкий
        ws.column_dimensions['J'].width = 20   # стовпець Оригінал
        for i, fl in enumerate(flags, start=2):
            fill = RED if fl == 'nf' else (YELLOW if fl == 'warn' else None)
            if fill:
                for cell in ws[i]:
                    cell.fill = fill

    output.seek(0)
    return output, not_found, warn
