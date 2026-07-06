"""
cache.py — Кеш нормалізацій для бота підбору сантехніки.

Зберігає пари: "що написав майстер" → "назва товару в каталозі"
Росте з кожним замовленням. Перевірка займає мілісекунди, нічого не коштує.

Файл на диску: normalization_cache.json
Формат запису:
  {
    "мрз кут. - 2 шт::plastic_ppr:raftec": {
      "normalized":   "Коліно PPR РН ф 25х3/4\", RAFTEC",
      "catalog_name": "Коліно PPR РН ф 25 мм, 3/4\", RAFTEC",
      "category":     "plastic_ppr",
      "confidence":   95
    }
  }

Ключ = оригінальний текст (нижній регістр) + :: + виробники з підказки менеджера.
Виробники в ключі потрібні щоб один і той самий рядок з різними підказками
давав різні результати.
"""

import os
import re
import json

CACHE_FILE = "normalization_cache.json"
_CACHE: dict = {}


# ─── Утиліти ────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> set:
    return set(re.findall(r'[а-яёіїєґa-z]+|[0-9]+', text.lower()))

def _cache_key(original: str, brand_map: dict) -> str:
    """
    Ключ = нормалізований оригінал + виробники з підказки менеджера.
    Приклад: "мрз кут. - 2 шт::plastic_ppr:raftec"
    """
    brands_str = "|".join(f"{k}:{v[0]}" for k, v in sorted(brand_map.items()))
    key = re.sub(r'\s+', ' ', original.lower().strip())
    return f"{key}::{brands_str}"


# ─── Завантаження / збереження ───────────────────────────────────────────────

def _load_cache():
    global _CACHE
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                _CACHE = json.load(f)
            print(f"📋 Кеш нормалізацій завантажено: {len(_CACHE)} записів")
        except Exception as e:
            print(f"⚠️ Помилка завантаження кешу: {e}")
            _CACHE = {}
    else:
        print("📋 Кеш нормалізацій порожній (новий)")
        _CACHE = {}

def _save_cache():
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не вдалося зберегти кеш: {e}")


# ─── Нечіткий пошук ─────────────────────────────────────────────────────────

def _fuzzy_match(original: str, brand_map: dict, threshold: float = 0.82) -> dict | None:
    """
    Нечіткий пошук в кеші через Jaccard similarity по токенах.
    "МРЗ кут - 2 шт" і "МРЗ кут. - 2шт" дадуть score ~0.9 → збіг.
    Виробники в підказці мають збігатись точно.
    """
    orig_tokens = _tokenize(original)
    if not orig_tokens:
        return None

    current_brands = "|".join(f"{k}:{v[0]}" for k, v in sorted(brand_map.items()))

    best_score = 0.0
    best_entry = None

    for cached_key, entry in _CACHE.items():
        # Перевіряємо виробників
        parts = cached_key.split("::", 1)
        cached_brands = parts[1] if len(parts) > 1 else ""
        if cached_brands != current_brands:
            continue

        cached_tokens = _tokenize(parts[0])
        if not cached_tokens:
            continue

        # Jaccard similarity
        intersection = len(orig_tokens & cached_tokens)
        union = len(orig_tokens | cached_tokens)
        score = intersection / union if union > 0 else 0.0

        if score > best_score:
            best_score = score
            best_entry = entry

    return best_entry if best_score >= threshold else None


# ─── Публічний API ───────────────────────────────────────────────────────────

def cache_lookup(original: str, brand_map: dict) -> dict | None:
    """
    Шукає оригінальний текст в кеші.
    Спочатку точний збіг, потім нечіткий (Jaccard >= 0.82).
    Повертає запис або None.
    """
    # Точний збіг
    key = _cache_key(original, brand_map)
    if key in _CACHE:
        return _CACHE[key]
    # Нечіткий
    return _fuzzy_match(original, brand_map)


def cache_save(original: str, brand_map: dict, normalized: str,
               catalog_name: str, category: str, confidence: int):
    """
    Зберігає успішний збіг в кеш.
    Зберігається тільки якщо confidence >= 85 (щоб не кешувати сумнівні результати).
    """
    if confidence < 85:
        return
    key = _cache_key(original, brand_map)
    _CACHE[key] = {
        "normalized":   normalized,
        "catalog_name": catalog_name,
        "category":     category,
        "confidence":   confidence,
    }
    _save_cache()


def cache_delete(original: str, brand_map: dict) -> bool:
    """
    Видаляє запис з кешу (якщо менеджер каже що результат неправильний).
    Повертає True якщо щось видалив.
    """
    key = _cache_key(original, brand_map)
    if key in _CACHE:
        del _CACHE[key]
        _save_cache()
        return True
    return False


def get_cache() -> dict:
    """Повертає весь кеш (для команди /кеш в Telegram)."""
    return _CACHE


# ─── Ініціалізація при імпорті ───────────────────────────────────────────────
_load_cache()
