import asyncio
from config import bot, dp, scheduler
import database as db
from scheduler_jobs import morning_checkin, evening_checkin
import handlers # Импортируем хэндлеры, чтобы они зарегистрировались

async def main():
    print("🚀 Инициализация базы данных...")
    await db.init_db()
    
    print("🚀 Настройка расписания...")
    scheduler.add_job(morning_checkin, 'cron', hour=9, minute=0)
    scheduler.add_job(evening_checkin, 'cron', hour=21, minute=0)
    scheduler.start()
    
    print("🚀 Менеджер запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())