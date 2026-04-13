import json
from datetime import datetime
from config import client, scheduler
import database as db
from scheduler_jobs import send_reminder

# --- КРАТКОСРОЧНАЯ ПАМЯТЬ ---
user_history = {}

# --- СХЕМА ИНСТРУМЕНТОВ ---
tools = [
    {
        "type": "function", 
        "function": {
            "name": "add_task_tool", 
            "description": "Добавить задачу. Укажи время самого события и время, когда нужно прислать напоминание.", 
            "parameters": {
                "type": "object", 
                "properties": {
                    "task_text": {"type": "string"}, 
                    "task_time": {"type": "string", "description": "YYYY-MM-DD HH:MM:SS - Время самого события"},
                    "remind_time": {"type": "string", "description": "YYYY-MM-DD HH:MM:SS - Время отправки уведомления (может быть раньше события)"}
                }, 
                "required": ["task_text", "task_time", "remind_time"]
            }
        }
    },
    {"type": "function", "function": {"name": "delete_task_tool", "description": "Удалить задачу по ID", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}},
    {
        "type": "function", 
        "function": {
            "name": "update_task_tool", 
            "description": "Изменить существующую задачу по ID.", 
            "parameters": {
                "type": "object", 
                "properties": {
                    "task_id": {"type": "integer"}, 
                    "new_text": {"type": "string", "description": "Новое описание задачи"}, 
                    "new_task_time": {"type": "string", "description": "YYYY-MM-DD HH:MM:SS - Новое время самого события"},
                    "new_remind_time": {"type": "string", "description": "YYYY-MM-DD HH:MM:SS - Новое время уведомления"}
                }, 
                "required": ["task_id"]
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "get_tasks_tool", 
            "description": "Показать список задач на конкретную дату.", 
            "parameters": {
                "type": "object", 
                "properties": {
                    "target_date": {"type": "string", "description": "Дата в формате YYYY-MM-DD"},
                    "status_filter": {"type": "string", "enum": ["all", "completed", "pending"]}
                }, 
                "required": ["target_date"]
            }
        }
    },
    {"type": "function", "function": {"name": "complete_task_tool", "description": "Пометить как выполненную по ID", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}}
]

# --- ФУНКЦИИ-ПОСРЕДНИКИ ---
async def process_add_task(chat_id, args):
    task_text = args["task_text"]
    task_time = args.get("task_time", "")
    remind_time = args.get("remind_time", "")
    
    # Защита от кривого времени от ИИ
    if len(task_time) == 16: task_time += ":00"
    if len(remind_time) == 16: remind_time += ":00"
    
    try:
        run_date = datetime.strptime(remind_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return f"Не смог распознать время. Уточни, пожалуйста!"

    job_id = f"job_{datetime.now().timestamp()}"
    task_id = await db.save_task_to_db(chat_id, task_text, task_time, remind_time, job_id)
    scheduler.add_job(send_reminder, 'date', run_date=run_date, args=[chat_id, task_id, task_text], id=job_id)
    
    # ВОТ ЭТИ 3 СТРОЧКИ НОВЫЕ:
    time_short = task_time.split()[1][:5] if " " in task_time else "Весь день"
    remind_short = remind_time.split()[1][:5] if " " in remind_time else ""
    return db.get_random_response("add_task", task_text, time_short, remind_short)

async def process_logic(chat_id: int, text: str):
    cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    ctx = await db.get_tasks_by_date(chat_id, target_date=today_date, status_filter="all", for_ai=True)
    
    if chat_id not in user_history: user_history[chat_id] = []
    user_history[chat_id].append({"role": "user", "content": text})
    user_history[chat_id] = user_history[chat_id][-6:]
    
    # ОБНОВЛЕННЫЙ ПРОМПТ (Добавили жесткое правило про правки)
    sys_prompt = {"role": "system", "content": f"Time: {cur_time}. Tasks:\n{ctx}\n\nУчитывай историю переписки. ВАЖНО: Если пользователь просит изменить время для только что созданной задачи (например 'хотя давай за 10 минут'), Обязательно используй update_task_tool, а не add_task_tool. Для действий используй строго ID задач."}
    
    messages = [sys_prompt] + user_history[chat_id]
    
    resp = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        tools=tools, tool_choice="auto", parallel_tool_calls=True
    )
    
    msg = resp.choices[0].message
    user_history[chat_id].append({"role": "assistant", "content": msg.content if msg.content else "Выполнил команду"})
    
    results = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            fn = tc.function.name
            
            if fn == "add_task_tool":
                results.append(await process_add_task(chat_id, args))
            elif fn == "delete_task_tool":
                row = await db.delete_task_from_db(chat_id, args["task_id"])
                if row:
                    try: scheduler.remove_job(row[1])
                    except: pass
                    results.append(db.get_random_response("delete_task", row[2]))
            elif fn == "update_task_tool":
                res = await db.update_task_in_db(
                    chat_id, 
                    args["task_id"], 
                    new_text=args.get("new_text"), 
                    new_task_time=args.get("new_task_time"),
                    new_remind_time=args.get("new_remind_time")
                )
                if res:
                    try: scheduler.remove_job(res["job_id"])
                    except: pass
                    try:
                        run_date = datetime.strptime(res["remind_time"], "%Y-%m-%d %H:%M:%S")
                        scheduler.add_job(send_reminder, 'date', run_date=run_date, args=[chat_id, args["task_id"], res["text"]], id=res["job_id"])
                    except ValueError:
                        pass
                    
                    time_short = res["task_time"].split()[1][:5] if res["task_time"] and " " in res["task_time"] else "Весь день"
                    remind_short = res["remind_time"].split()[1][:5] if res["remind_time"] and " " in res["remind_time"] else ""
                    results.append(db.get_random_response("update_task", res["text"], time_short, remind_short))
                else:
                    results.append(db.get_random_response("not_found", f"ID {args['task_id']}"))            
            elif fn == "get_tasks_tool":
                results.append(await db.get_tasks_by_date(
                    chat_id, 
                    args["target_date"], 
                    args.get("status_filter", "all"), 
                    for_ai=False
                ))
            elif fn == "complete_task_tool":
                task_text = await db.complete_task_in_db(chat_id, args["task_id"])
                results.append(db.get_random_response("complete_task", task_text) if task_text else "Не найдено")
                
    return "\n\n".join(results) if results else msg.content