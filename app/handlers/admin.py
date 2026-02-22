import datetime
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
    await callback.message.edit_text("Введите <b>Telegram ID</b> пользователя для выдачи VIP на 30 дней:", 
                                     parse_mode="HTML",
                                     reply_markup=get_admin_cancel_kb())
    await callback.answer()

@router.message(AdminState.waiting_for_vip_id)
async def process_vip_id(message: Message, state: FSMContext, session: AsyncSession, bot: Bot):
    if not message.text.isdigit():
        await message.answer("ID должен состоять только из цифр. Попробуйте снова или нажмите Отмена.")
        return
        
    target_id = int(message.text)
    user = await session.scalar(select(User).where(User.telegram_id == target_id))
    
    if not user:
        await message.answer("Пользователь не найден в БД.", reply_markup=get_admin_main_kb())
    else:
        now = datetime.datetime.utcnow()
        # Если VIP уже активен, прибавляем к нему. Если нет - отсчитываем от сейчас
        current_vip = user.vip_until if (user.vip_until and user.vip_until > now) else now
        user.vip_until = current_vip + datetime.timedelta(days=30)
        
        await session.commit()
        
        # Пытаемся уведомить счастливого пользователя (если он не заблокировал бота)
        try:
            await bot.send_message(
                target_id, 
                "👑 <b>Поздравляем!</b>\nАдминистратор выдал вам VIP-статус на 30 дней.\n"
                "Теперь вы можете отправлять фото, видео и кружочки!", 
                parse_mode="HTML"
            )
        except Exception:
            pass
            
        await message.answer(f"✅ Пользователю {target_id} успешно выдан VIP на 30 дней.", reply_markup=get_admin_main_kb())
        
    await state.clear()

