import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import BOT_TOKEN, DATABASE_URL, REDIS_URL, WEBHOOK_URL, WEBHOOK_PATH, WEBAPP_HOST, WEBAPP_PORT
from app.database.models import Base
from app.middlewares.db_middleware import DbSessionMiddleware
from app.middlewares.ban_middleware import BanCheckMiddleware
from app.middlewares.throttling import ThrottlingMiddleware

from app.handlers import admin, menu, chat
from app.services.ai_worker import ai_fallback_worker

# Глобальные переменные для БД и тасок
engine = None
ai_task = None

async def on_startup(bot: Bot, dispatcher: Dispatcher):
    global engine, ai_task
    logging.info("Starting up...")
    
    # Инициализация БД
    engine = create_async_engine(DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    session_pool = async_sessionmaker(engine, expire_on_commit=False)
    dispatcher.update.outer_middleware(DbSessionMiddleware(session_pool))
    
    # Устанавливаем вебхук в Telegram
    await bot.set_webhook(url=WEBHOOK_URL, drop_pending_updates=True)
    
    # Запускаем фоновый воркер ИИ
    storage = dispatcher.storage
    ai_task = asyncio.create_task(ai_fallback_worker(bot, storage))
    logging.info(f"Webhook set to {WEBHOOK_URL}")

async def on_shutdown(bot: Bot, dispatcher: Dispatcher):
    global engine, ai_task
    logging.info("Shutting down...")
    
    # Останавливаем ИИ
    if ai_task:
        ai_task.cancel()
        
    # Удаляем вебхук
    await bot.delete_webhook()
    
    # Закрываем БД
    if engine:
        await engine.dispose()
    await bot.session.close()

def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    
    bot = Bot(token=BOT_TOKEN)
    storage = RedisStorage.from_url(REDIS_URL)
    dp = Dispatcher(storage=storage)

    # Регистрация Middlewares (БД регистрируется в on_startup, чтобы избежать конфликтов)
    dp.message.outer_middleware(BanCheckMiddleware())
    dp.callback_query.outer_middleware(BanCheckMiddleware())
    dp.message.middleware(ThrottlingMiddleware())

    # Регистрация Роутеров
    dp.include_router(admin.router)
    dp.include_router(menu.router)
    dp.include_router(chat.router)

    # Регистрация хуков запуска/остановки
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    # Настройка aiohttp сервера
    app = web.Application()
    webhook_requests_handler = SimpleRequestHandler(dispatcher=dp, bot=bot)
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)
    
    # Связываем приложение с диспетчером
    setup_application(app, dp, bot=bot)

    # Запускаем веб-сервер
    web.run_app(app, host=WEBAPP_HOST, port=WEBAPP_PORT)

if __name__ == "__main__":
    main()