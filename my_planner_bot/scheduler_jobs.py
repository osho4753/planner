from datetime import datetime, timedelta
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import bot, scheduler
import database as db

async def send_reminder(chat_id: int, task_id: int, task_text: str):
    # --- ПРОВЕРКА 1: А нужно ли вообще напоминать? ---
    if await db.is_task_completed(task_id):
        return  # Если задача уже закрыта, просто отменяем отправку

    await bot.send_message(chat_id, f"🔔 Напоминаю: **{task_text}**!")
    
    task_time_str = await db.get_task_time(task_id)
    
    if task_time_str:
        task_time = datetime.strptime(task_time_str, "%Y-%m-%d %H:%M:%S")
        follow_up_time = task_time + timedelta(minutes=5)
        
        if follow_up_time < datetime.now():
            follow_up_time = datetime.now() + timedelta(minutes=1)
            
        check_job_id = f"check_{task_id}_{datetime.now().timestamp()}"
        scheduler.add_job(ask_completion, 'date', run_date=follow_up_time, args=[chat_id, task_id, task_text], id=check_job_id)

async def ask_completion(chat_id: int, task_id: int, task_text: str):
    # --- ПРОВЕРКА 2: Вдруг юзер выполнил задачу за эти 5 минут? ---
    if await db.is_task_completed(task_id):
        return  # Если успел закрыть, не спамим кнопками

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, выполнил", callback_data=f"done_yes_{task_id}"),
         InlineKeyboardButton(text="❌ Нет еще", callback_data=f"done_no_{task_id}")]
    ])
    await bot.send_message(chat_id, f"Ты выполнил: '{task_text}'?", reply_markup=keyboard)

async def morning_checkin():
    users = await db.get_users()
    for (cid,) in users:
        await bot.send_message(cid, "☀️ Доброе утро! Какие планы на сегодня?")

async def evening_checkin():
    users = await db.get_users()
    for (cid,) in users:
        await bot.send_message(cid, "✨ День подходит к концу. Есть планы на завтра?")