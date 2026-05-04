import telebot
import anthropic
from google import genai as genai_new
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
gemini_client = genai_new.Client(api_key=GEMINI_KEY)

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

Скорочення фітингів ППР:
- "кол", "кут", кутик намальований = Коліно PPR
- "рож", "рожон", "рожонці" = Трійник редукційний PPR
- "трійн" = Трійник однозначний рівний PPR
- "муф" = Муфта з'єднувальна PPR
- "пер", "перехід пп" = Перехідник-редукція PPR
- "амер", "американка" = Американка PPR (роз'ємне з'єднання)
- "МРЗ", "мрз" = Муфта PPR МРЗ (з різьбою зовнішньою)
- "МРВ", "мрв" = Муфта PPR МРВ (з різьбою внутрішньою)
- "РЗ", "рз" в кутнику = Коліно PPR РЗ (з різьбою зовнішньою)
- "РВ", "рв" в кутнику = Коліно PPR РВ (з різьбою внутрішньою, настінне)
- "вухастий", "вушастий", "настінне" = Коліно PPR настінне РВ
- "футорка" = Футорка шестигранна з ущільнювачем
- "заглушка корок" = Заглушка різьбова PPR

Скорочення фітингів каналізація:
- "діжка", "бочонок" = Ніпель або Муфта
- "бутилка", "пляшка" = Редукція коротка каналізаційна
- "хомут метал", "хомут сталь" = Хомут металевий для кріплення труб
- "компенсатор", "муфта компенсатор" = Муфта довга (компенсаційний патрубок)

Скорочення кранів і арматури:
- "кр", "шар", "шаровий" = Кран кульовий
- "кран американка" = Кран кульовий з американкою
- "кран п/м" = Кран кульовий прямий муфтовий
- "мінік", "кран на злив" = Кран дренажний (спускний)
- "зат", "засувка" = Засувка BB
- "батерфляй" = Засувка тип метелик
- "вентиль" = Вентиль
- "хлопушка", "лепісковий", "пелюстковий" = Зворотний клапан пелюстковий
- "зв.кл", "зворотній" = Зворотний клапан
- "косий", "косий фільтр", "у-подібний", "у" = Фільтр грубої очистки
- "байпас" = Байпас (обвідна лінія)
- "кран під термо", "під термоголов" = Кран кутовий під термоголовку
- "термоголов", "термоголовка з датчиком" = Термоголовка з виносним датчиком

Колектори і тепла підлога:
- "колектор з витратомірами", "колектор витрат" = Колектор з витратомірами нержавійка RAFTEC STEEL
- "єврокон", "єврокон ф16", "єврокон ф20" = Євроконус для труби теплої підлоги
- "кінцевий елемент" = Кінцевий елемент колектора
- "термоголов", "термоголовка" = Термоголовка М30х1,5

Труби:
- "тр", "д20", "ф20" = Труба (діаметр з числа)
- "pex", "пекс" = Труба зшитий поліетилен PEX
- "stabi", "стабі" = Труба ППР армована алюмінієм Stabi
- "композитна", "nano" = Труба PPR Nano Ag Composite армована PN25
- "press steel", "прес сталь", "inox" = Прес-фітинги нержавійка тип V

Різьба і з'єднання:
- "вн", "вн.р", "рв" після діаметру = внутрішня різьба
- "зовн", "зовн.р", "рз" після діаметру = зовнішня різьба
- "вн.р ВЗ" = з'єднання зовні/внутрішня різьба
- число після "х" або "x" = розмір (25х1/2 = 25мм на 1/2")
- "ЗЗ", "зз" = з'єднання зовнішня різьба + зовнішня різьба (ніпель)
- "ВЗ" = внутрішня + зовнішня різьба

Системи:
- PN20 або "вода", "faser hot" в заголовку = Труба PPR Faser HOT PN20 PP-RCT ASG
- PN25 або "тепло", "опал", "армована", "nano" = Труба PPR Nano Ag Composite PN25 ASG
- "+ізол" = додати утеплювач PLM для кожної труби (синій і червоний)
- "ізоляція ф22" = Утеплювач ламін. для труб ф 22х6 мм, PLM
- "ізоляція ф28" = Утеплювач ламін. для труб ф 28х6 мм, PLM

Обладнання і матеріали:
- "бойлер", "тен" = Водонагрівач електричний
- "циркуль", "циркуляційний" = Насос циркуляційний
- "сололіфт" = Каналізаційна насосна станція
- "бінокль" = Вузол нижнього підключення радіатора
- "елька" = Трубка декоративна для підключення радіатора
- "мастило", "змазка для канал" = Силіконове мастило для каналізаційних труб Glidex
- "пакля", "льон" = Льон сантехнічний Unigarn UNIPAK
- "мультипак", "паста" = Паста для ущільнення різьбових з'єднань Multipak UNIPAK
- "демферна стрічка", "демпферна" = Демпферна стрічка для теплої підлоги
- "плівка з розміткою", "плівка" = Плівка з розміткою для теплої підлоги
- "клей піна", "піна" = Клей-піна монтажна Budmonster
- "інсталяція", "інсталяція унітаз" = Інсталяційна система Rapid SL Grohe
- "мінеральна вата", "мікопласт екструдер" = Мінераловатний утеплювач (уточни розмір)

Каналізація:
- "розтруб" = розширена частина труби для з'єднання
- "ревізія" = Трійник зі знімною кришкою для прочищення
- "фанова" = Фанова труба (вентиляція каналізації)

Виробники (якщо вказано — додай до КОЖНОЇ позиції):
- "асг", "asg" = ASG
- "рафтек", "raftec" = RAFTEC
- "рафтек блек", "raftec black" = RAFTEC BLACK
- "рафтек голд", "raftec gold" = RAFTEC GOLD
- "плм", "plm" = PLM (утеплювач)
- "екопластик", "wavin", "вавін" = Wavin Ekoplastik
- "остендорф", "ostendorf" = OSTENDORF
- "unipak", "юніпак" = UNIPAK
- "grohe", "гроє" = Grohe

КРИТИЧНО ВАЖЛИВО — нормалізуй ТОЧНО в стилі каталогу:

ППР пластик (ASG, Wavin, RAFTEC):
- "тр 20 PN20 ASG" → "Труба PPR Faser HOT ф 20х2,8 мм, PN20 , PP-RCT, ASG"
- "тр 32 PN20 ASG" → "Труба PPR Faser HOT ф 32х4,4 мм, PN20 , PP-RCT, ASG"
- "тр 25 PN25 ASG" → "Труба PPR Nano Ag Composite, ф 25x4,2 мм, PP-RCT, PN25, ASG"
- "кол 90 25" → "Коліно PPR 90° ф 25, PP-RCT, ASG" (шукай виробника з контексту)
- "трійник 25х20х25" → "Трійник редукційний PPR ф 25х20х25, PP-RCT, ASG"
- "муфта 32х1" → "Муфта PPR з зовн.різьбою ф 32х1", PP-RCT, ASG"

Каналізація (ASG, OSTENDORF):
- "тр 50 2м ASG" → "Труба внут. канал. ф 50 x 1,8 мм, L = 2,0 м, сіра, HTR, ASG"
- "тр 50 1м ASG" → "Труба внут. канал. ф 50 x 1,8 мм, L = 1,0 м, сіра, HTR, ASG"
- "кол 50 90 ASG" → "Коліно каналізаційне ф50х90°, ASG"
- "кол 50 45 ASG" → "Коліно каналізаційне ф50х45°, ASG"
- "трійник 50х50х45 ASG" → "Трійник каналізаційний ф50х50х45°, ASG"
- "редукція 50х32 ASG" → "Редукція каналізаційна ф50х32, ASG"

ПРАВИЛО: якщо виробник вказаний в підказці — ОБОВ'ЯЗКОВО додай його в кожну назву!
ПРАВИЛО: довжину труби завжди вказуй як "L = Xм" або окремим qty"

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

    import io
    from google.genai import types as genai_types
    image_bytes = __import__('base64').b64decode(image_b64)
    resp = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            genai_types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            genai_types.Part.from_text(text=prompt)
        ]
    )
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
- "кол", "кут", "вухастий" = Коліно (вухастий/настінне = Коліно PPR настінне РВ)
- "рож", "рожон", "рожонці" = Трійник редукційний PPR
- "муф" = Муфта з'єднувальна PPR
- "МРЗ"/"мрз" = Муфта PPR МРЗ зовнішня різьба
- "МРВ"/"мрв" = Муфта PPR МРВ внутрішня різьба
- "пер", "перехід пп" = Перехідник-редукція PPR
- "тр", "д20", "ф20" = Труба (діаметр з числа)
- "кр", "шар", "кран американка" = Кран кульовий (з американкою)
- "кран п/м" = Кран кульовий прямий муфтовий
- "косий", "у" = Фільтр грубої очистки
- "хлопушка", "лепісковий" = Зворотний клапан пелюстковий
- "під термо" = Кран кутовий під термоголовку
- "термоголов" = Термоголовка з виносним датчиком
- "єврокон" = Євроконус для теплої підлоги
- "кінцевий елемент" = Кінцевий елемент колектора
- "амер", "американка" = Американка роз'ємне з'єднання
- "байпас" = Байпас
- "циркуль" = Насос циркуляційний
- "бойлер", "тен" = Водонагрівач електричний
- "сололіфт" = Каналізаційна насосна станція
- "пакля", "льон" = Льон сантехнічний UNIPAK
- "мультипак" = Паста для різьб UNIPAK
- "демферна", "демпферна" = Демпферна стрічка
- "плівка" = Плівка з розміткою для теплої підлоги
- "інсталяція" = Інсталяційна система для унітазу
- PN20/вода/faser = PPR PN20 Faser HOT ASG
- PN25/тепло/nano = PPR PN25 Nano Ag Composite ASG
- "+ізол" = додати утеплювач PLM синій і червоний

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
        import re
        всі_кандидати = set()
        for поз in група:
            normalized = поз.get("normalized", "")
            слова = [s.lower() for s in normalized.split() if len(s) > 2 and not s.isdigit()]
            числа = re.findall(r'\d+', normalized)
            for _, row in df_cat.iterrows():
                назва = str(row['Наименование']).lower()
                score = sum(1 for с in слова if с in назва)
                score += sum(2 for n in числа if n in назва)
                if score > 0:
                    всі_кандидати.add(row['Наименование'])

        # Топ-60 унікальних кандидатів для всієї групи
        кандидати_рядки = []
        for _, row in df_cat.iterrows():
            if row['Наименование'] in всі_кандидати:
                кандидати_рядки.append(
                    f"{row['Наименование']} | WMS: {row['Артикул WMS']} | Код: {row['Код']}"
                )
            if len(кандидати_рядки) >= 80:
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
        import re
        слова = [s.lower() for s in normalized.split() if len(s) > 2 and not s.isdigit()]
        числа = re.findall(r'\d+', normalized)
        кандидати = []
        for cat_key, cat_info in catalogs.items():
            for _, row in cat_info["df"].iterrows():
                назва = str(row['Наименование']).lower()
                score = sum(1 for с in слова if с in назва)
                score += sum(2 for n in числа if n in назва)
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
        err_text = "😕 Не вдалося розпізнати жодної позиції."
        if errors:
            err_text += "\n\n" + "\n".join(errors)
        bot.edit_message_text(err_text, chat_id=chat_id, message_id=msg_id)
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
