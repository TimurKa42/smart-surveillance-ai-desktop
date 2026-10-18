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

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import contextlib
import logging
import math
import multiprocessing
import os
import shutil
import statistics
import struct
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# python-for-android виставляє ANDROID_ARGUMENT в оточення - стандартний
# спосіб визначити, що код виконується на Android. Використовуємо це,
# щоб НЕ вмикати сикання (video.set(CAP_PROP_POS_FRAMES, ...)) на
# Android: на деяких Android-збірках OpenCV воно поводиться
# непередбачувано (снепиться на найближчий keyframe або мовчки не
# спрацьовує - та сама проблема, що описана в коментарі над
# grab_screenshot()). На Mac/Windows сикання працює коректно і дає в
# рази швидшу нарізку довгих відео, бо кадри МІЖ кандидатами взагалі
# не декодуються.
_IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

# Скільки потоків-декодерів максимум. cv2 віддає GIL під час grab()/
# read()/set(), тож кілька VideoCapture в різних потоках реально
# працюють паралельно на різних ядрах. На стрес-тесті (66 хв, файл з
# рідкими keyframe) нарізка йшла 361 с, а збереження скріншотів 178 с -
# і те, й інше майже цілком декодування, яке добре ділиться на потоки.
# Android не чіпаємо (там OpenCV/seek ненадійні - див. коментар вище).
#
# БУЛО фіксовано 4 - число, вигадане "на око", а не виміряне: доки не
# полагодили oversubscription (див. _single_threaded_ffmpeg_decode
# нижче), додавання воркерів понад це все одно нічого не давало б -
# кожен новий VideoCapture лише додавав ще один "претендент" на ті самі
# ядра, які й так уже конкурували між собою. Тепер, коли кожен воркер
# декодує РІВНО одним внутрішнім потоком ffmpeg, сумарна кількість
# потоків декодування дорівнює кількості воркерів "чесно" - тож має
# сенс дати їй рости разом із кількістю ядер, а не впиратись у довільну
# стелю.
#
# Стеля 8 - не "більше завжди краще": усі воркери читають ОДИН і той
# самий файл з диска (різні offset'и), і на HDD (на відміну від SSD)
# забагато паралельних читачів починає програвати через дискові сикання
# між ними - тож не женемось за кількістю ядер БЕЗ обмеження зверху.
PARALLEL_DECODE_WORKERS = min(os.cpu_count() or 4, 8)


# ---------------------------------------------------------------------------
# Перезаявка потоків ffmpeg (thread oversubscription) у паралельній нарізці
# ---------------------------------------------------------------------------
#
# ЗНАЙДЕНО НА РЕАЛЬНОМУ ЛОЗІ (66-хвилинне відео, рідкі keyframe):
#   - однопотоковий прохід сиканням, 601 кандидат: 261.6с
#   - паралельний прохід (4 потоки), 1202 кандидати (УДВІЧІ більше роботи!): 364.2с
# Тобто 4 потоки впорались із удвічі більшим об'ємом РОБОТИ за час, який
# лише на ~40% довший за однопотоковий прохід із удвічі меншим об'ємом.
# Реального 4-разового пришвидшення нема НАВІТЬ близько - навпаки, є
# ознаки того, що потоки один одному заважають.
#
# ПРИЧИНА: cv2.VideoCapture з FFmpeg-бекендом САМ по собі багатопотоковий -
# один-єдиний VideoCapture під капотом уже використовує кілька ядер CPU
# для декодування одного відеопотоку (frame-level threading у libavcodec).
# Коли PARALLEL_DECODE_WORKERS відкриває кілька НЕЗАЛЕЖНИХ VideoCapture
# одночасно (по одному на слайс), КОЖЕН з них знову намагається зайняти
# всі доступні ядра - у підсумку N потоків x (внутрішні потоки ffmpeg
# кожного) змагаються за ті самі фізичні ядра. Це і є thread
# oversubscription: контекст-світчі та боротьба за кеш процесора
# з'їдають виграш від паралелізму, а на слабких/малоядерних машинах
# можуть зробити паралельний шлях повільнішим за однопотоковий - що
# власне й видно в наведених вище цифрах.
#
# РІШЕННЯ: на час, поки декодери працюють ПАРАЛЕЛЬНО (декілька
# VideoCapture одночасно, кожен - свій потік), обмежуємо ffmpeg рівно
# ОДНИМ внутрішнім потоком на кожен VideoCapture через змінну оточення
# OPENCV_FFMPEG_CAPTURE_OPTIONS ("threads;1" - параметр, який FFmpeg-
# бекенд OpenCV передає напряму в avcodec_open2 як AVDictionary). Тоді
# сумарна кількість потоків декодування = PARALLEL_DECODE_WORKERS (по
# одному на слайс), а не PARALLEL_DECODE_WORKERS x (внутрішні потоки
# ffmpeg) - і кожен слайс справді отримує своє окреме ядро, замість
# того щоб ділити ті самі ядра з рештою слайсів.
#
# Змінна оточення читається FFmpeg-бекендом ПРИ КОЖНОМУ відкритті
# VideoCapture (не кешується при імпорті бібліотеки) - тож її можна
# безпечно виставляти прямо перед стартом пулу потоків і повертати
# назад одразу по завершенні. Поза цим "вікном" (однопотокова нарізка
# коротких відео, звичайні скріншоти) змінну НЕ чіпаємо - там, навпаки,
# внутрішня багатопотоковість ffmpeg іде на користь, бо декодер там
# лише один.
#
# _ffmpeg_env_lock захищає від ситуації, коли дві паралельні нарізки
# (напр. основна нарізка кадрів і паралельний grab_screenshots_batch)
# запускаються одночасно з різних місць - без цього одна могла б
# відновити змінну оточення, яку насправді ще потребує інша.
_FFMPEG_CAPTURE_OPTIONS_ENV = "OPENCV_FFMPEG_CAPTURE_OPTIONS"
_ffmpeg_env_lock = threading.Lock()


@contextlib.contextmanager
def _single_threaded_ffmpeg_decode():
    """
    Контекст-менеджер: на час виконання блоку кожен НОВОВІДКРИТИЙ
    cv2.VideoCapture (FFmpeg-бекенд) декодує ОДНИМ внутрішнім потоком
    замість автоматичної багатопотоковості. Використовувати ЛИШЕ
    навколо ділянок, де кілька VideoCapture працюють ОДНОЧАСНО в
    різних потоках (паралельна нарізка/скріншоти) - див. великий
    коментар вище про thread oversubscription.

    Попереднє значення змінної оточення (якщо було) коректно
    повертається по виходу з блоку, навіть якщо всередині стався
    виняток.
    """
    with _ffmpeg_env_lock:
        previous = os.environ.get(_FFMPEG_CAPTURE_OPTIONS_ENV)
        os.environ[_FFMPEG_CAPTURE_OPTIONS_ENV] = "threads;1"
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(_FFMPEG_CAPTURE_OPTIONS_ENV, None)
            else:
                os.environ[_FFMPEG_CAPTURE_OPTIONS_ENV] = previous


# ---------------------------------------------------------------------------
# Захист від зависання cv2 на не-відео файлах (macOS/AVFoundation)
# ---------------------------------------------------------------------------
#
# ПРОБЛЕМА: cv2.VideoCapture(path).isOpened()/.get(...) для файлу, який
# НЕ є відео (наприклад, картинку перетягнули замість відео), на
# більшості бекендів (Linux/FFmpeg, Windows/MSMF) чесно й швидко
# повертає False чи биті метадані. АЛЕ на macOS (бекенд AVFoundation)
# спостерігається, що той самий виклик може ЗАВИСНУТИ НАЗАВЖДИ замість
# помилки - AVFoundation чекає на подію "трек завантажено", яка для
# невідеофайлу просто ніколи не приходить.
#
# get_video_duration_sec() (з _accept_video_path у main.py) і
# dnd_tools.probe_video() (з обробника drag-and-drop) обидва
# викликаються СИНХРОННО в головному потоці Tkinter - навмисно, бо в
# нормальному випадку це читання заголовка, долі мілісекунди. Якщо
# саме тут cv2 зависне - зависає весь Tk event loop: жодна кнопка,
# жодне вікно більше не реагує, і користувачу здається, що програма
# "думає" вічно.
#
# РІШЕННЯ: виконувати цей виклик у окремому демон-потоці з явним
# лімітом часу. Убити застряглий C-виклик усередині OpenCV з Python
# неможливо (немає штатного способу перервати чужий код у
# розширенні) - тож потік, що завис, просто лишається демоном у фоні
# (не заважає роботі застосунку, завершиться сам або доживе до
# закриття програми), а користувачу чесно повертаємо "не вдалося
# перевірити файл за N секунд" замість вічного зависання.
CV2_PROBE_TIMEOUT_SEC = 8.0


def run_with_timeout(func, timeout_sec=CV2_PROBE_TIMEOUT_SEC):
    """
    Виконує func() (без аргументів) в окремому демон-потоці, чекає
    щонайбільше timeout_sec секунд.

    Повертає (result, timed_out: bool).
      - timed_out=False - func() встигла завершитись, result - те, що
        вона повернула (виняток з func() прокидається СЮДИ, у
        викликаючий потік, як і мало б бути при звичайному виклику).
      - timed_out=True - функція не встигла за відведений час; result
        завжди None. Демон-потік лишається доживати у фоні сам.
    """
    result_box = {}

    def target():
        try:
            result_box["value"] = func()
        except Exception as error:  # noqa: BLE001 - прокидаємо в викликаючий потік нижче
            result_box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout_sec)

    if thread.is_alive():
        return None, True

    if "error" in result_box:
        raise result_box["error"]
    return result_box.get("value"), False


def _open_capture(video_path, hw=False, prefer_ffmpeg=False):
    """
    Відкриває відео через OpenCV з ВИМКНЕНИМ авто-поворотом.

    Новіші збірки OpenCV (4.5+, ffmpeg-бекенд - Mac/Windows/Linux) самі
    повертають кадри за метаданими повороту телефонного відео
    (CAP_PROP_ORIENTATION_AUTO=1 за замовчуванням). А ми повертаємо їх
    ще й вручну (_apply_manual_rotation, кут читається з MP4 напряму,
    бо на Android OpenCV цього не вміє) - і виходило ПОДВІЙНЕ обертання:
    вертикальне відео лягало на бік. Вимикаємо авто-поворот, щоб
    єдиним джерелом правди був наш ручний.
    Якщо збірка не знає цієї властивості - set() просто поверне False.

    hw=True - спроба апаратного декодування (VideoToolbox на Mac,
    DXVA/D3D11 на Windows); якщо не вийшло - тихо відкриваємо звичайно.

    prefer_ffmpeg=True - явно просимо FFmpeg-бекенд (cv2.CAP_FFMPEG)
    замість бекенду за замовчуванням. ВАЖЛИВО для паралельної нарізки
    (_extract_slice_sequential, _grab_screenshot_chunk): без явної
    вказівки cv2.VideoCapture(video_path) на Mac резолвиться в
    AVFoundation (звідси й "BGR3" у fourcc по всьому файлу) - а
    _single_threaded_ffmpeg_decode() виставляє змінну оточення, яку
    ЧИТАЄ ЛИШЕ FFmpeg-бекенд. Якщо воркер відкриває капчу через
    AVFoundation - ця змінна для нього просто не існує, і весь
    механізм проти thread oversubscription не спрацьовує (перевірено
    логом: 4 паралельні слайси все одно давали сумарну швидкість
    одного потоку). Ігнорується, якщо hw=True (той шлях і так вже
    явно просить cv2.CAP_FFMPEG).
    """
    video = None
    if hw:
        try:
            video = cv2.VideoCapture(
                video_path, cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY],
            )
            if not video.isOpened():
                video.release()
                video = None
        except Exception:  # noqa: BLE001
            video = None
    elif prefer_ffmpeg:
        try:
            video = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
            if not video.isOpened():
                video.release()
                video = None
        except Exception:  # noqa: BLE001
            video = None
    if video is None:
        video = cv2.VideoCapture(video_path)
    try:
        video.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
    except Exception:  # noqa: BLE001
        pass
    return video


# Апаратне декодування: вирішуємо НЕ навмання, а коротким самозаміром
# (декілька десятків кадрів обома способами) при першому ж відео за
# запуск програми; рішення запам'ятовується до її закриття.
# SMART_SURVEILLANCE_HW_DECODE=1 - завжди вмикати, =0 - завжди вимикати.
_HW_SAMPLE_FRAMES = 45
_HW_MIN_SPEEDUP = 1.3
_hw_decision = None


def _hw_decode_is_faster(video_path):
    global _hw_decision
    if _hw_decision is not None:
        return _hw_decision

    forced = os.environ.get("SMART_SURVEILLANCE_HW_DECODE")
    if _IS_ANDROID or forced == "0":
        _hw_decision = False
        return False
    if forced == "1":
        _hw_decision = True
        return True

    def frames_per_sec(hw):
        video = _open_capture(video_path, hw=hw)
        try:
            if not video.isOpened() or not video.grab():  # перший кадр - розігрів
                return 0.0
            started = time.time()
            count = 0
            while count < _HW_SAMPLE_FRAMES and video.grab():
                count += 1
            return count / max(time.time() - started, 1e-6) if count >= 10 else 0.0
        finally:
            video.release()

    def measure():
        return frames_per_sec(False), frames_per_sec(True)

    try:
        result, timed_out = run_with_timeout(measure)
    except Exception:  # noqa: BLE001
        result, timed_out = None, True
    if timed_out or not result:
        _hw_decision = False
    else:
        sw_fps, hw_fps = result
        _hw_decision = hw_fps > sw_fps * _HW_MIN_SPEEDUP
        logger.debug(f"[diag] самозамір декодування: програмне {sw_fps:.0f} к/с, "
                     f"апаратне {hw_fps:.0f} к/с -> апаратне {'ВМИКАЄМО' if _hw_decision else 'не потрібне'}")
    return _hw_decision


# Скільки потоків для АСИНХРОННОГО запису full-res кадрів на диск (див.
# _save_full_frame нижче) - окремий, набагато менший пул від
# PARALLEL_DECODE_WORKERS: тут паралелиться лише сам imwrite (диск),
# а не декодування відео.
_FULL_FRAME_SAVE_WORKERS = 4

# Паралельну нарізку слайсами вмикаємо лише для довгих відео: кожен слайс
# починається з повільного seek, а декодер ffmpeg і так використовує
# кілька ядер - для короткого ролика це чистий програш.
_PARALLEL_MIN_DURATION_SEC = 120.0


def _write_image(path, frame):
    """Надійно зберігає кадр у файл і повертає True/False.

    Чому не cv2.imwrite: на Windows він не вміє писати у шляхи з
    не-ASCII символами (кирилиця в імені теки чи користувача) - і
    повертає False БЕЗ винятку, тож викликач думав, що файл збережено.
    Тут кадр кодується в пам'яті (imencode) і пишеться numpy.tofile,
    який працює з будь-яким шляхом. Помилка запису не мовчить - іде в лог."""
    try:
        ext = os.path.splitext(path)[1] or ".jpg"
        ok, buffer = cv2.imencode(ext, frame)
        if not ok:
            logger.warning(f"[diag] imencode не вдався для {path}")
            return False
        buffer.tofile(path)
        return os.path.exists(path)
    except Exception as error:  # noqa: BLE001
        logger.warning(f"[diag] не вдалось зберегти кадр {path}: {error}")
        return False


def _save_full_frame(full_dir, frame, pool=None):
    """Зберігає ПОВНОРОЗМІРНИЙ (уже повернутий) кадр у теку кешу і
    повертає шлях, або None, якщо теки нема. Так само, як
    grab_screenshot(): cv2.imwrite з якістю за замовчуванням -
    скріншот у звіті потім просто переноситься звідси, без другого
    декодування відео.

    pool - необов'язковий ThreadPoolExecutor: якщо переданий, сам запис
    JPEG на диск (cv2.imwrite) віддається йому АСИНХРОННО, а не
    виконується тут-таки в потоці декодування, який це викликав.
    cv2.imwrite відпускає GIL - кілька таких записів реально йдуть
    паралельно на диск, поки декодер вже читає наступний кандидат,
    замість того щоб чекати завершення попереднього запису.

    Шлях повертається ОДРАЗУ (ім'я файлу - uuid, згенерований тут же,
    ще ДО самого запису) - на відміну від синхронного режиму, тут
    неможливо дізнатись про невдалий запис одразу (лише в консоль,
    див. _write() нижче). Це прийнятний компроміс: extract_frames()
    дочекається завершення ВСІХ запусків пулу (pool.shutdown(wait=True))
    ПЕРЕД тим, як повернути готові frames викликачу - тобто до моменту,
    коли хтось реально відкриє full_path (звіт/історія формуються вже
    ПІСЛЯ завершення всього аналізу), запис або встигне, або
    невдача вже буде в консолі.

    Без pool (за замовчуванням) поведінка синхронна і не змінилась."""
    if not full_dir:
        return None
    path = os.path.join(full_dir, f"{uuid.uuid4().hex}.jpg")

    if pool is not None:
        def _write():
            try:
                if not _write_image(path, frame):
                    logger.warning(f"[diag] не вдалось зберегти full-res кадр: {path}")
            except Exception as error:  # noqa: BLE001
                logger.warning(f"[diag] не вдалось зберегти full-res кадр {path}: {error}")
        pool.submit(_write)
        return path

    try:
        if _write_image(path, frame):
            return path
    except Exception:  # noqa: BLE001
        pass
    return None


def _cpu_count():
    return os.cpu_count() or 1


def _decode_worker_count(item_count, min_items_per_worker=1):
    """Скільки потоків-декодерів має сенс запустити: не більше
    PARALLEL_DECODE_WORKERS, не більше ядер і не більше, ніж по
    min_items_per_worker елементів на потік. 1 - без паралелізму."""
    if _IS_ANDROID:
        return 1
    return max(1, min(PARALLEL_DECODE_WORKERS, _cpu_count(), item_count // max(1, min_items_per_worker)))


# ---------------------------------------------------------------------------
# Читання кута повороту напряму з MP4-файлу (без OpenCV)
# ---------------------------------------------------------------------------
#
# ЧОМУ ЦЕ ТУТ Є:
# OpenCV на Android (CAP_PROP_ORIENTATION_META) не вміє читати кут
# повороту з телефонних відео - завжди повертає 0, навіть якщо відео
# насправді треба повернути на 90/180/270 градусів. На Mac (desktop
# збірка OpenCV) те саме працює нормально - ось звідки різниця в
# поведінці застосунку на різних платформах.
#
# Рішення: дістаємо кут самі, читаючи "сирий" MP4-файл як послідовність
# байтів - без OpenCV і без будь-яких зовнішніх бібліотек. Це працює
# однаково на будь-якій платформі.
#
# ЩО ТАКЕ MP4-ФАЙЛ ЗСЕРЕДИНИ (коротко):
# MP4-файл складається з "боксів" (box) - шматків даних, кожен з яких
# має заголовок:
#   [4 байти: розмір цього боксу] [4 байти: назва боксу, напр. b"moov"] [дані...]
# Бокси можуть лежати один в одному, як матрьошка. Нам потрібен шлях:
#   moov -> trak -> tkhd
# "moov" - загальний контейнер з інформацією про відео.
# "trak" - один трек (доріжка). Треків може бути кілька: відео, аудіо.
# "tkhd" - заголовок треку. Саме тут лежить кут повороту та розміри
#          кадру (width/height), якщо це відео-трек.


def _read_box_header(file, offset):
    """
    Читає заголовок ОДНОГО боксу за вказаним зсувом (offset) у файлі.

    Повертає (назва_боксу, розмір_боксу, зсув_де_починаються_дані_боксу)
    або None, якщо дочитали до кінця файлу / заголовок биний.
    """
    file.seek(offset)
    header = file.read(8)
    if len(header) < 8:
        return None  # файл закінчився - боксів більше нема

    size = int.from_bytes(header[0:4], byteorder="big")
    box_type = header[4:8]
    data_start = offset + 8

    if size == 1:
        # Спеціальний випадок: розмір боксу не влазить у 4 байти,
        # тому реальний розмір (64-бітний) лежить одразу ПІСЛЯ заголовка.
        big_size_bytes = file.read(8)
        if len(big_size_bytes) < 8:
            return None
        size = int.from_bytes(big_size_bytes, byteorder="big")
        data_start = offset + 16
    elif size == 0:
        # size == 0 означає "цей бокс триває до самого кінця файлу"
        # (так буває, наприклад, з mdat, якщо файл писали "на льоту").
        file.seek(0, 2)  # перестрибуємо в кінець файлу
        size = file.tell() - offset

    return box_type, size, data_start


def _find_box(file, box_type, start, end):
    """
    Шукає бокс з назвою box_type серед "сусідів" у діапазоні [start, end)
    ОДНОГО рівня вкладеності (не заглиблюючись у самі бокси).

    Наприклад: _find_box(f, b"moov", 0, розмір_файлу) знайде "moov",
    навіть якщо перед ним стоять інші top-level бокси (ftyp, free, mdat) -
    і навіть якщо moov лежить В КІНЦІ файлу (так буває у перекодованих
    відео).

    Повертає (зсув_даних_боксу, розмір_даних_боксу) або None, якщо
    не знайшли.
    """
    offset = start
    while offset < end:
        header = _read_box_header(file, offset)
        if header is None:
            return None

        box_type_found, box_size, data_start = header
        if box_size <= 0:
            return None  # биний бокс - розмір не може бути від'ємним/нульовим

        data_size = box_size - (data_start - offset)

        if box_type_found == box_type:
            return data_start, data_size

        offset += box_size  # перестрибуємо одразу до НАСТУПНОГО боксу

    return None


# Кут повороту "закодований" у матриці tkhd чотирма числами (a, b, c, d).
# Це стандартні значення, які реально пишуть телефони/редактори відео.
# Формат чисел - fixed-point 16.16 (число ціле, помножене на 65536).
_ONE = 0x10000  # 1.0 у форматі fixed-point 16.16

_ROTATION_MATRIX_TO_ANGLE = {
    (_ONE, 0, 0, _ONE): 0,
    (0, _ONE, -_ONE, 0): 90,
    (-_ONE, 0, 0, -_ONE): 180,
    (0, -_ONE, _ONE, 0): 270,
}


def _matrix_to_angle(a, b, c, d):
    """
    Перетворює 4 числа матриці на кут повороту (0/90/180/270).

    Якщо матриця не збігається ЖОДНИМ з очікуваних 4 варіантів (це
    може бути дзеркальне відображення або щось екзотичне) - чесно
    повертаємо 0 замість того, щоб намагатись вгадати.
    """
    return _ROTATION_MATRIX_TO_ANGLE.get((a, b, c, d), 0)


def _read_tkhd_fields(file, data_start, data_size):
    """
    Читає з одного tkhd-боксу дві речі:
      1) width, height - потрібні лише щоб зрозуміти, чи це відео-трек
         (у аудіо-треку тут завжди 0x0). УВАГА: це fixed-point 16.16,
         так само як і матриця нижче (реальне значення в 65536 разів
         менше за те, що повертає ця функція) - але оскільки нам
         важлива лише перевірка "!= 0", а не саме число, ділити на
         0x10000 тут навмисно не стали. Якщо колись знадобляться
         справжні width/height у пікселях - не забудь про це ділення.
      2) матрицю (a, b, c, d) - з неї рахуємо кут повороту

    Повертає (width, height, a, b, c, d) або None, якщо бокс закороткий/биний.
    """
    file.seek(data_start)
    payload = file.read(data_size)
    if len(payload) < 1:
        return None

    version = payload[0]

    # Розмір "часових" полів (creation/modification/duration) залежить
    # від версії боксу: version=0 -> 4-байтні поля, version=1 -> 8-байтні.
    # Якщо це не врахувати, всі наступні зсуви (в т.ч. матриця і
    # width/height) "поплетуть" і ми прочитаємо сміття замість чисел.
    if version == 1:
        time_fields_size = 8 + 8 + 4 + 4 + 8  # creation+modification+track_id+reserved+duration
    else:
        time_fields_size = 4 + 4 + 4 + 4 + 4

    # Після часових полів завжди йде однаковий для обох версій блок:
    # reserved(8) + layer(2) + alternate_group(2) + volume(2) + reserved(2) = 16 байт
    fixed_block_size = 8 + 2 + 2 + 2 + 2

    matrix_offset = 4 + time_fields_size + fixed_block_size  # 4 = version+flags
    matrix_size = 36  # 9 чисел по 4 байти

    matrix_end = matrix_offset + matrix_size
    width_height_end = matrix_end + 8  # width(4) + height(4)

    if len(payload) < width_height_end:
        return None  # бокс коротший, ніж очікувалось - щось не так, здаємось

    def read_fixed_point(raw_bytes):
        # Fixed-point 16.16 і ЗІ ЗНАКОМ (потрібно для -1.0 у матриці
        # при поворотах на 180). "big" - порядок байтів, як завжди в MP4.
        return int.from_bytes(raw_bytes, byteorder="big", signed=True)

    # З 9 чисел матриці нам потрібні лише перші 4 (a, b, c, d) -
    # решта 5 для звичайного повороту телефонної камери завжди стандартні
    # і на кут не впливають.
    a = read_fixed_point(payload[matrix_offset : matrix_offset + 4])
    b = read_fixed_point(payload[matrix_offset + 4 : matrix_offset + 8])
    c = read_fixed_point(payload[matrix_offset + 12 : matrix_offset + 16])
    d = read_fixed_point(payload[matrix_offset + 16 : matrix_offset + 20])

    width = int.from_bytes(payload[matrix_end : matrix_end + 4], byteorder="big")
    height = int.from_bytes(payload[matrix_end + 4 : matrix_end + 8], byteorder="big")

    return width, height, a, b, c, d


def read_rotation_from_mp4(video_path):
    """
    Головна функція: відкриває MP4-файл і повертає кут повороту
    ВІДЕО-треку (0, 90, 180 або 270).

    Якщо щось пішло не так (файл биний, не MP4, нестандартна структура) -
    ТИХО повертає 0, так само як поводиться застосунок зараз, коли
    OpenCV не може прочитати метадані. Жодних винятків назовні -
    аналіз відео не повинен падати через це.
    """
    try:
        with open(video_path, "rb") as file:
            file.seek(0, 2)  # у кінець файлу, щоб дізнатись його розмір
            file_size = file.tell()

            moov_location = _find_box(file, b"moov", 0, file_size)
            if moov_location is None:
                return 0  # немає moov - не схоже на нормальний MP4

            moov_start, moov_size = moov_location
            moov_end = moov_start + moov_size

            # У файлі може бути кілька "trak" (наприклад відео + аудіо).
            # Йдемо по них по черзі і беремо ПЕРШИЙ, у якого в tkhd
            # стоять ненульові width/height - це і є відео-трек
            # (в аудіо-треку ці поля завжди 0x0).
            offset = moov_start
            while offset < moov_end:
                trak_location = _find_box(file, b"trak", offset, moov_end)
                if trak_location is None:
                    break  # більше треків нема

                trak_start, trak_size = trak_location
                trak_end = trak_start + trak_size

                tkhd_location = _find_box(file, b"tkhd", trak_start, trak_end)
                if tkhd_location is not None:
                    tkhd_start, tkhd_size = tkhd_location
                    fields = _read_tkhd_fields(file, tkhd_start, tkhd_size)
                    if fields is not None:
                        width, height, a, b, c, d = fields
                        if width != 0 and height != 0:
                            # Знайшли відео-трек - рахуємо кут і виходимо.
                            return _matrix_to_angle(a, b, c, d)

                # Це був не той trak (аудіо чи щось інше) - йдемо до наступного.
                offset = trak_start + trak_size

            return 0  # жодного відео-треку з нормальним tkhd не знайшли
    except (OSError, struct.error, ValueError):
        # Битий файл, обірваний запис, дивний контейнер - що завгодно.
        # Не валимо аналіз відео через це, просто кажемо "без повороту".
        return 0


def _read_mvhd_duration(file, moov_start, moov_end):
    """
    Читає duration/timescale з боксу "mvhd" (Movie Header) - там лежить
    ТОЧНА тривалість усього відео, як її бачить сам контейнер, а не
    порахована окремо для кожного треку.

    ЧОМУ ЦЕ ПОТРІБНО: video.get(cv2.CAP_PROP_FRAME_COUNT) в OpenCV
    для багатьох mp4-файлів (особливо перекодованих/скачаних, як
    типовий "videoplayback.mp4") повертає ЗАНИЖЕНЕ число кадрів.
    Порахована з нього тривалість (frame_count/fps) виходить у рази
    менша за реальну - і вся "розумна" нарізка кадрів у extract_frames()
    стискається в перші секунди відео замість того, щоб охопити його
    цілком. Читання duration напряму з mvhd не залежить від того, як
    OpenCV рахує кадри, і працює навіть на файлах, де метадані кадрів
    биті чи неповні.
    """
    mvhd_location = _find_box(file, b"mvhd", moov_start, moov_end)
    if mvhd_location is None:
        return None

    data_start, data_size = mvhd_location
    file.seek(data_start)
    payload = file.read(data_size)
    if len(payload) < 1:
        return None

    version = payload[0]
    try:
        if version == 1:
            # version(1) + flags(3) + creation_time(8) + modification_time(8) = 20
            timescale = int.from_bytes(payload[20:24], byteorder="big")
            duration = int.from_bytes(payload[24:32], byteorder="big")
        else:
            # version(1) + flags(3) + creation_time(4) + modification_time(4) = 12
            timescale = int.from_bytes(payload[12:16], byteorder="big")
            duration = int.from_bytes(payload[16:20], byteorder="big")
    except (IndexError, struct.error):
        return None

    if timescale <= 0:
        return None
    return duration / timescale


def read_duration_from_mp4(video_path):
    """
    Точна тривалість відео (у секундах), прочитана напряму з mp4-файлу
    (бокс mvhd). Повертає None, якщо файл не mp4 / moov не знайдено /
    бокс биний - у цьому разі викликаючий код сам вирішує, як рахувати
    тривалість далі (fallback на total_frame_count/fps).

    Працює тим самим "тихим" принципом, що й read_rotation_from_mp4:
    жодних винятків назовні, аналіз відео не повинен падати через це.
    """
    try:
        with open(video_path, "rb") as file:
            file.seek(0, 2)
            file_size = file.tell()

            moov_location = _find_box(file, b"moov", 0, file_size)
            if moov_location is None:
                return None

            moov_start, moov_size = moov_location
            return _read_mvhd_duration(file, moov_start, moov_start + moov_size)
    except (OSError, struct.error, ValueError):
        return None


# ---------------------------------------------------------------------------
# Надійний fps: video.get(cv2.CAP_PROP_FPS) іноді бреше
# ---------------------------------------------------------------------------
#
# ПРОБЛЕМА: на деяких файлах (спостережено на Mac, backend AVFoundation)
# OpenCV повертає fps, занижений РІВНО У ЦІЛУ КІЛЬКІСТЬ РАЗІВ відносно
# реального (класичний випадок - 59.94fps відео, яке репортиться як
# "номінальні" 29.97). Це критично, бо:
#   - timestamp_sec = frame_index / fps виходить УДВІЧІ більший за
#     реальне місце в відео - підписи часу в звіті брешуть;
#   - segment_index (гарантія покриття всієї довжини) рахується від
#     роздутого timestamp_sec, а порівнюється з segment_sec, порахованим
#     від ЧЕСНОГО duration_sec (mvhd) - через це лічильник відрізків
#     добігає до кінця, реально пройшовши лише ПОЛОВИНУ кадрів відео.
#
# РІШЕННЯ: раз у нас вже є надійні duration_sec (mvhd) і
# total_frame_count (OpenCV, для кількості кадрів зазвичай точний),
# рахуємо "ефективний" fps = total_frame_count / duration_sec і
# звіряємо з тим, що повернув OpenCV. Якщо розбіжність підозріло
# близька до цілої кратності (2x, 3x...) - довіряємо розрахунковому
# значенню. Використовується ОДНАКОВО в extract_frames(),
# grab_screenshot() і grab_screenshots_batch() - інакше в різних
# місцях виходили б різні fps і мітки часу/кадри розсинхронізувались б.

# Допустиме відхилення кратності від цілого числа (частка від нього),
# щоб відрізняти РЕАЛЬНИЙ баг репортингу (майже точна кратність) від
# звичайної похибки округлення duration/frame_count.
_FPS_MISMATCH_TOLERANCE = 0.05

# Кратність вище цього значення вважаємо вже неправдоподібною (швидше
# ознака биних метаданих, ніж баг репортингу fps) - у такому разі
# довіряти розрахунковому fps ризикованіше, ніж сирому.
_FPS_MISMATCH_MAX_MULTIPLE = 4


def _is_suspicious_mismatch(raw_fps, effective_fps):
    """
    True, якщо raw_fps і effective_fps відрізняються рівно (з невеликим
    допуском) у ціле число разів (2x, 3x, ...) - це і є "підпис" бага
    репортингу fps, а не звичайна похибка округлення.
    """
    if raw_fps <= 0 or effective_fps <= 0:
        return False

    ratio = effective_fps / raw_fps
    if ratio < 1:
        ratio = 1 / ratio

    nearest_multiple = round(ratio)
    if nearest_multiple < 2 or nearest_multiple > _FPS_MISMATCH_MAX_MULTIPLE:
        return False

    return abs(ratio - nearest_multiple) / nearest_multiple <= _FPS_MISMATCH_TOLERANCE


def _frame_count_is_trustworthy(video, total_frame_count, duration_sec, raw_fps):
    """
    Перевіряє, чи справді у файлі є заявлена OpenCV кількість кадрів
    (total_frame_count), а не просто довіряє цифрі "на слово".

    ЧОМУ ЦЕ ПОТРІБНО: _is_suspicious_mismatch() бачить лише СПІВВІДНОШЕННЯ
    raw_fps і total_frame_count/duration - і не може розрізнити ДВІ різні
    причини однієї й тієї самої кратної розбіжності:
      (a) raw_fps дійсно занижений бекендом (реальний баг, задокументований
          вище) - тоді total_frame_count ПРАВИЛЬНИЙ, і ЗА межею того, що
          дав би raw_fps * duration_sec, реально є ЩЕ контент;
      (b) total_frame_count дійсно роздутий (буває на перекодованих/
          скачаних mp4, напр. "videoplayback.mp4" з YouTube) - тоді
          ЗА цією межею контенту вже нема.

    РАНІШЕ тут сикали майже до САМОГО total_frame_count і дивились, чи
    video.read() поверне success=True. НА ПРАКТИЦІ (AVFoundation на
    Mac, файл "videoplayback.mp4") це давало ХИБНОПОЗИТИВНИЙ результат:
    сикання ЗА межу реального відео мовчки "прилипає" до одного з
    останніх кадрів, і read() однаково повертає success=True - тому
    роздутий total_frame_count (напр. 240270 замість реальних ~120135)
    хибно підтверджувався. Наслідок: уся нарізка рахувала мітки часу
    за вдвічі завищеним fps, і КОЖНА мітка часу в звіті виходила рівно
    ВДВІЧІ меншою за реальну (звідси і "покриття лише 10-30 хв замість
    усієї години" - насправді кадри бралися по всьому відео, просто з
    підписами вдвічі ближче до нуля, ніж мало бути).

    ТЕПЕР перевіряємо інакше: рахуємо межу "меншого" кандидата
    (raw_fps * duration_sec - duration_sec тут завжди чесний, з mvhd) і
    сикаємо НЕ до заявленого total_frame_count, а трохи ЗА цю межу.
    Якщо там дійсно є ЩЕ контент (кадр відрізняється від кадру САМЕ НА
    межі) - total_frame_count правдивий (case a). Якщо кадру за межею
    нема (read() провалюється) або він ІДЕНТИЧНИЙ прикордонному (ознака
    "прилипання" сикання до останнього реального кадру) - total_frame_count
    роздутий (case b), довіряти йому не можна.

    Повертає True, якщо total_frame_count підтвердився, False - якщо ні.
    """
    raw_frame_estimate = int(duration_sec * raw_fps)
    if raw_frame_estimate <= 0 or raw_frame_estimate >= total_frame_count - 1:
        # Кандидати майже не відрізняються - нема що звіряти, лишаємось
        # при попередній (довірливій) поведінці.
        return True

    # ВАЖЛИВО (знайдено на реальному файлі "videoplayback.mp4"): усе
    # порівняння нижче саме через сикання (CAP_PROP_POS_FRAMES) - а це
    # ТОЧНО той самий різновид сикання, що на файлах з рідкими keyframe
    # (AVFoundation на Mac) "прилипає" до найближчого опорного кадру
    # замість реального переходу. Якщо сикання на цьому файлі взагалі
    # ненадійне (перевіряємо тим самим _probe_seek_cost, яким нижче за
    # течією extract_frames() вирішує use_seek) - порівняння
    # boundary/beyond нижче може хибно побачити "різний контент" просто
    # тому, що сикнуло до ДВОХ РІЗНИХ найближчих keyframe, а не тому,
    # що за межею дійсно є ще відео. У такому разі чесніше одразу
    # визнати total_frame_count непідтвердженим (case b) - роздутий
    # лічильник кадрів на практиці зустрічається саме на файлах з
    # рідкими keyframe (перекодовані/скачані mp4) значно частіше, ніж
    # дійсно занижений raw_fps (case a).
    seek_cost = _probe_seek_cost(video, total_frame_count)
    if seek_cost is None or seek_cost > _SEEK_SLOW_THRESHOLD_SEC:
        return False

    boundary_index = raw_frame_estimate
    beyond_index = min(boundary_index + 50, total_frame_count - 1)

    try:
        video.set(cv2.CAP_PROP_POS_FRAMES, boundary_index)
        ok_boundary, frame_boundary = video.read()
        video.set(cv2.CAP_PROP_POS_FRAMES, beyond_index)
        ok_beyond, frame_beyond = video.read()
    except cv2.error:
        return False
    finally:
        # Повертаємо позицію на початок - виклик іде ДО реальної нарізки,
        # і наступний код (extract_frames/grab_screenshot) сам розставить
        # позицію заново, але краще не лишати VideoCapture в дивному стані.
        video.set(cv2.CAP_PROP_POS_FRAMES, 0)

    if not ok_boundary or not ok_beyond:
        return False  # за межею "малого" кандидата контенту вже нема

    # Порівнюємо кадри у зменшеному розмірі (дешево, 32x32 у відтінках
    # сірого) - щоб відрізнити СПРАВДІ інший кадр від того самого, до
    # якого "прилипло" сикання за межею реального відео.
    small_boundary = cv2.resize(cv2.cvtColor(frame_boundary, cv2.COLOR_BGR2GRAY), (32, 32))
    small_beyond = cv2.resize(cv2.cvtColor(frame_beyond, cv2.COLOR_BGR2GRAY), (32, 32))
    diff = cv2.absdiff(small_boundary, small_beyond)
    return cv2.countNonZero(diff) > 0


def _resolve_fps_cache_key(video_path):
    """(шлях, розмір, mtime) файлу - міняється, якщо файл перезаписали
    під тим самим іменем, і сам є унікальним для незмінного файлу."""
    try:
        stat = os.stat(video_path)
        return (video_path, stat.st_size, stat.st_mtime)
    except OSError:
        return None  # файл зник/недоступний - просто не кешуємо цей виклик


# Кеш результату _resolve_fps() по файлу - _frame_count_is_trustworthy()
# всередині може робити кілька додаткових seek-проб (сотні мілісекунд),
# а сам _resolve_fps() у межах ОДНОГО аналізу викликається по кілька
# разів для ОДНОГО Й ТОГО Ж файлу (get_video_duration_sec() на етапі
# вибору файлу -> extract_frames() -> grab_screenshots_batch() для
# звіту), щоразу відкриваючи відео заново. Той самий файл на диску
# завжди дасть той самий результат, тож рахувати двічі-тричі те саме -
# зайва робота.
#
# Кеш живе всю сесію застосунку (процес не перезапускається між
# відео) - без обмеження розміру він ріс би необмежено, якщо за один
# запуск проаналізувати багато РІЗНИХ файлів поспіль. Повноцінний LRU
# для цього сценарію - overkill (файлів за одну сесію реально
# одиниці-десятки, не тисячі), тож досить простого запобіжника:
# при досягненні _RESOLVE_FPS_CACHE_MAX_SIZE кеш просто повністю
# очищається перед додаванням нового запису. Це означає втрату
# "гарячих" записів для файлів, з якими вже попрацювали, - прийнятна
# ціна за те, щоб кеш не ріс без обмежень.
_resolve_fps_cache = {}
_RESOLVE_FPS_CACHE_MAX_SIZE = 64

# Кеш ЗАМІРІВ (НЕ готового рішення!) для цього файлу - той самий ключ
# (шлях, розмір, mtime), що й _resolve_fps_cache вище. Пробне сикання
# (_probe_seek_cost + _measure_sequential_fps) і бенчмарк апаратного
# декодування (_hw_decode_is_faster) разом коштують кілька секунд (на
# короткому 10-секундному ролику - це помітна частка від УСЬОГО часу
# нарізки, а не лише секунди на тлі кількох хвилин, як на довгому
# відео). Самі ВИМІРЮВАННЯ (скільки коштує один seek, скільки fps дає
# послідовний прохід, чи швидше апаратне декодування) для ОДНОГО Й
# ТОГО Ж файлу детерміновані (та сама доріжка, той самий диск) - тож
# повторний аналіз того самого відео (типово під час
# тестування/демонстрації) бере ЗАМІРИ з кешу й одразу йде рахувати
# кадри, без витрати часу на проби.
#
# ВАЖЛИВО: тут НЕ кешується підсумкове use_seek! На відміну від
# замірів, підсумкове рішення "сикати чи йти послідовно" залежить ще
# й від candidate_count (max_frames), який змінюється від виклику до
# виклику (різні рівні "Швидкий"/"Слабкий"/.../"Максимальний" на
# тому самому файлі) - тому порівняння
# seek_total_est < seq_total_est*0.8 перераховується заново на КОЖЕН
# виклик extract_frames() з актуальним candidate_count, навіть коли
# самі заміри взяті з кешу.
_decode_strategy_cache = {}
_DECODE_STRATEGY_CACHE_MAX_SIZE = 64


def _resolve_fps(video, video_path):
    """Кешуюча обгортка над _resolve_fps_uncached() - див. коментар над
    _resolve_fps_cache вище. При влучанні в кеш "video" взагалі не
    чіпається - лише коли треба порахувати заново."""
    cache_key = _resolve_fps_cache_key(video_path)
    if cache_key is not None and cache_key in _resolve_fps_cache:
        return _resolve_fps_cache[cache_key]

    result = _resolve_fps_uncached(video, video_path)
    if cache_key is not None:
        if len(_resolve_fps_cache) >= _RESOLVE_FPS_CACHE_MAX_SIZE:
            logger.debug(
                f"[diag] _resolve_fps_cache: досягнуто ліміт {_RESOLVE_FPS_CACHE_MAX_SIZE} "
                f"записів - очищаю кеш"
            )
            _resolve_fps_cache.clear()
        _resolve_fps_cache[cache_key] = result
    return result


def _resolve_fps_uncached(video, video_path):
    """
    Повертає (fps, duration_sec, total_frame_count) - узгоджену трійку
    значень замість сирого video.get(cv2.CAP_PROP_FPS).

    Якщо duration_sec (mvhd) і total_frame_count відомі, звіряємо сирий
    fps з розрахунковим (total_frame_count / duration_sec). При
    підозрілій кратності (див. _is_suspicious_mismatch) це ЩЕ НЕ означає,
    що винен саме fps - симетрично можливо, що це total_frame_count
    роздутий (типово для перекодованих/скачаних mp4), а raw_fps весь
    час був правильний. Тому перед тим, як довіритись розрахунковому
    fps, перевіряємо емпірично (_frame_count_is_trustworthy), чи
    справді у файлі стільки кадрів, скільки каже OpenCV. На Android
    сикання ненадійне саме по собі (див. коментар біля _IS_ANDROID) -
    там цю перевірку не робимо і лишаємось на попередній, менш точній
    логіці "довіряти розрахунковому".

    Якщо mvhd недоступний (не mp4, битий файл) або total_frame_count
    не читається - чесний відкат на сирий fps, без жодних розрахунків
    "навмання".
    """
    raw_fps = video.get(cv2.CAP_PROP_FPS) or 25.0

    # video.get(CAP_PROP_FRAME_COUNT) для файлів, які НЕ є відео
    # (наприклад, коли VideoCapture помилково "відкриває" звичайну
    # картинку як відео з одним кадром), повертає не 0, а відверто
    # биний лічильник - на практиці спостерігалось значення, рівне
    # INT64_MIN (-9223372036854775808). Довіряти такому числу не
    # можна: нижче за течією (segment_sec, use_seek, timestamp_sec)
    # воно трактується як "кількість кадрів відома і додатна" - і,
    # наприклад, рахуючи duration_sec = total_frame_count / raw_fps,
    # дає гігантську ВІД'ЄМНУ "тривалість", яка мовчки проходить повз
    # перевірки на макс. тривалість у main.py (від'ємне число менше
    # порога, хоч і не нуль). Тому одразу відкидаємо все, що явно не
    # схоже на правдоподібну кількість кадрів, і трактуємо це як
    # "невідомо" (0) - той самий сентинел, що й коли OpenCV взагалі не
    # зміг прочитати лічильник.
    raw_total_frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    total_frame_count = raw_total_frame_count if 0 < raw_total_frame_count < 100_000_000 else 0

    duration_sec = read_duration_from_mp4(video_path)

    if duration_sec and total_frame_count > 0:
        effective_fps = total_frame_count / duration_sec
        if _is_suspicious_mismatch(raw_fps, effective_fps):
            if not _IS_ANDROID and not _frame_count_is_trustworthy(video, total_frame_count, duration_sec, raw_fps):
                # total_frame_count не підтвердився - справжня причина
                # розбіжності не в fps (raw_fps весь час був правильний),
                # а в роздутому total_frame_count. Перераховуємо його
                # від чесних raw_fps і duration_sec замість сирого
                # значення OpenCV.
                corrected_total = int(round(duration_sec * raw_fps))
                logger.debug(
                    f"[diag] fps підозрілий, але total_frame_count={total_frame_count} "
                    f"не підтвердився сиканням - лишаємо raw_fps={raw_fps:.2f}, "
                    f"виправляємо total_frame_count -> {corrected_total}"
                )
                return raw_fps, duration_sec, corrected_total

            logger.debug(
                f"[diag] fps підозрілий: OpenCV(CAP_PROP_FPS)={raw_fps:.2f} "
                f"розрахунковий(total_frame_count/duration)={effective_fps:.2f} "
                f"- використовуємо розрахунковий"
            )
            return effective_fps, duration_sec, total_frame_count
        return raw_fps, duration_sec, total_frame_count

    if not duration_sec:
        duration_sec = total_frame_count / raw_fps if raw_fps > 0 else 0
    return raw_fps, duration_sec, total_frame_count


def get_video_duration_sec(video_path):
    """
    Тривалість відео в секундах - тонка обгортка над _resolve_fps(),
    яка вже вміє надійно її визначати (mvhd-бокс mp4, з відкатом на
    total_frame_count/fps).

    Повертає 0.0, якщо тривалість визначити не вдалось (не mp4/битий
    файл І одночасно total_frame_count або fps не читаються) - це
    той самий "невідомо", який _resolve_fps() віддає в такому
    випадку, а НЕ законна тривалість 0 секунд (відео нульової довжини
    відкрити для аналізу однаково не можна, тому таке значення тут
    ніколи не буває "справжнім"). З тієї самої причини 0.0
    повертається і якщо перевірка не встигла за CV2_PROBE_TIMEOUT_SEC
    (див. run_with_timeout) - викликаючий код (main.py,
    _accept_video_path) в обох випадках однаково запитає користувача,
    чи продовжувати з невідомою тривалістю.

    Не піднімає власних винятків при вже відкритому файлі - якщо
    cv2.VideoCapture взагалі не зміг відкрити video_path, кидає
    ValueError так само, як extract_frames().

    ВАЖЛИВО: сам виклик cv2.VideoCapture()/.isOpened() виконується не
    напряму, а через run_with_timeout() - ця функція завжди
    викликається СИНХРОННО в ГОЛОВНОМУ потоці Tkinter (main.py,
    _accept_video_path), і на деяких бекендах (macOS/AVFoundation) той
    самий виклик для файлу, що не є відео, може зависнути НАЗАВЖДИ
    замість чесної помилки - без таймауту зависло б усе вікно
    програми, а не лише ця перевірка (див. коментар над
    CV2_PROBE_TIMEOUT_SEC вище).
    """
    def probe():
        video = _open_capture(video_path)
        if not video.isOpened():
            raise ValueError(f"Не вдалось відкрити відео: {video_path}")
        try:
            _, duration_sec, _ = _resolve_fps(video, video_path)
        finally:
            video.release()
        return duration_sec or 0.0

    result, timed_out = run_with_timeout(probe)
    if timed_out:
        logger.warning(
            f"[diag] get_video_duration_sec: перевірка файлу не відповіла за "
            f"{CV2_PROBE_TIMEOUT_SEC:.0f}с - вважаємо тривалість невідомою: {video_path}"
        )
        return 0.0
    return result


def _prepare_orientation(video_path):
    """
    Телефонні відео майже завжди мають метадані повороту - камеру
    тримали "на боці", а плеєр показує відео вертикально/горизонтально
    ЗАВДЯКИ цій метадані. OpenCV за замовчуванням її ІГНОРУЄ і віддає
    сирі, неповернуті кадри.

    РАНІШЕ тут кут читався через video.get(CAP_PROP_ORIENTATION_META).
    На Android ця властивість НІКОЛИ не працює правильно - завжди
    повертає 0, навіть коли відео насправді треба повернути. Через
    це на телефоні всі вертикальні відео лягали "на бік" у звіті,
    хоча той самий код на Mac (desktop-збірка OpenCV) працював
    нормально.

    ТЕПЕР кут читається напряму з файлу через read_rotation_from_mp4()
    (дивись коментар над цією функцією вище) - без участі OpenCV,
    тому працює однаково на будь-якій платформі.
    """
    return read_rotation_from_mp4(video_path)


def _apply_manual_rotation(frame, angle):
    # ВАЖЛИВО: кут з метаданих контейнера (MP4/QuickTime rotate matrix)
    # означає "поверни кадр на цей кут ЗА годинниковою стрілкою", щоб
    # отримати правильну орієнтацію (це стандартна конвенція matrix-полів
    # tkhd, якою користуються камери на кшталт Samsung S24). Напрямки
    # для 90/270 були переплутані місцями - через це відео з камери
    # (де кут реально 90 або 270, а не 0, як у перекодованих Telegram-
    # відео) вертілось у зворотний бік, що на виході давало помітну
    # різницю в 180° від правильної орієнтації.
    if angle == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


def format_time(seconds):
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def resize_frame(frame, max_width=1024):
    height, width = frame.shape[:2]
    if width <= max_width:
        return frame
    scale = max_width / width
    new_size = (max_width, int(height * scale))
    return cv2.resize(frame, new_size)


def _resize_then_rotate(frame, angle, max_width):
    """
    Той самий результат, що й _apply_manual_rotation(frame, angle) з
    подальшим resize_frame(..., max_width), але в дешевшому порядку:
    спершу зменшуємо СИРИЙ (ще не повернутий) кадр, і лише потім
    повертаємо вже маленьку картинку - cv2.rotate на кадрі в кілька
    разів меншому за оригінал коштує помітно дешевше, ніж на
    повнорозмірному. Актуально насамперед для вертикальних відео з
    телефону (сирий кадр із декодера - ландшафтний, поворот на
    90/270° робить його портретним).

    Використовувати можна ЛИШЕ якщо повнорозмірний повернутий кадр
    більше нікому не потрібен (немає full_res_dir для кешу) - інакше
    треба звичайний порядок: _apply_manual_rotation(), а тоді
    resize_frame() від уже повернутого кадру, як і раніше.

    ВАЖЛИВО про max_width: він стосується ШИРИНИ ПІСЛЯ повороту. Для
    кута 90/270 (де ширина й висота міняються місцями після повороту)
    це означає, що обмежувати треба ВИСОТУ сирого кадру, а не його
    ширину - інакше на виході вийшов би кадр іншого розміру, ніж дав
    би старий порядок (rotate, потім resize по ширині).
    """
    if not angle:
        return resize_frame(frame, max_width=max_width)

    height, width = frame.shape[:2]
    limit_dim = height if angle in (90, 270) else width
    if limit_dim > max_width:
        scale = max_width / limit_dim
        frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
    return _apply_manual_rotation(frame, angle)


# Роздільна здатність, у якій кадри їдуть у Gemini. Роздільна здатність
# вихідного відео при цьому ніде не втрачається - grab_screenshot()
# завжди бере кадр заново з відеофайлу в оригінальній якості, для
# показу/зуму в інтерфейсі.
#
# ПЕРЕВІРЕНО (документація Gemini 3, не здогад): на відміну від
# "звичайних" мультимодальних моделей, де більша картинка = більше
# токенів (ріжеться на патчі/тайли), у Gemini 3 вартість зображення в
# токенах - це ФІКСОВАНИЙ бюджет, який визначає лише
# generationConfig.mediaResolution (MEDIA_RESOLUTION нижче): 1120
# токенів за unspecified/HIGH, 560 за MEDIUM, 280 за LOW - незалежно
# від реального розміру картинки, ПОКИ вона не перевищує поріг, після
# якого Gemini починає ділити зображення на кілька "тайлів" (за
# незалежними джерелами - це десь у районі 1500+ пікселів по довшій
# стороні, суттєво вище значення нижче). Тобто 640 -> 1024 не коштує
# ЖОДНОГО додаткового токена - чистий приріст деталізації для дрібних
# об'єктів (яхта в морі, номер машини тощо) за ту саму ціну.
GEMINI_FRAME_WIDTH = 1024

# Якість JPEG САМЕ для кадрів, що йдуть у Gemini (не для повнорозмірного
# кешу/скріншотів - там лишається дефолтна якість cv2.imwrite, це кадри
# для перегляду людиною). Дефолт cv2.imencode - 95, це помітно важчий
# base64-payload у HTTP-запиті, а модель бачить зображення в межах
# фіксованого token-бюджету (див. коментар над GEMINI_FRAME_WIDTH) -
# тобто "зайва" якість тут не додає моделі жодної деталі, а лише
# видовжує запит. 85 - типовий баланс "на око не відрізнити" для фото.
_GEMINI_JPEG_QUALITY = 85
_GEMINI_JPEG_ENCODE_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, _GEMINI_JPEG_QUALITY]

# На скільки відсотків пікселів має змінитись яскравість між кадрами,
# щоб вважати це "рухом", а не шумом матриці камери/вітром у листі.
# Використовується лише у старому бінарному режимі (_has_motion) - коли
# тривалість відео невідома і segment_sec порахувати нема з чого (див.
# коментар над MOTION_BASELINE_WINDOW нижче для основного режиму).
MOTION_THRESHOLD_PERCENT = 2.0

# ---------------------------------------------------------------------------
# Скоринг кандидатів відносно "фонового" рівня руху (хвилі, тремтіння
# картинки, дощ у кадрі тощо)
# ---------------------------------------------------------------------------
#
# ПРОБЛЕМА: бінарний поріг (_has_motion) порівнює кандидата з ОСТАННІМ
# ВІДІБРАНИМ кадром - а той може лежати на цілий відрізок (segment_sec)
# у минулому. На видовищі з постійним фоновим рухом (море, дерева на
# вітрі) фаза цього руху за такий час встигає повністю змінитись, тож
# _has_motion() спрацьовує майже на КОЖНОМУ кандидаті - і "переможцем"
# відрізка стає перший-ліпший кандидат, а не той, де реально видно
# об'єкт, що рухається (яхту).
#
# РІШЕННЯ: порівнюємо кожного кандидата з ПОПЕРЕДНІМ КАНДИДАТОМ (а не
# з останнім відібраним кадром) - це і є "миттєвий" рівень руху між
# сусідніми кадрами. Хвилі дають стабільно ВИСОКИЙ, але приблизно
# ОДНАКОВИЙ такий рівень на кожному кроці - зберігаємо останні
# MOTION_BASELINE_WINDOW значень і беремо їхню МЕДІАНУ як поточний
# "фон". Скор кандидата = його рівень руху МІНУС цей фон: хвилі самі по
# собі дають скор близько нуля, а реальний рухомий об'єкт - помітний
# додатний викид понад фон. Усередині відрізка перемагає кандидат з
# найвищим скором, обраний вже ПІСЛЯ того, як відрізок повністю
# пройдено, а не перший, хто перевищив фіксований поріг.
#
# Медіана (а не середнє) навмисно - середнє саме "поплило б" разом із
# самим фоном, якщо в межах вікна трапиться один сильний викид (та сама
# яхта) і зменшило б контраст для наступних кандидатів у тому ж вікні.
MOTION_BASELINE_WINDOW = 20

# У скільки разів частіше, ніж підсумковий ліміт max_frames, ми
# перевіряємо кандидатів на кадр. Детектору руху потрібно з чого
# вибирати - якщо семплювати так само рідко, як раніше (без детекції),
# вибирати буде нема з чого і вся економія токенів пропаде намарно.
CANDIDATE_OVERSAMPLE = 3


# ---------------------------------------------------------------------------
# "Важкі" відео (HEVC + високий fps) - чесний компроміс без нативного
# декодера
# ---------------------------------------------------------------------------
#
# Нативно (через ffmpeg/MediaCodec напряму) можна було б декодувати лише
# опорні кадри (keyframes) і зовсім пропускати решту - але це кілька
# годин роботи з інтеграцією під Android. Тут же чесно визнаємо: раз
# OpenCV все одно змушений ПОСЛІДОВНО прогнати кожен кадр через
# grab() (для HEVC це завжди повне декодування, бо кадри всередині GOP
# залежать один від одного - на відміну від H.264, де така сама
# послідовна прогонка теж декодує кожен кадр, але сам кодек "легший"
# і швидший), - зменшити можна лише те, що йде ПІСЛЯ grab(): скільки
# разів ми викликаємо retrieve() + resize() + cvtColor() + absdiff()
# для кандидатів. Для "важкого" відео просто перевіряємо кандидатів
# рідше - весь декодуючий прохід все одно залишається, зате менше
# зайвої роботи по дорозі.

_HEVC_FOURCC_CODES = {"hvc1", "hev1", "HEVC", "H265", "x265"}

# Вище цього fps + HEVC-кодек вважаємо відео "важким". 30 - типовий
# fps звичайного телефонного відео, тому поріг ставимо саме тут:
# 60 fps HEVC (часте для "уповільненої зйомки" чи сучасних камер)
# майже вдвічі більше кадрів на ту саму тривалість - і всі вони йдуть
# через дорогий декодер.
HEAVY_CODEC_FPS_THRESHOLD = 30

# У скільки разів рідше перевіряємо кандидатів на кадр для "важкого"
# відео. Підібрано емпірично: помітно менше retrieve()/resize()/
# детекцій руху за прохід, а FORCE_KEEP_EVERY і так підстраховує від
# пропуску статичних об'єктів (кут не змінюється, лиш густина перевірок).
HEAVY_CODEC_INTERVAL_MULTIPLIER = 2.5


def _read_fourcc(video):
    """
    Читає FourCC-код кодека з відкритого VideoCapture і повертає його
    звичайним рядком (напр. "hvc1", "avc1"), у верхньому регістрі не
    приводимо навмисно - FourCC-коди регістрозалежні за специфікацією.

    CAP_PROP_FOURCC повертає ціле число, "запаковане" як 4 ASCII-байти -
    розпаковуємо назад у рядок через struct.
    """
    fourcc_int = int(video.get(cv2.CAP_PROP_FOURCC))
    if fourcc_int <= 0:
        return ""
    try:
        return struct.pack("<I", fourcc_int).decode("ascii", errors="ignore").strip("\x00")
    except (struct.error, UnicodeDecodeError):
        return ""


def _is_heavy_video(video, fps):
    """
    "Важке" відео = HEVC (H.265/x265) + fps вище HEAVY_CODEC_FPS_THRESHOLD.

    Якщо FourCC не вдалось прочитати (fourcc_int == 0, буває на деяких
    Android-збірках OpenCV для окремих контейнерів) - чесно вважаємо
    відео НЕ важким і залишаємо звичайну, більш густу перевірку
    кандидатів. Краще зайва перевірка, ніж помилково пропущений рух.
    """
    fourcc = _read_fourcc(video)
    return fourcc in _HEVC_FOURCC_CODES and fps > HEAVY_CODEC_FPS_THRESHOLD


def _motion_percent(prev_gray, current_gray):
    """
    Порівнює два кадри (у відтінках сірого) і повертає, який ВІДСОТОК
    пікселів кадру помітно змінив яскравість (0..100) - сира метрика,
    без застосування порогу.

    cv2.absdiff рахує різницю яскравості піксель до пікселя. Далі
    бінаризуємо різницю (поріг 25 - це "помітна" зміна, а не шум
    матриці) і рахуємо частку пікселів, що реально змінились.
    """
    diff = cv2.absdiff(prev_gray, current_gray)
    _, diff_mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

    changed_pixels = cv2.countNonZero(diff_mask)
    total_pixels = diff_mask.shape[0] * diff_mask.shape[1]
    return (changed_pixels / total_pixels) * 100


def _has_motion(prev_gray, current_gray, threshold_percent=MOTION_THRESHOLD_PERCENT):
    """
    Бінарна версія поверх _motion_percent() - лишена для режиму без
    segment_sec (тривалість відео невідома, ділити на відрізки нема
    з чого), де скоринг відносно фону застосувати неможливо.
    """
    return _motion_percent(prev_gray, current_gray) >= threshold_percent


# ---------------------------------------------------------------------------
# Емпірична перевірка: чи справді "сикання" дешеве для ЦЬОГО файлу
# ---------------------------------------------------------------------------
#
# ПРОБЛЕМА: use_seek вмикається просто якщо є total_frame_count і не
# Android - і мовчки припускає, що video.set(CAP_PROP_POS_FRAMES, ...)
# дешевий (переходить одразу до потрібного кадру, майже нічого не
# декодуючи по дорозі). Це правда лише якщо в файлі досить часті
# keyframe. Якщо keyframe рідкісні, кожне сикання змушене декодувати
# ВЕСЬ проміжок від попереднього keyframe до цільового кадру - тобто
# по факту робить не менше роботи, ніж послідовний прохід, але ще й
# з накладними витратами на повторну ініціалізацію позиції щоразу.
#
# fourcc тут не допомагає визначити це наперед: на Mac (backend
# AVFoundation) CAP_PROP_FOURCC завжди повертає піксельний формат
# кадру на виході (напр. "BGR3"), а не реальний кодек джерела - тож
# детектор "важкого" відео (_is_heavy_video, за HEVC fourcc) на Mac
# фактично ніколи не спрацьовує, незалежно від того, наскільки рідкі
# keyframe у файлі насправді.
#
# РІШЕННЯ: не гадати, а зміряти. Перед нарізкою робимо кілька пробних
# сикань у довільні точки відео і дивимось на реальний час. Якщо він
# явно більший за розумний поріг - сикання тут не економить, і
# переходимо на послідовний прохід (той самий, що вже є для Android).

# Скільки пробних сикань робимо, щоб оцінити середню вартість.
_SEEK_PROBE_SAMPLE_COUNT = 3

# Поріг у секундах: скільки максимум має коштувати ОДНЕ сикання, щоб
# вважати його "дешевим". На нормальному файлі з частими keyframe
# сикання - це переважно операція за десятки мілісекунд; якщо середнє
# вимірювання перевищує цей поріг, це ознака рідких keyframe (кожне
# сикання декодує суттєвий шматок відео замість миттєвого переходу).
_SEEK_SLOW_THRESHOLD_SEC = 0.15


def _probe_seek_cost(video, total_frame_count, sample_count=_SEEK_PROBE_SAMPLE_COUNT):
    """
    Робить кілька пробних сикань у різні точки відео (не на самому
    початку і не в самому кінці - там частіше стоять keyframe і
    результат був би оманливо оптимістичним) та повертає СЕРЕДНІЙ
    реальний час одного seek+read у секундах.

    Повертає None, якщо жодне пробне сикання не вдалось (тоді
    викликаючий код лишає use_seek як є - принаймні спроба нічого не
    зламала).

    ВАЖЛИВО: після проб повертає позицію відео на кадр 0 - пробні
    сикання не повинні впливати на те, звідки почнеться сама нарізка.
    """
    if total_frame_count <= 0:
        return None

    fractions = (0.15, 0.45, 0.75)[:sample_count]
    positions = sorted({
        min(total_frame_count - 1, max(0, int(total_frame_count * frac)))
        for frac in fractions
    })

    total_time = 0.0
    successful = 0
    for position in positions:
        started_at = time.time()
        video.set(cv2.CAP_PROP_POS_FRAMES, position)
        success, _ = video.read()
        elapsed = time.time() - started_at
        total_time += elapsed
        if success:
            successful += 1
        if elapsed > _SEEK_SLOW_THRESHOLD_SEC * 2:
            # Уже перша проба явно повільна - решта проб лише даремно
            # з'їдала б час (на коротких відео вони коштували більше,
            # ніж уся економія від правильного вибору режиму).
            break

    video.set(cv2.CAP_PROP_POS_FRAMES, 0)

    if successful == 0:
        return None
    return total_time / successful


def _measure_sequential_fps(video, sample_frames=40):
    """Швидкий замір швидкості послідовного grab() (кадрів/с) з початку
    відео; після заміру повертає позицію на кадр 0. None - не вдалось."""
    try:
        video.set(cv2.CAP_PROP_POS_FRAMES, 0)
        if not video.grab():  # перший кадр - розігрів декодера
            return None
        started = time.time()
        count = 0
        while count < sample_frames and video.grab():
            count += 1
        elapsed = time.time() - started
        video.set(cv2.CAP_PROP_POS_FRAMES, 0)
        if count < 10 or elapsed <= 0:
            return None
        return count / elapsed
    except Exception:  # noqa: BLE001
        return None


def _iter_candidate_frames(video, interval_frames, total_frame_count, use_seek, state=None):
    """
    Генератор (frame_index, frame) для КОЖНОГО кандидата (кожен
    interval_frames-й кадр) - не вирішує, залишити його чи ні, це
    робить extract_frames() далі. Два способи дістати кандидата:

    - use_seek=True (десктоп, total_frame_count відомий): напряму
      сикаємо на потрібний індекс кадру (CAP_PROP_POS_FRAMES) і
      читаємо лише його - кадри МІЖ кандидатами взагалі не
      декодуються. Для години відео з кандидатом раз на кілька секунд
      це в рази швидше за послідовний прохід через ВСІ кадри.

    - use_seek=False (Android, або коли тривалість/кількість кадрів
      невідомі) - послідовний прохід: grab() для кожного кадру,
      retrieve() лише для кандидатів. Повільніше, зате безпечно -
      сикання на деяких Android-збірках OpenCV ненадійне (див.
      коментар біля _IS_ANDROID вище).
    """
    if use_seek:
        frame_index = 0
        while frame_index < total_frame_count:
            video.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            success, frame = video.read()
            if not success:
                logger.debug(
                    f"[diag] сикання зупинилось на кадрі {frame_index} "
                    f"з заявлених total_frame_count={total_frame_count} "
                    f"(video.read() повернув False після set()) - "
                    f"це {frame_index / total_frame_count * 100:.1f}% від заявленої довжини"
                )
                break
            yield frame_index, frame
            frame_index += interval_frames
    else:
        frame_index = 0
        while True:
            success = video.grab()
            if not success:
                percent = (frame_index / total_frame_count * 100) if total_frame_count else 0
                logger.debug(
                    f"[diag] послідовний прохід зупинився на кадрі {frame_index} "
                    f"з заявлених total_frame_count={total_frame_count} "
                    f"(video.grab() повернув False) - "
                    f"це {percent:.1f}% від заявленої довжини"
                )
                break
            if frame_index % interval_frames == 0:
                success, frame = video.retrieve()
                if success:
                    yield frame_index, frame
            frame_index += 1
            if state is not None:
                state["decoded"] = frame_index


def _extract_slice_sequential(
    video_path, fps, start_frame, end_frame, interval_frames, segment_sec, max_frames,
    seg_start, seg_end, manual_rotation, cancel_event, progress_out, slice_id, full_dir=None,
    frame_count_out=None, frame_queue=None, use_seek=False,
):
    """
    ОДИН слайс паралельної нарізки - виконується в ОКРЕМОМУ ПРОЦЕСІ
    (див. велику примітку над _extract_frames_parallel, чому саме
    процеси, а не потоки). Свій VideoCapture, свій відрізок відео
    [start_frame, end_frame) і свої сегменти [seg_start, seg_end).
    Логіка відбору кадрів та сама, що в основному циклі extract_frames()
    (кандидат раз на interval_frames, детекція руху, рівно один кадр на
    сегмент, запасний кандидат сегмента) - лише в межах слайсу.

    Через межу процесу можна передати лише picklable об'єкти - звідси
    відмінності від "однопроцесного" вигляду цієї ж логіки:
      - cancel_event: multiprocessing.Manager().Event() замість
        довільної функції is_cancelled() (та могла б замикати на
        собі, наприклад, стан Tkinter-віджета - а такий стан не можна
        ані передати в інший процес, ані безпечно смикати з нього).
        Викликаючий процес сам віддзеркалює is_cancelled() у цю подію.
      - progress_out / frame_count_out: multiprocessing.Manager().list()
        замість звичайного списку - той самий інтерфейс індексного
        запису (progress_out[slice_id] = ...), просто кожен запис іде
        через IPC до процесу-менеджера.
      - frame_queue: multiprocessing.Manager().Queue() замість прямого
        виклику on_frame_ready() - колбек користувача (main.py) не
        можна викликати з чужого процесу, тож щойно готовий кадр
        просто кладеться в чергу, а сам виклик колбека робить
        викликаючий процес (_extract_frames_parallel), вичитуючи цю
        чергу у своєму циклі очікування.
      - full_frame_pool (асинхронний запис full-res кадрів) прибрано:
        ThreadPoolExecutor не можна передати в інший процес. Запис
        тут завжди синхронний (cv2.imwrite) - і це не втрата: слайси
        і так уже виконуються в окремих процесах паралельно одне
        одному, тож запис на диск у одному слайсі не блокує декодування
        в інших.

    use_seek - яким способом слайс дістає кандидатів (те саме рішення,
    що extract_frames() уже прийняв для ВСЬОГО відео - див. коментар
    там же, чому "сикати чи ні" не можна вирішувати наперед за fourcc):
      - False: один повільний seek на старті слайсу, далі послідовний
        video.grab() по кожному кадру відрізка, retrieve() лише на
        кандидатах. Правильний вибір, коли keyframe рідкісні.
      - True: слайс НАПРЯМУ сикає (CAP_PROP_POS_FRAMES) на кожного
        кандидата окремо - кадри МІЖ кандидатами взагалі не декодуються.
    """
    # prefer_ffmpeg=True - обов'язково: цей виклик завжди йде з-під
    # _single_threaded_ffmpeg_decode() (див. виклик у extract_frames()),
    # а та керує ЛИШЕ FFmpeg-бекендом. Без прямої вказівки backend'у
    # cv2.VideoCapture(video_path) на Mac відкрив би AVFoundation.
    video = _open_capture(video_path, prefer_ffmpeg=True)
    if not video.isOpened():
        return []

    frames = []
    quota = max(0, seg_end - seg_start)
    current_segment_index = -1
    segment_best = None    # {"score", "timestamp_sec", "frame", "full", "gray"} - найкращий кандидат поточного відрізка (СИРИЙ кадр, ще не закодований)
    prev_candidate_gray = None   # попередній КАНДИДАТ (не обов'язково відібраний) - для локального рівня руху
    motion_baseline = deque(maxlen=MOTION_BASELINE_WINDOW)

    def flush_segment_best():
        nonlocal segment_best
        if segment_best is not None and len(frames) < quota:
            ok, buffer = cv2.imencode(".jpg", segment_best["frame"], _GEMINI_JPEG_ENCODE_PARAMS)
            if ok:
                frames.append({
                    "timestamp_sec": segment_best["timestamp_sec"],
                    "jpg_bytes": buffer.tobytes(),
                    "full_path": _save_full_frame(full_dir, segment_best.get("full")),
                })
                # frame_count_out - лише для UI-хінта "скільки вже нарізано"
                # (див. коментар над _extract_frames_parallel) - на саму
                # логіку нарізки жодного впливу немає, тож пишемо без
                # додаткових перевірок.
                if frame_count_out is not None:
                    frame_count_out[slice_id] = len(frames)
                # Кладемо готовий кадр у чергу - сам виклик on_frame_ready()
                # робить викликаючий процес (див. докстрінг вище й цикл
                # очікування в _extract_frames_parallel). Виняток запису в
                # чергу (напр. черга вже закрита під час завершення) не
                # повинен валити нарізку - краще втратити стрімінг одного
                # кадру, ніж усю решту слайсу.
                if frame_queue is not None:
                    try:
                        frame_queue.put(frames[-1])
                    except Exception as error:  # noqa: BLE001
                        logger.warning(f"[diag] не вдалось покласти кадр у чергу стрімінгу: {error}")
        segment_best = None

    try:
        span = max(1, end_frame - start_frame)

        if use_seek:
            # Перший кандидат слайсу - НЕ обов'язково start_frame:
            # вирівнюємо до глобальної сітки interval_frames (та сама
            # сітка, що й у решти слайсів і в однопотоковому шляху),
            # інакше межі "хто якого кандидата рахує" попливуть і
            # сегменти різних слайсів почнуть перекриватись/дірявитись.
            remainder = start_frame % interval_frames
            frame_index = start_frame if remainder == 0 else start_frame + (interval_frames - remainder)
        else:
            if start_frame > 0:
                video.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            frame_index = start_frame - 1

        while True:
            if use_seek:
                if frame_index >= end_frame:
                    break
                video.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                success, frame = video.read()
                if not success:
                    break
            else:
                frame_index += 1
                if frame_index >= end_frame:
                    break
                if not video.grab():
                    break
                if frame_index % interval_frames != 0:
                    continue
                success, frame = video.retrieve()
                if not success:
                    continue

            if cancel_event is not None and cancel_event.is_set():
                break

            progress_out[slice_id] = min(1.0, (frame_index - start_frame) / span)
            timestamp_sec = frame_index / fps
            segment_index = min(int(timestamp_sec // segment_sec), max_frames - 1)
            if segment_index < seg_start:
                if use_seek:
                    frame_index += interval_frames
                continue
            if segment_index >= seg_end:
                break

            if full_dir:
                # Повнорозмірний повернутий кадр знадобиться нижче для
                # кешу (segment_best["full"]) - доводиться повертати
                # його цілком, як і раніше.
                if manual_rotation:
                    frame = _apply_manual_rotation(frame, manual_rotation)
                gemini_frame = resize_frame(frame, max_width=GEMINI_FRAME_WIDTH)
            else:
                # Кеш не потрібен - "frame" нижче ніде більше не
                # використовується, тож можна зменшити СИРИЙ кадр і
                # повернути вже маленьку картинку (див. _resize_then_rotate).
                gemini_frame = _resize_then_rotate(frame, manual_rotation, GEMINI_FRAME_WIDTH)
            gray = cv2.cvtColor(gemini_frame, cv2.COLOR_BGR2GRAY)

            if segment_index != current_segment_index:
                flush_segment_best()
                if len(frames) >= quota:
                    break
                current_segment_index = segment_index

            # Рівень руху ЩОДО ПОПЕРЕДНЬОГО КАНДИДАТА (див. коментар над
            # MOTION_BASELINE_WINDOW) - не щодо останнього відібраного
            # кадру, який може лежати на цілий відрізок у минулому.
            motion_percent = 0.0 if prev_candidate_gray is None else _motion_percent(prev_candidate_gray, gray)
            prev_candidate_gray = gray

            baseline = statistics.median(motion_baseline) if motion_baseline else 0.0
            score = motion_percent - baseline
            motion_baseline.append(motion_percent)

            if segment_best is None or score > segment_best["score"]:
                # .copy() обов'язковий: retrieve()/read() перевикористовує буфер.
                segment_best = {
                    "score": score,
                    "timestamp_sec": timestamp_sec,
                    "frame": gemini_frame.copy(),
                    "full": frame.copy() if full_dir else None,
                    "gray": gray,
                }

            if use_seek:
                frame_index += interval_frames

        flush_segment_best()
    finally:
        progress_out[slice_id] = 1.0
        video.release()
    return frames


def _extract_frames_parallel(
    video_path, fps, total_frame_count, interval_frames, segment_sec, max_frames,
    manual_rotation, workers, progress_callback, is_cancelled, full_dir=None,
    frame_count_callback=None, on_frame_ready=None, use_seek=False,
):
    """
    Паралельна нарізка для файлів, де декодування (з сиканням чи без)
    займає більшість часу extract_frames(). Відео ділиться на `workers`
    суцільних слайсів по межах сегментів (сегмент = один кадр у
    підсумку, тож слайси не залежать один від одного); кожен слайс
    декодує ОКРЕМИЙ ПРОЦЕС зі своїм VideoCapture. Колбеки прогресу
    (progress_callback/frame_count_callback/on_frame_ready) як і раніше
    викликаються лише з ЦЬОГО (викликаючого) процесу.

    ---------------------------------------------------------------
    ЧОМУ ПРОЦЕСИ, А НЕ ПОТОКИ (ThreadPoolExecutor - БУЛО, і це не
    працювало)
    ---------------------------------------------------------------
    Перша спроба паралелізму (потоки + явний cv2.CAP_FFMPEG-бекенд +
    OPENCV_FFMPEG_CAPTURE_OPTIONS=threads;1, щоб прибрати
    oversubscription внутрішніх потоків ffmpeg) на практиці НЕ дала
    реального прискорення: 4 потоки декодували з тією самою сумарною
    швидкістю, що й один (виміряно - ~1.0-1.1x замість очікуваних
    ~3.5-4x). Причина глибша за бекенд відеофайлу: у CPython усі
    потоки одного процесу ділять один GIL (Global Interpreter Lock).
    Для мережевого/файлового I/O більшість C-розширень звільняють GIL
    на час очікування - тому паралельні запити до Gemini (нижче,
    MAX_PARALLEL_BATCHES) справді паралеляться. Але декодування відео -
    це CPU-навантаження (мільйони операцій над пікселями всередині
    grab()/retrieve()), і біндинги OpenCV для Python не гарантовано
    звільняють GIL на час такого виклику - тобто "паралельні" потоки
    декодування по факту виконувались по черзі на одному ядрі.

    Єдиний спосіб чесно розкидати CPU-навантаження по ядрах у Python -
    окремі ПРОЦЕСИ: кожен має свій інтерпретатор і свій GIL.
    ProcessPoolExecutor вирішує це, але через межу процесу не можна
    передати довільні python-об'єкти напряму (закриті колбеки, спільні
    списки, ThreadPoolExecutor) - звідси відмінності нижче.

    is_cancelled (довільна функція) -> cancel_event
    (multiprocessing.Manager().Event()): функцію не можна передати в
    інший процес (могла б замикати на собі стан Tkinter-віджета, який
    у принципі не існує поза головним процесом), тож цей процес сам
    опитує is_cancelled() і віддзеркалює результат у спільну подію,
    яку вже читають слайси.

    progress/frame_counts (звичайні списки) -> Manager().list(): той
    самий інтерфейс індексного запису, лише через IPC.

    on_frame_ready (колбек) -> frame_queue (Manager().Queue()): колбек
    так само не можна викликати з чужого процесу - слайс кладе готовий
    кадр у чергу, а сам виклик on_frame_ready() робить цей (викликаючий)
    процес у циклі очікування нижче, вичитуючи чергу кожні 0.25с
    (так само часто, як і раніше опитувався прогрес).

    full_frame_pool (ThreadPoolExecutor для асинхронного imwrite) -
    більше НЕ прокидається в слайси (не можна передати в інший процес) -
    запис full-res кадру там завжди синхронний. Це не втрата: слайси й
    так уже виконуються паралельно один одному в окремих процесах.

    use_seek - прокидається напряму в кожен _extract_slice_sequential
    (див. докстрінг там). Кожен кандидат (і сикаючий, і послідовний)
    незалежний від сусідніх слайсів - тож ділиться на слайси однаково
    тривіально в обох режимах.

    frame_count_callback(count: int) - ОКРЕМИЙ від progress_callback
    колбек, що звітує СУМАРНУ кількість уже нарізаних (не лише
    "пройдених") кадрів по всіх слайсах разом - інакше лічильник
    "нарізано X" у status-рядку простоював би на 0, поки не
    завершаться ВСІ слайси одразу, хоча прогрес-бар (за відсотком)
    реально рухається.

    on_frame_ready(frame_dict, index) - той самий колбек, що й у
    extract_frames() (див. докстрінг там), викликається ЦИМ процесом
    одразу по готовності кожного кадру (не чекаючи завершення всіх
    `workers` слайсів) - "нарізка й аналіз паралельно" (Gemini бачить
    кадри вже під час нарізки). index - позиція кадру В МЕЖАХ СВОГО
    слайсу, НЕ глобальна: кадри з різних слайсів ідуть у чергу
    вперемішку й не в порядку timestamp_sec (слайс з пізнішими
    сегментами може віддати свій перший кадр раніше за слайс із
    ранішими, якщо той повільніший). Це навмисно НЕ проблема -
    викликач (main.py) index ігнорує, а порядок кадрів у черзі для
    коректності аналізу не важливий (Gemini нумерує кадри локально в
    межах батчу, підсумковий звіт сортується за timestamp_sec, див.
    find_object_in_frames.__doc__).
    """
    bounds = [round(i * max_frames / workers) for i in range(workers + 1)]
    jobs = []
    for i in range(workers):
        seg_start, seg_end = bounds[i], bounds[i + 1]
        start_frame = 0 if i == 0 else int(math.ceil(seg_start * segment_sec * fps))
        end_frame = total_frame_count if i == workers - 1 else int(math.ceil(seg_end * segment_sec * fps))
        jobs.append((start_frame, end_frame, seg_start, seg_end))

    logger.debug(f"[diag] паралельна нарізка: {workers} процеси (use_seek={use_seek}), слайси (кадри) = "
                f"{[(j[0], j[1]) for j in jobs]}")

    parallel_start_ts = time.time()
    # Manager() піднімає окремий службовий процес і роздає проксі-об'єкти,
    # які МОЖНА передати робочим процесам (на відміну від звичайних
    # списків/подій/черг, які прив'язані до пам'яті одного процесу).
    with multiprocessing.Manager() as manager:
        progress = manager.list([0.0] * workers)
        frame_counts = manager.list([0] * workers)
        cancel_event = manager.Event()
        frame_queue = manager.Queue() if on_frame_ready is not None else None

        def _drain_frame_queue():
            """Вичитує всі кадри, що встигли з'явитись у черзі, і викликає
            on_frame_ready() для кожного - у ЦЬОМУ процесі, як і
            задокументовано вище."""
            if frame_queue is None:
                return
            while True:
                try:
                    frame_dict = frame_queue.get_nowait()
                except Exception:  # noqa: BLE001 - Empty з queue.Empty і будь-які збої проксі
                    break
                _notify_frame_ready(on_frame_ready, frame_dict, 0)

        # Див. велику примітку в докстрінгу вище й біля
        # _single_threaded_ffmpeg_decode(): без цього кожен з `workers`
        # VideoCapture намагається сам зайняти всі ядра CPU (ffmpeg за
        # замовчуванням багатопотоковий), і паралельні слайси лише
        # заважають один одному замість реального пришвидшення.
        with _single_threaded_ffmpeg_decode(), ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _extract_slice_sequential, video_path, fps, start_frame, end_frame, interval_frames,
                    segment_sec, max_frames, seg_start, seg_end, manual_rotation, cancel_event, progress, slice_id,
                    full_dir, frame_counts, frame_queue, use_seek,
                )
                for slice_id, (start_frame, end_frame, seg_start, seg_end) in enumerate(jobs)
            ]
            pending = set(futures)
            last_reported_percent = -1
            last_reported_count = -1
            while pending:
                if is_cancelled is not None and is_cancelled():
                    cancel_event.set()
                _done, pending = wait(pending, timeout=0.25)
                _drain_frame_queue()
                if progress_callback:
                    percent = int(sum(progress) / workers * 100)
                    if percent != last_reported_percent:
                        progress_callback(min(percent, 100) / 100)
                        last_reported_percent = percent
                if frame_count_callback:
                    total_count = sum(frame_counts)
                    if total_count != last_reported_count:
                        frame_count_callback(total_count)
                        last_reported_count = total_count
            # Останній видих: якщо якісь кадри потрапили в чергу МІЖ
            # останньою перевіркою pending і фактичним завершенням futures
            # (dict.result() нижче їх уже не поверне вдруге - чергу й
            # результат futures заповнює той самий flush_segment_best()),
            # цей виклик підбирає їх до того, як черга (разом з Manager)
            # піде на злам.
            _drain_frame_queue()
            parts = [future.result() for future in futures]

    frames = [frame for part in parts for frame in part]
    frames.sort(key=lambda item: item["timestamp_sec"])
    if len(frames) > max_frames:
        frames = frames[:max_frames]
    logger.info(f"[diag] паралельна нарізка: {time.time() - parallel_start_ts:.1f}с, кадрів: {len(frames)}")
    if progress_callback:
        progress_callback(1.0)
    if frame_count_callback:
        frame_count_callback(len(frames))
    return frames



def extract_frames(video_path, max_frames=300, min_interval_sec=0.5, progress_callback=None, is_cancelled=None,
                   full_res_dir=None, allow_hw=True, on_frame_ready=None, frame_count_callback=None,
                   allow_fast=True):
    """
    Швидка й "розумна" нарізка відео на кадри для відправки в Gemini.

    Тут П'ЯТЬ сходинок економії й точності:

    1) Швидке дістання кандидатів - на десктопі (Mac/Windows) сикаємо
       напряму на потрібний індекс кадру (CAP_PROP_POS_FRAMES): кадри
       МІЖ кандидатами взагалі не декодуються. Це в рази швидше за
       послідовний прохід через ВСІ кадри для довгого відео. На
       Android сикання ненадійне (див. _IS_ANDROID) - там лишається
       послідовний прохід: video.grab() дешево прогортає кадр без
       декодування, video.retrieve() декодує лише кандидатів. Обидва
       способи дає _iter_candidate_frames() - решта коду однакова.

    2) Точна тривалість (read_duration_from_mp4) - читається напряму
       з mp4-контейнера (бокс mvhd), а не рахується як
       total_frame_count/fps. Останнє для деяких файлів (перекодовані,
       скачані - типовий "videoplayback.mp4") дає ЗАНИЖЕНУ тривалість,
       через що вся нарізка стискалась у перші секунди відео замість
       того, щоб охопити його цілком. Якщо mvhd прочитати не вдалось
       (не mp4, битий файл) - чесний відкат на старий спосіб.

       РАЗОМ З ЦИМ - надійний fps (_resolve_fps): на деяких файлах
       video.get(cv2.CAP_PROP_FPS) повертає fps, занижений рівно в
       ціле число разів (напр. 29.97 замість реальних 59.94). Через
       це timestamp_sec = frame_index / fps виходив удвічі більшим за
       реальний, а гарантія покриття (крок 4 нижче) рахувала відрізки
       від роздутих міток і фактично встигала пройти лише половину
       кадрів відео. _resolve_fps() звіряє сирий fps з розрахунковим
       (total_frame_count / duration_sec) і бере розрахунковий при
       підозрілій кратності.

    3) Детекція руху (cv2.absdiff) - з кандидатів у підсумковий набір
       потрапляють лише ті, де картинка помітно змінилась порівняно
       з останнім ВЗЯТИМ кадром. Статичні шматки відео (де нічого не
       відбувається) майже не витрачають токени Gemini. Кандидатів при
       цьому перевіряємо густіше (CANDIDATE_OVERSAMPLE), щоб детектору
       було з чого обирати.

    4) Гарантія покриття всієї довжини (per-segment guarantee) - відео
       ділиться на max_frames рівних відрізків часу, і з КОЖНОГО
       відрізка береться рівно один кадр: перший "рухомий" кандидат,
       а якщо руху в відрізку так і не було - останній переглянутий
       кандидат (щоб відрізок все одно не лишився зовсім без кадру).
       Без цього безперервний рух (море, активна година запису з
       камери) вичерпав би ліміт кадрів у першій третині відео, а
       решта лишилась би без жодної уваги. Спрацьовує тільки якщо
       тривалість відома (крок 2) - інакше нарізка йде "по-старому",
       без сегментів.

    5) Для "важких" відео (HEVC + fps > HEAVY_CODEC_FPS_THRESHOLD), де
       навіть grab() змушений повністю декодувати кожен кадр - кандидатів
       перевіряємо ще рідше (HEAVY_CODEC_INTERVAL_MULTIPLIER). Сам
       декодуючий прохід від цього не коротшає, зате менше зайвих
       retrieve()/resize()/детекцій руху по дорозі. Див. коментар біля
       _is_heavy_video().

    Підсумкових кадрів завжди буде НЕ БІЛЬШЕ max_frames.

    progress_callback(fraction: float) - якщо переданий, викликається
    по ходу нарізки з часткою вже пройденого відео (0..1), щоб
    інтерфейс міг показати живий прогрес-бар замість "завислого" на
    одному значенні на весь час нарізки (яка для годинного відео може
    йти кілька хвилин).

    full_res_dir - необов'язкова тека: якщо задана, кожен кадр, що
    потрапив у підсумок, додатково зберігається туди в повній
    роздільній здатності (ключ "full_path" у словнику кадру). Тоді
    скріншоти звіту не треба заново діставати з відео (друге, часто
    таке ж довге декодування) - їх достатньо перенести звідси.

    is_cancelled - функція БЕЗ аргументів, що повертає True/False,
    якщо передана. Перевіряється на кожному кандидаті - якщо
    користувач натиснув "Зупинити аналіз", нарізка одразу перериваємо
    (StopIteration з циклу) і повертаємо вже зібрані на той момент
    кадри, замість того, щоб чесно дорізати все відео намарно.

    on_frame_ready(frame_dict, index) - необов'язковий колбек: якщо
    переданий, викликається СИНХРОННО одразу в момент, коли черговий
    кадр потрапляє у підсумковий список (тобто ЩЕ ПІД ЧАС нарізки, а
    не після її завершення). Це дозволяє викликаючому коду (main.py)
    почати відправляти вже готові кадри в Gemini, поки решта відео ще
    декодується, замість того, щоб чекати на весь список цілком -
    два найдовші етапи пайплайна (нарізка і звернення до Gemini) тоді
    йдуть паралельно, а не послідовно.

    Колбек викликається як з ОСНОВНОГО послідовного/сикаючого циклу
    нарізки (найпоширеніший шлях - десктоп з нормальними keyframe), так
    і з кожного слайсу паралельної нарізки (_extract_frames_parallel,
    повільний seek + довге відео) - у другому випадку з РІЗНИХ потоків
    одночасно, тож сам колбек має бути потокобезпечним (у main.py це
    так - див. on_frame_ready там). Порядок викликів на паралельному
    шляху НЕ гарантовано збігається з timestamp_sec (різні слайси
    покривають різні відрізки відео й можуть завершувати кадри
    вперемішку) - викликаючий код на це не покладається.

    Для апаратного декодування колбек НЕ викликається (навмисно - див.
    коментар над use_hw нижче: якщо hw-декодер обірветься раніше часу,
    прохід повторюється програмно з нуля, і вже відправлені в Gemini
    кадри дали б дублі). Викликаючий код мусить сам звірити, чи всі
    кадри з підсумкового списку вже пройшли через on_frame_ready (за
    довжиною), і за потреби доотправити решту заднім числом - див.
    коментар у main.py біля виклику extract_frames().

    Винятки з on_frame_ready НЕ повинні зупиняти нарізку - тому виклик
    обгорнутий у try/except: помилка колбека (наприклад, черга вже
    закрита) лише друкується в консоль, а декодування продовжується.
    """
    # Швидкий шлях (PyAV, кадри з ключових кадрів) - див. video_fast.py.
    # FastPathUnavailable кидається ДО видачі першого кадру, тож відкат на
    # старий шлях нижче безпечний (жодних дублів у on_frame_ready).
    if allow_fast:
        try:
            from video_fast import extract_frames_fast, FastPathUnavailable
        except Exception as error:  # noqa: BLE001 - нема PyAV/модуля -> старий шлях
            logger.debug(f"[diag] швидкий шлях недоступний (імпорт): {error}")
        else:
            try:
                return extract_frames_fast(
                    video_path, max_frames=max_frames, progress_callback=progress_callback,
                    is_cancelled=is_cancelled, full_res_dir=full_res_dir,
                    on_frame_ready=on_frame_ready, frame_count_callback=frame_count_callback,
                )
            except FastPathUnavailable as reason:
                logger.debug(f"[diag] швидкий шлях не підходить: {reason} - старий шлях")

    video = _open_capture(video_path)
    if not video.isOpened():
        raise ValueError(f"Не вдалось відкрити відео: {video_path}")

    manual_rotation = _prepare_orientation(video_path)

    fps, duration_sec, total_frame_count = _resolve_fps(video, video_path)

    candidate_budget = max_frames * CANDIDATE_OVERSAMPLE
    interval_sec = max(min_interval_sec, duration_sec / candidate_budget) if duration_sec else min_interval_sec

    is_heavy = _is_heavy_video(video, fps)
    if is_heavy:
        # Прохід через декодер все одно буде повним (див. коментар
        # біля _is_heavy_video вище) - економимо на тому, що можемо:
        # рідше робимо retrieve()+resize()+cvtColor()+absdiff().
        interval_sec *= HEAVY_CODEC_INTERVAL_MULTIPLIER

    interval_frames = max(1, round(interval_sec * fps))

    # Довжина одного відрізка (сегмента) для гарантії покриття. Якщо
    # тривалість невідома (duration_sec == 0) - сегментів не буде,
    # segment_sec лишається None, і нижче спрацьовує старий алгоритм
    # (чиста детекція руху, без гарантії по відрізках).
    segment_sec = (duration_sec / max_frames) if duration_sec else None

    # Сикання (швидкий шлях) вмикаємо тільки на не-Android і лише якщо
    # відомо, скільки всього кадрів у відео - без цього не можна
    # порахувати, коли зупинятись. Якщо OpenCV не зміг прочитати
    # total_frame_count (буває на побитих/екзотичних файлах) - чесний
    # відкат на повільний, але надійний послідовний прохід.
    use_seek = not _IS_ANDROID and total_frame_count > 0

    decode_cache_key = _resolve_fps_cache_key(video_path)
    # cached_measurements зберігає ЛИШЕ сирі заміри (measured_seek_cost,
    # seq_fps, use_hw) - НЕ готове рішення use_seek. Рішення завжди
    # перераховується нижче з актуальним candidate_count, навіть коли
    # заміри взяті з кешу.
    cached_measurements = (
        _decode_strategy_cache.get(decode_cache_key) if decode_cache_key is not None else None
    )

    # Перш ніж покладатись на use_seek - перевіряємо емпірично, чи
    # справді сикання тут дешеве (див. коментар біля _probe_seek_cost
    # вище: fourcc на Mac не дає це визначити наперед). Якщо ні -
    # чесно переходимо на послідовний прохід, який для файлів з
    # рідкими keyframe на практиці не повільніше, а часто і швидше,
    # бо не робить повторну ініціалізацію позиції на кожному кандидаті.
    # Якщо ЗАМІРИ для ЦЬОГО САМОГО файлу вже відомі з кешу (див.
    # _decode_strategy_cache вище) - самі проби (_probe_seek_cost,
    # _measure_sequential_fps) пропускаємо, але порівняння
    # seek_total_est vs seq_total_est РАХУЄМО ЗАНОВО з актуальним
    # candidate_count - бо саме candidate_count (а не заміри) - це
    # те, що змінюється від рівня до рівня ("Швидкий" -> "Максимальний").
    measured_seek_cost = None
    seq_fps = None
    if cached_measurements is not None:
        measured_seek_cost, seq_fps, _cached_use_hw = cached_measurements
        logger.debug(
            f"[diag] заміри сикання/послідовного проходу взяті з кешу "
            f"(сикання={('?' if measured_seek_cost is None else f'{measured_seek_cost:.3f}с')}, "
            f"послідовно={('?' if seq_fps is None else f'{seq_fps:.0f} к/с')}) - "
            f"самі проби пропущено, рішення перераховується під поточну кількість кандидатів"
        )
    elif use_seek:
        measured_seek_cost = _probe_seek_cost(video, total_frame_count)
        if measured_seek_cost is not None and measured_seek_cost > _SEEK_SLOW_THRESHOLD_SEC:
            seq_fps = _measure_sequential_fps(video)

    if use_seek and measured_seek_cost is not None and measured_seek_cost > _SEEK_SLOW_THRESHOLD_SEC:
        # Сикання не миттєве. Але "повільне" - поняття відносне: обираємо
        # за ПІДСУМКОВОЮ оцінкою часу, а не за фіксованим порогом.
        #   сикання:      кількість кандидатів x вартість одного сикання
        #   послідовно:   усі кадри / швидкість grab()
        # (на годинному відео 200 кандидатів x 0.31с = 63с проти
        # 120 тис. кадрів / 400 к/с = 300с - сикання виграє в 5 разів,
        # хоча одне сикання й "повільне"; на 10-секундному ролику
        # навпаки - 22 x 0.7с = 15с проти ~9с послідовно.)
        # ПЕРЕРАХОВУЄТЬСЯ на кожен виклик - candidate_count залежить від
        # max_frames (рівня деталізації), а не лише від файлу, тож
        # кешувати це підсумкове число не можна (був баг: рішення
        # застигало на кандидатах першого прогону і не оновлювалось для
        # наступних, значно щільніших рівнів).
        candidate_count = math.ceil(total_frame_count / interval_frames)
        seek_total_est = candidate_count * measured_seek_cost
        seq_total_est = (total_frame_count / seq_fps) if seq_fps else None
        if seq_total_est is not None and seek_total_est < seq_total_est * 0.8:
            logger.debug(
                f"[diag] сикання {measured_seek_cost:.3f}с/виклик, кандидатів={candidate_count} - "
                f"оцінка {seek_total_est:.0f}с проти {seq_total_est:.0f}с послідовно - лишаю сикання."
            )
        else:
            est_text = f"{seq_total_est:.0f}с" if seq_total_est is not None else "?"
            logger.debug(
                f"[diag] сикання повільне (~{measured_seek_cost:.3f}с/виклик; кандидатів="
                f"{candidate_count}; оцінка {seek_total_est:.0f}с проти {est_text} послідовно). "
                f"Перемикаюсь на послідовний прохід (grab())."
            )
            use_seek = False

    # Діагностика: одноразовий запис ключових параметрів нарізки в
    # лог - щоб було видно, ЧОМУ нарізка йде так чи інакше довго
    # (наприклад: рідкі keyframe у файлі змушують "сикання" на
    # практиці декодувати чималі шматки відео замість миттєвого
    # переходу). Тимчасова діагностика, прибрати можна в будь-який
    # момент - на саму нарізку жодного впливу не має.
    logger.debug(
        f"[diag] extract_frames: кодек={_read_fourcc(video) or '?'} "
        f"важке_відео={is_heavy} fps={fps:.2f} "
        f"тривалість={duration_sec:.1f}с "
        f"total_frame_count(OpenCV)={total_frame_count} "
        f"interval_sec={interval_sec:.2f} interval_frames={interval_frames} "
        f"segment_sec={segment_sec if segment_sec is None else round(segment_sec, 2)} "
        f"use_seek={use_seek} "
        f"виміряна_вартість_сикання="
        f"{'?' if measured_seek_cost is None else f'{measured_seek_cost:.3f}с'}"
    )

    # Файл із повільним seek: однопотоковий послідовний прохід декодує все
    # відео одним ядром (на тесті 66 хв це ~334 с). Ділимо на слайси й
    # декодуємо паралельно. Лише десктоп, лише коли відомі тривалість і
    # кількість кадрів (потрібні для сегментів) і кадрів достатньо, щоб
    # слайси мали сенс; інакше - старий шлях без змін.
    # Апаратне декодування - лише для послідовного проходу (без seek, бо
    # seek на апаратному декодері ненадійний) і не для паралельних
    # слайсів. Рішення - самозамір (_hw_decode_is_faster).
    # use_hw, на відміну від use_seek, НЕ залежить від candidate_count -
    # це властивість самого файлу/декодера (чи апаратний декодер
    # взагалі швидший за софтверний послідовний прохід), тож його
    # можна безпечно брати з кешу як є, без перерахунку.
    use_hw = False
    if cached_measurements is not None:
        use_hw = cached_measurements[2]
        if use_hw and not use_seek:
            hw_video = _open_capture(video_path, hw=True)
            if hw_video.isOpened():
                video.release()
                video = hw_video
            else:
                use_hw = False
    elif not use_seek and allow_hw and _hw_decode_is_faster(video_path):
        hw_video = _open_capture(video_path, hw=True)
        if hw_video.isOpened():
            video.release()
            video = hw_video
            use_hw = True

    if cached_measurements is None and decode_cache_key is not None:
        if len(_decode_strategy_cache) >= _DECODE_STRATEGY_CACHE_MAX_SIZE:
            _decode_strategy_cache.clear()
        # Кешуємо ЛИШЕ сирі заміри (measured_seek_cost, seq_fps, use_hw),
        # НЕ готове use_seek - див. коментар біля _decode_strategy_cache.
        _decode_strategy_cache[decode_cache_key] = (measured_seek_cost, seq_fps, use_hw)

    # Пул для АСИНХРОННОГО запису full-res кадрів (див. _save_full_frame) -
    # спільний на весь виклик extract_frames(), незалежно від того, який
    # шлях нарізки далі спрацює (паралельні слайси чи однопотоковий
    # прохід). None, якщо кеш узагалі не потрібен (full_res_dir не заданий) -
    # тоді _save_full_frame() лишається синхронною, як і раніше.
    full_frame_pool = ThreadPoolExecutor(max_workers=_FULL_FRAME_SAVE_WORKERS) if full_res_dir else None

    # БУЛО (баг): тут стояла умова `not use_seek and ...` - тобто
    # найпоширеніший на десктопі шлях (сикання, use_seek=True) НІКОЛИ
    # не потрапляв у паралельну нарізку, навіть на годинних відео з
    # сотнями кандидатів, кожен по 0.3-0.4с. "Нарізка й аналіз
    # паралельно" у такому разі стосувалось лише самого Gemini (батчі
    # йдуть під час нарізки), а сама нарізка йшла в один потік - і на
    # практиці регулярно виявлялась ДОВШОЮ за аналіз (той факт, що
    # оцінка `measured_seek_cost` - лише 3 проби, - могла помітно
    # відхилятись від реальної середньої вартості по всьому відео,
    # робив це ще помітнішим). Сикання кандидатів незалежне одне від
    # одного (на відміну від hw-декодування - див. use_hw нижче), тож
    # ділиться на слайси так само тривіально, як і послідовний прохід -
    # єдине, що справді виключає паралельний шлях, це апаратне
    # декодування (use_hw), де перезапуск програмним шляхом "з нуля"
    # при обриві hw-декодера зробив би вже надіслані в Gemini кадри
    # дублями.
    parallel_workers = _decode_worker_count(max_frames, min_items_per_worker=50)
    if (not use_hw and segment_sec and total_frame_count > 0 and parallel_workers > 1
            and duration_sec >= _PARALLEL_MIN_DURATION_SEC):
        video.release()
        try:
            return _extract_frames_parallel(
                video_path, fps, total_frame_count, interval_frames, segment_sec, max_frames,
                manual_rotation, parallel_workers, progress_callback, is_cancelled, full_res_dir,
                frame_count_callback, on_frame_ready, use_seek,
            )
        finally:
            # full_frame_pool тут не використовується (паралельний шлях
            # тепер - окремі процеси, і пише full-res кадри синхронно,
            # див. докстрінг _extract_frames_parallel) - shutdown() лише
            # коректно закриває порожній пул, створений вище про запас.
            if full_frame_pool is not None:
                full_frame_pool.shutdown(wait=True)

    frames = []
    last_kept_gray = None      # для старого бінарного режиму (segment_sec невідомий) - див. коментар над MOTION_BASELINE_WINDOW
    prev_candidate_gray = None   # попередній КАНДИДАТ (не обов'язково відібраний) - для локального рівня руху
    motion_baseline = deque(maxlen=MOTION_BASELINE_WINDOW)
    current_segment_index = -1
    segment_best = None    # {"score", "timestamp_sec", "frame", "full", "gray"} - найкращий кандидат поточного відрізка (СИРИЙ кадр, ще не закодований); кодуємо в JPEG лише той, що реально піде в підсумок

    def flush_segment_best():
        """Забирає найкращий (за скором відносно фону) кадр відрізка в підсумок."""
        nonlocal segment_best
        if segment_best is not None and len(frames) < max_frames:
            ok, buffer = cv2.imencode(".jpg", segment_best["frame"], _GEMINI_JPEG_ENCODE_PARAMS)
            if ok:
                frames.append({
                    "timestamp_sec": segment_best["timestamp_sec"],
                    "jpg_bytes": buffer.tobytes(),
                    "full_path": _save_full_frame(full_res_dir, segment_best.get("full"), full_frame_pool),
                })
                if not use_hw:
                    _notify_frame_ready(on_frame_ready, frames[-1], len(frames) - 1)
                # Лічильник "нарізано X" - працює і з hw-декодером
                if frame_count_callback:
                    frame_count_callback(len(frames))
        segment_best = None

    last_reported_percent = -1

    # Діагностика: розділяємо час на "дістати кандидата" (seek/grab +
    # read/retrieve - те, що робить сам OpenCV/декодер) і "обробити
    # кандидата" (resize/cvtColor/детекція руху/кодування в JPEG - те,
    # що робимо ми). Якщо весь час іде в перше - проблема в самому
    # декодуванні (напр. рідкі keyframe роблять "сикання" повільним),
    # якщо в друге - раніше треба оптимізувати наш код обробки.
    candidate_fetch_sec = 0.0
    candidate_process_sec = 0.0
    candidate_count = 0

    iter_state = {"decoded": 0}
    candidate_iterator = _iter_candidate_frames(video, interval_frames, total_frame_count, use_seek, iter_state)
    while True:
        fetch_start_ts = time.time()
        try:
            frame_index, frame = next(candidate_iterator)
        except StopIteration:
            break
        candidate_fetch_sec += time.time() - fetch_start_ts
        candidate_count += 1

        if is_cancelled is not None and is_cancelled():
            # Користувач натиснув "Зупинити аналіз" - віддаємо те, що
            # вже встигли нарізати (може бути й порожній список), і не
            # витрачаємо час на решту відео.
            break

        process_start_ts = time.time()

        timestamp_sec = frame_index / fps

        if progress_callback and total_frame_count:
            # Кидаємо колбек не на КОЖНОМУ кандидаті (їх можуть бути
            # сотні), а раз на цілий відсоток - цього достатньо для
            # плавного прогрес-бару і не заважає швидкості нарізки.
            percent = int(frame_index / total_frame_count * 100)
            if percent != last_reported_percent:
                progress_callback(min(percent, 100) / 100)
                last_reported_percent = percent

        if full_res_dir:
            # Повнорозмірний повернутий кадр знадобиться нижче для
            # кешу (segment_best["full"] / прямий _save_full_frame) -
            # доводиться повертати його цілком, як і раніше.
            if manual_rotation:
                frame = _apply_manual_rotation(frame, manual_rotation)
            gemini_frame = resize_frame(frame, max_width=GEMINI_FRAME_WIDTH)
        else:
            # Кеш не потрібен - "frame" нижче ніде більше не
            # використовується, тож можна зменшити СИРИЙ кадр і
            # повернути вже маленьку картинку (див. _resize_then_rotate).
            gemini_frame = _resize_then_rotate(frame, manual_rotation, GEMINI_FRAME_WIDTH)
        gray = cv2.cvtColor(gemini_frame, cv2.COLOR_BGR2GRAY)

        try:
            if segment_sec:
                # min(...) - захист від похибки округлення float на межі
                # останнього відрізка (інакше вона могла дати на один
                # відрізок, а отже й кадр, більше за max_frames).
                segment_index = min(int(timestamp_sec // segment_sec), max_frames - 1)
                if segment_index != current_segment_index:
                    # Новий відрізок почався - попередній вже завершено,
                    # забираємо його переможця (найвищий скор).
                    flush_segment_best()
                    if len(frames) >= max_frames:
                        # Ліміт міг вичерпатись і через фонові відрізки
                        # (напр. довге повністю статичне відео) - без цієї
                        # перевірки цикл декодував би відео до самого
                        # кінця намарно, нічого вже не додаючи.
                        break
                    current_segment_index = segment_index

                # Рівень руху ЩОДО ПОПЕРЕДНЬОГО КАНДИДАТА (див. коментар
                # над MOTION_BASELINE_WINDOW) - не щодо останнього
                # відібраного кадру, який може лежати на цілий відрізок
                # у минулому і на постійному фоновому русі (хвилі) дає
                # хибний "рух" майже завжди.
                motion_percent = 0.0 if prev_candidate_gray is None else _motion_percent(prev_candidate_gray, gray)
                prev_candidate_gray = gray

                baseline = statistics.median(motion_baseline) if motion_baseline else 0.0
                score = motion_percent - baseline
                motion_baseline.append(motion_percent)

                if segment_best is None or score > segment_best["score"]:
                    # .copy() обов'язковий: і retrieve(), і read()
                    # перевикористовують один і той самий буфер пам'яті
                    # на кожному виклику. Копіюємо лише коли кандидат
                    # реально стає новим лідером відрізка - токени й
                    # витрати на копіювання не ростуть відносно старої
                    # логіки.
                    segment_best = {
                        "score": score,
                        "timestamp_sec": timestamp_sec,
                        "frame": gemini_frame.copy(),
                        "full": frame.copy() if full_res_dir else None,
                        "gray": gray,
                    }
            else:
                # Тривалість відео невідома - segment_sec порахувати
                # нема з чого, лишаємо старий бінарний режим (порівняння
                # з останнім відібраним кадром, перший рух - переможець).
                is_first_frame = last_kept_gray is None
                motion_detected = not is_first_frame and _has_motion(last_kept_gray, gray)
                if is_first_frame or motion_detected:
                    ok, buffer = cv2.imencode(".jpg", gemini_frame, _GEMINI_JPEG_ENCODE_PARAMS)
                    if ok:
                        frames.append({
                            "timestamp_sec": timestamp_sec,
                            "jpg_bytes": buffer.tobytes(),
                            "full_path": _save_full_frame(full_res_dir, frame, full_frame_pool),
                        })
                        last_kept_gray = gray
                        if not use_hw:
                            _notify_frame_ready(on_frame_ready, frames[-1], len(frames) - 1)
                        if frame_count_callback:
                            frame_count_callback(len(frames))
                        if len(frames) >= max_frames:
                            break
        finally:
            # finally виконається навіть при continue/break вище - тож
            # час обробки кандидата рахується коректно в будь-якому
            # разі, а не тільки коли цикл "доходить до кінця" ітерації.
            candidate_process_sec += time.time() - process_start_ts

    logger.debug(
        f"[diag] extract_frames: кандидатів опрацьовано={candidate_count} "
        f"час_на_дістати_кандидата(seek+read)={candidate_fetch_sec:.1f}с "
        f"час_на_обробку(resize/детекція/кодування)={candidate_process_sec:.1f}с"
    )

    if (use_hw and total_frame_count
            and iter_state["decoded"] < total_frame_count * 0.9
            and not (is_cancelled is not None and is_cancelled())
            and len(frames) < max_frames):
        # Апаратний декодер обірвав читання значно раніше кінця - не
        # довіряємо цьому результату, робимо чесно програмно.
        logger.warning("[diag] апаратне декодування обірвалось раніше часу - повторюю програмно")
        video.release()
        if full_frame_pool is not None:
            # Результат цієї спроби відкидається цілком (рекурсивний
            # виклик нижче зробить усе заново і сам створить свій
            # пул) - не чекаємо на вже запущені записи, просто не
            # заважаємо їм дописатись у фоні.
            full_frame_pool.shutdown(wait=False)
        return extract_frames(video_path, max_frames, min_interval_sec, progress_callback,
                              is_cancelled, full_res_dir, allow_hw=False, allow_fast=False)

    flush_segment_best()  # останній відрізок відео міг не встигнути "закритись" наступним

    if progress_callback:
        progress_callback(1.0)

    video.release()
    if full_frame_pool is not None:
        # Дочекатись усіх асинхронних записів full-res кадрів ПЕРЕД
        # поверненням - див. докстрінг _save_full_frame().
        full_frame_pool.shutdown(wait=True)
    return frames


def _notify_frame_ready(on_frame_ready, frame_dict, index):
    """Безпечно викликає on_frame_ready() - винятки колбека (напр. черга
    вже закрита) не повинні валити саму нарізку відео."""
    if on_frame_ready is None:
        return
    try:
        on_frame_ready(frame_dict, index)
    except Exception as error:  # noqa: BLE001
        logger.warning(f"[diag] on_frame_ready впав, продовжую нарізку: {error}")


def grab_screenshot(video_path, timestamp_sec, save_path):
    """
    Дістає один кадр за часовою міткою і зберігає як є, без рамок,
    в ОРИГІНАЛЬНІЙ якості (тут роздільну здатність НЕ стискаємо, на
    відміну від кадрів, які їдуть у Gemini) - щоб в інтерфейсі можна
    було дивитись і зумити чітку картинку.

    ВАЖЛИВО: раніше тут використовувався video.set(CAP_PROP_POS_MSEC).
    На багатьох Android-збірках OpenCV цей seek або "прилипає" до
    найближчого опорного кадру (keyframe) - вони в телефонних
    H.264-роликах стоять рідко, - або взагалі мовчки НЕ спрацьовує,
    і читання просто продовжується з поточної позиції (для щойно
    відкритого VideoCapture це позиція 0) - тому в звіті всім
    моментам підставлявся один і той самий, перший кадр відео.

    ТЕПЕР на десктопі (Mac/Windows) сикаємо напряму через
    CAP_PROP_POS_FRAMES (той самий надійний спосіб, що вже
    використовує extract_frames() для нарізки кандидатів) - кадри між
    поточною позицією і потрібним індексом взагалі не декодуються, тож
    один скріншот дістається практично миттєво незалежно від того, на
    якій хвилині відео він лежить. На Android так само лишаємо
    послідовний прохід через grab() - сикання там ненадійне (див.
    коментар біля _IS_ANDROID на початку файлу).

    ПРИМІТКА: якщо потрібно дістати ДЕКІЛЬКА кадрів з одного відео
    (як у звіті аналізу) - використовуй grab_screenshots_batch()
    нижче, вона відкриває файл лише ОДИН раз замість N.
    """
    video = _open_capture(video_path)
    manual_rotation = _prepare_orientation(video_path)

    fps, _duration_sec, _total_frame_count = _resolve_fps(video, video_path)
    target_frame_index = max(0, int(round(timestamp_sec * fps)))

    if _IS_ANDROID:
        frame_index = 0
        ok = True
        while frame_index < target_frame_index and ok:
            ok = video.grab()
            frame_index += 1
        success, frame = video.read()
    else:
        video.set(cv2.CAP_PROP_POS_FRAMES, target_frame_index)
        success, frame = video.read()

    video.release()

    if success:
        if manual_rotation:
            frame = _apply_manual_rotation(frame, manual_rotation)
        return _write_image(save_path, frame)

    return False


def _grab_screenshot_chunk(video_path, manual_rotation, chunk):
    """Один потік: свій VideoCapture, суцільний шматок відсортованих цілей.
    Повертає (множина збережених шляхів, кількість цілей у шматку)."""
    # prefer_ffmpeg=True - той самий фікс, що й у _extract_slice_sequential:
    # цей виклик йде з-під _single_threaded_ffmpeg_decode() (див. виклик
    # у grab_screenshots_batch()), а вона обмежує лише FFmpeg-бекенд.
    video = _open_capture(video_path, prefer_ffmpeg=True)
    saved = set()
    try:
        for target_frame_index, save_path in chunk:
            video.set(cv2.CAP_PROP_POS_FRAMES, target_frame_index)
            success, frame = video.read()
            if success:
                if manual_rotation:
                    frame = _apply_manual_rotation(frame, manual_rotation)
                if _write_image(save_path, frame):
                    saved.add(save_path)
    finally:
        video.release()
    return saved, len(chunk)


class ScreenshotDiskFullError(OSError):
    """
    П3 плану: диск скінчився (ENOSPC) ПОСЕРЕД пакетного збереження
    скріншотів у grab_screenshots_batch().

    БУЛО (реальний випадок, 403 знайдених моменти втрачено цілком):
    один OSError із ЩОЙНО завершеного future обривав as_completed()
    одразу - функція так ніколи й не доходила до `return saved`, тож
    навіть ті скріншоти, які фізично ВЖЕ лежали на диску (з інших
    futures, що встигли завершитись РАНІШЕ за той, що впав), губились
    для виклику - grab_screenshots_batch() просто ніколи не повертав
    жодного значення, а кидав виняток. У main.py це рахувалось як
    "Непередбачена помилка" на РІВНІ ВСЬОГО pipeline - і побудова
    results/history_store.append_session() (вона йде ПІСЛЯ виклику
    цієї функції) просто не наступала - усі вже знайдені Gemini
    моменти викидались РАЗОМ із тими кількома скріншотами, які не
    встигли зберегтися.

    ТЕПЕР: при ENOSPC не обриваємо collection одразу - даємо
    ЗАПУЩЕНИМ ВЖЕ futures дозавершитись (деякі процеси могли встигнути
    записати свій шматок ДО того, як диск остаточно скінчився), потім
    кидаємо ЦЕЙ клас із частковим saved УСЕРЕДИНІ - виклик
    (grab_screenshots_batch -> main.py) дістає його через
    error.saved/error.done/error.total і продовжує з тим, що реально
    є, замість викидати все.
    """

    def __init__(self, saved, done_targets, total_targets):
        super().__init__(28, "No space left on device")
        self.saved = saved
        self.done_targets = done_targets
        self.total_targets = total_targets


def grab_screenshots_batch(video_path, timestamp_and_path_list, progress_callback=None):
    """
    Дістає ОДРАЗУ кілька кадрів за ОДНЕ відкриття відеофайлу.

    ПРОБЛЕМА, яку це вирішує: раніше для звіту з N знайдених моментів
    grab_screenshot() викликався в циклі N разів - кожен виклик заново
    відкривав VideoCapture і гортав файл від нульового кадру. Для
    відео з великою кількістю знайдених моментів це N повних проходів
    замість одного.

    ТУТ: на десктопі (Mac/Windows) кожен потрібний кадр дістається
    прямим сиканням (CAP_PROP_POS_FRAMES) - так само, як тепер робить
    і grab_screenshot() вище. Кадри, яких нема серед цілей, взагалі не
    декодуються - для звіту з N моментами, розкиданими по годинному
    відео, це рази, а не відсотки економії порівняно з послідовним
    grab() через увесь файл. На Android сикання ненадійне (див.
    коментар біля _IS_ANDROID на початку файлу), тому там лишається
    старий послідовний прохід ВПЕРЕД по відсортованих цілях - як і
    раніше, він відкриває файл лише ОДИН раз замість N.

    timestamp_and_path_list: список кортежів (timestamp_sec, save_path).
    Повертає set успішно збережених save_path.

    progress_callback(fraction: float), якщо переданий, викликається
    ПІСЛЯ КОЖНОГО обробленого моменту (0..1 - частка вже пройдених
    цілей зі списку) - щоб інтерфейс міг показати РЕАЛЬНИЙ прогрес
    збереження скріншотів (для звіту з десятками моментів це відчутний
    за часом крок), а не "завислий" індикатор на весь час цього кроку,
    як було раніше.
    """
    video = _open_capture(video_path)
    manual_rotation = _prepare_orientation(video_path)
    fps, _duration_sec, _total_frame_count = _resolve_fps(video, video_path)

    targets = sorted(
        ((max(0, int(round(ts * fps))), path) for ts, path in timestamp_and_path_list),
        key=lambda item: item[0],
    )

    saved = set()
    total_targets = len(targets)

    if _IS_ANDROID:
        frame_index = 0
        disk_full_error = None
        for i, (target_frame_index, save_path) in enumerate(targets):
            while frame_index < target_frame_index:
                if not video.grab():
                    video.release()
                    if progress_callback:
                        progress_callback(1.0)
                    if disk_full_error is not None:
                        raise ScreenshotDiskFullError(saved, i, total_targets) from disk_full_error
                    return saved
                frame_index += 1

            success, frame = video.read()
            frame_index += 1
            if success:
                if manual_rotation:
                    frame = _apply_manual_rotation(frame, manual_rotation)
                try:
                    if _write_image(save_path, frame):
                        saved.add(save_path)
                except OSError as error:
                    # Той самий принцип, що й у ProcessPoolExecutor-гілці
                    # нижче: одна дискова помилка не повинна обривати цикл -
                    # дозберігаємо решту цілей, кидаємо ScreenshotDiskFullError
                    # із частковим saved УЖЕ ПІСЛЯ циклу.
                    disk_full_error = error
            if progress_callback and total_targets:
                progress_callback((i + 1) / total_targets)
        if disk_full_error is not None:
            raise ScreenshotDiskFullError(saved, total_targets, total_targets) from disk_full_error
    else:
        workers = _decode_worker_count(total_targets, min_items_per_worker=8)
        if workers > 1:
            # Кожен seek на файлі з рідкими keyframe коштує сотні мілісекунд
            # (на тесті ~0.55 с на скріншот, 324 шт. = 178 с), і всі вони
            # незалежні - тож роздаємо суцільні шматки відсортованих цілей
            # потокам, кожен зі своїм VideoCapture. Прогрес - після кожного
            # шматка, з цього (викликаючого) потоку.
            video.release()
            chunk_count = min(total_targets, workers * 8)
            chunk_size = int(math.ceil(total_targets / chunk_count))
            chunks = [targets[i:i + chunk_size] for i in range(0, total_targets, chunk_size)]
            done_targets = 0
            # Процеси, а не потоки - з тієї ж причини, що й у
            # _extract_frames_parallel (див. велику примітку в її
            # докстрінгу): декодування - CPU-навантаження, GIL не дає
            # потокам реально ділити його між ядрами. Тут простіше:
            # результат кожного шматка (saved-шляхи, count) повертається
            # через futures.result() як і раніше - жодних Manager/Queue
            # не треба, бо тут немає ні "живого" стрімінгу кадрів, ні
            # спільного лічильника прогресу під час виконання (прогрес
            # оновлюється вже ПІСЛЯ завершення кожного шматка, у цьому
            # процесі, через as_completed нижче).
            with _single_threaded_ffmpeg_decode(), ProcessPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_grab_screenshot_chunk, video_path, manual_rotation, chunk) for chunk in chunks]
                disk_full_error = None
                for future in as_completed(futures):
                    try:
                        chunk_saved, chunk_len = future.result()
                    except OSError as error:
                        # ENOSPC (чи інша дискова помилка) в ОДНОМУ шматку не
                        # повинен обривати збір РЕШТИ futures - вони вже
                        # запущені (можливо, встигли записати свій шматок ДО
                        # того, як диск остаточно скінчився) - див.
                        # ScreenshotDiskFullError вище. Не намагаємось
                        # відрізнити ENOSPC від іншого OSError тут навмисно:
                        # яка б не була причина падіння ОДНОГО процесу, решта
                        # заслуговують на шанс дозавершитись і віддати те, що
                        # реально зберегли.
                        disk_full_error = error
                        continue
                    saved.update(chunk_saved)
                    done_targets += chunk_len
                    if progress_callback and total_targets:
                        progress_callback(done_targets / total_targets)
            if disk_full_error is not None:
                raise ScreenshotDiskFullError(saved, done_targets, total_targets) from disk_full_error
            return saved

        disk_full_error = None
        for i, (target_frame_index, save_path) in enumerate(targets):
            video.set(cv2.CAP_PROP_POS_FRAMES, target_frame_index)
            success, frame = video.read()
            if success:
                if manual_rotation:
                    frame = _apply_manual_rotation(frame, manual_rotation)
                try:
                    if _write_image(save_path, frame):
                        saved.add(save_path)
                except OSError as error:
                    disk_full_error = error
            if progress_callback and total_targets:
                progress_callback((i + 1) / total_targets)

    video.release()
    if progress_callback and not total_targets:
        progress_callback(1.0)  # нема жодної цілі - крок і так "завершений"
    if disk_full_error is not None:
        raise ScreenshotDiskFullError(saved, total_targets, total_targets) from disk_full_error
    return saved

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
