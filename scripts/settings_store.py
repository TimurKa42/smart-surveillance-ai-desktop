# =============================================================================
# LookBack
# © 2026 Тимур Каленик (Timur Kalenik Corporation) <timurkalenik950@gmail.com>
# автор: Тимур Каленик. усі права захищено.
#
# проєкт зроблений для конкурсу МАН. код відкритий щоб журі могло подивитись
# і оцінити. копіювати, поширювати, міняти і використовувати код (весь або
# частинами) без письмової згоди автора і без вказання авторства не можна.
# повні умови в файлі LICENSE, не забути його покласти поруч
# =============================================================================

# сеттінгс_стор, тут зберігаю налаштування (тім, мова і таке інше)
# все лежить у файлі settings.json, щоб не забути де воно
# беру тільки джейсон і ос, більше нічого не треба

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import json
import os
import app_paths

SETTINGS_PATH = os.path.join(app_paths.user_data_dir(), "settings.json")

# тім: "auto" це як у системі, "light" і "dark" це завжди така тема
# лангвідж: "ua" або "en"
# детейл_левел_індекс: слайдер "Детально", від 0 до 3, 1 це середній
# рімембер_детейл_левел: чи пам'ятати рівень між запусками
# апі_тір: який ключ у юзера, "free", "paid" або None (ще не вибрав)
# вшитий ключ завжди фрі, не забути
# велкам_шоун: чи вже показав вітальне вікно
# ключів яких нема в цьому словнику ф-ція load_settings не читає
# тому всі поля писати сюди, щоб не забути
DEFAULT_SETTINGS = {
    "theme": "auto",
    "language": "ua",
    "detail_level_index": 1,
    "remember_detail_level": False,
    "api_tier": None,
    "welcome_shown": False,
}


# ф-ція читання налаштувань, не забути її викликати на старті
def load_settings():
    # якщо файлу нема, беру значення за замовчуванням
    if not os.path.exists(SETTINGS_PATH):
        return DEFAULT_SETTINGS.copy()

    # це спроба відкрити файл
    try:
        f = open(SETTINGS_PATH, "r", encoding="utf-8")
        data = json.load(f)
        f.close()
    except (OSError, json.JSONDecodeError):
        # файл зіпсований, тому знову беру за замовчуванням
        return DEFAULT_SETTINGS.copy()

    # беру за основу значення за замовчуванням
    settings = DEFAULT_SETTINGS.copy()
    # з файлу беру тільки ті ключі які є в словнику
    for key in DEFAULT_SETTINGS:
        if key in data:
            settings[key] = data[key]
    return settings


# ф-ція збереження налаштувань, не забути її викликати коли юзер щось змінив
def save_settings(settings):
    try:
        f = open(SETTINGS_PATH, "w", encoding="utf-8")
        json.dump(settings, f, ensure_ascii=False, indent=2)
        f.close()
    except OSError:
        # помилку запису ігнорую, це не страшно
        pass

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
