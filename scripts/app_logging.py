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
app_logging.py

Налаштування логів. Пишемо все у файл logs/app.log і одночасно в консоль.
Викликаємо один раз при старті програми (main.py).

Як використовувати в модулях:
    import logging
    logger = logging.getLogger(__name__)
    logger.info("...")
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

MAX_BYTES = 2 * 1024 * 1024  # розмір одного файлу логів - 2 МБ
BACKUP_COUNT = 5             # скільки старих файлів зберігаємо (app.log.1 ... app.log.5)

_configured = False


def setup_logging(level=logging.DEBUG, console_level=logging.DEBUG):
    """
    Налаштовує кореневий логер. Можна викликати кілька разів,
    але зробиться це тільки один раз.

    level         - що писати у файл (DEBUG = все, включно з діагностикою).
    console_level - що показувати в консолі.
    """
    global _configured
    if _configured:
        return  # вже налаштовано, щоб не було дублів у логах
    _configured = True

    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Коли файл стає більшим за MAX_BYTES, почнеться новий
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # Беремо найнижчий рівень з двох, інакше частину повідомлень відсіємо
    root_logger.setLevel(min(level, console_level))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # Сторонні бібліотеки на DEBUG пишуть дуже багато (кожен HTTP-запит),
    # тому їх притишуємо, щоб бачити тільки справжні проблеми
    for noisy_logger_name in ("urllib3", "PIL"):
        logging.getLogger(noisy_logger_name).setLevel(logging.WARNING)

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
