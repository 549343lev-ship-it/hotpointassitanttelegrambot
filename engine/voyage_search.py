"""
engine/voyage_search.py — Векторний пошук через Voyage AI.

АРХІТЕКТУРА:
  1. Один раз при старті: завантажуємо embeddings каталогу з файлу (або будуємо)
  2. При кожному запиті:
     а) Voyage конвертує normalized текст → вектор (embedding)
     б) Cosine similarity з усіма товарами → топ-N
     в) Якщо score >= THRESHOLD (0.82) → повертаємо одразу без Claude
     г) Якщо нижче → fallback на Claude pick

МОДЕЛЬ: voyage-3 (баланс ціна/якість для сантехнічної термінології)
EMBEDDINGS FILE: DATA_DIR/catalog_embeddings.npz
  - vectors: float32 array shape (N, 1024)
  - names:   list of catalog item names
  - built:   timestamp

ВАРТІСТЬ:
  - Побудова: ~$0.05 (один раз)
  - Пошук: $0.06 / 1M токенів (~$0.000001 за запит)
"""

import os
import json
import time
import numpy as np

DATA_DIR        = os.environ.get("DATA_DIR") or ("/var/data" if os.path.isdir("/var/data") else ".")
EMBEDDINGS_FILE = os.path.join(DATA_DIR, "catalog_embeddings.npz")
VOYAGE_KEY      = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL    = "voyage-3"        # voyage-3-large для кращої точності якщо треба
BATCH_SIZE      = 128               # Voyage API ліміт за раз
THRESHOLD_AUTO  = 0.82              # вище → повертаємо без Claude
THRESHOLD_MIN   = 0.60              # нижче → взагалі не знайдено

# ─── Глобальний стан ─────────────────────────────────────────────────────────

_vectors: np.ndarray | None = None  # shape (N, dim) — всі вектори каталогу
_names:   list[str]          = []   # назви товарів (індекс → назва)
_ready    = False                   # чи завантажено

def _get_client():      # повертає voyage client (lazy, бо ключ може з'явитися пізніше)
    import voyageai
    return voyageai.Client(api_key=VOYAGE_KEY)


# ─── Побудова / завантаження embeddings ──────────────────────────────────────

def build_embeddings(catalog: list[dict], force: bool = False) -> bool:     # будує embeddings для всього каталогу і зберігає в npz файл; повертає True якщо успішно
    """Будує embeddings для всього каталогу. Запускати вручну або при старті якщо файлу немає."""
    global _vectors, _names, _ready

    if not VOYAGE_KEY:
        print("⚠️ VOYAGE_API_KEY не заданий — векторний пошук вимкнено", flush=True)
        return False

    if os.path.exists(EMBEDDINGS_FILE) and not force:
        print(f"💾 Embeddings вже є: {EMBEDDINGS_FILE}", flush=True)
        return load_embeddings()

    if not catalog:
        print("⚠️ Каталог порожній — нема що індексувати", flush=True)
        return False

    print(f"🔨 Будую embeddings для {len(catalog)} товарів...", flush=True)
    client = _get_client()

    # Готуємо тексти для embedding
    # Формат: "категорія: назва товару" — дає контекст моделі
    texts = []
    names = []
    for item in catalog:
        name = item.get('name', '')
        cat  = item.get('category', '')
        texts.append(f"{cat}: {name}" if cat else name)
        names.append(name)

    # Батчами по BATCH_SIZE
    all_vectors = []
    total_batches = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
    t0 = time.time()

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1

        try:
            result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
            all_vectors.extend(result.embeddings)
        except Exception as e:
            print(f"  ❌ Батч {batch_num}/{total_batches}: {e}", flush=True)
            time.sleep(2)
            try:
                result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
                all_vectors.extend(result.embeddings)
            except Exception as e2:
                print(f"  ❌ Повторна спроба провалилась: {e2}", flush=True)
                return False

        if batch_num % 10 == 0 or batch_num == total_batches:
            elapsed = time.time() - t0
            pct = batch_num / total_batches * 100
            print(f"  ⚡ {pct:.0f}% ({batch_num}/{total_batches} батчів, {elapsed:.0f}с)", flush=True)

        time.sleep(0.05)    # невелика пауза щоб не спамити API

    # Зберігаємо
    vectors_arr = np.array(all_vectors, dtype=np.float32)
    np.savez_compressed(
        EMBEDDINGS_FILE,
        vectors=vectors_arr,
        names=np.array(names, dtype=object),
        built=np.array([time.strftime("%Y-%m-%d %H:%M")]),
    )

    _vectors = vectors_arr
    _names   = names
    _ready   = True

    elapsed = time.time() - t0
    print(f"✅ Embeddings збудовано: {len(names)} товарів, {vectors_arr.shape[1]}d, {elapsed:.0f}с", flush=True)
    return True


def load_embeddings() -> bool:      # завантажує embeddings з файлу в пам'ять; повертає True якщо успішно
    """Завантажує embeddings з файлу. Викликати при старті після load_catalog()."""
    global _vectors, _names, _ready

    if not os.path.exists(EMBEDDINGS_FILE):
        print(f"ℹ️ Embeddings файл не знайдено: {EMBEDDINGS_FILE}", flush=True)
        return False

    try:
        data     = np.load(EMBEDDINGS_FILE, allow_pickle=True)
        _vectors = data['vectors'].astype(np.float32)
        _names   = list(data['names'])
        built    = str(data['built'][0]) if 'built' in data else 'невідомо'
        _ready   = True
        print(f"✅ Voyage embeddings: {len(_names)} товарів, {_vectors.shape[1]}d, збудовано {built}", flush=True)
        return True
    except Exception as e:
        print(f"❌ Помилка завантаження embeddings: {e}", flush=True)
        return False


def is_ready() -> bool:     # чи готовий векторний пошук (embeddings завантажені)
    return _ready and _vectors is not None and len(_names) > 0


# ─── Векторний пошук ──────────────────────────────────────────────────────────

def _cosine_sim(query_vec: np.ndarray, matrix: np.ndarray) -> np.ndarray:  # cosine similarity між вектором запиту і матрицею всіх товарів
    """Vectorized cosine similarity. query_vec shape (dim,), matrix shape (N, dim)."""
    # Нормалізуємо рядки матриці (якщо ще не нормалізовані)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1e-9, norms)
    normed = matrix / norms
    q_norm = query_vec / (np.linalg.norm(query_vec) + 1e-9)
    return normed @ q_norm  # shape (N,)


def voyage_search(query: str, top_n: int = 5,
                  category: str = None, catalog: list = None) -> list[dict]:    # векторний пошук: query → embedding → топ-N схожих товарів з каталогу
    """
    Основна функція векторного пошуку.
    
    Повертає список dict:
      {'name': str, 'score': float, '_match_pct': int, 'category': str, ...}
    
    score >= THRESHOLD_AUTO → можна брати без Claude
    score < THRESHOLD_MIN   → нічого не знайдено
    """
    if not is_ready():
        return []
    if not VOYAGE_KEY:
        return []

    try:
        client = _get_client()
        result = client.embed([query], model=VOYAGE_MODEL, input_type="query")
        q_vec  = np.array(result.embeddings[0], dtype=np.float32)
    except Exception as e:
        print(f"⚠️ Voyage embed query: {e}", flush=True)
        return []

    # Cosine similarity з усім каталогом
    scores = _cosine_sim(q_vec, _vectors)   # shape (N,)

    # Фільтруємо за категорією якщо треба
    if category and catalog:
        # Будуємо маску: True якщо товар у потрібній категорії
        cat_mask = np.array([
            catalog[i].get('category') == category if i < len(catalog) else False
            for i in range(len(_names))
        ], dtype=bool)
        # Для товарів не з тієї категорії — знижуємо score (не відкидаємо повністю)
        scores = np.where(cat_mask, scores, scores * 0.7)

    # Топ-N за score
    top_idx = np.argpartition(scores, -min(top_n, len(scores)))[-top_n:]
    top_idx = top_idx[np.argsort(scores[top_idx])[::-1]]   # сортуємо спадно

    results = []
    for idx in top_idx:
        score = float(scores[idx])
        if score < THRESHOLD_MIN:
            continue
        name = _names[idx]
        pct  = int(score * 100)

        # Знаходимо повну інформацію про товар з каталогу
        item = {}
        if catalog and idx < len(catalog):
            item = catalog[idx]

        results.append({
            'name':       name,
            'name_full':  item.get('name_full', name),
            'artikul':    item.get('artikul', ''),
            'category':   item.get('category', ''),
            'price':      item.get('price', 0),
            'score':      round(score, 4),
            '_match_pct': pct,
            '_voyage':    True,         # маркер що знайдено через Voyage
        })

    return results


def voyage_find_one(query: str, category: str = None, catalog: list = None) -> dict | None:    # знаходить один найкращий товар якщо score >= THRESHOLD_AUTO, інакше None
    """
    Знаходить ОДИН товар якщо впевненість висока (>= THRESHOLD_AUTO).
    Повертає товар або None якщо треба Claude.
    """
    results = voyage_search(query, top_n=3, category=category, catalog=catalog)
    if not results:
        return None

    best = results[0]
    if best['score'] >= THRESHOLD_AUTO:
        return best     # впевнені → одразу без Claude

    return None         # невпевнені → нехай Claude вирішує


# ─── Утиліти ──────────────────────────────────────────────────────────────────

def rebuild_if_needed(catalog: list[dict]) -> None:     # перебудовує embeddings якщо файлу немає або каталог сильно змінився
    """Викликати при старті після load_catalog()."""
    if not VOYAGE_KEY:
        return

    if not os.path.exists(EMBEDDINGS_FILE):
        print("⚡ Voyage: embeddings не знайдено, будую...", flush=True)
        build_embeddings(catalog)
        return

    # Перевіряємо чи кількість товарів не змінилась суттєво
    try:
        data = np.load(EMBEDDINGS_FILE, allow_pickle=True)
        saved_count = len(data['names'])
        current_count = len(catalog)
        diff = abs(current_count - saved_count)
        if diff > 500:  # більше 500 нових товарів → перебудовуємо
            print(f"⚡ Voyage: каталог змінився ({saved_count}→{current_count}), перебудовую...", flush=True)
            build_embeddings(catalog, force=True)
            return
    except Exception:
        pass

    load_embeddings()
