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

Валідація файлу, який користувач перетягнув (drag-and-drop) у головне
вікно програми. Чиста логіка без UI-залежностей (як video_tools.py /
gemini_tools.py) - легко тестувати окремо від Tkinter.

Перевірка - двоступенева:
  1) швидка - по розширенню файлу (майже миттєво);
  2) надійніша, але повільніша - спроба відкрити файл через OpenCV і
     прочитати з нього ДВА кадри (так само, як це вже робить
     video_tools.get_video_duration_sec для звичайного вибору файлу
     через діалог) - саме два, а не один, див. коментар над
     probe_video() нижче.

Результат - один із трьох станів (STATUS_*), а не голе True/False:
одного "не вдалося прочитати кадр" замало, щоб сказати, чи файл
СПРАВДІ не відео, чи просто трапився незвичний контейнер/кодек, який
OpenCV не осилив, - у другому випадку рішення краще віддати
користувачу ("продовжити на свій ризик?"), а не тихо відхиляти файл.
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

STATUS_OK = "ok"                # точно відео - можна брати без запитань
STATUS_NOT_VIDEO = "not_video"  # точно НЕ відео - відхиляємо одразу
STATUS_UNKNOWN = "unknown"      # є сумніви - питаємо користувача

# Скільки кадрів має бути у файлі, щоб узагалі вважати його "відео" в
# сенсі цього застосунку. OpenCV охоче "відкриває" звичайну картинку
# (PNG/JPG) як відео з РІВНО ОДНИМ кадром - isOpened()=True,
# read()=True, - і при цьому CAP_PROP_FRAME_COUNT для такого файлу
# повертає не 0, а відверто биний лічильник (на практиці спостерігалось
# INT64_MIN). Тому "прочитався один кадр" саме по собі НІЧОГО не
# доводить - перевіряємо ще й другий кадр і правдоподібність лічильника.
_MIN_PLAUSIBLE_FRAME_COUNT = 2
_MAX_PLAUSIBLE_FRAME_COUNT = 10_000_000


def has_video_extension(path):
    _, ext = os.path.splitext(path)
    return ext.lower() in VIDEO_EXTENSIONS


def _probe_video_uncapped(path):
    """Сама перевірка, без обмеження за часом - винесена окремо, щоб
    run_with_timeout() міг виконати її в окремому потоці (див.
    коментар над CV2_PROBE_TIMEOUT_SEC у video_tools.py: на деяких
    бекендах, зокрема macOS/AVFoundation, cv2.VideoCapture для файлу,
    що не є відео, може зависнути НАЗАВЖДИ замість чесної помилки - а
    ця перевірка викликається СИНХРОННО в головному потоці Tkinter під
    час drag-and-drop, тож без таймауту зависло б усе вікно програми)."""
    video = None
    try:
        video = cv2.VideoCapture(path)
        if not video.isOpened():
            return False

        ok, _ = video.read()
        if not ok:
            return False

        # Перший кадр прочитався - це ЩЕ НЕ доказ, що це відео (див.
        # коментар біля _MIN_PLAUSIBLE_FRAME_COUNT вище). Перевіряємо
        # другий кадр: якщо він теж є - сумнівів більше нема, точно
        # відео з кількома кадрами.
        ok2, _ = video.read()
        if ok2:
            return True

        # Другого кадру нема - можливо, це справді відео рівно з одним
        # кадром (теоретично можливо, але для аналізу руху в цьому
        # застосунку однаково безглуздо), а можливо - картинка. Останній
        # шанс відрізнити - лічильник кадрів: у картинки він або 0,
        # або відверто биний (від'ємний/астрономічний), а не "1".
        frame_count = video.get(cv2.CAP_PROP_FRAME_COUNT)
        return _MIN_PLAUSIBLE_FRAME_COUNT <= frame_count <= _MAX_PLAUSIBLE_FRAME_COUNT
    finally:
        if video is not None:
            video.release()


def probe_video(path):
    """
    Намагається відкрити файл через OpenCV і переконатись, що в ньому
    справді є більше одного кадру (див. _probe_video_uncapped вище).

    Повертає:
        True  - файл відкрився, і в ньому підтверджено декілька кадрів
                (чи бодай правдоподібний лічильник) - точно відео.
        False - файл відкрився, АЛЕ жодного кадру прочитати не
                вдалося, або OpenCV взагалі не зміг його відкрити, або
                прочитався РІВНО один кадр без правдоподібного
                лічильника (типова ознака картинки, а не відео) - це
                чіткий сигнал "не відео / зіпсований файл".
        None  - під час перевірки сталася НЕСПОДІВАНА помилка
                (наприклад, файл раптом зник з диска, чи немає прав
                читання), АБО перевірка не встигла за
                CV2_PROBE_TIMEOUT_SEC (файл завис на відкритті - див.
                коментар над CV2_PROBE_TIMEOUT_SEC у video_tools.py) -
                в обох випадках це НЕ означає "не відео", лише
                "перевірити не вдалося".
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
    Головна функція модуля - повертає один із STATUS_* для шляху, який
    перетягнули у вікно.

        STATUS_OK        - розширення схоже на відео, І OpenCV зміг
                            прочитати перший кадр. Приймаємо мовчки.
        STATUS_NOT_VIDEO - розширення НЕ відеофайлу, І OpenCV
                            підтвердив, що прочитати кадр не вдалося.
                            Відхиляємо файл одразу, без діалогу.
        STATUS_UNKNOWN    - будь-яка "сумнівна" комбінація: незнайоме
                            розширення, яке все ж вдалося прочитати;
                            "правильне" розширення, яке чомусь не
                            прочиталось; або сталася помилка (probe_video
                            повернув None). Рішення лишаємо за
                            користувачем.

    Файл, що не існує на диску (наприклад, перетягнули щось із
    мережевого ресурсу, який не встиг змонтуватись) - трактуємо як
    STATUS_NOT_VIDEO, а не як помилку перевірки.
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
