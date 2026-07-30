"""
build_embeddings.py — Запускати ЛОКАЛЬНО (не на сервері!)

Будує embeddings для каталогу і зберігає catalog_embeddings.npz з полями:
  vectors, names, categories, routing_paths, built

Запуск (Git Bash / Windows):
  export VOYAGE_API_KEY=va-...
  cd /c/Users/Admin/hotpointassitanttelegrambot
  python build_embeddings.py
"""

import os, sys, time, re
import numpy as np
import pandas as pd

VOYAGE_KEY   = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL = "voyage-3"
BATCH_SIZE   = 128
OUTPUT_FILE  = "catalog_embeddings.npz"

if not VOYAGE_KEY:
    print("❌ Встанови VOYAGE_API_KEY перед запуском")
    sys.exit(1)

PRICES_DIR = "prices" if os.path.isdir("prices") else "."

CATALOG_FILES = [
    ("adapters_reducers",                        "adapters_reducers"),
    ("ПЕРЕХОДНИКИ",                              "adapters_reducers"),
    ("automation",                               "automation"),
    ("АВТОМАТИКА_ДЛЯ_ОПАЛЕННЯ_ВОДОПОСТАЧАННЯ",  "automation"),
    ("boilers",                                  "boilers"),
    ("КОТЛИ",                                    "boilers"),
    ("fasteners_sealants",                       "fasteners_sealants"),
    ("КРІПЛЕННЯ_УЩІЛЬНЮВАЧІ_РОЗХІДНИКИ",        "fasteners_sealants"),
    ("filtration",                               "filtration"),
    ("ОЧИСТКА",                                  "filtration"),
    ("heating",                                  "heating"),
    ("ОПАЛЕННЯ",                                 "heating"),
    ("hoses",                                    "hoses"),
    ("ШЛАНГИ",                                   "hoses"),
    ("insulation",                               "insulation"),
    ("УТЕПЛЮВАЧ_ТА_ПОЛОТНО",                    "insulation"),
    ("metal_plastic",                            "metal_plastic"),
    ("МЕТАЛОПЛАСТИК_ВСЕ",                       "metal_plastic"),
    ("mixers_faucets",                           "mixers_faucets"),
    ("ЗМІШУВАЧІ_І_КОМПЛЕКТУЮЧІ",                "mixers_faucets"),
    ("plastic_ppr",                              "plastic_ppr"),
    ("ПЛАСТИК_ППР_ПНД",                         "plastic_ppr"),
    ("pumps",                                    "pumps"),
    ("НАСОСНА_ТЕХНІКА",                         "pumps"),
    ("push_systems",                             "push_systems"),
    ("СИСТЕМИ_PUSHОПАЛЕННЯ_ВОДОПОСТАЧАННЯ_І_ТД","push_systems"),
    ("radiators_radiatorsvalve",                 "radiators_radiatorsvalve"),
    ("РАДІАТОРИ_І_КОМПЛЕКТУЮЧІ_ДО_НИХ",        "radiators_radiatorsvalve"),
    ("safety_valves",                            "safety_valves"),
    ("АРМАТУРА_БЕЗПЕКИ",                        "safety_valves"),
    ("sanitary_ware",                            "sanitary_ware"),
    ("САНФАЯНСЧИСТОВА_САНТЕХНІКА_ТА_ІНСТАЛЯЦІЇ","sanitary_ware"),
    ("sewage",                                   "sewage"),
    ("КАНАЛІЗАЦІЯ",                              "sewage"),
    ("shutoff_valves",                           "shutoff_valves"),
    ("ЗАПІРНА_АРМАТУРА",                        "shutoff_valves"),
    ("siphons_fittings",                         "siphons_fittings"),
    ("СИФОНИ_АРМАТУРИ_УНІТАЗІВ_ТРАПИ_І_ТД",    "siphons_fittings"),
    ("towel_warmers",                            "towel_warmers"),
    ("РУШНИКОСУШКИ_І_КОМПЛЕКТУЮЧІ_ДО_НИХ",     "towel_warmers"),
    ("underfloor_heating",                       "underfloor_heating"),
    ("СИСТЕМИ_ТЕПЛИХ_ПІДЛОГ",                   "underfloor_heating"),
    ("water_heaters",                            "water_heaters"),
    ("ВОДОНАГРІВАЧІ",                           "water_heaters"),
    ("water_meters",                             "water_meters"),
    ("ВОДОМІРИ",                                "water_meters"),
]


# ─── Читання xlsx з group/subgroup ───────────────────────────────────────────

def _is_header(name, artikul, price) -> bool:   # рядок без ціни і артикулу = заголовок групи
    name = str(name).strip()
    if not name or name == 'nan':
        return True
    art = str(artikul).strip()
    if art and art not in ('nan', '0', ''):
        return False
    try:
        if float(price) > 0:
            return False
    except Exception:
        pass
    return True


def make_routing_path(category: str, group: str, subgroup: str) -> str:    # будує ієрархічний шлях cat|group|subgroup
    parts = [category]
    if group and group.strip():
        parts.append(group.strip())
    if subgroup and subgroup.strip():
        parts.append(subgroup.strip())
    return '|'.join(parts)


def read_xlsx(path: str, category: str) -> list[dict]:  # читає xlsx і повертає товари з group/subgroup/routing_path
    engine = 'xlrd' if path.endswith('.xls') else 'openpyxl'
    df     = pd.read_excel(path, header=0, engine=engine)
    cols   = list(df.columns)
    rename = {}
    if len(cols) >= 1: rename[cols[0]] = 'name'
    if len(cols) >= 2: rename[cols[1]] = 'artikul'
    if len(cols) >= 3: rename[cols[2]] = 'price'
    df = df.rename(columns=rename)

    items        = []
    current_grp  = ''
    current_sub  = ''
    prev_header  = False

    for _, row in df.iterrows():
        name    = str(row.get('name', '')).strip()
        artikul = row.get('artikul', '')
        price   = row.get('price', 0)

        if _is_header(name, artikul, price):
            if name and name != 'nan':
                if prev_header:
                    current_sub = name   # другий рівень підряд → підгрупа
                else:
                    current_grp = name   # перший рівень → група
                    current_sub = ''
            prev_header = True
            continue

        prev_header = False
        try:    p = float(price)
        except: p = 0.0

        name_clean = re.sub(r'\s*\{[^}]+\}', '', name).strip()
        if not name_clean or 'виведено з асортименту' in name_clean.lower():
            continue

        rpath = make_routing_path(category, current_grp, current_sub)
        items.append({
            'name':         name_clean,
            'category':     category,
            'price':        p,
            'group':        current_grp,
            'subgroup':     current_sub,
            'routing_path': rpath,
        })
    return items


# ─── Збираємо каталог ────────────────────────────────────────────────────────
print("📦 Читаю прайси...")
catalog = []
seen    = set()

for key, category in CATALOG_FILES:
    path = os.path.join(PRICES_DIR, f"{key}.xlsx")
    if not os.path.exists(path):
        path = os.path.join(PRICES_DIR, f"{key}.xls")
    if not os.path.exists(path):
        continue
    try:
        items = read_xlsx(path, category)
        added = 0
        for item in items:
            if item['name'] not in seen:
                seen.add(item['name'])
                catalog.append(item)
                added += 1
        print(f"  ✅ {key}: {added} товарів")
    except Exception as e:
        print(f"  ❌ {key}: {e}")

print(f"\n📊 Каталог: {len(catalog)} унікальних товарів")

# Статистика по routing_paths
paths_sample = list({c['routing_path'] for c in catalog})[:10]
print(f"   Прикладів routing_path:")
for p in paths_sample:
    print(f"     {p}")

# ─── Будуємо embeddings ──────────────────────────────────────────────────────
print(f"\n🔨 Будую embeddings ({VOYAGE_MODEL})...")
print(f"   Батчів: {(len(catalog)+BATCH_SIZE-1)//BATCH_SIZE}")
print(f"   Приблизна вартість: ${len(catalog)*15/1_000_000*0.06:.4f}\n")

import voyageai
client = voyageai.Client(api_key=VOYAGE_KEY)

# Текст для embedding: routing_path + назва — модель бачить повний контекст
# Приклад: "fasteners_sealants|KVADO: Хомут DN15 сталевий KVADO"
texts         = [f"{c['routing_path']}: {c['name']}" for c in catalog]
names         = [c['name']         for c in catalog]
categories    = [c['category']     for c in catalog]
routing_paths = [c['routing_path'] for c in catalog]

all_vectors = []
t0    = time.time()
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
        eta     = elapsed / batch_num * (total - batch_num)
        print(f"  ⚡ {batch_num/total*100:.0f}% ({batch_num}/{total}), {elapsed:.0f}с, ETA ~{eta:.0f}с")

# ─── Зберігаємо з новими полями ─────────────────────────────────────────────
vectors_arr = np.array(all_vectors, dtype=np.float32)
np.savez_compressed(
    OUTPUT_FILE,
    vectors       = vectors_arr,
    names         = np.array(names,         dtype=object),
    categories    = np.array(categories,    dtype=object),
    routing_paths = np.array(routing_paths, dtype=object),
    built         = np.array([time.strftime("%Y-%m-%d %H:%M")]),
)

elapsed = time.time() - t0
size_mb = os.path.getsize(OUTPUT_FILE) / 1024 / 1024
print(f"\n✅ Збережено: {OUTPUT_FILE}")
print(f"   Товарів:       {len(names)}")
print(f"   Розмір вектора: {vectors_arr.shape[1]}d")
print(f"   Розмір файлу:  {size_mb:.1f} MB")
print(f"   Час:           {elapsed:.0f}с")
print()
print("📤 Тепер завантаж catalog_embeddings.npz на Render:")
print("   Render Dashboard → hotpointbot → Disk → Upload file")
