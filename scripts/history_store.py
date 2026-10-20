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

# history_store.py, тут історія аналізів, зберігаю її у файлі мета джейсон, щоб не забути
#
# один аналіз відео це одна сесія, в ній беру:
#     session_id - унікальний номер
#     finished_at - коли аналіз закінчився
#     video_name - назва відео
#     video_path - шлях до відео
#     frames - список знайдених кадрів (id, filename, timestamp_sec, description)
#
# скріншоти називаю юайді, щоб кадри з різних відео не затирали один одного
#
# міграція, не забути: раніше мета джейсон був просто списком кадрів без сесій.
# старий файл при першому читанні стає однією сесією "Без категорії"
# (is_legacy=True) і одразу пишеться назад в новому форматі

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import json
import os
import uuid

# назва файлу з історією, щоб не забути
META_FILENAME = "meta.json"


# ф-ція шляху до мета джейсон, не забути її
def meta_path(screenshots_dir):
    return os.path.join(screenshots_dir, META_FILENAME)


# ф-ція перевірки старого формату, старий це список кадрів без ключа frames
def _is_legacy_flat_format(data):
    if not data:
        return False
    first = data[0]
    return isinstance(first, dict) and "frames" not in first


# ф-ція міграції, складаю старі кадри в одну сесію "Без категорії"
# дату сесії беру найпізнішу серед кадрів, щоб вона стояла на правильному місці в списку
def _migrate_flat_records(flat_records):
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


# ф-ція читання історії, не забути її
# повертаю список сесій, найновіші першими
# якщо файл зіпсований або його нема, повертаю порожній список
# старий формат одразу переводжу в новий
def load_sessions(screenshots_dir):
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


# ф-ція запису, пишу всю історію в мета джейсон
def save_sessions(screenshots_dir, sessions):
    path = meta_path(screenshots_dir)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)


# ф-ція додавання нової сесії на початок історії, щоб не забути
def append_session(screenshots_dir, session):
    sessions = load_sessions(screenshots_dir)
    sessions.insert(0, session)
    save_sessions(screenshots_dir, sessions)


# ф-ція нового айді для кадру
def new_record_id():
    return uuid.uuid4().hex


# ф-ція нового айді для сесії
def new_session_id():
    return uuid.uuid4().hex


# ф-ція нової назви для скріншота, юайді плюс джейпег
def new_screenshot_filename():
    return f"{uuid.uuid4().hex}.jpg"

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
