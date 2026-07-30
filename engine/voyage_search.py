"""
engine/voyage_search.py — Векторний пошук через Voyage AI.

АРХІТЕКТУРА:
  1. Один раз (локально): build_embeddings() → catalog_embeddings.npz
     Зберігає: vectors, names, categories, routing_paths
  2. При старті сервера: load_embeddings() → завантажує npz в пам'ять
  3. При кожному запиті: voyage_search(query, routing_path=...) →
       а) embed query → вектор
       б) фільтруємо по routing_path (ієрархія: cat|group|subgroup)
       в) cosine similarity тільки по відфільтрованій підмножині
       г) повертаємо топ-N з score

ROUTING_PATH — ієрархічний шлях з xlsx-структури:
  "fasteners_sealants"                → вся категорія
  "fasteners_sealants|KVADO"          → тільки KVADO в кріпленнях
  "fasteners_sealants|Walraven|Xомути"→ тільки хомути Walraven
  "plastic_ppr|EKOPLASTIK"            → тільки Ekoplastik
  "sewage|OSTENDORF"                  → тільки Ostendorf каналізація

  Пошук завжди від найвужчого вузла вгору:
    subgroup → group → category → весь каталог

THRESHOLD_AUTO  = 0.82  → повертаємо без Claude
THRESHOLD_MIN   = 0.60  → нижче — нічого не знайдено
"""

import os
import time
import numpy as np

DATA_DIR        = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
EMBEDDINGS_FILE = os.path.join(DATA_DIR, "catalog_embeddings.npz")
VOYAGE_KEY      = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL    = "voyage-3"
BATCH_SIZE      = 128
THRESHOLD_AUTO  = 0.82
THRESHOLD_MIN   = 0.60

# ─── Суміжні категорії (з search.py) — для fallback пошуку ───────────────────
# Якщо у своїй категорії нічого — шукаємо в суміжних зі зниженим score
SIMILAR_CATS = {
    'plastic_ppr':              ['adapters_reducers', 'heating'],
    'adapters_reducers':        ['plastic_ppr', 'shutoff_valves', 'heating'],
    'heating':                  ['plastic_ppr', 'shutoff_valves', 'adapters_reducers'],
    'push_systems':             ['metal_plastic', 'plastic_ppr'],
    'metal_plastic':            ['push_systems', 'adapters_reducers'],
    'sewage':                   ['siphons_fittings'],
    'siphons_fittings':         ['sewage'],
    'shutoff_valves':           ['adapters_reducers', 'safety_valves', 'filtration'],
    'filtration':               ['shutoff_valves'],
    'underfloor_heating':       ['metal_plastic', 'push_systems'],
    'insulation':               ['fasteners_sealants'],
    'fasteners_sealants':       ['insulation'],
    'boilers':                  ['heating', 'shutoff_valves'],
}

# Несумісні пари (category_запиту, category_товару) → score = 0.0
INCOMPATIBLE_CATS = {
    ('adapters_reducers',        'plastic_ppr'),
    ('plastic_ppr',              'adapters_reducers'),
    ('shutoff_valves',           'plastic_ppr'),
    ('plastic_ppr',              'shutoff_valves'),
    ('sewage',                   'plastic_ppr'),
    ('plastic_ppr',              'sewage'),
    ('underfloor_heating',       'plastic_ppr'),
    ('boilers',                  'radiators_radiatorsvalve'),
    ('radiators_radiatorsvalve', 'boilers'),
    ('fasteners_sealants',       'plastic_ppr'),
    ('fasteners_sealants',       'sewage'),
    ('fasteners_sealants',       'adapters_reducers'),
}

# ─── Глобальний стан ─────────────────────────────────────────────────────────

_vectors:       np.ndarray | None = None   # (N, dim) float32
_names:         list[str]         = []     # назва товару
_categories:    list[str]         = []     # category_code товару
_routing_paths: list[str]         = []     # "cat|group|subgroup" або "cat|group"
_ready = False


def _get_client():      # повертає voyage client (lazy init)
    import voyageai
    return voyageai.Client(api_key=VOYAGE_KEY)


# ─── Побудова routing_path ────────────────────────────────────────────────────

def make_routing_path(category: str, group: str, subgroup: str) -> str:    # будує ієрархічний шлях з категорії/групи/підгрупи товару
    """
    "plastic_ppr" + "EKOPLASTIK" + "Ekoplastik труба" → "plastic_ppr|EKOPLASTIK|Ekoplastik труба"
    "fasteners_sealants" + "KVADO" + ""               → "fasteners_sealants|KVADO"
    "sewage" + "" + ""                                 → "sewage"
    """
    parts = [category]
    if group and group.strip():
        parts.append(group.strip())
    if subgroup and subgroup.strip():
        parts.append(subgroup.strip())
    return '|'.join(parts)


def path_ancestors(path: str) -> list[str]:     # повертає шлях і всі батьківські вузли від вузького до широкого
    """
    "cat|grp|sub" → ["cat|grp|sub", "cat|grp", "cat"]
    Використовується для fallback: спочатку вузько, потім ширше.
    """
    parts = path.split('|')
    return ['|'.join(parts[:i]) for i in range(len(parts), 0, -1)]


# ─── Побудова / завантаження embeddings ──────────────────────────────────────

def build_embeddings(catalog: list[dict], force: bool = False) -> bool:     # будує embeddings для всього каталогу і зберігає в npz з routing_paths; запускати локально
    """
    Запускати ЛОКАЛЬНО через build_embeddings.py, не на сервері.
    Зберігає: vectors, names, categories, routing_paths, built.
    """
    global _vectors, _names, _categories, _routing_paths, _ready

    if not VOYAGE_KEY:
        print("⚠️ VOYAGE_API_KEY не заданий", flush=True)
        return False
    if os.path.exists(EMBEDDINGS_FILE) and not force:
        print(f"💾 Embeddings вже є: {EMBEDDINGS_FILE}", flush=True)
        return load_embeddings()
    if not catalog:
        print("⚠️ Каталог порожній", flush=True)
        return False

    print(f"🔨 Будую embeddings для {len(catalog)} товарів...", flush=True)
    client = _get_client()

    texts         = []
    names         = []
    categories    = []
    routing_paths = []

    for item in catalog:
        name     = item.get('name', '')
        cat      = item.get('category', '')
        group    = item.get('group', '')
        subgroup = item.get('subgroup', '')
        rpath    = make_routing_path(cat, group, subgroup)

        # Текст для embedding: routing_path + назва → дає моделі повний контекст
        # Приклад: "fasteners_sealants|KVADO: Хомут DN15 сталевий KVADO"
        texts.append(f"{rpath}: {name}")
        names.append(name)
        categories.append(cat)
        routing_paths.append(rpath)

    # Батчами
    all_vectors   = []
    total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
    t0 = time.time()

    for i in range(0, len(texts), BATCH_SIZE):
        batch     = texts[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        try:
            result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
            all_vectors.extend(result.embeddings)
        except Exception as e:
            print(f"  ❌ Батч {batch_num}: {e}", flush=True)
            time.sleep(2)
            try:
                result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
                all_vectors.extend(result.embeddings)
            except Exception as e2:
                print(f"  ❌ Повтор провалився: {e2}", flush=True)
                return False
        if batch_num % 10 == 0 or batch_num == total_batches:
            pct = batch_num / total_batches * 100
            print(f"  ⚡ {pct:.0f}% ({batch_num}/{total_batches}, {time.time()-t0:.0f}с)", flush=True)
        time.sleep(0.05)

    vectors_arr = np.array(all_vectors, dtype=np.float32)
    np.savez_compressed(
        EMBEDDINGS_FILE,
        vectors       = vectors_arr,
        names         = np.array(names,         dtype=object),
        categories    = np.array(categories,    dtype=object),
        routing_paths = np.array(routing_paths, dtype=object),
        built         = np.array([time.strftime("%Y-%m-%d %H:%M")]),
    )
    _vectors       = vectors_arr
    _names         = names
    _categories    = categories
    _routing_paths = routing_paths
    _ready         = True
    print(f"✅ Embeddings: {len(names)} товарів, {vectors_arr.shape[1]}d, {time.time()-t0:.0f}с", flush=True)
    return True


def load_embeddings() -> bool:      # завантажує embeddings з npz в пам'ять; повертає True якщо успішно
    global _vectors, _names, _categories, _routing_paths, _ready

    if not os.path.exists(EMBEDDINGS_FILE):
        print(f"ℹ️ Embeddings не знайдено: {EMBEDDINGS_FILE}", flush=True)
        return False
    try:
        data           = np.load(EMBEDDINGS_FILE, allow_pickle=True)
        _vectors       = data['vectors'].astype(np.float32)
        _names         = list(data['names'])
        _categories    = list(data['categories'])    if 'categories'    in data else [''] * len(_names)
        _routing_paths = list(data['routing_paths']) if 'routing_paths' in data else [''] * len(_names)
        built          = str(data['built'][0]) if 'built' in data else '?'
        _ready         = True
        print(f"✅ Voyage: {len(_names)} товарів, {_vectors.shape[1]}d, збудовано {built}", flush=True)
        return True
    except Exception as e:
        print(f"❌ Помилка завантаження embeddings: {e}", flush=True)
        return False


def is_ready() -> bool:     # чи готовий векторний пошук
    return _ready and _vectors is not None and len(_names) > 0


# ─── Векторний пошук ──────────────────────────────────────────────────────────

def _cosine_sim(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:  # cosine similarity між запитом і матрицею товарів
    norms  = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms  = np.where(norms == 0, 1e-9, norms)
    normed = matrix / norms
    q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    return normed @ q_norm


def _build_score_mask(req_cat: str | None,
                      req_path: str | None,
                      brand_tokens: list[str] | None) -> np.ndarray:   # будує float32 маску множників для кожного товару за категорією/шляхом/брендом
    """
    Повертає масив множників shape (N,):
      1.0  — товар точно у потрібному вузлі routing_path
      0.9  — товар у ширшому вузлі (батько req_path)
      0.75 — товар у суміжній категорії (SIMILAR_CATS)
      0.0  — несумісна категорія (INCOMPATIBLE_CATS)
      0.5  — інше

    Якщо req_path або req_cat не задані → всі 1.0 (пошук по всьому каталогу).
    brand_tokens → товари без бренду отримують додатковий множник 0.8.
    """
    n    = len(_names)
    mask = np.ones(n, dtype=np.float32)

    if not req_cat and not req_path:
        return mask  # немає обмежень — шукаємо по всьому

    # Предки req_path від вузького до широкого: ["cat|grp|sub", "cat|grp", "cat"]
    path_ancestors_set: list[str] = path_ancestors(req_path) if req_path else []
    # Перший предок — сама категорія (останній елемент списку)
    req_cat_eff = req_cat or (path_ancestors_set[-1] if path_ancestors_set else '')
    similar     = set(SIMILAR_CATS.get(req_cat_eff, []))

    for idx in range(n):
        ipath = _routing_paths[idx] if idx < len(_routing_paths) else ''
        icat  = _categories[idx]    if idx < len(_categories)    else ''

        # Несумісна категорія → 0.0 (жорсткий блок)
        if req_cat_eff and icat and (req_cat_eff, icat) in INCOMPATIBLE_CATS:
            mask[idx] = 0.0
            continue

        if req_path:
            if ipath == req_path:
                mult = 1.0          # точний вузол
            elif any(ipath.startswith(anc) for anc in path_ancestors_set[1:]):
                mult = 0.9          # батьківський вузол (ширше)
            elif icat == req_cat_eff:
                mult = 0.85         # та ж категорія але інша група
            elif icat in similar:
                mult = 0.75         # суміжна категорія
            else:
                mult = 0.5          # все інше
        else:
            # Тільки категорія без path
            if icat == req_cat_eff:
                mult = 1.0
            elif icat in similar:
                mult = 0.75
            else:
                mult = 0.5

        mask[idx] = mult

    # Бренд-фільтр: товари без потрібного бренду отримують множник 0.8
    if brand_tokens:
        brand_lc = [t.lower() for t in brand_tokens]
        for idx in range(n):
            if mask[idx] == 0.0:
                continue
            name_lc = _names[idx].lower() if idx < len(_names) else ''
            if not any(b in name_lc for b in brand_lc):
                mask[idx] *= 0.8

    return mask


def voyage_search(query: str,
                  top_n: int = 5,
                  category: str | None = None,
                  routing_path: str | None = None,
                  brand_tokens: list[str] | None = None,
                  catalog: list | None = None) -> list[dict]:   # головна функція: семантичний пошук з ієрархічною маршрутизацією
    """
    Параметри:
      query        — нормалізований текст запиту
      category     — category_code (напр. 'fasteners_sealants')
      routing_path — повний шлях (напр. 'fasteners_sealants|KVADO')
                     якщо None але є category — шукаємо в усій категорії
      brand_tokens — список токенів бренду (напр. ['ECO', 'eco'])
      catalog      — список товарів CATALOG (для повернення повної інфо)

    Повертає список dict:
      {'name', 'score', '_match_pct', 'category', 'routing_path', '_voyage': True, ...}
    """
    if not is_ready() or not VOYAGE_KEY:
        return []

    try:
        client = _get_client()
        result = client.embed([query], model=VOYAGE_MODEL, input_type="query")
        q_vec  = np.array(result.embeddings[0], dtype=np.float32)
    except Exception as e:
        print(f"⚠️ Voyage embed: {e}", flush=True)
        return []

    # Cosine similarity по всьому каталогу
    scores = _cosine_sim(q_vec, _vectors)   # shape (N,)

    # Застосовуємо маску маршрутизації
    mask   = _build_score_mask(category, routing_path, brand_tokens)
    scores = scores * mask                  # елементне множення

    # Топ-N
    top_n_eff = min(top_n, len(scores))
    top_idx   = np.argpartition(scores, -top_n_eff)[-top_n_eff:]
    top_idx   = top_idx[np.argsort(scores[top_idx])[::-1]]

    results = []
    for idx in top_idx:
        score = float(scores[idx])
        if score < THRESHOLD_MIN:
            continue
        name  = _names[idx] if idx < len(_names) else ''
        item  = catalog[idx] if catalog and idx < len(catalog) else {}
        results.append({
            'name':         name,
            'name_full':    item.get('name_full', name),
            'artikul':      item.get('artikul', ''),
            'category':     item.get('category', _categories[idx] if idx < len(_categories) else ''),
            'price':        item.get('price', 0),
            'score':        round(score, 4),
            '_match_pct':   int(score * 100),
            '_voyage':      True,
            '_rpath':       _routing_paths[idx] if idx < len(_routing_paths) else '',
        })

    return results


def voyage_find_one(query: str,
                    category: str | None = None,
                    routing_path: str | None = None,
                    brand_tokens: list[str] | None = None,
                    catalog: list | None = None) -> dict | None:    # повертає один товар якщо score >= THRESHOLD_AUTO, інакше None
    results = voyage_search(query, top_n=3, category=category,
                            routing_path=routing_path,
                            brand_tokens=brand_tokens, catalog=catalog)
    if not results:
        return None
    best = results[0]
    return best if best['score'] >= THRESHOLD_AUTO else None


# ─── Утиліти ──────────────────────────────────────────────────────────────────

def rebuild_if_needed(catalog: list[dict]) -> None:     # перевіряє актуальність embeddings і перебудовує у фоні якщо треба
    if not VOYAGE_KEY:
        print("ℹ️ VOYAGE_API_KEY не заданий — Voyage вимкнено", flush=True)
        return

    if not os.path.exists(EMBEDDINGS_FILE):
        print(f"⚡ Voyage: будую embeddings у фоні для {len(catalog)} товарів...", flush=True)
        import threading
        def _build():
            try:
                build_embeddings(catalog)
                print("✅ Voyage embeddings збудовано!", flush=True)
            except Exception as e:
                print(f"❌ Voyage build: {e}", flush=True)
        threading.Thread(target=_build, daemon=True).start()
        return

    try:
        data        = np.load(EMBEDDINGS_FILE, allow_pickle=True)
        saved_count = len(data['names'])
        diff        = abs(len(catalog) - saved_count)
        if diff > 500:
            print(f"⚡ Voyage: каталог змінився ({saved_count}→{len(catalog)}), перебудовую...", flush=True)
            import threading
            threading.Thread(
                target=lambda: build_embeddings(catalog, force=True),
                daemon=True
            ).start()
            return
    except Exception:
        pass

    load_embeddings()
