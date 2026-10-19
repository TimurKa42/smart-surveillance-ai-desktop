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
keep_awake.py - два невеликі класи для довгого аналізу.

Чому це потрібно. На тесті з 66-хвилинним відео аналіз затягнувся
майже на годину. Комп'ютер заснув, і запити до Gemini висіли, поки
він спав. Тому:

1) KeepAwake - поки триває аналіз, не даємо системі заснути.
   Екран може гаснути, це не заважає. Але якщо ноутбук закрили
   кришкою на батареї, він все одно засне, з цим програма нічого
   не зробить.

2) SuspendWatchdog - лічильник простою. Раз на секунду перевіряє,
   чи не стрибнув годинник. Якщо стрибнув, значить комп'ютер спав
   або завис. Цей час віднімаємо, щоб не рахувати його в "часі аналізу".
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

# Прапорці Windows для SetThreadExecutionState
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

_IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

# Linux: команда, яка тримає блокування сну, поки живий наш процес.
# Якщо програма впаде, блокування зникне саме (аналог caffeinate -w PID на macOS).
_LINUX_WAIT_FOR_PID = 'while kill -0 {pid} 2>/dev/null; do sleep 5; done'
_LINUX_TAG = "Smart Surveillance AI"


def _linux_inhibit_commands(pid):
    """Список команд для блокування сну на Linux, від кращої до гіршої."""
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
    Якщо система не підтримує блокування (наприклад, Linux без systemd
    чи Android без pyjnius), просто пише повідомлення і нічого не робить.
    На Windows блокування прив'язане до потоку, тому start() і stop()
    треба викликати з одного потоку (у main.py це фоновий потік аналізу).
    """

    def __init__(self):
        self._process = None
        self._windows_active = False
        self._wake_lock = None  # Android PowerManager.WakeLock

    def start(self):
        try:
            if sys.platform == "darwin":
                # -i: не засинати. -w PID: caffeinate сам закриється разом з нашою програмою
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
        except Exception as error:  # noqa: BLE001 - помилка сну не має зупиняти аналіз
            print(f"[diag] не вдалось увімкнути захист від сну: {error}")

    def _start_linux(self):
        for command in _linux_inhibit_commands(os.getpid()):
            try:
                # start_new_session - щоб при stop() прибрати і команду, і її sh разом
                process = subprocess.Popen(
                    command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            except OSError:
                continue
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                self._process = process  # працює, блокування тримається
                return
            # Завершилась одразу (немає dbus або нема доступу), пробуємо наступну
        print("[diag] Linux: systemd-inhibit/gnome-session-inhibit недоступні - захисту від сну нема")

    def _start_android(self):
        try:
            from jnius import autoclass
        except ImportError:
            print("[diag] Android: pyjnius недоступний - захисту від сну нема")
            return
        # Для цього потрібен дозвіл WAKE_LOCK у buildozer.spec
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        PowerManager = autoclass("android.os.PowerManager")
        power_manager = PythonActivity.mActivity.getSystemService(Context.POWER_SERVICE)
        lock = power_manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "SmartSurveillance:analysis")
        lock.setReferenceCounted(False)
        # Страховка: lock сам зніметься через 3 години, навіть якщо stop() не викличуть
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
                    self._process.wait(timeout=1)  # прибираємо процес, щоб не лишився "зомбі"
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
    Рахує, скільки секунд програма простояла (сон або зависання).

    Раз на tick_sec секунд дивимось, скільки часу минуло. Якщо різниця
    більша за tick_sec на threshold_sec і більше, додаємо її до
    suspended_sec. Дрібні стрибки годинника ігноруємо.
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
