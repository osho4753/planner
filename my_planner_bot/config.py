import os
import json
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from openai import AsyncOpenAI
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TELEGRAM_TOKEN or not GROQ_API_KEY:
    raise ValueError("Проверьте файл .env, не хватает ключей!")

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()
client = AsyncOpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")

DB_NAME = "/data/planner.db" if os.path.exists("/data") else "planner.db"
STATUS_PHRASES = ["Сейчас запишу...", "Секунду...", "Проверяю...", "Записываю...", "Смотрю твои планы..."]

try:
    with open('responses.json', 'r', encoding='utf-8') as file:
        BOT_RESPONSES = json.load(file)
except FileNotFoundError:
    BOT_RESPONSES = {"not_found": ["Не нашел такую задачу..."]}