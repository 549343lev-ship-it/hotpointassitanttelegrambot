import telebot
import anthropic
import os
import pandas as pd
import base64
import json
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

def локальний_пошук(запит, топ=50):
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
    if попередній_пошук:
        кандидати = попередній_пошук
    else:
        кандидати = локальний_пошук(текст_запиту)
    
    if not кандидати:
        return []
    
    кандидати_текст = "\n".join([
        f"{r['Наименование']} | WMS: {r['Артикул WMS']} | Код: {r['Код']}"
        for r in кандидати
    ])
    
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=2048,
        messages=[{
            "role": "user",
            "content": f"""Ось список товарів з бази:
{кандидати_текст}

Запит:
{текст_запиту}

Знайди відповідні товари. Відповідай ТІЛЬКИ JSON масивом без іншого тексту:
[{{"назва": "точна назва з бази", "артикул": "артикул або пусто", "код": "код або пусто", "кількість": "кількість або пусто"}}]"""
        }]
    )
    raw = response.content[0].text.strip().replace('```json','').replace('```','').strip()
    return json.loads(raw)

def створити_excel(результати):
    rows = [{'Наименование': i.get('назва',''), 'Артикул WMS': i.get('артикул',''), 'Код': i.get('код',''), 'Кількість': i.get('кількість','')} for i in результати]
    result_df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        result_df.to_excel(writer, index=False)
    output.seek(0)
    return output

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    bot.reply_to(message, "⏳ Розпізнаю фото...")
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded = bot.download_file(file_info.file_path)
    image_b64 = base64.b64encode(downloaded).decode('utf-8')
    
    # Спочатку розпізнаємо текст
    ocr = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
            {"type": "text", "text": "Розпізнай список матеріалів на фото. Виведи кожен товар з кількістю у форматі: назва - кількість"}
        ]}]
    )
    текст = ocr.content[0].text
    bot.reply_to(message, f"📋 Розпізнано:\n{текст}\n\n⏳ Шукаю у базі...")
    
    # Потім шукаємо у базі
    кандидати = локальний_пошук(текст)
    try:
        результати = знайти_товари(текст, кандидати)
        excel = створити_excel(результати)
        bot.send_document(message.chat.id, excel, visible_file_name="замовлення.xlsx")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка пошуку: {str(e)}")

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    bot.reply_to(message, "⏳ Шукаю у базі...")
    кандидати = локальний_пошук(message.text)
    try:
        результати = знайти_товари(message.text, кандидати)
        excel = створити_excel(результати)
        bot.send_document(message.chat.id, excel, visible_file_name="замовлення.xlsx")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка: {str(e)}")

bot.polling()
