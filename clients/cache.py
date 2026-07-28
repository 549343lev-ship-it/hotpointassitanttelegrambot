"""
clients/cache.py — Кеш нормалізацій бота.

Зберігає пари: "що написав майстер" → "назва товару в каталозі"
TTL: auto-записи живуть 60 днів. confirmed і banned — вічні.
Мінімальний confidence для збереження: 95.
"""

import os
import re
import json
import time

DATA_DIR   = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
CACHE_FILE = os.path.join(DATA_DIR, "normalization_cache.json")

TTL_DAYS             = 60    # днів — скільки живе auto-запис
CACHE_MIN_CONFIDENCE = 95    # мінімальний confidence для збереження
FUZZY_THRESHOLD      = 0.82  # мінімальний Jaccard similarity для нечіткого збігу

_CACHE: dict = {}


# ─── Утиліти ─────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> set:        # розбиває текст на набір токенів для нечіткого порівняння
    return set(re.findall(r'[а-яёіїєґa-z]+|[0-9]+', text.lower()))

def _cache_key(original: str, brand_map: dict) -> str:     # будує унікальний ключ: текст + виробники з підказки менеджера
    brands_str = "|".join(f"{k}:{v[0]}" for k, v in sorted(brand_map.items()))
    key = re.sub(r'\s+', ' ', original.lower().strip())
    return f"{key}::{brands_str}"

def _today() -> str:    # повертає поточну дату у форматі YYYY-MM-DD
    return time.strftime("%Y-%m-%d")

def _is_expired(entry: dict) -> bool:   # перевіряє чи минув TTL для auto-запису (confirmed і banned — вічні)
    status = entry.get('status', 'auto')
    if status in ('confirmed', 'banned'):
        return False
    saved_at = entry.get('saved_at')
    if not saved_at:
        return False
    try:
        saved_ts = time.mktime(time.strptime(saved_at, "%Y-%m-%d"))
        age_days = (time.time() - saved_ts) / 86400
        return age_days > TTL_DAYS
    except Exception:
        return False


# ─── Завантаження / збереження ───────────────────────────────────────────────

def _load_cache():      # завантажує кеш з файлу при старті
    global _CACHE
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, encoding="utf-8") as f:
                _CACHE = json.load(f)
            total   = len(_CACHE)
            expired = sum(1 for e in _CACHE.values() if _is_expired(e))
            print(f"📋 Кеш: {total} записів ({expired} прострочених)")
        except Exception as e:
            print(f"⚠️ Помилка кешу: {e}")
            _CACHE = {}
    else:
        _CACHE = {}

def _save_cache():      # зберігає поточний стан кешу на диск
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_CACHE, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не вдалося зберегти кеш: {e}")


# ─── Нечіткий пошук ──────────────────────────────────────────────────────────

def _fuzzy_match(original: str, brand_map: dict) -> dict | None:    # шукає схожий запис через Jaccard similarity
    orig_tokens    = _tokenize(original)
    if not orig_tokens:
        return None
    current_brands = "|".join(f"{k}:{v[0]}" for k, v in sorted(brand_map.items()))
    best_score, best_entry = 0.0, None
    for cached_key, entry in _CACHE.items():
        if entry.get('status') == 'banned':
            continue
        if _is_expired(entry):
            continue
        if entry.get('confidence', 0) < CACHE_MIN_CONFIDENCE:
            continue
        parts         = cached_key.split("::", 1)
        cached_brands = parts[1] if len(parts) > 1 else ""
        if cached_brands not in (current_brands, ""):
            continue
        cached_tokens = _tokenize(parts[0])
        if not cached_tokens:
            continue
        intersection = len(orig_tokens & cached_tokens)
        union        = len(orig_tokens | cached_tokens)
        score        = intersection / union if union > 0 else 0.0
        if score > best_score:
            best_score, best_entry = score, entry
    return best_entry if best_score >= FUZZY_THRESHOLD else None


# ─── Публічний API ───────────────────────────────────────────────────────────

def cache_lookup(original: str, brand_map: dict) -> dict | None:    # шукає запис: точний збіг → нечіткий; пропускає banned і прострочені
    keys = [_cache_key(original, brand_map)]
    base = _cache_key(original, {})
    if base not in keys:
        keys.append(base)
    for key in keys:
        entry = _CACHE.get(key)
        if not entry:
            continue
        if entry.get('status') == 'banned':
            continue
        if _is_expired(entry):
            continue
        if is_banned(original, entry.get('catalog_name', '')):
            continue
        return entry
    result = _fuzzy_match(original, brand_map)
    if result and not is_banned(original, result.get('catalog_name', '')):
        return result
    return None


def cache_save(original: str, brand_map: dict, normalized: str,
               catalog_name: str, category: str, confidence: int):  # зберігає auto-запис якщо confidence >= 95
    if confidence < CACHE_MIN_CONFIDENCE:
        return
    key      = _cache_key(original, brand_map)
    existing = _CACHE.get(key)
    if existing:
        st = existing.get('status')
        if st == 'confirmed':
            return
        if st == 'banned':
            if existing.get('catalog_name') == catalog_name:
                return
            i = 1
            while f"{key}::ban{i}" in _CACHE:
                i += 1
            _CACHE[f"{key}::ban{i}"] = existing
    _CACHE[key] = {
        "normalized":   normalized,
        "catalog_name": catalog_name,
        "category":     category,
        "confidence":   confidence,
        "status":       "auto",
        "saved_at":     _today(),
    }
    _save_cache()


def cache_confirm(original: str, brand_map: dict, normalized: str,
                  catalog_name: str, category: str):    # зберігає підтверджений адміном запис (confidence=100, без TTL)
    key = _cache_key(original, brand_map)
    _CACHE[key] = {
        "normalized":   normalized,
        "catalog_name": catalog_name,
        "category":     category,
        "confidence":   100,
        "status":       "confirmed",
        "saved_at":     _today(),
    }
    _save_cache()


def cache_set_status(original: str, catalog_name: str, status: str) -> bool:   # встановлює статус confirmed/banned для пари
    key_part = re.sub(r'\s+', ' ', original.lower().strip())
    updated  = False
    for cached_key, entry in _CACHE.items():
        if cached_key.split("::")[0] == key_part and entry.get('catalog_name') == catalog_name:
            entry['status']   = status
            entry['saved_at'] = _today()
            updated = True
    if updated:
        _save_cache()
    return updated


def cache_ban_pair(original: str, catalog_name: str, category: str = "other") -> None:     # банить пару оригінал→товар у всіх записах
    key_part = re.sub(r'\s+', ' ', original.lower().strip())
    touched  = False
    for cached_key, entry in _CACHE.items():
        if cached_key.split("::")[0] == key_part and entry.get('catalog_name') == catalog_name:
            entry['status']   = 'banned'
            entry['saved_at'] = _today()
            touched = True
    if not touched:
        base = _cache_key(original, {})
        i = 1
        while f"{base}::ban{i}" in _CACHE:
            i += 1
        _CACHE[f"{base}::ban{i}"] = {
            "normalized":   original,
            "catalog_name": catalog_name,
            "category":     category,
            "confidence":   0,
            "status":       "banned",
            "saved_at":     _today(),
        }
    _save_cache()


def is_banned(original: str, catalog_name: str) -> bool:    # перевіряє чи заборонена пара оригінал→товар
    key_part = re.sub(r'\s+', ' ', original.lower().strip())
    for cached_key, entry in _CACHE.items():
        if entry.get('status') != 'banned':
            continue
        if cached_key.split("::")[0] == key_part and entry.get('catalog_name') == catalog_name:
            return True
    return False


def cache_delete(original: str, brand_map: dict) -> bool:   # видаляє запис з кешу
    key = _cache_key(original, brand_map)
    if key in _CACHE:
        del _CACHE[key]
        _save_cache()
        return True
    return False


def cache_cleanup_expired() -> int:     # видаляє всі прострочені auto-записи; повертає к-ть видалених
    to_delete = [k for k, v in _CACHE.items()
                 if v.get('status', 'auto') == 'auto' and _is_expired(v)]
    for k in to_delete:
        del _CACHE[k]
    if to_delete:
        _save_cache()
        print(f"🧹 Кеш: видалено {len(to_delete)} прострочених")
    return len(to_delete)


def get_cache_stats() -> dict:  # повертає статистику кешу (total, auto, confirmed, banned, expired)
    total     = len(_CACHE)
    confirmed = sum(1 for e in _CACHE.values() if e.get('status') == 'confirmed')
    banned    = sum(1 for e in _CACHE.values() if e.get('status') == 'banned')
    expired   = sum(1 for e in _CACHE.values() if _is_expired(e))
    auto      = total - confirmed - banned
    return {'total': total, 'auto': auto, 'confirmed': confirmed,
            'banned': banned, 'expired': expired,
            'ttl_days': TTL_DAYS, 'min_conf': CACHE_MIN_CONFIDENCE}


def get_cache() -> dict:    # повертає весь кеш як словник
    return _CACHE


# ─── Ініціалізація ───────────────────────────────────────────────────────────
_load_cache()
