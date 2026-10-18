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

Керує API-ключем Gemini. Ключів може бути два:

1) ВШИТИЙ ключ - підставляється під час збірки у файл embedded_key.py
   (цей файл у .gitignore і в репозиторій не потрапляє). Завдяки йому
   користувачу не треба нічого вводити - програма одразу готова до роботи.
2) ВЛАСНИЙ ключ користувача - зберігається у файлі .env поруч із
   програмою (див. save_api_key). Якщо він є, використовується замість
   вшитого. Видалив власний ключ (clear_api_key) - програма знову бере
   вшитий.

На відміну від мобільної версії тут не потрібна окрема тека даних
застосунку: на комп'ютері програма має право писати файли у своїй теці
даних, тому .env лежить поруч зі скриптами (див. app_paths.user_data_dir).
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import os

import app_paths

ENV_FILENAME = ".env"

# Вшитий ключ. Якщо файлу embedded_key.py нема (звичайна розробка без
# ключа), просто лишається None, і програма працює лише з власним ключем.
try:
    from embedded_key import EMBEDDED_API_KEY
except ImportError:
    EMBEDDED_API_KEY = None


def get_env_path():
    """Шлях до .env: у розробці - поруч зі скриптами, у зібраній програмі -
    у теці даних користувача (див. app_paths.user_data_dir)."""
    return os.path.join(app_paths.user_data_dir(), ENV_FILENAME)


def load_custom_key():
    """
    Повертає ВЛАСНИЙ ключ користувача з .env або None, якщо його нема.

    Читає .env напряму (без python-dotenv) - файл рівно з одним
    рядком GEMINI_API_KEY=..., того самого формату, що зберігає
    save_api_key() нижче. Так само вміє прочитати .env, який
    користувач створив вручну (скопіювавши .env.example).
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


def load_api_key():
    """Ключ, яким програма реально користується: спочатку власний,
    якщо його нема - вшитий. None, якщо немає жодного."""
    key = load_custom_key()
    if key:
        return key
    return EMBEDDED_API_KEY or None


def is_using_custom_key():
    """True, якщо користувач підключив власний ключ."""
    return load_custom_key() is not None


def has_embedded_key():
    """True, якщо в збірку вшито стандартний ключ."""
    return bool(EMBEDDED_API_KEY)


def save_api_key(key):
    """Зберігає ВЛАСНИЙ ключ у .env - файл рівно з одним рядком."""
    path = get_env_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"GEMINI_API_KEY={key.strip()}\n")
    try:
        os.chmod(path, 0o600)   # ключ читає лише власник файлу
    except OSError:
        pass


def clear_api_key():
    """Видаляє власний ключ (.env). Вшитий ключ при цьому лишається."""
    path = get_env_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
