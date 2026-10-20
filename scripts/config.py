# =============================================================================
# LookBack
# © 2026 Тимур Каленик (Timur Kalenik Corporation) <timurkalenik950@gmail.com>
# Автор: Тимур Каленик. Усі права захищено.
#
# Проєкт створено для конкурсу МАН. Вихідний код відкрито для ознайомлення
# та оцінювання журі. Копіювання, поширення, зміна та використання коду
# (повністю або частково) без письмової згоди автора та без зазначення
# авторства заборонені. Повні умови — у файлі LICENSE.
# =============================================================================

"""
config.py

ключ джеміні. є два варіанти:
1) вшитий ключ з файлу embedded_key.py (цього файлу немає в репозиторії).
   тоді користувачу нічого вводити не треба.
2) свій ключ, збережений у файлі .env. якщо він є, використовується він.
   видалив свій ключ - знову працює вшитий.

файл .env лежить у папці даних (див. app_paths.user_data_dir).
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import os

import app_paths

ENV_FILENAME = ".env"

# вшитий ключ. якщо файлу embedded_key.py нема, буде нон - тоді працює тільки свій ключ
try:
    from embedded_key import EMBEDDED_API_KEY
except ImportError:
    EMBEDDED_API_KEY = None


def get_env_path():
    """шлях до файлу .env."""
    return os.path.join(app_paths.user_data_dir(), ENV_FILENAME)


def load_custom_key():
    """
    повертає свій ключ з .env або нон, якщо його нема.

    .env читаємо самі, без пайтон-дотенв. формат - один рядок:
    GEMINI_API_KEY=...
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
    """
    ключ, який реально використовуємо: спочатку свій, потім вшитий.
    нон, якщо немає жодного.
    """
    key = load_custom_key()
    if key:
        return key
    return EMBEDDED_API_KEY or None


def is_using_custom_key():
    """тру, якщо є свій ключ."""
    return load_custom_key() is not None


def has_embedded_key():
    """тру, якщо ключ вшитий у збірку."""
    return bool(EMBEDDED_API_KEY)


def save_api_key(key):
    """зберігає свій ключ у .env (один рядок)."""
    path = get_env_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"GEMINI_API_KEY={key.strip()}\n")
    try:
        # ключ бачить тільки власник файлу
        os.chmod(path, 0o600)
    except OSError:
        pass


def clear_api_key():
    """видаляє свій ключ (.env). вшитий ключ лишається."""
    path = get_env_path()
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
