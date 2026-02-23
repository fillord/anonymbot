import datetime
import logging
import os
from aiogram import Router, F, Bot
from aiogram.types import Message, CallbackQuery, LabeledPrice, PreCheckoutQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart, CommandObject, Command  # ВОТ ЭТОГО НЕ ХВАТАЛО
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


# Наши внутренние модули
from app.database.models import User, Transaction
from app.database.db import get_or_create_user
from app.utils.states import ChatState, RegState, SettingsState
from app.services.matchmaker import redis_client
from app.services.ai_client import clear_ai_context

router = Router()

# Главная клавиатура
def get_main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🔍 Найти собеседника")],
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="👑 VIP статус")],
            [KeyboardButton(text="⚙️ Настройки"), KeyboardButton(text="🆘 Помощь")] # <--- Изменили эту строку
        ],
        resize_keyboard=True
    )

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, session: AsyncSession):
    referrer_id = int(command.args) if command.args and command.args.isdigit() else None
    
    # Теперь функция возвращает 2 значения
    user, ref_event = await get_or_create_user(session, message.from_user.id, referrer_id)

    # Отправляем ПУШ-УВЕДОМЛЕНИЕ тому, кто пригласил
    if ref_event:
        try:
            if ref_event["bonus"]:
                await message.bot.send_message(
                    ref_event["id"], 
                    f"🎉 <b>Новый реферал!</b> (Всего: {ref_event['count']})\n🎁 <b>Поздравляем!</b> Достигнута цель. Вам начислен VIP на 3 дня!",
                    parse_mode="HTML"
                )
            else:
                await message.bot.send_message(
                    ref_event["id"], 
                    f"👤 <b>Новый реферал!</b> По вашей ссылке зарегистрировался человек. (Всего: {ref_event['count']})",
                    parse_mode="HTML"
                )
        except: pass

    # ОНБОРДИНГ (Если нет пола или возраста)
    if not user.gender:
        await state.set_state(RegState.gender)
        builder = InlineKeyboardBuilder()
        builder.button(text="👨 Парень", callback_data="setgen_M")
        builder.button(text="👩 Девушка", callback_data="setgen_F")
        await message.answer("👋 Добро пожаловать! Для начала укажите ваш пол:", reply_markup=builder.as_markup())
        return

    await state.set_state(ChatState.menu)
    await message.answer("👋 Добро пожаловать в Анонимный Чат!\nЖмите «🔍 Найти собеседника», чтобы начать!", reply_markup=get_main_kb())

# Обработка выбора пола
@router.callback_query(RegState.gender, F.data.startswith("setgen_"))
async def process_gender(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    gender = callback.data.split("_")[1]
    user, _ = await get_or_create_user(session, message.from_user.id)
    user.gender = gender
    await session.commit()
    
    await state.set_state(RegState.age)
    await callback.message.edit_text("Отлично! Теперь напишите ваш возраст (цифрой, например 20):")

# Обработка ввода возраста
@router.message(RegState.age)
async def process_age(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text.isdigit() or not (12 <= int(message.text) <= 99):
        await message.answer("Пожалуйста, введите реальный возраст цифрами (от 12 до 99).")
        return
        
    user, _ = await get_or_create_user(session, message.from_user.id)
    user.age = int(message.text)
    await session.commit()
    
    await state.set_state(ChatState.menu)
    await message.answer("✅ Регистрация завершена! Приятного общения.", reply_markup=get_main_kb())

@router.message(F.text == "👤 Профиль", ChatState.menu)
async def show_profile(message: Message, session: AsyncSession):
    user, _ = await get_or_create_user(session, message.from_user.id)
    
    import datetime
    now = datetime.datetime.utcnow()
    is_vip = user.vip_until and user.vip_until > now
    status = "👑 VIP" if is_vip else "Обычный"
    
    # Проверяем сброс лимита смены ника (30 дней)
    if user.last_nickname_change and (now - user.last_nickname_change).days >= 30:
        user.nickname_changes = 0
        await session.commit()
    
    changes_left = max(0, 20 - user.nickname_changes)
    nick_display = user.nickname if user.nickname else "Не установлен (Случайный)"
    
    bot_info = await message.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={message.from_user.id}"

    gender_emoji = "👨" if user.gender == "M" else "👩"
    filter_text = {"M": "Парни 👨", "F": "Девушки 👩", "any": "Все 🌍"}.get(user.search_gender, "Все")
    
    text = (
        f"👤 <b>Ваш профиль:</b>\n\n"
        f"Указано: {gender_emoji} | {user.age} лет\n"
        f"⭐️ Рейтинг: <b>{user.rating:.1f}/5.0</b>\n"
        f"⚡️ Статус: <b>{status}</b>\n"
        f"🎯 Поиск: <b>{filter_text}</b>\n\n"
        f"🔗 <b>Ваша ссылка:</b>\n<code>{ref_link}</code>\n"
        f"<i>Приглашено друзей: {user.referrals_count}</i>"
    )
    
    builder = InlineKeyboardBuilder()
    if is_vip:
        builder.button(text="🎯 Настроить фильтр пола", callback_data="change_filter")
        # Твоя кнопка смены ника тут же
        builder.button(text=f"✏️ Изменить ник", callback_data="change_nickname")
        builder.adjust(1)
    
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup() if is_vip else None)
# Добавь хендлер для изменения фильтра
@router.callback_query(F.data == "change_filter")
async def change_filter_menu(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="Искать парней 👨", callback_data="setfilter_M")
    builder.button(text="Искать девушек 👩", callback_data="setfilter_F")
    builder.button(text="Искать всех 🌍", callback_data="setfilter_any")
    builder.adjust(1)
    await callback.message.edit_text("🎯 Кого вы хотите искать?", reply_markup=builder.as_markup())

@router.callback_query(F.data.startswith("setfilter_"))
async def process_set_filter(callback: CallbackQuery, session: AsyncSession):
    target = callback.data.split("_")[1]
    user, _ = await get_or_create_user(session, callback.from_user.id)
    user.search_gender = target
    await session.commit()
    await callback.message.edit_text("✅ Фильтр поиска успешно обновлен!")

# 2. Добавь обработчики ниже:
@router.callback_query(F.data == "change_nickname")
async def start_change_nickname(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    user, _ = await get_or_create_user(session, callback.from_user.id)
    
    if user.nickname_changes >= 20:
        await callback.answer("Вы исчерпали лимит в 20 смен ника за месяц!", show_alert=True)
        return
        
    await state.set_state(ChatState.waiting_for_nickname)
    await callback.message.edit_text(
        "📝 <b>Ввод никнейма</b>\n\n"
        "Введите желаемый никнейм (до 15 символов). Он будет отображаться у ваших собеседников.\n"
        "<i>Или отправьте /start для отмены.</i>",
        parse_mode="HTML"
    )

@router.message(ChatState.waiting_for_nickname)
async def process_new_nickname(message: Message, state: FSMContext, session: AsyncSession):
    await state.set_state(ChatState.menu)
    new_nick = message.text.strip()
    
    if len(new_nick) > 15:
        await message.answer("❌ Никнейм слишком длинный. Максимум 15 символов.", reply_markup=get_main_kb())
        return
        
    user, _ = await get_or_create_user(session, message.from_user.id)
    
    user.nickname = new_nick
    user.nickname_changes += 1
    user.last_nickname_change = datetime.datetime.utcnow()
    await session.commit()
    
    await message.answer(f"✅ Никнейм успешно изменен на <b>{new_nick}</b>!", parse_mode="HTML", reply_markup=get_main_kb())

@router.message(F.text == "👑 VIP статус", ChatState.menu)
async def show_vip_info(message: Message, session: AsyncSession):
    # Проверяем текущий статус пользователя
    user, _ = await get_or_create_user(session, message.from_user.id)
    
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
    builder.button(text="💎 Купить за 50 ⭐️", callback_data="buy_vip_menu")
    
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

# ==========================================
# 1. ОТПРАВКА СЧЕТА НА ОПЛАТУ (INVOICE)
# ==========================================
@router.callback_query(F.data == "buy_vip_menu")
async def show_vip_purchase_menu(callback: CallbackQuery):
    builder = InlineKeyboardBuilder()
    builder.button(text="⭐️ Telegram Stars (Автоматически)", callback_data="pay_stars")
    builder.button(text="💳 Перевод на карту (Ручной)", callback_data="pay_card")
    builder.adjust(1)
    
    await callback.message.edit_text(
        "Выберите удобный способ оплаты VIP-статуса (30 дней):\n"
        "Стоимость: <b>150 рублей</b> или <b>50 ⭐️</b>",
        parse_mode="HTML",
        reply_markup=builder.as_markup()
    )

@router.callback_query(F.data == "pay_stars")
async def pay_via_stars(callback: CallbackQuery):
    await callback.message.delete()
    prices = [LabeledPrice(label="VIP на 30 дней", amount=50)]
    await callback.message.answer_invoice(
        title="💎 VIP-статус (30 дней)",
        description="Оплата через Telegram Stars",
        payload="vip_30_days_payload",
        provider_token="",
        currency="XTR",
        prices=prices
    )

@router.callback_query(F.data == "pay_card")
async def pay_via_card(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ChatState.waiting_for_receipt)
    
    text = (
        "💳 <b>Оплата переводом на карту</b>\n\n"
        "Переведите <b>150 рублей</b> по номеру карты:\n"
        "<code>2202 2000 1234 5678</code> (Сбер / Т-Банк)\n"
        "Получатель: <i>Александр А.</i>\n\n"
        "📸 <b>Сразу после перевода отправьте скриншот чека (фото) или PDF-файл в этот чат.</b>\n"
        "<i>Или нажмите /start для отмены.</i>"
    )
    await callback.message.edit_text(text, parse_mode="HTML")

@router.message(ChatState.waiting_for_receipt, F.photo | F.document)
async def process_receipt_file(message: Message, state: FSMContext, bot: Bot):
    # Возвращаем пользователя в главное меню
    await state.set_state(ChatState.menu)
    user_id = message.from_user.id
    
    # Клавиатура для админа
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Выдать VIP", callback_data=f"admin_approve_{user_id}")
    builder.button(text="❌ Отклонить", callback_data=f"admin_reject_{user_id}")
    builder.adjust(2)
    
    admin_ids = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]
    caption_text = f"💳 <b>Новый чек на оплату VIP!</b>\nОт пользователя: <code>{user_id}</code>"
    
    for admin_id in admin_ids:
        try:
            # Если юзер прислал фото (скриншот)
            if message.photo:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=message.photo[-1].file_id, # Берем фото в лучшем качестве
                    caption=caption_text,
                    parse_mode="HTML",
                    reply_markup=builder.as_markup()
                )
            # Если юзер прислал файл (например, PDF чек)
            elif message.document:
                await bot.send_document(
                    chat_id=admin_id,
                    document=message.document.file_id,
                    caption=caption_text,
                    parse_mode="HTML",
                    reply_markup=builder.as_markup()
                )
        except Exception as e:
            # Игнорируем ошибки (например, если админ заблокировал бота)
            pass
            
    await message.answer(
        "✅ Чек успешно отправлен!\nМодератор проверит его в течение 5-15 минут, и вам придет уведомление.",
        reply_markup=get_main_kb() # Возвращаем основную клавиатуру
    )

# Заглушка, если юзер прислал текст, стикер, голосовое и т.д.
@router.message(ChatState.waiting_for_receipt)
async def process_receipt_wrong_format(message: Message):
    await message.answer(
        "⚠️ Пожалуйста, отправьте <b>скриншот чека (фотографию) или PDF-файл документа</b>.\n"
        "Если хотите отменить оплату, просто нажмите /start", 
        parse_mode="HTML"
    )

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
        user, _ = await get_or_create_user(session, user_id)
        
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

# ==========================================
# МЕНЮ НАСТРОЕК
# ==========================================
@router.message(F.text == "⚙️ Настройки", ChatState.menu)
async def show_settings(message: Message, session: AsyncSession):
    user, _ = await get_or_create_user(session, message.from_user.id)
    
    gender_str = "👨 Парень" if user.gender == "M" else "👩 Девушка" if user.gender == "F" else "Не указан"
    
    text = (
        f"⚙️ <b>Настройки профиля</b>\n\n"
        f"Текущий пол: <b>{gender_str}</b>\n"
        f"Текущий возраст: <b>{user.age or 'Не указан'}</b>\n\n"
        "Выберите, что хотите изменить:"
    )
    
    builder = InlineKeyboardBuilder()
    builder.button(text="👤 Изменить пол", callback_data="settings_gender")
    builder.button(text="📅 Изменить возраст", callback_data="settings_age")
    builder.button(text="🧹 Очистить память ИИ", callback_data="settings_clear_ai")
    builder.adjust(1)
    
    await message.answer(text, parse_mode="HTML", reply_markup=builder.as_markup())

# --- ИЗМЕНЕНИЕ ПОЛА ---
@router.callback_query(F.data == "settings_gender")
async def change_gender_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_gender)
    builder = InlineKeyboardBuilder()
    builder.button(text="👨 Парень", callback_data="setnewgen_M")
    builder.button(text="👩 Девушка", callback_data="setnewgen_F")
    await callback.message.edit_text("Выберите ваш пол:", reply_markup=builder.as_markup())

@router.callback_query(SettingsState.waiting_for_gender, F.data.startswith("setnewgen_"))
async def process_new_gender(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
    new_gender = callback.data.split("_")[1]
    
    # 1. Обновляем в PostgreSQL
    user, _ = await get_or_create_user(session, callback.from_user.id)
    user.gender = new_gender
    await session.commit()
    
    # 2. Обновляем в кэше Redis (ВАЖНО для матчмейкера!)
    await redis_client.hset(f"user_prefs:{callback.from_user.id}", "g", new_gender)
    
    await state.set_state(ChatState.menu)
    await callback.message.edit_text(f"✅ Ваш пол успешно изменен!")

# --- ИЗМЕНЕНИЕ ВОЗРАСТА ---
@router.callback_query(F.data == "settings_age")
async def change_age_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(SettingsState.waiting_for_age)
    await callback.message.edit_text("Введите ваш новый возраст (цифрами, например 20):")

@router.message(SettingsState.waiting_for_age)
async def process_new_age(message: Message, state: FSMContext, session: AsyncSession):
    if not message.text.isdigit() or not (12 <= int(message.text) <= 99):
        await message.answer("Пожалуйста, введите реальный возраст цифрами (от 12 до 99).")
        return
        
    user, _ = await get_or_create_user(session, message.from_user.id)
    user.age = int(message.text)
    await session.commit()
    
    await state.set_state(ChatState.menu)
    await message.answer("✅ Ваш возраст успешно изменен!", reply_markup=get_main_kb())

# --- ОЧИСТКА ИИ ---
@router.callback_query(F.data == "settings_clear_ai")
async def clear_ai_memory(callback: CallbackQuery):
    await clear_ai_context(callback.from_user.id)
    # show_alert=True покажет всплывающее окошко поверх Telegram
    await callback.answer("🧹 Память ИИ для вас успешно очищена!", show_alert=True)