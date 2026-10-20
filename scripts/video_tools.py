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
video_tools.py

нарізка відео на кадри для джеміні і збереження скріншотів знайдених моментів.

ідея коротко, щоб не забути:
1. беру кандидатів-кадрів приблизно раз на кілька секунд
2. відео ділю на однакові відрізки часу, з кожного беру один найбільш рухливий кадр
3. довге відео ріжу на шматки і читаю паралельно, кожен шматок у своєму процесі
4. швидкість залежить від того, чи вміє відео швидко стрибати на потрібний кадр (сік), це й перевіряю
"""

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

# андроїд: там сік в опенсв ненадійний, тому по кадрах не стрибаю
_IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

# скільки процесів-декодерів максимум: не більше ядер і не більше 8,
# бо на хдд багато читачів разом гальмують один одного
PARALLEL_DECODE_WORKERS = min(os.cpu_count() or 4, 8)


# ---------------------------------------------------------------------------
# один декодер на процес, щоб паралельні шматки не билися за ядра
# ---------------------------------------------------------------------------
# сі-ві-ту відеокептчер через ффмпег сам бере кілька ядер. якщо відкрити
# кілька таких разом, вони заважають одне одному. тому на час паралельної
# роботи кажу ффмпегу брати один потік на декодер

_FFMPEG_CAPTURE_OPTIONS_ENV = "OPENCV_FFMPEG_CAPTURE_OPTIONS"
_ffmpeg_env_lock = threading.Lock()


@contextlib.contextmanager
def _single_threaded_ffmpeg_decode():
    # поки блок працює, кожен новий декодер ффмпег має один потік
    # на виході стару змінну повертаю назад
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
# таймаут для сі-ві-ту: на маку може зависнути на файлі, що не відео
# ---------------------------------------------------------------------------
# головний потік ткінтера викликає перевірку файлу, тому зависання тут
# вбиває вікно. запускаю перевірку в окремому потоці з лімітом часу.
# завислий потік вбити не можна, він просто лишається фоновим

CV2_PROBE_TIMEOUT_SEC = 8.0


def run_with_timeout(func, timeout_sec=CV2_PROBE_TIMEOUT_SEC):
    # запускаю ф-цію в потоці, повертаю (результат, чи_вийшов_час)
    result_box = {}

    def target():
        try:
            result_box["value"] = func()
        except Exception as error:  # noqa: BLE001 - передаємо помилку назад
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
    # відкриваю відео
    # hw=True - прошу апаратне декодування, якщо не вийде, відкрию звичайно
    # prefer_ffmpeg - прошу саме ффмпег, тільки він слухає налаштування
    # потоків з _single_threaded_ffmpeg_decode()
    # авто-поворот опенсв вимикаю, кут повороту кручу сам
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


# ---------------------------------------------------------------------------
# апаратне декодування: один короткий замір на початку запуску програми
# ---------------------------------------------------------------------------
# міряю скільки кадрів за секунду дає звичайне декодування і скільки апаратне.
# апаратне беру тільки якщо воно швидше хоча б в 1.3 раза.
# результат пам'ятаю до закриття програми.
# SMART_SURVEILLANCE_HW_DECODE=1 завжди вмикає, =0 завжди вимикає

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
            if not video.isOpened() or not video.grab():  # перший кадр для розігріву
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
        logger.debug(f"[diag] замір декодування: програмне {sw_fps:.0f} к/с, "
                     f"апаратне {hw_fps:.0f} к/с")
    return _hw_decision


# скільки потоків пишуть повні кадри на диск у фоні
_FULL_FRAME_SAVE_WORKERS = 4

# паралельну нарізку вмикаю тільки для довгих відео,
# на коротких старт шматка довший за виграш
_PARALLEL_MIN_DURATION_SEC = 120.0


# ---------------------------------------------------------------------------
# запис файлів
# ---------------------------------------------------------------------------

def _write_image(path, frame):
    # пишу кадр у файл, кирилиця в шляху працює (сі-ві-ту імврайт цього не вміє на віндовс)
    # повертаю True якщо файл з'явився на диску
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
    # зберігаю повний кадр у теку кешу і повертаю шлях (або None)
    # пул, якщо є, пише у фоні, щоб декодер читав наступний кадр
    # поки попередній ще пишеться на диск
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
    # скільки процесів запустити: не більше ліміту, ядер
    # і кількості порцій роботи (item_count // min_items_per_worker)
    if _IS_ANDROID:
        return 1
    return max(1, min(PARALLEL_DECODE_WORKERS, _cpu_count(), item_count // max(1, min_items_per_worker)))


# ---------------------------------------------------------------------------
# читаю мп4 напряму: поворот і точна тривалість
# ---------------------------------------------------------------------------
# мп4 це коробки (бокс). кожна починається з розміру (4 байти) і назви
# (4 байти), далі дані. коробки можуть містити інші коробки.
# треба: moov -> trak -> tkhd (поворот) і moov -> mvhd (тривалість).
# опенсв на андроїд не вміє читати поворот, тому читаю сам

def _read_box_header(file, offset):
    # читаю заголовок однієї коробки, повертаю (назва, розмір, початок_даних) або None
    file.seek(offset)
    header = file.read(8)
    if len(header) < 8:
        return None

    size = int.from_bytes(header[0:4], byteorder="big")
    box_type = header[4:8]
    data_start = offset + 8

    if size == 1:
        # розмір не влазить в 4 байти, він далі, 8 байтів
        big_size_bytes = file.read(8)
        if len(big_size_bytes) < 8:
            return None
        size = int.from_bytes(big_size_bytes, byteorder="big")
        data_start = offset + 16
    elif size == 0:
        # розмір 0 це до кінця файлу
        file.seek(0, 2)
        size = file.tell() - offset

    return box_type, size, data_start


def _find_box(file, box_type, start, end):
    # шукаю коробку з назвою box_type серед сусідів в межах [start, end)
    # повертаю (початок_даних, розмір_даних) або None
    offset = start
    while offset < end:
        header = _read_box_header(file, offset)
        if header is None:
            return None

        box_type_found, box_size, data_start = header
        if box_size <= 0:
            return None  # битий заголовок, далі йти не можна

        data_size = box_size - (data_start - offset)

        if box_type_found == box_type:
            return data_start, data_size

        offset += box_size  # йду до наступної коробки

    return None


# матриця повороту: числа у форматі фікс-поінт 16.16 (1.0 = 0x10000)
_ONE = 0x10000

_ROTATION_MATRIX_TO_ANGLE = {
    (_ONE, 0, 0, _ONE): 0,
    (0, _ONE, -_ONE, 0): 90,
    (-_ONE, 0, 0, -_ONE): 180,
    (0, -_ONE, _ONE, 0): 270,
}


def _matrix_to_angle(a, b, c, d):
    # 4 числа матриці перетворюю на кут, невідома матриця -> 0
    return _ROTATION_MATRIX_TO_ANGLE.get((a, b, c, d), 0)


def _read_tkhd_fields(file, data_start, data_size):
    # читаю з ткхд ширину, висоту і матрицю (a, b, c, d)
    # повертаю (ширина, висота, a, b, c, d) або None
    file.seek(data_start)
    payload = file.read(data_size)
    if len(payload) < 1:
        return None

    version = payload[0]

    # розмір полів з датами залежить від версії коробки
    if version == 1:
        time_fields_size = 8 + 8 + 4 + 4 + 8
    else:
        time_fields_size = 4 + 4 + 4 + 4 + 4

    # після дат однаковий блок на 16 байт
    fixed_block_size = 8 + 2 + 2 + 2 + 2

    matrix_offset = 4 + time_fields_size + fixed_block_size  # 4 це версія і прапорці
    matrix_size = 36                                         # 9 чисел по 4 байти

    matrix_end = matrix_offset + matrix_size
    width_height_end = matrix_end + 8

    if len(payload) < width_height_end:
        return None

    def read_fixed_point(raw_bytes):
        # зі знаком, бо в матриці є -1.0
        return int.from_bytes(raw_bytes, byteorder="big", signed=True)

    a = read_fixed_point(payload[matrix_offset: matrix_offset + 4])
    b = read_fixed_point(payload[matrix_offset + 4: matrix_offset + 8])
    c = read_fixed_point(payload[matrix_offset + 12: matrix_offset + 16])
    d = read_fixed_point(payload[matrix_offset + 16: matrix_offset + 20])

    width = int.from_bytes(payload[matrix_end: matrix_end + 4], byteorder="big")
    height = int.from_bytes(payload[matrix_end + 4: matrix_end + 8], byteorder="big")

    return width, height, a, b, c, d


def read_rotation_from_mp4(video_path):
    # кут повороту відео-треку (0, 90, 180 або 270), якщо щось не так то 0
    try:
        with open(video_path, "rb") as file:
            file.seek(0, 2)
            file_size = file.tell()

            moov_location = _find_box(file, b"moov", 0, file_size)
            if moov_location is None:
                return 0

            moov_start, moov_size = moov_location
            moov_end = moov_start + moov_size

            # треків може бути кілька (відео, аудіо), беру перший
            # де ширина і висота не нульові, це відео
            offset = moov_start
            while offset < moov_end:
                trak_location = _find_box(file, b"trak", offset, moov_end)
                if trak_location is None:
                    break

                trak_start, trak_size = trak_location
                trak_end = trak_start + trak_size

                tkhd_location = _find_box(file, b"tkhd", trak_start, trak_end)
                if tkhd_location is not None:
                    tkhd_start, tkhd_size = tkhd_location
                    fields = _read_tkhd_fields(file, tkhd_start, tkhd_size)
                    if fields is not None:
                        width, height, a, b, c, d = fields
                        if width != 0 and height != 0:
                            return _matrix_to_angle(a, b, c, d)

                offset = trak_start + trak_size

            return 0
    except (OSError, struct.error, ValueError):
        return 0


def _read_mvhd_duration(file, moov_start, moov_end):
    # тривалість відео з коробки мвхд, не залежить від підрахунку кадрів опенсв
    # який для деяких файлів занижується
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
            timescale = int.from_bytes(payload[20:24], byteorder="big")
            duration = int.from_bytes(payload[24:32], byteorder="big")
        else:
            timescale = int.from_bytes(payload[12:16], byteorder="big")
            duration = int.from_bytes(payload[16:20], byteorder="big")
    except (IndexError, struct.error):
        return None

    if timescale <= 0:
        return None
    return duration / timescale


def read_duration_from_mp4(video_path):
    # точна тривалість відео в секундах, None якщо прочитати не вдалось
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
# фпс: опенсв іноді занижує фпс рівно в 2-4 рази, це перевіряю
# ---------------------------------------------------------------------------

_FPS_MISMATCH_TOLERANCE = 0.05
_FPS_MISMATCH_MAX_MULTIPLE = 4


def _is_suspicious_mismatch(raw_fps, effective_fps):
    # True якщо два фпс відрізняються рівно в ціле число разів (2x, 3x, ...)
    if raw_fps <= 0 or effective_fps <= 0:
        return False

    ratio = effective_fps / raw_fps
    if ratio < 1:
        ratio = 1 / ratio

    nearest_multiple = round(ratio)
    if nearest_multiple < 2 or nearest_multiple > _FPS_MISMATCH_MAX_MULTIPLE:
        return False

    return abs(ratio - nearest_multiple) / nearest_multiple <= _FPS_MISMATCH_TOLERANCE


def _probe_seek_cost(video, total_frame_count, sample_count=3):
    # середній час одного стрибка (сік) по відео в секундах
    # None якщо жоден стрибок не вдався, після перевірки повертаю позицію на початок
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
            break  # і так видно що повільно, далі не міряю

    video.set(cv2.CAP_PROP_POS_FRAMES, 0)

    if successful == 0:
        return None
    return total_time / successful


def _frame_count_is_trustworthy(video, total_frame_count, duration_sec, raw_fps):
    # перевіряю чи справді в файлі стільки кадрів, скільки каже опенсв
    # ставлю позицію трохи за межу яку дає менший фпс. якщо там є
    # відмінний від граничного кадр, то кадри є і лічильник правдивий.
    # якщо кадру нема або він такий самий як граничний, лічильник роздутий
    raw_frame_estimate = int(duration_sec * raw_fps)
    if raw_frame_estimate <= 0 or raw_frame_estimate >= total_frame_count - 1:
        return True

    # якщо стрибки повільні, цій перевірці вірити не можна
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
        video.set(cv2.CAP_PROP_POS_FRAMES, 0)

    if not ok_boundary or not ok_beyond:
        return False

    # порівнюю дуже маленькі копії, щоб не рахувати зайвого
    small_boundary = cv2.resize(cv2.cvtColor(frame_boundary, cv2.COLOR_BGR2GRAY), (32, 32))
    small_beyond = cv2.resize(cv2.cvtColor(frame_beyond, cv2.COLOR_BGR2GRAY), (32, 32))
    diff = cv2.absdiff(small_boundary, small_beyond)
    return cv2.countNonZero(diff) > 0


# кеш фпс і тривалості по файлу, ключ: шлях, розмір і час зміни
_resolve_fps_cache = {}
_RESOLVE_FPS_CACHE_MAX_SIZE = 64

# кеш замірів (не рішень!) для файлу: скільки коштує сік, швидкість
# послідовного читання і чи вигідне апаратне декодування.
# рішення про сік рахую щоразу, бо воно залежить від max_frames
_decode_strategy_cache = {}
_DECODE_STRATEGY_CACHE_MAX_SIZE = 64

_SEEK_SLOW_THRESHOLD_SEC = 0.15


def _resolve_fps_cache_key(video_path):
    # (шлях, розмір, час зміни), якщо файлу нема то None
    try:
        stat = os.stat(video_path)
        return (video_path, stat.st_size, stat.st_mtime)
    except OSError:
        return None


def _resolve_fps(video, video_path):
    # повертаю (фпс, тривалість, кількість кадрів) з кешем
    cache_key = _resolve_fps_cache_key(video_path)
    if cache_key is not None and cache_key in _resolve_fps_cache:
        return _resolve_fps_cache[cache_key]

    result = _resolve_fps_uncached(video, video_path)
    if cache_key is not None:
        if len(_resolve_fps_cache) >= _RESOLVE_FPS_CACHE_MAX_SIZE:
            _resolve_fps_cache.clear()
        _resolve_fps_cache[cache_key] = result
    return result


def _resolve_fps_uncached(video, video_path):
    # повертаю (фпс, тривалість, кількість кадрів) без кешу
    raw_fps = video.get(cv2.CAP_PROP_FPS) or 25.0

    # для файлу що не відео опенсв може повернути сміття (INT64_MIN)
    # таке значення вважаю невідомим (0)
    raw_total_frame_count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    total_frame_count = raw_total_frame_count if 0 < raw_total_frame_count < 100_000_000 else 0

    duration_sec = read_duration_from_mp4(video_path)

    if duration_sec and total_frame_count > 0:
        effective_fps = total_frame_count / duration_sec
        if _is_suspicious_mismatch(raw_fps, effective_fps):
            # підозра на баг, але може бути роздутий лічильник кадрів а не фпс
            if not _IS_ANDROID and not _frame_count_is_trustworthy(video, total_frame_count, duration_sec, raw_fps):
                corrected_total = int(round(duration_sec * raw_fps))
                logger.debug(
                    f"[diag] total_frame_count={total_frame_count} не підтвердився - "
                    f"беру {corrected_total} від raw_fps={raw_fps:.2f}"
                )
                return raw_fps, duration_sec, corrected_total

            logger.debug(
                f"[diag] fps підозрілий: OpenCV={raw_fps:.2f}, "
                f"розрахунковий={effective_fps:.2f} - беру розрахунковий"
            )
            return effective_fps, duration_sec, total_frame_count
        return raw_fps, duration_sec, total_frame_count

    if not duration_sec:
        duration_sec = total_frame_count / raw_fps if raw_fps > 0 else 0
    return raw_fps, duration_sec, total_frame_count


def get_video_duration_sec(video_path):
    # тривалість відео в секундах, 0.0 якщо невідомо або перевірка зависла
    # викликається з головного потоку, тому з таймаутом
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
        logger.warning(f"[diag] перевірка відео зависла, тривалість невідома: {video_path}")
        return 0.0
    return result


def _prepare_orientation(video_path):
    # кут повороту який треба застосувати до кадру
    return read_rotation_from_mp4(video_path)


def _apply_manual_rotation(frame, angle):
    # кут з метаданих це повернути за годинниковою стрілкою
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
    # спершу зменшую, потім повертаю, так швидше
    # для 90/270 обмежую висоту, бо після повороту ширина стає висотою
    if not angle:
        return resize_frame(frame, max_width=max_width)

    height, width = frame.shape[:2]
    limit_dim = height if angle in (90, 270) else width
    if limit_dim > max_width:
        scale = max_width / limit_dim
        frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
    return _apply_manual_rotation(frame, angle)


# ширина кадру що йде в джеміні, повне відео не зменшується,
# скріншоти беру з оригіналу
GEMINI_FRAME_WIDTH = 1024

# якість джпег для джеміні: модель і так бачить кадр у фіксованому бюджеті
# токенів, тож висока якість лише робить запит важчим
_GEMINI_JPEG_QUALITY = 85
_GEMINI_JPEG_ENCODE_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, _GEMINI_JPEG_QUALITY]

# відсоток пікселів що мають змінитись, щоб вважати це рухом
MOTION_THRESHOLD_PERCENT = 2.0

# скільки останніх кандидатів беру для фонового рівня руху
MOTION_BASELINE_WINDOW = 20

# кандидатів беру в 3 рази більше ніж кадрів у підсумку
CANDIDATE_OVERSAMPLE = 3

# для важкого відео (хевс + високий фпс) кандидатів беру рідше
HEAVY_CODEC_FPS_THRESHOLD = 30
HEAVY_CODEC_INTERVAL_MULTIPLIER = 2.5

_HEVC_FOURCC_CODES = {"hvc1", "hev1", "HEVC", "H265", "x265"}


def _read_fourcc(video):
    # назва кодека з відкритого відео (наприклад hvc1), '' якщо не вдалось
    fourcc_int = int(video.get(cv2.CAP_PROP_FOURCC))
    if fourcc_int <= 0:
        return ""
    try:
        return struct.pack("<I", fourcc_int).decode("ascii", errors="ignore").strip("\x00")
    except (struct.error, UnicodeDecodeError):
        return ""


def _is_heavy_video(video, fps):
    # важке це хевс + фпс вище порогу, якщо кодек не визначився то не важке
    fourcc = _read_fourcc(video)
    return fourcc in _HEVC_FOURCC_CODES and fps > HEAVY_CODEC_FPS_THRESHOLD


def _motion_percent(prev_gray, current_gray):
    # відсоток пікселів що помітно змінились між двома кадрами (0..100)
    diff = cv2.absdiff(prev_gray, current_gray)
    _, diff_mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)

    changed_pixels = cv2.countNonZero(diff_mask)
    total_pixels = diff_mask.shape[0] * diff_mask.shape[1]
    return (changed_pixels / total_pixels) * 100


def _has_motion(prev_gray, current_gray, threshold_percent=MOTION_THRESHOLD_PERCENT):
    # так/ні чи є рух, потрібно лише для режиму без тривалості
    return _motion_percent(prev_gray, current_gray) >= threshold_percent


def _measure_sequential_fps(video, sample_frames=40):
    # швидкість послідовного читання (кадрів/с), None якщо не вдалось
    try:
        video.set(cv2.CAP_PROP_POS_FRAMES, 0)
        if not video.grab():
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
    # віддаю (номер_кадру, кадр) для кожного кандидата
    # use_seek=True - стрибаю прямо на кандидата, інші кадри не декодую
    # use_seek=False - читаю всі кадри підряд (грааб), але декодую тільки кандидатів
    if use_seek:
        frame_index = 0
        while frame_index < total_frame_count:
            video.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            success, frame = video.read()
            if not success:
                logger.debug(f"[diag] сикання зупинилось на кадрі {frame_index} з {total_frame_count}")
                break
            yield frame_index, frame
            frame_index += interval_frames
    else:
        frame_index = 0
        while True:
            success = video.grab()
            if not success:
                logger.debug(f"[diag] послідовне читання зупинилось на кадрі {frame_index} з {total_frame_count}")
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
    # один шматок відео, виконується в окремому процесі
    # логіка та сама що в extract_frames: з кожного відрізка беру
    # найбільш рухливого кандидата. різниця в тому що обмеження
    # тут не глобальні, а тільки для цього шматка
    video = _open_capture(video_path, prefer_ffmpeg=True)
    if not video.isOpened():
        return []

    frames = []
    quota = max(0, seg_end - seg_start)
    current_segment_index = -1
    segment_best = None
    prev_candidate_gray = None
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
                if frame_count_out is not None:
                    frame_count_out[slice_id] = len(frames)
                if frame_queue is not None:
                    try:
                        frame_queue.put(frames[-1])
                    except Exception as error:  # noqa: BLE001
                        logger.warning(f"[diag] не вдалось покласти кадр у чергу: {error}")
        segment_best = None

    try:
        span = max(1, end_frame - start_frame)

        if use_seek:
            # вирівнюю перший кандидат по загальній сітці, щоб шматки не перекривались
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
                if manual_rotation:
                    frame = _apply_manual_rotation(frame, manual_rotation)
                gemini_frame = resize_frame(frame, max_width=GEMINI_FRAME_WIDTH)
            else:
                gemini_frame = _resize_then_rotate(frame, manual_rotation, GEMINI_FRAME_WIDTH)
            gray = cv2.cvtColor(gemini_frame, cv2.COLOR_BGR2GRAY)

            if segment_index != current_segment_index:
                flush_segment_best()
                if len(frames) >= quota:
                    break
                current_segment_index = segment_index

            # рух відносно попереднього кандидата, а не відібраного кадру
            motion_percent = 0.0 if prev_candidate_gray is None else _motion_percent(prev_candidate_gray, gray)
            prev_candidate_gray = gray

            baseline = statistics.median(motion_baseline) if motion_baseline else 0.0
            score = motion_percent - baseline
            motion_baseline.append(motion_percent)

            if segment_best is None or score > segment_best["score"]:
                # копі() треба, бо опенсв перевикористовує буфер
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
    # паралельна нарізка: відео ділю на шматки по межах відрізків,
    # кожен шматок читає окремий процес (в потоках гіл заважав би)
    # через межу процесів колбеки і спільні списки напряму не передати, тому:
    # cancel_event, прогрес і лічильники йдуть через менеджер,
    # а готові кадри в чергу. колбек on_frame_ready викликаю вже тут
    bounds = [round(i * max_frames / workers) for i in range(workers + 1)]
    jobs = []
    for i in range(workers):
        seg_start, seg_end = bounds[i], bounds[i + 1]
        start_frame = 0 if i == 0 else int(math.ceil(seg_start * segment_sec * fps))
        end_frame = total_frame_count if i == workers - 1 else int(math.ceil(seg_end * segment_sec * fps))
        jobs.append((start_frame, end_frame, seg_start, seg_end))

    logger.debug(f"[diag] паралельна нарізка: {workers} процесів, use_seek={use_seek}")

    parallel_start_ts = time.time()
    with multiprocessing.Manager() as manager:
        progress = manager.list([0.0] * workers)
        frame_counts = manager.list([0] * workers)
        cancel_event = manager.Event()
        frame_queue = manager.Queue() if on_frame_ready is not None else None

        def _drain_frame_queue():
            # забираю готові кадри з черги і віддаю в on_frame_ready
            if frame_queue is None:
                return
            while True:
                try:
                    frame_dict = frame_queue.get_nowait()
                except Exception:  # noqa: BLE001 - черга порожня
                    break
                _notify_frame_ready(on_frame_ready, frame_dict, 0)

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
    # нарізаю відео на кадри для джеміні
    #
    # кроки, щоб не забути:
    # 1. спершу пробую швидкий шлях (відео_фаст, через ключові кадри)
    # 2. якщо не підійшов, йду старим шляхом:
    #    точна тривалість і фпс (з перевіркою на баг),
    #    кандидати рідко, відбір по рухові,
    #    сік або послідовне читання залежно від швидкості стрибків,
    #    для довгих відео паралельна нарізка в кілька процесів
    #
    # progress_callback(fraction) - прогрес 0..1
    # is_cancelled() - True якщо треба зупинитись
    # full_res_dir - тека для повних кадрів (потім скріншоти з неї)
    # on_frame_ready(frame, index) - викликається коли кадр готовий (стрімінг в джеміні)
    # frame_count_callback(count) - скільки кадрів вже є
    # allow_hw - чи дозволено апаратне декодування
    # allow_fast - чи дозволено швидкий шлях
    if allow_fast:
        try:
            from video_fast import extract_frames_fast, FastPathUnavailable
        except Exception as error:  # noqa: BLE001 - модуля немає, йду старим шляхом
            logger.debug(f"[diag] швидкий шлях недоступний: {error}")
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
        interval_sec *= HEAVY_CODEC_INTERVAL_MULTIPLIER

    interval_frames = max(1, round(interval_sec * fps))
    segment_sec = (duration_sec / max_frames) if duration_sec else None

    # стрибати чи читати підряд: за замовчуванням стрибаю (не на андроїд)
    use_seek = not _IS_ANDROID and total_frame_count > 0

    decode_cache_key = _resolve_fps_cache_key(video_path)
    cached_measurements = (
        _decode_strategy_cache.get(decode_cache_key) if decode_cache_key is not None else None
    )

    # перевіряю чи стрибки на цьому файлі справді дешеві
    measured_seek_cost = None
    seq_fps = None
    if cached_measurements is not None:
        measured_seek_cost, seq_fps, _cached_use_hw = cached_measurements
        logger.debug("[diag] заміри взяті з кешу, пробу пропущено")
    elif use_seek:
        measured_seek_cost = _probe_seek_cost(video, total_frame_count)
        if measured_seek_cost is not None and measured_seek_cost > _SEEK_SLOW_THRESHOLD_SEC:
            seq_fps = _measure_sequential_fps(video)

    if use_seek and measured_seek_cost is not None and measured_seek_cost > _SEEK_SLOW_THRESHOLD_SEC:
        # стрибок повільний, порівнюю що дешевше загалом:
        # стрибати на кожного кандидата чи читати весь файл підряд
        candidate_count = math.ceil(total_frame_count / interval_frames)
        seek_total_est = candidate_count * measured_seek_cost
        seq_total_est = (total_frame_count / seq_fps) if seq_fps else None
        if seq_total_est is not None and seek_total_est < seq_total_est * 0.8:
            logger.debug(f"[diag] лишаю стрибки: {seek_total_est:.0f}с проти {seq_total_est:.0f}с")
        else:
            est_text = f"{seq_total_est:.0f}с" if seq_total_est is not None else "?"
            logger.debug(f"[diag] стрибки повільні ({seek_total_est:.0f}с проти {est_text}) - читаю підряд")
            use_seek = False

    logger.debug(
        f"[diag] extract_frames: fps={fps:.2f} тривалість={duration_sec:.1f}с "
        f"кадрів={total_frame_count} interval_frames={interval_frames} "
        f"segment_sec={segment_sec} use_seek={use_seek}"
    )

    # апаратне декодування тільки для послідовного читання (не для стрибків)
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
        _decode_strategy_cache[decode_cache_key] = (measured_seek_cost, seq_fps, use_hw)

    full_frame_pool = ThreadPoolExecutor(max_workers=_FULL_FRAME_SAVE_WORKERS) if full_res_dir else None

    # паралельна нарізка для довгих відео (і з сіком, і без)
    # не можна тільки при апаратному декодуванні: перезапуск з нуля дав би дублі
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
            if full_frame_pool is not None:
                full_frame_pool.shutdown(wait=True)

    frames = []
    last_kept_gray = None
    prev_candidate_gray = None
    motion_baseline = deque(maxlen=MOTION_BASELINE_WINDOW)
    current_segment_index = -1
    segment_best = None

    def flush_segment_best():
        # забираю найкращий кадр відрізка в підсумок
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
                if frame_count_callback:
                    frame_count_callback(len(frames))
        segment_best = None

    last_reported_percent = -1
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
            break

        process_start_ts = time.time()
        timestamp_sec = frame_index / fps

        if progress_callback and total_frame_count:
            percent = int(frame_index / total_frame_count * 100)
            if percent != last_reported_percent:
                progress_callback(min(percent, 100) / 100)
                last_reported_percent = percent

        if full_res_dir:
            if manual_rotation:
                frame = _apply_manual_rotation(frame, manual_rotation)
            gemini_frame = resize_frame(frame, max_width=GEMINI_FRAME_WIDTH)
        else:
            gemini_frame = _resize_then_rotate(frame, manual_rotation, GEMINI_FRAME_WIDTH)
        gray = cv2.cvtColor(gemini_frame, cv2.COLOR_BGR2GRAY)

        try:
            if segment_sec:
                # мін(...) це захист від похибки округлення на межі останнього відрізка
                segment_index = min(int(timestamp_sec // segment_sec), max_frames - 1)
                if segment_index != current_segment_index:
                    flush_segment_best()
                    if len(frames) >= max_frames:
                        break
                    current_segment_index = segment_index

                motion_percent = 0.0 if prev_candidate_gray is None else _motion_percent(prev_candidate_gray, gray)
                prev_candidate_gray = gray

                baseline = statistics.median(motion_baseline) if motion_baseline else 0.0
                score = motion_percent - baseline
                motion_baseline.append(motion_percent)

                if segment_best is None or score > segment_best["score"]:
                    segment_best = {
                        "score": score,
                        "timestamp_sec": timestamp_sec,
                        "frame": gemini_frame.copy(),
                        "full": frame.copy() if full_res_dir else None,
                        "gray": gray,
                    }
            else:
                # тривалість невідома: беру кадри з рухом, без відрізків
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
            candidate_process_sec += time.time() - process_start_ts

    logger.debug(
        f"[diag] кандидатів={candidate_count} "
        f"дістати={candidate_fetch_sec:.1f}с обробити={candidate_process_sec:.1f}с"
    )

    # апаратний декодер обірвався раніше кінця, результат ненадійний, роблю програмно
    if (use_hw and total_frame_count
            and iter_state["decoded"] < total_frame_count * 0.9
            and not (is_cancelled is not None and is_cancelled())
            and len(frames) < max_frames):
        logger.warning("[diag] апаратне декодування обірвалось раніше часу - повторюю програмно")
        video.release()
        if full_frame_pool is not None:
            full_frame_pool.shutdown(wait=False)
        return extract_frames(video_path, max_frames, min_interval_sec, progress_callback,
                              is_cancelled, full_res_dir, allow_hw=False, allow_fast=False)

    flush_segment_best()

    if progress_callback:
        progress_callback(1.0)

    video.release()
    if full_frame_pool is not None:
        full_frame_pool.shutdown(wait=True)
    return frames


def _notify_frame_ready(on_frame_ready, frame_dict, index):
    # викликаю колбек, помилка в ньому не має зупиняти нарізку
    if on_frame_ready is None:
        return
    try:
        on_frame_ready(frame_dict, index)
    except Exception as error:  # noqa: BLE001
        logger.warning(f"[diag] on_frame_ready впав, продовжую нарізку: {error}")


def grab_screenshot(video_path, timestamp_sec, save_path):
    # один кадр за часом, в оригінальній якості, для показу в інтерфейсі
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
    # один процес: свій декодер, свій шматок цілей (відсортованих за часом)
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
    # не вдалось записати частину скріншотів (найчастіше нема місця на диску)
    # saved це шляхи що встигли записатись, їх не можна втрачати

    def __init__(self, saved, done_targets, total_targets):
        super().__init__(28, "No space left on device")
        self.saved = saved
        self.done_targets = done_targets
        self.total_targets = total_targets


def grab_screenshots_batch(video_path, timestamp_and_path_list, progress_callback=None):
    # дістаю кілька кадрів за один прохід по відео
    # timestamp_and_path_list це список пар (час_сек, шлях_файлу)
    # повертаю сет успішно збережених шляхів
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
        # читаю підряд, без стрибків
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
                    disk_full_error = error
            if progress_callback and total_targets:
                progress_callback((i + 1) / total_targets)
        if disk_full_error is not None:
            raise ScreenshotDiskFullError(saved, total_targets, total_targets) from disk_full_error
    else:
        workers = _decode_worker_count(total_targets, min_items_per_worker=8)
        if workers > 1:
            # шматки цілей роздаю процесам, кожен зі своїм декодером
            video.release()
            chunk_count = min(total_targets, workers * 8)
            chunk_size = int(math.ceil(total_targets / chunk_count))
            chunks = [targets[i:i + chunk_size] for i in range(0, total_targets, chunk_size)]
            done_targets = 0
            disk_full_error = None
            with _single_threaded_ffmpeg_decode(), ProcessPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_grab_screenshot_chunk, video_path, manual_rotation, chunk) for chunk in chunks]
                for future in as_completed(futures):
                    try:
                        chunk_saved, chunk_len = future.result()
                    except OSError as error:
                        # одна помилка диска не має зупиняти решту шматків
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
        progress_callback(1.0)
    if disk_full_error is not None:
        raise ScreenshotDiskFullError(saved, total_targets, total_targets) from disk_full_error
    return saved

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
