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

# keep_awake.py, тут два невеликі класи для довгого аналізу
#
# чому це треба, щоб не забути: на тесті з 66-хвилинним відео аналіз
# затягнувся майже на годину. комп'ютер заснув, і запити до Джеміні
# висіли, поки він спав. тому:
#
# 1) KeepAwake, поки триває аналіз, не даю системі заснути.
#    екран може гаснути, це не заважає. але якщо ноутбук закрили
#    кришкою на батареї, він все одно засне, з цим програма нічого не зробить
#
# 2) SuspendWatchdog, лічильник простою. раз на секунду дивлюсь,
#    чи не стрибнув годинник. якщо стрибнув, значить комп'ютер спав
#    або завис. цей час віднімаю, щоб не рахувати його в "часі аналізу"

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

# прапорці віндовс для сет тред екзекюшн стейт
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

_IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

# лінукс: команда, яка тримає блокування сну, поки живий наш процес
# якщо програма впаде, блокування зникне саме (як кофеїнейт -w PID на маку)
_LINUX_WAIT_FOR_PID = 'while kill -0 {pid} 2>/dev/null; do sleep 5; done'
_LINUX_TAG = "LookBack"


# ф-ція списку команд для блокування сну на лінукс, від кращої до гіршої, не забути її
def _linux_inhibit_commands(pid):
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
    # start() почати блокувати сон, stop() зняти блокування
    # якщо система не підтримує блокування (лінукс без системді
    # чи андроїд без пайджніус), просто пишу повідомлення і нічого не роблю
    # на віндовс блокування прив'язане до потоку, тому start() і stop()
    # треба викликати з одного потоку (в мейн.пай це фоновий потік аналізу), щоб не забути

    def __init__(self):
        self._process = None
        self._windows_active = False
        self._wake_lock = None  # андроїд павер менеджер вейклок

    def start(self):
        try:
            if sys.platform == "darwin":
                # -i не засинати, -w PID: кофеїнейт сам закриється разом з нашою програмою
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

    # ф-ція запуску на лінукс, пробую команди по черзі
    def _start_linux(self):
        for command in _linux_inhibit_commands(os.getpid()):
            try:
                # start_new_session щоб при stop() прибрати і команду, і її ш разом
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
            # завершилась одразу (нема дібас або нема доступу), пробую наступну
        print("[diag] Linux: systemd-inhibit/gnome-session-inhibit недоступні - захисту від сну нема")

    # ф-ція запуску на андроїд, не забути її
    def _start_android(self):
        try:
            from jnius import autoclass
        except ImportError:
            print("[diag] Android: pyjnius недоступний - захисту від сну нема")
            return
        # для цього треба дозвіл WAKE_LOCK у білдозер спек, щоб не забути
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        Context = autoclass("android.content.Context")
        PowerManager = autoclass("android.os.PowerManager")
        power_manager = PythonActivity.mActivity.getSystemService(Context.POWER_SERVICE)
        lock = power_manager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "LookBack:analysis")
        lock.setReferenceCounted(False)
        # страховка: вейклок сам зніметься через 3 години, навіть якщо stop() не викличу
        lock.acquire(3 * 60 * 60 * 1000)
        self._wake_lock = lock

    # ф-ція зняття блокування, не забути її викликати
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
                    self._process.wait(timeout=1)  # прибираю процес, щоб не лишився зомбі
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
    # рахую скільки секунд програма простояла (сон або зависання)
    #
    # раз на tick_sec секунд дивлюсь скільки часу минуло. якщо різниця
    # більша за tick_sec на threshold_sec і більше, додаю її до
    # suspended_sec. дрібні стрибки годинника ігнорую

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

    # ф-ція самого лічильника, тут крутиться цикл, не забути її
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
