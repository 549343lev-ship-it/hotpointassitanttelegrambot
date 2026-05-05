import os, json, re, base64, threading, time
from io import BytesIO

import telebot
import anthropic
import pandas as pd
from google import genai as genai_new
from google.genai import types as genai_types

# ═══════════════════════════════════════════════════════════════════════════════
# ІНІЦІАЛІЗАЦІЯ
# ═══════════════════════════════════════════════════════════════════════════════
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_KEY")
GEMINI_KEY     = os.environ.get("GEMINI_KEY")

bot           = telebot.TeleBot(TELEGRAM_TOKEN)
claude        = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
gemini_client = genai_new.Client(api_key=GEMINI_KEY)

CATALOG_PATH = "catalog_smart.json"
RULES_FILE   = "rules.txt"

CATALOG_FILES = [
    ("adapters_reducers",        "Перехідники та редуктори"),
    ("automation",               "Автоматика опалення"),
    ("boilers",                  "Котли"),
    ("fasteners_sealants",       "Кріплення та ущільнювачі"),
    ("filtration",               "Фільтри та очистка"),
    ("heating",                  "Опалення"),
    ("hoses",                    "Шланги"),
    ("insulation",               "Утеплювач"),
    ("metal_plastic",            "Металопластик"),
    ("mixers_faucets",           "Змішувачі та крани"),
    ("plastic_ppr",              "Пластик ППР"),
    ("pumps",                    "Насоси"),
    ("push_systems",             "Системи PUSH"),
    ("radiators_radiatorsvalve", "Радіатори та арматура"),
    ("safety_valves",            "Арматура безпеки"),
    ("sanitary_ware",            "Санфаянс"),
    ("sewage",                   "Каналізація"),
    ("shutoff_valves",           "Запірна арматура"),
    ("siphons_fittings",         "Сифони та арматура"),
    ("towel_warmers",            "Полотенцесушителі"),
    ("underfloor_heating",       "Тепла підлога"),
    ("water_heaters",            "Водонагрівачі"),
    ("water_meters",             "Водолічильники"),
]

CATALOG = []
IS_READY = False

# ═══════════════════════════════════════════════════════════════════════════════
# РОЗУМНИЙ ТОКЕНІЗАТОР
# ═══════════════════════════════════════════════════════════════════════════════
def smart_tokenize(text: str) -> tuple[set, set]:
    """Розбиває текст на слова та окремо на цифри. Знищує 'х', '*' для чистих розмірів."""
    text = str(text).lower()
    text = text.replace('х', ' ').replace('x', ' ').replace('*', ' ').replace('-', ' ').replace(',', '.')
    
    tokens = set(re.findall(r'[а-яёіїєґa-z]+|\d+(?:\.\d+)?', text))
    numbers = set(re.findall(r'\d+(?:\.\d+)?', text))
    words = tokens - numbers
    return words, numbers

# ═══════════════════════════════════════════════════════════════════════════════
# АВТОБУДОВА КАТАЛОГУ
# ═══════════════════════════════════════════════════════════════════════════════
def is_header_row(name, artikul, or_val) -> bool:
    name = str(name).strip()
    if not name or name == 'nan': return True
    art = str(artikul).strip()
    if art and art not in ('nan', '0', ''): return False
    try:
        if float(or_val) > 0: return False
    except: pass
    return True

def build_catalog_from_xlsx() -> list[dict]:
    catalog = []
    search_dirs = ['.', 'src', os.path.dirname(os.path.abspath(__file__))]
    
    print("📂 Читання Excel файлів...")
    for key, label in CATALOG_FILES:
        found = False
        for d in search_dirs:
            path = os.path.join(d, f"{key}.xlsx")
            if os.path.exists(path):
                try:
                    df = pd.read_excel(path, header=0)
                    cols = list(df.columns)
                    rename = {}
                    if len(cols) >= 1: rename[cols[0]] = 'name'
                    if len(cols) >= 2: rename[cols[1]] = 'artikul'
                    if len(cols) >= 3: rename[cols[2]] = 'or_price'
                    if len(cols) >= 4: rename[cols[3]] = 'kod'
                    df = df.rename(columns=rename)
                    count = 0
                    for _, row in df.iterrows():
                        name    = str(row.get('name', '')).strip()
                        artikul = row.get('artikul', '')
                        or_val  = row.get('or_price', 0)
                        kod     = row.get('kod', '')
                        if is_header_row(name, artikul, or_val): continue
                        
                        try: price = float(or_val)
                        except: price = 0.0
                        
                        words, numbers = smart_tokenize(name)
                        
                        catalog.append({
                            'name':     name,
                            'artikul':  str(artikul).strip() if str(artikul).strip() != 'nan' else '',
                            'kod':      str(kod).strip() if str(kod).strip() != 'nan' else '',
                            'category': label,
                            'price':    price,
                            '_words':   list(words),
                            '_numbers': list(numbers)
                        })
                        count += 1
                    print(f"  ✅ {path}: {count} товарів")
                    found = True
                    break
                except Exception as e:
                    print(f"  ❌ {path}: {e}")
        if not found:
            print(f"  ⚠️  {key}.xlsx не знайдено")
    return catalog

def init_catalog_background():
    global CATALOG, IS_READY
    print("📦 Завантажую каталог у фоні...")
    
    if os.path.exists(CATALOG_PATH):
        with open(CATALOG_PATH, encoding="utf-8") as f:
            CATALOG = json.load(f)
        print(f"✅ Каталог завантажено з кешу: {len(CATALOG)} позицій")
    else:
        print("⚙️ Файл кешу не знайдено — будую з xlsx файлів...")
        CATALOG = build_catalog_from_xlsx()
        if CATALOG:
            with open(CATALOG_PATH, "w", encoding="utf-8") as f:
                json.dump(CATALOG, f, ensure_ascii=False)
            print(f"✅ Каталог збережено в кеш.")
            
    IS_READY = True
    print("🚀 БАЗА ДАНИХ ГОТОВА! Бот приймає запити.")

threading.Thread(target=init_catalog_background, daemon=True).start()

def get_rules() -> str:
    if not os.path.exists(RULES_FILE): return ""
    with open(RULES_FILE, encoding="utf-8") as f: return f.read().strip()

def add_rule(new_rule: str):
    with open(RULES_FILE, "a", encoding="utf-8") as f: f.write(f"- {new_rule}\n")

# ═══════════════════════════════════════════════════════════════════════════════
# КРОК 1: ОЧИЩЕНА НОРМАЛІЗАЦІЯ
# ═══════════════════════════════════════════════════════════════════════════════
ЗНАННЯ_САНТЕХНІКИ = """
КАНАЛІЗАЦІЯ: 90° = 87,5°. ASG = HTR (сіра), OSTENDORF = HT Safe.
PPR: PN20 = Faser HOT, PN25 = Nano Ag Composite. МРЗ/МРВ = Муфти.
СЛЕНГ: кол/кут=Коліно, трій/рожон=Трійник, муф=Муфта, пер=Перехідник, шар=Кран кульовий, єврокон=Євроконус, ізол=Утеплювач PLM.
"""

def normalize_photo(image_b64: str, caption: str = "") -> list[dict]:
    rules = get_rules()
    rules_block = f"\nДодаткові правила:\n{rules}" if rules else ""

    prompt = f"""Ти — експерт із сантехніки України. На фото рукописний список замовлення.
ПІДКАЗКА МЕНЕДЖЕРА: {caption}{rules_block}
БАЗА ЗНАНЬ: {ЗНАННЯ_САНТЕХНІКИ}

ЗАВДАННЯ:
1. Прочитай кожен рядок.
2. Нормалізуй до ІДЕАЛЬНО ЧИСТОГО ПОШУКОВОГО ЗАПИТУ. БЕЗ зайвих слів типу "градусів", "діаметр", "довжина", "мм". Тільки суха суть!
3. Цифри розмірів (25х20) обов'язково розбивай пробілом (25 20).

ПРИКЛАДИ ЧИСТОГО ФОРМАТУ:
- "тр 50 1м" → "Труба каналізаційна 50 1м АСГ"
- "коліно 25 90" → "Коліно PPR 25 90 RAFTEC"
- "трійник 25х20" → "Трійник редукційний PPR 25 20 25 ASG"
- "кліпси 25" → "Кліпса PPR 25 ASG"
- "американка 3/4" → "Американка пряма PPR 3/4 RAFTEC"

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом:
[ {{"original": "що написано на фото", "normalized": "чистий пошуковий запит", "qty": "кількість"}} ]"""

    image_bytes = base64.b64decode(image_b64)
    resp = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            genai_types.Part.from_text(text=prompt)
        ]
    )
    bt = chr(96) * 3
    raw = resp.text.strip().replace(bt+'json', '').replace(bt, '').strip()
    try:
        if '[' in raw and ']' in raw: raw = raw[raw.index('['):raw.rindex(']')+1]
        return json.loads(raw)
    except: return []

# ═══════════════════════════════════════════════════════════════════════════════
# КРОК 2: ЖОРСТКИЙ МАТЕМАТИЧНИЙ ПОШУК
# ═══════════════════════════════════════════════════════════════════════════════
def smart_keyword_search(query: str, top_n: int = 5) -> list[dict]:
    q_words, q_numbers = smart_tokenize(query)
    if not q_words and not q_numbers: return []

    scores = []
    for item in CATALOG:
        i_words = set(item.get('_words', []))
        i_numbers = set(item.get('_numbers', []))
        
        # ЖОРСТКИЙ ФІЛЬТР ЦИФР: Якщо в запиті є цифра (напр. 50), вона ПОВИННА бути в товарі!
        if q_numbers and not q_numbers.issubset(i_numbers):
            continue # Пропускаємо товар, якщо розміри не збігаються
            
        word_match = len(q_words & i_words)
        if word_match > 0 or len(q_numbers) > 0:
            # Даємо бали за збіг слів + пріоритет коротшим назвам каталогу (точніший збіг)
            precision = word_match / max(len(i_words), 1)
            score = (word_match * 2) + precision
            item['_score'] = score
            scores.append((score, item))

    scores.sort(key=lambda x: -x[0])
    return [item for _, item in scores[:top_n]]

# ═══════════════════════════════════════════════════════════════════════════════
# КРОК 3: CLAUDE + РЯТІВНИК (Fallback)
# ═══════════════════════════════════════════════════════════════════════════════
def claude_pick_batch(позиції_з_кандидатами: list[dict]) -> list[dict]:
    запити = []
    for i, пос in enumerate(позиції_з_кандидатами):
        кандидати = "\n".join(f"  {j+1}. {c['name']}" for j, c in enumerate(пос['candidates']))
        запити.append(f"{i+1}. ЗАПИТ: {пос['normalized']}\n   КАНДИДАТИ:\n{кандидати}")

    prompt = f"""Ти — експерт із сантехніки. Для кожного запиту обери ОДИН найкращий збіг з кандидатів.
{chr(10).join(запити)}
ВІДПОВІДАЙ ТІЛЬКИ JSON масивом: [ {{"знайдено": true, "номер_кандидата": 1}}, {{"знайдено": false}} ]"""

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5", # Ваша вказана модель
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        bt = chr(96) * 3
        raw = resp.content[0].text.strip().replace(bt+'json', '').replace(bt, '').strip()
        if '[' in raw and ']' in raw: raw = raw[raw.index('['):raw.rindex(']')+1]
        return json.loads(raw)
    except Exception as e:
        print(f"⚠️ Claude API Error: {e}. Вмикаю автоматичний математичний вибір!")
        # Повертаємо пустий масив, щоб увімкнувся Fallback
        return []

def find_items(позиції: list[dict]) -> list[dict]:
    потребують_claude = []
    результати = [None] * len(позиції)

    for i, пос in enumerate(позиції):
        normalized = пос.get('normalized', '')
        кандидати = smart_keyword_search(normalized, top_n=5)

        if not кандидати:
            результати[i] = {**пос, 'знайдено': False, 'назва': ''}
            continue

        потребують_claude.append({'idx': i, 'normalized': normalized, 'candidates': кандидати, 'qty': пос.get('qty',''), 'original': пос.get('original','')})

    if потребують_claude:
        відповіді = claude_pick_batch(потребують_claude)
        for j, пос in enumerate(потребують_claude):
            idx = пос['idx']
            r = відповіді[j] if j < len(відповіді) else {}
            
            # АВТОМАТИЧНИЙ РЯТІВНИК (Fallback): Якщо Claude впав або сказав false, але у нас є хороший кандидат з математики!
            if r.get('знайдено') and r.get('номер_кандидата'):
                n = max(0, min(int(r['номер_кандидата']) - 1, len(пос['candidates'])-1))
                found = пос['candidates'][n]
            elif len(пос['candidates']) > 0:
                # Беремо топового кандидата з математичного фільтру
                found = пос['candidates'][0]
            else:
                found = None

            if found:
                результати[idx] = {
                    'original':  пос['original'], 'normalized': пос['normalized'],
                    'знайдено':  True, 'назва': found['name'], 'ціна': found.get('price', ''), 'qty': пос['qty']
                }
            else:
                результати[idx] = {
                    'original':  пос['original'], 'normalized': пос['normalized'],
                    'знайдено':  False, 'назва': '', 'qty': пос['qty']
                }

    return результати

# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL ТА БАТЧ
# ═══════════════════════════════════════════════════════════════════════════════
def create_excel(результати: list[dict]) -> tuple[BytesIO, list[str]]:
    rows, not_found = [], []
    for r in результати:
        if r and r.get('знайдено'):
            rows.append({'Наименование': r.get('назва', ''), 'Кількість': r.get('qty', ''), 'Ціна': r.get('ціна', ''), 'Оригінал': r.get('original', '')})
        elif r:
            not_found.append(r.get('normalized') or r.get('original', ''))

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=['Наименование', 'Кількість', 'Ціна', 'Оригінал'])
        df.to_excel(writer, index=False, sheet_name='Замовлення')
        if not_found:
            pd.DataFrame({'Не знайдено': not_found}).to_excel(writer, index=False, sheet_name='Не знайдено')
    output.seek(0)
    return output, not_found

user_batches, stop_flags, pending_hints = {}, {}, {}

def process_batch(chat_id: int):
    if not IS_READY:
        bot.send_message(chat_id, "⏳ Бот ще завантажується. Будь ласка, зачекайте.")
        user_batches.pop(chat_id, None)
        return

    batch = user_batches.pop(chat_id, None)
    if not batch: return
    stop_flags.pop(chat_id, None)
    
    items = batch['items']
    msg_id = bot.send_message(chat_id, f"🔄 Читаю {len(items)} фото...").message_id

    всі_позиції, errors = [], []
    for idx, item in enumerate(items, 1):
        if stop_flags.get(chat_id): return
        try:
            bot.edit_message_text(f"📖 Читаю фото {idx}/{len(items)}...", chat_id=chat_id, message_id=msg_id)
            всі_позиції.extend(normalize_photo(item['data'], item.get('caption', '')))
        except Exception as e: errors.append(f"Помилка {idx}: {e}")

    if not всі_позиції:
        bot.edit_message_text("😕 Нічого не знайдено.", chat_id=chat_id, message_id=msg_id)
        return

    preview = "\n".join(f"• {п.get('original','')} → {п.get('normalized','')}" for п in всі_позиції[:8])
    if len(всі_позиції) > 8: preview += f"\n... та ще {len(всі_позиції)-8}"
    
    bot.send_message(chat_id, f"✅ Розпізнано (Очищений формат):\n\n{preview}")
    bot.edit_message_text(f"🔍 Шукаю {len(всі_позиції)} позицій у базі...", chat_id=chat_id, message_id=msg_id)

    результати = find_items(всі_позиції)

    bot.edit_message_text("📊 Формую Excel...", chat_id=chat_id, message_id=msg_id)
    excel, not_found = create_excel(результати)

    знайдено = [r for r in результати if r and r.get('знайдено')]
    bot.send_document(chat_id, excel, visible_file_name="замовлення.xlsx")

    звіт = f"✅ Знайдено: {len(знайдено)}/{len(результати)} позицій"
    if not_found: звіт += f"\n⚠️ Не знайдено ({len(not_found)} шт.):\n" + "\n".join(f"• {n}" for n in not_found[:5])
    bot.edit_message_text(звіт, chat_id=chat_id, message_id=msg_id)

def add_to_batch(chat_id: int, item: dict):
    if not IS_READY:
        bot.send_message(chat_id, "⏳ Зачекайте хвилинку, бот щойно перезапустився...")
        return
    if chat_id not in user_batches:
        user_batches[chat_id] = {'items': []}
        bot.send_message(chat_id, "📥 Отримав! Чекаю 4 сек...")
    if 'timer' in user_batches[chat_id]: user_batches[chat_id]['timer'].cancel()
    user_batches[chat_id]['items'].append(item)
    timer = threading.Timer(4.0, process_batch, args=[chat_id])
    user_batches[chat_id]['timer'] = timer
    timer.start()

# ═══════════════════════════════════════════════════════════════════════════════
# TELEGRAM ХЕНДЛЕРИ
# ═══════════════════════════════════════════════════════════════════════════════
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    bot.reply_to(message, "👋 Привіт! Бот для сантехніки.")

@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('правило'))
def handle_rule(message):
    rule = message.text[7:].strip()
    if rule:
        add_rule(rule)
        bot.reply_to(message, f"✅ Записав: {rule}")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded = bot.download_file(file_info.file_path)
    image_b64 = base64.b64encode(downloaded).decode('utf-8')
    caption = message.caption or ""
    hint = pending_hints.pop(message.chat.id, "")
    add_to_batch(message.chat.id, {'type': 'photo', 'data': image_b64, 'caption': " | ".join(filter(None, [caption, hint]))})

@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/'))
def handle_text_hint(message):
    text = message.text.strip()
    if text:
        pending_hints[message.chat.id] = text
        bot.reply_to(message, f"💬 Підказка збережена: {text}\nКидай фото!")

if __name__ == "__main__":
    print("🤖 Бот запущено!")
    bot.polling(none_stop=True)
