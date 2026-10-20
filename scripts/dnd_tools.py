# =============================================================================
# LookBack
# © 2026 Тимур Каленик (Timur Kalenik Corporation) <timurkalenik950@gmail.com>
# Автор: Тимур Каленик. Усі права захищено.
# =============================================================================

"""
dnd_tools.py

валідація файлу, який користувач перетягнув (драг-енд-дроп) у головне
вікно програми. чиста логіка без юай-залежностей.

перевірка двоступенева:
  1) швидка - по розширенню файлу;
  2) повільніша - спроба відкрити файл через опенсв і прочитати ДВА кадри.
     саме два: картинка (jpg/png) відкривається як "відео" з одним кадром,
     тож один успішний кадр не доводить, що це відео.

результат - один із трьох станів:
  STATUS_OK        - це відео, можна брати;
  STATUS_NOT_VIDEO - точно не відео (червоний текст у вікні);
  STATUS_UNKNOWN   - незрозуміло (незвичний контейнер, завис опенсв),
                     рішення за користувачем.
"""

import logging
import os

import cv2

from video_tools import CV2_PROBE_TIMEOUT_SEC, run_with_timeout, _open_capture

logger = logging.getLogger(__name__)

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v",
    ".wmv", ".flv", ".mpg", ".mpeg", ".3gp",
}

# розширення, які точно не відео: відхиляю без відкриття файлу
NON_VIDEO_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff",
    ".heic", ".heif", ".svg", ".ico",
    ".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".exe", ".dmg", ".py", ".json", ".csv",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a",
}

STATUS_OK = "ok"
STATUS_NOT_VIDEO = "not_video"
STATUS_UNKNOWN = "unknown"

# скільки кадрів треба прочитати, щоб файл вважався відео
PROBE_FRAMES_REQUIRED = 2


def probe_video(path):
    """Пробує прочитати PROBE_FRAMES_REQUIRED кадрів.

    Повертає "ok" (кадри прочитано), "fail" (файл не відкрився або кадрів
    замало) або "timeout" (опенсв завис).
    """
    def probe():
        video = _open_capture(path)
        try:
            if not video.isOpened():
                return False
            for _ in range(PROBE_FRAMES_REQUIRED):
                ok, _frame = video.read()
                if not ok:
                    return False
            return True
        finally:
            video.release()

    try:
        # run_with_timeout повертає результат, чи_вийшов_час
        value, timed_out = run_with_timeout(probe, CV2_PROBE_TIMEOUT_SEC)
    except Exception:  # noqa: BLE001
        logger.exception(f"Помилка перевірки файлу: {path}")
        return "fail"

    if timed_out:
        logger.warning(f"Перевірка файлу зависла: {path}")
        return "timeout"
    return "ok" if value else "fail"


def classify_dropped_file(path):
    """Повертає STATUS_OK, STATUS_NOT_VIDEO або STATUS_UNKNOWN."""
    if not path or not os.path.isfile(path):
        return STATUS_NOT_VIDEO

    ext = os.path.splitext(path)[1].lower()

    if ext in NON_VIDEO_EXTENSIONS:
        return STATUS_NOT_VIDEO

    result = probe_video(path)

    if ext in VIDEO_EXTENSIONS:
        # розширення відеошне: або ок, або питаю користувача
        return STATUS_OK if result == "ok" else STATUS_UNKNOWN

    # розширення невідоме
    if result == "ok":
        return STATUS_OK
    if result == "timeout":
        return STATUS_UNKNOWN
    return STATUS_NOT_VIDEO
