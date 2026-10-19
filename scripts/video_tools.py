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
video_tools.py

Нарізка відео на кадри для Gemini і збереження скріншотів знайдених моментів.

Як це працює:
1. Беремо з відео кандидатів-кадрів (приблизно раз на кілька секунд).
2. Відео ділимо на однакові відрізки часу. З кожного відрізка лишаємо
   один кадр з найбільшим рухом, тож відео покрите рівномірно.
3. Довге відео ріжемо на шматки і читаємо паралельно, кожен у своєму процесі.
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
import statistics
import struct
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed, wait

import cv2

logger = logging.getLogger(__name__)

# На Android сикання в OpenCV ненадійне, тому там читаємо відео підряд.
IS_ANDROID = "ANDROID_ARGUMENT" in os.environ

GEMINI_FRAME_WIDTH = 1024                       # ширина кадру для Gemini
_GEMINI_JPEG_ENCODE_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 85]
MOTION_THRESHOLD_PERCENT = 2.0                  # поріг руху (режим без тривалості)
MOTION_BASELINE_WINDOW = 20                     # скільки кандидатів беремо для "фону"
CANDIDATE_OVERSAMPLE = 3                        # кандидатів у 3 рази більше за кадри
PARALLEL_MIN_DURATION_SEC = 120.0               # коротші відео ріжемо в один потік
MAX_PARALLEL_WORKERS = 8
SEEK_SLOW_THRESHOLD_SEC = 0.15                  # сикання дорожче за це - вважаємо повільним
CV2_PROBE_TIMEOUT_SEC = 8.0                     # ліміт часу на перевірку файлу


# ---------------------------------------------------------------------------
# Допоміжне: запуск з таймаутом і відкриття відео
# ---------------------------------------------------------------------------

def run_with_timeout(func, timeout_sec=CV2_PROBE_TIMEOUT_SEC):
    """Запускає func у фоновому потоці і чекає не більше timeout_sec.
    Повертає (результат, чи_вийшов_час). Якщо час вийшов - результат None.
    Потік, що завис, просто лишається фоновим (його не можна вбити)."""
    box = {}

    def target():
        try:
            box["value"] = func()
        except Exception as error:  # noqa: BLE001 - передаємо помилку назад
            box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout_sec)

    if thread.is_alive():
        return None, True
    if "error" in box:
        raise box["error"]
    return box.get("value"), False


def _open_capture(video_path, prefer_ffmpeg=False):
    """Відкриває відео. Вимикаємо авто-поворот OpenCV, бо кут крутимо самі."""
    video = None
    if prefer_ffmpeg:
        # Просимо саме FFmpeg, бо тільки він слухає налаштування потоків
        video = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)
        if not video.isOpened():
            video.release()
            video = None
    if video is None:
        video = cv2.VideoCapture(video_path)
    video.set(cv2.CAP_PROP_ORIENTATION_AUTO, 0)
    return video


_FFMPEG_OPTIONS_ENV = "OPENCV_FFMPEG_CAPTURE_OPTIONS"
_ffmpeg_env_lock = threading.Lock()


@contextlib.contextmanager
def _single_thread_decode():
    """Поки працює паралельна нарізка, кожен декодер використовує один потік.
    Інакше кілька декодерів борються за одні й ті самі ядра процесора."""
    with _ffmpeg_env_lock:
        old_value = os.environ.get(_FFMPEG_OPTIONS_ENV)
        os.environ[_FFMPEG_OPTIONS_ENV] = "threads;1"
        try:
            yield
        finally:
            if old_value is None:
                os.environ.pop(_FFMPEG_OPTIONS_ENV, None)
            else:
                os.environ[_FFMPEG_OPTIONS_ENV] = old_value


# ---------------------------------------------------------------------------
# Читаємо метадані MP4 напряму (тривалість і поворот)
# ---------------------------------------------------------------------------
# MP4 складається з "коробок" (box). Кожна має заголовок:
# [4 байти розміру][4 байти назви], далі дані. Коробки можуть містити інші коробки.

def _find_box(f, box_type, start, end):
    """Шукає коробку з назвою box_type серед сусідів у діапазоні [start, end).
    Повертає (початок_даних, довжина_даних) або None."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            return None
        size = int.from_bytes(header[:4], "big")
        name = header[4:8]
        header_len = 8
        if size == 1:                       # дуже велика коробка: розмір іде далі
            size = int.from_bytes(f.read(8), "big")
            header_len = 16
        elif size == 0:                     # коробка тягнеться до кінця файлу
            size = end - pos
        if size < header_len:
            return None
        if name == box_type:
            return pos + header_len, size - header_len
        pos += size
    return None


# Матриця повороту у форматі fixed-point (1.0 = 0x10000).
_ONE = 0x10000
_ANGLE_BY_MATRIX = {
    (_ONE, 0, 0, _ONE): 0,
    (0, _ONE, -_ONE, 0): 90,
    (-_ONE, 0, 0, -_ONE): 180,
    (0, -_ONE, _ONE, 0): 270,
}


def _parse_tkhd(f, start, length):
    """Читає коробку tkhd (заголовок треку).
    Повертає (ширина, висота, a, b, c, d) або None."""
    f.seek(start)
    payload = f.read(length)
    if len(payload) < 4:
        return None
    # Розмір блоку з датами залежить від версії коробки
    time_part = 32 if payload[0] == 1 else 20
    m = 4 + time_part + 16                  # 4 = версія і прапорці
    if len(payload) < m + 44:
        return None
    a = int.from_bytes(payload[m:m + 4], "big", signed=True)
    b = int.from_bytes(payload[m + 4:m + 8], "big", signed=True)
    c = int.from_bytes(payload[m + 12:m + 16], "big", signed=True)
    d = int.from_bytes(payload[m + 16:m + 20], "big", signed=True)
    width = int.from_bytes(payload[m + 36:m + 40], "big")
    height = int.from_bytes(payload[m + 40:m + 44], "big")
    return width, height, a, b, c, d


def read_rotation_from_mp4(video_path):
    """Кут повороту відео-треку (0, 90, 180 або 270). При помилці - 0."""
    try:
        with open(video_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            moov = _find_box(f, b"moov", 0, file_size)
            if moov is None:
                return 0
            pos = moov[0]
            moov_end = moov[0] + moov[1]
            while True:
                trak = _find_box(f, b"trak", pos, moov_end)
                if trak is None:
                    return 0
                trak_start, trak_len = trak
                trak_end = trak_start + trak_len
                tkhd = _find_box(f, b"tkhd", trak_start, trak_end)
                if tkhd is not None:
                    info = _parse_tkhd(f, *tkhd)
                    # Аудіо-трек має ширину й висоту 0 - такий пропускаємо
                    if info is not None and info[0] and info[1]:
                        return _ANGLE_BY_MATRIX.get(info[2:], 0)
                pos = trak_end
    except (OSError, ValueError, struct.error):
        return 0


def read_duration_from_mp4(video_path):
    """Точна тривалість відео в секундах (з коробки mvhd). None, якщо не вдалось."""
    try:
        with open(video_path, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()
            moov = _find_box(f, b"moov", 0, file_size)
            if moov is None:
                return None
            mvhd = _find_box(f, b"mvhd", moov[0], moov[0] + moov[1])
            if mvhd is None:
                return None
            start, length = mvhd
            f.seek(start)
            payload = f.read(length)
            if not payload:
                return None
            if payload[0] == 1:
                timescale = int.from_bytes(payload[20:24], "big")
                duration = int.from_bytes(payload[24:32], "big")
            else:
                timescale = int.from_bytes(payload[12:16], "big")
                duration = int.from_bytes(payload[16:20], "big")
            if timescale <= 0:
                return None
            return duration / timescale
    except (OSError, ValueError, struct.error):
        return None


def _prepare_orientation(video_path):
    """Кут повороту, який треба застосувати до кадру."""
    return read_rotation_from_mp4(video_path)


def _apply_manual_rotation(frame, angle):
    if angle == 90:
        return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    if angle == 180:
        return cv2.rotate(frame, cv2.ROTATE_180)
    if angle == 270:
        return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return frame


# ---------------------------------------------------------------------------
# FPS і кількість кадрів
# ---------------------------------------------------------------------------

def _looks_like_fps_bug(raw_fps, real_fps):
    """Чи різниця fps схожа на баг: рівно у 2-4 рази."""
    if raw_fps <= 0 or real_fps <= 0:
        return False
    ratio = max(raw_fps, real_fps) / min(raw_fps, real_fps)
    k = round(ratio)
    return 2 <= k <= 4 and abs(ratio - k) / k <= 0.05


def _frame_count_is_real(video, total, duration, raw_fps):
    """Перевіряємо, чи кадрів справді стільки, скільки каже OpenCV.
    Дивимось, чи є кадр далі за межею, яку дав би raw_fps."""
    guess = int(duration * raw_fps)
    if guess <= 0 or guess >= total - 1:
        return True
    cost = _probe_seek_cost(video, total)
    if cost is None or cost > SEEK_SLOW_THRESHOLD_SEC:
        return False

    video.set(cv2.CAP_PROP_POS_FRAMES, guess)
    ok1, frame1 = video.read()
    video.set(cv2.CAP_PROP_POS_FRAMES, min(guess + 50, total - 1))
    ok2, frame2 = video.read()
    video.set(cv2.CAP_PROP_POS_FRAMES, 0)
    if not (ok1 and ok2):
        return False

    # Порівнюємо дуже маленькі копії: якщо вони однакові, кадр "прилип"
    a = cv2.resize(cv2.cvtColor(frame1, cv2.COLOR_BGR2GRAY), (32, 32))
    b = cv2.resize(cv2.cvtColor(frame2, cv2.COLOR_BGR2GRAY), (32, 32))
    return cv2.countNonZero(cv2.absdiff(a, b)) > 0


def _resolve_fps(video, video_path):
    """Повертає (fps, тривалість_сек, кількість_кадрів) - перевірені значення."""
    raw_fps = video.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    if not (0 < total < 100_000_000):
        total = 0                           # сміття від OpenCV, наприклад для картинки

    duration = read_duration_from_mp4(video_path)
    if not duration:
        duration = total / raw_fps if total else 0.0
        return raw_fps, duration, total
    if total == 0:
        return raw_fps, duration, 0

    real_fps = total / duration
    if not _looks_like_fps_bug(raw_fps, real_fps):
        return raw_fps, duration, total

    # Підозра на баг. Перевіряємо, хто винен: fps чи кількість кадрів
    if not IS_ANDROID and not _frame_count_is_real(video, total, duration, raw_fps):
        return raw_fps, duration, int(round(duration * raw_fps))
    return real_fps, duration, total


def get_video_duration_sec(video_path):
    """Тривалість відео в секундах. 0.0, якщо невідомо.
    Викликається з головного потоку, тому з таймаутом."""
    def probe():
        video = _open_capture(video_path)
        if not video.isOpened():
            raise ValueError(f"Не вдалось відкрити відео: {video_path}")
        try:
            _, duration, _ = _resolve_fps(video, video_path)
        finally:
            video.release()
        return duration or 0.0

    result, timed_out = run_with_timeout(probe)
    if timed_out:
        logger.warning(f"[diag] перевірка відео зависла: {video_path}")
        return 0.0
    return result


def _probe_seek_cost(video, total):
    """Середній час одного стрибка (сикання) по відео. None, якщо не вийшло."""
    if total <= 0:
        return None
    spots = sorted({min(total - 1, int(total * f)) for f in (0.15, 0.45, 0.75)})
    times = []
    for pos in spots:
        started = time.time()
        video.set(cv2.CAP_PROP_POS_FRAMES, pos)
        ok, _ = video.read()
        elapsed = time.time() - started
        if ok:
            times.append(elapsed)
        if elapsed > SEEK_SLOW_THRESHOLD_SEC * 2:
            break                           # і так ясно, що повільно - далі не міряємо
    video.set(cv2.CAP_PROP_POS_FRAMES, 0)
    return sum(times) / len(times) if times else None


# ---------------------------------------------------------------------------
# Допоміжні функції для кадрів
# ---------------------------------------------------------------------------

def format_time(seconds):
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def resize_frame(frame, max_width=1024):
    height, width = frame.shape[:2]
    if width <= max_width:
        return frame
    scale = max_width / width
    return cv2.resize(frame, (max_width, int(height * scale)))


def _resize_then_rotate(frame, angle, max_width):
    """Спершу зменшуємо, потім повертаємо - так швидше.
    Для 90/270 обмежуємо висоту, бо після повороту ширина стає висотою."""
    if not angle:
        return resize_frame(frame, max_width=max_width)
    height, width = frame.shape[:2]
    limit = height if angle in (90, 270) else width
    if limit > max_width:
        scale = max_width / limit
        frame = cv2.resize(frame, (int(width * scale), int(height * scale)))
    return _apply_manual_rotation(frame, angle)


def _motion_percent(prev_gray, current_gray):
    """Відсоток пікселів, які помітно змінилися між двома кадрами (0..100)."""
    diff = cv2.absdiff(prev_gray, current_gray)
    _, mask = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(mask) / (mask.shape[0] * mask.shape[1]) * 100


def _prepare(frame, rotation, keep_full):
    """Готує кадр: (маленький для Gemini, повний або None)."""
    if keep_full:
        full = _apply_manual_rotation(frame, rotation)
        return resize_frame(full, max_width=GEMINI_FRAME_WIDTH), full
    return _resize_then_rotate(frame, rotation, GEMINI_FRAME_WIDTH), None


def _write_image(path, frame):
    """Записує JPG. Підтримує кирилицю в шляху.
    Помилку диска (OSError) НЕ ковтаємо - її має побачити викликач."""
    ext = os.path.splitext(path)[1] or ".jpg"
    ok, buffer = cv2.imencode(ext, frame)
    if not ok:
        return False
    buffer.tofile(path)
    return True


def _save_full_frame(full_dir, frame):
    """Зберігає повний кадр у кеш. Повертає шлях або None."""
    if not full_dir or frame is None:
        return None
    path = os.path.join(full_dir, f"{uuid.uuid4().hex}.jpg")
    try:
        return path if _write_image(path, frame) else None
    except OSError as error:
        logger.warning(f"[diag] не вдалось зберегти кеш-кадр: {error}")
        return None


def _notify_frame_ready(callback, item, index):
    """Викликає колбек. Помилка в ньому не має зупиняти нарізку."""
    if callback is None:
        return
    try:
        callback(item, index)
    except Exception as error:  # noqa: BLE001
        logger.warning(f"[diag] on_frame_ready впав: {error}")


def _throttled(callback):
    """Обгортка: викликає callback лише коли змінився відсоток."""
    if callback is None:
        return lambda fraction: None
    last = [-1]

    def report(fraction):
        percent = int(fraction * 100)
        if percent != last[0]:
            last[0] = percent
            callback(min(percent, 100) / 100)
    return report


def _worker_count(item_count, per_worker):
    """Скільки процесів запускати. 1 на Android."""
    if IS_ANDROID:
        return 1
    return max(1, min(MAX_PARALLEL_WORKERS, os.cpu_count() or 1, item_count // per_worker))


# ---------------------------------------------------------------------------
# Вибір кадрів: з кожного відрізка часу лишаємо один найкращий
# ---------------------------------------------------------------------------

class FramePicker:
    """Вибирає кадри для Gemini.

    Якщо відома тривалість - ділимо відео на відрізки і з кожного беремо
    кадр з найбільшим рухом відносно фону (медіана останніх кадрів).
    Якщо тривалість невідома - просто беремо кадри, де є рух.
    """

    def __init__(self, segment_sec, max_frames, seg_start, seg_end, full_dir, on_frame):
        self.segment_sec = segment_sec
        self.max_frames = max_frames
        self.seg_start = seg_start              # з якого відрізка беремо (для шматків паралельної нарізки)
        self.seg_end = seg_end                  # до якого відрізка (не включно)
        self.full_dir = full_dir
        self.on_frame = on_frame
        self.quota = (seg_end - seg_start) if segment_sec else max_frames
        self.frames = []
        self.current_segment = -1
        self.best = None                        # найкращий кадр поточного відрізка
        self.prev_gray = None
        self.baseline = deque(maxlen=MOTION_BASELINE_WINDOW)
        self.last_kept_gray = None              # для режиму без тривалості

    def is_full(self):
        return len(self.frames) >= self.quota

    def add(self, timestamp, small, full):
        """Додає кандидата. False - далі читати відео не треба."""
        if self.segment_sec is None:
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            if self.last_kept_gray is None or _motion_percent(self.last_kept_gray, gray) >= MOTION_THRESHOLD_PERCENT:
                self.last_kept_gray = gray
                self._emit(timestamp, small, full)
            return True

        segment = min(int(timestamp // self.segment_sec), self.max_frames - 1)
        if segment < self.seg_start:
            return True                         # це чужий шматок, пропускаємо
        if segment >= self.seg_end:
            return False                        # далі - вже не наш шматок

        if segment != self.current_segment:
            self._flush()                       # попередній відрізок закінчився
            self.current_segment = segment

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        motion = 0.0 if self.prev_gray is None else _motion_percent(self.prev_gray, gray)
        self.prev_gray = gray
        # Рух відносно фону: якщо хвилі рухаються завжди, їхній рух
        # віднімається і лишається тільки справжня подія
        background = statistics.median(self.baseline) if self.baseline else 0.0
        self.baseline.append(motion)
        score = motion - background

        if self.best is None or score > self.best["score"]:
            # copy() потрібен, бо OpenCV може перевикористати буфер
            self.best = {
                "score": score,
                "ts": timestamp,
                "small": small.copy(),
                "full": None if full is None else full.copy(),
            }
        return True

    def _flush(self):
        if self.best is not None and not self.is_full():
            self._emit(self.best["ts"], self.best["small"], self.best["full"])
        self.best = None

    def _emit(self, timestamp, small, full):
        ok, buffer = cv2.imencode(".jpg", small, _GEMINI_JPEG_ENCODE_PARAMS)
        if not ok:
            return
        item = {
            "timestamp_sec": timestamp,
            "jpg_bytes": buffer.tobytes(),
            "full_path": _save_full_frame(self.full_dir, full),
        }
        self.frames.append(item)
        _notify_frame_ready(self.on_frame, item, len(self.frames) - 1)

    def finish(self):
        self._flush()
        return self.frames


def _iter_candidates(video, start, end, interval, use_seek):
    """Віддає (номер_кадру, кадр) для кожного interval-го кадру в [start, end).
    use_seek=True - стрибаємо до кожного кандидата.
    use_seek=False - читаємо всі кадри підряд, але декодуємо тільки кандидатів."""
    first = start + (-start) % interval     # перший кадр сітки, що не раніше start
    if use_seek:
        index = first
        while index < end:
            video.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = video.read()
            if not ok:
                return
            yield index, frame
            index += interval
    else:
        if start > 0:
            video.set(cv2.CAP_PROP_POS_FRAMES, start)
        index = start
        while index < end:
            if not video.grab():
                return
            if index % interval == 0:
                ok, frame = video.retrieve()
                if ok:
                    yield index, frame
            index += 1


def _extract_range(video, fps, start, end, interval, segment_sec, max_frames, seg_start, seg_end,
                   rotation, full_dir, use_seek, should_stop, report, on_frame):
    """Нарізає один шматок відео і повертає список знайдених кадрів."""
    picker = FramePicker(segment_sec, max_frames, seg_start, seg_end, full_dir, on_frame)
    span = max(1, end - start)
    for index, frame in _iter_candidates(video, start, end, interval, use_seek):
        if should_stop():
            break
        report(min(1.0, (index - start) / span))
        small, full = _prepare(frame, rotation, full_dir is not None)
        if not picker.add(index / fps, small, full):
            break
        if picker.is_full():
            break
    return picker.finish()


def _extract_slice(video_path, fps, start, end, interval, segment_sec, max_frames, seg_start, seg_end,
                   rotation, full_dir, use_seek, slice_id, progress, stop, queue):
    """Виконується в окремому процесі: нарізає свій шматок відео."""
    video = _open_capture(video_path, prefer_ffmpeg=True)
    if not video.isOpened():
        progress[slice_id] = 1.0
        return []

    def report(fraction):
        progress[slice_id] = fraction

    try:
        return _extract_range(
            video, fps, start, end, interval, segment_sec, max_frames, seg_start, seg_end,
            rotation, full_dir, use_seek,
            should_stop=lambda: stop.is_set(),
            report=report,
            on_frame=lambda item, index: queue.put(item),   # в основний процес
        )
    finally:
        progress[slice_id] = 1.0
        video.release()


def _drain_queue(queue, handler, index):
    """Бере з черги всі готові кадри і передає їх handler-у. Повертає новий лічильник."""
    while True:
        try:
            item = queue.get_nowait()
        except Exception:  # noqa: BLE001 - черга порожня
            return index
        handler(item, index)
        index += 1


def _extract_parallel(video_path, fps, total, interval, segment_sec, max_frames, rotation, workers,
                      use_seek, full_dir, progress_callback, is_cancelled, on_frame):
    """Ділить відео на шматки по межах відрізків і нарізає їх в окремих процесах."""
    bounds = [round(i * max_frames / workers) for i in range(workers + 1)]
    jobs = []
    for i in range(workers):
        seg_start, seg_end = bounds[i], bounds[i + 1]
        start = 0 if i == 0 else math.ceil(seg_start * segment_sec * fps)
        end = total if i == workers - 1 else math.ceil(seg_end * segment_sec * fps)
        jobs.append((start, end, seg_start, seg_end))

    drained = 0
    with multiprocessing.Manager() as manager:
        progress = manager.list([0.0] * workers)
        stop = manager.Event()
        queue = manager.Queue()
        report = _throttled(progress_callback)

        with _single_thread_decode(), ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_extract_slice, video_path, fps, s, e, interval, segment_sec, max_frames,
                            ss, se, rotation, full_dir, use_seek, i, progress, stop, queue)
                for i, (s, e, ss, se) in enumerate(jobs)
            ]
            pending = set(futures)
            while pending:
                if is_cancelled and is_cancelled():
                    stop.set()
                _done, pending = wait(pending, timeout=0.25)
                drained = _drain_queue(queue, on_frame, drained)
                report(sum(progress) / workers)
            drained = _drain_queue(queue, on_frame, drained)
            parts = [future.result() for future in futures]

    frames = sorted((f for part in parts for f in part), key=lambda item: item["timestamp_sec"])
    return frames[:max_frames]


def _try_fast_path(video_path, max_frames, **kwargs):
    """Пробує швидкий шлях (video_fast). None - якщо не підійшов."""
    try:
        from video_fast import extract_frames_fast, FastPathUnavailable
    except Exception as error:  # noqa: BLE001
        logger.debug(f"[fast] модуль недоступний: {error}")
        return None
    try:
        return extract_frames_fast(video_path, max_frames=max_frames, **kwargs)
    except FastPathUnavailable as reason:
        logger.debug(f"[fast] не підходить: {reason}")
        return None


def extract_frames(video_path, max_frames=300, min_interval_sec=0.5, progress_callback=None,
                   is_cancelled=None, full_res_dir=None, on_frame_ready=None, frame_count_callback=None):
    """Нарізає відео на кадри для Gemini.

    progress_callback(fraction)   - прогрес 0..1
    is_cancelled()                - True, якщо треба зупинитись
    full_res_dir                  - тека, куди зберегти повні кадри (для скріншотів)
    on_frame_ready(frame, index)  - викликається одразу, коли кадр готовий
    frame_count_callback(count)   - скільки кадрів уже є
    """
    fast = _try_fast_path(
        video_path, max_frames,
        progress_callback=progress_callback, is_cancelled=is_cancelled,
        full_res_dir=full_res_dir, on_frame_ready=on_frame_ready,
        frame_count_callback=frame_count_callback,
    )
    if fast is not None:
        return fast

    video = _open_capture(video_path)
    if not video.isOpened():
        raise ValueError(f"Не вдалось відкрити відео: {video_path}")

    try:
        rotation = _prepare_orientation(video_path)
        fps, duration, total = _resolve_fps(video, video_path)

        # Кожен кандидат - через стільки кадрів
        interval_sec = min_interval_sec
        if duration:
            interval_sec = max(min_interval_sec, duration / (max_frames * CANDIDATE_OVERSAMPLE))
        interval = max(1, round(interval_sec * fps))
        segment_sec = duration / max_frames if duration else None

        # Стрибати чи читати підряд: якщо сикання повільне - читаємо підряд
        use_seek = False
        if not IS_ANDROID and total > 0:
            cost = _probe_seek_cost(video, total)
            use_seek = cost is None or cost <= SEEK_SLOW_THRESHOLD_SEC

        def on_frame(item, index):
            if frame_count_callback:
                frame_count_callback(index + 1)
            _notify_frame_ready(on_frame_ready, item, index)

        workers = _worker_count(max_frames, per_worker=50)
        if segment_sec and total > 0 and workers > 1 and duration >= PARALLEL_MIN_DURATION_SEC:
            video.release()
            return _extract_parallel(
                video_path, fps, total, interval, segment_sec, max_frames, rotation, workers,
                use_seek, full_res_dir, progress_callback, is_cancelled, on_frame,
            )

        frames = _extract_range(
            video, fps, 0, total or (1 << 62), interval, segment_sec, max_frames, 0, max_frames,
            rotation, full_res_dir, use_seek,
            should_stop=is_cancelled or (lambda: False),
            report=_throttled(progress_callback),
            on_frame=on_frame,
        )
        if progress_callback:
            progress_callback(1.0)
        return frames
    finally:
        video.release()


# ---------------------------------------------------------------------------
# Скріншоти знайдених моментів (повний кадр з відео)
# ---------------------------------------------------------------------------

class ScreenshotDiskFullError(OSError):
    """Частину скріншотів не вдалось записати (найчастіше - немає місця на диску).
    У saved лежать шляхи тих, що записались."""

    def __init__(self, saved, done_targets, total_targets):
        super().__init__(28, "No space left on device")
        self.saved = saved
        self.done_targets = done_targets
        self.total_targets = total_targets


def _save_targets(video, targets, rotation, on_done=None):
    """Дістає кадри за номерами і записує у файли.
    Повертає (set збережених шляхів, чи була помилка запису)."""
    saved = set()
    write_error = False
    position = 0
    for i, (frame_index, path) in enumerate(targets):
        if IS_ANDROID:
            # Без сикання: читаємо підряд до потрібного кадру
            while position < frame_index and video.grab():
                position += 1
            ok, frame = video.read()
            position += 1
        else:
            video.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = video.read()

        if ok:
            if rotation:
                frame = _apply_manual_rotation(frame, rotation)
            try:
                if _write_image(path, frame):
                    saved.add(path)
            except OSError:
                write_error = True
        if on_done:
            on_done(i + 1)
    return saved, write_error


def _grab_chunk(video_path, rotation, chunk):
    """Виконується в окремому процесі: обробляє свій шматок цілей."""
    video = _open_capture(video_path, prefer_ffmpeg=True)
    try:
        saved, write_error = _save_targets(video, chunk, rotation)
    finally:
        video.release()
    return saved, write_error, len(chunk)


def grab_screenshots_batch(video_path, timestamp_and_path_list, progress_callback=None):
    """Зберігає кілька скріншотів за один прохід.

    timestamp_and_path_list - список пар (час_сек, шлях_файлу).
    Повертає set успішно збережених шляхів.
    Якщо не вдалось записати частину - кидає ScreenshotDiskFullError,
    але в ній є вже збережені шляхи."""
    targets_raw = list(timestamp_and_path_list)
    if not targets_raw:
        if progress_callback:
            progress_callback(1.0)
        return set()

    video = _open_capture(video_path)
    try:
        rotation = _prepare_orientation(video_path)
        fps, _, _ = _resolve_fps(video, video_path)
        targets = sorted(
            ((max(0, int(round(ts * fps))), path) for ts, path in targets_raw),
            key=lambda item: item[0],
        )
        total = len(targets)
        workers = _worker_count(total, per_worker=8)

        if workers == 1:
            on_done = (lambda n: progress_callback(n / total)) if progress_callback else None
            saved, write_error = _save_targets(video, targets, rotation, on_done)
            if write_error:
                raise ScreenshotDiskFullError(saved, total, total)
            return saved
    finally:
        video.release()

    # Паралельно: шматки цілей обробляють окремі процеси
    chunk_size = math.ceil(total / min(total, workers * 8))
    chunks = [targets[i:i + chunk_size] for i in range(0, total, chunk_size)]
    saved_all = set()
    write_error = False
    done = 0
    with _single_thread_decode(), ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_grab_chunk, video_path, rotation, chunk) for chunk in chunks]
        for future in as_completed(futures):
            saved, chunk_error, chunk_len = future.result()
            saved_all |= saved
            write_error = write_error or chunk_error
            done += chunk_len
            if progress_callback:
                progress_callback(done / total)

    if write_error:
        raise ScreenshotDiskFullError(saved_all, done, total)
    return saved_all

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
