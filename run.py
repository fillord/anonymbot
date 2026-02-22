import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import BOT_TOKEN, DATABASE_URL, REDIS_URL
from app.database.models import Base
from app.middlewares.db_middleware import DbSessionMiddleware
from app.middlewares.ban_middleware import BanCheckMiddleware
from app.middlewares.throttling import ThrottlingMiddleware
# Импорт роутеров
from app.handlers import admin, menu, chat
# Импорт фоновой задачи
from app.services.ai_worker import ai_fallback_worker

async def main():
    # 1. Настройка логирования
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Starting bot...")

    # 2. Инициализация БД (PostgreSQL)
    engine = create_async_engine(DATABASE_URL, echo=False)
    session_pool = async_sessionmaker(engine, expire_on_commit=False)

    # Создаем таблицы (для MVP пойдет, в проде лучше использовать Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 3. Инициализация Бота и Диспетчера (с RedisStorage для быстрых FSM)
    bot = Bot(token=BOT_TOKEN)
    storage = RedisStorage.from_url(REDIS_URL)
    dp = Dispatcher(storage=storage)

    # 4. Регистрация Middlewares
    # Пробрасываем сессию БД во все апдейты
    dp.update.outer_middleware(DbSessionMiddleware(session_pool))
    
    # Проверяем на бан перед обработкой сообщений и коллбеков
    dp.message.outer_middleware(BanCheckMiddleware())
    dp.callback_query.outer_middleware(BanCheckMiddleware())

    dp.message.middleware(ThrottlingMiddleware())
    # 5. Регистрация Router'ов (Порядок важен!)
    dp.include_router(admin.router)  # Сначала админские команды
    dp.include_router(menu.router)   # Затем меню (/start, профиль)
    dp.include_router(chat.router)   # В конце логика чата и маршрутизация сообщений

    # 6. Запуск фоновой задачи ИИ-матчмейкера
    # Используем create_task, чтобы задача крутилась в фоне и не блокировала бота
    asyncio.create_task(ai_fallback_worker(bot, storage))

    # 7. Запуск поллинга
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await engine.dispose()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Bot stopped.")