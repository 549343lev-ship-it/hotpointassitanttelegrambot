import telebot
import anthropic
from google import genai as genai_new
import os
import pandas as pd
import base64
import json
import threading
from io import BytesIO

# Отримання токенів з системних змінних
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")
GEMINI_KEY = os.environ.get("GEMINI_KEY")

# Ініціалізація клієнтів
bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
gemini_client = genai_new.Client(api_key=GEMINI_KEY)

# ─── КАТАЛОГИ ПО ФАЙЛАХ ──────────────────────────────────────────────────────
CATALOG_FILES = [
    ("adapters_reducers.xlsx",   "Перехідники, редуктори, подовжувачі різьб"),
    ("automation.xlsx",          "Автоматика для опалення та водопостачання"),
    ("boilers.xlsx",             "Котли"),
    ("fasteners_sealants.xlsx",  "Кріплення, ущільнювачі, розхідники"),
    ("filtration.xlsx",          "Фільтри, колби, системи фільтрації, очистка води"),
    ("heating.xlsx",             "Опалення загальне"),
    ("hoses.xlsx",               "Шланги"),
    ("insulation.xlsx",          "Утеплювач"),
    ("metal_plastic.xlsx",       "Металопластикова система, труби і фітинги М/П"),
    ("mixers_faucets.xlsx",      "Змішувачі, крани"),
    ("plastic_ppr.xlsx",         "Пластик ППР, система пайки, труби і фітинги ППР"),
    ("pumps.xlsx",               "Насосна техніка, насоси"),
    ("push_systems.xlsx",        "Системи PUSH, прес-з'єднання"),
    ("radiators_radiatorsvalve.xlsx", "Радіатори та арматура для радіаторів"),
    ("safety_valves.xlsx",       "Арматура безпеки, клапани"),
    ("sanitary_ware.xlsx",       "Санфаянс, унітази, інсталяції, умивальники"),
    ("sewage.xlsx",              "Каналізація, труби та фітинги каналізаційні"),
    ("shutoff_valves.xlsx",      "Запірна арматура, крани, вентилі"),
    ("siphons_fittings.xlsx",    "Сифони та арматура"),
    ("towel_warmers.xlsx",       "Полотенцесушителі"),
    ("underfloor_heating.xlsx",  "Системи теплої підлоги"),
    ("water_heaters.xlsx",       "Водонагрівачі"),
    ("water_meters.xlsx",        "Водолічильники"),
]

catalogs = {}

def load_catalog(filename, label):
    if not os.path.exists(filename):
        print(f"⚠️  Файл не знайдено: {filename}")
        return None
    try:
        df = pd.read_excel(filename, header=0)
        cols = list(df.columns)
        rename = {}
        if len(cols) >= 1: rename[cols[0]] = 'Наименование'
        if len(cols) >= 2: rename[cols[1]] = 'Артикул WMS'
        if len(cols) >= 3: rename[cols[2]] = 'ОР'
        if len(cols) >= 4: rename[cols[3]] = 'Код'
        df = df.rename(columns=rename)

        df = df[['Наименование', 'Артикул WMS', 'Код']].copy()
        df = df.dropna(subset=['Наименование'])
        df = df[df['Наименование'].astype(str).str.strip() != '']
        df = df.reset_index(drop=True)
        return df
    except Exception as e:
        print(f"❌ Помилка завантаження {filename}: {e}")
        return None

print("📦 Завантажую каталоги...")
for filename, label in CATALOG_FILES:
    key = filename.replace('.xlsx', '')
    df = load_catalog(filename, label)
    if df is not None:
        catalogs[key] = {"label": label, "df": df}
        print(f"  ✅ {filename}: {len(df)} позицій")

CATALOG_DESCRIPTIONS = "\n".join(
    f"- {key}: {info['label']}"
    for key, info in catalogs.items()
)

# ─── ПРАВИЛА ТА БАТЧІ ────────────────────────────────────────────────────────
user_batches = {}
stop_flags = {}
pending_hints = {}
RULES_FILE = "rules.txt"

def get_rules():
    if not os.path.exists(RULES_FILE):
        return ""
    with open(RULES_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()

def add_rule(new_rule):
    with open(RULES_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {new_rule}\n")

# ─── КРОК 1: OCR + НОРМАЛІЗАЦІЯ ──────────────────────────────────────────────
def нормалізувати_фото(image_b64, caption=""):
    rules = get_rules()
    rules_block = f"\nДодаткові правила від користувача:\n{rules}" if rules else ""

    prompt = f"""Ти — експерт із сантехніки України. На фото рукописний список замовлення.
ПІДКАЗКА ВІД МЕНЕДЖЕРА: {caption}
{rules_block}

ЗАВДАННЯ:
1. Прочитай кожен рядок (тільки сантехніка)
2. Нормалізуй до короткої торгової назви
3. Витягни кількість та категорію

ДОСТУПНІ КАТЕГОРІЇ:
{CATALOG_DESCRIPTIONS}

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом:
[
  {{
    "original": "текст з фото",
    "normalized": "назва як у прайсі",
    "qty": "кількість",
    "category": "ключ категорії"
  }}
]"""

    from google.genai import types as genai_types
    image_bytes = base64.b64decode(image_b64)
    
    # ВИПРАВЛЕНО: Використання стабільної моделі gemini-1.5-flash
    resp = gemini_client.models.generate_content(
        model="gemini-1.5-flash", 
        contents=[
            genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            genai_types.Part.from_text(text=prompt)
        ]
    )
    
    raw = resp.text.strip().replace('
```json', '').replace('```', '').strip()
    if '[' in raw:
        raw = raw[raw.index('['):raw.rindex(']')+1]
    return json.loads(raw)

def нормалізувати_текст(текст):
    rules = get_rules()
    rules_block = f"\nДодаткові правила:\n{rules}" if rules else ""

    prompt = f"""Ти — експерт із сантехніки України. Нормалізуй список:
{текст}
{rules_block}

ДОСТУПНІ КАТЕГОРІЇ:
{CATALOG_DESCRIPTIONS}

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом."""

    resp = client.messages.create(
        model="claude-3-5-sonnet-20240620", # Актуальна модель Claude
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
    return json.loads(raw)

# ─── КРОК 2: ПОШУК У КАТАЛОЗІ ────────────────────────────────────────────────
def знайти_у_каталозі(позиції, chat_id=None, msg_id=None, bot_ref=None):
    grouped = {}
    unknown = []

    for поз in позиції:
        cat = поз.get("category", "")
        if cat and cat in catalogs:
            grouped.setdefault(cat, []).append(поз)
        else:
            unknown.append(поз)

    всі_результати_map = {}

    for cat_key, група in grouped.items():
        if chat_id and msg_id and bot_ref:
            try:
                bot_ref.edit_message_text(
                    f"🔍 Шукаю: {catalogs[cat_key]['label']}...",
                    chat_id=chat_id, message_id=msg_id
                )
            except: pass

        df_cat = catalogs[cat_key]["df"]
        всі_кандидати = set()
        for поз in група:
            normalized = поз.get("normalized", "")
            слова = [s.lower() for s in normalized.split() if len(s) > 2]
            for _, row in df_cat.iterrows():
                назва = str(row['Наименование']).lower()
                if any(с in назва for с in слова):
                    всі_кандидати.add(row['Наименование'])

        кандидати_рядки = []
        for _, row in df_cat.iterrows():
            if row['Наименование'] in всі_кандидати:
                кандидати_рядки.append(f"{row['Наименование']} | WMS: {row['Артикул WMS']} | Код: {row['Код']}")
            if len(кандидати_рядки) >= 60: break

        if not кандидати_рядки:
            for поз in група:
                всі_результати_map[поз['original']] = {
                    "original": поз['original'], "normalized": поз.get('normalized', ''),
                    "знайдено": False, "кількість": поз.get('qty', ''), "категорія": catalogs[cat_key]['label']
                }
            continue

        prompt = f"Знайди збіги для:\n" + "\n".join([п.get('normalized','') for п in група]) + \
                 "\n\nУ каталозі:\n" + "\n".join(кандидати_рядки) + "\n\nВідповідай ТІЛЬКИ JSON масивом."

        resp = client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        
        try:
            raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
            результати_батч = json.loads(raw)
        except:
            результати_батч = [{"знайдено": False}] * len(група)

        for i, поз in enumerate(група):
            r = результати_батч[i] if i < len(результати_батч) else {"знайдено": False}
            всі_результати_map[поз['original']] = {
                "original": поз['original'], "normalized": поз.get('normalized', ''),
                "знайдено": r.get("знайдено", False), "назва": r.get("назва", ""),
                "артикул": r.get("артикул", ""), "код": r.get("код", ""),
                "кількість": поз.get('qty', ''), "категорія": catalogs[cat_key]['label']
            }

    # Обробка невідомих (спрощений пошук)
    for поз in unknown:
        всі_результати_map[поз['original']] = {
            "original": поз['original'], "normalized": поз.get('normalized', ''),
            "знайдено": False, "кількість": поз.get('qty', ''), "категорія": ""
        }

    return [всі_результати_map.get(п['original']) for п in позиції]

# ─── EXCEL ТА ОБРОБКА ────────────────────────────────────────────────────────
def створити_excel(результати):
    rows = []
    not_found = []
    for r in результати:
        if r and r.get("знайдено"):
            rows.append({
                'Наименование': r.get('назва', ''), 'Артикул WMS': r.get('артикул', ''),
                'Код': r.get('код', ''), 'Кількість': r.get('кількість', ''),
                'Категорія': r.get('категорія', ''), 'Оригінал': r.get('original', ''),
            })
        elif r:
            not_found.append(r.get('normalized') or r.get('original', ''))

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        pd.DataFrame(rows).to_excel(writer, index=False, sheet_name='Замовлення')
        if not_found:
            pd.DataFrame({'Не знайдено': not_found}).to_excel(writer, index=False, sheet_name='Не знайдено')
    output.seek(0)
    return output, not_found

def process_batch(chat_id):
    batch = user_batches.pop(chat_id, None)
    if not batch: return
    items = batch['items']
    stop_flags.pop(chat_id, None)
    
    status_msg = bot.send_message(chat_id, f"🔄 Починаю обробку {len(items)} файл(ів)...")
    msg_id = status_msg.message_id
    всі_позиції = []

    for index, item in enumerate(items, 1):
        if stop_flags.get(chat_id): return
        try:
            if item['type'] == 'photo':
                bot.edit_message_text(f"📖 Читаю фото {index}...", chat_id=chat_id, message_id=msg_id)
                всі_позиції.extend(нормалізувати_фото(item['data'], item.get('caption', '')))
            elif item['type'] == 'text':
                всі_позиції.extend(нормалізувати_текст(item['text']))
        except Exception as e:
            bot.send_message(chat_id, f"❌ Помилка: {e}")

    if всі_позиції:
        результати = знайти_у_каталозі(всі_позиції, chat_id, msg_id, bot)
        excel, nf = створити_excel(результати)
        bot.send_document(chat_id, excel, visible_file_name="замовлення.xlsx")
        bot.edit_message_text(f"✅ Готово! Знайдено {len([r for r in результати if r.get('знайдено')])} поз.", chat_id=chat_id, message_id=msg_id)

def add_to_batch(chat_id, item):
    if chat_id not in user_batches:
        user_batches[chat_id] = {'items': []}
        bot.send_message(chat_id, "📥 Отримав. Чекаю ще файли 4 сек...")
    
    if 'timer' in user_batches[chat_id]:
        user_batches[chat_id]['timer'].cancel()
    
    user_batches[chat_id]['items'].append(item)
    timer = threading.Timer(4.0, process_batch, args=[chat_id])
    user_batches[chat_id]['timer'] = timer
    timer.start()

# ─── ОБРОБНИКИ КОМАНД ────────────────────────────────────────────────────────
@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.reply_to(message, "Привіт! Надсилай фото списку або текст з командою 'пошук'.")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded = bot.download_file(file_info.file_path)
    image_b64 = base64.b64encode(downloaded).decode('utf-8')
    add_to_batch(message.chat.id, {'type': 'photo', 'data': image_b64, 'caption': message.caption or ""})

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('пошук'))
def handle_text_search(message):
    add_to_batch(message.chat.id, {'type': 'text', 'text': message.text[5:].strip()})

@bot.message_handler(commands=['stop'])
def handle_stop(message):
    stop_flags[message.chat.id] = True
    bot.reply_to(message, "🛑 Зупинено.")

# Запуск бота
print("🤖 Бот запущений...")
bot.polling(none_stop=True)
