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

# notify_tools.py
# тут усі сповіщення про завершення аналізу, щоб не забути що де
# мак (зібраний апп): беру нативні сповіщення через юзернотіфікейшнс (пайобджісі),
# вони приходять від самої програми з іконкою, клік виводить вікно вперед
# працює тільки в зібраному аппі, бо прив'язано до бандл-ідентифікатора
# (com.smartsurveillance.ai у main.spec, не чіпати!)
# мак (запуск з пайчарма чи термінала): запасний варіант через осаскріпт,
# відправник буде "Редактор скриптів", це обмеження осаскріпта, не баг
# віндовс і лінукс: беру плаєр, як і раніше
# ф-ція notify() не кидає виняток назовні, щоб фоновий потік не падав

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

# один айді для віндовс, має збігатися з тим, що в ярликах installer.iss,
# інакше віндовс може мовчки відкидати тости, щоб не забути
APP_USER_MODEL_ID = "SmartSurveillanceAI.App"
_APP_DISPLAY_NAME = "LookBack"

# пробую взяти плаєр
try:
    from plyer import notification as _plyer_notification
    _PLYER_IMPORT_OK = True
except Exception as _plyer_error:  # noqa: BLE001
    _plyer_notification = None
    _PLYER_IMPORT_OK = False
    _log.warning("plyer недоступний: %s", _plyer_error)

# вінотіфай тільки для віндовс, справжні тости з нашим айді
_WINOTIFY_OK = False
_winotify = None
if sys.platform == "win32":
    try:
        import winotify as _winotify
        _WINOTIFY_OK = True
    except Exception as _winotify_error:  # noqa: BLE001
        _log.warning("winotify недоступний: %s", _winotify_error)


# ф-ція для айді процесу на віндовс, викликати якомога раніше, не забути її
def set_windows_app_id():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception as error:  # noqa: BLE001
        _log.warning("не вдалося задати AppUserModelID: %s", error)


set_windows_app_id()

# пайобджісі тільки для мака
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


# ф-ція перевіряє чи це зібраний апп, а не пайтон з пайчарма
def _running_from_app_bundle():
    if not getattr(sys, "frozen", False):
        return False
    try:
        bundle_path = str(NSBundle.mainBundle().bundlePath())
        return bundle_path.endswith(".app")
    except Exception:  # noqa: BLE001
        return False


# тримаю посилання на делегат, інакше його збере сміттєзбірник, щоб не забути
_delegate = None
_delegate_lock = threading.Lock()

if _UN_OK:
    try:
        try:
            _protocol = objc.protocolNamed("UNUserNotificationCenterDelegate")
        except Exception:  # noqa: BLE001
            _protocol = None

        # клас делегата: показує сповіщення навіть коли програма активна
        # і повертає фокус на вікно по кліку
        class _NotificationDelegate(NSObject, protocols=[_protocol] if _protocol else []):

            def userNotificationCenter_willPresentNotification_withCompletionHandler_(
                    self, center, notification, completion_handler):
                # збираю опції: банер, список і звук
                options = (
                    getattr(_UN, "UNNotificationPresentationOptionBanner", 16)
                    | getattr(_UN, "UNNotificationPresentationOptionList", 8)
                    | getattr(_UN, "UNNotificationPresentationOptionSound", 2)
                )
                completion_handler(options)

            def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(
                    self, center, response, completion_handler):
                # колбек приходить з довільного потоку, фокус роблю в головному
                self.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "focusApp:", None, False)
                completion_handler()

            def focusApp_(self, _sender):
                try:
                    app = NSApplication.sharedApplication()
                    app.unhide_(None)
                    # розгортаю згорнуті вікна з докa
                    for window in app.windows():
                        if window.isMiniaturized():
                            window.deminiaturize_(None)
                    app.activateIgnoringOtherApps_(True)
                except Exception as error:  # noqa: BLE001
                    print(f"[diag] не вдалося вивести вікно на передній план: {error}")
    except Exception as _error:  # noqa: BLE001
        # імпорт не має валити програму
        _UN_OK = False
        _UN_IMPORT_ERROR = _error

# ф-ція для делегата, ставлю його в центр сповіщень
def _ensure_delegate(center):
    global _delegate
    with _delegate_lock:
        if _delegate is None:
            _delegate = _NotificationDelegate.alloc().init()
        center.setDelegate_(_delegate)


# ф-ція нативного сповіщення від імені аппа, True якщо показано
def _notify_macos_native(title, message):
    with objc.autorelease_pool():
        center = _UN.UNUserNotificationCenter.currentNotificationCenter()
        _ensure_delegate(center)

        # спочатку дозвіл, при першому запуску мак покаже системний запит
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
                  "Сповіщення -> LookBack")
            return False

        # тепер саме сповіщення: заголовок, текст, звук
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


# ф-ція запасного варіанта для запуску не з аппа (пайчарм, термінал),
# відправником буде "Редактор скриптів", не забути що це обмеження осаскріпта
def _notify_macos_osascript(title, message):
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


# ф-ція перевіряє чи є хоч якийсь спосіб показати сповіщення
def is_available():
    if sys.platform == "darwin":
        return True
    return _PLYER_IMPORT_OK or _WINOTIFY_OK


# головна ф-ція, показує одне сповіщення, True якщо пішло на показ
# винятків назовні не кидає, щоб не забути
def notify(title, message, app_name="", app_icon="", timeout=10):
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

    # на віндовс спочатку пробую вінотіфай
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

    # якщо не вийшло, беру плаєр
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


# ф-ція реєструє назву й іконку аппа для віндовс, як у Lightshot,
# беруться з реєстру HKCU/Software/Classes/AppUserModelId/<ID>
# без цього віндовс показує сирий айді "SmartSurveillanceAI.App", щоб не забути
def _register_windows_identity():
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


# ф-ція шукає іконку, вінотіфай хоче абсолютний шлях або порожній рядок
def _windows_icon_path(app_icon=""):
    import os
    candidates = [app_icon]
    try:
        from app_paths import resource_path
        # пнг чіткіший за ico, той дає піксельну картинку в тості
        candidates.append(resource_path("assets/icon_1024.png"))
        candidates.append(resource_path("assets/icon.ico"))
    except Exception:  # noqa: BLE001
        pass
    for path in candidates:
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return ""

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
