import datetime
from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Message, CallbackQuery
from app.database.db import is_user_banned

class BanCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        
        user = event.from_user
        if user:
            session = data.get("session")
            if session:
                ban_status = await is_user_banned(session, user.id)
                
                if ban_status:
                    if ban_status == "permanent":
                        msg = "🚫 <b>Доступ заблокирован навсегда.</b>\nПричина: Многократные нарушения правил сообщества."
                    else:
                        # Считаем, сколько минут осталось
                        now = datetime.datetime.utcnow()
                        diff = ban_status - now
                        minutes_left = max(1, diff.seconds // 60)
                        
                        msg = (
                            f"🚫 <b>Вы временно заблокированы.</b>\n\n"
                            f"Срок блокировки: <b>{minutes_left} мин.</b>\n"
                            f"Пожалуйста, соблюдайте правила общения."
                        )

                    if isinstance(event, Message):
                        await event.answer(msg, parse_mode="HTML")
                    elif isinstance(event, CallbackQuery):
                        await event.answer(msg.replace("<b>", "").replace("</b>", ""), show_alert=True)
                    
                    return # Прерываем выполнение
                    
        return await handler(event, data)