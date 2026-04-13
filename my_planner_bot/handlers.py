import os
import tempfile
import random
from aiogram import F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, CallbackQuery
from config import bot, dp, client, STATUS_PHRASES
import database as db
import ai_logic

def get_main_menu():
    buttons = [
        [KeyboardButton(text="📅 Планы на сегодня"), KeyboardButton(text="🌅 На завтра")],
        [KeyboardButton(text="✅ Что сделано?"), KeyboardButton(text="❓ Что осталось?")]
    ]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


@dp.message(F.text == "/start")
async def cmd_start(m: Message):
    await db.add_user(m.chat.id)
    await m.answer("👋 Привет! Я твой менеджер. Буду присылать планы утром и вечером.", reply_markup=get_main_menu())

@dp.message(F.text)
async def handle_text(m: Message):
    txt = m.text
    if txt == "📅 Мои планы на сегодня": txt = "Покажи мои планы на сегодня"
    elif txt == "✅ Что я уже сделал?": txt = "Покажи выполненные задачи на сегодня"
    elif txt == "❓ Что осталось?": txt = "Покажи, что мне осталось сделать сегодня"
    elif txt == "🌅 На завтра": 
        txt = "Покажи мои планы на завтра"

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