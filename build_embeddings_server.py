"""
build_embeddings_server.py — для запуску в Render Shell.
Будує embeddings батчами, економить RAM.
Запуск: python build_embeddings_server.py
"""
import os, sys, time, gc
import numpy as np

VOYAGE_KEY   = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL = "voyage-3"
BATCH_SIZE   = 64          # менший батч = менше RAM
OUTPUT_FILE  = "/var/data/catalog_embeddings.npz"
DATA_DIR     = "/var/data"
CATALOG_FILE = os.path.join(DATA_DIR, "catalog.json")

if not VOYAGE_KEY:
    print("❌ VOYAGE_API_KEY не знайдено")
    sys.exit(1)

# Читаємо каталог
import json
print("📦 Читаю каталог...")
with open(CATALOG_FILE, encoding='utf-8') as f:
    catalog = json.load(f)

# Фільтруємо виведені
catalog = [c for c in catalog if 'виведено з асортименту' not in c.get('name','').lower()]

# Дедуплікація
seen = set()
unique = []
for c in catalog:
    if c['name'] not in seen:
        seen.add(c['name'])
        unique.append(c)
catalog = unique
print(f"✅ Товарів: {len(catalog)}")

# Готуємо тексти
texts = [f"{c.get('category','')}: {c['name']}" for c in catalog]
names = [c['name'] for c in catalog]

# Будуємо порціями і одразу зберігаємо на диск
import voyageai
client = voyageai.Client(api_key=VOYAGE_KEY)

total   = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE
t0      = time.time()
vectors = []   # тримаємо в RAM тільки поточний батч

print(f"🔨 Будую embeddings ({total} батчів)...")

for i in range(0, len(texts), BATCH_SIZE):
    batch     = texts[i:i+BATCH_SIZE]
    batch_num = i // BATCH_SIZE + 1
    try:
        result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
        vectors.extend(result.embeddings)
    except Exception as e:
        print(f"  ❌ Батч {batch_num}: {e}")
        time.sleep(3)
        result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
        vectors.extend(result.embeddings)

    if batch_num % 20 == 0 or batch_num == total:
        pct = batch_num / total * 100
        elapsed = time.time() - t0
        print(f"  ⚡ {pct:.0f}% ({batch_num}/{total}), {elapsed:.0f}с")

    time.sleep(0.03)

# Зберігаємо
print("💾 Зберігаю...")
arr = np.array(vectors, dtype=np.float32)
np.savez_compressed(OUTPUT_FILE,
    vectors=arr,
    names=np.array(names, dtype=object),
    built=np.array([time.strftime("%Y-%m-%d %H:%M")]))

size_mb = os.path.getsize(OUTPUT_FILE) / 1024 / 1024
print(f"✅ Готово! {len(names)} товарів, {size_mb:.1f}MB, {time.time()-t0:.0f}с")
