Content is user-generated and unverified.
import telebot
import anthropic
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

# ─── ЗАВАНТАЖЕННЯ КАТАЛОГУ ───────────────────────────────────────────────────
df = pd.read_excel("products.xlsx", header=0)
df.columns = ['Наименование', 'Артикул WMS', 'ОР', 'Резерв', 'Код'] + list(df.columns[5:])
df_товари = df[['Наименование', 'Артикул WMS', 'Код']].dropna(subset=['Наименование'])
df_товари = df_товари[df_товари['Наименование'].astype(str).str.strip() != '']
df_товари = df_товари.reset_index(drop=True)

# Будуємо рядок каталогу один раз при старті (для Claude)
КАТАЛОГ_РЯДКИ = "\n".join(
    f"{r['Наименование']} | WMS: {r['Артикул WMS']} | Код: {r['Код']}"
    for _, r in df_товари.iterrows()
)

# ─── ПРАВИЛА ─────────────────────────────────────────────────────────────────
user_batches = {}
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
# Приймає фото або текст, повертає список нормалізованих позицій
# Кожна позиція: {"normalized": "Труба ПВХ каналізаційна 50мм 1м", "qty": "2", "original": "труба 50 - 2шт"}

def нормалізувати_фото(image_b64, caption=""):
    rules = get_rules()
    rules_block = f"\nДодаткові правила від користувача:\n{rules}" if rules else ""

    prompt = f"""Ти — експерт із сантехніки. На фото рукописний список замовлення від майстра.

ЗАВДАННЯ:
1. Прочитай кожен рядок (ігноруй електрику, тільки сантехніка/опалення/водопостачання)
2. Нормалізуй кожну позицію до стандартної торгової назви
3. Витягни кількість якщо є{rules_block}

Підказка: '{caption}'

ПРАВИЛА НОРМАЛІЗАЦІЇ:
- "кол" → "Коліно каналізаційне"
- "тр 50" → "Труба каналізаційна ПВХ 50мм"
- "муф" → "Муфта"
- скорочення розшифровуй за контекстом списку
- якщо зверху написано "каналізація" — всі позиції каналізаційні

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом, без пояснень:
[
  {{"original": "що написано на фото", "normalized": "Стандартна торгова назва", "qty": "кількість або пусто"}}
]"""

    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": prompt}
        ]}]
    )
    raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
    return json.loads(raw)


def нормалізувати_текст(текст):
    rules = get_rules()
    rules_block = f"\nДодаткові правила:\n{rules}" if rules else ""

    prompt = f"""Ти — експерт із сантехніки. Майстер надіслав список текстом:{rules_block}

ТЕКСТ:
{текст}

ЗАВДАННЯ: нормалізуй кожну позицію до стандартної торгової назви і витягни кількість.

ВІДПОВІДАЙ ТІЛЬКИ JSON масивом:
[
  {{"original": "оригінальний рядок", "normalized": "Стандартна торгова назва", "qty": "кількість або пусто"}}
]"""

    resp = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
    return json.loads(raw)


# ─── КРОК 2: ПОШУК У КАТАЛОЗІ ────────────────────────────────────────────────
# Приймає список нормалізованих позицій, повертає знайдені товари з каталогу

def знайти_у_каталозі(позиції):
    """
    Передаємо Claude нормалізовані назви + ВЕСЬ каталог (або топ кандидатів).
    Claude обирає найкращий збіг для кожної позиції.
    """
    # Для великих каталогів (15к+) — спочатку фільтруємо кандидатів локально
    # щоб не перевищити контекстне вікно
    всі_результати = []

    for поз in позиції:
        normalized = поз.get("normalized", "")
        qty = поз.get("qty", "")
        original = поз.get("original", "")

        # Локальна фільтрація — беремо топ-30 кандидатів по ключових словах
        слова = [s.lower() for s in normalized.split() if len(s) > 2]
        кандидати = []
        for _, row in df_товари.iterrows():
            назва = str(row['Наименование']).lower()
            score = sum(1 for с in слова if с in назва)
            if score > 0:
                кандидати.append((score, row))
        кандидати.sort(key=lambda x: -x[0])
        топ = кандидати[:30]

        if not топ:
            всі_результати.append({
                "original": original,
                "normalized": normalized,
                "знайдено": False,
                "назва": "",
                "артикул": "",
                "код": "",
                "кількість": qty
            })
            continue

        кандидати_текст = "\n".join(
            f"{r['Наименование']} | WMS: {r['Артикул WMS']} | Код: {r['Код']}"
            for _, r in топ
        )

        prompt = f"""З цього списку товарів обери ОДИН найкращий збіг для запиту.

ЗАПИТ: {normalized}

СПИСОК:
{кандидати_текст}

Якщо є збіг — відповідай JSON:
{{"знайдено": true, "назва": "точна назва з списку", "артикул": "артикул", "код": "код"}}

Якщо нічого підходящого немає:
{{"знайдено": false}}

ТІЛЬКИ JSON, без пояснень."""

        resp = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip().replace('```json','').replace('```','').strip()
        результат = json.loads(raw)
        результат["original"] = original
        результат["normalized"] = normalized
        результат["кількість"] = qty
        всі_результати.append(результат)

    return всі_результати


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
                'Оригінал (від майстра)': r.get('original', ''),
            })
        else:
            not_found.append(r.get('normalized') or r.get('original', ''))

    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_out = pd.DataFrame(rows) if rows else pd.DataFrame(
            columns=['Наименование','Артикул WMS','Код','Кількість','Оригінал (від майстра)']
        )
        df_out.to_excel(writer, index=False, sheet_name='Замовлення')

        # Другий лист — не знайдені
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
    status_msg = bot.send_message(chat_id, f"🔄 Починаю обробку {len(items)} файл(ів)...")
    msg_id = status_msg.message_id

    всі_позиції = []  # нормалізовані позиції з усіх файлів
    errors = []

    # ── КРОК 1: нормалізація всіх файлів ──
    for index, item in enumerate(items, 1):
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

    # Показуємо що розпізнали — корисно для дебагу
    preview = "\n".join(
        f"• {п['original']} → {п['normalized']}" + (f" ({п['qty']})" if п.get('qty') else "")
        for п in всі_позиції[:10]
    )
    if len(всі_позиції) > 10:
        preview += f"\n... та ще {len(всі_позиції)-10} позицій"

    bot.send_message(chat_id, f"✅ Розпізнано {len(всі_позиції)} позицій:\n\n{preview}\n\n🔍 Шукаю в базі...")

    # ── КРОК 2: пошук у каталозі ──
    bot.edit_message_text(
        f"🔍 Крок 2: Шукаю {len(всі_позиції)} позицій у базі товарів...",
        chat_id=chat_id, message_id=msg_id
    )

    try:
        результати = знайти_у_каталозі(всі_позиції)
    except Exception as e:
        bot.edit_message_text(f"❌ Помилка пошуку: {e}", chat_id=chat_id, message_id=msg_id)
        return

    # ── КРОК 3: формуємо Excel ──
    bot.edit_message_text("📊 Формую Excel файл...", chat_id=chat_id, message_id=msg_id)

    excel, not_found = створити_excel(результати)
    знайдено = [r for r in результати if r.get("знайдено")]

    bot.send_document(chat_id, excel, visible_file_name="замовлення.xlsx")

    # Фінальний звіт
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
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)
        image_b64 = base64.b64encode(downloaded).decode('utf-8')
        add_to_batch(message.chat.id, {
            'type': 'photo',
            'data': image_b64,
            'caption': message.caption or ""
        })
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка завантаження фото: {e}")


@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('пошук'))
def handle_text(message):
    запит = message.text[5:].strip()
    if запит:
        add_to_batch(message.chat.id, {'type': 'text', 'text': запит})
    else:
        bot.reply_to(message, "Напиши запит після слова 'пошук'.\nНаприклад: `пошук труба 50`", parse_mode="Markdown")


bot.polling(none_stop=True)
