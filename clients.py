"""
clients.py — Профілі клієнтів для бота підбору сантехніки.

СТРУКТУРА НА ДИСКУ:
clients/
  ├── index.json                ← {slug: ім'я} всіх клієнтів
  └── <slug>/
        ├── profile.json        ← ім'я, примітки, лічильник замовлень
        ├── preferences.json    ← виведені уподобання (виробники по категоріях)
        ├── cache.json          ← клієнтський кеш нормалізацій
        └── history/
              └── 2026-07-06_143022.json  ← збережені замовлення

ЛОГІКА ПРІОРИТЕТІВ ВИРОБНИКА (3 рівні):
  1. Підказка менеджера в чаті   (найвищий)
  2. Преференції клієнта з історії
  3. DEFAULT_BRAND_PRIORITY загальні

Преференції рахуються автоматично: після кожного замовлення бот дивиться
які виробники клієнт реально брав по кожній категорії.
"""

import os
import re
import json
import time

CLIENTS_DIR = "clients"
INDEX_FILE = os.path.join(CLIENTS_DIR, "index.json")

# Активний клієнт на чат (тримається в пам'яті процесу)
_active_clients: dict = {}  # chat_id -> slug

# Виробники які розпізнаємо в назвах товарів для преференцій
_KNOWN_BRANDS = ['raftec', 'ekoplastik', 'asg', 'ostendorf', 'plm', 'hidros',
                 'idmar', 'biasi', 'tatra', 'termojet', 'ecosofy', 'valrom',
                 'unipak', 'kan', 'herz', 'rehau', 'purmo', 'giacomini',
                 'lexline', 'danfoss', 'venta', 'eco']


# ─── Утиліти ─────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Ім'я клієнта → безпечна назва папки."""
    s = name.lower().strip()
    s = re.sub(r'[^а-яёіїєґa-z0-9]+', '_', s)
    return s.strip('_')

def _ensure_dirs():
    os.makedirs(CLIENTS_DIR, exist_ok=True)

def _load_index() -> dict:
    _ensure_dirs()
    if os.path.exists(INDEX_FILE):
        try:
            with open(INDEX_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_index(index: dict):
    _ensure_dirs()
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


# ─── CRUD клієнтів ───────────────────────────────────────────────────────────

def create_client(name: str, notes: str = "") -> tuple[bool, str]:
    """Створює клієнта. Повертає (успіх, slug_або_повідомлення)."""
    slug = _slugify(name)
    if not slug:
        return False, "Невалідне ім'я клієнта"
    index = _load_index()
    if slug in index:
        return False, f"Клієнт '{index[slug]}' вже існує"

    client_dir = os.path.join(CLIENTS_DIR, slug)
    os.makedirs(os.path.join(client_dir, "history"), exist_ok=True)

    profile = {
        "name": name,
        "notes": [notes] if notes else [],
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "orders_count": 0,
    }
    with open(os.path.join(client_dir, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    index[slug] = name
    _save_index(index)
    return True, slug


def find_client(name: str) -> str | None:
    """Шукає клієнта за ім'ям (нечітко). Повертає slug або None."""
    slug = _slugify(name)
    index = _load_index()
    if slug in index:
        return slug
    for s in index:
        if slug in s or s in slug:
            return s
    return None


def get_profile(slug: str) -> dict | None:
    path = os.path.join(CLIENTS_DIR, slug, "profile.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def save_profile(slug: str, profile: dict):
    path = os.path.join(CLIENTS_DIR, slug, "profile.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


def add_note(slug: str, note: str) -> bool:
    profile = get_profile(slug)
    if not profile:
        return False
    profile.setdefault("notes", []).append(note)
    save_profile(slug, profile)
    return True


def list_clients() -> dict:
    return _load_index()


# ─── Активний клієнт на сесію ────────────────────────────────────────────────

def set_active(chat_id: int, slug: str):
    _active_clients[chat_id] = slug

def get_active(chat_id: int) -> str | None:
    return _active_clients.get(chat_id)

def clear_active(chat_id: int):
    _active_clients.pop(chat_id, None)


# ─── Історія замовлень + автооновлення преференцій ──────────────────────────

def save_order(slug: str, результати: list, caption: str = ""):
    """Зберігає замовлення в history/ і перераховує преференції."""
    client_dir = os.path.join(CLIENTS_DIR, slug)
    if not os.path.isdir(client_dir):
        return
    hist_dir = os.path.join(client_dir, "history")
    os.makedirs(hist_dir, exist_ok=True)

    fname = time.strftime("%Y-%m-%d_%H%M%S") + ".json"
    order_data = {
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "caption": caption,
        "items": [
            {
                "original":   r.get("original", ""),
                "normalized": r.get("normalized", ""),
                "назва":      r.get("назва", ""),
                "qty":        r.get("qty", ""),
                "category":   r.get("category", "other"),
                "знайдено":   r.get("знайдено", False),
            }
            for r in результати if r
        ],
    }
    with open(os.path.join(hist_dir, fname), "w", encoding="utf-8") as f:
        json.dump(order_data, f, ensure_ascii=False, indent=2)

    profile = get_profile(slug)
    if profile:
        profile["orders_count"] = profile.get("orders_count", 0) + 1
        save_profile(slug, profile)

    _update_preferences(slug)


def _update_preferences(slug: str):
    """
    Перераховує преференції з усієї історії:
    які виробники клієнт бере — загалом і по кожній категорії.
    """
    hist_dir = os.path.join(CLIENTS_DIR, slug, "history")
    if not os.path.isdir(hist_dir):
        return

    brand_counts: dict = {}
    by_category: dict = {}
    total_items = 0

    for fname in os.listdir(hist_dir):
        if not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(hist_dir, fname), encoding="utf-8") as f:
                order = json.load(f)
        except Exception:
            continue
        for item in order.get("items", []):
            if not item.get("знайдено"):
                continue
            name_lower = item.get("назва", "").lower()
            category = item.get("category", "other")
            total_items += 1
            for brand in _KNOWN_BRANDS:
                if brand in name_lower:
                    brand_counts[brand] = brand_counts.get(brand, 0) + 1
                    by_category.setdefault(category, {})
                    by_category[category][brand] = by_category[category].get(brand, 0) + 1
                    break

    preferences = {
        "top_brands": sorted(brand_counts.items(), key=lambda x: -x[1]),
        "by_category": {
            cat: sorted(brands.items(), key=lambda x: -x[1])
            for cat, brands in by_category.items()
        },
        "total_items": total_items,
        "updated": time.strftime("%Y-%m-%d %H:%M"),
    }
    path = os.path.join(CLIENTS_DIR, slug, "preferences.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(preferences, f, ensure_ascii=False, indent=2)


def get_preferences(slug: str) -> dict:
    path = os.path.join(CLIENTS_DIR, slug, "preferences.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


# ─── Клієнтський кеш нормалізацій (пріоритетніший за загальний) ──────────────

def client_cache_lookup(slug: str, original: str) -> dict | None:
    """Точний + нечіткий (Jaccard >= 0.82) пошук у кеші клієнта."""
    path = os.path.join(CLIENTS_DIR, slug, "cache.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        return None

    key = re.sub(r'\s+', ' ', original.lower().strip())
    if key in cache:
        return cache[key]

    orig_tokens = set(re.findall(r'[а-яёіїєґa-z]+|[0-9]+', key))
    if not orig_tokens:
        return None
    best_score, best_entry = 0.0, None
    for cached_key, entry in cache.items():
        cached_tokens = set(re.findall(r'[а-яёіїєґa-z]+|[0-9]+', cached_key))
        if not cached_tokens:
            continue
        inter = len(orig_tokens & cached_tokens)
        union = len(orig_tokens | cached_tokens)
        score = inter / union if union else 0
        if score > best_score:
            best_score, best_entry = score, entry
    return best_entry if best_score >= 0.82 else None


def client_cache_save(slug: str, original: str, catalog_name: str,
                      category: str, confidence: int):
    """Зберігає збіг у кеш клієнта (тільки confidence >= 85)."""
    if confidence < 85:
        return
    client_dir = os.path.join(CLIENTS_DIR, slug)
    if not os.path.isdir(client_dir):
        return
    path = os.path.join(client_dir, "cache.json")
    cache = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    key = re.sub(r'\s+', ' ', original.lower().strip())
    cache[key] = {
        "catalog_name": catalog_name,
        "category":     category,
        "confidence":   confidence,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
