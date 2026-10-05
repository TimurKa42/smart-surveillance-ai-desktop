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
keep_awake.py - дві невеликі допомогалки для довгого аналізу.

Навіщо. На стрес-тесті 66-хвилинного відео аналіз тривав 1 год 22 хв
замість ~10 хв: користувач запустив його і пішов, Mac заснув, а
запити до Gemini, що були "в польоті", зависли на ~63 хвилини (годинник
ішов, а програма стояла). Тому:

1) KeepAwake - поки йде аналіз, не дає системі ЗАСНУТИ ВІД БЕЗДІЯЛЬНОСТІ
   (macOS: caffeinate, Windows: SetThreadExecutionState, Linux:
   systemd-inhibit / gnome-session-inhibit, Android: PARTIAL_WAKE_LOCK
   через pyjnius). Екран може гаснути - це не заважає. Закрита кришка ноутбука на батареї все одно
   присипляє комп'ютер - від цього програма захистити не може.

2) SuspendWatchdog - фоновий "сторож": раз на секунду дивиться, чи не
   стрибнув годинник далі, ніж мав. Якщо стрибнув - значить, програма
   якийсь час НЕ працювала (сон, зависання системи), і цей простій
   накопичується, щоб не рахувати його у "Витрачено часу на аналіз" і
   писати в консоль. Працює однаково на macOS/Windows/Linux, бо міряє
   не тип сну, а сам факт "секундна пауза тривала набагато довше".
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import os
import shutil
import signal
import subprocess
import sys
import threading
import time

# Прапорці Windows для SetThreadExecutionState.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

_IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

# Linux: "довгоживуча" команда, під якою тримається блокування сну.
# Цикл перевіряє, чи ще живий наш процес (аналог `caffeinate -w PID` на
# macOS): навіть якщо програма впаде без stop(), блокування саме зникне.
_LINUX_WAIT_FOR_PID = 'while kill -0 {pid} 2>/dev/null; do sleep 5; done'
_LINUX_TAG = "Smart Surveillance AI"


def _linux_inhibit_commands(pid):
    """Список варіантів команд (від кращого) для блокування сну на Linux."""
    waiter = ["sh", "-c", _LINUX_WAIT_FOR_PID.format(pid=pid)]
    commands = []
    if shutil.which("systemd-inhibit"):
        commands.append([
            "systemd-inhibit", "--what=idle:sleep", f"--who={_LINUX_TAG}",
            "--why=Video analysis in progress", "--mode=block", *waiter,
        ])
    if shutil.which("gnome-session-inhibit"):
        commands.append([
            "gnome-session-inhibit", "--inhibit", "suspend:idle",
            "--reason", "Video analysis in progress", *waiter,
        ])
    return commands


class KeepAwake:
    """
    start() - почати блокувати сон, stop() - зняти блокування.
    Безпечно викликати на будь-якій платформі: де підтримки нема (Linux
    без systemd-inhibit/gnome-session-inhibit, Android без pyjnius) -
    лише пише діагностику й нічого не блокує. Через xdg-screensaver
    свідомо не робимо: він потребує id X-вікна і гасить лише
    скрінсейвер, а не сон системи. На Windows блокування прив'язане до
    ПОТОКУ, тож start() і stop() треба викликати з одного й того самого
    потоку (у main.py - з фонового потоку аналізу).
    """

    def __init__(self):
        self._process = None
        self._windows_active = False
        self._wake_lock = None  # Android PowerManager.WakeLock

    def start(self):
        try:
            if sys.platform == "darwin":
                # -i: не засинати від бездіяльності; -w PID: caffeinate сам
                # завершиться, коли завершиться наш процес (навіть при
                # аварійному закритті) - тож "вічно не спати" не вийде.
                self._process = subprocess.Popen(
                    ["caffeinate", "-i", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
                self._windows_active = True
            elif sys.platform.startswith("linux") and not _IS_ANDROID:
                self._start_linux()
            elif _IS_ANDROID:
                self._start_android()
        except Exception as error:  # noqa: BLE001 - захист від сну не має валити аналіз
            print(f"[diag] не вдалось увімкнути захист від сну: {error}")

    def _start_linux(self):
        for command in _linux_inhibit_commands(os.getpid()):
            try:
                # start_new_session: власна група процесів, щоб при stop()
                # прибити і inhibit, і його дочірній sh одним killpg.
                process = subprocess.Popen(
                    command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                continue
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                self._process = process  # живий - блокування тримається
                return
            # Завершився одразу (немає dbus/logind, відмовлено в доступі) -
            # пробуємо наступний варіант.
        print("[diag] Linux: systemd-inhibit/gnome-session-inhibit недоступні - захисту від сну нема")

    def _start_android(self):
        try:
            from jnius import autoclass
        except ImportError:
            print("[diag] Android: pyjnius недоступний - захисту від сну нема")
            return
        # Потрібен дозвіл WAKE_LOCK у buildozer.spec (android.permissions).
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        PowerManager = autoclass("android.os.PowerManager")
        power_manager = PythonActivity.mActivity.getSystemService(Context.POWER_SERVICE)
        lock = power_manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "SmartSurveillance:analysis")
        lock.setReferenceCounted(False)
        # Страховка: навіть якщо stop() не викличеться, lock зніметься сам
        # через 3 години, а не розряджатиме телефон до ранку.
        lock.acquire(3 * 60 * 60 * 1000)
        self._wake_lock = lock

    def stop(self):
        try:
            if self._wake_lock is not None:
                if self._wake_lock.isHeld():
                    self._wake_lock.release()
                self._wake_lock = None
            if self._process is not None:
                if sys.platform.startswith("linux"):
                    try:
                        os.killpg(os.getpgid(self._process.pid), signal.SIGTERM)
                    except (ProcessLookupError, PermissionError):
                        self._process.terminate()
                else:
                    self._process.terminate()
                try:
                    self._process.wait(timeout=1)  # прибираємо "зомбі"
                except subprocess.TimeoutExpired:
                    pass
                self._process = None
            if self._windows_active:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
                self._windows_active = False
        except Exception as error:  # noqa: BLE001
            print(f"[diag] не вдалось зняти захист від сну: {error}")


class SuspendWatchdog:
    """
    Рахує, скільки секунд програма "простояла" (сон системи, зависання).

    Раз на tick_sec секунд поток прокидається і дивиться, скільки часу
    насправді минуло за настінним годинником. Якщо більше за tick_sec на
    threshold_sec і вище - різниця додається до suspended_sec. Дрібні
    стрибки годинника (синхронізація часу) нижче порога ігноруються.
    """

    def __init__(self, tick_sec=1.0, threshold_sec=5.0, clock=time.time):
        self._tick_sec = tick_sec
        self._threshold_sec = threshold_sec
        self._clock = clock
        self._stop_event = threading.Event()
        self._thread = None
        self.suspended_sec = 0.0

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._tick_sec * 3)

    def _run(self):
        last = self._clock()
        while not self._stop_event.wait(self._tick_sec):
            now = self._clock()
            excess = (now - last) - self._tick_sec
            last = now
            if excess >= self._threshold_sec:
                self.suspended_sec += excess
                stamp = time.strftime("%H:%M:%S")
                print(f"[diag] {stamp}: програма не працювала ≈{excess:.0f}с "
                      f"(сон комп'ютера або зависання системи); разом простою: {self.suspended_sec:.0f}с")

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
