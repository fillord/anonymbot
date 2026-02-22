from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

def get_search_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="⛔ Отменить поиск")]],
        resize_keyboard=True
    )

def get_in_chat_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="➡️ Следующий собеседник")],
            [KeyboardButton(text="⛔ Завершить чат"), KeyboardButton(text="⚠️ Пожаловаться")]
        ],
        resize_keyboard=True
    )

def get_rating_kb(target_user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for i in range(1, 6):
        # callback_data формата: "rate_<оценка>_<id_собеседника>"
        builder.button(text=("⭐️" * i), callback_data=f"rate_{i}_{target_user_id}")
    builder.adjust(1) # По одной кнопке в ряд (или можно adjust(5) для горизонтального ряда)
    return builder.as_markup()

def get_report_reasons_kb(target_user_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    reasons = {
        "spam": " реклама/спам",
        "insult": "🤬 Оскорбления",
        "nsfw": "🔞 18+",
        "other": "❓ Другое"
    }
    
    for code, text in reasons.items():
        # Формат callback: rep_<причина>_<id_нарушителя>
        builder.button(text=text, callback_data=f"rep_{code}_{target_user_id}")
        
    builder.adjust(2) # По две кнопки в ряд
    return builder.as_markup()