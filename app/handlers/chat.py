import os
import datetime
import re
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext, StorageKey
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram.utils.keyboard import InlineKeyboardBuilder
from app.utils.states import ChatState
from app.keyboards.chat_kb import get_search_kb, get_in_chat_kb, get_rating_kb, get_report_reasons_kb
from app.handlers.menu import get_main_kb
from app.services.matchmaker import join_queue, leave_chat, is_in_chat, remove_from_queue, redis_client
from app.database.db import get_or_create_user, update_user_rating, add_report_and_check_ban
from app.services.ai_client import get_ai_response, clear_ai_context
from app.utils.name_generator import generate_random_name

router = Router()

# ==========================================
# 1. ВХОД В ОЧЕРЕДЬ
# ==========================================
@router.message(F.text == "🔍 Найти собеседника", ChatState.menu)
async def start_search(message: Message, state: FSMContext, session: AsyncSession):
    await state.set_state(ChatState.searching)
    await message.answer("🔍 Ищем собеседника...", reply_markup=get_search_kb())
    
    # Обновлено: get_or_create_user теперь возвращает 2 значения (user, ref_event)
    user, _ = await get_or_create_user(session, message.from_user.id)
    
    import datetime
    is_vip = user.vip_until and user.vip_until > datetime.datetime.utcnow()
    
    # Защита от старых юзеров: берем пол и кого ищем из базы
    user_gender = user.gender or "M"
    search_gender = user.search_gender or "any"
    
    # --- НАЗНАЧАЕМ ИМЯ ДЛЯ ТЕКУЩЕГО ЧАТА ---
    if is_vip and user.nickname:
        display_name = user.nickname
    else:
        display_name = generate_random_name()
    # Сохраняем имя в Redis на сутки (на случай долгих чатов)
    await redis_client.setex(f"display_name:{message.from_user.id}", 86400, display_name)

    # Вызываем обновленный матчмейкер с фильтрами пола
    partner_id, was_ai = await join_queue(
        message.from_user.id, 
        is_vip=is_vip, 
        user_gender=user_gender, 
        search_gender=search_gender
    )
    
    if partner_id:
        # Обязательно переводим ТЕКУЩЕГО пользователя в режим чата
        await state.set_state(ChatState.in_chat)
        
        if was_ai:
            # 1. Инициатору поиска отправляем стандартное приветствие
            await message.answer("✅ Собеседник найден! Поздоровайтесь.", reply_markup=get_in_chat_kb())
            
            # 2. Жертве ИИ отправляем сервисное уведомление (клавиатура у него уже есть)
            await message.bot.send_message(
                partner_id, 
                "⚠️ <i>У вашего собеседника возникла ошибка подключения, и мы переключили вас на другого пользователя. Продолжайте общение!</i>",
                parse_mode="HTML"
            )
        else:
            # Обычный коннект двух людей из очереди
            await message.answer("✅ Собеседник найден! Поздоровайтесь.", reply_markup=get_in_chat_kb())
            await message.bot.send_message(
                partner_id, 
                "✅ Собеседник найден! Поздоровайтесь.",
                reply_markup=get_in_chat_kb()
            )

@router.message(F.text == "⛔ Отменить поиск", ChatState.searching)
async def cancel_search(message: Message, state: FSMContext):
    await remove_from_queue(message.from_user.id) # Нужно добавить эту функцию в matchmaker.py (redis_client.lrem)
    await state.set_state(ChatState.menu)
    await message.answer("Поиск отменен.", reply_markup=get_main_kb())

# ==========================================
# 2. УПРАВЛЕНИЕ ЧАТОМ (ЗАВЕРШИТЬ / СЛЕДУЮЩИЙ)
# ==========================================
async def notify_partner_disconnect(bot: Bot, storage, partner_id: str, current_user_id: int):
    if partner_id and partner_id != "AI":
        partner_id_int = int(partner_id)
        
        # --- ИСПРАВЛЕНИЕ БАГА: Принудительно сбрасываем стейт собеседника ---
        state_key = StorageKey(bot_id=bot.id, chat_id=partner_id_int, user_id=partner_id_int)
        await storage.set_state(key=state_key, state=ChatState.menu)
        
        await bot.send_message(
            partner_id_int, 
            "Собеседник завершил чат. Оцените его:", 
            reply_markup=get_rating_kb(current_user_id)
        )
        await bot.send_message(partner_id_int, "Возврат в главное меню.", reply_markup=get_main_kb())

@router.message(F.text == "⛔ Завершить чат", ChatState.in_chat)
async def stop_chat(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    partner_id = await leave_chat(user_id)
    if partner_id == "AI":
        await clear_ai_context(user_id)
    await notify_partner_disconnect(bot, state.storage, partner_id, user_id)
    
    await state.set_state(ChatState.menu)
    if partner_id and partner_id != "AI":
        await message.answer("Чат завершен. Оцените собеседника:", reply_markup=get_rating_kb(int(partner_id)))
    
    await message.answer("Вы в главном меню.", reply_markup=get_main_kb())

@router.message(F.text == "➡️ Следующий собеседник", ChatState.in_chat)
async def next_chat(message: Message, state: FSMContext, bot: Bot, session: AsyncSession):
    # Логика: Завершаем текущий, не предлагая оценку (для скорости), и сразу в поиск
    user_id = message.from_user.id
    partner_id = await leave_chat(user_id)
    if partner_id == "AI":
        await clear_ai_context(user_id)
    await notify_partner_disconnect(bot, state.storage, partner_id, user_id)
    
    # Сразу запускаем поиск заново
    await start_search(message, state, session)

# ==========================================
# ИНИЦИАЦИЯ ЖАЛОБЫ (Кнопка в чате)
# ==========================================
@router.message(F.text == "⚠️ Пожаловаться", ChatState.in_chat)
async def init_report(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    
    # Узнаем с кем общался юзер и разрываем связь
    partner_id = await leave_chat(user_id)
    
    # Переводим инициатора в главное меню
    await state.set_state(ChatState.menu)
    
    if partner_id and partner_id != "AI":
        # Уведомляем собеседника о завершении чата (без упоминания жалобы, чтобы не провоцировать)
        partner_id_int = int(partner_id)
        await bot.send_message(partner_id_int, "Собеседник завершил чат.", reply_markup=get_main_kb())
        
        # Выдаем инициатору клавиатуру с выбором причины
        await message.answer(
            "Чат разорван. Укажите причину жалобы на собеседника:",
            reply_markup=get_report_reasons_kb(partner_id_int)
        )
    elif partner_id == "AI":
        # Если пожаловались на ИИ (бывает и такое)
        await message.answer("Чат завершен. Вы общались с нашим AI-помощником (он учится).", reply_markup=get_main_kb())
    else:
        await message.answer("Собеседник уже покинул чат.", reply_markup=get_main_kb())

# ==========================================
# ОБРАБОТКА ВЫБРАННОЙ ПРИЧИНЫ (Callback)
# ==========================================
@router.callback_query(F.data.startswith("rep_"))
async def process_report_reason(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    await callback.answer("Жалоба обрабатывается...")
    
    data = callback.data.split("_")
    reason = data[1]
    reported_id = int(data[2])
    reporter_id = callback.from_user.id
    
    ban_times = {
        10: 5 * 60,
        15: 30 * 60,
        20: 120 * 60,
        25: 1440 * 60
    }
    
    ban_info = await add_report_and_check_ban(session, reported_id, reporter_id, reason, ban_times)
    
    # Уведомляем админов
    admin_ids = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
    for admin_id in admin_ids:
        try:
            status = f"🚫 ЗАБАНЕН на {ban_info.get('duration', 0)} мин." if ban_info['is_banned'] else "⚠️ Предупреждение"
            if ban_info.get('permanent'): status = "🚫 ВЕЧНЫЙ БАН"
            await bot.send_message(
                admin_id,
                f"🚨 <b>Жалоба!</b>\nНа кого: <code>{reported_id}</code>\nПричина: {reason}\nСтатус: {status}",
                parse_mode="HTML"
            )
        except: pass

    # Убираем инлайн-кнопки (один раз!)
    await callback.message.edit_text("✅ Спасибо. Жалоба отправлена модераторам. Собеседник получил предупреждение.")
    
    # ИСПРАВЛЕНИЕ: Используем ban_info вместо несуществующей was_banned
    if ban_info.get('is_banned'):
        try:
            await bot.send_message(
                reported_id,
                "🚫 <b>Ваш аккаунт заблокирован!</b>\nВы получили слишком много жалоб. Доступ временно ограничен.",
                parse_mode="HTML",
                reply_markup=get_main_kb()
            )
            # Принудительно кикаем из поиска
            from app.services.matchmaker import remove_from_queue
            await remove_from_queue(reported_id)
        except Exception:
            pass

# ==========================================
# 3. МАРШРУТИЗАЦИЯ СООБЩЕНИЙ
# ==========================================

SPAM_PATTERN = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+|@\w+)", re.IGNORECASE)


@router.message()
async def route_message(message: Message, state: FSMContext, bot: Bot, session: AsyncSession):
    user_id = message.from_user.id
    partner_id = await is_in_chat(user_id)
    
    if not partner_id:
        current_state = await state.get_state()
        if current_state != ChatState.menu.state:
            await state.set_state(ChatState.menu)
            await message.answer("Чат завершен. Выберите действие:", reply_markup=get_main_kb())
        return
        
    current_state = await state.get_state()
    if current_state != ChatState.in_chat.state:
        await state.set_state(ChatState.in_chat)
    
    # ==========================================
    # 🛑 АНТИСПАМ-ФИЛЬТР
    # ==========================================
    text_to_check = message.text or message.caption
    if text_to_check and SPAM_PATTERN.search(text_to_check):
        await message.answer(
            "🚫 <b>Отправка ссылок запрещена!</b>\nВ целях безопасности мы блокируем любые ссылки и Telegram-юзернеймы.", 
            parse_mode="HTML"
        )
        return # Прерываем выполнение, сообщение не уйдет собеседнику

    # ==========================================
    # ФИЛЬТРАЦИЯ КОНТЕНТА (VIP СИСТЕМА)
    # ==========================================
    allowed_for_all = ['text', 'sticker', 'voice', 'animation'] # Добавили гифки
    
    if message.content_type not in allowed_for_all:
        user, _ = await get_or_create_user(session, user_id)
        import datetime
        is_vip = user.vip_until and user.vip_until > datetime.datetime.utcnow()
        
        if not is_vip:
            bot_info = await bot.get_me()
            ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
            share_url = f"https://t.me/share/url?url={ref_link}&text=Привет! Заходи общаться анонимно!"
            
            builder = InlineKeyboardBuilder()
            builder.button(text="💎 Купить VIP", callback_data="buy_vip_menu")
            builder.button(text="🔗 Отправить другу", url=share_url)
            builder.adjust(1)
            
            await message.answer(
                "⭐️ <b>Медиафайлы доступны только VIP!</b>\n\n"
                "Отправка фото, видео, кружочков и файлов доступна только обладателям VIP.\n\n"
                f"🔗 Ваша ссылка для приглашения 5 друзей:\n<code>{ref_link}</code>",
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
            return 

    # ==========================================
    # ОТПРАВКА СООБЩЕНИЯ С ИМЕНЕМ
    # ==========================================
    # Получаем имя отправителя
    sender_name = await redis_client.get(f"display_name:{user_id}") or "Аноним"
    prefix = f"👤 <b>{sender_name}</b>:\n"
    
    if partner_id == "AI":
        await bot.send_chat_action(chat_id=user_id, action="typing")
        text_to_ai = message.text or message.caption or "отправил медиа/стикер"
        
        from app.services.ai_client import get_ai_response
        ai_reply = await get_ai_response(user_id, text_to_ai)
        
        # Получаем имя ИИ
        ai_name = await redis_client.get(f"display_name:AI_{user_id}") or "Собеседник"
        await message.answer(f"👤 <b>{ai_name}</b>:\n{ai_reply}", parse_mode="HTML")
        
    else:
        try:
            original_text = message.html_text or ""
            new_text = prefix + original_text if original_text else prefix
            
            # Удаление EXIF из файлов
            if message.content_type == 'document' and message.document.mime_type and message.document.mime_type.startswith('image/'):
                file_info = await bot.get_file(message.document.file_id)
                file_bytes_io = await bot.download_file(file_info.file_path)
                from app.utils.security import strip_exif_data
                safe_bytes = strip_exif_data(file_bytes_io.read())
                
                from aiogram.types import BufferedInputFile
                input_file = BufferedInputFile(safe_bytes, filename=message.document.file_name or "safe_image.jpg")
                await bot.send_document(chat_id=int(partner_id), document=input_file, caption=new_text, parse_mode="HTML")
            
            # Маршрутизация по типам контента
            elif message.content_type == 'text':
                await bot.send_message(chat_id=int(partner_id), text=new_text, parse_mode="HTML")
            elif message.content_type == 'photo':
                await bot.send_photo(chat_id=int(partner_id), photo=message.photo[-1].file_id, caption=new_text, parse_mode="HTML")
            elif message.content_type == 'video':
                await bot.send_video(chat_id=int(partner_id), video=message.video.file_id, caption=new_text, parse_mode="HTML")
            elif message.content_type == 'voice':
                await bot.send_voice(chat_id=int(partner_id), voice=message.voice.file_id, caption=new_text, parse_mode="HTML")
            elif message.content_type == 'video_note': 
                await bot.send_message(chat_id=int(partner_id), text=prefix, parse_mode="HTML")
                await bot.send_video_note(chat_id=int(partner_id), video_note=message.video_note.file_id)
            else:
                # Только для стикеров и GIF-ок
                await bot.send_message(chat_id=int(partner_id), text=prefix, parse_mode="HTML")
                await message.send_copy(chat_id=int(partner_id))
                
        except Exception as e:
            import logging
            logging.error(f"Routing error: {e}")
            await leave_chat(user_id)
            
            await bot.send_message(int(partner_id), "Собеседник отключился.", reply_markup=get_main_kb())
            from aiogram.fsm.storage.base import StorageKey
            state_key = StorageKey(bot_id=bot.id, chat_id=int(partner_id), user_id=int(partner_id))
            await state.storage.set_state(key=state_key, state=ChatState.menu)
            
            await message.answer("Собеседник отключился.", reply_markup=get_main_kb())

# ==========================================
# 4. ОБРАБОТКА ОЦЕНОК (CALLBACK)
# ==========================================
@router.callback_query(F.data.startswith("rate_"))
async def process_rating(callback: CallbackQuery, session: AsyncSession):
    # Парсим: rate_5_123456789
    _, score_str, target_id_str = callback.data.split("_")
    score = int(score_str)
    target_id = int(target_id_str)
    
    await update_user_rating(session, target_id, score)
    
    # Удаляем инлайн-клавиатуру, чтобы нельзя было голосовать дважды
    await callback.message.edit_text(f"✅ Вы оценили собеседника на {score} звезд. Спасибо!")
    await callback.answer()

async def start_dialog(bot: Bot, user1: int, user2: int, state: FSMContext):
    # Эта функция вызывается, когда matchmaker спарил двоих
    await state.set_state(ChatState.in_chat)
    # Получаем рейтинг собеседника (в реале берем из БД)
    await bot.send_message(user1, "✅ Собеседник найден! Поздоровайтесь.", reply_markup=get_in_chat_kb())
    
    # Важно: В aiogram 3 стейты хранятся в storage (Redis). 
    # Чтобы установить стейт ВТОРОМУ пользователю, нужен доступ к storage.
    # В рамках MVP мы полагаемся на то, что `route_message` проверяет `is_in_chat(user_id)`.

