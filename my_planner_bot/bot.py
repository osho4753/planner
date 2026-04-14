import asyncio
from datetime import datetime
from config import bot, dp, scheduler
import database as db
from scheduler_jobs import morning_checkin, evening_checkin, send_reminder
import handlers 

async def restore_jobs():
    print("🔄 Восстановление напоминаний из базы...")
    tasks = await db.get_active_reminders()
    restored_count = 0
    
    for task_id, chat_id, task_text, remind_time, job_id in tasks:
        try:
            # Парсим время из строки
            run_date = datetime.strptime(remind_time, "%Y-%m-%d %H:%M:%S")
            
            # Восстанавливаем таймер ТОЛЬКО если время напоминания еще не прошло
            if run_date > datetime.now():
                scheduler.add_job(
                    send_reminder, 
                    'date', 
                    run_date=run_date, 
                    args=[chat_id, task_id, task_text], 
                    id=job_id,
                    replace_existing=True # На всякий случай, если джоба с таким ID уже есть
                )
                restored_count += 1
        except Exception as e:
            print(f"⚠️ Ошибка восстановления задачи {task_id}: {e}")
            
    print(f"✅ Восстановлено таймеров: {restored_count}")

async def main():
    print("🚀 Инициализация базы данных...")
    await db.init_db()
    
    # ВОТ ЗДЕСЬ ВЫЗЫВАЕМ НАШУ НОВУЮ ФУНКЦИЮ:
    await restore_jobs()
    
    print("🚀 Настройка расписания...")
    scheduler.add_job(morning_checkin, 'cron', hour=9, minute=0)
    scheduler.add_job(evening_checkin, 'cron', hour=21, minute=0)
    scheduler.start()
    
    print("🚀 Менеджер запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())