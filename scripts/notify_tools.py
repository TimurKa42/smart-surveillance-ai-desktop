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
notify_tools.py

Системні (десктопні) сповіщення про завершення аналізу.

macOS (зібраний .app): НАТИВНІ сповіщення через UserNotifications
(pyobjc). Сповіщення приходить від САМОГО застосунку - з його іконкою
(icon.icns) і назвою, як у Telegram, а клік по ньому виводить вікно
програми на передній план (розгортає з Dock, якщо згорнуте).
Працює лише в зібраному .app: UserNotifications прив'язані до
bundle-ідентифікатора (com.smartsurveillance.ai у main.spec).

macOS (запуск з PyCharm/терміналу, не .app): запасний варіант через
osascript - сповіщення приходить від "Редактора скриптів" (так само,
як було раніше). Це обмеження osascript, а не помилка. Перевіряти
нативні сповіщення треба у зібраному .app.

Windows / Linux: бібліотека plyer, як і раніше.

notify() ніколи не кидає виняток назовні - відсутність сповіщень не
має валити програму чи фоновий потік аналізу.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import logging
import subprocess
import sys
import threading
import uuid

_log = logging.getLogger(__name__)

# Єдиний AppUserModelID для Windows. ПОВИНЕН збігатися з AppUserModelID
# у ярликах installer.iss ([Icons]) - інакше Windows 10/11 може мовчки
# відкидати тости від незнайомого застосунку.
APP_USER_MODEL_ID = "SmartSurveillanceAI.App"
_APP_DISPLAY_NAME = "Smart Surveillance AI"

try:
    from plyer import notification as _plyer_notification
    _PLYER_IMPORT_OK = True
except Exception as _plyer_error:  # noqa: BLE001
    _plyer_notification = None
    _PLYER_IMPORT_OK = False
    _log.warning("plyer недоступний: %s", _plyer_error)

# ---- winotify (лише Windows): справжні тости з нашим AppUserModelID ----
_WINOTIFY_OK = False
_winotify = None
if sys.platform == "win32":
    try:
        import winotify as _winotify
        _WINOTIFY_OK = True
    except Exception as _winotify_error:  # noqa: BLE001
        _log.warning("winotify недоступний: %s", _winotify_error)


def set_windows_app_id():
    """Прописує AppUserModelID процесу (Windows). Викликати якомога раніше."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception as error:  # noqa: BLE001
        _log.warning("не вдалося задати AppUserModelID: %s", error)


set_windows_app_id()

# ---- pyobjc (лише macOS) ----
_UN_OK = False
_UN_IMPORT_ERROR = None
if sys.platform == "darwin":
    try:
        import objc
        from Foundation import NSObject, NSBundle
        from AppKit import NSApplication
        import UserNotifications as _UN
        _UN_OK = True
    except Exception as _error:  # noqa: BLE001
        _UN_IMPORT_ERROR = _error


def _running_from_app_bundle():
    """True, якщо процес - це зібраний .app (а не python із PyCharm)."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        bundle_path = str(NSBundle.mainBundle().bundlePath())
        return bundle_path.endswith(".app")
    except Exception:  # noqa: BLE001
        return False


_delegate = None          # тримаємо посилання - інакше делегат зберуть як сміття
_delegate_lock = threading.Lock()

if _UN_OK:
    try:
        try:
            _protocol = objc.protocolNamed("UNUserNotificationCenterDelegate")
        except Exception:  # noqa: BLE001
            _protocol = None

        class _NotificationDelegate(NSObject, protocols=[_protocol] if _protocol else []):
            """Показує сповіщення навіть коли застосунок активний і повертає
            фокус на вікно програми по кліку."""

            def userNotificationCenter_willPresentNotification_withCompletionHandler_(
                    self, center, notification, completion_handler):
                options = (
                    getattr(_UN, "UNNotificationPresentationOptionBanner", 16)
                    | getattr(_UN, "UNNotificationPresentationOptionList", 8)
                    | getattr(_UN, "UNNotificationPresentationOptionSound", 2)
                )
                completion_handler(options)

            def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                    self, center, response, completion_handler):
                # колбек приходить з довільного потоку - фокус робимо в головному
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "focusApp:", None, False)
                completion_handler()

            def focusApp_(self, _sender):
                try:
                    app = NSApplication.sharedApplication()
                    app.unhide_(None)
                    for window in app.windows():
                        if window.isMiniaturized():
                            window.deminiaturize_(None)
                    app.activateIgnoringOtherApps_(True)
                except Exception as error:  # noqa: BLE001
                    print(f"[diag] не вдалося вивести вікно на передній план: {error}")
    except Exception as _error:  # noqa: BLE001 - імпорт не має валити програму
        _UN_OK = False
        _UN_IMPORT_ERROR = _error

def _ensure_delegate(center):
    global _delegate
    with _delegate_lock:
        if _delegate is None:
            _delegate = _NotificationDelegate.alloc().init()
        center.setDelegate_(_delegate)


def _notify_macos_native(title, message):
    """Нативне сповіщення від імені .app. True - показано; False - ні."""
    with objc.autorelease_pool():
        center = _UN.UNUserNotificationCenter.currentNotificationCenter()
        _ensure_delegate(center)

        # 1) дозвіл (при першому запуску macOS покаже системний запит)
        auth_done = threading.Event()
        auth = {"granted": False}

        def on_auth(granted, error):
            auth["granted"] = bool(granted)
            if error is not None:
                print(f"[diag] дозвіл на сповіщення: {error}")
            auth_done.set()

        options = (
            getattr(_UN, "UNAuthorizationOptionAlert", 4)
            | getattr(_UN, "UNAuthorizationOptionSound", 2)
        )
        center.requestAuthorizationWithOptions_completionHandler_(options, on_auth)
        if not auth_done.wait(timeout=60):
            print("[diag] час очікування дозволу на сповіщення вичерпано")
            return False
        if not auth["granted"]:
            print("[diag] сповіщення заборонені: Системні налаштування -> "
                  "Сповіщення -> Smart Surveillance AI")
            return False

        # 2) саме сповіщення
        content = _UN.UNMutableNotificationContent.alloc().init()
        content.setTitle_(title)
        content.setBody_(message)
        content.setSound_(_UN.UNNotificationSound.defaultSound())
        request = _UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(
            str(uuid.uuid4()), content, None)

        add_done = threading.Event()
        add = {"ok": False}

        def on_add(error):
            if error is not None:
                print(f"[diag] сповіщення не додано: {error}")
            else:
                add["ok"] = True
            add_done.set()

        center.addNotificationRequest_withCompletionHandler_(request, on_add)
        add_done.wait(timeout=10)
        return add["ok"]


def _notify_macos_osascript(title, message):
    """Запасний варіант для запуску НЕ з .app (PyCharm, термінал).
    Відправником буде "Редактор скриптів" - обмеження osascript."""
    script = (
        "on run argv\n"
        "display notification (item 2 of argv) with title (item 1 of argv)\n"
        "end run"
    )
    result = subprocess.run(
        ["osascript", "-e", script, title, message],
        capture_output=True, timeout=10,
    )
    return result.returncode == 0


def is_available():
    """True, якщо є хоч якийсь спосіб показати сповіщення."""
    if sys.platform == "darwin":
        return True
    return _PLYER_IMPORT_OK or _WINOTIFY_OK


def notify(title, message, app_name="", app_icon="", timeout=10):
    """Показує одне системне сповіщення. True - пішло на показ, False - ні.
    Жодного винятку назовні не кидає."""
    if sys.platform == "darwin":
        try:
            if _UN_OK and _running_from_app_bundle():
                return _notify_macos_native(title, message)
            if _UN_IMPORT_ERROR is not None and getattr(sys, "frozen", False):
                print(f"[diag] pyobjc UserNotifications недоступний у збірці: {_UN_IMPORT_ERROR}")
            return _notify_macos_osascript(title, message)
        except Exception as error:  # noqa: BLE001
            print(f"[diag] сповіщення macOS не показано: {error}")
            return False

    if sys.platform == "win32" and _WINOTIFY_OK:
        _register_windows_identity()
        try:
            toast = _winotify.Notification(
                app_id=APP_USER_MODEL_ID, title=title, msg=message,
                icon=_windows_icon_path(app_icon), duration="short",
            )
            toast.show()
            return True
        except Exception as error:  # noqa: BLE001
            _log.warning("winotify не спрацював, пробую plyer: %s", error)
            print(f"[diag] winotify не спрацював: {error}")

    if not _PLYER_IMPORT_OK:
        return False
    try:
        _plyer_notification.notify(
            title=title, message=message,
            app_name=app_name or _APP_DISPLAY_NAME,
            app_icon=app_icon, timeout=timeout,
        )
        return True
    except Exception as error:  # noqa: BLE001
        _log.warning("системне сповіщення не показано: %s", error)
        print(f"[diag] системне сповіщення не показано: {error}")
        return False


_identity_registered = False


def _register_windows_identity():
    """Як у Lightshot: назва й іконка застосунку в заголовку тосту беруться
    з HKCU/Software/Classes/AppUserModelId/<ID>. Без цього Windows
    показує сирий ID ("SmartSurveillanceAI.App")."""
    global _identity_registered
    if _identity_registered or sys.platform != "win32":
        return
    _identity_registered = True
    try:
        import winreg
        key_path = "Software\\Classes\\AppUserModelId\\" + APP_USER_MODEL_ID
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, _APP_DISPLAY_NAME)
            icon = _windows_icon_path()
            if icon:
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon)
    except Exception as error:  # noqa: BLE001
        _log.warning("не вдалося зареєструвати ім'я/іконку застосунку: %s", error)


def _windows_icon_path(app_icon=""):
    """winotify вимагає абсолютний шлях до іконки (або порожній рядок)."""
    import os
    candidates = [app_icon]
    try:
        from app_paths import resource_path
        # PNG чіткіший за .ico (той дає піксельну картинку в тості)
        candidates.append(resource_path("assets/icon_1024.png"))
        candidates.append(resource_path("assets/icon.ico"))
    except Exception:  # noqa: BLE001
        pass
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return ""

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com