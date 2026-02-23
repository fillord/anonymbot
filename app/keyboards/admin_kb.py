from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_admin_main_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="🔨 Выдать БАН", callback_data="admin_ban")
    builder.button(text="🔓 Разбанить", callback_data="admin_unban")
    builder.button(text="👑 Выдать VIP", callback_data="admin_vip")
    builder.button(text="📢 Рассылка", callback_data="admin_broadcast") # <--- НОВАЯ КНОПКА
    builder.button(text="❌ Отмена", callback_data="admin_cancel")
    # Красивая раскладка: 1 кнопка, 2 в ряд, 2 в ряд, 1 внизу
    builder.adjust(1, 2, 2, 1) 
    return builder.as_markup()

def get_admin_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="admin_cancel")
    return builder.as_markup()