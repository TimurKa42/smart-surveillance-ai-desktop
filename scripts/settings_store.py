# =============================================================================
# Smart Surveillance AI
# © 2026 Тимур Каленик (Timur Kalenik Corporation) <timurkalenik950@gmail.com>
# Автор: Тимур Каленик. Усі права захищено.
#
# Проєкт створено для конкурсу МАН. Вихідний код відкрито для ознайомлення
# та оцінювання журі. Копіювання, поширення, зміна та використання коду
# (повністю або частково) без письмової згоди автора та без зазначення
# авторства заборонені. Повні умови — у файлі LICENSE.
# =============================================================================

"""
settings_store.py

Зберігає невеликі налаштування користувача (тема оформлення, мова
інтерфейсу) у файл settings.json поруч з програмою. Завдяки цьому
вибір користувача не скидається після перезапуску програми.

Файл максимально простий і без сторонніх бібліотек - лише json + os,
щоб не додавати зайву залежність заради двох налаштувань.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import json
import os

import app_paths

# У зібраній програмі (.app/.exe) файл лежить у теці даних користувача
# (macOS: ~/Library/Application Support/SmartSurveillanceAI), а не всередині
# збірки. Під час розробки - як і раніше, поруч зі скриптами.
SETTINGS_PATH = os.path.join(app_paths.user_data_dir(), "settings.json")

# "theme": "auto" - слідувати за системною темою (Windows/macOS/Linux),
#          "light" / "dark" - примусово світла чи темна, незалежно від
#          того, яку тему потім поставлять в системі.
# "language": "ua" - українська (за замовчуванням), "en" - англійська.
# "detail_level_index": позиція слайдера режиму "Детально" (0..3 -
#          Слабкий/Середній/Високий/Максимальний, див. DETAIL_LEVELS
#          у main.py). За замовчуванням 1 - "Середній".
# "remember_detail_level": чи зберігати detail_level_index між
#          запусками програми (чекбокс "Запам'ятати налаштування").
#          За замовчуванням False - слайдер щоразу скидається на
#          "Середній", а не тягне забутий рівень з минулого разу.
# "api_tier": тип API-ключа Gemini - "free" (безкоштовний), "paid"
#          (платний) або None (ще не обрано). ВАЖЛИВО: load_settings()
#          читає ЛИШЕ ключі, які є в цьому словнику, тому без цього
#          поля збережений тип ключа губився при кожному запуску.
DEFAULT_SETTINGS = {
    "theme": "auto",
    "language": "ua",
    "detail_level_index": 1,
    "remember_detail_level": False,
    "api_tier": None,
}


def load_settings():
    """Читає settings.json. Якщо файлу нема або він пошкоджений - тихо повертає значення за замовчуванням."""
    if not os.path.exists(SETTINGS_PATH):
        return DEFAULT_SETTINGS.copy()

    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SETTINGS.copy()

    settings = DEFAULT_SETTINGS.copy()
    for key in DEFAULT_SETTINGS:
        if key in data:
            settings[key] = data[key]
    return settings


def save_settings(settings):
    """Зберігає налаштування у settings.json. Помилки запису ігноруємо - це не критично для роботи програми."""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
