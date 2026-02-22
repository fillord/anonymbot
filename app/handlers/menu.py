import datetime
import logging
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import CommandStart, CommandObject, Command  # ВОТ ЭТОГО НЕ ХВАТАЛО
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# Наши внутренние модули
from app.database.models import User, Transaction
from app.database.db import get_or_create_user
from app.utils.states import ChatState
import os

router = Router()

# Главная клавиатура
def get_main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Найти собеседника")],
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="👑 VIP статус")],
            [KeyboardButton(text="🆘 Помощь")]
        ],
        resize_keyboard=True
    )

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, session: AsyncSession):
    # Парсим реферальный код (например: t.me/bot?start=123456789)
    referrer_id = None
    if command.args and command.args.isdigit():
        referrer_id = int(command.args)

    # Регистрируем или получаем юзера
    user = await get_or_create_user(session, message.from_user.id, referrer_id)

    await state.set_state(ChatState.menu)
    await message.answer(
        "👋 Добро пожаловать в Анонимный Чат!\n\n"
        "Здесь вы можете общаться абсолютно анонимно. Мы не сохраняем историю переписки.\n"
        "Жмите «🔍 Найти собеседника», чтобы начать!",
        reply_markup=get_main_kb()
    )

@router.message(F.text == "👤 Профиль", ChatState.menu)
async def show_profile(message: Message, session: AsyncSession):
    # Получаем актуальные данные
    user = await get_or_create_user(session, message.from_user.id)
    
    # Формируем статус
    import datetime
    is_vip = user.vip_until and user.vip_until > datetime.datetime.utcnow()
    status = "👑 VIP" if is_vip else "Обычный"
    
    # Ссылка для приглашений
    bot_info = await message.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"

    text = (
        f"👤 <b>Ваш профиль:</b>\n\n"
        f"⭐️ Рейтинг: <b>{user.rating:.1f}/5.0</b> (Оценок: {user.rating_count})\n"
        f"⚡️ Статус: <b>{status}</b>\n\n"
        f"🔗 <b>Ваша пригласительная ссылка:</b>\n<code>{ref_link}</code>\n\n"
        f"<i>Пригласите 5 друзей и получите VIP на 3 дня! (Приглашено: {user.referrals_count})</i>"
    )
    
    await message.answer(text, parse_mode="HTML")

@router.message(F.text == "👑 VIP статус", ChatState.menu)
async def show_vip_info(message: Message, session: AsyncSession):
    # Проверяем текущий статус пользователя
    user = await get_or_create_user(session, message.from_user.id)
    
    import datetime
    now = datetime.datetime.utcnow()
    is_vip = user.vip_until and user.vip_until > now
    
    if is_vip:
        status_text = f"✅ <b>Активен до:</b> {user.vip_until.strftime('%d.%m.%Y %H:%M')} (UTC)"
    else:
        status_text = "❌ <b>Неактивен</b>"

    text = (
        f"👑 <b>Управление VIP-статусом</b>\n\n"
        f"Ваш статус: {status_text}\n\n"
        "<b>Преимущества VIP:</b>\n"
        "🖼 Отправка фото, видео, кружочков и файлов\n"
        "🚀 Приоритет в поиске собеседника\n"
        "⭐️ Поддержка развития проекта\n\n"
        "<i>Вы можете получить VIP бесплатно, пригласив 5 друзей (ссылка в Профиле), либо приобрести его прямо сейчас за Telegram Stars!</i>"
    )

    # Создаем инлайн-кнопку, которая вызывает наш уже готовый инвойс
    builder = InlineKeyboardBuilder()
    builder.button(text="💎 Купить за 1 ⭐️", callback_data="buy_vip_menu")
    
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

# ==========================================
# 1. ОТПРАВКА СЧЕТА НА ОПЛАТУ (INVOICE)
# ==========================================
@router.callback_query(F.data == "buy_vip_menu")
async def show_vip_purchase_menu(callback: CallbackQuery):
    # Удаляем предыдущее сообщение, чтобы не засорять чат
    await callback.message.delete()
    
    # Цена: 50 звезд (можешь изменить на свое усмотрение)
    prices = [LabeledPrice(label="VIP на 30 дней", amount=1)]
    
    # Отправляем инвойс
    await callback.message.answer_invoice(
        title="💎 VIP-статус (30 дней)",
        description="Снятие всех ограничений:\n🖼 Отправка фото, видео, кружочков\n🚀 Приоритет в поиске\n⭐️ Уникальный статус",
        payload="vip_30_days_payload",
        provider_token="", # ПУСТОЙ токен означает оплату через Telegram Stars
        currency="XTR",    # Валюта: Звезды
        prices=prices
    )
    await callback.answer()

# ==========================================
# 2. ПОДТВЕРЖДЕНИЕ ГОТОВНОСТИ К ТРАНЗАКЦИИ
# ==========================================
@router.pre_checkout_query(F.invoice_payload == "vip_30_days_payload")
async def process_pre_checkout(pre_checkout_query: PreCheckoutQuery):
    # Сообщаем серверам Telegram, что мы готовы принять платеж
    await pre_checkout_query.answer(ok=True)

# ==========================================
# 3. УСПЕШНАЯ ОПЛАТА И ВЫДАЧА ТОВАРА
# ==========================================
@router.message(F.successful_payment)
async def process_successful_payment(message: Message, session: AsyncSession, bot: Bot):
    payment_info = message.successful_payment
    user_id = message.from_user.id
    
    if payment_info.invoice_payload == "vip_30_days_payload":
        # 1. Гарантированно получаем юзера из БД
        user = await get_or_create_user(session, user_id)
        
        # 2. Обновляем статус
        import datetime
        now = datetime.datetime.utcnow()
        current_vip = user.vip_until if (user.vip_until and user.vip_until > now) else now
        user.vip_until = current_vip + datetime.timedelta(days=30)
        
        # 3. Безопасное сохранение транзакции
        try:
            from app.database.models import Transaction
            new_tx = Transaction(user_id=user_id, amount=payment_info.total_amount)
            session.add(new_tx)
            await session.commit()
        except Exception as e:
            # Если таблицы нет, откатываем ошибку и сохраняем хотя бы VIP
            await session.rollback()
            user.vip_until = current_vip + datetime.timedelta(days=30)
            await session.commit()
            print(f"Ошибка сохранения транзакции: {e}")

        # 4. Уведомляем админа (ИСПРАВЛЕНЫ ТЕГИ)
        import os
        admin_ids = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
        for admin_id in admin_ids:
            try:
                await bot.send_message(
                    admin_id, 
                    f"💰 <b>Новая продажа!</b>\nПользователь <code>{user_id}</code> купил VIP за {payment_info.total_amount} ⭐️",
                    parse_mode="HTML"  # <--- ИСПРАВЛЕНИЕ
                )
            except: pass
        
        await message.answer("🎉 <b>VIP начислен!</b> Спасибо за поддержку.", parse_mode="HTML")