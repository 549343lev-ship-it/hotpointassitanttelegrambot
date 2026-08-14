"""
synonym_export.py — Генерує Excel-словник синонімів з normalization_cache.json.

Колонки:
  A: Оригінал (що написав менеджер)
  B: Нормалізована назва (що передали в Voyage)
  C: Знайдений товар у каталозі
  D: Категорія
  E: Статус (confirmed / auto / banned)
  F: Confidence
  G: Дата збереження
"""

import os
import re
import json
import time
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from config.settings import DATA_DIR

CACHE_FILE = os.path.join(DATA_DIR, "normalization_cache.json")

STATUS_ICON = {
    "confirmed": "✅",
    "auto":      "🤖",
    "banned":    "🚫",
}

HEADERS = [
    "Оригінал (рукопис)",
    "Нормалізована назва",
    "Товар у каталозі",
    "Категорія",
    "Статус",
    "Впевненість, %",
    "Дата",
]

COL_WIDTHS = [35, 38, 45, 22, 12, 14, 13]

STATUS_FILLS = {
    "confirmed": PatternFill("solid", fgColor="D6F5D6"),   # зелений
    "auto":      PatternFill("solid", fgColor="EBF3FB"),   # блакитний
    "banned":    PatternFill("solid", fgColor="FDDEDE"),   # червоний
}

HEADER_FILL = PatternFill("solid", fgColor="2C3E50")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=10)
CELL_FONT   = Font(name="Arial", size=9)
THIN_BORDER = Border(
    left=Side(style="thin", color="CCCCCC"),
    right=Side(style="thin", color="CCCCCC"),
    top=Side(style="thin", color="CCCCCC"),
    bottom=Side(style="thin", color="CCCCCC"),
)


def _load_raw_cache() -> dict:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _parse_key(raw_key: str) -> tuple[str, str]:
    """Розбирає 'оригінал::brand_hints' → (original_text, brand_hint_str)."""
    parts = raw_key.split("::", 1)
    original = parts[0].strip()
    hint     = parts[1].strip() if len(parts) > 1 else ""
    # Видаляємо суфікс банів типу ::ban1
    original = re.sub(r'::ban\d+$', '', original).strip()
    return original, hint


def build_synonym_excel(output_path: str) -> int:
    """
    Будує Excel-файл зі словником синонімів.
    Повертає кількість рядків (без заголовка).
    """
    raw = _load_raw_cache()
    if not raw:
        return 0

    wb = Workbook()
    ws = wb.active
    ws.title = "Словник синонімів"

    # ── Заголовок ─────────────────────────────────────────────────────────────
    for col_idx, header in enumerate(HEADERS, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font      = HEADER_FONT
        cell.fill      = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border    = THIN_BORDER
    ws.row_dimensions[1].height = 22

    # ── Дані ──────────────────────────────────────────────────────────────────
    # Сортуємо: спочатку confirmed, потім auto, потім banned; всередині за категорією
    def sort_key(item):
        status = item[1].get("status", "auto")
        order  = {"confirmed": 0, "auto": 1, "banned": 2}.get(status, 1)
        return (order, item[1].get("category", ""), item[0])

    rows = sorted(raw.items(), key=sort_key)

    row_num = 2
    for raw_key, entry in rows:
        status = entry.get("status", "auto")
        # Пропускаємо бан-маркери (суфікс ::ban1 тощо) — вони дублікати
        if "::ban" in raw_key and raw_key.split("::ban")[-1].isdigit():
            continue

        original, _hint = _parse_key(raw_key)

        col_values = [
            original,
            entry.get("normalized", ""),
            entry.get("catalog_name", ""),
            entry.get("category", ""),
            STATUS_ICON.get(status, status),
            entry.get("confidence", ""),
            entry.get("saved_at", ""),
        ]

        fill = STATUS_FILLS.get(status, STATUS_FILLS["auto"])

        for col_idx, value in enumerate(col_values, 1):
            cell = ws.cell(row=row_num, column=col_idx, value=value)
            cell.font      = CELL_FONT
            cell.fill      = fill
            cell.border    = THIN_BORDER
            cell.alignment = Alignment(vertical="center", wrap_text=(col_idx <= 3))

        ws.row_dimensions[row_num].height = 18
        row_num += 1

    total_rows = row_num - 2

    # ── Ширини колонок ────────────────────────────────────────────────────────
    for col_idx, width in enumerate(COL_WIDTHS, 1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    # ── Freeze + AutoFilter ───────────────────────────────────────────────────
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{row_num - 1}"

    # ── Зведений аркуш ────────────────────────────────────────────────────────
    ws2 = wb.create_sheet("Статистика")
    stats_data = _calc_stats(raw)
    ws2.append(["Показник", "Значення"])
    ws2["A1"].font = HEADER_FONT
    ws2["A1"].fill = HEADER_FILL
    ws2["B1"].font = HEADER_FONT
    ws2["B1"].fill = HEADER_FILL
    for stat_key, stat_val in stats_data.items():
        ws2.append([stat_key, stat_val])
    ws2.column_dimensions["A"].width = 28
    ws2.column_dimensions["B"].width = 14

    wb.save(output_path)
    return total_rows


def _calc_stats(raw: dict) -> dict:
    confirmed = sum(1 for e in raw.values() if e.get("status") == "confirmed")
    banned    = sum(1 for e in raw.values() if e.get("status") == "banned")
    auto      = len(raw) - confirmed - banned
    cats: dict[str, int] = {}
    for e in raw.values():
        cat = e.get("category", "other")
        cats[cat] = cats.get(cat, 0) + 1
    top_cats = sorted(cats.items(), key=lambda x: -x[1])[:5]
    result = {
        "Всього записів":   len(raw),
        "✅ Підтверджено":  confirmed,
        "🤖 Авто":          auto,
        "🚫 Заблоковано":   banned,
        "Дата експорту":    time.strftime("%Y-%m-%d %H:%M"),
    }
    for i, (cat, cnt) in enumerate(top_cats, 1):
        result[f"Топ {i}: {cat}"] = cnt
    return result
