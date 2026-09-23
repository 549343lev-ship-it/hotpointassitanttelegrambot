"""engine/project_spec.py — робота з проектними специфікаціями (PDF на десятки сторінок).

Навіщо: у проекті специфікація розкидана по розділах (ОВ, ВК: В1, Т3, К1), матеріал і бренд
задані в «Загальних вказівках» окремою сторінкою, а поруч лежать вентиляція, електрика
і блискавкозахист, яких Hotpoint не продає.
"""
from __future__ import annotations

import io
import re

# Блок, який додається в промпт OCR для PDF.
PROJECT_PROMPT_BLOCK = """
ЦЕ ПРОЕКТНА ДОКУМЕНТАЦІЯ. Порядок роботи:
1. Спочатку прочитай аркуші «Загальні дані» / «Загальні вказівки». Звідти візьми матеріал, бренд
   і серію для кожної системи (наприклад: водопровід — PP-R "Ekoplastik"; ізоляція — Thermaflex FRZ 13 мм;
   каналізація — ПВХ; радіаторні клапани — Danfoss). Далі дописуй це до КОЖНОГО рядка специфікації
   тієї системи, навіть якщо в самому рядку бренд не вказаний.
2. Бери позиції ТІЛЬКИ зі «Специфікацій обладнання та матеріалів». Не вигадуй позицій з креслень.
3. Поле section — розділ специфікації: ОВ, В1, Т3, К1, «водомірний вузол» тощо.
4. НЕ БЕРИ (це не наш асортимент): повітроводи та фасонину з оцинкованої сталі, решітки, дефлектори,
   кабелі, щити, автомати, світильники, блискавкозахист, заземлення, будівельні матеріали.
   Сантехніку, змішувачі, сифони, водонагрівачі, вентилятори, люки — БЕРИ.
5. Однакові позиції з різних розділів НЕ об'єднуй — давай окремими рядками з різним section.
   Кількості пізніше складе бот.
6. Кількість переписуй разом з одиницею виміру: "25 м", "14 шт", "194 секц".
"""

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")


def split_pdf(pdf_bytes: bytes, pages_per_chunk: int = 30) -> list[bytes]:
    """Ріже PDF на частини. Потрібно лише як запасний шлях, коли цілий файл не зчитався."""
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return [pdf_bytes]
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return [pdf_bytes]
    out: list[bytes] = []
    for start in range(0, len(reader.pages), pages_per_chunk):
        writer = PdfWriter()
        for page in reader.pages[start:start + pages_per_chunk]:
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        out.append(buf.getvalue())
    return out or [pdf_bytes]


def _qty_parts(qty) -> tuple[float, str]:
    s = str(qty or "")
    m = _NUM.search(s)
    num = float(m.group().replace(",", ".")) if m else 0.0
    unit = s[m.end():].strip() if m else s.strip()
    return num, unit


def merge_duplicates(позиції: list[dict]) -> list[dict]:
    """Однакова позиція з різних розділів → один рядок, кількості складені, розділи перелічені."""
    out: list[dict] = []
    index: dict[tuple, dict] = {}
    for п in позиції:
        num, unit = _qty_parts(п.get("qty"))
        key = (str(п.get("normalized", "")).lower().strip(), unit.lower())
        if not key[0] or num <= 0:
            out.append(п)
            continue
        first = index.get(key)
        if first is None:
            п["_qty_num"], п["_qty_unit"] = num, unit
            index[key] = п
            out.append(п)
            continue
        first["_qty_num"] += num
        first["qty"] = f"{first['_qty_num']:g} {first['_qty_unit']}".strip()
        secs = [s for s in (first.get("section", ""), п.get("section", "")) if s]
        first["section"] = " + ".join(dict.fromkeys(secs))
    for п in out:
        п.pop("_qty_num", None)
        п.pop("_qty_unit", None)
    return out


def spec_report(позиції: list[dict]) -> str:
    """Короткий підсумок для менеджера: скільки позицій і по яких розділах."""
    by_section: dict[str, int] = {}
    for п in позиції:
        by_section[п.get("section") or "—"] = by_section.get(п.get("section") or "—", 0) + 1
    parts = ", ".join(f"{k}: {v}" for k, v in sorted(by_section.items()))
    return f"📄 Зі специфікації взято {len(позиції)} позицій ({parts}). Звір з проектом перед замовленням."
