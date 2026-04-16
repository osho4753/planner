import gspread
from google.oauth2.service_account import Credentials
import threading
import queue
import time
import os
from config import GOOGLE_CREDS_PATH, GOOGLE_TEMPLATE_ID

class GoogleSheetsManager:
    def __init__(self):
        self.client = None
        self.queue = queue.Queue()
        self.enabled = False

        # Запускаем только если есть файл ключей
        if os.path.exists(GOOGLE_CREDS_PATH) and GOOGLE_TEMPLATE_ID:
            try:
                scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
                creds = Credentials.from_service_account_file(GOOGLE_CREDS_PATH, scopes=scopes)
                self.client = gspread.authorize(creds)
                self.enabled = True
                
                # Запускаем фоновый поток для обработки очереди
                threading.Thread(target=self._worker, daemon=True).start()
                print("✅ Google Sheets Manager успешно запущен")
            except Exception as e:
                print(f"⚠️ Ошибка GSheets (проверь ключи): {e}")

    def create_dashboard_sync(self, user_name: str):
        """Синхронно копирует шаблон и дает доступ по ссылке"""
        if not self.enabled:
            return None, "Google Sheets не настроены"
        try:
            # Копируем мастер-шаблон
            new_sheet = self.client.copy(GOOGLE_TEMPLATE_ID, title=f"📊 Планер: {user_name}")
            # Открываем доступ на чтение для всех, у кого есть ссылка
            new_sheet.share(None, perm_type='anyone', role='reader')
            return new_sheet.id, new_sheet.url
        except Exception as e:
            print(f"Ошибка копирования шаблона: {e}")
            return None, str(e)

    def add_task(self, spreadsheet_id: str, date: str, time_str: str, text: str):
        """Кладет задачу на добавление в очередь"""
        if not self.enabled: return
        self.queue.put({
            "action": "append",
            "spreadsheet_id": spreadsheet_id,
            "data": [date, time_str, text, "⏳ В планах"]
        })

    def complete_task(self, spreadsheet_id: str, text: str):
        """Кладет задачу на обновление статуса в очередь"""
        if not self.enabled: return
        self.queue.put({
            "action": "complete",
            "spreadsheet_id": spreadsheet_id,
            "text": text
        })

    # В google_sheets.py дополняем методы класса GoogleSheetsManager

    def delete_task(self, spreadsheet_id: str, task_id: int):
        """Очередь на удаление задачи"""
        if not self.enabled: return
        self.queue.put({
            "action": "delete",
            "spreadsheet_id": spreadsheet_id,
            "task_id": str(task_id)
        })

    def update_task(self, spreadsheet_id: str, task_id: int, new_text: str = None, new_time: str = None):
        """Очередь на обновление текста или времени"""
        if not self.enabled: return
        self.queue.put({
            "action": "update",
            "spreadsheet_id": spreadsheet_id,
            "task_id": str(task_id),
            "new_text": new_text,
            "new_time": new_time
        })

    # Обновляем воркер (метод _worker)
    def _worker(self):
        while True:
            job = self.queue.get()
            try:
                sheet = self.client.open_by_key(job['spreadsheet_id']).worksheet("Raw_Data")
                
                # Ищем строку по ID задачи (допустим, ID у нас в 5-м столбце)
                cell = None
                if job['action'] in ["complete", "delete", "update"]:
                    try:
                        cell = sheet.find(job['task_id'], in_column=5)
                    except:
                        print(f"Задача с ID {job.get('task_id')} не найдена в таблице")

                if job['action'] == "append":
                    # Добавляем: Дата, Время, Текст, Статус, ID
                    sheet.append_row(job['data'], value_input_option='RAW')
                    
                elif job['action'] == "complete" and cell:
                    sheet.update_cell(cell.row, 4, "✅ Выполнено")
                    
                elif job['action'] == "delete" and cell:
                    sheet.delete_rows(cell.row)
                    
                elif job['action'] == "update" and cell:
                    if job['new_text']:
                        sheet.update_cell(cell.row, 3, job['new_text'])
                    if job['new_time']:
                        sheet.update_cell(cell.row, 2, job['new_time'])

                time.sleep(1.2)
            except Exception as e:
                print(f"GSheets Worker Error: {e}")
            self.queue.task_done()

# Создаем глобальный экземпляр
sheets_manager = GoogleSheetsManager()