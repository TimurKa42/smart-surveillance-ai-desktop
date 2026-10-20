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

Логи в logs/app.log + консоль. Викликати раз в main.py, *щоб не забути*!

В модулях беру logging.getLogger(__name__) і пишу logger.info(...)
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

MAX_BYTES = 2 * 1024 * 1024  # 2 МБ, потім новий файл
BACKUP_COUNT = 5             # старих файлів 5, далі видаляються

_configured = False  # щоб не налаштовувати двічі


def setup_logging(level=logging.DEBUG, console_level=logging.DEBUG):
    # Ф-ція налаштування логів, не забути викликати в main
    # level - для файлу, console_level - для консолі
    global _configured
    if _configured:
        return  # вже робив, дублі не треба
    _configured = True

    os.makedirs(LOG_DIR, exist_ok=True)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Беру RotatingFileHandler, сам робить новий файл
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)

    # Ще вивід в консоль
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # min() щоб нічого не загубилось *щоб не забути*
    root_logger.setLevel(min(level, console_level))
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    # urllib3 і PIL спамлять на DEBUG, глушу їх
    for noisy_logger_name in ("urllib3", "PIL"):
        logging.getLogger(noisy_logger_name).setLevel(logging.WARNING)

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
