"""
clients/pending_cache.py — Черга нових збігів що чекають підтвердження адміна.

Замість автоматичного збереження в кеш — бот накопичує знайдені збіги тут.
Адмін командою "перевір кеш" отримує пачки по 5 штук і підтверджує або відхиляє.

Підтверджені → зберігаються в основний кеш (clients/cache.py).
Відхилені → видаляються (бот знайде щось інше наступного разу).

Файл: DATA_DIR/pending_cache.json
Формат запису:
{
  "id": "uuid4",
  "original":     "мрз кут 25х3/4",
  "brand_map":    {"plastic_ppr": ["ekoplastik", "Ekoplastik"]},
  "normalized":   "Коліно PPR РН ф25х3/4 Ekoplastik",
  "catalog_name": "Коліно PPR РН ф25х3/4, Ekoplastik",
  "category":     "plastic_ppr",
  "confidence":   95,
  "source":       "claude",
  "date":         "2026-07-28 10:30"
}
"""

import os
import json
import uuid
import time

DATA_DIR     = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
PENDING_FILE = os.path.join(DATA_DIR, "pending_cache.json")


# ─── Завантаження / збереження ───────────────────────────────────────────────

def _load() -> list:    # завантажує список pending записів з файлу
    if os.path.exists(PENDING_FILE):
        try:
            with open(PENDING_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save(records: list):   # зберігає список pending записів на диск
    try:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ pending_cache save: {e}")


# ─── Публічний API ───────────────────────────────────────────────────────────

def pending_add(original: str, brand_map: dict, normalized: str,
                catalog_name: str, category: str,
                confidence: int, source: str = "auto") -> bool:
    """
    Додає новий збіг в чергу на підтвердження.
    Не додає дублікати (той самий original + catalog_name).
    Повертає True якщо додано, False якщо вже є.
    """
    records = _load()

    # перевіряємо дублікати
    for r in records:
        if r.get('original') == original and r.get('catalog_name') == catalog_name:
            return False  # вже є в черзі

    records.append({
        "id":           str(uuid.uuid4())[:8],  # короткий id для кнопок
        "original":     original,
        "brand_map":    brand_map,
        "normalized":   normalized,
        "catalog_name": catalog_name,
        "category":     category,
        "confidence":   confidence,
        "source":       source,
        "date":         time.strftime("%Y-%m-%d %H:%M"),
    })
    _save(records)
    return True


def pending_count() -> int:     # повертає кількість записів що чекають підтвердження
    return len(_load())


def pending_get_batch(n: int = 5) -> list:  # повертає наступні N записів для показу адміну
    return _load()[:n]


def pending_confirm(ids: list) -> int:      # зберігає підтверджені записи в основний кеш; повертає к-ть збережених
    from clients.cache import cache_save
    records = _load()
    saved   = 0
    to_keep = []

    for r in records:
        if r["id"] in ids:
            # зберігаємо в основний кеш
            cache_save(
                r["original"],
                r.get("brand_map", {}),
                r["normalized"],
                r["catalog_name"],
                r["category"],
                r["confidence"],
            )
            saved += 1
        else:
            to_keep.append(r)

    _save(to_keep)
    return saved


def pending_reject(ids: list) -> int:   # видаляє відхилені записи з черги; повертає к-ть видалених
    records  = _load()
    to_keep  = [r for r in records if r["id"] not in ids]
    rejected = len(records) - len(to_keep)
    _save(to_keep)
    return rejected


def pending_confirm_all_batch(n: int = 5) -> int:   # підтверджує перші N записів одразу; повертає к-ть збережених
    batch = pending_get_batch(n)
    ids   = [r["id"] for r in batch]
    return pending_confirm(ids)


def pending_reject_all_batch(n: int = 5) -> int:    # відхиляє перші N записів одразу
    batch = pending_get_batch(n)
    ids   = [r["id"] for r in batch]
    return pending_reject(ids)


def pending_clear_all() -> int:     # очищає всю чергу (адмін-команда); повертає к-ть видалених
    records = _load()
    count   = len(records)
    _save([])
    return count
