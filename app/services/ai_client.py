import json
import logging
from google import genai
from google.genai import types
from app.config import GEMINI_API_KEY
from app.services.matchmaker import redis_client

# Инициализируем новый клиент
client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """Ты — случайный анонимный собеседник в Telegram чат-рулетке.
Тебе 20-25 лет. Общайся коротко (1-2 предложения), естественно, как обычный человек в мессенджере.
Используй сленг, иногда можешь писать без заглавных букв. Никакого официоза.
Никогда не признавайся, что ты ИИ, языковая модель или бот. Ты живой человек! Если спрашивают — переводи тему, шути или возмущайся.
Не будь слишком вежливым, поддерживай живой и немного небрежный стиль."""

async def get_ai_response(user_id: int, text: str) -> str:
    redis_key = f"ai_context:{user_id}"
    
    # 1. Достаем историю диалога из Redis
    history_data = await redis_client.lrange(redis_key, 0, -1)
    
    contents = []
    for item in history_data:
        msg = json.loads(item)
        # В новом SDK роли называются "user" и "model"
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])])
        )
        
    # 2. Добавляем текущее сообщение пользователя
    contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=text)])
    )
    
    # 3. Запрос к новой API
    try:
        response = await client.aio.models.generate_content(
            model='gemini-2.5-flash', # Используем актуальную модель
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.8,
            )
        )
        ai_text = response.text
    except Exception as e:
        # Теперь ошибка 100% отобразится в pm2 logs
        logging.error(f"Gemini API Error: {e}")
        ai_text = "блин, инет лагает... что ты сказал?"
        
    # 4. Сохраняем новое сообщение юзера и ответ ИИ в Redis
    user_msg = {"role": "user", "content": text}
    ai_msg = {"role": "assistant", "content": ai_text}
    
    await redis_client.rpush(redis_key, json.dumps(user_msg))
    await redis_client.rpush(redis_key, json.dumps(ai_msg))
    
    # 5. Храним только последние 12 сообщений
    await redis_client.ltrim(redis_key, -12, -1)
    
    return ai_text

async def clear_ai_context(user_id: int):
    """Очищает память ИИ после завершения чата"""
    await redis_client.delete(f"ai_context:{user_id}")