import datetime
import asyncio
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.filters.admin_filter import IsAdmin
from app.utils.states import AdminState
from app.keyboards.admin_kb import get_admin_main_kb, get_admin_cancel_kb
from app.database.models import User, Report, Transaction
from app.services.matchmaker import redis_client

# Подключаем роутер и вешаем на него фильтр IsAdmin(). 
# Теперь ни один хендлер в этом роутере не сработает для обычного юзера.
router = Router()
router.message.filter(IsAdmin())
router.callback_query.filter(IsAdmin())

@router.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("🛠 <b>Панель администратора</b>\nВыберите действие:", 
                         reply_markup=get_admin_main_kb(), 
                         parse_mode="HTML")

@router.callback_query(F.data == "admin_cancel")
async def admin_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Действие отменено. Вы вышли из режима администрирования.")
    await callback.answer()

# ==========================================
# СТАТИСТИКА (Сбор данных из PG и Redis)
# ==========================================
@router.callback_query(F.data == "admin_stats")
async def show_statistics(callback: CallbackQuery, session: AsyncSession):
    # 1. Данные из БД (PostgreSQL)
    total_users = await session.scalar(select(func.count(User.telegram_id)))
    
    now = datetime.datetime.utcnow()
    banned_users = await session.scalar(select(func.count(User.telegram_id)).where(User.ban_until > now))
    vip_users = await session.scalar(select(func.count(User.telegram_id)).where(User.vip_until > now))
    total_reports = await session.scalar(select(func.count(Report.id)))
    total_stars = await session.scalar(select(func.sum(Transaction.amount))) or 0
    
    # Добавь это в текст ответа:
    text = (
        f"📊 <b>Статистика:</b>\n\n"
        f"💰 Заработано всего: <b>{total_stars} ⭐️</b>\n"
        # ... остальной текст статистики ...
    )
    
    # 2. Данные реального времени (Redis)
    normal_q_len = await redis_client.llen("queue:normal")
    vip_q_len = await redis_client.llen("queue:vip")
    
    # Считаем количество активных чатов (грубый, но быстрый подсчет по ключам)
    # Используем scan для неблокирующего поиска ключей
    chat_keys = []
    cursor = '0'
    while cursor != 0:
        cursor, keys = await redis_client.scan(cursor=cursor, match='chat:*', count=100)
        chat_keys.extend(keys)
    
    # В Redis каждый чат это 2 ключа (chat:A=B и chat:B=A). Значит чатов = ключи / 2
    active_chats = len(chat_keys) // 2
    
    ai_chats_count = await redis_client.scard("ai_chats")

    text = (
        f"📊 <b>Статистика реального времени:</b>\n\n"
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"👑 Активных VIP: <b>{vip_users}</b>\n"
        f"🚫 В бане: <b>{banned_users}</b>\n"
        f"⚠️ Всего жалоб за все время: <b>{total_reports}</b>\n\n"
        f"⚡️ <b>Прямо сейчас (Redis):</b>\n"
        f"Очередь (обычная): <b>{normal_q_len}</b>\n"
        f"Очередь (VIP): <b>{vip_q_len}</b>\n"
        f"Активных чатов: <b>{active_chats}</b> (из них с ИИ: {ai_chats_count})"
    )
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=get_admin_main_kb())
    await callback.answer()

# ==========================================
# РУЧНОЙ БАН
# ==========================================
@router.callback_query(F.data == "admin_ban")
async def ask_ban_id(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.waiting_for_ban_id)
    await callback.message.edit_text("Введите <b>Telegram ID</b> пользователя для бана на 30 дней:", 
                                     parse_mode="HTML",
                                     reply_markup=get_admin_cancel_kb())
    await callback.answer()

@router.message(AdminState.waiting_for_ban_id)
async def process_ban_id(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    if not message.text.isdigit():
        await message.answer("ID должен состоять только из цифр. Попробуйте снова или нажмите Отмена.")
        return
        
    target_id = int(message.text)
    user = await session.scalar(select(User).where(User.telegram_id == target_id))
    
    if not user:
        await message.answer("Пользователь не найден в БД.", reply_markup=get_admin_main_kb())
    else:
        user.ban_until = datetime.datetime.utcnow() + datetime.timedelta(days=30)
        await session.commit()
        
        # Пытаемся уведомить пользователя
        try:
            await bot.send_message(target_id, "🚫 Администратор выдал вам блокировку на 30 дней.")
            # Тут также можно вызвать логику принудительного разрыва текущего чата через Redis
        except Exception:
            pass
            
        await message.answer(f"✅ Пользователь {target_id} успешно забанен на 30 дней.", reply_markup=get_admin_main_kb())
        
    await state.clear()

# ==========================================
# РАЗБАН ПОЛЬЗОВАТЕЛЯ
# ==========================================
@router.callback_query(F.data == "admin_unban")
async def ask_unban_id(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.waiting_for_unban_id)
    await callback.message.edit_text(
        "Введите <b>Telegram ID</b> пользователя для разбана:", 
        parse_mode="HTML",
        reply_markup=get_admin_cancel_kb() # Твоя кнопка "Отмена"
    )
    await callback.answer()

@router.message(AdminState.waiting_for_unban_id)
async def process_unban_id(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    if not message.text.isdigit():
        await message.answer("ID должен состоять только из цифр. Попробуйте снова или нажмите Отмена.")
        return
        
    target_id = int(message.text)
    user = await session.scalar(select(User).where(User.telegram_id == target_id))
    
    if not user:
        await message.answer("Пользователь не найден в БД.", reply_markup=get_admin_main_kb())
    else:
        # СНИМАЕМ ВСЕ ОГРАНИЧЕНИЯ И СБРАСЫВАЕМ ЖАЛОБЫ
        user.is_banned = False
        user.ban_until = None
        user.strikes = 0 
        
        await session.commit()
        
        # Радуем пользователя
        try:
            await bot.send_message(
                target_id, 
                "✅ <b>Ваш аккаунт был разблокирован администратором!</b>\n"
                "Вы снова можете искать собеседников. Пожалуйста, соблюдайте правила.", 
                parse_mode="HTML"
            )
        except Exception:
            pass # Если юзер заблокировал бота, просто игнорируем
            
        await message.answer(f"✅ Пользователь <code>{target_id}</code> успешно разбанен и его счетчик нарушений сброшен.", 
                             parse_mode="HTML", 
                             reply_markup=get_admin_main_kb())
        
    await state.clear()

# ==========================================
# ВЫДАЧА VIP
# ==========================================
@router.callback_query(F.data == "admin_vip")
async def ask_vip_id(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.waiting_for_vip_id)
    await callback.message.edit_text("Введите <b>Telegram ID</b> пользователя для выдачи VIP", 
                                     parse_mode="HTML",
                                     reply_markup=get_admin_cancel_kb())
    await callback.answer()

# ==========================================
# ВЫДАЧА / УПРАВЛЕНИЕ VIP
# ==========================================
@router.callback_query(F.data == "admin_vip")
async def ask_vip_id(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.waiting_for_vip_id)
    await callback.message.edit_text("Введите <b>Telegram ID</b> пользователя для управления VIP:", 
                                     parse_mode="HTML",
                                     reply_markup=get_admin_cancel_kb())
    await callback.answer()

@router.message(AdminState.waiting_for_vip_id)
async def process_vip_id(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text.isdigit():
        await message.answer("ID должен состоять только из цифр. Попробуйте снова или нажмите Отмена.")
        return
        
    target_id = int(message.text)
    user = await session.scalar(select(User).where(User.telegram_id == target_id))
    
    if not user:
        await message.answer("Пользователь не найден в БД.", reply_markup=get_admin_main_kb())
        await state.clear()
        return

    # Проверяем текущий статус
    now = datetime.datetime.utcnow()
    is_vip = user.vip_until and user.vip_until > now
    status_text = f"✅ Активен до {user.vip_until.strftime('%d.%m.%Y %H:%M')}" if is_vip else "❌ Неактивен"

    # Формируем клавиатуру с выбором срока
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    builder = InlineKeyboardBuilder()
    # callback_data формата: setvip_ДНИ_ID (0 = забрать, 9999 = навсегда)
    builder.button(text="❌ Аннулировать VIP", callback_data=f"setvip_0_{target_id}")
    builder.button(text="1 день", callback_data=f"setvip_1_{target_id}")
    builder.button(text="7 дней", callback_data=f"setvip_7_{target_id}")
    builder.button(text="30 дней", callback_data=f"setvip_30_{target_id}")
    builder.button(text="♾ Навсегда", callback_data=f"setvip_9999_{target_id}")
    builder.button(text="◀️ Назад", callback_data="admin_cancel")
    builder.adjust(1, 3, 1, 1) # Разметка: 1 кнопка, потом 3 в ряд, потом 1, потом 1

    await message.answer(
        f"Управление VIP для <code>{target_id}</code>\n"
        f"Текущий статус: <b>{status_text}</b>\n\n"
        f"Выберите действие:", 
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )
    await state.clear() # Сбрасываем стейт ввода текста

# Обработка нажатий на новые кнопки сроков
@router.callback_query(F.data.startswith("setvip_"))
async def process_set_vip_duration(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    # Разбираем callback: "setvip_30_123456789"
    parts = callback.data.split("_")
    days = int(parts[1])
    target_id = int(parts[2])
    
    user = await session.scalar(select(User).where(User.telegram_id == target_id))
    if not user:
        await callback.answer("Пользователь не найден!", show_alert=True)
        return
        
    now = datetime.datetime.utcnow()
    
    # 1. Если админ забирает VIP
    if days == 0:
        user.vip_until = None
        admin_msg = f"❌ VIP-статус у пользователя <code>{target_id}</code> успешно <b>аннулирован</b>."
        user_msg = "❌ Ваш VIP-статус был аннулирован администратором."
        
    # 2. Если VIP навсегда (ставим на 100 лет)
    elif days == 9999:
        user.vip_until = now + datetime.timedelta(days=36500)
        admin_msg = f"♾ Пользователю <code>{target_id}</code> выдан VIP <b>НАВСЕГДА</b>."
        user_msg = "👑 <b>Поздравляем!</b>\nАдминистратор выдал вам VIP-статус <b>НАВСЕГДА</b>!\nТеперь у вас нет ограничений на отправку медиа."
        
    # 3. Обычная выдача/продление на X дней
    else:
        current_vip = user.vip_until if (user.vip_until and user.vip_until > now) else now
        user.vip_until = current_vip + datetime.timedelta(days=days)
        admin_msg = f"✅ Пользователю <code>{target_id}</code> выдан/продлен VIP на <b>{days} дней</b>."
        user_msg = f"👑 <b>Поздравляем!</b>\nАдминистратор выдал вам VIP-статус на {days} дней!\nТеперь вы можете отправлять фото, видео и кружочки!"

    await session.commit()
    
    # Пытаемся уведомить пользователя
    try:
        await bot.send_message(target_id, user_msg, parse_mode="HTML")
    except Exception:
        pass # Игнорируем, если бот заблокирован у юзера
        
    # Возвращаем админа в главное меню
    await callback.message.edit_text(admin_msg, parse_mode="HTML", reply_markup=get_admin_main_kb())
    await callback.answer("Успешно!")

@router.callback_query(F.data.startswith("admin_approve_"))
async def approve_receipt(callback: CallbackQuery, session: AsyncSession, bot: Bot):
    # Парсим ID юзера из callback_data (admin_approve_123456)
    target_user_id = int(callback.data.split("_")[-1])
    
    user = await session.scalar(select(User).where(User.telegram_id == target_user_id))
    
    if user:
        # Выдаем VIP
        now = datetime.datetime.utcnow()
        current_vip = user.vip_until if (user.vip_until and user.vip_until > now) else now
        user.vip_until = current_vip + datetime.timedelta(days=30)
        
        # Сохраняем транзакцию для статистики (условно 150 звезд/рублей)
        new_tx = Transaction(user_id=target_user_id, amount=150)
        session.add(new_tx)
        
        await session.commit()
        
        # Радуем юзера
        try:
            await bot.send_message(
                target_user_id, 
                "🎉 <b>Оплата подтверждена!</b>\nАдминистратор проверил ваш чек. Вам выдан VIP-статус на 30 дней!\n\n<i>Теперь вы можете отправлять фото, видео и голосовые!</i>",
                parse_mode="HTML"
            )
        except:
            pass
            
    # Редактируем сообщение админа, убирая кнопки
    await callback.message.edit_caption(
        caption=f"✅ Вы выдали VIP пользователю {target_user_id}", 
        reply_markup=None
    )
    await callback.answer("VIP успешно выдан!")

@router.callback_query(F.data.startswith("admin_reject_"))
async def reject_receipt(callback: CallbackQuery, bot: Bot):
    target_user_id = int(callback.data.split("_")[-1])
    
    # Огорчаем юзера
    try:
        await bot.send_message(
            target_user_id, 
            "❌ <b>Оплата не найдена!</b>\nВаш чек был отклонен администратором. Если произошла ошибка, свяжитесь с поддержкой.",
            parse_mode="HTML"
        )
    except:
        pass
        
    # Редактируем сообщение админа
    await callback.message.edit_caption(
        caption=f"❌ Вы отклонили чек от пользователя {target_user_id}", 
        reply_markup=None
    )
    await callback.answer("Чек отклонен.")

# ==========================================
# ГЛОБАЛЬНАЯ РАССЫЛКА
# ==========================================
@router.callback_query(F.data == "admin_broadcast")
async def ask_broadcast_msg(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminState.waiting_for_broadcast_msg)
    await callback.message.edit_text(
        "📢 <b>Рассылка сообщений</b>\n\n"
        "Отправьте сюда сообщение, которое получат ВСЕ пользователи бота. "
        "<i>Можно использовать текст, фото, видео или кружочки.</i>\n\n"
        "Внимание: Рассылка начнется СРАЗУ после отправки сообщения!",
        parse_mode="HTML",
        reply_markup=get_admin_cancel_kb()
    )
    await callback.answer()

@router.message(AdminState.waiting_for_broadcast_msg)
async def process_broadcast_msg(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    await state.clear()
    
    # 1. Уведомляем админа
    status_msg = await message.answer("⏳ <i>Рассылка запущена... Пожалуйста, подождите.</i>", parse_mode="HTML")
    
    # 2. Выгружаем ID всех пользователей из БД
    result = await session.execute(select(User.telegram_id))
    users = result.scalars().all()
    
    success_count = 0
    failed_count = 0
    
    # --- ФОРМИРУЕМ НАДПИСЬ ОБЪЯВЛЕНИЯ ---
    prefix = "📢 <b>ОБЪЯВЛЕНИЕ</b>\n\n"
    # Сохраняем исходное форматирование админа (жирный текст, ссылки и т.д.)
    original_text = message.html_text or ""
    new_text = prefix + original_text
    
    # 3. Рассылаем
    for user_id in users:
        try:
            # Разбираем по типам контента, чтобы подставить новый текст в caption
            if message.content_type == 'text':
                await bot.send_message(chat_id=user_id, text=new_text, parse_mode="HTML")
                
            elif message.content_type == 'photo':
                await bot.send_photo(chat_id=user_id, photo=message.photo[-1].file_id, caption=new_text, parse_mode="HTML")
                
            elif message.content_type == 'video':
                await bot.send_video(chat_id=user_id, video=message.video.file_id, caption=new_text, parse_mode="HTML")
                
            elif message.content_type == 'document':
                await bot.send_document(chat_id=user_id, document=message.document.file_id, caption=new_text, parse_mode="HTML")
                
            elif message.content_type == 'voice':
                await bot.send_voice(chat_id=user_id, voice=message.voice.file_id, caption=new_text, parse_mode="HTML")
                
            elif message.content_type == 'video_note': 
                # Telegram "кружочки" не поддерживают текст под ними. 
                # Поэтому отправляем 2 сообщения: сначала плашку объявления, потом сам кружок.
                await bot.send_message(chat_id=user_id, text="📢 <b>ОБЪЯВЛЕНИЕ</b>", parse_mode="HTML")
                await bot.send_video_note(chat_id=user_id, video_note=message.video_note.file_id)
                
            else:
                # Фоллбек для стикеров, локаций, гифок и т.д.
                await bot.send_message(chat_id=user_id, text="📢 <b>ОБЪЯВЛЕНИЕ</b>", parse_mode="HTML")
                await message.send_copy(chat_id=user_id)

            success_count += 1
        except Exception:
            # Если юзер заблокировал бота или удалил аккаунт
            failed_count += 1
            
        # Защита от лимитов Telegram - ждем 0.05 сек (20 сообщений в секунду)
        await asyncio.sleep(0.05)
        
    # 4. Выводим отчет
    report_text = (
        f"📢 <b>Рассылка завершена!</b>\n\n"
        f"✅ Успешно доставлено: <b>{success_count}</b>\n"
        f"❌ Не доставлено (блокировки): <b>{failed_count}</b>\n"
        f"👥 Всего в базе: <b>{len(users)}</b>"
    )
    
    await status_msg.edit_text(report_text, parse_mode="HTML", reply_markup=get_admin_main_kb())