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
df = pd.read_excel("Список.xlsx", header=0)
df.columns = ['Наименование', 'Артикул WMS', 'ОР', 'Резерв', 'Код'] + list(df.columns[5:])
df_товари = df[['Наименование', 'Артикул WMS', 'Код']].dropna(subset=['Наименование'])
df_товари = df_товари[df_товари['Наименование'].astype(str).str.strip() != '']

товари_для_пошуку = df_товари.to_dict('records')
товари_текст = "\n".join([
    f"{r['Наименование']} | WMS: {r['Артикул WMS']} | Код: {r['Код']}"
    for r in товари_для_пошуку
])

def обробити_запит(текст_запиту):
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": f"""Ось база товарів (Назва | Артикул WMS | Код):
{товари_текст}

Ось запит:
{текст_запиту}

Знайди відповідні товари з бази. Відповідай ТІЛЬКИ у форматі JSON масиву, без жодного іншого тексту:
[
  {{"назва": "точна назва з бази", "артикул": "артикул WMS або пусто", "код": "код або пусто", "кількість": "кількість з запиту або пусто"}},
  ...
]"""
        }]
    )
    return response.content[0].text

def створити_excel(результати):
    rows = []
    for item in результати:
        rows.append({
            'Наименование': item.get('назва', ''),
            'Артикул WMS': item.get('артикул', ''),
            'Код': item.get('код', ''),
            'Кількість': item.get('кількість', '')
        })
    result_df = pd.DataFrame(rows)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        result_df.to_excel(writer, index=False, sheet_name='Замовлення')
    output.seek(0)
    return output

def обробити_і_відповісти(message, текст_запиту):
    try:
        bot.reply_to(message, "⏳ Шукаю товари у базі...")
        raw = обробити_запит(текст_запиту)
        clean = raw.strip().replace('```json', '').replace('```', '').strip()
        результати = json.loads(clean)
        excel = створити_excel(результати)
        bot.send_document(message.chat.id, excel, visible_file_name="замовлення.xlsx")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка: {str(e)}")

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded = bot.download_file(file_info.file_path)
    image_b64 = base64.b64encode(downloaded).decode('utf-8')
    bot.reply_to(message, "⏳ Розпізнаю фото і шукаю товари...")
    
    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": f"""Ось база товарів (Назва | Артикул WMS | Код):
{товари_текст}

Розпізнай список матеріалів на фото. Знайди відповідні товари з бази. Відповідай ТІЛЬКИ у форматі JSON масиву, без жодного іншого тексту:
[
  {{"назва": "точна назва з бази", "артикул": "артикул WMS або пусто", "код": "код або пусто", "кількість": "кількість з фото або пусто"}},
  ...
]"""}
            ]
        }]
    )
    try:
        raw = response.content[0].text
        clean = raw.strip().replace('```json', '').replace('```', '').strip()
        результати = json.loads(clean)
        excel = створити_excel(результати)
        bot.send_document(message.chat.id, excel, visible_file_name="замовлення.xlsx")
    except Exception as e:
        bot.reply_to(message, f"❌ Помилка: {str(e)}\n\nВідповідь моделі: {response.content[0].text[:500]}")

@bot.message_handler(func=lambda m: True)
def handle_text(message):
    обробити_і_відповісти(message, message.text)

bot.polling()
