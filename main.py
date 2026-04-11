import asyncio
import os
import tempfile
import aiosqlite
import random
import json
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv
from openai import AsyncOpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.base import JobLookupError

# --- НАСТРОЙКИ ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()
client = AsyncOpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

DB_NAME = "/data/planner.db" if os.path.exists("/data") else "planner.db"
STATUS_PHRASES = ["Сейчас запишу...", "Секунду...", "Проверяю...", "Записываю...", "Смотрю твои планы..."]

# --- ЗАГРУЗКА ФРАЗ ---
try:
    with open('responses.json', 'r', encoding='utf-8') as file:
        BOT_RESPONSES = json.load(file)
except:
    BOT_RESPONSES = {"not_found": ["Не нашел такую задачу..."]}

def get_random_response(action: str, task: str, time: str = ""):
    templates = BOT_RESPONSES.get(action, [f"Готово: {task}"])
    return random.choice(templates).format(task=task, time=time)

# --- МЕНЮ ---
def get_main_menu():
    buttons = [[KeyboardButton(text="📅 Мои планы на сегодня")], [KeyboardButton(text="✅ Что я уже сделал?"), KeyboardButton(text="❓ Что осталось?")]]
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- БД ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY)")
        await db.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, task_text TEXT, remind_at TEXT, job_id TEXT, is_completed INTEGER DEFAULT 0)")
        await db.commit()

# --- ЛОГИКА ПЛАНИРОВЩИКА ---
async def morning_checkin():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT chat_id FROM users")
        users = await cursor.fetchall()
    for (cid,) in users:
        await bot.send_message(cid, "☀️ Доброе утро! Какие планы на сегодня?")

async def evening_checkin():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT chat_id FROM users")
        users = await cursor.fetchall()
    for (cid,) in users:
        await bot.send_message(cid, "✨ День подходит к концу. Есть планы на завтра?")

# --- ИНСТРУМЕНТЫ (ТЕПЕРЬ РАБОТАЮТ ЧЕРЕЗ ID) ---
async def get_today_tasks(chat_id: int, status_filter: str = "all", for_ai: bool = False):
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_NAME) as db:
        # Теперь достаем и ID задачи (r[0])
        q = "SELECT id, task_text, remind_at, is_completed FROM tasks WHERE chat_id = ? AND remind_at LIKE ?"
        p = [chat_id, f"{today}%"]
        if status_filter == "completed": q += " AND is_completed = 1"
        elif status_filter == "pending": q += " AND is_completed = 0"
        cursor = await db.execute(q, p)
        rows = await cursor.fetchall()
        if not rows: return "Пока задач нет."
        
        # Если отдаем ИИ - добавляем [ID: x], если человеку - выводим красиво
        res = []
        for r in rows:
            icon = "✅" if r[3] else "⏳"
            time = r[2].split()[1][:5]
            if for_ai:
                res.append(f"[ID: {r[0]}] {icon} {r[1]} (в {time})")
            else:
                res.append(f"{icon} {r[1]} (в {time})")
                
        return "\n".join(res) if for_ai else "Вот что у нас в графике:\n\n" + "\n".join(res)

async def add_task(chat_id: int, task_text: str, remind_at: str):
    job_id = f"job_{datetime.now().timestamp()}"
    scheduler.add_job(send_reminder, 'date', run_date=datetime.strptime(remind_at, "%Y-%m-%d %H:%M:%S"), args=[chat_id, task_text], id=job_id)
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT INTO tasks (chat_id, task_text, remind_at, job_id) VALUES (?,?,?,?,0)", (chat_id, task_text, remind_at, job_id))
        await db.commit()
    return get_random_response("add_task", task_text, remind_at.split()[1][:5])

async def delete_task(chat_id: int, task_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT id, job_id, task_text FROM tasks WHERE chat_id = ? AND id = ?", (chat_id, task_id))
        row = await cursor.fetchone()
        if row:
            try: scheduler.remove_job(row[1])
            except: pass
            await db.execute("DELETE FROM tasks WHERE id = ?", (row[0],))
            await db.commit()
            return get_random_response("delete_task", row[2]) # row[2] - это реальный текст задачи
    return get_random_response("not_found", f"ID {task_id}")

async def update_task(chat_id: int, task_id: int, new_text: str = None, new_time: str = None):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT id, job_id, task_text, remind_at FROM tasks WHERE chat_id = ? AND id = ?", (chat_id, task_id))
        row = await cursor.fetchone()
        if row:
            t_id, j_id, db_text, db_time = row
            f_text = new_text if new_text else db_text
            
            # --- УМНАЯ ОБРАБОТКА ВРЕМЕНИ ---
            f_time = db_time
            if new_time:
                # Если ИИ прислал только время (например "06:30" или "06:30:00")
                if len(new_time) <= 8:
                    old_date = db_time.split()[0] # Берем дату от старой задачи
                    time_part = new_time if len(new_time) == 8 else f"{new_time}:00"
                    f_time = f"{old_date} {time_part}"
                # Если ИИ прислал дату и время, но забыл секунды ("2026-04-11 06:30")
                elif len(new_time) == 16:
                    f_time = f"{new_time}:00"
                else:
                    f_time = new_time
            
            try: scheduler.remove_job(j_id)
            except: pass
            
            # Теперь время 100% в правильном формате, пробуем поставить таймер
            try:
                run_date = datetime.strptime(f_time, "%Y-%m-%d %H:%M:%S")
                scheduler.add_job(send_reminder, 'date', run_date=run_date, args=[chat_id, f_text], id=j_id)
                await db.execute("UPDATE tasks SET task_text = ?, remind_at = ? WHERE id = ?", (f_text, f_time, t_id))
                await db.commit()
                return get_random_response("update_task", f_text, f_time.split()[1][:5])
            except ValueError as e:
                # Если ИИ прислал совсем уж дичь, не ломаем бота, а просим повторить
                return f"Ой, не понял формат времени ({new_time}). Скажи чуть точнее, пожалуйста!"
                
    return get_random_response("not_found", f"ID {task_id}")

async def complete_task(chat_id: int, task_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        # Сначала узнаем текст задачи, чтобы красиво ответить
        cursor = await db.execute("SELECT task_text FROM tasks WHERE chat_id = ? AND id = ?", (chat_id, task_id))
        row = await cursor.fetchone()
        if row:
            await db.execute("UPDATE tasks SET is_completed = 1 WHERE id = ?", (task_id,))
            await db.commit()
            return get_random_response("complete_task", row[0])
    return get_random_response("not_found", f"ID {task_id}")

async def send_reminder(chat_id: int, task_text: str):
    await bot.send_message(chat_id, f"🔔 Напоминаю: пора **{task_text.lower()}**!")

# --- СХЕМА ДЛЯ ИИ (ИЗМЕНЕНО НА TASK_ID) ---
tools = [
    {"type": "function", "function": {"name": "add_task_tool", "description": "Add task", "parameters": {"type": "object", "properties": {"task_text": {"type": "string"}, "remind_at": {"type": "string"}}, "required": ["task_text", "remind_at"]}}},
    {"type": "function", "function": {"name": "delete_task_tool", "description": "Delete a task by its ID", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "update_task_tool", "description": "Update task by its ID", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}, "new_text": {"type": "string"}, "new_time": {"type": "string", "description": "FORMAT STRICTLY: YYYY-MM-DD HH:MM:SS"}}, "required": ["task_id"]}}},    
    {"type": "function", "function": {"name": "get_today_tasks_tool", "description": "Show tasks to the user", "parameters": {"type": "object", "properties": {"status_filter": {"type": "string", "enum": ["all", "completed", "pending"]}}}}},
    {"type": "function", "function": {"name": "complete_task_tool", "description": "Mark task as completed by its ID", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}}
]

async def process_logic(chat_id: int, text: str):
    cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # Передаем ИИ список с ID
    ctx = await get_today_tasks(chat_id, "all", for_ai=True)
    
    sys_prompt = (
        f"Time: {cur_time}. Tasks:\n{ctx}\n\n"
        "ВАЖНОЕ ПРАВИЛО: Для удаления, обновления или отметки задачи как выполненной "
        "используй СТРОГО числовой ID из списка (например, 5), а не текст задачи."
    )
    
    resp = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": text}],
        tools=tools, tool_choice="auto", parallel_tool_calls=True
    )
    msg = resp.choices[0].message
    results = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            fn = tc.function.name
            if fn == "add_task_tool": results.append(await add_task(chat_id, args["task_text"], args["remind_at"]))
            elif fn == "delete_task_tool": results.append(await delete_task(chat_id, args["task_id"]))
            elif fn == "update_task_tool": results.append(await update_task(chat_id, args["task_id"], args.get("new_text"), args.get("new_time")))
            elif fn == "get_today_tasks_tool": results.append(await get_today_tasks(chat_id, args.get("status_filter", "all"), for_ai=False))
            elif fn == "complete_task_tool": results.append(await complete_task(chat_id, args["task_id"]))
    
    return "\n\n".join(results) if results else msg.content

# --- ХЭНДЛЕРЫ ---
@dp.message(F.text == "/start")
async def cmd_start(m: Message):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (m.chat.id,))
        await db.commit()
    await m.answer("👋 Привет! Я твой менеджер. Буду присылать планы утром и вечером.", reply_markup=get_main_menu())

@dp.message(F.text)
async def handle_text(m: Message):
    txt = m.text
    if txt == "📅 Мои планы на сегодня": txt = "Покажи мои планы на сегодня"
    elif txt == "✅ Что я уже сделал?": txt = "Покажи выполненные задачи на сегодня"
    elif txt == "❓ Что осталось?": txt = "Покажи, что мне осталось сделать сегодня"

    status = await m.reply(f"⏳ {random.choice(STATUS_PHRASES)}")
    try:
        ans = await process_logic(m.chat.id, txt)
        await status.edit_text(ans)
    except Exception as e: await status.edit_text(f"Заминка: {e}")

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
        
        ans = await process_logic(m.chat.id, trans.text)
        await status.edit_text(ans)
    except Exception as e: await status.edit_text(f"Ошибка: {e}")
    finally:
        if os.path.exists(tmp_path): os.remove(tmp_path)

async def main():
    await init_db()
    scheduler.add_job(morning_checkin, 'cron', hour=9, minute=0)
    scheduler.add_job(evening_checkin, 'cron', hour=21, minute=0)
    scheduler.start()
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())