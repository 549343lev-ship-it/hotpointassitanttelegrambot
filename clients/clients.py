"""
clients.py — Профілі клієнтів для бота підбору сантехніки.

СТРУКТУРА НА ДИСКУ:
clients/
  ├── index.json                ← {slug: ім'я} всіх клієнтів
  └── <slug>/
        ├── profile.json        ← ім'я, примітки, лічильник замовлень
        ├── preferences.json    ← уподобання виробників по категоріях
        ├── cache.json          ← клієнтський кеш нормалізацій
        ├── history/            ← збережені замовлення
        └── examples/           ← пари фото+рахунок для навчання
              ├── приклад_1/
              │     ├── photo.jpg
              │     └── invoice.xls(x)
              └── приклад_2/...
"""

import os
import re
import json
import time

DATA_DIR    = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
CLIENTS_DIR = os.path.join(DATA_DIR, "clients")
INDEX_FILE  = os.path.join(CLIENTS_DIR, "index.json")

try:
    from config.settings import ADMIN_ID
except ImportError:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

_active_clients: dict = {}  # chat_id → slug

_KNOWN_BRANDS = ['raftec', 'ekoplastik', 'asg', 'ostendorf', 'plm', 'hidros',
                 'idmar', 'biasi', 'tatra', 'termojet', 'ecosoft', 'valrom',
                 'unipak', 'kan', 'herz', 'rehau', 'purmo', 'giacomini',
                 'lexline', 'danfoss', 'venta', 'eco']


# ─── Утиліти ─────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:     # ім'я клієнта → безпечна назва папки
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

def _jaccard(a: str, b: str) -> float:     # нечіткий збіг двох рядків через токени
    ta = set(re.findall(r'[а-яёіїєґa-z0-9]+', a.lower()))
    tb = set(re.findall(r'[а-яёіїєґa-z0-9]+', b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# ─── CRUD клієнтів ───────────────────────────────────────────────────────────

def create_client(name: str, notes: str = "", owner_id: int = 0) -> tuple[bool, str]:  # створює клієнта; повертає (успіх, slug або повідомлення)
    slug = _slugify(name)
    if not slug:
        return False, "Невалідне ім'я клієнта"
    index = _load_index()
    if slug in index:
        return False, f"existing:{slug}"   # спеціальний маркер: клієнт вже є

    client_dir = os.path.join(CLIENTS_DIR, slug)
    os.makedirs(os.path.join(client_dir, "history"),  exist_ok=True)
    os.makedirs(os.path.join(client_dir, "examples"), exist_ok=True)

    profile = {
        "name":         name,
        "notes":        [notes] if notes else [],
        "created":      time.strftime("%Y-%m-%d %H:%M"),
        "orders_count": 0,
        "examples_count": 0,
        "owner_id":     owner_id,   # chat_id менеджера що створив
    }
    with open(os.path.join(client_dir, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

    index[slug] = name
    _save_index(index)
    return True, slug


def find_client(name: str) -> str | None:   # точний пошук клієнта за ім'ям; повертає slug або None
    slug  = _slugify(name)
    index = _load_index()
    if slug in index:
        return slug
    for s in index:
        if slug in s or s in slug:
            return s
    return None


def find_similar_clients(name: str, threshold: float = 0.4) -> list[tuple[str, str, float]]:   # нечіткий пошук схожих клієнтів; повертає список (slug, ім'я, score) відсортований за схожістю
    index   = _load_index()
    results = []
    for slug, cname in index.items():
        score = _jaccard(name, cname)
        if score >= threshold:
            results.append((slug, cname, score))
    results.sort(key=lambda x: -x[2])
    return results[:5]  # топ-5


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


def list_clients(owner_id: int = 0) -> dict:
    """Повертає клієнтів: адмін бачить всіх, менеджер — тільки своїх."""
    index = _load_index()
    if owner_id == ADMIN_ID or owner_id == 0:
        return index
    result = {}
    for slug, name in index.items():
        profile = get_profile(slug)
        if not profile:
            continue
        pid = profile.get('owner_id', 0)
        if pid == owner_id or pid == 0:
            result[slug] = name
    return result


def list_profiles(owner_id: int = 0) -> list[dict]:
    """Повертає список профілів клієнтів для даного менеджера (або всіх для адміна)."""
    filtered = list_clients(owner_id=owner_id)
    result = []
    for slug in filtered:
        p = get_profile(slug)
        if p:
            p['slug'] = slug
            result.append(p)
    return sorted(result, key=lambda x: x.get('name', ''))


def create_profile(slug: str, name: str, owner_id: int = 0):
    """Створює профіль клієнта (спрощений виклик для client_handler)."""
    client_dir = os.path.join(CLIENTS_DIR, slug)
    os.makedirs(os.path.join(client_dir, "history"),  exist_ok=True)
    os.makedirs(os.path.join(client_dir, "examples"), exist_ok=True)
    profile = {
        "name":           name,
        "notes":          [],
        "created":        time.strftime("%Y-%m-%d %H:%M"),
        "orders_count":   0,
        "examples_count": 0,
        "owner_id":       owner_id,
    }
    with open(os.path.join(client_dir, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    index = _load_index()
    index[slug] = name
    _save_index(index)


# ─── Активний клієнт ─────────────────────────────────────────────────────────

def set_active(chat_id: int, slug: str):
    _active_clients[chat_id] = slug

def get_active(chat_id: int) -> str | None:
    return _active_clients.get(chat_id)

def clear_active(chat_id: int):
    _active_clients.pop(chat_id, None)


# ─── Навчання: examples/ (фото + рахунок) ────────────────────────────────────

def get_next_example_dir(slug: str) -> tuple[str, int]:     # повертає (шлях до нової папки приклад_N, номер N)
    examples_dir = os.path.join(CLIENTS_DIR, slug, "examples")
    os.makedirs(examples_dir, exist_ok=True)
    n = 1
    while os.path.exists(os.path.join(examples_dir, f"приклад_{n}")):
        n += 1
    path = os.path.join(examples_dir, f"приклад_{n}")
    os.makedirs(path, exist_ok=True)
    return path, n


def save_example_photo(slug: str, example_n: int, photo_bytes: bytes, ext: str = "jpg") -> str:    # зберігає фото в examples/приклад_N/photo.ext; повертає шлях
    path = os.path.join(CLIENTS_DIR, slug, "examples", f"приклад_{example_n}", f"photo.{ext}")
    with open(path, "wb") as f:
        f.write(photo_bytes)
    return path


def save_example_invoice(slug: str, example_n: int, file_bytes: bytes, ext: str = "xlsx") -> str:  # зберігає рахунок в examples/приклад_N/invoice.ext; повертає шлях
    path = os.path.join(CLIENTS_DIR, slug, "examples", f"приклад_{example_n}", f"invoice.{ext}")
    with open(path, "wb") as f:
        f.write(file_bytes)
    return path


def parse_invoice(invoice_path: str) -> list[str]:  # парсить рахунок XLS/XLSX; повертає список назв товарів (колонка "Номенклатура")
    try:
        import pandas as pd
        engine = 'xlrd' if invoice_path.endswith('.xls') else 'openpyxl'
        df     = pd.read_excel(invoice_path, header=None, engine=engine)

        # Шукаємо колонку "Номенклатура" по всіх рядках і колонках
        header_row = None
        nom_col    = None
        for ri in range(min(15, len(df))):
            for ci in range(len(df.columns)):
                val = str(df.iloc[ri, ci]).strip().lower()
                if 'номенклатур' in val:
                    header_row = ri
                    nom_col    = ci
                    break
            if nom_col is not None:
                break

        if nom_col is None:
            # Fallback: колонка 1 (другий стовпець) після першого рядка
            nom_col    = 1
            header_row = 0

        items = []
        for ri in range((header_row or 0) + 1, len(df)):
            val = str(df.iloc[ri, nom_col]).strip()
            if not val or val == 'nan' or val == 'None':
                continue
            # Пропускаємо рядки які явно є підписами/реквізитами
            if any(skip in val for skip in ['Покупець', 'Виконавець', 'оплат', 'реквізит', 'пропозиці']):
                continue
            # Прибираємо залишки упаковки {N/N} і {N}
            val = re.sub(r'\s*\{[^}]+\}', '', val).strip()
            # Прибираємо NEW! префікс
            val = re.sub(r'^NEW!\s*', '', val).strip()
            if val and len(val) > 3:
                items.append(val)
        return items
    except Exception as e:
        print(f"⚠️ parse_invoice: {e}")
        return []


def learn_from_example(slug: str, example_n: int,
                       pairs: list[dict],
                       scope: str = "both") -> dict:   # зберігає пари оригінал→товар; scope: client | global | both
    """
    pairs: [{'original': '...', 'catalog_name': '...', 'category': '...'}, ...]
    Викликається після того як Gemini зіставив рядки фото з рахунком.

    scope:
      'client' — тільки в кеш цього клієнта (як було раніше)
      'global' — тільки в глобальний кеш бота (працює для ВСІХ клієнтів)
      'both'   — і туди, і туди (за замовчуванням)

    Повертає {'client': n, 'global': n, 'global_updated': n, 'total': n}
    """
    from clients.cache import cache_learn_bulk

    client_saved = 0
    g = {'saved': 0, 'updated': 0, 'skipped_banned': 0, 'skipped_empty': 0}

    valid = [p for p in pairs
             if str(p.get('original') or '').strip()
             and str(p.get('catalog_name') or '').strip()]

    if scope in ('client', 'both') and slug:
        for p in valid:
            original     = str(p['original']).strip()
            catalog_name = str(p['catalog_name']).strip()
            category     = str(p.get('category') or 'other')
            client_cache_save(slug, original, catalog_name, category, 100)
            client_cache_set_status(slug, original, catalog_name, 'confirmed')
            client_saved += 1

    if scope in ('global', 'both'):
        g = cache_learn_bulk(valid, source='training')

    # Оновлюємо лічильник прикладів
    if slug:
        profile = get_profile(slug)
        if profile:
            profile['examples_count'] = profile.get('examples_count', 0) + 1
            save_profile(slug, profile)

    return {
        'client':         client_saved,
        'global':         g['saved'],
        'global_updated': g['updated'],
        'skipped_banned': g['skipped_banned'],
        'total':          len(valid),
    }


def learn_global(pairs: list[dict], source: str = "training") -> dict:  # навчання глобального кешу бота без прив'язки до клієнта
    """Пряме навчання бота парами оригінал→товар. Без клієнта, без прикладів."""
    from clients.cache import cache_learn_bulk
    valid = [p for p in pairs
             if str(p.get('original') or '').strip()
             and str(p.get('catalog_name') or '').strip()]
    return cache_learn_bulk(valid, source=source)


def list_examples(slug: str) -> list[dict]:     # повертає список прикладів клієнта з інфо про наявність фото/рахунку
    examples_dir = os.path.join(CLIENTS_DIR, slug, "examples")
    if not os.path.isdir(examples_dir):
        return []
    result = []
    for d in sorted(os.listdir(examples_dir)):
        dpath = os.path.join(examples_dir, d)
        if not os.path.isdir(dpath):
            continue
        files   = os.listdir(dpath)
        has_photo   = any(f.startswith('photo') for f in files)
        has_invoice = any(f.startswith('invoice') for f in files)
        result.append({
            'name':        d,
            'path':        dpath,
            'has_photo':   has_photo,
            'has_invoice': has_invoice,
            'photo_path':  next((os.path.join(dpath, f) for f in files if f.startswith('photo')), None),
            'invoice_path':next((os.path.join(dpath, f) for f in files if f.startswith('invoice')), None),
        })
    return result


# ─── Кеш клієнта ─────────────────────────────────────────────────────────────

def client_cache_lookup(slug: str, original: str,
                        required_brand_tokens: list = None) -> dict | None:  # точний + нечіткий (Jaccard≥0.82) пошук у кеші клієнта; banned і невідповідні бренди ігноруються
    path = os.path.join(CLIENTS_DIR, slug, "cache.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        return None

    def _ok(entry: dict) -> bool:
        if entry.get('status') == 'banned':
            return False
        if required_brand_tokens:
            name_lower = entry.get('catalog_name', '').lower()
            if not any(tok.lower() in name_lower for tok in required_brand_tokens):
                return False
        return True

    key = re.sub(r'\s+', ' ', original.lower().strip())
    if key in cache and _ok(cache[key]):
        return cache[key]

    orig_tokens = set(re.findall(r'[а-яёіїєґa-z]+|[0-9]+', key))
    if not orig_tokens:
        return None
    best_score, best_entry = 0.0, None
    for cached_key, entry in cache.items():
        if not _ok(entry):
            continue
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
                      category: str, confidence: int):  # зберігає збіг у кеш клієнта; не перезаписує confirmed і banned
    if confidence < 85:
        return
    client_dir = os.path.join(CLIENTS_DIR, slug)
    if not os.path.isdir(client_dir):
        return
    path  = os.path.join(client_dir, "cache.json")
    cache = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    key      = re.sub(r'\s+', ' ', original.lower().strip())
    existing = cache.get(key)
    if existing:
        st = existing.get('status')
        if st == 'confirmed':
            return
        if st == 'banned':
            if existing.get('catalog_name') == catalog_name:
                return
            i = 1
            while f"{key}::ban{i}" in cache:
                i += 1
            cache[f"{key}::ban{i}"] = existing
    cache[key] = {
        "catalog_name": catalog_name,
        "category":     category,
        "confidence":   confidence,
        "status":       "auto",
        "saved_at":     time.strftime("%Y-%m-%d"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def client_cache_set_status(slug: str, original_or_key: str,
                            catalog_name: str, status: str) -> bool:    # встановлює статус confirmed/banned для запису в кеші клієнта
    path = os.path.join(CLIENTS_DIR, slug, "cache.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        return False

    # Спочатку пробуємо точний ключ (з cb_cache_entry_decision)
    if original_or_key in cache:
        entry = cache[original_or_key]
        if entry.get('catalog_name') == catalog_name or not catalog_name:
            entry['status']   = status
            entry['saved_at'] = time.strftime("%Y-%m-%d")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
            return True

    # Fallback: нормалізований ключ
    key   = re.sub(r'\s+', ' ', original_or_key.lower().strip())
    entry = cache.get(key)
    if entry and (entry.get('catalog_name') == catalog_name or not catalog_name):
        entry['status']   = status
        entry['saved_at'] = time.strftime("%Y-%m-%d")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        return True
    return False


def client_cache_delete_keys(slug: str, keys: list[str]) -> int:    # видаляє конкретні ключі з кешу клієнта; повертає кількість видалених
    path = os.path.join(CLIENTS_DIR, slug, "cache.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        return 0
    deleted = 0
    for k in keys:
        if k in cache:
            del cache[k]
            deleted += 1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    return deleted


def client_cache_clear(slug: str, status_filter: str = None) -> int:   # очищає кеш клієнта; status_filter='auto'/'confirmed'/'banned' або None=всі; повертає кількість видалених
    path = os.path.join(CLIENTS_DIR, slug, "cache.json")
    if not os.path.exists(path):
        return 0
    try:
        with open(path, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        return 0
    if status_filter is None:
        count = len(cache)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({}, f)
        return count
    to_delete = [k for k, v in cache.items()
                 if v.get('status', 'auto') == status_filter]
    for k in to_delete:
        del cache[k]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    return len(to_delete)


def get_client_cache(slug: str) -> dict:    # повертає весь кеш клієнта
    path = os.path.join(CLIENTS_DIR, slug, "cache.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_order_count(slug: str) -> int:
    profile = get_profile(slug)
    return profile.get('orders_count', 0) if profile else 0


def clear_client_cache(slug: str, mode: str = 'all') -> int:
    """mode: 'all' | 'auto' | 'confirmed' | 'banned'"""
    status_filter = None if mode == 'all' else mode
    return client_cache_clear(slug, status_filter)


  # статистика кешу клієнта: total/auto/confirmed/banned
    cache     = get_client_cache(slug)
    confirmed = sum(1 for v in cache.values() if v.get('status') == 'confirmed')
    banned    = sum(1 for v in cache.values() if v.get('status') == 'banned')
    auto      = len(cache) - confirmed - banned
    return {'total': len(cache), 'auto': auto, 'confirmed': confirmed, 'banned': banned}


# ─── Історія + преференції ────────────────────────────────────────────────────

def save_order(slug: str, результати: list, caption: str = ""):  # зберігає замовлення в history/ і перераховує преференції
    client_dir = os.path.join(CLIENTS_DIR, slug)
    if not os.path.isdir(client_dir):
        return
    hist_dir = os.path.join(client_dir, "history")
    os.makedirs(hist_dir, exist_ok=True)
    fname      = time.strftime("%Y-%m-%d_%H%M%S") + ".json"
    order_data = {
        "date":    time.strftime("%Y-%m-%d %H:%M"),
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


def _update_preferences(slug: str):    # перераховує преференції виробників з усієї історії
    hist_dir = os.path.join(CLIENTS_DIR, slug, "history")
    if not os.path.isdir(hist_dir):
        return
    brand_counts: dict = {}
    by_category: dict  = {}
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
            category   = item.get("category", "other")
            total_items += 1
            for brand in _KNOWN_BRANDS:
                if brand in name_lower:
                    brand_counts[brand] = brand_counts.get(brand, 0) + 1
                    by_category.setdefault(category, {})
                    by_category[category][brand] = by_category[category].get(brand, 0) + 1
                    break
    prefs = {
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
        json.dump(prefs, f, ensure_ascii=False, indent=2)


def get_preferences(slug: str) -> dict:
    path = os.path.join(CLIENTS_DIR, slug, "preferences.json")
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}
