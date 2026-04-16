import json
from datetime import datetime
from config import client, scheduler
import database as db
from scheduler_jobs import send_reminder
from google_sheets import sheets_manager

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
            "description": "Изменить существующую задачу по ID. ВАЖНО: Если меняешь new_task_time (время события), ОБЯЗАТЕЛЬНО высчитай и передай new_remind_time (время напоминания), иначе напоминание сработает по старому расписанию.", 
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
    {"type": "function", "function": {"name": "complete_task_tool", "description": "Пометить как выполненную по ID", "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}},{
        "type": "function", 
        "function": {
            "name": "set_timezone_tool", 
            "description": "Установить часовой пояс пользователя на основе его города. Вызови это, когда пользователь называет свой город.", 
            "parameters": {
                "type": "object", 
                "properties": {
                    "offset": {
                        "type": "string", 
                        "description": "Смещение города от UTC в часах (например, 3 для Москвы, 5 для Екатеринбурга, 7 для Новосибирска)"
                    }
                }, 
                "required": ["offset"]
            }
        }
    }
]

# --- ФУНКЦИИ-ПОСРЕДНИКИ ---
async def process_add_task(chat_id, args):
    task_text = args["task_text"]
    task_time = args.get("task_time", "")
    remind_time = args.get("remind_time", "")
    
    if len(task_time) == 16: task_time += ":00"
    if len(remind_time) == 16: remind_time += ":00"
    
    try:
        run_date = datetime.strptime(remind_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "Не смог распознать время. Уточни, пожалуйста!", "Error: Invalid time format"

    job_id = f"job_{datetime.now().timestamp()}"
    task_id = await db.save_task_to_db(chat_id, task_text, task_time, remind_time, job_id)
    scheduler.add_job(send_reminder, 'date', run_date=run_date, args=[chat_id, task_id, task_text], id=job_id)
    
    time_short = task_time.split()[1][:5] if " " in task_time else "Весь день"
    remind_short = remind_time.split()[1][:5] if " " in remind_time else ""
    
    human_text = db.get_random_response("add_task", task_text, time_short, remind_short)
    
    ai_info = f"Success. Task added with ID: {task_id}" # ВОТ ЭТО ИИ ЗАПОМНИТ
    # --- GOOGLE SHEETS INTEGRATION ---
    ss_id = await db.get_user_spreadsheet(chat_id)
    if ss_id:
        # Форматируем дату и время красиво
        t_date = task_time.split()[0] if len(task_time) >= 10 else "Сегодня"
        t_time = task_time.split()[1][:5] if " " in task_time else "Весь день"
        # Кидаем в очередь
        sheets_manager.add_task(ss_id, t_date, t_time, task_text, task_id)
    # ---------------------------------
    
    return human_text, ai_info


async def process_logic(chat_id: int, text: str):
    cur_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today_date = datetime.now().strftime("%Y-%m-%d")
    
    ctx = await db.get_tasks_by_date(chat_id, target_date=today_date, status_filter="all", for_ai=True)
    
    if chat_id not in user_history: user_history[chat_id] = []
    user_history[chat_id].append({"role": "user", "content": text})
    
    # Чуть увеличим контекст до 8 сообщений, чтобы ИИ лучше помнил историю инструментов
    user_history[chat_id] = user_history[chat_id][-8:]
    sys_prompt = {
        "role": "system", 
        "content": f"Time: {cur_time}. Tasks:\n{ctx}\n\nУчитывай историю. ВАЖНО: Если юзер просит изменить время только что созданной задачи, используй update_task_tool. Для действий нужен ID задач. Если пользователь называет свой город, обязательно вызови set_timezone_tool, чтобы настроить его часовой пояс, а затем поприветствуй его."
    }

    messages = [sys_prompt] + user_history[chat_id]
    
    resp = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=messages,
        tools=tools, tool_choice="auto", parallel_tool_calls=True
    )
    
    msg = resp.choices[0].message
    
    # --- ИСПРАВЛЕНИЕ: ПРАВИЛЬНОЕ СОХРАНЕНИЕ ОТВЕТА АССИСТЕНТА ---
    assistant_msg = {"role": "assistant", "content": msg.content}
    if msg.tool_calls:
        assistant_msg["tool_calls"] = [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
    user_history[chat_id].append(assistant_msg)
    # -------------------------------------------------------------
    
    results = []
    if msg.tool_calls:
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            fn = tc.function.name
            ai_info = "Executed" # Техническая инфа для ИИ
            
            if fn == "add_task_tool":
                human_text, ai_info = await process_add_task(chat_id, args)
                results.append(human_text)
                
            elif fn == "delete_task_tool":
                row = await db.delete_task_from_db(chat_id, args["task_id"])
                if row:
                    try: scheduler.remove_job(row[1])
                    except: pass
                    ss_id = await db.get_user_spreadsheet(chat_id)
                    if ss_id:
                        sheets_manager.delete_task(ss_id, args["task_id"])
                    results.append(db.get_random_response("delete_task", row[2]))
                    ai_info = f"Success. Deleted task ID: {args['task_id']}"
                else:
                    results.append(db.get_random_response("not_found", f"ID {args['task_id']}"))
                    ai_info = f"Error: Task ID {args['task_id']} not found"
                    
            elif fn == "update_task_tool":
                res = await db.update_task_in_db(
                    chat_id, 
                    args["task_id"], 
                    new_text=args.get("new_text"), 
                    new_task_time=args.get("new_task_time"),
                    new_remind_time=args.get("new_remind_time")
                )
                ss_id = await db.get_user_spreadsheet(chat_id)
                if ss_id:
                    # Обновляем текст и время в таблице
                    new_t = res["task_time"].split()[1][:5] if res["task_time"] else None
                    sheets_manager.update_task(ss_id, args["task_id"], new_text=res["text"], new_time=new_t)
                if res:
                    # Удаляем старый таймер, если он еще висит
                    try: scheduler.remove_job(res["job_id"])
                    except: pass
                    
                    try:
                        # Берем новое время напоминания
                        local_run_date = datetime.strptime(res["remind_time"], "%Y-%m-%d %H:%M:%S")
                        
                        # Конвертируем в UTC с учетом пояса юзера (как мы делали в add_task)
                        user_tz = await db.get_user_tz(chat_id)
                        utc_run_date = local_run_date - timedelta(hours=user_tz)
                        aware_run_date = utc_run_date.replace(tzinfo=timezone.utc)
                        
                        # Заводим таймер ТОЛЬКО если новое время в будущем
                        if aware_run_date > datetime.now(timezone.utc):
                            scheduler.add_job(send_reminder, 'date', run_date=aware_run_date, args=[chat_id, args["task_id"], res["text"]], id=res["job_id"])
                        
                        time_short = res["task_time"].split()[1][:5] if res["task_time"] and " " in res["task_time"] else "Весь день"
                        remind_short = res["remind_time"].split()[1][:5] if res["remind_time"] and " " in res["remind_time"] else ""
                        results.append(db.get_random_response("update_task", res["text"], time_short, remind_short))
                        ai_info = f"Success. Updated task ID: {args['task_id']}"
                        
                    except ValueError:
                        # ВМЕСТО PASS ТЕПЕРЬ МЫ ЧЕСТНО ГОВОРИМ ОБ ОШИБКЕ
                        results.append(f"Я обновил задачу «{res['text']}», но не смог разобрать точное время для таймера. Давай уточним, во сколько именно напомнить?")
                        ai_info = "Error: Invalid time format during update. Asked user for clarification."
                else:
                    results.append(db.get_random_response("not_found", f"ID {args['task_id']}")) 
                    ai_info = f"Error: Task ID {args['task_id']} not found"

            elif fn == "get_tasks_tool":
                res = await db.get_tasks_by_date(chat_id, args["target_date"], args.get("status_filter", "all"), for_ai=False)
                results.append(res)
                ai_info = res # Скармливаем ИИ список задач, чтобы он их видел
                
            elif fn == "complete_task_tool":
                task_text = await db.complete_task_in_db(chat_id, args["task_id"])
                if task_text:
                    results.append(db.get_random_response("complete_task", task_text))
                    ai_info = f"Success. Completed task ID: {args['task_id']}"
                    
                    # --- GOOGLE SHEETS INTEGRATION ---
                    ss_id = await db.get_user_spreadsheet(chat_id)
                    if ss_id:
                        sheets_manager.complete_task(ss_id, task_text)
                    # ---------------------------------
                else:
                    results.append("Не найдено")
                    ai_info = f"Error: Task not found"

            elif fn == "set_timezone_tool":
                # Переводим строку от ИИ в нормальное число
                offset = int(args["offset"]) 
                
                await db.set_user_tz(chat_id, offset)
                
                sign = "+" if offset > 0 else ""
                results.append(f"🌍 Отлично! Я установил твой часовой пояс (UTC{sign}{offset}). Теперь все напоминания будут приходить точно вовремя. Какие планы запишем?")
                ai_info = f"Success. Timezone updated to UTC{sign}{offset}"
                
            # --- ИСПРАВЛЕНИЕ: СОХРАНЯЕМ РЕЗУЛЬТАТ ТУЛА В ИСТОРИЮ ИИ ---
            user_history[chat_id].append({
                "role": "tool",
                "tool_call_id": tc.id,
                "name": fn,
                "content": ai_info
            })
            # -------------------------------------------------------------
                
    return "\n\n".join(results) if results else msg.content