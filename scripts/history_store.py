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

Історія аналізів. Зберігається у файлі screenshots/meta.json.

Один аналіз відео - це одна "сесія":
    session_id    - унікальний номер
    finished_at   - коли аналіз закінчився
    video_name    - назва відео
    video_path    - шлях до відео
    frames        - список знайдених кадрів:
                    {id, filename, timestamp_sec, description}

Файли скріншотів називаємо uuid, щоб кадри з різних відео не затирали один одного.

Міграція: раніше meta.json був просто списком кадрів без сесій. Такий
старий файл при першому читанні перетворюється на одну сесію "Без категорії"
(is_legacy=True) і одразу записується назад у новому форматі.
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
    """Старий формат - список кадрів без ключа "frames"."""
    if not data:
        return False
    first = data[0]
    return isinstance(first, dict) and "frames" not in first


def _migrate_flat_records(flat_records):
    """
    Складає старі кадри в одну сесію "Без категорії".
    Дата сесії - найпізніша дата серед кадрів, щоб вона стояла на правильному місці в списку.
    """
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
    Повертає список сесій, найновіші - першими.

    Якщо файл зіпсований або його нема - повертає порожній список.
    Старий формат одразу переводить у новий.
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
    """Записує всю історію у meta.json."""
    path = meta_path(screenshots_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


def append_session(screenshots_dir, session):
    """Додає нову сесію на початок історії."""
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
