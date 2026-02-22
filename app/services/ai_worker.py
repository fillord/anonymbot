import asyncio
import time
from aiogram.fsm.storage.base import StorageKey
from app.services.matchmaker import redis_client
from app.keyboards.chat_kb import get_in_chat_kb
from app.utils.states import ChatState
from app.services.ai_client import clear_ai_context

async def ai_fallback_worker(bot, storage):
    while True:
        try:
            for queue_name in ["queue:vip", "queue:normal"]:
                user_id = await redis_client.lindex(queue_name, 0)
                
                if user_id:
                    join_time = await redis_client.hget("queue_times", str(user_id))
                    
                    if join_time and (int(time.time()) - int(join_time) >= 10):
                        await redis_client.lpop(queue_name)
                        await redis_client.hdel("queue_times", str(user_id))
                        
                        await clear_ai_context(int(user_id))
                        # Подключаем ИИ в Redis
                        await redis_client.set(f"chat:{user_id}", "AI")
                        await redis_client.sadd("ai_chats", user_id)
                        
                        # --- ИСПРАВЛЕНИЕ БАГА: Принудительно меняем FSM-стейт на "в чате" ---
                        state_key = StorageKey(bot_id=bot.id, chat_id=int(user_id), user_id=int(user_id))
                        await storage.set_state(key=state_key, state=ChatState.in_chat)
                        
                        await bot.send_message(
                            int(user_id), 
                            "✅ Собеседник найден! Можете общаться.",
                            reply_markup=get_in_chat_kb()
                        )
                        
                        # Имитация того, что ИИ печатает сообщение
                        await bot.send_chat_action(chat_id=int(user_id), action="typing")
                        await asyncio.sleep(2)
                        
                        if await redis_client.get(f"chat:{user_id}") == "AI":
                            await bot.send_message(int(user_id), "Привет! Как дела?")
                            
        except Exception as e:
            print(f"Worker Error: {e}")
            
        await asyncio.sleep(3)