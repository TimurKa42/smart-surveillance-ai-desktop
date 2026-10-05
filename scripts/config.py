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
config.py (десктопна версія)

Керує API-ключем Gemini: зберігає його у файлі .env поруч із
програмою і дозволяє вводити/змінювати ключ прямо з інтерфейсу, без
ручного редагування файлу.

На відміну від мобільної версії тут не потрібна окрема тека даних
застосунку (на Android APK - архів лише для читання, тому там ключ
писали в App.user_data_dir). На комп'ютері програма має право писати
файли у своїй же теці, тому .env лежить поруч зі скриптами - так само,
як це описано в README (.env.example -> .env).
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import os

import app_paths

ENV_FILENAME = ".env"


def get_env_path():
    """Шлях до .env: у розробці - поруч зі скриптами, у зібраній програмі -
    у теці даних користувача (див. app_paths.user_data_dir)."""
    return os.path.join(app_paths.user_data_dir(), ENV_FILENAME)


def load_api_key():
    """
    Повертає збережений ключ або None, якщо його ще нема.

    Читає .env напряму (без python-dotenv) - файл рівно з одним
    рядком GEMINI_API_KEY=..., того самого формату, що зберігає
    save_api_key() нижче. Так само вміє прочитати .env, який
    користувач створив вручну за інструкцією з README (скопіювавши
    .env.example).
    """
    path = get_env_path()
    if not os.path.exists(path):
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    return key or None
    except OSError:
        return None

    return None


def save_api_key(key):
    """Зберігає ключ у .env - файл рівно з одним рядком."""
    path = get_env_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"GEMINI_API_KEY={key.strip()}\n")
    try:
        os.chmod(path, 0o600)   # ключ читає лише власник файлу
    except OSError:
        pass


def clear_api_key():
    """Видаляє .env - використовується, якщо треба ввести інший ключ з нуля."""
    path = get_env_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
