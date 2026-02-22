from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_admin_main_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📊 Статистика", callback_data="admin_stats")
    builder.button(text="🔨 Выдать БАН", callback_data="admin_ban")
    builder.button(text="🔓 Разбанить", callback_data="admin_unban")
    builder.button(text="👑 Выдать VIP", callback_data="admin_vip")
    builder.button(text="❌ Отмена", callback_data="admin_cancel")
    builder.adjust(1, 2, 2) # 1 кнопка в 1 ряду, 2 во втором, 1 в третьем
    return builder.as_markup()

def get_admin_cancel_kb() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="admin_cancel")
    return builder.as_markup()