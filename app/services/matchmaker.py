# app/services/matchmaker.py
import time
import redis.asyncio as redis

redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

async def get_partner_from_queues(target_queues: list) -> int | None:
    """Проверяет подходящие очереди и возвращает собеседника"""
    for q in target_queues:
        partner_id = await redis_client.lpop(q)
        if partner_id:
            return int(partner_id)
    return None

async def join_queue(user_id: int, is_vip: bool, user_gender: str, search_gender: str) -> tuple[int | None, bool]:
    user_id_str = str(user_id)
    
    # 0. Кэшируем предпочтения юзера в Redis на 24 часа для быстрого доступа
    await redis_client.hset(f"user_prefs:{user_id_str}", mapping={"g": user_gender, "s": search_gender})
    await redis_client.expire(f"user_prefs:{user_id_str}", 86400)

    # ==========================================
    # 1. ПЕРЕХВАТ ИЗ ИИ-ЧАТА (С УЧЕТОМ ФИЛЬТРОВ)
    # ==========================================
    ai_chat_users = await redis_client.smembers("ai_chats")
    for ai_user_id in ai_chat_users:
        # Получаем данные того, кто общается с ИИ
        prefs = await redis_client.hgetall(f"user_prefs:{ai_user_id}")
        if not prefs:
            continue
            
        ai_g = prefs.get("g", "M")
        ai_s = prefs.get("s", "any")
        
        # Проверяем ВЗАИМНУЮ совместимость
        is_match_for_me = (search_gender == 'any' or search_gender == ai_g)
        is_match_for_them = (ai_s == 'any' or ai_s == user_gender)
        
        if is_match_for_me and is_match_for_them:
            # Atomic удаление: если мы смогли его удалить (никто не перехватил до нас)
            if await redis_client.srem("ai_chats", ai_user_id):
                await connect_users(user_id, int(ai_user_id))
                return int(ai_user_id), True

    # ==========================================
    # 2. ПОИСК В ОБЫЧНЫХ ОЧЕРЕДЯХ
    # ==========================================
    target_queues = []
    if search_gender == 'any':
        target_queues = [f"queue:M:{user_gender}", f"queue:M:any", f"queue:F:{user_gender}", f"queue:F:any"]
    elif search_gender == 'M':
        target_queues = [f"queue:M:{user_gender}", f"queue:M:any"]
    elif search_gender == 'F':
        target_queues = [f"queue:F:{user_gender}", f"queue:F:any"]

    partner_id = await get_partner_from_queues(target_queues)
    
    if partner_id and int(partner_id) != user_id:
        await redis_client.hdel("queue_times", str(partner_id))
        await redis_client.delete(f"user_queue:{partner_id}") 
        await connect_users(user_id, partner_id)
        return int(partner_id), False

    # ==========================================
    # 3. ЕСЛИ НИКОГО НЕТ - ВСТАЕМ В ОЧЕРЕДЬ
    # ==========================================
    my_queue = f"queue:{user_gender}:{search_gender}"
    
    if is_vip:
        await redis_client.lpush(my_queue, user_id)
    else:
        await redis_client.rpush(my_queue, user_id)
        
    await redis_client.hset("queue_times", user_id_str, int(time.time()))
    await redis_client.set(f"user_queue:{user_id_str}", my_queue)
    return None, False

async def connect_users(user1: int, user2: int):
    await redis_client.set(f"chat:{user1}", user2)
    await redis_client.set(f"chat:{user2}", user1)

async def leave_chat(user_id: int):
    partner_id = await redis_client.get(f"chat:{user_id}")
    if partner_id:
        await redis_client.delete(f"chat:{user_id}")
        if partner_id != "AI":
            await redis_client.delete(f"chat:{partner_id}")
    return partner_id

async def is_in_chat(user_id: int):
    return await redis_client.get(f"chat:{user_id}")

async def remove_from_queue(user_id: int):
    user_id_str = str(user_id)
    my_queue = await redis_client.get(f"user_queue:{user_id}")
    
    if my_queue:
        await redis_client.lrem(my_queue, 0, user_id_str)
        await redis_client.delete(f"user_queue:{user_id}")
        
    await redis_client.hdel("queue_times", user_id_str)
    await redis_client.srem("ai_chats", user_id_str)