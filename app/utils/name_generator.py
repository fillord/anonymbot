# app/utils/name_generator.py
import random

ADJECTIVES = ["Тайный", "Хитрый", "Спящий", "Бодрый", "Дикий", "Милый", "Дерзкий", "Грустный", "Веселый", "Одинокий"]
NOUNS = ["Енот", "Кот", "Волк", "Лис", "Панда", "Барсук", "Тигр", "Хомяк", "Соболь", "Медведь"]

def generate_random_name() -> str:
    return f"{random.choice(ADJECTIVES)} {random.choice(NOUNS)}"