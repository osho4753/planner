import os
import tempfile
import random
from datetime import datetime, timedelta

from aiogram import F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import bot, dp, client, STATUS_PHRASES
import database as db
import ai_logic

def get_main_menu():
    buttons = [
        [KeyboardButton(text="📅 Планы на сегодня"), KeyboardButton(text="🌅 На завтра")],
        [KeyboardButton(text="✅ Что сделано?"), KeyboardButton(text="❓ Что осталось?")],
        [KeyboardButton(text="⏰ Часовой пояс (/tz)")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

@dp.message(F.text == "/start")
async def cmd_start(m: Message):
    await db.add_user(m.chat.id)
    await m.answer(
        "👋 Привет! Я твой умный менеджер. \n\nЧтобы я будил тебя и присылал напоминания вовремя, **напиши, в каком городе ты находишься?** 🌍", 
        reply_markup=get_main_menu()
    )

# --- НОВЫЙ БЛОК: ИНТЕРАКТИВНЫЕ ЧЕКБОКСЫ ---
@dp.message(F.text.in_(["📅 Планы на сегодня", "🌅 На завтра"]))
async def show_interactive_plans(m: Message):
    # Определяем, какую дату просит пользователь
    if m.text == "📅 Планы на сегодня":
        target_date = datetime.now().strftime("%Y-%m-%d")
        header = "Твои планы на сегодня (по нажатию отмечаешь сделанным):"
    else:
        target_date = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        header = "Твои планы на завтра (по нажатию отмечаешь сделанным):"
        
    # Достаем список задач из базы
    tasks = await db.get_raw_tasks(m.chat.id, target_date)
    
    if not tasks:
        date_str = datetime.strptime(target_date, "%Y-%m-%d").strftime("%d.%m")
        await m.answer(f"На {date_str} планов пока нет! ☕")
        return
        
    # Строим клавиатуру из задач
    builder = InlineKeyboardBuilder()
    for t_id, text, is_completed, t_time in tasks:
        icon = "✅" if is_completed else "⬜️"
        time_short = t_time.split()[1][:5] if t_time else "Весь день"
        
        builder.row(InlineKeyboardButton(
            text=f"{icon} {time_short} | {text}", 
            callback_data=f"check_{t_id}_{target_date}" # передаем дату, чтобы знать, что перерисовывать
        ))
        
    await m.answer(header, reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("check_"))
async def toggle_checkbox(call: CallbackQuery):
    parts = call.data.split("_")
    task_id = int(parts[1])
    target_date = parts[2]
    
    # 1. Меняем статус в базе (0 на 1, или 1 на 0)
    await db.toggle_task_status(task_id)
    
    # 2. Получаем обновленный список задач
    tasks = await db.get_raw_tasks(call.message.chat.id, target_date)
    
    # 3. Перерисовываем клавиатуру с новыми галочками
    builder = InlineKeyboardBuilder()
    for t_id, text, is_completed, t_time in tasks:
        icon = "✅" if is_completed else "⬜️"
        time_short = t_time.split()[1][:5] if t_time else "Весь день"
        builder.row(InlineKeyboardButton(
            text=f"{icon} {time_short} | {text}", 
            callback_data=f"check_{t_id}_{target_date}"
        ))
        
    # Мгновенно обновляем сообщение
    await call.message.edit_reply_markup(reply_markup=builder.as_markup())
    await call.answer()
# ------------------------------------------

@dp.message(F.text)
async def handle_text(m: Message):
    txt = m.text
    # Обрати внимание: мы убрали отсюда "На сегодня" и "На завтра", 
    # так как их теперь ловит функция show_interactive_plans выше
    if txt == "✅ Что сделано?": txt = "Покажи выполненные задачи на сегодня"
    elif txt == "❓ Что осталось?": txt = "Покажи, что мне осталось сделать сегодня"

    status = await m.reply(f"⏳ {random.choice(STATUS_PHRASES)}")
    try:
        ans = await ai_logic.process_logic(m.chat.id, txt)
        await status.edit_text(ans)
    except Exception as e: 
        await status.edit_text(f"Заминка: {e}")

@dp.message(F.voice)
async def handle_voice(m: Message):
    status = await m.reply(f"⏳ {random.choice(STATUS_PHRASES)}")
    tmp_path = ""
    try:
        file_info = await bot.get_file(m.voice.file_id)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp: tmp_path = tmp.name
        await bot.download_file(file_info.file_path, tmp_path)
        
        with open(tmp_path, "rb") as f:
            trans = await client.audio.transcriptions.create(file=("audio.ogg", f.read()), model="whisper-large-v3")
        
        ans = await ai_logic.process_logic(m.chat.id, trans.text)
        await status.edit_text(ans)
    except Exception as e: 
        await status.edit_text(f"Ошибка: {e}")
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

@dp.callback_query(F.data.startswith("done_"))
async def handle_completion_buttons(call: CallbackQuery):
    parts = call.data.split("_")
    action = parts[1]
    task_id = int(parts[2])

    if action == "yes":
        await db.complete_task_in_db(call.message.chat.id, task_id)
        await call.message.edit_text("Отлично! Отметил задачу как выполненную ✅")
    else:
        await call.message.edit_text("Понял, задача пока висит в планах ⏳")
    await call.answer()
