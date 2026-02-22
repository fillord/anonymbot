from aiogram.filters import BaseFilter
from aiogram.types import Message, CallbackQuery
import os

class IsAdmin(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        # Получаем список ID админов из .env (например: ADMIN_IDS=123456789,987654321)
        admin_ids_str = os.getenv("ADMIN_IDS", "")
        admin_ids = [int(x.strip()) for x in admin_ids_str.split(",") if x.strip().isdigit()]
        
        user_id = event.from_user.id
        return user_id in admin_ids