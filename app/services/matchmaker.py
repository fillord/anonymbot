import time
import redis.asyncio as redis

redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)

async def join_queue(user_id: int, is_vip: bool = False) -> tuple[int | None, bool]:
    # 1. Сначала пытаемся спасти кого-то от ИИ
    ai_chat_user = await redis_client.spop("ai_chats")
    if ai_chat_user:
        await connect_users(user_id, int(ai_chat_user))
        # Возвращаем ID партнера и True (означает, что мы перехватили его у ИИ)
        return int(ai_chat_user), True

    # 2. Ищем реального собеседника в очередях
    partner_id = await redis_client.lpop("queue:vip")
    if not partner_id:
        partner_id = await redis_client.lpop("queue:normal")

    if partner_id and int(partner_id) != user_id:
        await redis_client.hdel("queue_times", str(partner_id))
        await connect_users(user_id, int(partner_id))
        # Возвращаем ID партнера и False (обычный поиск)
        return int(partner_id), False

    # 3. Встаем в очередь
    queue_name = "queue:vip" if is_vip else "queue:normal"
    await redis_client.rpush(queue_name, user_id)
    await redis_client.hset("queue_times", str(user_id), int(time.time()))
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
    await redis_client.lrem("queue:normal", 0, user_id_str)
    await redis_client.lrem("queue:vip", 0, user_id_str)
    await redis_client.hdel("queue_times", user_id_str)
    await redis_client.srem("ai_chats", user_id_str)