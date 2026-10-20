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
app_paths.py

Шляхи до файлів, щоб працювало і в PyCharm, і в .exe/.app.
Папка зібраної програми read-only, тому .env, settings.json
і screenshots/ кидаю в папку даних юзера.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import os
import shutil
import sys

APP_NAME = "LookBack"


def is_frozen():
    # Ф-ція: це .exe/.app чи ні, не забути її
    return getattr(sys, "frozen", False)


def resource_path(relative_path):
    # Беру файли зі збірки (іконки, шрифти), тільки читати
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def user_data_dir():
    # Папка для .env, settings.json, screenshots/
    # розробка - папка проєкту
    # віндовс - %APPDATA%\\LookBack
    # мак - ~/Library/Application Support/LookBack
    if not is_frozen():
        return os.path.dirname(os.path.abspath(__file__))

    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")

    path = os.path.join(base, APP_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def wipe_user_data():
    # Ф-ція чистить всі дані юзера (ключ, налаштування, скріни, логи)
    # Тільки в зібраній програмі, *щоб не стерти свій проєкт!*
    # True якщо видалили
    if not is_frozen():
        return False
    shutil.rmtree(user_data_dir(), ignore_errors=True)
    return True


def apply_crisp_window_icon(window, ico_path=None):
    # Ф-ція ставить чітку іконку, тільки віндовс
    # Tk бере з .ico один розмір, тому розмито
    # Викликати із затримкою, customtkinter ставить свою *щоб не забути*
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

        # Розміри під DPI
        try:
            dpi = user32.GetDpiForWindow(hwnd)
            small = user32.GetSystemMetricsForDpi(SM_CXSMICON, dpi)
            big = user32.GetSystemMetricsForDpi(SM_CXICON, dpi)
        except Exception:  # noqa: BLE001
            # Стара віндовс, беру звичайні розміри
            small = user32.GetSystemMetrics(SM_CXSMICON)
            big = user32.GetSystemMetrics(SM_CXICON)

        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        # Велика - панель завдань, мала - заголовок
        big_icon = user32.LoadImageW(None, ico_path, IMAGE_ICON, big, big, LR_LOADFROMFILE)
        small_icon = user32.LoadImageW(None, ico_path, IMAGE_ICON, small, small, LR_LOADFROMFILE)
        if big_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big_icon)
        if small_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
    except Exception as error:  # noqa: BLE001
        print(f"[diag] не вдалося встановити іконку вікна: {error}")


# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
