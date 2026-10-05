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

Шляхи, які однаково працюють і під час розробки (PyCharm), і в зібраній
програмі (PyInstaller .exe / .app).

Чому це потрібно:
- У зібраній програмі __file__ вказує всередину пакета (у --onefile це
  взагалі тимчасова тека, яка видаляється після закриття), а відносні
  шляхи на кшталт "screenshots" залежать від того, звідки запустили
  програму (з Finder робоча тека - це "/").
- Тека установки (Program Files, /Applications) зазвичай недоступна для
  запису, тож .env, settings.json і screenshots/ треба тримати в теці
  даних користувача.

Під час розробки (не frozen) усе лишається як було - файли лежать
поруч зі скриптами, нічого переносити не треба.
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
    """True, якщо програма запущена зі збірки PyInstaller."""
    return getattr(sys, "frozen", False)


def resource_path(relative_path):
    """
    Шлях до ресурсу, який ЗАПАКОВАНО в збірку (шрифти, іконки - усе, що
    додано в datas у main.spec). Тільки для читання.
    """
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def user_data_dir():
    """
    Тека для файлів, які програма ПИШЕ: .env, settings.json, screenshots/.

    - розробка: тека проєкту (як і раніше)
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
    Повністю видаляє теку даних користувача (.env з ключем, settings.json,
    історія кадрів, скріншоти, логи). Працює ТІЛЬКИ у зібраній програмі:
    під час розробки ця тека - це сама тека проєкту, і видаляти її не можна.
    Повертає True, якщо теку видалено.
    """
    if not is_frozen():
        return False
    shutil.rmtree(user_data_dir(), ignore_errors=True)
    return True

def apply_crisp_window_icon(window, ico_path=None):
    """
    Windows: ставить чіткі іконки вікна напряму через WinAPI - велику
    (панель завдань, Alt+Tab) і малу (заголовок вікна). Tk сам бере з .ico
    лише один розмір і розтягує його, тому на панелі завдань іконка мутна.
    Викликати з затримкою (after 400+ мс), інакше customtkinter перебʼє її
    своєю іконкою. Ніколи не кидає виняток.
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

        try:
            dpi = user32.GetDpiForWindow(hwnd)
            small = user32.GetSystemMetricsForDpi(SM_CXSMICON, dpi)
            big = user32.GetSystemMetricsForDpi(SM_CXICON, dpi)
        except Exception:  # noqa: BLE001
            small = user32.GetSystemMetrics(SM_CXSMICON)
            big = user32.GetSystemMetrics(SM_CXICON)

        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        big_icon = user32.LoadImageW(None, ico_path, IMAGE_ICON, big, big, LR_LOADFROMFILE)
        small_icon = user32.LoadImageW(None, ico_path, IMAGE_ICON, small, small, LR_LOADFROMFILE)
        if big_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big_icon)
        if small_icon:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
    except Exception as error:  # noqa: BLE001
        print(f"[diag] не вдалося встановити іконку вікна: {error}")


# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com