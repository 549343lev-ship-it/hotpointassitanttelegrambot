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

# Завантаження списку товарів
df = pd.read_excel("products.xlsx", header=0)
df.columns = ['Наименование', 'Артикул WMS', 'ОР', 'Резерв', 'Код'] + list(df.columns[5:])
df_товари = df[['Наименование', 'Артикул WMS', 'Код']].dropna(subset=['Наименование'])
df_товари = df_товари[df_товари['Наименование'].astype(str).str.strip() != '']
df_товари = df_товари.reset_index(drop=True)

# Словник для збору повідомлень (Альбомів)
user_batches = {}

# Файл, де бот буде зберігати свої знання
RULES_FILE = "rules.txt"

# Функція для отримання поточних правил з файлу
def get_rules():
    if not os.path.exists(RULES_FILE):
        return "Спеціальних правил поки немає."
    with open(RULES_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()

# Функція для збереження нового правила
def add_rule(new_rule):
    with open(RULES_FILE, "a", encoding="utf-8") as f:
        f.write(f"- {new_rule}\n")

def локальний_пошук(запит, топ=15):
    слова = [s.lower() for s in запит.split() if len(s) > 2]
    результати = []
    for _, row in df_товари.iterrows():
        назва = str(row['Наименование']).lower()
        score = sum(1 for слово in слова if слово in назва)
        if score > 0:
            результати.append((score, row))
    результати.sort(key=lambda x: -x[0])
    return [r[1] for r in результати[:топ]]

def знайти_товари(текст_запиту, попередній_пошук=None):
    кандидати = попередній_пошук if попередній_пошук else локальний_пошук(текст_запиту)
    if not кандидати: return []
    
    кандидати_текст = "\n".join([f"{r['Наименование']} | WMS: {r['Артикул WMS']} | Код: {r['Код']}" for r in кандидати])
    
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{"role": "user", "content": f"""Ось список товарів з бази:\n{кандидати_текст}\n\nЗапит:\n{текст_запиту}\nЗнайди відповідні товари. Відповідай ТІЛЬКИ JSON масивом: [{{"назва": "точна назва", "артикул": "артикул", "код": "код", "кількість": "кількість"}}]"""}]
    )
    raw = response.content[0].text.strip().replace('```json','').replace('```','').strip()
    return json.loads(raw)

def створити_excel(результати):
    columns = ['Наименование', 'Артикул WMS', 'Код', 'Кількість']
    if not результати:
        result_df = pd.DataFrame(columns=columns)
    else:
        rows = [{'Наименование': i.get('назва',''), 'Артикул WMS': i.get('артикул',''), 'Код': i.get('код',''), 'Кількість': i.get('кількість','')} for i in результати]
        result_df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        result_df.to_excel(writer, index=False)
    output.seek(0)
    return output

def process_batch(chat_id):
    batch = user_batches.pop(chat_id, None)
    if not batch: return
    
    items = batch['items']
    bot.send_message(chat_id, f"🔄 Починаю обробку {len(items)} файлів/запитів. Це займе певний час...")
    
    all_results = []
    errors = []
    
    # Завантажуємо правила перед розпізнаванням
    rules_text = get_rules()
    
    for index, item in enumerate(items, 1):
        try:
            if item['type'] == 'photo':
                # Тепер бот бере правила не з коду, а з файлу
                prompt_text = f"""Ти експерт із сантехніки. Прочитай рукописний текст.
                ВРАХУЙ ЦІ ПРАВИЛА ВІД КОРИСТУВАЧА ДЛЯ РОЗПІЗНАВАННЯ (СЛЕНГ/АБРЕВІАТУРИ):
                {rules_text}
                
                Ігноруй електрику! Тільки сантехніка, водопостачання, опалення.
                Підказка від користувача (якщо є): '{item['caption']}'.
                Виведи кожен знайдений товар у форматі: назва - кількість. Нічого зайвого."""

                ocr = client.messages.create(
                    model="claude-sonnet-4-5",
                    max_tokens=1024,
                    messages=[{"role": "user", "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": item['data']}},
                        {"type": "text", "text": prompt_text}
                    ]}]
                )
                текст = ocr.content[0].text
                кандидати = локальний_пошук(текст, топ=15)
                результати = знайти_товари(текст, кандидати)
                
                if результати:
                    all_results.extend(результати)
                else:
                    errors.append(f"⚠️ Фото {index}: розпізнано ({текст[:30]}...), але не знайдено в базі.")
            
            elif item['type'] == 'text':
                запит = item['text']
                кандидати = локальний_пошук(запит, топ=15)
                результати = знайти_товари(запит, кандидати)
                if результати:
                    all_results.extend(результати)
                else:
                    errors.append(f"⚠️ Текст '{запит[:20]}...': не знайдено в базі.")
                    
        except Exception as e:
            errors.append(f"❌ Помилка на елементі {index}: {str(e)}")

    if all_results:
        excel = створити_excel(all_results)
        bot.send_document(chat_id, excel, visible_file_name="загальне_замовлення.xlsx")
        
        status_msg = f"✅ Успішно зібрано товарів: {len(all_results)} шт."
        if errors:
            status_msg += "\n\nДеякі проблеми під час обробки:\n" + "\n".join(errors)
        bot.send_message(chat_id, status_msg)
    else:
        bot.send_message(chat_id, "🤷‍♂️ Жодного товару не вдалося знайти в базі.\n\nДеталі:\n" + "\n".join(errors))

def add_to_batch(chat_id, item):
    if chat_id in user_batches:
        user_batches[chat_id]['timer'].cancel()
    else:
        user_batches[chat_id] = {'items': []}
        bot.send_message(chat_id, "📥 Отримав дані. Чекаю 4 секунди, чи будуть ще файли...")
        
    user_batches[chat_id]['items'].append(item)
    timer = threading.Timer(4.0, process_batch, args=[chat_id])
    user_batches[chat_id]['timer'] = timer
    timer.start()

# === ОБРОБНИКИ ПОВІДОМЛЕНЬ ===

# 1. Обробник для навчання (Нова функція!)
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('правило'))
def handle_rule(message):
    # Відрізаємо слово "Правило" або "правило" (перші 7 символів)
    new_rule = message.text[7:].strip()
    
    if new_rule:
        add_rule(new_rule)
        bot.reply_to(message, f"✅ Зрозумів і записав у базу знань:\n{new_rule}\n\nТепер я буду враховувати це при кожному наступному фото.")
    else:
        bot.reply_to(message, "Напиши правило після слова 'Правило'.\nНаприклад:\n`Правило якщо бачиш 'кол' — це коліно каналізаційне`", parse_mode="Markdown")

# 2. Обробник фото
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)
        image_b64 = base64.b64encode(downloaded).decode('utf-8')
        
        item = {
            'type': 'photo',
            'data': image_b64,
            'caption': message.caption if message.caption else ""
        }
        add_to_batch(message.chat.id, item)
    except Exception as e:
        bot.reply_to(message, f"Помилка завантаження фото: {e}")

# 3. Обробник пошуку по тексту
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('пошук'))
def handle_text(message):
    запит = message.text[5:].strip()
    item = {
        'type': 'text',
        'text': запит
    }
    add_to_batch(message.chat.id, item)

bot.polling(none_stop=True)
