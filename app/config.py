import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip().replace('"', '').replace("'", "")
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Настройки Webhook
WEBHOOK_DOMAIN = os.getenv("WEBHOOK_DOMAIN", "https://anon.yolacloud.ru") 
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"{WEBHOOK_DOMAIN}{WEBHOOK_PATH}"

# Настройки локального сервера (aiohttp)
WEBAPP_HOST = "127.0.0.1"
WEBAPP_PORT = 8085