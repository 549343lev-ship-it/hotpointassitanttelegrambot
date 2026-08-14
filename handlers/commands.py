"""handlers/commands.py — /start, /help, /stop та кнопки головного меню."""
from config.settings import ADMIN_ID
from keyboards.reply import main_keyboard


def register(bot, state: dict):
    """
    Реєструє хендлери команд.
    state — спільний словник стану з bot.py (stop_flags, user_batches тощо).
    """
    from engine.brand_selector import cancel as bs_cancel

    @bot.message_handler(commands=['start', 'help'])
    def handle_start(message):
        admin      = message.from_user.id == ADMIN_ID
        admin_note = (
            "\n\n👑 *Адмін:* твої виправлення застосовуються одразу. "
            "Чужі — чекають у «👑 Правила на розгляд»."
            if admin else
            "\n\n_Твої виправлення і правила підтверджує адмін._"
        )
        bot.reply_to(message, f"""👋 Привіт! Я підбираю сантехніку з бази по фото списку.

*📋 ЯК ПРАЦЮВАТИ (3 кроки):*
1️⃣ Напиши виробників (кожен рядок = категорія):
`каналізація остендорф`
`пайка екопластик`
`крани рафтек`
2️⃣ Кинь фото рукописного списку (можна кілька)
3️⃣ Отримай Excel: 🟥 не знайдено, 🟨 перевір

*👤 ПОСТІЙНІ КЛІЄНТИ:*
`новий клієнт Петренко` — створити профіль
`клієнт Петренко` — активуй ПЕРЕД фото
`клієнт стоп` — вимкнути | `клієнти` — список

*🎓 ЯКЩО БОТ ПОМИЛИВСЯ:*
Тапни «🎓 Навчання» → номери рядків → причину → правильний варіант.{admin_note}

`пошук <текст>` — підбір без фото | /stop — зупинити""",
            parse_mode="Markdown",
            reply_markup=main_keyboard(message.from_user.id))

    @bot.message_handler(func=lambda m: m.text and 'як користуватись' in m.text.lower())
    def kb_howto(message):
        bot.reply_to(message, """📸 *ПОВНА ІНСТРУКЦІЯ*

*Крок 1. Клієнт (якщо постійний):*
`клієнт Петренко` — бот згадає всі його минулі замовлення.
Новий? → `новий клієнт Петренко`

*Крок 2. Виробники:*
`каналізація остендорф`
`пайка екопластик`
Або: `усе рафтек`

*Крок 3. Фото:*
Кинь фото (можна кілька — почекай 4 сек).

*Крок 4. Перевір Excel:*
🟥 червоний = не знайдено
🟨 жовтий = перевір
Колонка «Джерело» показує звідки вибір.

*Крок 5. Навчи якщо є помилки:*
Тапни «🎓 Навчання» → номери рядків → причину → правильний товар.""",
            parse_mode="Markdown")

    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ('стоп', '🛑 стоп'))
    def kb_stop(message):
        _do_stop(message.chat.id, bot, state)
        bot.reply_to(message, "🛑 Зупинено. Всі активні сесії скасовано.")

    @bot.message_handler(commands=['stop'])
    def handle_stop(message):
        _do_stop(message.chat.id, bot, state)
        bot.reply_to(message, "🛑 Зупинено. Всі активні сесії скасовано.")

    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ('правило', '📋 правило'))
    def kb_rule_btn(message):
        bot.reply_to(message,
            "Напиши: `правило <текст>`\nПриклад: `правило рожон = трійник`",
            parse_mode="Markdown")


def _do_stop(chat_id: int, bot, state: dict):
    """Зупиняє всі активні сесії для чату."""
    from engine.brand_selector import cancel as bs_cancel

    state['stop_flags'][chat_id] = True

    if chat_id in state['user_batches']:
        t = state['user_batches'][chat_id].get('timer')
        if t:
            t.cancel()
        state['user_batches'].pop(chat_id, None)

    state.get('_order_setup', {}).pop(chat_id, None)
    state.get('_pre_batch',   {}).pop(chat_id, None)
    state.get('_learn_state', {}).pop(chat_id, None)
    state.get('_manual_wait', {}).pop(chat_id, None)
    state.get('pending_hints',{}).pop(chat_id, None)
    bs_cancel(chat_id)
