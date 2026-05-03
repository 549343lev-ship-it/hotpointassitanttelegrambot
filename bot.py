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

def локальний_пошук(запит, топ=15): # Зменшено до 15 для економії токенів і уникнення помилки 429
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
        model="claude-3-5-sonnet-latest", # Оновлена правильна модель
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
    # Жорстко задаємо колонки на випадок, якщо результати порожні
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

@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    status_msg = bot.reply_to(message, "⏳ Отримав фото. Завантажую...")
    
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded = bot.download_file(file_info.file_path)
        image_b64 = base64.b64encode(downloaded).decode('utf-8')
        
        # Беремо текст з підпису до фото
        user_caption = message.caption if message.caption else ""
        
        bot.edit_message_text("⏳ Розпізнаю текст (це може зайняти трохи часу)...", 
                              chat_id=message.chat.id, message_id=status_msg.message_id)
        
        # Жорсткий промпт, щоб бот не галюцинував електрикою
        prompt_text = f"""Ти експерт із сантехнічних систем. Уважно прочитай рукописний текст на фото. 
Це список матеріалів для водопостачання, опалення, каналізації тощо. 
Користувач також залишив таку підказку щодо брендів/категорій: '{user_caption}'.
Не вигадуй жодних товарів (особливо електрику!), пиши ТІЛЬКИ те, що бачиш. 
Виведи кожен товар з кількістю у форматі: назва - кількість"""

        ocr = client.messages.create(
            model="claude-3-5-sonnet-latest",
            max_tokens=1024,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                {"type": "text", "text": prompt_text}
            ]}]
        )
        текст = ocr.content[0].text
        
        bot.edit_message_text(f"📋 Розпізнано:\n{текст}\n\n⏳ Зіставляю з базою товарів...", 
                              chat_id=message.chat.id, message_id=status_msg.message_id)
        
        кандидати = локальний_пошук(текст, топ=15)
        результати = знайти_товари(текст, кандидати)
        
        if not результати:
            bot.edit_message_text("🤷‍♂️ Розпізнав текст, але не знайшов жодного збігу в базі Excel.", 
                                  chat_id=message.chat.id, message_id=status_msg.message_id)
            return

        excel = створити_excel(результати)
        bot.send_document(message.chat.id, excel, visible_file_name="замовлення.xlsx")
        
        bot.edit_message_text("✅ Готово! Файл успішно сформовано.", 
                              chat_id=message.chat.id, message_id=status_msg.message_id)
                              
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "rate_limit" in error_msg:
             bot.edit_message_text("❌ Забагато запитів. Зачекайте хвилинку і спробуйте ще раз.", 
                                   chat_id=message.chat.id, message_id=status_msg.message_id)
        else:
             bot.edit_message_text(f"❌ Ой, сталась помилка, все-таки я це не зроблю.\nПричина: {error_msg}", 
                                   chat_id=message.chat.id, message_id=status_msg.message_id)

# Реагує ТІЛЬКИ якщо текст починається зі слова "пошук" або "Пошук"
@bot.message_handler(func=lambda m: m.text and m.text.lower().startswith('пошук'))
def handle_text(message):
    status_msg = bot.reply_to(message, "⏳ Починаю пошук...")
    
    # Відрізаємо слово "Пошук" з початку тексту (перші 6 символів зі зміщенням)
    запит = message.text[5:].strip()
    
    try:
        кандидати = локальний_пошук(запит, топ=15)
        
        bot.edit_message_text("⏳ Аналізую базу...", chat_id=message.chat.id, message_id=status_msg.message_id)
        
        результати = знайти_товари(запит, кандидати)
        
        if not результати:
            bot.edit_message_text("🤷‍♂️ За цим запитом не знайдено товарів у базі.", 
                                  chat_id=message.chat.id, message_id=status_msg.message_id)
            return
            
        bot.edit_message_text("⏳ Формую Excel файл...", chat_id=message.chat.id, message_id=status_msg.message_id)
        
        excel = створити_excel(результати)
        bot.send_document(message.chat.id, excel, visible_file_name="замовлення.xlsx")
        
        bot.edit_message_text("✅ Готово! Файл сформовано.", chat_id=message.chat.id, message_id=status_msg.message_id)
        
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "rate_limit" in error_msg:
             bot.edit_message_text("❌ Забагато запитів. Зачекайте хвилинку і спробуйте ще раз.", 
                                   chat_id=message.chat.id, message_id=status_msg.message_id)
        else:
             bot.edit_message_text(f"❌ Ой, сталась помилка, все-таки я це не зроблю.\nПричина: {error_msg}", 
                                   chat_id=message.chat.id, message_id=status_msg.message_id)

bot.polling(none_stop=True)
