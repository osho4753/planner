from datetime import datetime, timedelta
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import bot, scheduler
import database as db

async def send_reminder(chat_id: int, task_id: int, task_text: str):
    await bot.send_message(chat_id, f"🔔 Напоминаю: **{task_text}**!")
    
    # Заводим таймер проверки на 5 минут вперед
    follow_up_time = datetime.now() + timedelta(minutes=5)
    check_job_id = f"check_{task_id}_{datetime.now().timestamp()}"
    scheduler.add_job(ask_completion, 'date', run_date=follow_up_time, args=[chat_id, task_id, task_text], id=check_job_id)

async def ask_completion(chat_id: int, task_id: int, task_text: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, выполнил", callback_data=f"done_yes_{task_id}"),
         InlineKeyboardButton(text="❌ Нет еще", callback_data=f"done_no_{task_id}")]
    ])
    await bot.send_message(chat_id, f"Прошло 5 минут. Ты выполнил задачу: '{task_text}'?", reply_markup=keyboard)

async def morning_checkin():
    users = await db.get_users()
    for (cid,) in users:
        await bot.send_message(cid, "☀️ Доброе утро! Какие планы на сегодня?")

async def evening_checkin():
    users = await db.get_users()
    for (cid,) in users:
        await bot.send_message(cid, "✨ День подходит к концу. Есть планы на завтра?")