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
video_fast.py

Швидка нарізка кадрів через PyAV. Замість декодування всього відео
беремо кадри з ключових кадрів (I-frame), найближчих до потрібного часу.

Коли використовується: тільки якщо встановлено PyAV, відео довше
90 секунд і ключові кадри не рідші за довжину відрізка. Інакше кидаємо
FastPathUnavailable, і video_tools.extract_frames() працює старим шляхом.

Функція extract_frames_fast() має ті самі колбеки і той самий формат
результату, що й extract_frames().
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import logging
import math
import os
import queue
import statistics
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait

import cv2

try:
    import av
    AV_AVAILABLE = True
except Exception:  # noqa: BLE001 - PyAV немає (наприклад, на Android), беремо старий шлях
    av = None
    AV_AVAILABLE = False

import video_tools as vt

logger = logging.getLogger(__name__)

# Короткі відео беремо старим шляхом: там він і так швидкий і точніший.
FAST_MIN_DURATION_SEC = 90.0
# Скільки точок перевіряємо в одному відрізку, щоб вибрати найрухливішу.
FAST_SEEKS_PER_SEGMENT = 2
# Після ключового кадру декодуємо ще кілька кадрів, щоб ближче підійти до цілі.
FAST_FORWARD_FRAMES = 12
# Вікно (с) і точки, де міряємо, як часто в файлі стоять ключові кадри.
_GOP_PROBE_WINDOW_SEC = 40.0
_GOP_PROBE_POINTS = (0.2, 0.5, 0.8)
# Ширина картинки для порівняння руху (менша означає швидше).
_MOTION_WIDTH = 320
# Максимум паралельних потоків декодування.
FAST_MAX_WORKERS = 4
# Якщо seek-ів мало, паралельний режим не окупиться.
_FAST_PARALLEL_MIN_SEEKS = 120


class FastPathUnavailable(Exception):
    """Швидкий шлях не підходить, тож треба брати старий."""


def _open_video(video_path, single_thread):
    container = av.open(video_path)
    stream = container.streams.video[0]
    if single_thread:
        stream.codec_context.thread_count = 1
    else:
        stream.thread_type = "AUTO"
    return container, stream


def _duration_sec(container, stream, video_path):
    try:
        if stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)
        if container.duration:
            return container.duration / av.time_base
    except Exception:  # noqa: BLE001
        pass
    try:
        return float(vt.read_duration_from_mp4(video_path) or 0)
    except Exception:  # noqa: BLE001
        return 0.0


def _estimate_gop_sec(container, stream, duration_sec):
    """
    Дивимось тільки пакети (без декодування) і міряємо відстань між
    ключовими кадрами. Повертаємо медіану в секундах або None, якщо
    ключових кадрів майже немає.
    """
    time_base = stream.time_base
    keyframe_times = []
    for fraction in _GOP_PROBE_POINTS:
        start = duration_sec * fraction
        try:
            container.seek(int(start / time_base), stream=stream, backward=True)
            for packet in container.demux(stream):
                if packet.pts is None:
                    continue
                t = float(packet.pts * time_base)
                if t > start + _GOP_PROBE_WINDOW_SEC:
                    break
                if packet.is_keyframe:
                    keyframe_times.append(t)
        except Exception:  # noqa: BLE001
            continue
    keyframe_times = sorted(set(round(t, 3) for t in keyframe_times))
    gaps = [b - a for a, b in zip(keyframe_times, keyframe_times[1:]) if b - a > 0.05]
    if not gaps:
        return None
    return statistics.median(gaps)


def _grab_near(container, stream, target_sec, forward_frames):
    """
    Переходимо до ключового кадру перед target_sec, декодуємо його і ще
    кілька кадрів. Беремо останній кадр, що не пізніший за ціль.
    """
    time_base = stream.time_base
    container.seek(int(target_sec / time_base), stream=stream, backward=True, any_frame=False)
    chosen = None
    chosen_sec = None
    decoded = 0
    for frame in container.decode(stream):
        if frame.pts is None:
            continue
        pts_sec = float(frame.pts * time_base)
        if chosen is None:
            chosen, chosen_sec = frame, pts_sec
        elif pts_sec <= target_sec:
            chosen, chosen_sec = frame, pts_sec
        else:
            break
        decoded += 1
        if decoded > forward_frames:
            break
    return chosen, chosen_sec


def _frame_to_arrays(frame, rotation, full_dir):
    """
    Перетворюємо кадр PyAV на маленький BGR для Gemini і, якщо треба,
    на повний розмір для кешу. Зменшуємо ще до переходу в BGR, так швидше.
    """
    if full_dir:
        full = frame.to_ndarray(format="bgr24")
        if rotation:
            full = vt._apply_manual_rotation(full, rotation)
        return vt.resize_frame(full, max_width=vt.GEMINI_FRAME_WIDTH), full

    width, height = frame.width, frame.height
    limit_dim = height if rotation in (90, 270) else width
    if limit_dim > vt.GEMINI_FRAME_WIDTH:
        scale = vt.GEMINI_FRAME_WIDTH / limit_dim
        small = frame.to_ndarray(width=max(2, int(width * scale)),
                                 height=max(2, int(height * scale)), format="bgr24")
    else:
        small = frame.to_ndarray(format="bgr24")
    if rotation:
        small = vt._apply_manual_rotation(small, rotation)
    return small, None


def _motion_gray(small_bgr):
    gray = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    if width > _MOTION_WIDTH:
        gray = cv2.resize(gray, (_MOTION_WIDTH, max(1, int(height * _MOTION_WIDTH / width))),
                          interpolation=cv2.INTER_AREA)
    return gray


def _run_slice(video_path, seg_start, seg_end, segment_sec, per_segment, rotation, full_dir,
               single_thread, cancel_event, frame_queue, stats):
    """
    Обробляє свій набір відрізків у власному VideoCapture. Готові кадри
    кладе в чергу і повертає списком.
    """
    container, stream = _open_video(video_path, single_thread)
    frames = []
    seen_pts = set()
    prev_gray = None
    baseline = deque(maxlen=vt.MOTION_BASELINE_WINDOW)
    try:
        for segment_index in range(seg_start, seg_end):
            if cancel_event.is_set():
                break
            best = None
            # Беремо кілька кадрів у відрізку і вибираємо найрухливіший
            for j in range(per_segment):
                if cancel_event.is_set():
                    break
                target = segment_index * segment_sec + (j + 0.5) * segment_sec / per_segment
                try:
                    frame, pts_sec = _grab_near(container, stream, target, FAST_FORWARD_FRAMES)
                except Exception as error:  # noqa: BLE001
                    logger.debug(f"[fast] сикання на {target:.1f}с не вдалось: {error}")
                    continue
                if frame is None:
                    continue
                key = round(pts_sec, 3)
                if key in seen_pts:
                    continue
                seen_pts.add(key)

                small, full = _frame_to_arrays(frame, rotation, full_dir)
                gray = _motion_gray(small)
                motion = 0.0 if prev_gray is None else vt._motion_percent(prev_gray, gray)
                prev_gray = gray
                base = statistics.median(baseline) if baseline else 0.0
                # Рух мінус фон: хвилі дають малий бал, а справжній рух - великий
                score = motion - base
                baseline.append(motion)
                stats["candidates"] += 1
                if best is None or score > best["score"]:
                    best = {"score": score, "timestamp_sec": pts_sec, "small": small, "full": full}

            if best is not None:
                ok, buffer = cv2.imencode(".jpg", best["small"], vt._GEMINI_JPEG_ENCODE_PARAMS)
                if ok:
                    item = {
                        "timestamp_sec": best["timestamp_sec"],
                        "jpg_bytes": buffer.tobytes(),
                        "full_path": vt._save_full_frame(full_dir, best["full"]),
                    }
                    frames.append(item)
                    if frame_queue is not None:
                        frame_queue.put(item)
            # Лічильник для прогресу (GIL робить таке оновлення безпечним)
            stats["segments_done"] += 1
    finally:
        container.close()
    return frames


def extract_frames_fast(video_path, max_frames=300, progress_callback=None, is_cancelled=None,
                        full_res_dir=None, on_frame_ready=None, frame_count_callback=None,
                        workers=None):
    """
    Те саме, що vt.extract_frames(), але через ключові кадри.
    Кидає FastPathUnavailable до того, як почне віддавати кадри.
    """
    # Можна вимкнути цей шлях через змінну оточення
    if os.environ.get("VIDEO_FAST_DISABLE") == "1":
        raise FastPathUnavailable("вимкнено через VIDEO_FAST_DISABLE")
    if not AV_AVAILABLE:
        raise FastPathUnavailable("PyAV не встановлено")

    try:
        container, stream = _open_video(video_path, single_thread=False)
    except Exception as error:  # noqa: BLE001
        raise FastPathUnavailable(f"PyAV не відкрив файл: {error}")
    try:
        duration = _duration_sec(container, stream, video_path)
        if duration < FAST_MIN_DURATION_SEC:
            raise FastPathUnavailable(f"коротке відео ({duration:.0f}с) - старий шлях точніший")
        segment_sec = duration / max_frames
        gop_sec = _estimate_gop_sec(container, stream, duration)
        if gop_sec is None or gop_sec > segment_sec:
            raise FastPathUnavailable(
                f"GOP={'?' if gop_sec is None else f'{gop_sec:.1f}с'} довший за сегмент "
                f"{segment_sec:.1f}с - keyframe-вибірка дала б дірки")
        # Швидкий тест: чи працює перехід і декодування на цьому файлі
        probe_frame, _ = _grab_near(container, stream, duration * 0.5, 0)
        if probe_frame is None:
            raise FastPathUnavailable("тестове сикання не повернуло кадр")
    finally:
        container.close()

    rotation = vt._prepare_orientation(video_path)
    per_segment = FAST_SEEKS_PER_SEGMENT
    total_seeks = max_frames * per_segment
    if workers is None:
        # Для малої роботи досить одного потоку, інакше беремо кілька
        cpu = os.cpu_count() or 1
        workers = 1 if total_seeks < _FAST_PARALLEL_MIN_SEEKS else max(1, min(FAST_MAX_WORKERS, cpu - 1 if cpu > 2 else cpu))
    workers = max(1, min(workers, max_frames))
    single_thread = workers > 1

    # Межі відрізків для кожного потоку
    bounds = [round(i * max_frames / workers) for i in range(workers + 1)]
    logger.debug(f"[fast] keyframe-нарізка: тривалість={duration:.0f}с сегмент={segment_sec:.1f}с "
                 f"GOP≈{gop_sec:.1f}с seek-ів={total_seeks} воркерів={workers}")

    start_ts = time.time()
    cancel_event = threading.Event()
    frame_queue = queue.Queue()
    stats = {"candidates": 0, "segments_done": 0}
    emitted = 0

    def drain():
        nonlocal emitted
        while True:
            try:
                item = frame_queue.get_nowait()
            except queue.Empty:
                return
            vt._notify_frame_ready(on_frame_ready, item, emitted)
            emitted += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_run_slice, video_path, bounds[i], bounds[i + 1], segment_sec, per_segment,
                        rotation, full_res_dir, single_thread, cancel_event, frame_queue, stats)
            for i in range(workers) if bounds[i + 1] > bounds[i]
        ]
        pending = set(futures)
        last_percent, last_count = -1, -1
        while pending:
            if is_cancelled is not None and is_cancelled():
                cancel_event.set()
            _done, pending = wait(pending, timeout=0.2)
            drain()
            if progress_callback:
                percent = int(stats["segments_done"] / max_frames * 100)
                if percent != last_percent:
                    progress_callback(min(percent, 100) / 100)
                    last_percent = percent
            if frame_count_callback and emitted != last_count:
                frame_count_callback(emitted)
                last_count = emitted
        drain()
        try:
            parts = [future.result() for future in futures]
        except Exception as error:  # noqa: BLE001
            if emitted == 0:
                raise FastPathUnavailable(f"воркер упав до першого кадру: {error}")
            raise

    frames = sorted((f for part in parts for f in part), key=lambda item: item["timestamp_sec"])
    # Один і той самий ключовий кадр міг потрапити у два сусідні відрізки, прибираємо дублікати
    unique, last_ts = [], None
    for item in frames:
        if last_ts is not None and abs(item["timestamp_sec"] - last_ts) < 1e-3:
            continue
        unique.append(item)
        last_ts = item["timestamp_sec"]
    unique = unique[:max_frames]

    logger.info(f"[fast] keyframe-нарізка: {time.time() - start_ts:.1f}с, кадрів: {len(unique)} "
                f"з {max_frames} сегментів, кандидатів: {stats['candidates']}, воркерів: {workers}")
    if progress_callback:
        progress_callback(1.0)
    if frame_count_callback:
        frame_count_callback(len(unique))
    return unique

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
