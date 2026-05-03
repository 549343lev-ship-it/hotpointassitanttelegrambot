import telebot
import anthropic
import google.generativeai as genai
import os
import pandas as pd
import base64
import json
import threading
from io import BytesIO

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY")

bot = telebot.TeleBot(TELEGRAM_TOKEN)
client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

GEMINI_KEY = os.environ.get("GEMINI_KEY")
genai.configure(api_key=GEMINI_KEY)
gemini = genai.GenerativeModel("gemini-2.0-flash")

# ─── КАТАЛОГИ ПО ФАЙЛАХ ──────────────────────────────────────────────────────
# Кожен файл = окрема категорія товарів
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

# ─── ЗАВАНТАЖЕННЯ ВСІХ КАТАЛОГІВ ─────────────────────────────────────────────
# catalogs = { "sewage": {"label": "Каналізація", "df": DataFrame} }
catalogs = {}

def load_catalog(filename, label):
    if not os.path.exists(filename):
        print(f"⚠️  Файл не знайдено: {filename}")
        return None
    try:
        df = pd.read_excel(filename, header=0)
        # Перші 4 колонки завжди: Наименование, Артикул WMS, ОР, Код
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
    else:
        print(f"  ⚠️  {filename}: пропущено")

print(f"📦 Завантажено {len(catalogs)} каталогів")

# Будуємо текстовий опис каталогів для Claude (для визначення категорії)
CATALOG_DESCRIPTIONS = "\n".join(
    f"- {key}: {info['label']}"
    for key, info in catalogs.items()
)

# ─── ПРАВИЛА ─────────────────────────────────────────────────────────────────
user_batches = {}
stop_flags = {}   # chat_id -> True якщо юзер хоче зупинити
pending_hints = {}  # chat_id -> текст-підказка для наступного фото
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

    prompt = f"""Ти — експерт із сантехніки України. На фото рукописний список замовлення від майстра-сантехніка.

ПІДКАЗКА ВІД МЕНЕДЖЕРА: {caption}
(якщо вказано виробника — використовуй його для ВСІХ позицій списку)
(якщо вказано тип системи — застосовуй до всіх підходящих позицій){rules_block}

ЗАВДАННЯ:
1. Прочитай кожен рядок — ТІЛЬКИ сантехніка/опалення/водопостачання, ігноруй електрику
2. Нормалізуй до короткої торгової назви БЕЗ зайвих слів
3. Витягни кількість
4. Визнач категорію

СЛОВНИК СЛЕНГУ МАЙСТРІВ:
- "кол", "кут", кутик намальований = Коліно
- "рож", "рожон", "трійн" = Трійник
- "муф", "муфта" = Муфта
- "тр", "труба", "д20", "ф20" = Труба (діаметр береться з числа)
- "кр", "кран" = Кран кульовий
- "пер" = Перехідник
- "шар" = Кран кульовий
- "зат" = Засувка
- число після "х" або "x" = розмір (25х20 = 25 на 20мм)
- "вн", "вн.р" = внутрішня різьба
- "зовн", "зовн.р" = зовнішня різьба
- PN20 або "вода" в заголовку = труба PN20 поліпропіленова
- PN25 або "тепло", "опал" в заголовку = труба PN25 армована
- "+ізол" = додати утеплювач для кожної труби
- "АСГ", "асг" = виробник ASG
- "рафтек", "raftec", "RAFTEC" = виробник RAFTEC
- "плм", "PLM" = виробник PLM (утеплювач)

ФОРМАТ НОРМАЛІЗОВАНОЇ НАЗВИ: коротко як в прайсі
Приклади:
- "труба д 20 - 15м" → normalized: "Труба ППР PN20 20мм", qty: "15"
- "кутник 25х20" → normalized: "Коліно ППР 25мм"
- "рожонці 25х20х25" → normalized: "Трійник ППР 25х20х25мм"
- "муфта 20х1/2" → normalized: "Муфта ППР 20х1/2 вн.різьба"
- "кол 110" → normalized: "Коліно каналізаційне 110мм"
- "тр 50-2м" → normalized: "Труба каналізаційна ПВХ 50мм", qty: "2"
- "кр 1/2" → normalized: "Кран кульовий 1/2"

ДОСТУПНІ КАТЕГОРІЇ:
{CATALOG_DESCRIPTIONS}

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом, без пояснень:
[
  {{
    "original": "що написано на фото",
    "normalized": "коротка торгова назва",
    "qty": "кількість або пусто",
    "category": "ключ категорії або пусто"
  }}
]"""

    import PIL.Image, io
    image_bytes = __import__('base64').b64decode(image_b64)
    pil_image = PIL.Image.open(io.BytesIO(image_bytes))
    resp = gemini.generate_content([prompt, pil_image])
    raw = resp.text.strip().replace('```json', '').replace('```', '').strip()
    if '[' in raw:
        raw = raw[raw.index('['):raw.rindex(']')+1]
    return json.loads(raw)


def нормалізувати_текст(текст):
    rules = get_rules()
    rules_block = f"\nДодаткові правила:\n{rules}" if rules else ""

    prompt = f"""Ти — експерт із сантехніки України. Майстер надіслав список текстом:{rules_block}

ТЕКСТ:
{текст}

СЛОВНИК СЛЕНГУ:
- "кол", "кут" = Коліно
- "рож", "рожон" = Трійник
- "муф" = Муфта  
- "тр", "д20", "ф20" = Труба
- "кр", "шар" = Кран кульовий
- PN20/вода = поліпропіленова PN20, PN25/тепло = армована PN25
- "+ізол" = додати утеплювач

ДОСТУПНІ КАТЕГОРІЇ:
{CATALOG_DESCRIPTIONS}

ЗАВДАННЯ: нормалізуй кожну позицію до короткої торгової назви як в прайсі, витягни кількість і визнач категорію.

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом:
[
  {{
    "original": "оригінальний рядок",
    "normalized": "коротка торгова назва",
    "qty": "кількість або пусто",
    "category": "ключ категорії або пусто"
  }}
]"""

    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
    return json.loads(raw)


# ─── КРОК 2: ПОШУК У КАТАЛОЗІ (БАТЧ — 1 запит на категорію) ─────────────────
def знайти_у_каталозі(позиції, chat_id=None, msg_id=None, bot_ref=None):
    """
    Групуємо позиції по категоріях → 1 API запит на категорію.
    Замість 78 запитів — 3-5 запитів. Економія ~95% коштів.
    """
    # Групуємо по категорії
    grouped = {}  # category_key -> [поз, ...]
    unknown = []  # позиції без категорії

    for поз in позиції:
        cat = поз.get("category", "")
        if cat and cat in catalogs:
            grouped.setdefault(cat, []).append(поз)
        else:
            unknown.append(поз)

    всі_результати_map = {}  # original -> результат

    # ── Обробка груп по категоріях ──
    total_cats = len(grouped)
    done_cats = [0]
    for cat_key, група in grouped.items():
        done_cats[0] += 1
        if chat_id and msg_id and bot_ref:
            try:
                bot_ref.edit_message_text(
                    f"🔍 Шукаю категорію {done_cats[0]}/{total_cats}: {catalogs[cat_key]['label']} ({len(група)} позицій)...",
                    chat_id=chat_id, message_id=msg_id
                )
            except Exception:
                pass
        cat_info = catalogs[cat_key]
        df_cat = cat_info["df"]

        # Збираємо всіх кандидатів для всієї групи одним проходом
        всі_кандидати = set()
        for поз in група:
            normalized = поз.get("normalized", "")
            слова = [s.lower() for s in normalized.split() if len(s) > 2]
            for _, row in df_cat.iterrows():
                назва = str(row['Наименование']).lower()
                if any(с in назва for с in слова):
                    всі_кандидати.add(row['Наименование'])

        # Топ-60 унікальних кандидатів для всієї групи
        кандидати_рядки = []
        for _, row in df_cat.iterrows():
            if row['Наименование'] in всі_кандидати:
                кандидати_рядки.append(
                    f"{row['Наименование']} | WMS: {row['Артикул WMS']} | Код: {row['Код']}"
                )
            if len(кандидати_рядки) >= 60:
                break

        if not кандидати_рядки:
            for поз in група:
                всі_результати_map[поз['original']] = {
                    "original": поз['original'],
                    "normalized": поз.get('normalized', ''),
                    "знайдено": False,
                    "назва": "", "артикул": "", "код": "",
                    "кількість": поз.get('qty', ''),
                    "категорія": cat_info['label']
                }
            continue

        # Формуємо список запитів для батчу
        запити_текст = "\n".join(
            f"{i+1}. {поз.get('normalized','')}"
            for i, поз in enumerate(група)
        )
        кандидати_текст = "\n".join(кандидати_рядки)

        prompt = f"""Ти — експерт із сантехніки. Знайди найкращий збіг з каталогу для кожного запиту.

КАТЕГОРІЯ: {cat_info['label']}

ЗАПИТИ:
{запити_текст}

КАТАЛОГ:
{кандидати_текст}

Для кожного запиту обери ОДИН найкращий збіг або постав знайдено: false.

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом (порядок як у запитах):
[
  {{"знайдено": true, "назва": "точна назва з каталогу", "артикул": "WMS артикул", "код": "код"}},
  {{"знайдено": false}}
]"""

        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
        # Витягуємо тільки JSON масив якщо є зайвий текст
        if '[' in raw:
            raw = raw[raw.index('['):raw.rindex(']')+1]
        try:
            результати_батч = json.loads(raw)
        except Exception:
            результати_батч = [{"знайдено": False}] * len(група)

        for i, поз in enumerate(група):
            r = результати_батч[i] if i < len(результати_батч) else {"знайдено": False}
            всі_результати_map[поз['original']] = {
                "original": поз['original'],
                "normalized": поз.get('normalized', ''),
                "знайдено": r.get("знайдено", False),
                "назва": r.get("назва", ""),
                "артикул": r.get("артикул", ""),
                "код": r.get("код", ""),
                "кількість": поз.get('qty', ''),
                "категорія": cat_info['label']
            }

    # ── Невідомі категорії — шукаємо по всіх каталогах батчем ──
    for поз in unknown:
        normalized = поз.get("normalized", "")
        слова = [s.lower() for s in normalized.split() if len(s) > 2]
        кандидати = []
        for cat_key, cat_info in catalogs.items():
            for _, row in cat_info["df"].iterrows():
                назва = str(row['Наименование']).lower()
                score = sum(1 for с in слова if с in назва)
                if score > 0:
                    кандидати.append((score, row, cat_info['label']))
        кандидати.sort(key=lambda x: -x[0])
        топ = кандидати[:30]

        if not топ:
            всі_результати_map[поз['original']] = {
                "original": поз['original'], "normalized": normalized,
                "знайдено": False, "назва": "", "артикул": "", "код": "",
                "кількість": поз.get('qty', ''), "категорія": ""
            }
            continue

        кандидати_текст = "\n".join(
            f"{r['Наименование']} | WMS: {r['Артикул WMS']} | Код: {r['Код']}"
            for _, r, _ in топ
        )
        prompt = f"""З цього списку товарів обери ОДИН найкращий збіг.

ЗАПИТ: {normalized}

СПИСОК:
{кандидати_текст}

JSON відповідь:
{{"знайдено": true, "назва": "...", "артикул": "...", "код": "..."}}
або {{"знайдено": false}}"""

        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
        if '{' in raw:
            raw = raw[raw.index('{'):raw.rindex('}')+1]
        try:
            r = json.loads(raw)
        except Exception:
            r = {"знайдено": False}
        всі_результати_map[поз['original']] = {
            "original": поз['original'], "normalized": normalized,
            "знайдено": r.get("знайдено", False),
            "назва": r.get("назва", ""), "артикул": r.get("артикул", ""),
            "код": r.get("код", ""), "кількість": поз.get('qty', ''),
            "категорія": топ[0][2] if r.get("знайдено") else ""
        }

    # Повертаємо в оригінальному порядку
    return [всі_результати_map.get(п['original'], {
        "original": п['original'], "normalized": п.get('normalized',''),
        "знайдено": False, "назва": "", "артикул": "", "код": "",
        "кількість": п.get('qty',''), "категорія": ""
    }) for п in позиції]


# ─── EXCEL ───────────────────────────────────────────────────────────────────
def створити_excel(результати):
    rows = []
    not_found = []

    for r in результати:
        if r.get("знайдено"):
            rows.append({
                'Наименование': r.get('назва', ''),
                'Артикул WMS': r.get('артикул', ''),
                'Код': r.get('код', ''),
                'Кількість': r.get('кількість', ''),
                'Категорія': r.get('категорія', ''),
                'Оригінал (від майстра)': r.get('original', ''),
            })
        else:
            not_found.append(r.get('normalized') or r.get('original', ''))

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_out = pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=['Наименование','Артикул WMS','Код','Кількість','Категорія','Оригінал (від майстра)']
        )
        df_out.to_excel(writer, index=False, sheet_name='Замовлення')

        if not_found:
            df_nf = pd.DataFrame({'Не знайдено в базі': not_found})
            df_nf.to_excel(writer, index=False, sheet_name='Не знайдено')

    output.seek(0)
    return output, not_found


# ─── ОСНОВНА ОБРОБКА БАТЧУ ───────────────────────────────────────────────────
def process_batch(chat_id):
    batch = user_batches.pop(chat_id, None)
    if not batch:
        return

    items = batch['items']
    stop_flags.pop(chat_id, None)  # скидаємо флаг на початку
    status_msg = bot.send_message(chat_id, f"🔄 Починаю обробку {len(items)} файл(ів)...")
    msg_id = status_msg.message_id

    всі_позиції = []
    errors = []

    for index, item in enumerate(items, 1):
        if stop_flags.get(chat_id):
            bot.edit_message_text("🛑 Зупинено.", chat_id=chat_id, message_id=msg_id)
            return
        try:
            if item['type'] == 'photo':
                bot.edit_message_text(
                    f"📖 Крок 1/{len(items)}: Читаю і нормалізую фото {index}...",
                    chat_id=chat_id, message_id=msg_id
                )
                позиції = нормалізувати_фото(item['data'], item.get('caption', ''))
                всі_позиції.extend(позиції)

            elif item['type'] == 'text':
                bot.edit_message_text(
                    f"📝 Крок 1: Нормалізую текстовий запит...",
                    chat_id=chat_id, message_id=msg_id
                )
                позиції = нормалізувати_текст(item['text'])
                всі_позиції.extend(позиції)

        except Exception as e:
            errors.append(f"❌ Помилка нормалізації елемента {index}: {e}")

    if not всі_позиції:
        bot.edit_message_text("😕 Не вдалося розпізнати жодної позиції.", chat_id=chat_id, message_id=msg_id)
        return

    preview = "\n".join(
        f"• {п['original']} → {п['normalized']}"
        + (f" ({п['qty']})" if п.get('qty') else "")
        + (f" [{п.get('category','')}]" if п.get('category') else "")
        for п in всі_позиції[:10]
    )
    if len(всі_позиції) > 10:
        preview += f"\n... та ще {len(всі_позиції)-10} позицій"

    bot.send_message(chat_id, f"✅ Розпізнано {len(всі_позиції)} позицій:\n\n{preview}\n\n🔍 Шукаю в базі...")

    bot.edit_message_text(
        f"🔍 Крок 2: Шукаю {len(всі_позиції)} позицій у базі товарів...",
        chat_id=chat_id, message_id=msg_id
    )

    if stop_flags.get(chat_id):
        bot.edit_message_text("🛑 Зупинено.", chat_id=chat_id, message_id=msg_id)
        return

    # Пошук з таймером прогресу
    прогрес = [0]
    def notify_progress():
        прогрес[0] += 1
        хв = прогрес[0] * 30
        try:
            bot.edit_message_text(
                f"⏳ Шукаю {len(всі_позиції)} позицій... ({хв} сек)",
                chat_id=chat_id, message_id=msg_id
            )
        except Exception:
            pass
        if прогрес[0] < 6:  # максимум 3 хв
            progress_timer = threading.Timer(30.0, notify_progress)
            progress_timer.daemon = True
            progress_timer.start()
    
    progress_timer = threading.Timer(30.0, notify_progress)
    progress_timer.daemon = True
    progress_timer.start()

    результати = []
    try:
        результати = знайти_у_каталозі(всі_позиції, chat_id, msg_id, bot)
    except Exception as e:
        try:
            bot.edit_message_text(f"❌ Помилка пошуку: {e}", chat_id=chat_id, message_id=msg_id)
        except Exception:
            bot.send_message(chat_id, f"❌ Помилка пошуку: {e}")
        return
    finally:
        прогрес[0] = 99  # зупиняємо таймер

    bot.edit_message_text("📊 Формую Excel файл...", chat_id=chat_id, message_id=msg_id)

    excel, not_found = створити_excel(результати)
    знайдено = [r for r in результати if r.get("знайдено")]

    bot.send_document(chat_id, excel, visible_file_name="замовлення.xlsx")

    звіт = f"✅ Знайдено: {len(знайдено)} з {len(результати)} позицій"
    if not_found:
        звіт += f"\n⚠️ Не знайдено ({len(not_found)} шт.) — дивись лист 'Не знайдено' в Excel:\n"
        звіт += "\n".join(f"• {n}" for n in not_found[:5])
        if len(not_found) > 5:
            звіт += f"\n... та ще {len(not_found)-5}"
    if errors:
        звіт += "\n\n" + "\n".join(errors)

    bot.edit_message_text(звіт, chat_id=chat_id, message_id=msg_id)


# ─── БАТЧ ────────────────────────────────────────────────────────────────────
def add_to_batch(chat_id, item):
    if chat_id not in user_batches:
        user_batches[chat_id] = {'items': []}
        bot.send_message(chat_id, "📥 Отримав! Чекаю 4 сек, чи будуть ще файли...")

    if 'timer' in user_batches[chat_id]:
        user_batches[chat_id]['timer'].cancel()

    user_batches[chat_id]['items'].append(item)
    timer = threading.Timer(4.0, process_batch, args=[chat_id])
    user_batches[chat_id]['timer'] = timer
    timer.start()


# ─── ОБРОБНИКИ ───────────────────────────────────────────────────────────────
@bot.message_handler(commands=['start', 'help'])
def handle_start(message):
    bot.reply_to(message, """👋 Привіт! Я бот для підбору сантехніки.

📸 Кинь фото рукописного списку — я розпізнаю і знайду товари в базі
📝 Напиши *пошук <текст>* — для текстового запиту
📋 Напиши *правило <текст>* — щоб навчити мене сленгу

Приклад: `правило кол = коліно каналізаційне`""", parse_mode="Markdown")


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('правило'))
def handle_rule(message):
    new_rule = message.text[7:].strip()
    if new_rule:
        add_rule(new_rule)
        bot.reply_to(message, f"✅ Записав правило:\n_{new_rule}_\n\nВраховуватиму при наступних фото.", parse_mode="Markdown")
    else:
        bot.reply_to(message, "Напиши правило після слова 'Правило'.\nНаприклад:\n`Правило кол = коліно каналізаційне`", parse_mode="Markdown")


@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    for attempt in range(3):
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded = bot.download_file(file_info.file_path)
            image_b64 = base64.b64encode(downloaded).decode('utf-8')
            # Якщо є caption — використовуємо, якщо forwarded — caption може бути порожнім
            caption = message.caption or ""
            # Якщо є збережена підказка — додаємо до caption
            hint = pending_hints.pop(message.chat.id, "")
            full_caption = " | ".join(filter(None, [caption, hint]))
            add_to_batch(message.chat.id, {
                'type': 'photo',
                'data': image_b64,
                'caption': full_caption
            })
            return
        except Exception as e:
            if attempt == 2:
                bot.reply_to(message, f"❌ Не вдалося завантажити фото після 3 спроб: {e}")
            else:
                import time; time.sleep(2)


@bot.message_handler(func=lambda m: m.text and not m.text.startswith('/') 
                     and not m.text.lower().startswith('пошук')
                     and not m.text.lower().startswith('правило'))
def handle_forwarded_text(message):
    """Forwarded текст зберігаємо як підказку до наступного фото (10 сек)"""
    if message.forward_from or message.forward_from_chat or message.forward_sender_name:
        текст = message.text.strip()
        if текст:
            pending_hints[message.chat.id] = текст
            bot.reply_to(message, f"💬 Запам'ятав підказку: _{текст}_\n\nТепер кидай фото — врахую!", parse_mode="Markdown")
            # Скидаємо підказку через 60 сек якщо фото не надійшло
            def clear_hint(chat_id):
                pending_hints.pop(chat_id, None)
            t = threading.Timer(60.0, clear_hint, args=[message.chat.id])
            t.daemon = True
            t.start()


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('пошук'))
def handle_text(message):
    запит = message.text[5:].strip()
    if запит:
        add_to_batch(message.chat.id, {'type': 'text', 'text': запит})
    else:
        bot.reply_to(message, "Напиши запит після слова 'пошук'.\nНаприклад: `пошук труба 50`", parse_mode="Markdown")


@bot.message_handler(commands=['stop'])
def handle_stop(message):
    chat_id = message.chat.id
    stop_flags[chat_id] = True
    if chat_id in user_batches:
        if 'timer' in user_batches[chat_id]:
            user_batches[chat_id]['timer'].cancel()
        user_batches.pop(chat_id, None)
    bot.reply_to(message, "🛑 Зупинено. Надсилай нове фото коли будеш готовий.")

bot.polling(none_stop=True)
