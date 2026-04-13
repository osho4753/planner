from datetime import datetime
import aiosqlite
import random
from config import DB_NAME, BOT_RESPONSES

def get_random_response(action: str, task: str, time: str = "", remind_time: str = ""):
    templates = BOT_RESPONSES.get(action, [f"Готово: {task}"])
    template = random.choice(templates)
    return template.replace("{task}", task).replace("{time}", time).replace("{remind_time}", remind_time)

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("CREATE TABLE IF NOT EXISTS users (chat_id INTEGER PRIMARY KEY)")
        
        # Создаем таблицу для новых пользователей
        await db.execute('''CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            chat_id INTEGER, 
            task_text TEXT, 
            task_time TEXT, 
            remind_time TEXT, 
            job_id TEXT, 
            is_completed INTEGER DEFAULT 0
        )''')
        
        try: 
            await db.execute("ALTER TABLE tasks ADD COLUMN task_time TEXT")
        except: 
            pass
            
        try: 
            await db.execute("ALTER TABLE tasks ADD COLUMN remind_time TEXT")
        except: 
            pass
            
        await db.commit()


async def get_users():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT chat_id FROM users")
        return await cursor.fetchall()

async def add_user(chat_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
        await db.commit()

async def get_tasks_by_date(chat_id: int, target_date: str, status_filter: str = "all", for_ai: bool = False):
    """
    target_date ожидает формат 'YYYY-MM-DD'.
    Если ИИ или юзер не укажут дату, по умолчанию берем сегодня.
    """
    async with aiosqlite.connect(DB_NAME) as db:
        # Ищем задачи, которые начинаются с указанной даты
        q = "SELECT id, task_text, task_time, is_completed FROM tasks WHERE chat_id = ? AND task_time LIKE ?"
        p = [chat_id, f"{target_date}%"]
        
        if status_filter == "completed": q += " AND is_completed = 1"
        elif status_filter == "pending": q += " AND is_completed = 0"
        
        cursor = await db.execute(q, p)
        rows = await cursor.fetchall()
        
        date_str = datetime.strptime(target_date, "%Y-%m-%d").strftime("%d.%m")
        if not rows: return f"На {date_str} планов пока нет."
        
        res = []
        for r in rows:
            icon = "✅" if r[3] else "⏳"
            time = r[2].split()[1][:5] if r[2] else "Весь день"
            if for_ai: res.append(f"[ID: {r[0]}] {icon} {r[1]} (в {time})")
            else: res.append(f"{icon} {r[1]} (в {time})")
                
        header = f"📅 Планы на {date_str}:\n\n"
        return "\n".join(res) if for_ai else header + "\n".join(res)
    
async def save_task_to_db(chat_id: int, task_text: str, task_time: str, remind_time: str, job_id: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "INSERT INTO tasks (chat_id, task_text, task_time, remind_time, job_id, is_completed) VALUES (?,?,?,?,?,0)", 
            (chat_id, task_text, task_time, remind_time, job_id)
        )
        task_id = cursor.lastrowid
        await db.commit()
        return task_id

async def delete_task_from_db(chat_id: int, task_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT id, job_id, task_text FROM tasks WHERE chat_id = ? AND id = ?", (chat_id, task_id))
        row = await cursor.fetchone()
        if row:
            await db.execute("DELETE FROM tasks WHERE id = ?", (row[0],))
            await db.commit()
            return row # Возвращаем данные для удаления таймера
    return None

async def complete_task_in_db(chat_id: int, task_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT task_text FROM tasks WHERE chat_id = ? AND id = ?", (chat_id, task_id))
        row = await cursor.fetchone()
        if row:
            await db.execute("UPDATE tasks SET is_completed = 1 WHERE id = ?", (task_id,))
            await db.commit()
            return row[0]
    return None

async def get_raw_tasks(chat_id: int, target_date: str):
    """Возвращает список задач в виде кортежей для построения кнопок"""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT id, task_text, is_completed, task_time FROM tasks WHERE chat_id = ? AND task_time LIKE ?",
            (chat_id, f"{target_date}%")
        )
        return await cursor.fetchall()

async def toggle_task_status(task_id: int):
    """Меняет статус задачи (выполнена <-> не выполнена)"""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT is_completed FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        if row:
            new_status = 0 if row[0] == 1 else 1
            await db.execute("UPDATE tasks SET is_completed = ? WHERE id = ?", (new_status, task_id))
            await db.commit()
async def get_task_time(task_id: int):
    """Достает реальное время события по ID"""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT task_time FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return row[0] if row else None
    
async def update_task_in_db(chat_id: int, task_id: int, new_text: str = None, new_task_time: str = None, new_remind_time: str = None):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT id, job_id, task_text, task_time, remind_time FROM tasks WHERE chat_id = ? AND id = ?", (chat_id, task_id))
        row = await cursor.fetchone()
        if row:
            t_id, j_id, db_text, db_task_time, db_remind_time = row
            f_text = new_text if new_text else db_text
            f_task_time = new_task_time if new_task_time else db_task_time
            f_remind_time = new_remind_time if new_remind_time else db_remind_time
            
            await db.execute("UPDATE tasks SET task_text = ?, task_time = ?, remind_time = ? WHERE id = ?", (f_text, f_task_time, f_remind_time, t_id))
            await db.commit()
            return {"job_id": j_id, "text": f_text, "remind_time": f_remind_time, "task_time": f_task_time}
    return None
async def is_task_completed(task_id: int) -> bool:
    """Проверяет, выполнена ли задача на данный момент"""
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT is_completed FROM tasks WHERE id = ?", (task_id,))
        row = await cursor.fetchone()
        return bool(row and row[0] == 1)