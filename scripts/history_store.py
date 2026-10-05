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
history_store.py

Історія АНАЛІЗІВ (сесій) за весь час роботи програми - зберігається у
screenshots/meta.json. Кожен запуск аналізу відео - це одна "сесія":
дата+час завершення, відео (назва+шлях) і список знайдених кадрів
усередині неї. Раніше (до появи сесій) тут зберігався просто плаский
список окремих кадрів - див. "Міграція" нижче.

Формат сесії:
    {
        "session_id": str,
        "finished_at": str,     # ISO-рядок, коли аналіз ЗАВЕРШИВСЯ
        "video_name": str,
        "video_path": str,
        "frames": [
            {
                "id": str,
                "filename": str,       # ім'я файлу скріншота в screenshots/
                "timestamp_sec": float,  # таймкод усередині відео
                "description": str,      # опис від Gemini
            },
            ...
        ],
    }

Файли скріншотів іменуються унікальним uuid (а не frame_<номер>.jpg,
як раніше) - інакше кадр №5 сьогоднішнього відео і кадр №5 завтрашнього
іншого відео мовчки переписували б один одного на диску.

Міграція. До появи сесій meta.json був ПЛОСКИМ списком записів кадрів
(кожен - id, filename, found_at, timestamp_sec, description, video_name,
без session_id і без вкладеності). При першому ж load_sessions() такий
старий файл автоматично перетворюється на ОДНУ сесію "Без категорії"
(ознака - "is_legacy": True, video_name/video_path порожні) з усіма
старими кадрами всередині, і одразу зберігається назад у новому
форматі - тобто міграція відбувається один раз і непомітно для
користувача.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import json
import os
import uuid

META_FILENAME = "meta.json"


def meta_path(screenshots_dir):
    return os.path.join(screenshots_dir, META_FILENAME)


def _is_legacy_flat_format(data):
    """Старий формат - це список словників кадрів БЕЗ ключа "frames"
    (він же ознака нового формату - списку сесій)."""
    if not data:
        return False
    first = data[0]
    return isinstance(first, dict) and "frames" not in first


def _migrate_flat_records(flat_records):
    """Групує старі плоскі записи кадрів в ОДНУ сесію "Без категорії"
    (main.py покаже цю назву через localization, тут лишень ознака
    is_legacy). finished_at сесії - найпізніший found_at серед кадрів,
    щоб сесія розумно сортувалась разом з новими."""
    frames = []
    found_ats = []
    video_name = ""
    for record in flat_records:
        if not isinstance(record, dict):
            continue
        found_at = record.get("found_at", "")
        if found_at:
            found_ats.append(found_at)
        if not video_name and record.get("video_name"):
            video_name = record.get("video_name")
        frames.append({
            "id": record.get("id") or new_record_id(),
            "filename": record.get("filename"),
            "timestamp_sec": record.get("timestamp_sec"),
            "description": record.get("description", ""),
        })

    return {
        "session_id": "legacy",
        "finished_at": max(found_ats) if found_ats else "",
        "video_name": video_name,
        "video_path": "",
        "is_legacy": True,
        "frames": frames,
    }


def load_sessions(screenshots_dir):
    """
    Повертає список сесій аналізу (найновіші - першими).

    Якщо meta.json пошкоджений або відсутній - повертаємо порожній
    список, а не падаємо. Це навмисно "м'яка" функція.

    Якщо файл ще у старому плоскому форматі - автоматично мігрує його
    в новий (сесія "Без категорії") і одразу перезаписує на диску.
    """
    path = meta_path(screenshots_dir)
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    if not data:
        return []

    if _is_legacy_flat_format(data):
        sessions = [_migrate_flat_records(data)]
        save_sessions(screenshots_dir, sessions)
        return sessions

    sessions = [s for s in data if isinstance(s, dict)]
    sessions.sort(key=lambda s: s.get("finished_at", ""), reverse=True)
    return sessions


def save_sessions(screenshots_dir, sessions):
    path = meta_path(screenshots_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def append_session(screenshots_dir, session):
    """Дописує ОДНУ нову сесію (результат щойно завершеного аналізу) на
    початок історії - викликається після кожного успішного аналізу."""
    sessions = load_sessions(screenshots_dir)
    sessions.insert(0, session)
    save_sessions(screenshots_dir, sessions)


def new_record_id():
    return uuid.uuid4().hex


def new_session_id():
    return uuid.uuid4().hex


def new_screenshot_filename():
    return f"{uuid.uuid4().hex}.jpg"

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
