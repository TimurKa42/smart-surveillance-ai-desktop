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
app_logging.py

Централізоване налаштування логування для застосунку. Раніше
video_tools.py і gemini_tools.py писали діагностику/тайминги/помилки
просто через print("[timing] ...") / print("[diag] ...") - зручно для
розробки в PyCharm, але після завершення процесу все губиться, і
немає способу подивитись, що відбувалось під час аналізу вже ПІСЛЯ
того, як застосунок закрили (наприклад, якщо на захисті MAN щось
пішло не так).

setup_logging() викликається ОДИН РАЗ на старті застосунку (main.py,
до першого використання video_tools/gemini_tools) і налаштовує
кореневий логер одразу двома handler'ами:

- FileHandler з ротацією (RotatingFileHandler) - пише в logs/app.log
  поряд зі screenshots/, з ротацією по розміру, щоб файл не ріс
  безмежно за багато запусків.
- StreamHandler - дублює те саме в консоль (для розробки).

Модулі, які хочуть логувати, просто роблять:

    import logging
    logger = logging.getLogger(__name__)
    logger.info("...")

- жодних додаткових залежностей від цього модуля їм не треба, достатньо,
щоб setup_logging() уже відпрацював десь на старті процесу.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import logging
import os
from logging.handlers import RotatingFileHandler

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "app.log")

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

MAX_BYTES = 2 * 1024 * 1024  # 2 МБ на файл
BACKUP_COUNT = 5             # + до 5 старих файлів (app.log.1 ... app.log.5)

_configured = False


def setup_logging(level=logging.DEBUG, console_level=logging.DEBUG):
    """
    Налаштовує кореневий логер застосунку. Безпечно викликати кілька
    разів - реальне налаштування відбувається лише один раз (захист
    від подвійних handler'ів і задвоєних рядків у логах, якщо
    setup_logging() випадково викликали повторно).

    level         - мінімальний рівень, що йде у файл (за замовчуванням
                    DEBUG - у файл пишемо все, включно з [diag]).
    console_level - мінімальний рівень, що дублюється в консоль (за
                    замовчуванням теж DEBUG - щоб під час довгого
                    аналізу в консолі PyCharm було видно ту саму
                    діагностику нарізки/Gemini, що й раніше через
                    print(); технічні DEBUG-логи сторонніх бібліотек
                    (urllib3, PIL) при цьому окремо притишені нижче,
                    щоб не тонути в шумі HTTP-з'єднань).
    """
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(min(level, console_level))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Сторонні бібліотеки (requests/urllib3, PIL і т.д.) на DEBUG дуже
    # "балакучі" - кожен HTTP-запит/відповідь, заголовки тощо. Нам
    # потрібен DEBUG лише для НАШИХ модулів (video_tools, gemini_tools),
    # тож технічні логери сторонніх бібліотек притишуємо до WARNING -
    # не заважає бачити реальні збої (SSL-помилки, retry), але прибирає
    # шум з кожного окремого HTTP-виклику.
    for noisy_logger_name in ("urllib3", "PIL"):
        logging.getLogger(noisy_logger_name).setLevel(logging.WARNING)

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
