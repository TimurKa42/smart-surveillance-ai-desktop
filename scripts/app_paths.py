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
dnd_tools.py

Перевірка файлу, який перетягнули у вікно програми (drag-and-drop).
Тут немає нічого з інтерфейсу, тому модуль можна тестувати окремо.

Перевірка йде в два кроки:
  1) швидко - дивимось на розширення файлу;
  2) повільніше - пробуємо відкрити файл через OpenCV і прочитати
     два кадри (див. probe_video() нижче).

Результат - один із трьох станів (STATUS_*), а не просто True/False.
Якщо OpenCV не прочитав файл, це ще не означає, що це не відео
(може бути незвичний кодек). Тому в такому випадку питаємо користувача.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import os

import cv2

from video_tools import CV2_PROBE_TIMEOUT_SEC, run_with_timeout

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
    ".wmv", ".flv", ".mpg", ".mpeg", ".3gp",
}

STATUS_OK = "ok"                # точно відео, можна брати одразу
STATUS_NOT_VIDEO = "not_video"  # точно не відео, відхиляємо
STATUS_UNKNOWN = "unknown"      # не впевнені, питаємо користувача

# Мінімум і максимум кадрів, щоб вважати файл відео.
# OpenCV може "відкрити" картинку як відео з одним кадром, і лічильник
# кадрів у такої картинки буває сміттям (наприклад, INT64_MIN).
# Тому одного прочитаного кадру мало, дивимось ще й на другий кадр і на лічильник.
_MIN_PLAUSIBLE_FRAME_COUNT = 2
_MAX_PLAUSIBLE_FRAME_COUNT = 10_000_000


def has_video_extension(path):
    _, ext = os.path.splitext(path)
    return ext.lower() in VIDEO_EXTENSIONS


def _probe_video_uncapped(path):
    """Сама перевірка без ліміту часу. Її запускає run_with_timeout(),
    бо OpenCV на деяких системах (macOS) може зависнути на не-відео файлі,
    а ця функція викликається в головному потоці Tkinter."""
    video = None
    try:
        video = cv2.VideoCapture(path)
        if not video.isOpened():
            return False

        ok, _ = video.read()
        if not ok:
            return False

        # Один кадр ще нічого не доводить, читаємо другий
        ok2, _ = video.read()
        if ok2:
            return True

        # Другого кадру немає. Дивимось на лічильник кадрів:
        # у картинки він або 0, або сміття, а не нормальне число
        frame_count = video.get(cv2.CAP_PROP_FRAME_COUNT)
        return _MIN_PLAUSIBLE_FRAME_COUNT <= frame_count <= _MAX_PLAUSIBLE_FRAME_COUNT
    finally:
        if video is not None:
            video.release()


def probe_video(path):
    """
    Пробує відкрити файл через OpenCV і перевірити, що в ньому є кадри.

    Повертає:
        True  - це точно відео
        False - OpenCV не зміг прочитати кадри, це схоже на не відео
        None  - під час перевірки сталась помилка або вона не встигла
                за CV2_PROBE_TIMEOUT_SEC. Це не означає, що файл поганий,
                просто перевірити не вдалося.
    """
    try:
        result, timed_out = run_with_timeout(lambda: _probe_video_uncapped(path))
    except Exception:
        return None
    if timed_out:
        print(f"[diag] probe_video: перевірка файлу не відповіла за {CV2_PROBE_TIMEOUT_SEC:.0f}с: {path}")
        return None
    return result


def classify_dropped_file(path):
    """
    Повертає один зі STATUS_* для шляху, який перетягнули у вікно.

        STATUS_OK        - розширення відео і OpenCV прочитав кадр. Приймаємо.
        STATUS_NOT_VIDEO - не відео за розширенням і OpenCV не прочитав кадр.
                           Відхиляємо одразу.
        STATUS_UNKNOWN   - всі інші випадки (незнайоме розширення, що читається,
                           або помилка перевірки). Рішення за користувачем.

    Якщо файлу немає на диску, вважаємо його не відео.
    """
    if not path or not os.path.isfile(path):
        return STATUS_NOT_VIDEO

    ext_ok = has_video_extension(path)
    probe_result = probe_video(path)

    if ext_ok and probe_result is True:
        return STATUS_OK
    if not ext_ok and probe_result is False:
        return STATUS_NOT_VIDEO
    return STATUS_UNKNOWN

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
