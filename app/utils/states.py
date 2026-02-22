from aiogram.fsm.state import State, StatesGroup

class ChatState(StatesGroup):
    menu = State()         # Находится в главном меню
    searching = State()    # Встал в очередь, ждет
    in_chat = State()      # Общается с собеседником (или ИИ)

class AdminState(StatesGroup):
    waiting_for_ban_id = State()
    waiting_for_vip_id = State()
    waiting_for_unban_id = State()