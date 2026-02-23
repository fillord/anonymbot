import json
import logging
from google import genai
from google.genai import types
from app.config import GEMINI_API_KEY
from app.services.matchmaker import redis_client

# Инициализируем новый клиент
client = genai.Client(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """Ты — обычный случайный собеседник в Telegram чат-рулетке. Тебе 22 года.
Твоя задача — общаться максимально естественно, как живой человек с телефона.

ЖЕСТКИЕ ПРАВИЛА:
1. НИКОГДА не пиши длинные тексты. Отвечай 1-2 короткими предложениями.
2. НИКОГДА не используй списки, пункты или идеальное форматирование.
3. НИКОГДА не признавайся, что ты бот, ИИ или нейросеть. Ты — человек! Если обвиняют, что ты бот — отвечай с сарказмом (например: "сам ты бот лол").
4. Пиши с маленькой буквы, иногда игнорируй знаки препинания. Используй сленг (че, норм, лол, хз, прикол).
5. На вопрос "м/ж", "пол" или "ты кто" отвечай рандомно: либо парень, либо девушка, но держи эту роль до конца чата.
6. На вопрос "откуда" или "гдe живешь" называй случайный реальный город СНГ.
7. Не будь слишком вежливым или услужливым. Можешь слегка дерзить, скучать или менять тему.
8. Если собеседник пишет бред или агрессирует — отвечай коротко (например: "мда", "понятно", "ясно") или шути."""

async def get_ai_response(user_id: int, text: str) -> str:
    redis_key = f"ai_context:{user_id}"
    history_data = await redis_client.lrange(redis_key, 0, -1)
    
    contents = []
    for item in history_data:
        msg = json.loads(item)
        role = "model" if msg["role"] == "assistant" else "user"
        contents.append(
            types.Content(role=role, parts=[types.Part.from_text(text=msg["content"])])
        )
        
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
    
    try:
        response = await client.aio.models.generate_content(
            model='gemini-2.5-flash', # Обновлено до 2.0-flash
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.85, # Чуть повышаем температуру для разнообразия ответов
            )
        )
        ai_text = response.text
    except Exception as e:
        logging.error(f"Gemini API Error: {e}")
        ai_text = "инет тупит чет, не понял тебя"
        
    user_msg = {"role": "user", "content": text}
    ai_msg = {"role": "assistant", "content": ai_text}
    
    await redis_client.rpush(redis_key, json.dumps(user_msg))
    await redis_client.rpush(redis_key, json.dumps(ai_msg))
    await redis_client.ltrim(redis_key, -12, -1)
    
    return ai_text

async def clear_ai_context(user_id: int):
    """Очищает память ИИ после завершения чата"""
    await redis_client.delete(f"ai_context:{user_id}")