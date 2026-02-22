from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message
from app.services.matchmaker import redis_client

# Настройки лимитов: максимум 3 сообщения за 2 секунды
RATE_LIMIT = 3
TIME_WINDOW = 2

class ThrottlingMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        
        # Нас интересуют только сообщения (текст, фото, стикеры и т.д.)
        if not isinstance(event, Message):
            return await handler(event, data)
            
        user_id = event.from_user.id
        redis_key = f"throttle:{user_id}"
        
        # Увеличиваем счетчик сообщений пользователя
        count = await redis_client.incr(redis_key)
        
        if count == 1:
            # Если это первое сообщение, задаем время жизни ключа (окно времени)
            await redis_client.expire(redis_key, TIME_WINDOW)
            
        if count > RATE_LIMIT:
            # Если превысил лимит, предупреждаем ТОЛЬКО один раз (чтобы бот сам не стал спамером)
            if count == RATE_LIMIT + 1:
                await event.answer("⚠️ <b>Помедленнее!</b> Вы отправляете сообщения слишком быстро.", parse_mode="HTML")
            
            # Прерываем обработку (сообщение не дойдет до собеседника и хендлеров)
            return
            
        # Если всё в порядке - пропускаем сообщение дальше
        return await handler(event, data)