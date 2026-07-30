"""
build_embeddings.py — Запускати ЛОКАЛЬНО (не на сервері!)

Будує embeddings для каталогу і зберігає catalog_embeddings.npz
Потім завантажуєш цей файл на Render через Disk або GitHub.

Запуск:
  pip install voyageai numpy pandas openpyxl
  VOYAGE_API_KEY=your_key python build_embeddings.py

Або з .env:
  export VOYAGE_API_KEY=va-...
  python build_embeddings.py
"""

import os, sys, time, json
import numpy as np

VOYAGE_KEY  = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL = "voyage-3"
BATCH_SIZE   = 128
OUTPUT_FILE  = "catalog_embeddings.npz"

if not VOYAGE_KEY:
    print("❌ Встанови VOYAGE_API_KEY перед запуском")
    print("   export VOYAGE_API_KEY=va-...")
    sys.exit(1)

# ─── Читаємо каталог з xlsx файлів ──────────────────────────────────────────
print("📦 Читаю прайси...")

import pandas as pd
import re

# Шукаємо прайси — або в папці prices/ або прямо в корені репо
PRICES_DIR = "prices" if os.path.isdir("prices") else "."

# Кожен елемент: (ім'я файлу без .xlsx, category_code)
# Підтримує і англійські і кириличні імена файлів
CATALOG_FILES = [
    ("adapters_reducers",                    "adapters_reducers"),
    ("ПЕРЕХОДНИКИ",                          "adapters_reducers"),
    ("ПЕРЕХОДНИКИ_1",                        "adapters_reducers"),
    ("automation",                           "automation"),
    ("automation_1",                         "automation"),
    ("АВТОМАТИКА_ДЛЯ_ОПАЛЕННЯ_ВОДОПОСТАЧАННЯ", "automation"),
    ("boilers",                              "boilers"),
    ("КОТЛИ",                                "boilers"),
    ("fasteners_sealants",                   "fasteners_sealants"),
    ("КРІПЛЕННЯ_УЩІЛЬНЮВАЧІ_РОЗХІДНИКИ",    "fasteners_sealants"),
    ("filtration",                           "filtration"),
    ("ОЧИСТКА",                              "filtration"),
    ("heating",                              "heating"),
    ("ОПАЛЕННЯ",                             "heating"),
    ("hoses",                                "hoses"),
    ("ШЛАНГИ",                               "hoses"),
    ("insulation",                           "insulation"),
    ("УТЕПЛЮВАЧ_ТА_ПОЛОТНО",                "insulation"),
    ("metal_plastic",                        "metal_plastic"),
    ("МЕТАЛОПЛАСТИК_ВСЕ",                   "metal_plastic"),
    ("mixers_faucets",                       "mixers_faucets"),
    ("ЗМІШУВАЧІ_І_КОМПЛЕКТУЮЧІ",            "mixers_faucets"),
    ("plastic_ppr",                          "plastic_ppr"),
    ("ПЛАСТИК_ППР_ПНД",                     "plastic_ppr"),
    ("pumps",                                "pumps"),
    ("НАСОСНА_ТЕХНІКА",                     "pumps"),
    ("push_systems",                         "push_systems"),
    ("СИСТЕМИ_PUSHОПАЛЕННЯ_ВОДОПОСТАЧАННЯ_І_ТД", "push_systems"),
    ("radiators_radiatorsvalve",             "radiators_radiatorsvalve"),
    ("РАДІАТОРИ_І_КОМПЛЕКТУЮЧІ_ДО_НИХ",    "radiators_radiatorsvalve"),
    ("safety_valves",                        "safety_valves"),
    ("АРМАТУРА_БЕЗПЕКИ",                    "safety_valves"),
    ("sanitary_ware",                        "sanitary_ware"),
    ("САНФАЯНСЧИСТОВА_САНТЕХНІКА_ТА_ІНСТАЛЯЦІЇ", "sanitary_ware"),
    ("sewage",                               "sewage"),
    ("КАНАЛІЗАЦІЯ",                          "sewage"),
    ("shutoff_valves",                       "shutoff_valves"),
    ("ЗАПІРНА_АРМАТУРА",                    "shutoff_valves"),
    ("siphons_fittings",                     "siphons_fittings"),
    ("СИФОНИ_АРМАТУРИ_УНІТАЗІВ_ТРАПИ_І_ТД", "siphons_fittings"),
    ("towel_warmers",                        "towel_warmers"),
    ("РУШНИКОСУШКИ_І_КОМПЛЕКТУЮЧІ_ДО_НИХ", "towel_warmers"),
    ("underfloor_heating",                   "underfloor_heating"),
    ("СИСТЕМИ_ТЕПЛИХ_ПІДЛОГ",               "underfloor_heating"),
    ("water_heaters",                        "water_heaters"),
    ("ВОДОНАГРІВАЧІ",                       "water_heaters"),
    ("water_meters",                         "water_meters"),
    ("ВОДОМІРИ",                            "water_meters"),
]

catalog = []
for key, category in CATALOG_FILES:
    path = os.path.join(PRICES_DIR, f"{key}.xlsx")
    if not os.path.exists(path):
        path = os.path.join(PRICES_DIR, f"{key}.xls")   # пробуємо старий формат
    if not os.path.exists(path):
        continue   # файл не знайдено — пропускаємо мовчки
    try:
        engine = 'xlrd' if path.endswith('.xls') else 'openpyxl'
        df = pd.read_excel(path, header=0, engine=engine)
        cols = list(df.columns)
        rename = {}
        if len(cols) >= 1: rename[cols[0]] = 'name'
        if len(cols) >= 2: rename[cols[1]] = 'artikul'
        if len(cols) >= 3: rename[cols[2]] = 'price'
        df = df.rename(columns=rename)
        count = 0
        for _, row in df.iterrows():
            name = str(row.get('name', '')).strip()
            if not name or name == 'nan': continue
            try:
                p = float(row.get('price', 0))
            except: p = 0.0
            name_clean = re.sub(r'\s*\{[^}]+\}', '', name).strip()
            catalog.append({'name': name_clean, 'category': category, 'price': p})
            count += 1
        print(f"  ✅ {key}: {count} товарів")
    except Exception as e:
        print(f"  ❌ {key}: {e}")

# Видаляємо дублікати по назві (деякі файли перекриваються)
seen = set()
unique = []
for c in catalog:
    if c['name'] not in seen:
        seen.add(c['name'])
        unique.append(c)
catalog = unique

# Видаляємо виведені з асортименту
before = len(catalog)
catalog = [c for c in catalog if 'виведено з асортименту' not in c['name'].lower()]
print(f"\n📊 Каталог: {len(catalog)} товарів (видалено {before-len(catalog)} виведених)") 

# ─── Будуємо embeddings ──────────────────────────────────────────────────────
print(f"\n🔨 Будую embeddings через Voyage AI ({VOYAGE_MODEL})...")
print(f"   Батчів: {(len(catalog)+BATCH_SIZE-1)//BATCH_SIZE}")
print(f"   Приблизна вартість: ${len(catalog)*15/1_000_000*0.06:.4f}")
print()

import voyageai
client = voyageai.Client(api_key=VOYAGE_KEY)

texts  = [f"{c['category']}: {c['name']}" for c in catalog]
names  = [c['name'] for c in catalog]

all_vectors = []
t0 = time.time()
total = (len(texts) + BATCH_SIZE - 1) // BATCH_SIZE

for i in range(0, len(texts), BATCH_SIZE):
    batch     = texts[i:i+BATCH_SIZE]
    batch_num = i // BATCH_SIZE + 1
    try:
        result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
        all_vectors.extend(result.embeddings)
    except Exception as e:
        print(f"  ❌ Батч {batch_num}: {e} — повторюю...")
        time.sleep(3)
        result = client.embed(batch, model=VOYAGE_MODEL, input_type="document")
        all_vectors.extend(result.embeddings)

    if batch_num % 20 == 0 or batch_num == total:
        elapsed = time.time() - t0
        pct = batch_num / total * 100
        eta = elapsed / batch_num * (total - batch_num)
        print(f"  ⚡ {pct:.0f}% ({batch_num}/{total}), {elapsed:.0f}с, ETA ~{eta:.0f}с")

# ─── Зберігаємо ─────────────────────────────────────────────────────────────
vectors_arr = np.array(all_vectors, dtype=np.float32)
np.savez_compressed(
    OUTPUT_FILE,
    vectors=vectors_arr,
    names=np.array(names, dtype=object),
    built=np.array([time.strftime("%Y-%m-%d %H:%M")]),
)

elapsed = time.time() - t0
size_mb = os.path.getsize(OUTPUT_FILE) / 1024 / 1024
print(f"\n✅ Збережено: {OUTPUT_FILE}")
print(f"   Товарів: {len(names)}")
print(f"   Розмір вектора: {vectors_arr.shape[1]}d")
print(f"   Розмір файлу: {size_mb:.1f} MB")
print(f"   Час: {elapsed:.0f}с")
print()
print("📤 Тепер завантаж catalog_embeddings.npz на Render:")
print("   Render Dashboard → hotpointbot → Disk → Upload file")
print("   Або через GitHub якщо файл невеликий (<100MB)")
