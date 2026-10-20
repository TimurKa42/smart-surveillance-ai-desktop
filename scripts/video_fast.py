# =============================================================================
# LookBack
# © 2026 Тимур Каленик (Timur Kalenik Corporation) <timurkalenik950@gmail.com>
# автор: Тимур Каленик. усі права захищено.
#
# проєкт зроблений для конкурсу МАН. код відкритий щоб журі могло подивитись
# і оцінити. копіювати, поширювати, міняти і використовувати код (весь або
# частинами) без письмової згоди автора і без вказання авторства не можна.
# повні умови в файлі LICENSE, не забути його покласти поруч
# =============================================================================

# відео_фаст, швидка нарізка кадрів через піайв
# замість того щоб декодувати все відео, беру кадри з ключових кадрів
# (ай-фрейм) які найближче до потрібного часу
#
# коли працює: тільки якщо стоїть піайв, відео довше 90 секунд
# і ключові кадри не рідші ніж довжина відрізка
# якщо щось не так, кидаю FastPathUnavailable і відео_тулс
# робить все старим шляхом
#
# ф-ція extract_frames_fast має ті самі колбеки і той самий результат
# що і extract_frames, щоб не забути і не переплутати

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

# пробую взяти піайв, на андроїд його нема, тоді піду старим шляхом
try:
    import av
    AV_AVAILABLE = True
except Exception:
    av = None
    AV_AVAILABLE = False

import video_tools as vt

logger = logging.getLogger(__name__)

# короткі відео беру старим шляхом, там він і так швидкий і точніший
FAST_MIN_DURATION_SEC = 90.0
# скільки точок перевіряю в одному відрізку щоб вибрати найрухливішу
FAST_SEEKS_PER_SEGMENT = 2
# після ключового кадру декодую ще кілька кадрів щоб підійти ближче до цілі
FAST_FORWARD_FRAMES = 12
# вікно в секундах і точки де міряю як часто стоять ключові кадри!!!!!!
_GOP_PROBE_WINDOW_SEC = 40.0
_GOP_PROBE_POINTS = (0.2, 0.5, 0.8)
# ширина картинки для порівняння руху, менша значить швидше
_MOTION_WIDTH = 320
# максимум тредів для декодування
FAST_MAX_WORKERS = 4
# якщо сіків мало, то багато тредів не окупиться
_FAST_PARALLEL_MIN_SEEKS = 120


# свій клас помилки, кидаю його коли швидкий шлях не підходить
class FastPathUnavailable(Exception):
    pass


# ф-ція відкриття відео, не забути закрити контейнер потім
def _open_video(video_path, single_thread):
    container = av.open(video_path)
    stream = container.streams.video[0]
    # якщо буде багато тредів зверху, то тут ставлю один тред
    if single_thread:
        stream.codec_context.thread_count = 1
    else:
        stream.thread_type = "AUTO"
    return container, stream


# ф-ція довжини відео в секундах, пробую два способи по черзі
def _duration_sec(container, stream, video_path):
    # перший спосіб, беру з потоку
    try:
        if stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)
        if container.duration:
            return container.duration / av.time_base
    except Exception:
        pass
    # другий спосіб, беру з мп4 через свою ф-цію
    try:
        return float(vt.read_duration_from_mp4(video_path) or 0)
    except Exception:
        return 0.0


# ф-ція оцінки гоп, не забути що тут без декодування
# дивлюсь тільки пакети і міряю відстань між ключовими кадрами
# повертаю медіану в секундах, або None якщо ключових кадрів майже нема
def _estimate_gop_sec(container, stream, duration_sec):
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
                # вийшов за вікно, далі не дивлюсь
                if t > start + _GOP_PROBE_WINDOW_SEC:
                    break
                if packet.is_keyframe:
                    keyframe_times.append(t)
        except Exception:
            continue

    # прибираю повтори і сортую
    keyframe_times = sorted(set(round(t, 3) for t in keyframe_times))
    # рахую відстані між сусідніми ключовими кадрами
    gaps = []
    for i in range(len(keyframe_times) - 1):
        gap = keyframe_times[i + 1] - keyframe_times[i]
        if gap > 0.05:
            gaps.append(gap)
    if len(gaps) == 0:
        return None
    return statistics.median(gaps)


# ф-ція беру кадр біля потрібного часу
# перехожу до ключового кадру перед target_sec, декодую його і ще кілька
# беру останній кадр який не пізніше за ціль
def _grab_near(container, stream, target_sec, forward_frames):
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
            # перший кадр беру завжди
            chosen = frame
            chosen_sec = pts_sec
        elif pts_sec <= target_sec:
            chosen = frame
            chosen_sec = pts_sec
        else:
            break
        decoded += 1
        if decoded > forward_frames:
            break
    return chosen, chosen_sec


# ф-ція перетворення кадру піайв в масиви, не забути її для кожного кадру
# роблю маленький бгр для джеміні і, якщо треба, повний розмір для кешу
# зменшую ще до переходу в бгр, так швидше
def _frame_to_arrays(frame, rotation, full_dir):
    if full_dir:
        full = frame.to_ndarray(format="bgr24")
        if rotation:
            full = vt._apply_manual_rotation(full, rotation)
        small = vt.resize_frame(full, max_width=vt.GEMINI_FRAME_WIDTH)
        return small, full

    width = frame.width
    height = frame.height
    # якщо повернуто на 90 або 270, то міряю по висоті
    if rotation in (90, 270):
        limit_dim = height
    else:
        limit_dim = width

    if limit_dim > vt.GEMINI_FRAME_WIDTH:
        scale = vt.GEMINI_FRAME_WIDTH / limit_dim
        new_width = max(2, int(width * scale))
        new_height = max(2, int(height * scale))
        small = frame.to_ndarray(width=new_width, height=new_height, format="bgr24")
    else:
        small = frame.to_ndarray(format="bgr24")
    if rotation:
        small = vt._apply_manual_rotation(small, rotation)
    return small, None


# ф-ція робить сіру картинку для порівняння руху
def _motion_gray(small_bgr):
    gray = cv2.cvtColor(small_bgr, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    # якщо широка, то зменшую
    if width > _MOTION_WIDTH:
        new_height = max(1, int(height * _MOTION_WIDTH / width))
        gray = cv2.resize(gray, (_MOTION_WIDTH, new_height), interpolation=cv2.INTER_AREA)
    return gray


# ф-ція одного треду, не забути що в неї свій контейнер
# обробляє свій набір відрізків, готові кадри кладе в чергу і повертає списком
def _run_slice(video_path, seg_start, seg_end, segment_sec, per_segment, rotation, full_dir,
               single_thread, cancel_event, frame_queue, stats):
    container, stream = _open_video(video_path, single_thread)
    frames = []
    seen_pts = set()
    prev_gray = None
    baseline = deque(maxlen=vt.MOTION_BASELINE_WINDOW)
    try:
        for segment_index in range(seg_start, seg_end):
            # якщо скасували, виходжу
            if cancel_event.is_set():
                break
            best = None

            # беру кілька кадрів у відрізку і вибираю найрухливіший
            for j in range(per_segment):
                if cancel_event.is_set():
                    break
                target = segment_index * segment_sec + (j + 0.5) * segment_sec / per_segment
                try:
                    frame, pts_sec = _grab_near(container, stream, target, FAST_FORWARD_FRAMES)
                except Exception as error:
                    logger.debug(f"[fast] сикання на {target:.1f}с не вдалось: {error}")
                    continue
                if frame is None:
                    continue

                # якщо такий кадр вже брав, пропускаю
                key = round(pts_sec, 3)
                if key in seen_pts:
                    continue
                seen_pts.add(key)

                small, full = _frame_to_arrays(frame, rotation, full_dir)
                gray = _motion_gray(small)
                if prev_gray is None:
                    motion = 0.0
                else:
                    motion = vt._motion_percent(prev_gray, gray)
                prev_gray = gray

                if len(baseline) > 0:
                    base = statistics.median(baseline)
                else:
                    base = 0.0
                # рух мінус фон, хвилі дають малий бал а справжній рух великий
                score = motion - base
                baseline.append(motion)
                stats["candidates"] += 1
                if best is None or score > best["score"]:
                    best = {"score": score, "timestamp_sec": pts_sec, "small": small, "full": full}

            # якщо знайшов найкращий кадр, кодую його в джейпег
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
            # лічильник для прогресу, гіл робить таке оновлення безпечним
            stats["segments_done"] += 1
    finally:
        # контейнер закриваю завжди, щоб не забути
        container.close()
    return frames


# головна ф-ція, не забути її викликати з відео_тулс
# те саме що vt.extract_frames, але через ключові кадри
# кидає FastPathUnavailable до того як почне віддавати кадри
def extract_frames_fast(video_path, max_frames=300, progress_callback=None, is_cancelled=None,
                        full_res_dir=None, on_frame_ready=None, frame_count_callback=None,
                        workers=None):
    # цей шлях можна вимкнути через змінну оточення
    if os.environ.get("VIDEO_FAST_DISABLE") == "1":
        raise FastPathUnavailable("вимкнено через VIDEO_FAST_DISABLE")
    if not AV_AVAILABLE:
        raise FastPathUnavailable("PyAV не встановлено")

    # пробую відкрити файл
    try:
        container, stream = _open_video(video_path, single_thread=False)
    except Exception as error:
        raise FastPathUnavailable(f"PyAV не відкрив файл: {error}")

    # перевіряю чи підходить швидкий шлях
    try:
        duration = _duration_sec(container, stream, video_path)
        if duration < FAST_MIN_DURATION_SEC:
            raise FastPathUnavailable(f"коротке відео ({duration:.0f}с) - старий шлях точніший")
        segment_sec = duration / max_frames
        gop_sec = _estimate_gop_sec(container, stream, duration)

        if gop_sec is None:
            gop_text = "?"
        else:
            gop_text = f"{gop_sec:.1f}с"
        if gop_sec is None or gop_sec > segment_sec:
            raise FastPathUnavailable(
                f"GOP={gop_text} довший за сегмент "
                f"{segment_sec:.1f}с - keyframe-вибірка дала б дірки")

        # швидкий тест, чи працює перехід і декодування на цьому файлі
        probe_frame, _ = _grab_near(container, stream, duration * 0.5, 0)
        if probe_frame is None:
            raise FastPathUnavailable("тестове сикання не повернуло кадр")
    finally:
        container.close()

    rotation = vt._prepare_orientation(video_path)
    per_segment = FAST_SEEKS_PER_SEGMENT
    total_seeks = max_frames * per_segment

    # скільки тредів брати
    if workers is None:
        cpu = os.cpu_count() or 1
        if total_seeks < _FAST_PARALLEL_MIN_SEEKS:
            # для малої роботи досить одного треду
            workers = 1
        else:
            if cpu > 2:
                free_cpu = cpu - 1
            else:
                free_cpu = cpu
            workers = max(1, min(FAST_MAX_WORKERS, free_cpu))
    workers = max(1, min(workers, max_frames))
    # якщо тредів багато, то в кожного декодера ставлю один тред
    single_thread = workers > 1

    # межі відрізків для кожного треду
    bounds = [round(i * max_frames / workers) for i in range(workers + 1)]
    logger.debug(f"[fast] keyframe-нарізка: тривалість={duration:.0f}с сегмент={segment_sec:.1f}с "
                 f"GOP≈{gop_sec:.1f}с seek-ів={total_seeks} воркерів={workers}")

    start_ts = time.time()
    cancel_event = threading.Event()
    frame_queue = queue.Queue()
    stats = {"candidates": 0, "segments_done": 0}
    emitted = 0

    # ф-ція забирає готові кадри з черги і віддає їх назовні
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
        # запускаю по одному заданню на кожен тред
        futures = []
        for i in range(workers):
            if bounds[i + 1] > bounds[i]:
                future = pool.submit(_run_slice, video_path, bounds[i], bounds[i + 1], segment_sec,
                                     per_segment, rotation, full_res_dir, single_thread,
                                     cancel_event, frame_queue, stats)
                futures.append(future)

        pending = set(futures)
        last_percent = -1
        last_count = -1
        # чекаю поки всі закінчать
        while pending:
            if is_cancelled is not None and is_cancelled():
                cancel_event.set()
            _done, pending = wait(pending, timeout=0.2)
            drain()
            # оновлюю прогрес
            if progress_callback:
                percent = int(stats["segments_done"] / max_frames * 100)
                if percent != last_percent:
                    progress_callback(min(percent, 100) / 100)
                    last_percent = percent
            # оновлюю лічильник кадрів
            if frame_count_callback and emitted != last_count:
                frame_count_callback(emitted)
                last_count = emitted
        drain()

        # збираю результати з усіх тредів
        try:
            parts = [future.result() for future in futures]
        except Exception as error:
            if emitted == 0:
                raise FastPathUnavailable(f"воркер упав до першого кадру: {error}")
            raise

    # складаю всі кадри в один список і сортую по часу
    frames = []
    for part in parts:
        for f in part:
            frames.append(f)
    frames = sorted(frames, key=lambda item: item["timestamp_sec"])

    # один і той самий ключовий кадр міг потрапити в два сусідні відрізки, прибираю дублікати
    unique = []
    last_ts = None
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
