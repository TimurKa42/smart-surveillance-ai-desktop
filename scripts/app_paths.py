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
app_paths.py

Шляхи до файлів, які однаково працюють і в PyCharm, і в зібраній
програмі (.exe / .app).

У зібраній програмі папка з програмою часто тільки для читання
(Program Files, /Applications). Тому файли, які програма пише
(.env, settings.json, screenshots/), зберігаємо в папці даних користувача.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import os
import shutil
import sys

APP_NAME = "SmartSurveillanceAI"


def is_frozen():
    """True, якщо програму зібрано в .exe або .app."""
    return getattr(sys, "frozen", False)


def resource_path(relative_path):
    """
    Шлях до файлу, який вбудований у збірку (іконки, шрифти).
    Тільки для читання.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def user_data_dir():
    """
    Папка для файлів, які програма пише: .env, settings.json, screenshots/.

    - розробка: папка проєкту
    - Windows:  %APPDATA%\\SmartSurveillanceAI
    - macOS:    ~/Library/Application Support/SmartSurveillanceAI
    """
    if not is_frozen():
        return os.path.dirname(os.path.abspath(__file__))

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")

    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def wipe_user_data():
    """
    Видаляє папку даних користувача (ключ, налаштування, історію, скріншоти, логи).
    Працює тільки в зібраній програмі, щоб випадково не стерти папку проєкту.
    Повертає True, якщо видалено.
    """
    if not is_frozen():
        return False
    shutil.rmtree(user_data_dir(), ignore_errors=True)
    return True


def apply_crisp_window_icon(window, ico_path=None):
    """
    Ставить чітку іконку вікна у Windows через WinAPI.
    Tk бере з .ico тільки один розмір, тому на панелі завдань іконка розмита.
    Викликати із затримкою, бо customtkinter ставить свою іконку.
    Помилки не кидає.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ico_path = ico_path or resource_path(os.path.join("assets", "icon.ico"))
        if not os.path.isfile(ico_path):
            return
        user32 = ctypes.windll.user32
        window.update_idletasks()
        hwnd = user32.GetParent(window.winfo_id()) or window.winfo_id()

        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        ICON_SMALL, ICON_BIG = 0, 1
        SM_CXSMICON, SM_CXICON = 49, 11

        # Розміри іконок під DPI екрана
        try:
            dpi = user32.GetDpiForWindow(hwnd)
            small = user32.GetSystemMetricsForDpi(SM_CXSMICON, dpi)
            big = user32.GetSystemMetricsForDpi(SM_CXICON, dpi)
        except Exception:  # noqa: BLE001
            # Старі Windows без функцій з DPI - беремо звичайні розміри
            small = user32.GetSystemMetrics(SM_CXSMICON)
            big = user32.GetSystemMetrics(SM_CXICON)

        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        # Велика іконка (панель завдань) і мала (заголовок вікна)
        big_icon = user32.LoadImageW(None, ico_path, IMAGE_ICON, big, big, LR_LOADFROMFILE)
        small_icon = user32.LoadImageW(None, ico_path, IMAGE_ICON, small, small, LR_LOADFROMFILE)
        if big_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big_icon)
        if small_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
    except Exception as error:  # noqa: BLE001
        print(f"[diag] не вдалося встановити іконку вікна: {error}")


# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
