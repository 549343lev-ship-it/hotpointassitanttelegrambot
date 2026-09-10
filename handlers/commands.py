"""handlers/commands.py — /start, /help, /stop та кнопки головного меню."""
from config.settings import ADMIN_ID
from keyboards.reply import main_keyboard


def register(bot, state: dict):
    from engine.brand_selector import cancel as bs_cancel

    @bot.message_handler(commands=['start', 'help'])
    def handle_start(message):
        admin      = message.from_user.id == ADMIN_ID
        admin_note = (
            "\n\n👑 *Адмін:* твої виправлення застосовуються одразу."
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
Тапни «📚 Навчання» → номери рядків → причину → правильний варіант.{admin_note}

`пошук <текст>` — підбір без фото | /stop — зупинити""",
            parse_mode="Markdown",
            reply_markup=main_keyboard(message.from_user.id))

    @bot.message_handler(func=lambda m: m.text and m.text.lower().strip() in ('стоп', '🛑 стоп'))
    def kb_stop(message):
        _do_stop(message.chat.id, bot, state)
        bot.reply_to(message, "🛑 Зупинено. Всі активні сесії скасовано.")

    @bot.message_handler(commands=['stop'])
    def handle_stop(message):
        _do_stop(message.chat.id, bot, state)
        bot.reply_to(message, "🛑 Зупинено. Всі активні сесії скасовано.")


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
