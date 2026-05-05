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
    ("adapters_reducers",        "Перехідники, латунь, редуктори (GEBO)"),
    ("automation",               "Автоматика опалення"),
    ("boilers",                  "Котли"),
    ("fasteners_sealants",       "Кріплення, хомути, ущільнювачі"),
    ("filtration",               "Фільтри та очистка"),
    ("heating",                  "Опалення загальне"),
    ("hoses",                    "Шланги"),
    ("insulation",               "Утеплювач (мерилон)"),
    ("metal_plastic",            "Металопластик"),
    ("mixers_faucets",           "Змішувачі та крани"),
    ("plastic_ppr",              "Пластик ППР (пайка, опори/кліпси, обводки, планки)"),
    ("pumps",                    "Насоси"),
    ("push_systems",             "Системи PUSH"),
    ("radiators_radiatorsvalve", "Радіатори та арматура до них"),
    ("safety_valves",            "Арматура безпеки"),
    ("sanitary_ware",            "Санфаянс (інсталяції)"),
    ("sewage",                   "Каналізація"),
    ("shutoff_valves",           "Запірна арматура (вкл. металеві американки, крани)"),
    ("siphons_fittings",         "Сифони та арматура"),
    ("towel_warmers",            "Полотенцесушителі"),
    ("underfloor_heating",       "Тепла підлога"),
    ("water_heaters",            "Водонагрівачі"),
    ("water_meters",             "Водолічильники"),
]

CATALOG_KEYS_DESC = "\n".join([f"- {k}: {desc}" for k, desc in CATALOG_FILES])

CATALOG = []
IS_READY = False

# ═══════════════════════════════════════════════════════════════════════════════
# РОЗУМНИЙ ТОКЕНІЗАТОР (Оминає PN та DN)
# ═══════════════════════════════════════════════════════════════════════════════
def smart_tokenize(text: str) -> tuple[set, set]:
    text = str(text).lower()
    text = re.sub(r'\bpn\s*\d+\b', ' ', text)
    text = re.sub(r'\bdn\s*\d+\b', ' ', text)
    text = text.replace('х', ' ').replace('x', ' ').replace('*', ' ').replace(',', '.')
    text = re.sub(r'[^\w\s/.]', ' ', text)
    numbers = set(re.findall(r'\b\d+(?:\.\d+)?(?:/\d+)?\b', text))
    words = set(re.findall(r'\b[а-яёіїєґa-z]+\b', text))
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
                            'file_key': key,
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
# КРОК 1: ІЗОЛЬОВАНА НОРМАЛІЗАЦІЯ
# ═══════════════════════════════════════════════════════════════════════════════
ЗНАННЯ_САНТЕХНІКИ = """
1. АМЕРИКАНКИ: Звичайна "американка 3/4" (метал/латунь) — це категорія "shutoff_valves" (Запірна арматура). Бувають прямі і кутові.
2. ПАЙКА (PPR): Якщо написано "мрн 25х3/4" або "американка 25х3/4" — це перехід з труби на різьбу, шукати в "plastic_ppr".
3. ОПОРИ/КЛІПСИ: Якщо написано "опора" — це означає "кліпса", шукати в "plastic_ppr".
4. ХОМУТИ: Для хомутів ігноруй "DN", дивись тільки на реальні розміри в міліметрах (напр. 48-53 мм).
5. СЛЕНГ: кол/кут=Коліно, трій/рожон=Трійник, муф=Муфта, пер=Перехідник, шар=Кран кульовий, єврокон=Євроконус, ізол=Утеплювач PLM.
"""

def normalize_photo(image_b64: str, caption: str = "") -> list[dict]:
    rules = get_rules()
    rules_block = f"\nДодаткові правила:\n{rules}" if rules else ""

    prompt = f"""Ти — професійний менеджер з продажу сантехніки. Твоє завдання — перекласти сленг монтажника на точну структуру для алгоритму.
ПІДКАЗКА МЕНЕДЖЕРА: {caption}
(ОБОВ'ЯЗКОВО! Враховуй виробників/бренди з підказки для відповідних категорій товару).

БАЗА ЗНАНЬ: {ЗНАННЯ_САНТЕХНІКИ}{rules_block}

КАТЕГОРІЇ (ФАЙЛИ) ДЛЯ ІЗОЛЯЦІЇ:
{CATALOG_KEYS_DESC}

ЗАВДАННЯ ДЛЯ КОЖНОГО РЯДКА:
1. Визнач правильну категорію (category_key).
2. Сформуй clean_name: ТІЛЬКИ тип деталі. БЕЗ розмірів і БЕЗ бренду! (напр. "Коліно PPR", "Кліпса").
3. Витягни ВСІ цифри розмірів у масив sizes (напр. ["25", "3/4"]).
4. ВИТЯГНИ БРЕНД (виробника) в окреме поле "brand". Якщо він є в підказці (напр. "Пайка рафтек") — пиши ЛАТИНИЦЕЮ "RAFTEC". Якщо немає, пиши "".

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом:
[
  {{
    "original": "американка 3/4",
    "category_key": "shutoff_valves",
    "clean_name": "Американка пряма",
    "brand": "",
    "sizes": ["3/4"],
    "qty": "5"
  }}
]"""

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
# КРОК 2: СУВОРИЙ МАТЕМАТИЧНИЙ ПОШУК (Бренди, PN, Різьба)
# ═══════════════════════════════════════════════════════════════════════════════
def smart_keyword_search(parsed_item: dict, top_n: int = 5) -> list[dict]:
    target_category = parsed_item.get('category_key', '')
    q_words, _ = smart_tokenize(parsed_item.get('clean_name', ''))
    q_sizes = set(parsed_item.get('sizes', []))
    q_brand = str(parsed_item.get('brand', '')).lower().strip()
    q_pn = str(parsed_item.get('pn', '')).lower().replace(' ', '')
    q_thread = str(parsed_item.get('thread_type', '')).lower()
    
    subset = [i for i in CATALOG if i.get('file_key') == target_category] if target_category else CATALOG
    if not subset: subset = CATALOG 

    scores = []
    for item in subset:
        i_words = set(item.get('_words', []))
        i_numbers = set(item.get('_numbers', []))
        item_name_lower = item['name'].lower()
        
        # 1. ЖОРСТКЕ ВІДСІКАННЯ ПО БРЕНДУ (АБСОЛЮТНИЙ ФІЛЬТР)
        if q_brand:
            valid_brand = False
            if q_brand in item_name_lower:
                valid_brand = True
            # Обробка синонімів для популярних брендів
            elif q_brand in ["еко", "eco", "ekoplastik"] and any(b in item_name_lower for b in ["eco", "ekoplastik"]):
                valid_brand = True
            elif q_brand == "asg" and "асг" in item_name_lower:
                valid_brand = True
                
            if not valid_brand:
                continue # ПРОПУСКАЄМО ТОВАР: Виробник не збігається!
        
        # 2. ЖОРСТКИЙ ФІЛЬТР РОЗМІРУ (Геометрія)
        if q_sizes and not q_sizes.issubset(i_numbers):
            continue 
            
        word_match = len(q_words & i_words)
        if word_match > 0 or not q_words:
            num_penalty = (len(i_numbers) - len(q_sizes)) * 0.2
            precision = word_match / max(len(i_words), 1)
            
            score = (word_match * 2) + precision - num_penalty
            confidence = min(100, int((word_match / max(len(q_words), 1)) * 100))
            if num_penalty > 0: confidence -= min(20, int(num_penalty * 10))
            
            # ПРІОРИТЕТ ТИСКУ (PN)
            if q_pn:
                if q_pn in item_name_lower:
                    score += 50
                    confidence = min(100, confidence + 20)
                else:
                    if "pn20" in item_name_lower or "pn16" in item_name_lower or "pn25" in item_name_lower:
                        score -= 40
                        confidence -= 30
                        
            # ПРІОРИТЕТ РІЗЬБИ (РВ/РЗ)
            if q_thread:
                if q_thread == "рв" and ("рв" in item_name_lower or "вв" in item_name_lower or "внутр" in item_name_lower):
                    score += 20
                elif q_thread == "рз" and ("рз" in item_name_lower or "зз" in item_name_lower or "зовн" in item_name_lower or "наруж" in item_name_lower):
                    score += 20
                else:
                    score -= 10
                    
            item['_confidence'] = f"{max(10, confidence)}%"
            scores.append((score, item))

    # М'який пошук (якщо суворий провалився) - ТАКОЖ ІЗ ЖОРСТКИМ БРЕНДОМ
    if not scores:
        for item in subset:
            item_name_lower = item['name'].lower()
            
            if q_brand:
                valid_brand = False
                if q_brand in item_name_lower: valid_brand = True
                elif q_brand in ["еко", "eco", "ekoplastik"] and any(b in item_name_lower for b in ["eco", "ekoplastik"]): valid_brand = True
                if not valid_brand: continue # Пропускаємо і тут
                
            i_words = set(item.get('_words', []))
            word_match = len(q_words & i_words)
            if word_match > 0:
                score = word_match / max(len(i_words), 1)
                confidence = min(60, int((word_match / max(len(q_words), 1)) * 100))
                item['_confidence'] = f"{confidence}%"
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
        info = f"Розміри: {пос.get('sizes')}, PN: {пос.get('pn')}, Бренд: {пос.get('brand')}, Різьба: {пос.get('thread_type')}"
        запити.append(f"{i+1}. ЗАПИТ: {пос['normalized']} ({info})\n   КАНДИДАТИ:\n{кандидати}")

    prompt = f"""Ти — експерт із сантехніки. Для кожного запиту обери ОДИН найкращий збіг з кандидатів.
{chr(10).join(запити)}
ВІДПОВІДАЙ ТІЛЬКИ JSON масивом: [ {{"знайдено": true, "номер_кандидата": 1}}, {{"знайдено": false}} ]"""

    try:
        resp = claude.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        bt = chr(96) * 3
        raw = resp.content[0].text.strip().replace(bt+'json', '').replace(bt, '').strip()
        if '[' in raw and ']' in raw: raw = raw[raw.index('['):raw.rindex(']')+1]
        return json.loads(raw)
    except Exception as e:
        print(f"⚠️ Claude API Error. Вмикаю fallback!")
        return []

def find_items(позиції: list[dict]) -> list[dict]:
    потребують_claude = []
    результати = [None] * len(позиції)

    for i, пос in enumerate(позиції):
        кандидати = smart_keyword_search(пос, top_n=5)

        if not кандидати:
            результати[i] = {**пос, 'знайдено': False, 'назва': '', 'оцінка': '0%'}
            continue

        normalized_str = f"[{пос.get('category_key', 'Всі')}] {пос.get('clean_name', '')}"
        потребують_claude.append({
            'idx': i, 'normalized': normalized_str, 
            'sizes': пос.get('sizes', []), 'brand': пос.get('brand', ''), 
            'pn': пос.get('pn', ''), 'thread_type': пос.get('thread_type', ''),
            'candidates': кандидати, 'qty': пос.get('qty',''), 'original': пос.get('original','')
        })

    if потребують_claude:
        відповіді = claude_pick_batch(потребують_claude)
        for j, пос in enumerate(потребують_claude):
            idx = пос['idx']
            r = відповіді[j] if j < len(відповіді) else {}
            
            confidence_str = "0%"
            
            if r.get('знайдено') and r.get('номер_кандидата'):
                n = max(0, min(int(r['номер_кандидата']) - 1, len(пос['candidates'])-1))
                found = пос['candidates'][n]
                confidence_str = found.get('_confidence', '100%')
            elif len(пос['candidates']) > 0:
                found = пос['candidates'][0]
                confidence_str = found.get('_confidence', '100%')
            else:
                found = None

            # 5. ПОРІГ ВПЕВНЕНОСТІ 50% (ВІДСІКАННЯ)
            if found:
                try:
                    conf_val = int(confidence_str.replace('%', ''))
                except:
                    conf_val = 100 # Якщо не змогли розпізнати, довіряємо
                    
                if conf_val < 50:
                    found = None # Відхиляємо сумнівний товар
                else:
                    confidence = f"{conf_val}% (Claude)" if r.get('знайдено') else f"{conf_val}%"

            if found:
                результати[idx] = {
                    'original':  пос['original'], 'normalized': пос['normalized'],
                    'знайдено':  True, 'назва': found['name'], 'ціна': found.get('price', ''), 'qty': пос['qty'], 'оцінка': confidence
                }
            else:
                результати[idx] = {
                    'original':  пос['original'], 'normalized': пос['normalized'],
                    'знайдено':  False, 'назва': '', 'qty': пос['qty'], 'оцінка': '<50% (Відхилено)'
                }

    return результати

# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL ТА БАТЧ
# ═══════════════════════════════════════════════════════════════════════════════
def create_excel(результати: list[dict]) -> tuple[BytesIO, list[str]]:
    rows, not_found = [], []
    for r in результати:
        if r and r.get('знайдено'):
            rows.append({'Наименование': r.get('назва', ''), 'Кількість': r.get('qty', ''), 'Ціна': r.get('ціна', ''), 'Оцінка підбору': r.get('оцінка', ''), 'Оригінал': r.get('original', '')})
        elif r:
            not_found.append(r.get('normalized') or r.get('original', ''))

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=['Наименование', 'Кількість', 'Ціна', 'Оцінка підбору', 'Оригінал'])
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
            bot.edit_message_text(f"📖 Аналізую фото {idx}/{len(items)}...", chat_id=chat_id, message_id=msg_id)
            всі_позиції.extend(normalize_photo(item['data'], item.get('caption', '')))
        except Exception as e: errors.append(f"Помилка {idx}: {e}")

    if not всі_позиції:
        bot.edit_message_text("😕 Нічого не знайдено.", chat_id=chat_id, message_id=msg_id)
        return

    preview = "\n".join(f"• {п.get('original','')} → [{п.get('category_key','')}] {п.get('clean_name','')} (Розміри: {п.get('sizes',[])}, Бренд: {п.get('brand','')})" for п in всі_позиції[:8])
    if len(всі_позиції) > 8: preview += f"\n... та ще {len(всі_позиції)-8}"
    
    bot.send_message(chat_id, f"✅ Маршрутизація завершена:\n\n{preview}")
    bot.edit_message_text(f"🔍 Шукаю {len(всі_позиції)} позицій в ізольованих каталогах...", chat_id=chat_id, message_id=msg_id)

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
