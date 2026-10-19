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

Зберігає налаштування (тема, мова тощо) у файлі settings.json.
Використовуємо тільки json і os, без зайвих бібліотек.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import json
import os

import app_paths

# У збірці файл лежить у папці даних користувача, у PyCharm - поруч зі скриптами
SETTINGS_PATH = os.path.join(app_paths.user_data_dir(), "settings.json")

# "theme": "auto" - як у системі, "light" / "dark" - завжди така тема
# "language": "ua" або "en"
# "detail_level_index": рівень слайдера "Детально" (0-3), 1 = Середній
# "remember_detail_level": чи запам'ятовувати рівень між запусками
# "api_tier": тип свого ключа - "free", "paid" або None (ще не вибрано).
#            Вшитий ключ завжди free.
# "welcome_shown": чи вже показали вітальне вікно
# Ключі, яких немає в цьому словнику, load_settings() не читає - тому всі поля тут
DEFAULT_SETTINGS = {
    "theme": "auto",
    "language": "ua",
    "detail_level_index": 1,
    "remember_detail_level": False,
    "api_tier": None,
    "welcome_shown": False,
}


def load_settings():
    """
    Читає settings.json. Якщо файлу нема або він зіпсований,
    повертає значення за замовчуванням.
    """
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
    """Зберігає налаштування. Помилки запису ігноруємо, це не критично."""
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
