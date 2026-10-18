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
main.py

Головний файл програми - тут вікно (інтерфейс) на CustomTkinter.

Як це працює для користувача:
1) обираєш відео
2) пишеш, кого/що шукати
3) обираєш тип аналізу справа (Швидкий - менше кадрів і без "роздумів", Детально - до 2000 кадрів + слайдер)
4) тиснеш "Почати аналіз"
5) чекаєш - і отримуєш звіт: зліва список знайдених моментів, справа фото
6) фото можна клікнути - відкриється на весь екран (там же можна зумити
   кнопками + / - і тягати картинку мишкою, якщо вона збільшена)
7) кнопка "Очистити все" скидає програму до початкового стану

Зверху також є панель налаштувань:
- Тема оформлення: Авто (слідує за системною темою), Світла, Темна.
  Якщо обрати Світлу чи Темну - вона так і залишиться, навіть якщо
  потім змінити тему в самій операційній системі.
- Мова інтерфейсу: українська (за замовчуванням) або англійська.

Обидва вибори запам'ятовуються в settings.json і не скидаються після
перезапуску програми (див. settings_store.py).

Уся "важка" логіка (нарізка відео, звернення до Gemini) винесена в
video_tools.py і gemini_tools.py - тут лише сам інтерфейс.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import logging
import os
import queue
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime
import tkinter as tk
import customtkinter as ctk
from tkinter import filedialog
from PIL import Image, ImageTk

import app_paths

# Зібраний .app запускається з Finder із робочою текою "/" (тільки для
# читання), тож усі відносні шляхи (logs/, .env, settings.json тощо)
# ламалися б ще до появи вікна. Переходимо в теку даних користувача ДО
# будь-якого коду, що щось створює чи читає за відносним шляхом.
# Ресурси, запаковані в збірку, читаються через app_paths.resource_path
# (sys._MEIPASS) і від робочої теки не залежать.
if app_paths.is_frozen():
    os.chdir(app_paths.user_data_dir())

from app_logging import setup_logging

setup_logging()  # налаштовуємо логування ДО першого імпорту, що його використовує
logger = logging.getLogger(__name__)

import config
import history_store
import dnd_tools
import notify_tools
from video_tools import extract_frames, grab_screenshots_batch, format_time, get_video_duration_sec, ScreenshotDiskFullError
from gemini_tools import (
    find_object_in_frames_streaming,
    check_api_key,
    MissingApiKeyError,
    GeminiApiError,
    STREAM_END,
)
from pdf_export import (
    export_frame_to_pdf,
    export_report_to_pdf,
    build_pdf_filename,
    build_full_report_filename,
)
from localization import Localization
from keep_awake import KeepAwake, SuspendWatchdog
from settings_store import load_settings, save_settings, SETTINGS_PATH

# tkinterdnd2 - кросплатформна (Win/Mac/Linux) підтримка drag-and-drop
# файлу з ОС прямо у вікно програми: звичайний tkinter/customtkinter
# цього не вміє. Бібліотека опціональна - якщо вона не встановлена
# (або на системі бракує нативних tkdnd-бібліотек, які вона під
# капотом підвантажує), програма просто працює без drag-and-drop,
# вибір файлу через кнопку "Обрати відео" лишається як завжди.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_IMPORT_OK = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    _DND_IMPORT_OK = False

# App мусить успадковувати TkinterDnD.DnDWrapper (домішку з методами
# drop_target_register/dnd_bind), А НЕ TkinterDnD.Tk - останній сам
# успадкований від tkinter.Tk, і разом з ctk.CTk (теж від tkinter.Tk)
# дав би конфлікт порядку успадкування (MRO). DnDWrapper - "чиста"
# домішка без власного __init__ від Tk, тому конфлікту нема, а
# фактичну підтримку D&D вмикаємо окремим викликом TkinterDnD._require()
# у App.__init__ (див. _setup_drag_and_drop).
_APP_BASE_CLASSES = (ctk.CTk, TkinterDnD.DnDWrapper) if _DND_IMPORT_OK else (ctk.CTk,)

# Відповідність kind помилки з gemini_tools.GeminiApiError -> ключ
# перекладу зі status-повідомленням для користувача. "unknown" свідомо
# нема тут - для нього лишається загальний status_error з текстом
# самої помилки (див. run_pipeline).
GEMINI_ERROR_STATUS_KEYS = {
    "network": "status_error_network",
    "timeout": "status_error_timeout",
    "rate_limit": "status_error_rate_limit",
    "quota_daily": "status_error_quota_daily",
    "invalid_key": "status_error_invalid_key",
    "server": "status_error_server",
    "blocked": "status_error_blocked",
}

# Посилання на отримання безкоштовного API-ключа Gemini. Відкривається
# кнопкою в вікні вводу ключа (webbrowser.open) - текст самого посилання
# скопіювати мишкою не можна (CTkLabel це не текстове поле), тому
# зручніше одразу відкрити сторінку в браузері.
API_KEY_URL = "https://aistudio.google.com/apikey"

# Прогрес-бар "доганяє" ціль плавно, а не стрибає миттєво - кожні
# PROGRESS_ANIM_INTERVAL_MS мс відображене значення зсувається на
# частку (PROGRESS_ANIM_SPEED) різниці до цілі. Що більша частка - то
# швидше "доганяє". При 0.18 і кроці 30мс великий стрибок (напр. 20% ->
# 90%) вкладається приблизно в ту саму половину секунди, що й плавна
# анімація в мобільній версії.
PROGRESS_ANIM_INTERVAL_MS = 30
PROGRESS_ANIM_SPEED = 0.18

# "Підкрутка" прогресу під час очікування відповіді Gemini (окремо від
# плавного "доганяння" вище). Bar рухається лише за РЕАЛЬНИМИ подіями
# (нарізаний кадр, завершена пачка) - але коли пачка "у польоті" довго
# (повільна мережа/сервери Google підтупили), між двома реальними
# подіями може пройти чимало секунд, і бар усе це просто стоїть.
#
# Підкрутка непомітно зсуває ціль ВПЕРЕД, маленькими кроками, у бік
# "стелі" - оптимістичної оцінки того, скільки прогресу дадуть пачки,
# що вже відправлені, але ще не підтверджені. Стеля ЗАВЖДИ лишає запас
# (не дотягує до значення, яке дасть реальне підтвердження), тож коли
# відповідь справді приходить - бар чесно "дострибує" вперед, а не
# відкочується назад. Це не "фейковий" прогрес - лише візуальна
# гарантія, що бар ніколи не виглядає застряглим.
PROGRESS_TRICKLE_INTERVAL_MS = 700
# 0.04 - навмисно повільно: за перші ~5с очікування бар підповзає лише
# на кілька відсотків (непомітний натяк "я ще живий"), а не "добігає"
# більшість шляху до стелі - інакше на типовому (кілька секунд) батчі
# підкрутка сама б виглядала підозріло швидкою.
PROGRESS_TRICKLE_GAIN = 0.04       # частка залишку до стелі, яку долаємо за один тік
PROGRESS_TRICKLE_MIN_GAP = 0.3     # менший розрив - вже не рухаємось, чекаємо реальної події
# Скільки "оптимістичного" прогресу видаємо пачкам, що ще В ПОЛЬОТІ
# (відправлені, відповідь не прийшла) - трохи МЕНШЕ за 1.0, щоб стеля
# завжди лишала запас під реальне підтвердження.
PROGRESS_TRICKLE_INFLIGHT_CREDIT = 0.85

# Скільки капсула прогресу лишається на екрані вже ПІСЛЯ 100% - щоб
# користувач встиг помітити "завершено", а не бачив миттєве зникнення
# бару одразу за фінальним стрибком до 100%.
PROGRESS_HOLD_AT_100_MS = 3000

# Зелений колір заливки прогрес-бару - фіксований (не з теми), як і в
# мобільній версії: зелений "прогрес завершується" має читатись
# однаково і на світлій, і на темній темі.
PROGRESS_BAR_COLOR = "#33C761"

# Жорсткий ліміт довжини запиту - у СИМВОЛАХ (а не словах). Поле вводу
# саме не пускає далі цього числа (див. _on_prompt_modified).
MAX_PROMPT_CHARS = 200

# Нові тексти (вітальне вікно, власний/стандартний ключ). Підмішуються в
# self.loc.t() - див. App._install_extra_strings. Якщо захочеш, ці ключі
# можна перенести в localization.py, а цей словник і виклик видалити.
# Тут же перевизначено кілька старих текстів, які згадували кнопки, яких
# більше нема ("Змінити ключ"), і вибір типу ключа.
EXTRA_STRINGS = {
    "ua": {
        "welcome_title": "Вітаємо у Smart Surveillance AI!",
        "welcome_text": (
            "Програма знаходить людей і об'єкти на відео за вашим описом "
            "та показує знайдені моменти зі скріншотами."
        ),
        "welcome_step_1": "Оберіть відео або перетягніть файл у вікно",
        "welcome_step_2": "Напишіть, кого або що шукати",
        "welcome_step_3": "Натисніть «Почати аналіз» і перегляньте результат",
        "welcome_note": "Програма вже готова до роботи - ключ вводити не потрібно.",
        "own_key_button": "Використати власний ключ",
        "default_key_button": "Повернутись до стандартного ключа",
        "default_key_title": "Стандартний ключ",
        "default_key_confirm": (
            "Повернутись до стандартного ключа? Ваш власний ключ буде "
            "видалено з цього комп'ютера."
        ),
        "status_default_key_restored": "Використовується стандартний ключ",
        "status_error_quota_daily": (
            "Вичерпано добовий ліміт Gemini. Спробуй завтра або встав власний ключ у «Меню»"
        ),
        "status_error_invalid_key": "API-ключ недійсний. Онови його в «Меню»",
        "about_tip_1": (
            "Програма працює зі стандартним ключем. За бажання підключіть "
            "власний ключ у «Меню»."
        ),
    },
    "en": {
        "welcome_title": "Welcome to Smart Surveillance AI!",
        "welcome_text": (
            "The app finds people and objects in a video from your description "
            "and shows the moments it found with screenshots."
        ),
        "welcome_step_1": "Choose a video or drag the file into the window",
        "welcome_step_2": "Write who or what to look for",
        "welcome_step_3": "Press \u201cStart analysis\u201d and check the result",
        "welcome_note": "The app is ready to use - no key setup needed.",
        "own_key_button": "Use my own key",
        "default_key_button": "Go back to the default key",
        "default_key_title": "Default key",
        "default_key_confirm": (
            "Go back to the default key? Your own key will be removed "
            "from this computer."
        ),
        "status_default_key_restored": "Using the default key",
        "status_error_quota_daily": (
            "The daily Gemini limit is used up. Try again tomorrow or add your own key in \u201cMenu\u201d"
        ),
        "status_error_invalid_key": "API key is invalid. Update it in \u201cMenu\u201d",
        "about_tip_1": (
            "The app works with a default key. If you want, add your own key in \u201cMenu\u201d."
        ),
    },
}

# Єдиний стиль рядка звіту (час + текст): однакові шрифти, час - в окремій
# колонці фіксованої ширини ліворуч, текст - праворуч, все по лівому краю.
# Використовується і в списку моментів, і під фото, і в повноекранному перегляді.
REPORT_TIME_COL_WIDTH = 66
REPORT_ROW_HOVER_COLOR = "#144870"
SCREENSHOTS_FOLDER = os.path.join(app_paths.user_data_dir(), "screenshots")  # сюди зберігаємо знайдені скріншоти

# ---------- Іконка вікон (Windows) ----------
# customtkinter через 200 мс після створення КОЖНОГО вікна (головного і всіх
# діалогів-CTkToplevel) ставить власну синю іконку, якщо ми не задали свою.
# Тому підміняємо саме цей крок на нашу іконку з assets/icon.ico.
# У зібраному .exe ресурси лежать у sys._MEIPASS (папка _internal), у
# розробці - поруч із main.py. На macOS/Linux .ico не підходить - там нічого
# не чіпаємо.
def _find_app_icon():
    if not sys.platform.startswith("win"):
        return None
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "assets", "icon.ico")
    return path if os.path.exists(path) else None


APP_ICON_PATH = _find_app_icon()

if APP_ICON_PATH:
    def _set_own_titlebar_icon(self):
        try:
            if not self._iconbitmap_method_called:
                self.iconbitmap(APP_ICON_PATH)
        except Exception:  # noqa: BLE001 - іконка не критична
            pass

    ctk.CTkToplevel._windows_set_titlebar_icon = _set_own_titlebar_icon
    ctk.CTk._windows_set_titlebar_icon = _set_own_titlebar_icon

# Максимальна тривалість відео, яке можна аналізувати - 1 година 10
# хвилин. Довші відео відхиляємо ще на етапі choose_file(), не
# чекаючи, поки користувач витратить час (і токени Gemini) на явно
# завелике відео.
MAX_VIDEO_DURATION_SEC = 70 * 60

# УСІ режими працюють на ОДНІЙ моделі - gemini-3.5-flash-lite. Причина:
# проєкт заточений під дешевизну, доступність і швидкість, а на
# безкоштовному тарифі Google AI Studio головний ліміт - кількість
# ЗАПИТІВ на добу (RPD), а не токени. За даними на вересень 2026 у
# Flash-Lite це ~500 запитів/добу, а у Flash-моделей (3.6/3.8) лише
# ~20 - тобто 1-2 аналізи і ліміт вичерпано. Актуальні цифри дивись
# на сторінці лімітів у своєму AI Studio, вони можуть змінюватись.
ANALYSIS_MODEL_NAME = "gemini-3.5-flash-lite"

# Режим "Швидкий" (без слайдера деталізації) - фіксована кількість
# кадрів і мінімальне "мислення" (це і є дефолт Flash-Lite - найшвидший
# і найдешевший варіант). 200 кадрів - це рівно 5 повних пачок по
# BATCH_SIZE=40 (5 запитів). Для відео до 20-30 хвилин це кадр кожні
# ~6-9 секунд, цього зазвичай досить, а для довших або складних сцен
# є "Детально".
FAST_MODE_FRAMES = 200
FAST_MODE_THINKING_LEVEL = "minimal"

# "Швидкий" - користувач сам сказав "мені не треба максимальна точність,
# прожени швидко", тож віддаємо найдешевшу/найшвидшу роздільність кадру
# для Gemini (MEDIA_RESOLUTION_LOW - 280 токенів/кадр замість 1120 за
# замовчуванням, тобто вчетверо менше токенів на ХВИЛИННИЙ ліміт
# безкоштовного ключа - див. коментар над DEFAULT_MEDIA_RESOLUTION у
# gemini_tools.py). Ціна - дрібні/далекі об'єкти можуть розпізнаватись
# гірше, що для "Швидкого" (і так менше кадрів, minimal thinking) прийнятно.
FAST_MODE_MEDIA_RESOLUTION = "MEDIA_RESOLUTION_LOW"

# Режим "Детально" - 4 фіксовані позиції слайдера. Кожна позиція - це
# (ключ перекладу підпису, thinkingLevel, скільки кадрів вирізати,
# mediaResolution для gemini_tools.find_object_in_frames*).
# Модель та сама (Flash-Lite), тож ріст деталізації - це більше
# кадрів + глибше "мислення" + вища роздільність кадру. Кількості
# кадрів навмисно кратні BATCH_SIZE=40 - інакше остання пачка виходить
# маленькою, а запит на неї коштує як повний (квота на добу рахується
# в запитах). Запитів на аналіз: 400 кадрів = 10, 800 = 20, 1200 = 30,
# 2000 = 50. На відео максимальної тривалості (70 хв) це кадр кожні
# ~10 / 5 / 3.5 / 2 с. Для коротких відео реальних кадрів менше:
# нарізка не частіша за min_interval_sec (0.5 с) у video_tools.extract_frames.
# "high" на Flash-Lite помітно повільніший (багато токенів мислення),
# тому він лише на "Максимальному" - там користувач свідомо обирає
# найповільніший і найуважніший розбір.
#
# mediaResolution росте разом з рівнем так само поступово, як і
# thinkingLevel (замість того, щоб одразу на "Слабкому" давати HIGH):
# LOW на двох перших рівнях (там і так менше кадрів і "low" thinking -
# точність все одно нижча за призначенням), MEDIUM на "Високому",
# HIGH лише на "Максимальному" - де користувач свідомо платить
# найбільше токенів і часу за найповнішу картинку. Токени/кадр:
# LOW=280, MEDIUM=560, HIGH=1120 (див. _TOKENS_PER_FRAME_BY_RESOLUTION
# у gemini_tools.py).
DETAIL_LEVELS = [
    ("detail_level_weak", "minimal", 400, "MEDIA_RESOLUTION_LOW"),
    ("detail_level_medium", "low", 800, "MEDIA_RESOLUTION_LOW"),
    ("detail_level_high", "medium", 1200, "MEDIA_RESOLUTION_MEDIUM"),
    ("detail_level_max", "high", 2000, "MEDIA_RESOLUTION_HIGH"),
]
DEFAULT_DETAIL_LEVEL_INDEX = 1  # "Середній" (800 кадрів) - за замовчуванням

BUTTON_COLOR = "#1F6AA5"            # звичайний колір кнопки у звіті

# Кольори нижче - кортежі (світла тема, темна тема). CustomTkinter сам
# підставляє потрібне значення залежно від поточної теми оформлення,
# тому окремо нічого пересчитувати при перемиканні теми не треба.
#
# Темно-синій (#123A5C) чудово виглядає на темному фоні, але на
# світлому - забагато контрасту і "їсть очі". Тому на світлій темі
# беремо яскравіший, "легший" синій, а на темній лишаємо той самий
# темно-синій, який і був.
BUTTON_COLOR_SELECTED = ("#2E86DE", "#123A5C")   # який момент/модель/тема/мова зараз обрані

# Так само з сірим "gray30": на чорному фоні виглядає добре, а на
# білому - як брудна пляма. На світлій темі беремо світліший,
# блакитнувато-сірий відтінок, на темній лишаємо gray30, як і було.
MODEL_NEUTRAL_COLOR = ("#C4CDD6", "gray30")      # колір кнопки, поки вона НЕ обрана

# Колір ТЕКСТУ на кнопках. Обрана кнопка завжди має досить темний/
# насичений фон (синій, темно-синій), тож білий текст на ній читається
# добре в обох темах. А от неактивна кнопка на світлій темі світло-сіра
# (майже біла) - білий текст на ній зникає, тому там текст має бути
# темним; на темній темі - навпаки, світлим.
TEXT_COLOR_SELECTED = "white"
TEXT_COLOR_NEUTRAL = ("gray10", "gray90")

# "Нейтральні" статусні кольори (підказки, лічильники, "нічого не
# знайдено" тощо). ВАЖЛИВО: тут навмисно кортеж (світла, темна), а НЕ
# одна рядкова назва кольору на кшталт "gray" - CustomTkinter оновлює
# кольір віджета при зміні теми ТІЛЬКИ якщо він заданий кортежем.
# Плоский рядок застосовується один раз і більше ніколи не
# перерахується - через це раніше після перемикання світла->темна
# текст лишався "заморожений" у кольорі з попередньої теми.
STATUS_COLOR_NEUTRAL = ("gray45", "gray65")
STATUS_COLOR_ERROR = ("#C0392B", "#FF6B60")

# Жовтий - для попереджень, які НЕ є помилкою (напр. невідома
# тривалість відео - можливо, все гаразд, просто не вдалось
# визначити). На відміну від STATUS_COLOR_ERROR, це не "щось
# зламалось", а "будь уважний".
WARNING_COLOR = ("#B8860B", "#F5C242")

# Типи аналізу на вибір користувачу - ключ перекладу підпису кнопки і
# ключ РЕЖИМУ (а не назва моделі: модель в обох режимах одна й та сама,
# див. ANALYSIS_MODEL_NAME, тому за назвою моделі режими не розрізнити).
MODE_FAST = "fast"        # без слайдера деталізації, FAST_MODE_FRAMES кадрів
MODE_DETAIL = "detail"    # 4 рівні деталізації (DETAIL_LEVELS)
MODEL_OPTIONS = [
    ("model_fast_label", MODE_FAST),
    ("model_detail_label", MODE_DETAIL),
]

# Теми оформлення на вибір. Ключ зліва - те, що зберігається в
# settings.json; значення справа - те, що розуміє customtkinter.
# "System" - це і є "Авто": ctk сам визначає системну тему один раз
# при старті і застосовує, а от Light/Dark лишаються примусовими і не
# міняються, навіть якщо користувач потім перемкне тему в системі.
THEME_MODES = {
    "auto": "System",
    "light": "Light",
    "dark": "Dark",
}
THEME_OPTIONS = [
    ("auto", "theme_auto"),
    ("light", "theme_light"),
    ("dark", "theme_dark"),
]

# Мови інтерфейсу на вибір - код мови і підпис на кнопці (підпис
# навмисно не перекладається, це лише скорочення UA / EN).
LANGUAGE_OPTIONS = [
    ("ua", "UA"),
    ("en", "EN"),
]

# Тип API-ключа Gemini - ОБОВ'ЯЗКОВЕ поле в меню. При ПЕРШОМУ запуску не
# обрано нічого (DEFAULT_API_TIER = None): аналіз не стартує, доки
# користувач сам не обере "Безкоштовний" або "Платний" (див.
# start_analysis). Після вибору значення зберігається в settings.json і
# більше не скидається. ЧОМУ це потрібно: у
# gemini_tools.py дроселювання запитів (паралелізм/затримки між
# викликами) розраховане під ліміти БЕЗКОШТОВНОГО тарифу Gemini
# (RPM/RPD free-tier) - якщо в користувача ПЛАТНИЙ ключ із набагато
# вищою стелею, той самий дросель штучно душить його під безкоштовний
# ліміт, хоча реального 429 від Google на платному ключі могло б і не
# бути. Тому вибір тарифу винесено в явну, обов'язкову настройку - без
# здогадування за одним лише фактом наявності ключа (ключ сам по собі
# не каже, платний він чи безкоштовний).
API_TIER_OPTIONS = [
    ("free", "api_tier_free"),
    ("paid", "api_tier_paid"),
]
DEFAULT_API_TIER = None   # нічого не обрано, доки користувач не вибере сам
VALID_API_TIERS = {key for key, _ in API_TIER_OPTIONS}   # {"free", "paid"}

RESIZE_DEBOUNCE_MS = 60             # затримка перед перерахунком розміру фото
LIGHTBOX_PADDING = 40               # відступ навколо фото в повноекранному режимі

# Звичайний Tkinter не вміє робити справжню напівпрозорість одного
# віджета поверх іншого (це працює лише для цілих вікон).
# Тому замість "чорний з прозорістю 50%" беремо просто темно-сірий
# колір - виглядає як затемнення, а не суцільна чорнота.
LIGHTBOX_BG_COLOR = "#1a1a1a"

# Кроки зуму в повноекранному режимі: 100% -> 150% -> ... -> 500%
ZOOM_LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]

# Кольори кнопки "Меню" - фіксовані (не залежать від теми), як у
# мобільній версії: помаранчева кнопка добре виділяється на будь-якому
# фоні і завжди легко впізнається як "тут усі налаштування".
MENU_BUTTON_COLOR = "#ED8221"
MENU_BUTTON_HOVER_COLOR = "#FF9E4D"

# Кольори кнопки скидання API-ключа (і в меню, і в історії кадрів) -
# явно "небезпечний" червоний, щоб було видно, що дія руйнівна.
DANGER_COLOR = "#C0392B"
DANGER_HOVER_COLOR = "#A93226"

# Колір кнопки "Налаштування" (рівень деталізації) - бордовий, щоб
# явно відрізнятись і від синіх кнопок моделі, і від помаранчевої
# "Меню", і від червоного DANGER_COLOR (це не небезпечна дія).
SETTINGS_BUTTON_COLOR = "#7B2D3B"
SETTINGS_BUTTON_HOVER_COLOR = "#642230"


def _patch_ctk_textbox_scrollbar_check():
    """
    customtkinter (5.2.x): CTkTextbox раз на ~200 мс сам себе перепланує
    _check_if_scrollbars_needed() через after(). Коли віджет уже знищено
    (закрили діалог або все вікно), наступний виклик падає з
    TclError: invalid command name ".!ctktextbox.!text" - і Tkinter
    друкує "Exception in Tkinter callback" у консоль. Обгортаємо метод:
    якщо віджета вже нема - тихо виходимо (і цикл перепланування
    зупиняється сам). Безпечно, якщо в іншій версії customtkinter
    методу нема - тоді нічого не робимо.
    """
    original = getattr(ctk.CTkTextbox, "_check_if_scrollbars_needed", None)
    if original is None or getattr(original, "_is_safe_wrapper", False):
        return

    def safe_check(self, *args, **kwargs):
        try:
            if not self.winfo_exists():
                return None
            return original(self, *args, **kwargs)
        except tk.TclError:
            return None

    safe_check._is_safe_wrapper = True
    ctk.CTkTextbox._check_if_scrollbars_needed = safe_check


_patch_ctk_textbox_scrollbar_check()


class App(*_APP_BASE_CLASSES):
    def __init__(self):
        super().__init__()
        self._wipe_on_exit = False   # true - після закриття вікна видалити всі дані користувача
        self._closing = False   # true, коли вікно закривається (див. shutdown / report_callback_exception)

        # ---- налаштування (тема, мова), завантажені з settings.json ----
        self.settings = load_settings()
        # api_tier - нове поле; старі settings.json (до цієї версії) його
        # не мають, тож м'яко підставляємо дефолт, а не покладаємось на
        # DEFAULT_SETTINGS у settings_store.py (там теж варто додати цей
        # ключ, але без .get() тут програма впала б на КОЖНОГО, хто
        # оновився зі старим settings.json).
        self.settings.setdefault("api_tier", DEFAULT_API_TIER)
        # Дійсне значення ("free"/"paid") лишаємо як є - воно зберігається
        # між запусками. Усе інше (None, сміття) = "ще не обрано".
        if self.settings["api_tier"] not in VALID_API_TIERS:
            self.settings["api_tier"] = None
        self.loc = Localization(self.settings["language"])
        self._install_extra_strings()
        ctk.set_appearance_mode(THEME_MODES.get(self.settings["theme"], "System"))
        ctk.set_default_color_theme("blue")

        self.title(self.loc.t("window_title"))
        self.geometry("950x650")
        self.minsize(700, 500)
        if APP_ICON_PATH:
            try:
                self.iconbitmap(APP_ICON_PATH)
            except Exception:  # noqa: BLE001
                pass
            # Чіткі іконки (заголовок + панель завдань) напряму через WinAPI.
            # Із затримкою: customtkinter ~200 мс після старту ставить свою
            # іконку, тож ми перебиваємо її вже ПІСЛЯ нього (+ повтор про запас).
            self.after(400, lambda: app_paths.apply_crisp_window_icon(self, APP_ICON_PATH))
            self.after(1500, lambda: app_paths.apply_crisp_window_icon(self, APP_ICON_PATH))

        self.video_path = None         # шлях до обраного відео (поки не обрано - None)
        self.report_buttons = []       # (кнопка, result) - для підсвітки обраного моменту
        self.current_pil_image = None  # оригінал відкритої картинки (для перерахунку розміру)
        self.current_result = None     # який саме момент звіту зараз показано

        # Прапорець скасування поточного аналізу. Сам HTTP-запит, який
        # вже пішов, зупинити неможливо - але extract_frames() і
        # find_object_in_frames() (video_tools.py / gemini_tools.py)
        # перевіряють його на кожному кроці і самі обриваються, тільки-но
        # він стає True, замість того щоб чесно доробити все до кінця.
        self._cancel_event = threading.Event()
        self._analysis_running = False   # чи йде зараз фоновий потік аналізу

        self.selected_mode = None      # який тип аналізу обрав користувач: MODE_FAST / MODE_DETAIL (None = ще не обрав)
        self.model_buttons = []        # (кнопка, model_name, label_key) - для підсвітки обраної моделі й перемалювання підпису при зміні мови
        self.theme_buttons = []        # (кнопка, theme_key) - для підсвітки обраної теми (заповнюється при відкритті меню)

        # ---- рівень деталізації режиму "Детально" (слайдер 0..3) ----
        # Якщо remember_detail_level=False - при кожному запуску
        # стартуємо з DEFAULT_DETAIL_LEVEL_INDEX ("Середній"), а не з
        # того, що лежить у settings.json з минулого разу (детальніше
        # див. коментар над DEFAULT_SETTINGS у settings_store.py).
        self.remember_detail_level = self.settings.get("remember_detail_level", False)
        if self.remember_detail_level:
            self.detail_level_index = self.settings.get("detail_level_index", DEFAULT_DETAIL_LEVEL_INDEX)
        else:
            self.detail_level_index = DEFAULT_DETAIL_LEVEL_INDEX
        # Стан вікна "Налаштування" (слайдер деталізації) - так само,
        # як меню/про застосунок: не персистентне, створюється заново.
        self._detail_dialog = None
        self._detail_title_label = None
        self._detail_info_btn = None
        self._detail_slider = None
        self._detail_level_label = None
        self._detail_frames_label = None
        self._detail_remember_checkbox = None
        self._detail_remember_var = None
        self._detail_info_dialog = None
        self._detail_info_body_label = None

        # ---- стан вікна "Про типи аналізу" (Швидкий / Детально) ----
        self._model_info_dialog = None
        self._model_info_body_label = None
        self._detail_caption_labels = []
        self.language_buttons = []     # (кнопка, lang_code) - для підсвітки обраної мови (те саме)

        # ---- стан вікна "Меню" (тема/мова/API-ключ/історія) ----
        # Меню - НЕ персистентний віджет, а створюється заново щоразу,
        # коли його відкривають (простіше, ніж тримати сховане вікно
        # весь час роботи програми). Тому посилання на нього і на
        # елементи всередині зберігаємо тут, щоб _apply_language() міг
        # оновити текст, якщо меню зараз відкрите.
        self._menu_dialog = None
        self._menu_title_label = None
        self._menu_about_btn = None
        self._menu_exit_btn = None
        self._menu_theme_label = None
        self._menu_lang_label = None
        self._menu_history_btn = None
        self._menu_reset_key_btn = None
        self._menu_wipe_btn = None

        # ---- стан вікна "Про застосунок" ----
        self._about_dialog = None
        self._about_content = None

        # ---- стан вікна історії аналізів (список сесій, верхній рівень) ----
        self._history_window = None
        self._history_sessions = []
        self._history_selection_mode = False
        self._history_selected_ids = set()  # ids обраних СЕСІЙ
        self._history_scroll = None
        self._history_delete_btn = None
        self._history_select_all_var = None
        self._history_select_all_frame = None
        self._history_select_all_chk = None

        # ---- стан вікна кадрів ОДНІЄЇ сесії (відкривається з вікна вище) ----
        self._history_session_window = None
        self._history_current_session = None
        self._history_session_frames = []
        self._history_frame_selection_mode = False
        self._history_frame_selected_ids = set()  # ids обраних КАДРІВ
        self._history_frame_scroll = None
        self._history_frame_delete_btn = None
        self._history_frame_select_all_var = None
        self._history_frame_select_all_frame = None
        self._history_frame_select_all_chk = None
        self._history_session_export_btn = None

        # ---- стан для коректного перемикання мови "на льоту" ----
        # Частину текстів (статус, "нічого не знайдено") треба вміти
        # перемалювати заново вже іншою мовою, тому запам'ятовуємо не
        # готовий текст, а ключ перекладу + параметри до нього.
        self._status_key = None
        self._status_kwargs = {}
        self._status_color = STATUS_COLOR_NEUTRAL
        self._no_results_label = None   # лейбл "нічого не знайдено" в звіті (якщо показаний)
        self._last_prompt_text = ""     # останній текстовий запит - потрібен для опису "нічого не знайдено"
        self._photo_state = "placeholder"  # "placeholder" | "image" | "no_results"

        self._resize_job = None        # id відкладеного перерахунку розміру (для debounce)

        # ---- прогрес аналізу (капсула замість голого тексту) ----
        self.progress_frame = None
        self.progress_bar = None
        self.progress_percent_label = None
        # displayed - те, що РЕАЛЬНО намальовано зараз (плавно "доганяє"
        # target через _animate_progress_step). target - куди йдемо.
        self._progress_target = 0
        self._progress_displayed = 0.0
        self._progress_anim_job = None  # id відкладеного after() кроку анімації, якщо йде
        self._progress_hide_job = None  # id відкладеного after() "сховати після 100%" (show_results), якщо йде

        # ---- "підкрутка" прогресу під час очікування Gemini (див. коментар над PROGRESS_TRICKLE_*) ----
        self._progress_trickle_ceiling = None  # None = підкрутка вимкнена
        self._progress_trickle_job = None      # id відкладеного after() тіку підкрутки, якщо йде

        # ---- стан повноекранного перегляду (лайтбоксу) ----
        self.lightbox_frame = None     # сама повноекранна підкладка, якщо відкрита
        self.lightbox_canvas = None    # канвас з фото всередині підкладки
        self.lightbox_photo = None     # посилання на поточну PhotoImage (щоб її не видалив GC)
        self.lightbox_image_item = None  # id картинки на канвасі
        self.lightbox_caption_label = None  # текст підпису (опис) у верхній панелі лайтбоксу
        self.lightbox_time_label = None     # час у підписі лайтбоксу
        self._suspend_watchdog = None   # сторож простою системи (сон) - див. keep_awake.py
        self.zoom_index = 0            # індекс поточного зуму в ZOOM_LEVELS
        self.pan_x = 0                 # зміщення фото по X (перетягування мишкою)
        self.pan_y = 0                 # зміщення фото по Y
        self._drag_start = None        # звідки почали тягнути мишкою

        self._build_interface()

        # Drag-and-drop файлу з ОС - викликаємо ПІСЛЯ побудови
        # інтерфейсу: оверлей "відпустіть тут" кладеться (place) поверх
        # уже готових віджетів, а не в порожнє вікно.
        self._setup_drag_and_drop()

        # Enter в полі промпту - запускає аналіз (замість переносу рядка)
        self.prompt_box.bind("<Return>", self._on_prompt_enter)
        self.prompt_box.bind("<Shift-Return>", lambda e: None)  # Shift+Enter - звичайний перенос рядка

        # Стрілки - навігація по звіту: вліво/вгору = попередній момент,
        # вправо/вниз = наступний. Прив'язано на все вікно, але не заважає
        # друкувати в полі промпту (див. _is_typing_in_prompt).
        self.bind("<Left>", lambda e: self._navigate_report(-1))
        self.bind("<Up>", lambda e: self._navigate_report(-1))
        self.bind("<Right>", lambda e: self._navigate_report(1))
        self.bind("<Down>", lambda e: self._navigate_report(1))

        # За замовчуванням хрестик вікна викликає destroy() НАПРЯМУ, поки
        # mainloop ще крутиться. У customtkinter у фоні через after()
        # висить періодична перевірка DPI-масштабування
        # (check_dpi_scaling) - вона встигає спрацювати вже на
        # знищеному вікні і падає з TclError "application has been
        # destroyed" (нешкідливо по суті, але засмічує вивід і псує
        # exit code). Тому спочатку зупиняємо mainloop через
        # quit() - після цього Tkinter перестає обробляти
        # відкладені after-колбеки, і лише потім спокійно знищуємо
        # вікно через destroy().
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

        # Перевіряємо наявність API-ключа з невеликою затримкою (after),
        # а не одразу в __init__ - вікно встигає повністю намалюватись,
        # і модальне вікно налаштувань лягає поверх уже видимого вікна,
        # а не поверх порожнього екрану під час запуску.
        self.after(150, self._on_start)

    def _on_close_window(self):
        self._closing = True
        self._cancel_event.set()   # якщо аналіз ще йде - просимо фоновий потік зупинитись
        self.quit()

    def report_callback_exception(self, exc, val, tb):
        """
        Під час закриття вікна ті відкладені after()-колбеки customtkinter
        (перевірка DPI-масштабу check_dpi_scaling, скролбари CTkTextbox),
        що ще встигли спрацювати на вже знищеному вікні, кидають
        TclError ("application has been destroyed" / "invalid command
        name"). Це нешкідливо, тож під час закриття їх не друкуємо;
        решту винятків - як завжди.
        """
        if getattr(self, "_closing", False) and isinstance(val, tk.TclError):
            return
        super().report_callback_exception(exc, val, tb)

    def shutdown(self):
        """
        Акуратне завершення після виходу з mainloop(): спершу скасовуємо
        УСІ відкладені after()-виклики цього інтерпретатора Tcl (свої і
        customtkinter-івські), і лише потім знищуємо вікно. Без цього
        деякі з них (особливо на macOS, де знищення вікна може ще раз
        прокрутити цикл подій) встигають спрацювати вже на мертвому
        вікні і засмічують консоль помилками TclError.
        """
        self._closing = True
        self._cancel_event.set()
        try:
            pending_jobs = self.tk.splitlist(self.tk.call("after", "info"))
        except tk.TclError:
            pending_jobs = ()
        for job in pending_jobs:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        try:
            self.destroy()
        except tk.TclError:
            pass
        if self._wipe_on_exit:
            self._wipe_data_now()

    def _install_extra_strings(self):
        """Підмішує EXTRA_STRINGS у self.loc.t(), не чіпаючи localization.py."""
        original_t = self.loc.t

        def t(key, **kwargs):
            extra = EXTRA_STRINGS.get(self.loc.language, EXTRA_STRINGS["en"]).get(key)
            if extra is not None:
                return extra.format(**kwargs) if kwargs else extra
            return original_t(key, **kwargs)

        self.loc.t = t

    def _on_start(self):
        """Перший запуск - спершу вітальне вікно, потім перевірка ключа."""
        if self.settings.get("welcome_shown"):
            self._ensure_api_key()
        else:
            self._open_welcome_dialog()

    def _open_welcome_dialog(self):
        """Вітальне вікно, показується один раз (прапорець welcome_shown)."""
        dialog = self._create_modal_dialog(self.loc.t("welcome_title"), 460, 440)

        def on_close():
            self.settings["welcome_shown"] = True
            save_settings(self.settings)
            dialog.destroy()
            self._ensure_api_key()

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        # Кнопка внизу пакується першою, щоб завжди лишатись видимою.
        ctk.CTkButton(
            dialog, text=self.loc.t("dialog_understood"), command=on_close
        ).pack(side="bottom", pady=(0, 24))

        ctk.CTkLabel(
            dialog, text=self.loc.t("welcome_title"),
            font=ctk.CTkFont(size=22, weight="bold"), wraplength=400,
        ).pack(padx=26, pady=(26, 8))
        ctk.CTkLabel(
            dialog, text=self.loc.t("welcome_text"), wraplength=400,
            justify="left", text_color=STATUS_COLOR_NEUTRAL,
        ).pack(padx=26, pady=(0, 16))

        for number in range(1, 4):
            ctk.CTkLabel(
                dialog, text="\u2022  " + self.loc.t("welcome_step_" + str(number)),
                wraplength=400, justify="left", anchor="w",
            ).pack(fill="x", padx=26, pady=3)

        ctk.CTkLabel(
            dialog, text=self.loc.t("welcome_note"), wraplength=400,
            justify="left", anchor="w", text_color=STATUS_COLOR_NEUTRAL,
        ).pack(fill="x", padx=26, pady=(16, 0))

    def _ensure_api_key(self):
        """Якщо ключа нема взагалі (ні вшитого, ні власного) - відкриває
        вікно налаштувань ключа. У збірці з вшитим ключем це не спрацює."""
        if not config.load_api_key():
            self._open_api_key_dialog(is_first_setup=True)

    def _build_interface(self):
        # Єдині шрифти для рядків звіту (час - жирний, текст - звичайний).
        self.font_report_time = ctk.CTkFont(size=13, weight="bold")
        self.font_report_text = ctk.CTkFont(size=13)
        self.font_report_time_big = ctk.CTkFont(size=16, weight="bold")
        self.font_report_text_big = ctk.CTkFont(size=16)

        # ---------- Верхня панель: лише кнопка "Меню" ліворуч ----------
        # Тема, мова, зміна API-ключа і перегляд історії кадрів раніше
        # висіли тут окремими рядками - тепер усе це заховано за однією
        # кнопкою "Меню" (як у мобільній версії), а верх вікна лишається
        # чистим і не відволікає від самої роботи з відео.
        top_bar = ctk.CTkFrame(self, fg_color="transparent")
        top_bar.pack(fill="x", padx=15, pady=(15, 0))

        self.menu_button = ctk.CTkButton(
            top_bar,
            text=self.loc.t("menu_button"),
            width=90,
            fg_color=MENU_BUTTON_COLOR,
            hover_color=MENU_BUTTON_HOVER_COLOR,
            text_color="white",
            command=self._open_menu,
        )
        self.menu_button.pack(side="left")

        # ---------- Вибір відео + скидання ----------
        top_frame = ctk.CTkFrame(self)
        top_frame.pack(fill="x", padx=15, pady=(10, 5))

        self.file_button = ctk.CTkButton(
            top_frame, text=self.loc.t("choose_video"), command=self.choose_file
        )
        self.file_button.pack(side="left")

        self.file_label = ctk.CTkLabel(top_frame, text=self.loc.t("no_file_selected"))
        self.file_label.pack(side="left", padx=10)

        # ---------- Поле для текстового запиту ----------
        prompt_header = ctk.CTkFrame(self, fg_color="transparent")
        prompt_header.pack(fill="x", padx=15, pady=(10, 0))

        self.search_prompt_label_widget = ctk.CTkLabel(prompt_header, text=self.loc.t("search_prompt_label"))
        self.search_prompt_label_widget.pack(side="left")

        self.word_count_label = ctk.CTkLabel(
            prompt_header, text=self.loc.t("char_count", count=0, max=MAX_PROMPT_CHARS), text_color=STATUS_COLOR_NEUTRAL
        )
        self.word_count_label.pack(side="right")

        self.prompt_box = ctk.CTkTextbox(self, height=80)
        self.prompt_box.pack(fill="x", padx=15, pady=5)
        self.prompt_box.bind("<KeyRelease>", self.update_char_count)
        # <<Modified>> спрацьовує на ЛЮБУ зміну тексту (друк, вставка, drag-and-drop) -
        # саме тут обрізаємо все, що довше за MAX_PROMPT_CHARS.
        self.prompt_box._textbox.bind("<<Modified>>", self._on_prompt_modified, add="+")
        self._bind_clipboard_shortcuts(self.prompt_box)

        # ---------- Кнопка відправки + кнопка зупинки аналізу ----------
        # Обидві кнопки лежать в одному рядку по центру, але видима
        # завжди лише ОДНА з них: "Почати аналіз" - поки нічого не
        # виконується, "Зупинити аналіз" (червона) - поки йде фоновий
        # потік run_pipeline(). Перемикання - через _set_analysis_running().
        submit_row = ctk.CTkFrame(self, fg_color="transparent")
        submit_row.pack(pady=10)

        self.submit_button = ctk.CTkButton(
            submit_row, text=self.loc.t("start_analysis"), command=self.start_analysis
        )
        self.submit_button.pack()

        self.stop_button = ctk.CTkButton(
            submit_row,
            text=self.loc.t("stop_analysis"),
            fg_color=DANGER_COLOR,
            hover_color=DANGER_HOVER_COLOR,
            command=self.stop_analysis,
        )
        # Свідомо НЕ .pack() тут - з'являється лише на час аналізу,
        # див. _set_analysis_running(True) у start_analysis().

        # ---------- Статус зліва, вибір моделі справа ----------
        status_row = ctk.CTkFrame(self, fg_color="transparent")
        status_row.pack(fill="x", padx=15)

        self.status_label = ctk.CTkLabel(status_row, text="")
        self.status_label.pack(side="left")

        # Капсула прогресу аналізу - схована (не запакована), поки
        # аналіз не йде; показується _set_progress() нижче. Разом з
        # відсотком поруч - так само, як маленький індикатор заряду
        # в мобільній версії.
        self.progress_frame = ctk.CTkFrame(status_row, fg_color="transparent")

        self.progress_bar = ctk.CTkProgressBar(
            self.progress_frame, width=90, height=10, progress_color=PROGRESS_BAR_COLOR,
            # corner_radius=0 - за замовчуванням CTkProgressBar заокруглює
            # краї індикатора виконання, і той заокруглений "хвостик"
            # лишається видимим навіть при value=0 (особливо на такому
            # вузькому 90px барі це виглядає як частково заповнений
            # прогрес, хоча реальне значення - чесний нуль). Пласкі,
            # прямі краї цю оптичну ілюзію прибирають.
            corner_radius=0,
        )
        self.progress_bar.set(0)
        self.progress_bar.pack(side="left")

        self.progress_percent_label = ctk.CTkLabel(self.progress_frame, text="", width=36)
        self.progress_percent_label.pack(side="left", padx=(6, 0))

        model_frame = ctk.CTkFrame(status_row, fg_color="transparent")
        model_frame.pack(side="right")

        self.choose_model_label_widget = ctk.CTkLabel(model_frame, text=self.loc.t("choose_model_label"))
        self.choose_model_label_widget.pack(side="left", padx=(0, 2))

        self.model_info_btn = ctk.CTkButton(
            model_frame, text=self.loc.t("menu_info_button"), width=22, height=22,
            corner_radius=11, fg_color="transparent",
            hover_color=("gray85", "gray25"),
            text_color=("gray30", "gray70"),
            font=ctk.CTkFont(size=14),
            command=self._open_model_info_dialog,
        )
        self.model_info_btn.pack(side="left", padx=(0, 8))

        for label_key, mode in MODEL_OPTIONS:
            btn = ctk.CTkButton(
                model_frame,
                text=self.loc.t(label_key),
                width=90,
                fg_color=MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_NEUTRAL,
                command=lambda mode=mode: self._select_mode(mode),
            )
            btn.pack(side="left", padx=3)
            self.model_buttons.append((btn, mode, label_key))

        # Кнопка "Налаштування" (рівень деталізації) - видима лише коли
        # обрано режим "Детально" (у нього є слайдер, у "Швидкий" нема).
        # Ховається/показується в _select_mode().
        self.detail_settings_button = ctk.CTkButton(
            model_frame,
            text=self.loc.t("settings_button"),
            width=110,
            fg_color=SETTINGS_BUTTON_COLOR,
            hover_color=SETTINGS_BUTTON_HOVER_COLOR,
            text_color="white",
            command=self._open_detail_settings,
        )

        # ---------- Звіт: зліва список знайдених моментів, справа фото ----------
        # "Очистити все" - небезпечна дія, тому внизу вікна, окремо від основних
        # кнопок. Пакується ПЕРШИМ (side=bottom), щоб звіт займав решту місця.
        bottom_bar = ctk.CTkFrame(self, fg_color="transparent")
        bottom_bar.pack(side="bottom", fill="x", padx=15, pady=(0, 12))

        self.clear_button = ctk.CTkButton(
            bottom_bar,
            text=self.loc.t("clear_all"),
            fg_color="transparent",
            border_width=1,
            border_color=DANGER_COLOR,
            text_color=STATUS_COLOR_ERROR,
            hover_color=("gray85", "gray20"),
            command=self._confirm_reset_all,
        )
        self.clear_button.pack(side="right")

        results_frame = ctk.CTkFrame(self)
        results_frame.pack(fill="both", expand=True, padx=15, pady=(5, 8))

        self.report_list = ctk.CTkScrollableFrame(
            results_frame, width=280, label_text=self.loc.t("report_label")
        )
        self.report_list.pack(side="left", fill="y", padx=(0, 10), pady=5)

        right_panel = ctk.CTkFrame(results_frame, fg_color="transparent")
        right_panel.pack(side="left", fill="both", expand=True, pady=5)

        # Підпис моменту: час (жирний) у колонці фіксованої ширини + текст праворуч -
        # той самий стиль, що й у списку зліва.
        self.caption_frame = ctk.CTkFrame(right_panel, fg_color="transparent")
        self.caption_frame.pack(pady=(0, 5), anchor="w", fill="x")
        self.caption_frame.grid_columnconfigure(1, weight=1)

        self.caption_time_label = ctk.CTkLabel(
            self.caption_frame, text="", width=REPORT_TIME_COL_WIDTH,
            font=self.font_report_time, anchor="w", justify="left",
        )
        self.caption_time_label.grid(row=0, column=0, sticky="nw", padx=(0, 8))
        self.caption_time_label.grid_remove()

        self.description_label = ctk.CTkLabel(
            self.caption_frame, text="", wraplength=470, justify="left",
            anchor="w", font=self.font_report_text,
        )
        self.description_label.grid(row=0, column=1, sticky="nw")

        # ---------- Кнопки експорту в PDF (неактивні, поки нема звіту) ----------
        export_row = ctk.CTkFrame(right_panel, fg_color="transparent")
        export_row.pack(fill="x", pady=(0, 2), anchor="w")

        self.export_pdf_button = ctk.CTkButton(
            export_row,
            text=self.loc.t("export_pdf_button"),
            width=180,
            state="disabled",
            command=self.export_current_pdf,
        )
        self.export_pdf_button.pack(side="left")

        self.export_full_report_button = ctk.CTkButton(
            export_row,
            text=self.loc.t("export_full_report_button"),
            width=180,
            fg_color="transparent",
            border_width=1,
            text_color=("gray10", "gray90"),
            state="disabled",
            command=self.export_full_report_pdf,
        )
        self.export_full_report_button.pack(side="left", padx=(8, 0))

        self.export_hint_label = ctk.CTkLabel(
            right_panel, text=self.loc.t("export_hint"), wraplength=470,
            justify="left", anchor="w", text_color=STATUS_COLOR_NEUTRAL,
            font=ctk.CTkFont(size=11),
        )
        self.export_hint_label.pack(fill="x", pady=(0, 8))

        # Контейнер під фото - саме ЙОГО розмір ми відстежуємо, щоб
        # знати, скільки місця реально є під картинку прямо зараз.
        self.photo_container = ctk.CTkFrame(right_panel, fg_color="transparent")
        self.photo_container.pack(fill="both", expand=True)
        self.photo_container.bind("<Configure>", self._on_photo_container_resize)

        self._create_photo_label()

    def _paste_clipboard_into(self, widget):
        """
        Вставляє вміст буфера обміну у widget (CTkEntry або CTkTextbox)
        на місце курсора, заміняючи поточне виділення, якщо воно є.

        Винесено в окремий метод (а не лишено лише всередині
        _bind_clipboard_shortcuts), щоб цю саму логіку можна було
        викликати НАПРЯМУ з кнопки "Вставити" - на випадок, якщо
        Cmd+V з якоїсь причини не спрацьовує в конкретному Tcl/Tk
        білді на конкретному Маку (біндинг на клавіші тоді ні до чого,
        а от клік по кнопці спрацює завжди).

        Повертає True, якщо щось справді вставилось, False - якщо
        буфер обміну порожній / без тексту.
        """
        is_entry_like = hasattr(widget, "select_present")

        # Спершу пробуємо звичний "STRING" (дефолт clipboard_get()), а
        # якщо його нема - "UTF8_STRING". На macOS більшість
        # застосунків (Safari, Chrome, Notes і т.д.) кладуть
        # скопійований текст саме як UTF8_STRING, а не як "голий"
        # STRING - без цього фолбека clipboard_get() мовчки падає з
        # TclError.
        clipboard_text = None
        for clip_type in (None, "UTF8_STRING"):
            try:
                clipboard_text = (
                    widget.clipboard_get(type=clip_type) if clip_type
                    else widget.clipboard_get()
                )
                break
            except tk.TclError:
                continue

        if clipboard_text is None:
            return False  # у буфері обміну справді нема тексту жодного відомого типу

        has_selection = widget.select_present() if is_entry_like else bool(widget.tag_ranges("sel"))
        if has_selection:
            widget.delete("sel.first", "sel.last")
        widget.insert("insert", clipboard_text)
        return True

    def _bind_clipboard_shortcuts(self, widget):
        """
        Прив'язує Copy/Cut/Paste/"Виділити все" до текстового поля
        (CTkEntry або CTkTextbox) вручну, через реальні виклики
        clipboard_get/clipboard_append замість стандартних Tk
        virtual-подій (<<Copy>> тощо).

        Причина: на macOS Tk за замовчуванням не прив'язує Cmd+C/V/X/A
        до текстових полів (лише Control-, який на Mac ніхто не
        використовує для копіювання) - тому без цієї прив'язки вставити
        скопійований API-ключ чи текст запиту можна було лише вручну
        передрукувавши його. Прив'язуємо одразу і Command-, і
        Control- - друге вже й так зазвичай працює на Windows/Linux,
        але зайва прив'язка нічому не заважає.

        widget має підтримувати або "Entry"-подібний API
        (select_present/selection_get/select_range - CTkEntry), або
        "Text"-подібний (tag_ranges("sel")/tag_add - CTkTextbox).
        Визначаємо, який саме, за наявністю select_present.
        """
        is_entry_like = hasattr(widget, "select_present")

        def has_selection():
            if is_entry_like:
                return widget.select_present()
            return bool(widget.tag_ranges("sel"))

        def get_selection():
            if is_entry_like:
                return widget.selection_get()
            return widget.get("sel.first", "sel.last")

        def delete_selection():
            if is_entry_like:
                widget.delete("sel.first", "sel.last")
            else:
                widget.delete("sel.first", "sel.last")

        def do_copy(event=None):
            if has_selection():
                widget.clipboard_clear()
                widget.clipboard_append(get_selection())
            return "break"

        def do_cut(event=None):
            if has_selection():
                widget.clipboard_clear()
                widget.clipboard_append(get_selection())
                delete_selection()
            return "break"

        def do_paste(event=None):
            self._paste_clipboard_into(widget)
            return "break"

        def do_select_all(event=None):
            if is_entry_like:
                widget.select_range(0, "end")
                widget.icursor("end")
            else:
                widget.tag_add("sel", "1.0", "end-1c")
            return "break"

        for modifier in ("Command", "Control"):
            widget.bind(f"<{modifier}-c>", do_copy)
            widget.bind(f"<{modifier}-x>", do_cut)
            widget.bind(f"<{modifier}-v>", do_paste)
            widget.bind(f"<{modifier}-a>", do_select_all)

        # Windows: при українській/російській розкладці keysym - кириличний
        # ("м", "с"...), тож <Control-v> не спрацьовує і вставка/копіювання
        # "мовчки" не працюють. Ловимо за кодом клавіші (virtual-key code
        # не залежить від розкладки): A=65, C=67, V=86, X=88.
        if sys.platform.startswith("win"):
            win_keycode_actions = {65: do_select_all, 67: do_copy, 86: do_paste, 88: do_cut}

            def on_ctrl_keypress(event):
                action = win_keycode_actions.get(event.keycode)
                if action is not None:
                    return action(event)
                return None

            widget.bind("<Control-KeyPress>", on_ctrl_keypress, add="+")

    # ---------- Модальні діалоги: ЄДИНА точка створення й центрування ----------
    def _window_scaling(self, window):
        """Масштаб DPI для вікна (на Windows з 125%/150% != 1.0)."""
        try:
            return ctk.ScalingTracker.get_window_scaling(window)
        except Exception:  # noqa: BLE001
            return 1.0

    def _center_position(self, width_px, height_px):
        """Лівий верхній кут (x, y) для вікна width_px x height_px (у
        ФІЗИЧНИХ пікселях) так, щоб воно стояло по центру головного вікна.
        Якщо головне згорнуте - центруємо по екрану. Не виходимо за екран."""
        self.update_idletasks()
        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        try:
            minimized = self.state() == "iconic"
        except Exception:  # noqa: BLE001
            minimized = False
        if minimized or self.winfo_width() <= 1:
            area_x, area_y, area_w, area_h = 0, 0, screen_w, screen_h
        else:
            area_x, area_y = self.winfo_rootx(), self.winfo_rooty()
            area_w, area_h = self.winfo_width(), self.winfo_height()
        x = area_x + (area_w - width_px) // 2
        y = area_y + (area_h - height_px) // 2
        x = max(0, min(x, screen_w - width_px))
        y = max(0, min(y, screen_h - height_px - 40))  # 40 - запас під панель задач/док
        return x, y

    def _centered_geometry(self, dialog, width, height):
        """Рядок geometry \"WxH+X+Y\" (width/height - логічні, як в
        CTk .geometry) для центрування по головному вікну."""
        scale = self._window_scaling(dialog)
        x, y = self._center_position(int(width * scale), int(height * scale))
        return f"{width}x{height}+{x}+{y}"

    def _center_dialog_to_content(self, dialog):
        """Центрує вже наповнений діалог (розмір - за вмістом)."""
        dialog.update_idletasks()
        x, y = self._center_position(dialog.winfo_reqwidth(), dialog.winfo_reqheight())
        dialog.geometry(f"+{x}+{y}")

    def _grab_dialog(self, dialog):
        """grab_set + фокус. На Linux grab падає, поки вікно не видно."""
        dialog.update_idletasks()
        try:
            dialog.grab_set()
        except tk.TclError:
            try:
                dialog.wait_visibility()
                dialog.grab_set()
            except tk.TclError:
                pass
        dialog.focus_force()

    def _create_modal_dialog(self, title, width, height, *, minsize=None, grab=True,
                             parent=None, on_shown=None):
        """Створює модальне вікно ПО ЦЕНТРУ головного. Усі діалоги
        застосунку (меню, \"Про застосунок\", налаштування, історія, ключ
        API...) створюються тільки через цю функцію.

        Позицію задаємо ОДРАЗУ разом з розміром (до першого показу), тож
        вікно не з'являється \"де попало\" і не стрибає. Якщо діалог
        відкривається поверх іншого модального вікна, transient робимо до
        нього (не до головного), щоб воно лишалось поверх."""
        previous_grab = self.grab_current()
        dialog = ctk.CTkToplevel(self)
        dialog.withdraw()  # прихований, поки код, що створює діалог, не наповнить його віджетами
        dialog.title(title)
        dialog.geometry(self._centered_geometry(dialog, width, height))
        dialog.resizable(False, False)
        if minsize:
            dialog.minsize(*minsize)
        if parent is None:
            parent = previous_grab if previous_grab is not None else self
        dialog.transient(parent)

        # Показуємо ОДИН раз, коли код, що викликав цю функцію, вже
        # додав усі віджети (він працює синхронно, до повернення в
        # цикл подій). Інакше на macOS мигає порожній квадратик.
        def reveal():
            if not dialog.winfo_exists():
                return
            dialog.deiconify()
            dialog.lift()
            if grab:
                self._grab_dialog(dialog)
            else:
                dialog.focus_force()
            # on_shown - напр. закрити попереднє вікно ВЖЕ ПІСЛЯ того, як
            # нове показане: так перехід між вікнами без \"дірки\" й смикання.
            if on_shown is not None:
                try:
                    on_shown()
                except Exception:  # noqa: BLE001
                    pass

        dialog.after(15, reveal)
        return dialog

    def _ask_yes_no(self, title, message, yes_color=None, yes_hover_color=None, message_color=None):
        """
        Свій діалог підтвердження "Так/Ні" замість tkinter.messagebox.
        askyesno - у системного діалогу кнопки завжди мовою ОС
        ("Yes"/"No"), це неможливо перекласти штатними засобами
        tkinter. Блокує виконання так само, як messagebox (через
        wait_window) - повертає True/False лише після відповіді
        користувача.

        yes_color/yes_hover_color - необов'язковий колір кнопки "Так".
        За замовчуванням (None) лишається DANGER_COLOR, як і раніше -
        досі всі підтвердження в програмі були про руйнівні дії
        (скидання ключа, видалення кадрів). Параметр потрібен для
        майбутніх НЕ-небезпечних підтверджень (напр. "продовжити на
        власний ризик?" при невідомій тривалості відео), де червона
        кнопка виглядала б як помилкове застереження.

        message_color - необов'язковий колір ТЕКСТУ повідомлення
        (напр. WARNING_COLOR для "увага, а не помилка"). За
        замовчуванням - звичайний колір лейбла.

        Якщо діалог відкривається ПОВЕРХ іншого модального вікна (меню
        чи історія кадрів - в обох свій grab_set), після закриття
        відновлюємо grab на те вікно - інакше воно лишиться на екрані,
        але "мертвим" для кліків: Tk-грабом керує лише ОДНЕ вікно
        одночасно, і він не відновлюється автоматично сам.
        """
        previous_grab = self.grab_current()

        dialog = ctk.CTkToplevel(self)
        dialog.withdraw()  # не показуємо, поки не наповнимо й не відцентруємо
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.transient(previous_grab if previous_grab is not None else self)

        result = {"confirmed": False}

        message_kwargs = {"text_color": message_color} if message_color else {}
        ctk.CTkLabel(
            dialog, text=message, wraplength=360, justify="left", **message_kwargs
        ).pack(padx=24, pady=(24, 16))

        button_row = ctk.CTkFrame(dialog, fg_color="transparent")
        button_row.pack(pady=(0, 20))

        def on_no():
            result["confirmed"] = False
            dialog.destroy()

        def on_yes():
            result["confirmed"] = True
            dialog.destroy()

        no_btn = ctk.CTkButton(
            button_row, text=self.loc.t("dialog_no"), width=100,
            fg_color="transparent", border_width=1, text_color=("gray10", "gray90"),
            command=on_no,
        )
        no_btn.pack(side="left", padx=(0, 8))

        yes_btn = ctk.CTkButton(
            button_row, text=self.loc.t("dialog_yes"), width=100,
            fg_color=yes_color or DANGER_COLOR, hover_color=yes_hover_color or DANGER_HOVER_COLOR,
            command=on_yes,
        )
        yes_btn.pack(side="left")

        # Хрестик/Cmd+Q по цьому вікну - трактуємо як "Ні", а не як
        # "нічого не сталось": підтвердження мусить дати чітку відповідь.
        dialog.protocol("WM_DELETE_WINDOW", on_no)

        self._center_dialog_to_content(dialog)
        dialog.deiconify()
        self._grab_dialog(dialog)
        self.wait_window(dialog)  # блокує до закриття dialog (натиск кнопки або хрестика)

        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()

        return result["confirmed"]

    def _create_photo_label(self):
        """
        Створює (або перестворює з нуля) лейбл під фото у правій панелі.

        Винесено в окремий метод, бо при скиданні ("Очистити все") ми
        повністю ВИДАЛЯЄМО старий лейбл і створюємо новий, замість того
        щоб чистити в нього картинку через configure(image=None).
        Так надійніше: у customtkinter є баг - після configure(image=None)
        віджет "ламається" і наступний configure() на ньому падає з помилкою
        "image ... doesn't exist". Новий віджет такої проблеми не має.
        """
        self.photo_label = ctk.CTkLabel(self.photo_container, text=self.loc.t("photo_placeholder"))
        self.photo_label.pack(fill="both", expand=True)
        self.photo_label.bind("<Button-1>", self.toggle_lightbox)

    # ---------------- Тема і мова ----------------

    def _select_theme(self, theme_key):
        # Повторні кліки, поки попередня зміна теми ще завершується, ігноруємо
        if getattr(self, "_theme_switch_busy", False):
            return
        self.settings["theme"] = theme_key
        save_settings(self.settings)

        # Спершу оновлюємо підсвітку кнопок (поки нічого не перемальовується)
        for btn, key, _ in self.theme_buttons:
            is_selected = key == theme_key
            btn.configure(
                fg_color=BUTTON_COLOR_SELECTED if is_selected else MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_SELECTED if is_selected else TEXT_COLOR_NEUTRAL,
            )

        if not sys.platform.startswith("win"):
            ctk.set_appearance_mode(THEME_MODES[theme_key])
            return

        # Windows: при зміні теми customtkinter перефарбовує заголовки ВСІХ
        # вікон - для цього тимчасово ховає (withdraw) і знову показує
        # (deiconify) їх. Меню налаштувань - модальне (grab_set) і
        # transient до головного вікна, тож у цей момент воно втрачає grab
        # або лишається схованим, і ВЕСЬ застосунок перестає реагувати на
        # кліки. Тому на час зміни теми знімаємо grab, а потім повертаємо.
        self._theme_switch_busy = True
        dialog = self._menu_dialog
        if dialog is not None and dialog.winfo_exists():
            try:
                dialog.grab_release()
            except tk.TclError:
                pass
        self.after(50, lambda: self._apply_theme_windows(theme_key))

    def _apply_theme_windows(self, theme_key):
        try:
            ctk.set_appearance_mode(THEME_MODES[theme_key])
        except Exception:  # noqa: BLE001 - у жодному разі не лишаємо програму заблокованою
            logger.exception("Не вдалося змінити тему")
        # Даємо customtkinter завершити перефарбування заголовків
        self.after(400, self._restore_menu_grab)

    def _restore_menu_grab(self):
        """Повертає меню налаштувань на екран і його модальність після зміни теми."""
        self._theme_switch_busy = False
        dialog = self._menu_dialog
        if dialog is not None and dialog.winfo_exists():
            try:
                dialog.deiconify()
                dialog.lift()
                dialog.focus_force()
                dialog.grab_set()
            except tk.TclError:
                pass

    def _select_language(self, lang_code):
        if lang_code == self.loc.language:
            return
        self.loc.set_language(lang_code)
        self.settings["language"] = lang_code
        save_settings(self.settings)

        for btn, code in self.language_buttons:
            is_selected = code == lang_code
            btn.configure(
                fg_color=BUTTON_COLOR_SELECTED if is_selected else MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_SELECTED if is_selected else TEXT_COLOR_NEUTRAL,
            )

        self._apply_language()

    def _has_api_tier(self):
        """True, якщо тип API-ключа відомий: для вшитого ключа - завжди
        (він безкоштовний), для власного - якщо користувач його обрав."""
        if not config.is_using_custom_key() and config.has_embedded_key():
            return True
        return self.settings.get("api_tier") in VALID_API_TIERS

    def _effective_api_tier(self):
        """Тип ключа, з яким реально працюємо: вшитий ключ = "free"."""
        if not config.is_using_custom_key():
            return "free"
        return self.settings.get("api_tier") or "free"

    def _select_api_tier(self, tier_key):
        if tier_key not in VALID_API_TIERS:
            return
        if tier_key == self.settings.get("api_tier"):
            return
        self.settings["api_tier"] = tier_key
        save_settings(self.settings)   # зберігається в settings.json - переживає перезапуск
        for btn, key in self.api_tier_buttons:
            is_selected = key == tier_key
            btn.configure(
                fg_color=BUTTON_COLOR_SELECTED if is_selected else MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_SELECTED if is_selected else TEXT_COLOR_NEUTRAL,
            )
        # Тип обрано - червона підказка в меню й червоний статус у
        # головному вікні більше не актуальні.
        if getattr(self, "_menu_tier_hint", None) is not None:
            self._menu_tier_hint.configure(text="")
        if self._status_key == "status_choose_api_tier":
            self._set_status(None)
        # Саме дроселювання паралельних запитів живе в gemini_tools.py, а
        # не тут - див. TODO біля виклику find_object_in_frames_streaming()
        # нижче: цей файл поки просто передає туди self.settings["api_tier"].

    def _apply_language(self):
        """Перемальовує всі статичні написи інтерфейсу поточною мовою."""
        self.title(self.loc.t("window_title"))

        self.menu_button.configure(text=self.loc.t("menu_button"))

        self.file_button.configure(text=self.loc.t("choose_video"))
        if not self.video_path:
            self.file_label.configure(text=self.loc.t("no_file_selected"))
        self.clear_button.configure(text=self.loc.t("clear_all"))

        self.search_prompt_label_widget.configure(text=self.loc.t("search_prompt_label"))
        self.update_char_count()

        self.submit_button.configure(text=self.loc.t("start_analysis"))
        self.stop_button.configure(text=self.loc.t("stop_analysis"))
        self.choose_model_label_widget.configure(text=self.loc.t("choose_model_label"))

        # Підписи кнопок моделей ("Швидкий"/"Детально") раніше були
        # жорстко зашиті і не потребували перемальовування - тепер це
        # звичайні перекладені слова.
        self.model_info_btn.configure(text=self.loc.t("menu_info_button"))
        for btn, _, label_key in self.model_buttons:
            btn.configure(text=self.loc.t(label_key))
        self.detail_settings_button.configure(text=self.loc.t("settings_button"))

        self.export_pdf_button.configure(text=self.loc.t("export_pdf_button"))
        self.export_full_report_button.configure(text=self.loc.t("export_full_report_button"))
        self.export_hint_label.configure(text=self.loc.t("export_hint"))

        self.report_list.configure(label_text=self.loc.t("report_label"))

        # Якщо зараз відкрите вікно "Меню" - перемальовуємо і його теж
        # (інакше воно лишиться старою мовою до наступного відкриття).
        if self._menu_dialog is not None and self._menu_dialog.winfo_exists():
            self._menu_dialog.title(self.loc.t("menu_title"))
            self._menu_title_label.configure(text=self.loc.t("menu_title"))
            self._menu_theme_label.configure(text=self.loc.t("theme_label"))
            for btn, _, text_key in self.theme_buttons:
                btn.configure(text=self.loc.t(text_key))
            self._menu_lang_label.configure(text=self.loc.t("language_label"))
            self._menu_tier_label.configure(text=self.loc.t("api_tier_label"))
            for (btn, _tier_key), (_, text_key) in zip(self.api_tier_buttons, API_TIER_OPTIONS):
                btn.configure(text=self.loc.t(text_key))
            self._menu_tier_hint.configure(
                text="" if self._has_api_tier() else self.loc.t("api_tier_required_hint")
            )
            self._menu_history_btn.configure(text=self.loc.t("view_history_button"))
            self._menu_about_btn.configure(text=self.loc.t("menu_about_button"))
            self._menu_exit_btn.configure(text=self.loc.t("menu_exit_button"))
            self._menu_reset_key_btn.configure(text=self._key_button_text())
            self._menu_wipe_btn.configure(text=self.loc.t("wipe_all_button"))

        # Так само з відкритим вікном історії аналізів (список сесій).
        if self._history_window is not None and self._history_window.winfo_exists():
            self._history_window.title(self.loc.t("history_sessions_title"))
            self._history_delete_btn.configure(text=self.loc.t("history_delete_button"))
            if self._history_select_all_chk is not None:
                self._history_select_all_chk.configure(text=self.loc.t("history_select_all"))
            self._history_rebuild_rows()

        # І з відкритим вікном кадрів ОДНІЄЇ сесії, якщо воно теж
        # відкрите паралельно (просте перевідкриття тим самим методом
        # оновлює всі підписи одразу, включно з заголовком сесії).
        if self._history_session_window is not None and self._history_session_window.winfo_exists():
            self._open_history_session_window(self._history_current_session)

        # І з відкритим вікном "Про застосунок".
        if self._about_dialog is not None and self._about_dialog.winfo_exists():
            self._about_dialog.title(self.loc.t("about_title"))
            self._render_about_content()

        # І з відкритим вікном "Налаштування" (рівень деталізації).
        if self._detail_dialog is not None and self._detail_dialog.winfo_exists():
            self._detail_dialog.title(self.loc.t("settings_title"))
            self._detail_title_label.configure(text=self.loc.t("settings_title"))
            self._detail_info_btn.configure(text=self.loc.t("settings_info_button"))
            level_key, _, frames, _ = DETAIL_LEVELS[self.detail_level_index]
            self._detail_level_label.configure(text=self.loc.t(level_key))
            self._detail_frames_label.configure(text=self.loc.t("detail_level_frames_hint", count=frames))
            for caption, level_key in self._detail_caption_labels:
                caption.configure(text=self.loc.t(level_key))
            self._detail_remember_checkbox.configure(text=self.loc.t("remember_detail_level"))

        # І з відкритим інфо-вікном "Про режим «Детально»".
        if self._detail_info_dialog is not None and self._detail_info_dialog.winfo_exists():
            self._detail_info_dialog.title(self.loc.t("detail_info_title"))
            self._detail_info_body_label.configure(text=self.loc.t("detail_info_body"))

        # І з відкритим інфо-вікном "Про типи аналізу".
        if self._model_info_dialog is not None and self._model_info_dialog.winfo_exists():
            self._model_info_dialog.title(self.loc.t("model_info_title"))
            self._model_info_body_label.configure(text=self.loc.t("model_info_body"))

        # Стан фото/опису, що залежить від мови (плейсхолдер або
        # "нічого не знайдено") - перемальовуємо. Якщо ж зараз показано
        # реальний результат аналізу - його не чіпаємо, бо опис там від
        # Gemini і від обраної мови інтерфейсу не залежить.
        if self._photo_state == "placeholder":
            self.photo_label.configure(text=self.loc.t("photo_placeholder"))
        elif self._photo_state == "no_results":
            self.photo_label.configure(text=self.loc.t("nothing_found_photo"))
            self._set_caption("", self.loc.t("nothing_found_description", query=self._last_prompt_text))

        if self._no_results_label is not None:
            self._no_results_label.configure(text=self.loc.t("nothing_found_list"))

        # Статус-рядок перемальовуємо за збереженим ключем (якщо він є)
        if self._status_key:
            self.status_label.configure(
                text=self.loc.t(self._status_key, **self._status_kwargs), text_color=self._status_color
            )

    # ---------------- Меню (тема / мова / API-ключ / історія) ----------------

    def _open_menu(self, parent=None, on_shown=None):
        """
        Єдине вікно з усіма налаштуваннями - тема, мова, скидання
        API-ключа і перехід до історії кадрів. Не персистентне -
        створюється заново щоразу (простіше, ніж тримати сховане
        вікно весь час роботи програми).
        """
        if self._menu_dialog is not None and self._menu_dialog.winfo_exists():
            self._menu_dialog.lift()
            return

        dialog = self._create_modal_dialog(
            self.loc.t("menu_title"), 340, min(650, max(self.winfo_screenheight() - 100, 480)),
            parent=parent, on_shown=on_shown,
        )
        self._menu_dialog = dialog

        def on_menu_close():
            dialog.destroy()
            self._menu_dialog = None

        dialog.protocol("WM_DELETE_WINDOW", on_menu_close)

        # --- Заголовок вікна ("Про застосунок" тепер окрема кнопка нижче) ---
        title_row = ctk.CTkFrame(dialog, fg_color="transparent")
        title_row.pack(padx=20, pady=(20, 0), fill="x")

        self._menu_title_label = ctk.CTkLabel(
            title_row, text=self.loc.t("menu_title"),
            font=ctk.CTkFont(size=16, weight="bold"), anchor="w",
        )
        self._menu_title_label.pack(side="left")

        self.theme_buttons = []
        self.language_buttons = []

        self._menu_theme_label = ctk.CTkLabel(dialog, text=self.loc.t("theme_label"), anchor="w")
        self._menu_theme_label.pack(padx=20, pady=(20, 6), fill="x")

        theme_row = ctk.CTkFrame(dialog, fg_color="transparent")
        theme_row.pack(padx=20, fill="x")
        for theme_key, text_key in THEME_OPTIONS:
            is_selected = theme_key == self.settings["theme"]
            btn = ctk.CTkButton(
                theme_row,
                text=self.loc.t(text_key),
                width=90,
                fg_color=BUTTON_COLOR_SELECTED if is_selected else MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_SELECTED if is_selected else TEXT_COLOR_NEUTRAL,
                command=lambda theme_key=theme_key: self._select_theme(theme_key),
            )
            btn.pack(side="left", padx=(0, 6), pady=4)
            self.theme_buttons.append((btn, theme_key, text_key))

        self._menu_lang_label = ctk.CTkLabel(dialog, text=self.loc.t("language_label"), anchor="w")
        self._menu_lang_label.pack(padx=20, pady=(16, 6), fill="x")

        lang_row = ctk.CTkFrame(dialog, fg_color="transparent")
        lang_row.pack(padx=20, fill="x")
        for lang_code, label in LANGUAGE_OPTIONS:
            is_selected = lang_code == self.loc.language
            btn = ctk.CTkButton(
                lang_row,
                text=label,
                width=60,
                fg_color=BUTTON_COLOR_SELECTED if is_selected else MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_SELECTED if is_selected else TEXT_COLOR_NEUTRAL,
                command=lambda lang_code=lang_code: self._select_language(lang_code),
            )
            btn.pack(side="left", padx=(0, 6), pady=4)
            self.language_buttons.append((btn, lang_code))

        # --- Тип API-ключа: Безкоштовний / Платний (обов'язкове поле) ---
        # Впливає на те, наскільки агресивно gemini_tools.py дроселює
        # паралельні запити до Gemini - див. коментар біля API_TIER_OPTIONS.
        # Увесь блок лежить в одному контейнері: з вшитим ключем тип обирати
        # не потрібно (він завжди безкоштовний), тому контейнер не показуємо.
        tier_block = ctk.CTkFrame(dialog, fg_color="transparent")
        if config.is_using_custom_key() or not config.has_embedded_key():
            tier_block.pack(fill="x")

        self._menu_tier_label = ctk.CTkLabel(tier_block, text=self.loc.t("api_tier_label"), anchor="w")
        self._menu_tier_label.pack(padx=20, pady=(16, 6), fill="x")

        tier_row = ctk.CTkFrame(tier_block, fg_color="transparent")
        tier_row.pack(padx=20, fill="x")
        self.api_tier_buttons = []
        current_tier = self.settings.get("api_tier")   # None = ще не обрано, жодна кнопка не підсвічена
        for tier_key, text_key in API_TIER_OPTIONS:
            is_selected = tier_key == current_tier
            btn = ctk.CTkButton(
                tier_row,
                text=self.loc.t(text_key),
                width=110,
                fg_color=BUTTON_COLOR_SELECTED if is_selected else MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_SELECTED if is_selected else TEXT_COLOR_NEUTRAL,
                command=lambda tier_key=tier_key: self._select_api_tier(tier_key),
            )
            btn.pack(side="left", padx=(0, 6), pady=4)
            self.api_tier_buttons.append((btn, tier_key))

        # Червона підказка, поки тип ключа не обрано (зникає після вибору).
        self._menu_tier_hint = ctk.CTkLabel(
            tier_block,
            text="" if self._has_api_tier() else self.loc.t("api_tier_required_hint"),
            text_color=STATUS_COLOR_ERROR, anchor="w",
        )
        self._menu_tier_hint.pack(padx=20, pady=(2, 0), fill="x")

        ctk.CTkFrame(dialog, fg_color=("gray80", "gray25"), height=1).pack(fill="x", padx=20, pady=12)

        self._menu_history_btn = ctk.CTkButton(
            dialog, text=self.loc.t("view_history_button"),
            command=self._open_history_window,
        )
        self._menu_history_btn.pack(padx=20, pady=(0, 8), fill="x")

        self._menu_about_btn = ctk.CTkButton(
            dialog, text=self.loc.t("menu_about_button"),
            command=self._open_about_dialog,
        )
        self._menu_about_btn.pack(padx=20, pady=(0, 8), fill="x")

        self._menu_reset_key_btn = ctk.CTkButton(
            dialog, text=self._key_button_text(),
            command=self._on_key_button,
        )
        self._menu_reset_key_btn.pack(padx=20, pady=(0, 8), fill="x")

        # Повне видалення даних - для випадку, коли програму прибирають з
        # комп'ютера: macOS/Windows не видаляють теку даних разом із самою
        # програмою, тож "чисте" видалення робиться звідси перед тим, як
        # перетягнути .app у смітник.
        self._menu_wipe_btn = ctk.CTkButton(
            dialog, text=self.loc.t("wipe_all_button"),
            fg_color="transparent", border_width=2, border_color=DANGER_COLOR,
            text_color=STATUS_COLOR_ERROR, hover_color=("gray85", "gray20"),
            command=self._confirm_wipe_all_data,
        )
        self._menu_wipe_btn.pack(padx=20, pady=(0, 8), fill="x")

        # "Вихід" - останній пункт меню: закриває програму (з підтвердженням).
        self._menu_exit_btn = ctk.CTkButton(
            dialog, text=self.loc.t("menu_exit_button"),
            fg_color="transparent", border_width=1, text_color=("gray10", "gray90"),
            hover_color=("gray85", "gray25"),
            command=self._confirm_exit,
        )
        self._menu_exit_btn.pack(padx=20, pady=(0, 16), fill="x")

    def _confirm_exit(self):
        key = "exit_confirm_running" if self._analysis_running else "exit_confirm"
        if not self._ask_yes_no(
            self.loc.t("exit_title"), self.loc.t(key),
            yes_color=BUTTON_COLOR, yes_hover_color=REPORT_ROW_HOVER_COLOR,
        ):
            return
        self._on_close_window()

    def _confirm_reset_all(self):
        if not self._ask_yes_no(self.loc.t("clear_all_title"), self.loc.t("clear_all_confirm")):
            return
        self.reset_all()

    def _confirm_wipe_all_data(self):
        if not self._ask_yes_no(self.loc.t("wipe_all_title"), self.loc.t("wipe_all_confirm")):
            return
        self._wipe_on_exit = True
        self._on_close_window()

    def _wipe_data_now(self):
        """Викликається вже ПІСЛЯ закриття вікна (див. shutdown), коли
        нічого більше не пише на диск. У зібраній програмі видаляє всю теку
        даних; під час розробки - лише .env і settings.json (тека проєкту
        лишається недоторканою)."""
        logging.shutdown()   # закриваємо лог-файл, щоб він не з'явився знову
        if app_paths.wipe_user_data():
            return
        config.clear_api_key()
        try:
            os.remove(SETTINGS_PATH)
        except OSError:
            pass

    def _key_button_text(self):
        """Підпис кнопки ключа в меню залежно від того, який ключ зараз."""
        if not config.has_embedded_key():
            return self.loc.t("reset_api_key_button")      # збірка без вшитого ключа - як раніше
        if config.is_using_custom_key():
            return self.loc.t("default_key_button")
        return self.loc.t("own_key_button")

    def _on_key_button(self):
        if not config.has_embedded_key():
            self._confirm_reset_api_key()
            return
        if config.is_using_custom_key():
            self._confirm_default_key()
            return
        # Зараз стандартний ключ - даємо вставити власний. Меню закриваємо,
        # щоб після збереження ключа воно відкрилось уже з вибором типу ключа.
        if self._menu_dialog is not None and self._menu_dialog.winfo_exists():
            self._menu_dialog.destroy()
        self._menu_dialog = None
        self._open_api_key_dialog(is_first_setup=False)

    def _confirm_default_key(self):
        """Видаляє власний ключ - програма знову бере вшитий."""
        confirmed = self._ask_yes_no(
            self.loc.t("default_key_title"),
            self.loc.t("default_key_confirm"),
            yes_color=BUTTON_COLOR, yes_hover_color=REPORT_ROW_HOVER_COLOR,
        )
        if not confirmed:
            return
        config.clear_api_key()
        if self._menu_dialog is not None and self._menu_dialog.winfo_exists():
            self._menu_dialog.destroy()
        self._menu_dialog = None
        self._set_status("status_default_key_restored")

    def _confirm_reset_api_key(self):
        confirmed = self._ask_yes_no(
            self.loc.t("reset_api_key_title"),
            self.loc.t("reset_api_key_confirm"),
        )
        if not confirmed:
            return

        config.clear_api_key()

        if self._menu_dialog is not None and self._menu_dialog.winfo_exists():
            self._menu_dialog.destroy()
        self._menu_dialog = None

        # Скидання ключа скидає й поточну (незбережену) сесію аналізу -
        # так само, як у мобільній версії. Повна історія кадрів (усі
        # МИНУЛІ аналізи) при цьому НЕ чіпається - лише API-ключ і те,
        # що зараз відкрито на екрані.
        self.reset_all()
        self._open_api_key_dialog(is_first_setup=True)

    def _open_about_dialog(self):
        """Вікно "Про застосунок" - коротка інструкція: що це за програма,
        5 кроків користування і кілька корисних підказок. Відкривається
        пунктом "Про застосунок" у меню. Не персистентне - створюється заново щоразу.
        Сам вміст будується в _render_about_content, щоб його можна було
        перемалювати на льоту при зміні мови."""
        if self._about_dialog is not None and self._about_dialog.winfo_exists():
            self._about_dialog.lift()
            return

        dialog = self._create_modal_dialog(self.loc.t("about_title"), 480, 600)
        self._about_dialog = dialog

        def on_close():
            dialog.destroy()
            self._about_dialog = None

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        # Кнопка внизу пакується першою, щоб завжди лишатись видимою.
        close_btn = ctk.CTkButton(dialog, text=self.loc.t("dialog_understood"), command=on_close)
        close_btn.pack(side="bottom", pady=(0, 20))
        self._about_content = ctk.CTkFrame(dialog, fg_color="transparent")
        self._about_content.pack(fill="both", expand=True, padx=26, pady=(22, 10))
        self._render_about_content()

        # Висота вікна підганяється під фактичний вміст (різні мови дають
        # різну кількість рядків), але не вище за екран.
        dialog.update_idletasks()
        needed = self._about_content.winfo_reqheight() + close_btn.winfo_reqheight() + 22 + 10 + 20 + 10
        height = min(needed, max(self.winfo_screenheight() - 120, 400))
        dialog.geometry(self._centered_geometry(dialog, 480, height))  # перецентруємо після зміни висоти

    def _render_about_content(self):
        """Малює (або перемальовує) вміст вікна "Про застосунок":
        заголовок, 5 нумерованих кроків, підказки й підпис."""
        frame = self._about_content
        for widget in frame.winfo_children():
            widget.destroy()

        t = self.loc.t
        bold = ctk.CTkFont(weight="bold")

        def divider():
            ctk.CTkFrame(frame, fg_color=("gray80", "gray25"), height=1).pack(fill="x", pady=14)

        # --- Шапка ---
        ctk.CTkLabel(
            frame, text=t("window_title"), anchor="w",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(fill="x")
        ctk.CTkLabel(
            frame, text=t("about_subtitle"), anchor="w", justify="left",
            wraplength=420, text_color=STATUS_COLOR_NEUTRAL,
        ).pack(fill="x", pady=(4, 0))
        divider()

        # --- Кроки ---
        ctk.CTkLabel(
            frame, text=t("about_steps_title"), anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(fill="x", pady=(0, 10))

        for number in range(1, 6):
            row = ctk.CTkFrame(frame, fg_color="transparent")
            row.pack(fill="x", pady=(0, 10))
            ctk.CTkLabel(
                row, text=str(number), width=26, height=26, corner_radius=13,
                fg_color=BUTTON_COLOR, text_color=TEXT_COLOR_SELECTED, font=bold,
            ).pack(side="left", anchor="n", padx=(0, 12))
            column = ctk.CTkFrame(row, fg_color="transparent")
            column.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(
                column, text=t(f"about_step_{number}_title"), anchor="w", font=bold,
            ).pack(fill="x")
            ctk.CTkLabel(
                column, text=t(f"about_step_{number}_text"), anchor="w", justify="left",
                wraplength=375, text_color=STATUS_COLOR_NEUTRAL,
            ).pack(fill="x")
        divider()

        # --- Підказки ---
        ctk.CTkLabel(
            frame, text=t("about_tips_title"), anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(fill="x", pady=(0, 8))
        for number in range(1, 4):
            ctk.CTkLabel(
                frame, text="\u2022  " + t(f"about_tip_{number}"), anchor="w",
                justify="left", wraplength=420,
            ).pack(fill="x", pady=(0, 4))

        # --- Підпис ---
        ctk.CTkLabel(
            frame, text=t("about_credit"), anchor="w", justify="left",
            wraplength=420, text_color=STATUS_COLOR_NEUTRAL,
            font=ctk.CTkFont(size=11),
        ).pack(fill="x", side="bottom")

    # ---------------- Налаштування деталізації (модель "Детально") ----------------

    def _open_detail_settings(self):
        """
        Вікно з одним слайдером на 4 фіксовані позиції (DETAIL_LEVELS) -
        відкривається кнопкою "Налаштування", яка видима лише коли
        обрано режим "Детально" (див. _select_mode). Не персистентне -
        створюється заново щоразу, як меню й "Про застосунок".
        """
        if self._detail_dialog is not None and self._detail_dialog.winfo_exists():
            self._detail_dialog.lift()
            return

        dialog = self._create_modal_dialog(self.loc.t("settings_title"), 480, 340)
        self._detail_dialog = dialog

        def on_close():
            dialog.destroy()
            self._detail_dialog = None

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        title_row = ctk.CTkFrame(dialog, fg_color="transparent")
        title_row.pack(padx=20, pady=(20, 0), fill="x")

        self._detail_title_label = ctk.CTkLabel(
            title_row, text=self.loc.t("settings_title"),
            font=ctk.CTkFont(size=16, weight="bold"), anchor="w",
        )
        self._detail_title_label.pack(side="left")

        self._detail_info_btn = ctk.CTkButton(
            title_row, text=self.loc.t("settings_info_button"), width=28, height=28,
            corner_radius=14, fg_color="transparent",
            hover_color=("gray85", "gray25"),
            text_color=("gray30", "gray70"),
            font=ctk.CTkFont(size=16),
            command=self._open_detail_info_dialog,
        )
        self._detail_info_btn.pack(side="left", padx=(6, 0), pady=(3, 0))

        label_key, _, frames, _ = DETAIL_LEVELS[self.detail_level_index]
        self._detail_level_label = ctk.CTkLabel(
            dialog, text=self.loc.t(label_key), font=ctk.CTkFont(size=26, weight="bold"),
        )
        self._detail_level_label.pack(padx=20, pady=(20, 0))

        self._detail_frames_label = ctk.CTkLabel(
            dialog, text=self.loc.t("detail_level_frames_hint", count=frames), text_color=STATUS_COLOR_NEUTRAL,
        )
        self._detail_frames_label.pack(padx=20, pady=(2, 12))

        def on_slider_move(value):
            index = round(value)
            self.detail_level_index = index
            level_key, _, level_frames, _ = DETAIL_LEVELS[index]
            self._detail_level_label.configure(text=self.loc.t(level_key))
            self._detail_frames_label.configure(text=self.loc.t("detail_level_frames_hint", count=level_frames))
            # Пишемо на диск одразу при кожному русі слайдера, ЛИШЕ якщо
            # чекбокс "Запам'ятати" увімкнено - інакше detail_level_index
            # у settings.json взагалі не чіпаємо, він там лишається
            # тим, що було збережено раніше (або дефолтним).
            if self.remember_detail_level:
                self.settings["detail_level_index"] = index
                save_settings(self.settings)

        self._detail_slider = ctk.CTkSlider(
            dialog, from_=0, to=len(DETAIL_LEVELS) - 1,
            number_of_steps=len(DETAIL_LEVELS) - 1,
            command=on_slider_move,
            fg_color=MODEL_NEUTRAL_COLOR,          # незаповнена частина треку (після кружка)
            progress_color=BUTTON_COLOR_SELECTED,  # заповнена частина треку (до кружка)
            button_color=BUTTON_COLOR_SELECTED,    # сам кружок
            button_hover_color=BUTTON_COLOR_SELECTED,
        )
        self._detail_slider.set(self.detail_level_index)
        # padx=34 (а не 20, як раніше) - приблизно радіус самого
        # кружка-повзунка. Раніше слайдер розтягувався до тих самих
        # країв, що й підписи під ним, але сам кружок фізично НЕ
        # доходить до країв треку (він завжди інсетнутий на свій
        # радіус) - через це підписи "Слабкий"/"Максимальний", притиснуті
        # впритул до країв, стояли помітно лівіше/правіше за кружок.
        # Однаковий відступ на слайдері й на рядку підписів нижче -
        # ось що фактично вирівнює підписи під кружком.
        self._detail_slider.pack(padx=34, pady=(0, 4), fill="x")

        # Підписи під слайдером - раніше пробував рівні колонки (grid),
        # але це виявилось НЕПРАВИЛЬНО: центр колонки і реальна позиція
        # кружка на цій позиції слайдера - це РІЗНІ точки. У кружка
        # рівно 4 позиції на треку - 0%, 33%, 67%, 100% від його
        # довжини (те саме from_/to/number_of_steps, що й у самого
        # слайдера) - а не центри 4 рівних чвертей рядка (12.5%, 37.5%,
        # 62.5%, 87.5%). Тому тепер підписи розставлені через place()
        # з relx РІВНО в тих самих частках (0, 1/3, 2/3, 1), а не
        # порівну поділеною шириною - це і є те, що фактично стоїть
        # під кружком у кожній з 4 позицій.
        captions_row = ctk.CTkFrame(dialog, fg_color="transparent", height=18)
        captions_row.pack(padx=34, pady=(0, 0), fill="x")
        self._detail_caption_labels = []
        last_index = len(DETAIL_LEVELS) - 1
        for i, (level_key, _, _, _) in enumerate(DETAIL_LEVELS):
            relx = i / last_index
            anchor = "w" if i == 0 else "e" if i == last_index else "center"
            caption = ctk.CTkLabel(
                captions_row, text=self.loc.t(level_key),
                font=ctk.CTkFont(size=10), text_color=STATUS_COLOR_NEUTRAL,
            )
            caption.place(relx=relx, rely=0.5, anchor=anchor)
            self._detail_caption_labels.append((caption, level_key))

        self._detail_remember_var = tk.BooleanVar(value=self.remember_detail_level)

        def on_remember_toggle():
            self.remember_detail_level = self._detail_remember_var.get()
            self.settings["remember_detail_level"] = self.remember_detail_level
            # Вмикаючи чекбокс - одразу зберігаємо і ПОТОЧНИЙ рівень
            # слайдера, а не лише прапорець сам по собі, інакше "увімкнув
            # Запам'ятати" нічого фактично не запам'ятало б до наступного
            # руху слайдера.
            if self.remember_detail_level:
                self.settings["detail_level_index"] = self.detail_level_index
            save_settings(self.settings)

        self._detail_remember_checkbox = ctk.CTkCheckBox(
            dialog, text=self.loc.t("remember_detail_level"),
            variable=self._detail_remember_var, command=on_remember_toggle,
        )
        self._detail_remember_checkbox.pack(padx=20, pady=(16, 20), anchor="w")

    def _open_detail_info_dialog(self):
        """Невелике інформаційне вікно "Про режим «Детально»" - те саме
        за структурою, що й _open_about_dialog, лише інший текст і
        відкривається з вікна налаштувань деталізації, а не з меню."""
        if self._detail_info_dialog is not None and self._detail_info_dialog.winfo_exists():
            self._detail_info_dialog.lift()
            return

        # Це вікно завжди відкривається ПОВЕРХ "Налаштувань" (у того
        # своя grab_set()) - тому, як і в _ask_yes_no, зберігаємо, чий
        # grab був активний, і повертаємо його при закритті. Без цього
        # "Налаштування" лишались би на екрані, але "мертвими" для
        # кліків після закриття цього інфо-вікна.
        previous_grab = self.grab_current()

        dialog = self._create_modal_dialog(self.loc.t("detail_info_title"), 400, 320)
        self._detail_info_dialog = dialog

        def on_close():
            dialog.destroy()
            self._detail_info_dialog = None
            if previous_grab is not None and previous_grab.winfo_exists():
                previous_grab.grab_set()

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        self._detail_info_body_label = ctk.CTkLabel(
            dialog, text=self.loc.t("detail_info_body"),
            wraplength=360, justify="left", anchor="nw",
        )
        self._detail_info_body_label.pack(padx=20, pady=20, fill="both", expand=True)

        ctk.CTkButton(dialog, text=self.loc.t("dialog_understood"), command=on_close).pack(pady=(0, 20))

    def _open_model_info_dialog(self):
        """Інформаційне вікно "Про типи аналізу" - пояснює різницю між
        "Швидкий" і "Детально", відкривається кружком "ⓘ" біля підпису
        "Тип аналізу:" на головному екрані. За структурою те саме, що
        й _open_about_dialog / _open_detail_info_dialog."""
        if self._model_info_dialog is not None and self._model_info_dialog.winfo_exists():
            self._model_info_dialog.lift()
            return

        previous_grab = self.grab_current()

        dialog = self._create_modal_dialog(self.loc.t("model_info_title"), 420, 360)
        self._model_info_dialog = dialog

        def on_close():
            dialog.destroy()
            self._model_info_dialog = None
            if previous_grab is not None and previous_grab.winfo_exists():
                previous_grab.grab_set()

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        self._model_info_body_label = ctk.CTkLabel(
            dialog, text=self.loc.t("model_info_body"),
            wraplength=380, justify="left", anchor="nw",
        )
        self._model_info_body_label.pack(padx=20, pady=20, fill="both", expand=True)

        ctk.CTkButton(dialog, text=self.loc.t("dialog_understood"), command=on_close).pack(pady=(0, 20))

    # ---------------- API-ключ ----------------

    def _open_api_key_dialog(self, is_first_setup=False):
        """
        Модальне вікно вводу/зміни API-ключа Gemini.

        is_first_setup=True - це самий перший запуск АБО щойно скинутий
        ключ (config.load_api_key() зараз повертає None) - без ключа
        програма однаково не може нічого зробити, тому вікно НЕ можна
        закрити (нема ні хрестика, ні "Скасувати") - користувач мусить
        або вставити робочий ключ, або закрити всю програму.
        is_first_setup=False - користувач сам натиснув "Змінити ключ",
        маючи вже робочий; поле одразу заповнене ним, і вікно можна
        закрити звичайним хрестиком, лишивши старий ключ як є.

        Перевірка ключа йде РЕАЛЬНИМ запитом до Gemini (gemini_tools.
        check_api_key) у фоновому потоці, щоб вікно не "замерзало" на
        час запиту.
        """
        dialog = self._create_modal_dialog(self.loc.t("setup_title"), 480, 320)

        # Хрестик закриває вікно в обох режимах. Раніше при першому запуску
        # він був заблокований, але вікно модальне (grab_set) і блокує
        # головне - на Windows користувач опинявся в пастці без способу
        # вийти. Це безпечно: якщо ключа нема, при натисканні
        # "Почати аналіз" вікно відкриється знову (див. start_analysis).
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        # grab_set і показ вікна робить _create_modal_dialog (після того,
        # як вікно наповнене), тож тут нічого додатково не потрібно.

        description_key = "setup_description_first" if is_first_setup else "setup_description_change"
        ctk.CTkLabel(
            dialog, text=self.loc.t(description_key), wraplength=440, justify="left"
        ).pack(padx=20, pady=(20, 4), fill="x")

        # Посилання відкриває сторінку в браузері - текст у звичайному
        # CTkLabel мишкою не виділити і не скопіювати, тому простіше
        # одразу відкрити потрібну сторінку однією кнопкою.
        link_button = ctk.CTkButton(
            dialog,
            text=self.loc.t("setup_open_link_button"),
            fg_color="transparent",
            hover_color=("gray85", "gray25"),
            text_color=("#1F6AA5", "#4DA3E8"),
            anchor="w",
            command=lambda: webbrowser.open(API_KEY_URL),
        )
        link_button.pack(padx=16, pady=(0, 10), fill="x")

        entry_row = ctk.CTkFrame(dialog, fg_color="transparent")
        entry_row.pack(padx=20, pady=5)

        entry = ctk.CTkEntry(entry_row, width=330, placeholder_text="AQ.Ab8...")
        existing_key = config.load_custom_key()   # НЕ load_api_key(): вшитий ключ показувати не можна
        if existing_key:
            entry.insert(0, existing_key)
        entry.pack(side="left")
        entry.focus_set()
        self._bind_clipboard_shortcuts(entry)

        def do_paste_button():
            if not self._paste_clipboard_into(entry):
                status_label.configure(text=self.loc.t("setup_clipboard_empty"), text_color=STATUS_COLOR_ERROR)
            entry.focus_set()

        # Кнопка "Вставити" - гарантований запасний варіант поруч із
        # полем, на випадок, якщо Cmd+V з якоїсь причини не спрацьовує
        # в конкретному Tcl/Tk білді на конкретному Маку (біндинг на
        # клавіші тоді ні до чого не зобов'язує, а клік по кнопці
        # спрацює завжди - це просто виклик функції, без жодної
        # залежності від фокуса чи модифікаторів клавіатури).
        paste_button = ctk.CTkButton(
            entry_row, text=self.loc.t("setup_paste_button"), width=80,
            command=do_paste_button,
        )
        paste_button.pack(side="left", padx=(6, 0))

        status_label = ctk.CTkLabel(dialog, text="", text_color=STATUS_COLOR_NEUTRAL, wraplength=440)
        status_label.pack(padx=20, pady=(8, 0), fill="x")

        def do_save():
            key = entry.get().strip()
            if not key:
                status_label.configure(text=self.loc.t("setup_empty"), text_color=STATUS_COLOR_ERROR)
                return

            save_btn.configure(state="disabled")
            status_label.configure(text=self.loc.t("setup_checking"), text_color=STATUS_COLOR_NEUTRAL)

            def worker():
                result = check_api_key(key)

                def finish():
                    if not dialog.winfo_exists():
                        return  # вікно вже закрили, поки йшла перевірка
                    if result == "valid":
                        config.save_api_key(key)
                        dialog.destroy()
                    else:
                        save_btn.configure(state="normal")
                        status_key = "setup_invalid" if result == "invalid" else "setup_network_error"
                        status_label.configure(text=self.loc.t(status_key), text_color=STATUS_COLOR_ERROR)

                self.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        save_btn = ctk.CTkButton(dialog, text=self.loc.t("setup_save_button"), command=do_save)
        save_btn.pack(pady=15)

        entry.bind("<Return>", lambda e: do_save())

    # ---------------- Історія кадрів (усі знахідки за весь час) ----------------

    def _open_history_window(self):
        """
        Історія аналізів за ВЕСЬ час роботи програми - читає
        screenshots/meta.json. Верхній рівень - список СЕСІЙ (по одній
        на кожен завершений аналіз, найновіші згори); відкриття рядка
        сесії показує кадри всередині неї в окремому вікні
        (_open_history_session_window). Відкривається з меню, тому саме
        меню тут закриваємо. А от закриття кнопкою вікна ОС / протоколом
        WM_DELETE_WINDOW - навпаки, повертає користувача в меню (а не
        закриває всю програму) - так само, як навігація "назад" у
        мобільній версії.
        """
        old_menu = self._menu_dialog if (
            self._menu_dialog is not None and self._menu_dialog.winfo_exists()
        ) else None
        self._menu_dialog = None

        if self._history_window is not None and self._history_window.winfo_exists():
            if old_menu is not None:
                old_menu.destroy()
            self._history_window.lift()
            return

        # Меню закриваємо ПІСЛЯ того, як вікно історії показане (on_shown),
        # а не до - інакше між ними видно \"дірку\" і смикання.
        def close_old_menu():
            if old_menu is not None and old_menu.winfo_exists():
                old_menu.destroy()

        win = self._create_modal_dialog(
            self.loc.t("history_sessions_title"), 420, 580, minsize=(360, 400),
            parent=self, on_shown=close_old_menu,
        )
        self._history_window = win

        def on_close():
            self._history_window = None

            def close_history():
                if win.winfo_exists():
                    win.destroy()

            # Спершу показуємо меню, потім прибираємо історію (так само без \"дірки\").
            self._open_menu(parent=self, on_shown=close_history)

        win.protocol("WM_DELETE_WINDOW", on_close)

        self._history_selection_mode = False
        self._history_selected_ids = set()

        back_row = ctk.CTkFrame(win, fg_color="transparent")
        back_row.pack(fill="x", padx=15, pady=(15, 0))
        ctk.CTkButton(
            back_row, text=self.loc.t("back_button"), width=90,
            fg_color="transparent", border_width=1, text_color=("gray10", "gray90"),
            hover_color=("gray85", "gray25"), command=on_close,
        ).pack(side="left")

        top_row = ctk.CTkFrame(win, fg_color="transparent")
        top_row.pack(fill="x", padx=15, pady=(10, 5))

        self._history_delete_btn = ctk.CTkButton(
            top_row, text=self.loc.t("history_delete_button"), width=110,
            command=self._history_on_delete_pressed,
        )
        self._history_delete_btn.pack(side="left")

        # "Всі" - чекбокс, що вибирає/знімає ВСІ сесії одразу. Видно
        # лише в режимі вибору (коли поруч уже є чекбокси на самих
        # рядках) - тому спочатку не пакуємо (pack_forget), показуємо
        # його в _history_on_delete_pressed разом із входом у режим
        # вибору.
        self._history_select_all_var = tk.BooleanVar(value=False)
        self._history_select_all_frame = ctk.CTkFrame(top_row, fg_color="transparent")
        self._history_select_all_chk = ctk.CTkCheckBox(
            self._history_select_all_frame, text=self.loc.t("history_select_all"),
            width=24, variable=self._history_select_all_var,
            command=self._history_toggle_all,
        )
        self._history_select_all_chk.pack(side="left")

        self._history_update_delete_btn_style()

        self._history_scroll = ctk.CTkScrollableFrame(win)
        self._history_scroll.pack(fill="both", expand=True, padx=15, pady=(5, 15))

        self._history_sessions = history_store.load_sessions(SCREENSHOTS_FOLDER)
        self._history_rebuild_rows()

    def _format_session_title(self, session):
        """Текст заголовка рядка сесії: "Аналіз ДД.ММ.РРРР ГГ:ХХ:СС", або
        "Без категорії" для сесії, в яку мігрували старі (доверсійні)
        плоскі записи без дати завершення чи відео."""
        if session.get("is_legacy"):
            return self.loc.t("history_uncategorized")
        iso_str = session.get("finished_at", "")
        try:
            dt = datetime.fromisoformat(iso_str)
        except (ValueError, TypeError):
            return self.loc.t("history_uncategorized")
        return self.loc.t("history_session_row_title", date=dt.strftime("%d.%m.%Y %H:%M:%S"))

    def _history_rebuild_rows(self):
        if self._history_scroll is None or not self._history_scroll.winfo_exists():
            return

        for widget in self._history_scroll.winfo_children():
            widget.destroy()

        if not self._history_sessions:
            ctk.CTkLabel(
                self._history_scroll, text=self.loc.t("history_sessions_empty"), text_color=STATUS_COLOR_NEUTRAL
            ).pack(pady=20)
            return

        for session in self._history_sessions:
            title_text = self._format_session_title(session)
            subtitle_parts = [part for part in (
                session.get("video_name", ""),
                self.loc.t("history_frames_count", count=len(session.get("frames", []))),
            ) if part]

            row = ctk.CTkFrame(self._history_scroll, fg_color=("gray90", "gray20"))
            row.pack(fill="x", pady=4)

            def on_row_click(event=None, s=session):
                self._history_on_row_click(s)

            label = ctk.CTkLabel(
                row,
                text=f"{title_text}\n{'   •   '.join(subtitle_parts)}",
                anchor="w", justify="left",
            )
            label.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            label.bind("<Button-1>", on_row_click)
            row.bind("<Button-1>", on_row_click)

            if self._history_selection_mode:
                var = tk.BooleanVar(value=session["session_id"] in self._history_selected_ids)
                chk = ctk.CTkCheckBox(
                    row, text="", width=24, variable=var,
                    command=lambda s=session, v=var: self._history_toggle_one(s, v),
                )
                chk.pack(side="right", padx=8, pady=8)

    def _history_on_row_click(self, session):
        if self._history_selection_mode:
            sid = session["session_id"]
            if sid in self._history_selected_ids:
                self._history_selected_ids.discard(sid)
            else:
                self._history_selected_ids.add(sid)
            self._history_rebuild_rows()
            self._history_sync_select_all_var()
            self._history_update_delete_btn_style()
            return
        self._open_history_session_window(session)

    def _history_toggle_one(self, session, var):
        sid = session["session_id"]
        if var.get():
            self._history_selected_ids.add(sid)
        else:
            self._history_selected_ids.discard(sid)
        self._history_sync_select_all_var()
        self._history_update_delete_btn_style()

    def _history_toggle_all(self):
        """Чекбокс "Всі" - позначає/знімає позначку одразу з усіх сесій."""
        if self._history_select_all_var.get():
            self._history_selected_ids = {s["session_id"] for s in self._history_sessions}
        else:
            self._history_selected_ids = set()
        self._history_rebuild_rows()
        self._history_update_delete_btn_style()

    def _history_sync_select_all_var(self):
        """Тримає чекбокс "Всі" у правильному стані: позначений лише
        тоді, коли позначено буквально кожну сесію в історії."""
        if self._history_select_all_var is None:
            return
        all_selected = bool(self._history_sessions) and (
            len(self._history_selected_ids) == len(self._history_sessions)
        )
        self._history_select_all_var.set(all_selected)

    def _history_update_delete_btn_style(self):
        """
        Кнопка "Видалити" має три вигляди залежно від стану:
          1) звичайний (не в режимі вибору) - нейтральний сірий,
             звичайний (не жирний) текст;
          2) у режимі вибору, але нічого не позначено - той самий сірий,
             але текст стає жирним (це і є сигнал "тепер онови вибір");
          3) позначено хоча б одну сесію - кнопка стає червоною
             (небезпечна дія), текст жирний і білий.
        """
        if self._history_delete_btn is None:
            return

        if self._history_selection_mode and self._history_selected_ids:
            self._history_delete_btn.configure(
                fg_color=DANGER_COLOR,
                hover_color=DANGER_HOVER_COLOR,
                text_color="white",
                font=ctk.CTkFont(size=13, weight="bold"),
            )
        elif self._history_selection_mode:
            self._history_delete_btn.configure(
                fg_color=MODEL_NEUTRAL_COLOR,
                hover_color=MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_NEUTRAL,
                font=ctk.CTkFont(size=13, weight="bold"),
            )
        else:
            self._history_delete_btn.configure(
                fg_color=MODEL_NEUTRAL_COLOR,
                hover_color=MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_NEUTRAL,
                font=ctk.CTkFont(size=13, weight="normal"),
            )

    def _history_set_selection_mode(self, enabled):
        """Вмикає/вимикає режим вибору і разом з ним показує/ховає
        чекбокс "Всі" (він потрібен лише поки видно чекбокси на рядках)."""
        self._history_selection_mode = enabled
        if enabled:
            self._history_select_all_var.set(False)
            self._history_select_all_frame.pack(side="left", padx=(10, 0))
        else:
            self._history_select_all_frame.pack_forget()
            self._history_selected_ids = set()

    def _history_on_delete_pressed(self):
        # Кнопка "Видалити" - це і вхід у режим вибору, і кнопка
        # підтвердження, залежно від поточного стану:
        if not self._history_selection_mode:
            self._history_set_selection_mode(True)
            self._history_rebuild_rows()
            self._history_update_delete_btn_style()
            return

        if not self._history_selected_ids:
            # У режимі вибору, але нічого не позначено - трактуємо як
            # "скасувати вибір", а не як "видалити нуль сесій".
            self._history_set_selection_mode(False)
            self._history_rebuild_rows()
            self._history_update_delete_btn_style()
            return

        count = len(self._history_selected_ids)
        confirmed = self._ask_yes_no(
            self.loc.t("history_session_delete_confirm_title"),
            self.loc.t("history_session_delete_confirm_message", count=count),
        )
        if confirmed:
            self._history_do_delete()

    def _history_do_delete(self):
        """Видаляє обрані СЕСІЇ цілком - усі кадри кожної обраної сесії
        (файли на диску + запис) і саму сесію."""
        ids_to_delete = self._history_selected_ids
        remaining = []
        for session in self._history_sessions:
            if session["session_id"] in ids_to_delete:
                for frame in session.get("frames", []):
                    file_path = os.path.join(SCREENSHOTS_FOLDER, frame.get("filename", ""))
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
            else:
                remaining.append(session)

        history_store.save_sessions(SCREENSHOTS_FOLDER, remaining)
        self._history_sessions = remaining
        self._history_set_selection_mode(False)
        self._history_rebuild_rows()
        self._history_update_delete_btn_style()

    # ---------------- Кадри всередині ОДНІЄЇ сесії ----------------

    def _open_history_session_window(self, session):
        """
        Окреме вікно зі списком кадрів ОДНІЄЇ сесії аналізу. Переверстка
        рядків тут - та сама, що й раніше була для плоскої історії
        (_history_frame_rebuild_rows дзеркалить _history_rebuild_rows),
        режим вибору й видалення окремих кадрів - теж перевикористаний.
        Додатково - кнопка "Експортувати в PDF" для всієї сесії одразу.
        """
        if self._history_session_window is not None and self._history_session_window.winfo_exists():
            self._history_session_window.destroy()

        self._history_current_session = session
        self._history_session_frames = list(session.get("frames", []))

        win = self._create_modal_dialog(
            self.loc.t("history_title"), 420, 620, minsize=(360, 420)
        )
        self._history_session_window = win

        def on_close():
            win.destroy()
            self._history_session_window = None
            self._history_current_session = None
            # повертаємо керування списку сесій під цим вікном
            if self._history_window is not None and self._history_window.winfo_exists():
                self._history_window.grab_set()

        win.protocol("WM_DELETE_WINDOW", on_close)

        self._history_frame_selection_mode = False
        self._history_frame_selected_ids = set()

        header_row = ctk.CTkFrame(win, fg_color="transparent")
        header_row.pack(fill="x", padx=15, pady=(15, 2))
        ctk.CTkButton(
            header_row, text=self.loc.t("back_button"), width=90,
            fg_color="transparent", border_width=1, text_color=("gray10", "gray90"),
            hover_color=("gray85", "gray25"), command=on_close,
        ).pack(side="left", padx=(0, 10))
        ctk.CTkLabel(
            header_row, text=self._format_session_title(session), font=ctk.CTkFont(weight="bold"),
            anchor="w", justify="left",
        ).pack(side="left", fill="x", expand=True)

        if session.get("video_name"):
            ctk.CTkLabel(
                win, text=session.get("video_name", ""), text_color=STATUS_COLOR_NEUTRAL, anchor="w",
            ).pack(fill="x", padx=15, pady=(0, 5))

        top_row = ctk.CTkFrame(win, fg_color="transparent")
        top_row.pack(fill="x", padx=15, pady=(0, 5))

        self._history_frame_delete_btn = ctk.CTkButton(
            top_row, text=self.loc.t("history_delete_button"), width=110,
            command=self._history_frame_on_delete_pressed,
        )
        self._history_frame_delete_btn.pack(side="left")

        self._history_frame_select_all_var = tk.BooleanVar(value=False)
        self._history_frame_select_all_frame = ctk.CTkFrame(top_row, fg_color="transparent")
        self._history_frame_select_all_chk = ctk.CTkCheckBox(
            self._history_frame_select_all_frame, text=self.loc.t("history_select_all"),
            width=24, variable=self._history_frame_select_all_var,
            command=self._history_frame_toggle_all,
        )
        self._history_frame_select_all_chk.pack(side="left")

        self._history_session_export_btn = ctk.CTkButton(
            top_row, text=self.loc.t("export_full_report_button"),
            command=self._history_session_export_pdf,
        )
        self._history_session_export_btn.pack(side="right")

        self._history_frame_update_delete_btn_style()

        self._history_frame_scroll = ctk.CTkScrollableFrame(win)
        self._history_frame_scroll.pack(fill="both", expand=True, padx=15, pady=(5, 15))

        self._history_frame_rebuild_rows()

    def _history_frame_rebuild_rows(self):
        if self._history_frame_scroll is None or not self._history_frame_scroll.winfo_exists():
            return

        for widget in self._history_frame_scroll.winfo_children():
            widget.destroy()

        if not self._history_session_frames:
            ctk.CTkLabel(
                self._history_frame_scroll, text=self.loc.t("history_empty"), text_color=STATUS_COLOR_NEUTRAL
            ).pack(pady=20)
            return

        for frame in self._history_session_frames:
            timestamp_sec = frame.get("timestamp_sec")
            time_text = format_time(timestamp_sec) if timestamp_sec is not None else "—"

            row = ctk.CTkFrame(self._history_frame_scroll, fg_color=("gray90", "gray20"))
            row.pack(fill="x", pady=4)

            def on_row_click(event=None, f=frame):
                self._history_frame_on_row_click(f)

            label = ctk.CTkLabel(
                row,
                text=f"{time_text}\n{frame.get('description', '')[:60]}",
                anchor="w", justify="left",
            )
            label.pack(side="left", fill="x", expand=True, padx=10, pady=8)
            label.bind("<Button-1>", on_row_click)
            row.bind("<Button-1>", on_row_click)

            if self._history_frame_selection_mode:
                var = tk.BooleanVar(value=frame["id"] in self._history_frame_selected_ids)
                chk = ctk.CTkCheckBox(
                    row, text="", width=24, variable=var,
                    command=lambda f=frame, v=var: self._history_frame_toggle_one(f, v),
                )
                chk.pack(side="right", padx=8, pady=8)

    def _history_frame_on_row_click(self, frame):
        if self._history_frame_selection_mode:
            fid = frame["id"]
            if fid in self._history_frame_selected_ids:
                self._history_frame_selected_ids.discard(fid)
            else:
                self._history_frame_selected_ids.add(fid)
            self._history_frame_rebuild_rows()
            self._history_frame_sync_select_all_var()
            self._history_frame_update_delete_btn_style()
            return
        self._show_history_preview(frame, self._history_current_session)

    def _history_frame_toggle_one(self, frame, var):
        fid = frame["id"]
        if var.get():
            self._history_frame_selected_ids.add(fid)
        else:
            self._history_frame_selected_ids.discard(fid)
        self._history_frame_sync_select_all_var()
        self._history_frame_update_delete_btn_style()

    def _history_frame_toggle_all(self):
        if self._history_frame_select_all_var.get():
            self._history_frame_selected_ids = {f["id"] for f in self._history_session_frames}
        else:
            self._history_frame_selected_ids = set()
        self._history_frame_rebuild_rows()
        self._history_frame_update_delete_btn_style()

    def _history_frame_sync_select_all_var(self):
        if self._history_frame_select_all_var is None:
            return
        all_selected = bool(self._history_session_frames) and (
            len(self._history_frame_selected_ids) == len(self._history_session_frames)
        )
        self._history_frame_select_all_var.set(all_selected)

    def _history_frame_update_delete_btn_style(self):
        if self._history_frame_delete_btn is None:
            return

        if self._history_frame_selection_mode and self._history_frame_selected_ids:
            self._history_frame_delete_btn.configure(
                fg_color=DANGER_COLOR,
                hover_color=DANGER_HOVER_COLOR,
                text_color="white",
                font=ctk.CTkFont(size=13, weight="bold"),
            )
        elif self._history_frame_selection_mode:
            self._history_frame_delete_btn.configure(
                fg_color=MODEL_NEUTRAL_COLOR,
                hover_color=MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_NEUTRAL,
                font=ctk.CTkFont(size=13, weight="bold"),
            )
        else:
            self._history_frame_delete_btn.configure(
                fg_color=MODEL_NEUTRAL_COLOR,
                hover_color=MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_NEUTRAL,
                font=ctk.CTkFont(size=13, weight="normal"),
            )

    def _history_frame_set_selection_mode(self, enabled):
        self._history_frame_selection_mode = enabled
        if enabled:
            self._history_frame_select_all_var.set(False)
            self._history_frame_select_all_frame.pack(side="left", padx=(10, 0))
        else:
            self._history_frame_select_all_frame.pack_forget()
            self._history_frame_selected_ids = set()

    def _history_frame_on_delete_pressed(self):
        if not self._history_frame_selection_mode:
            self._history_frame_set_selection_mode(True)
            self._history_frame_rebuild_rows()
            self._history_frame_update_delete_btn_style()
            return

        if not self._history_frame_selected_ids:
            self._history_frame_set_selection_mode(False)
            self._history_frame_rebuild_rows()
            self._history_frame_update_delete_btn_style()
            return

        count = len(self._history_frame_selected_ids)
        confirmed = self._ask_yes_no(
            self.loc.t("history_delete_confirm_title"),
            self.loc.t("history_delete_confirm_message", count=count),
        )
        if confirmed:
            self._history_frame_do_delete()

    def _history_frame_do_delete(self):
        """Видаляє обрані кадри з ПОТОЧНОЇ сесії (файли + записи).
        Якщо після видалення в сесії не лишилось жодного кадру - сесія
        видаляється з історії цілком, а це вікно закривається."""
        ids_to_delete = self._history_frame_selected_ids
        remaining = []
        for frame in self._history_session_frames:
            if frame["id"] in ids_to_delete:
                file_path = os.path.join(SCREENSHOTS_FOLDER, frame.get("filename", ""))
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            else:
                remaining.append(frame)

        self._history_session_frames = remaining
        self._history_current_session["frames"] = remaining

        sessions = history_store.load_sessions(SCREENSHOTS_FOLDER)
        current_id = self._history_current_session.get("session_id")
        if remaining:
            sessions = [
                self._history_current_session if s.get("session_id") == current_id else s
                for s in sessions
            ]
        else:
            sessions = [s for s in sessions if s.get("session_id") != current_id]
        history_store.save_sessions(SCREENSHOTS_FOLDER, sessions)
        self._history_sessions = sessions

        self._history_frame_set_selection_mode(False)

        if not remaining:
            # Сесія щойно спорожніла і зникла з історії - закриваємо це
            # вікно і оновлюємо список сесій позаду нього.
            if self._history_session_window is not None and self._history_session_window.winfo_exists():
                self._history_session_window.destroy()
            self._history_session_window = None
            self._history_current_session = None
            self._history_rebuild_rows()
            return

        self._history_frame_rebuild_rows()
        self._history_frame_update_delete_btn_style()
        self._history_rebuild_rows()  # лічильник кадрів у списку сесій теж змінився

    def _history_session_export_pdf(self):
        """Кнопка "Експортувати в PDF" у вікні сесії - зберігає ВСІ
        кадри цієї сесії в один PDF-файл (та сама логіка, що й
        export_full_report_pdf для поточного щойно завершеного аналізу,
        але для будь-якої сесії з історії)."""
        session = self._history_current_session
        if session is None or not session.get("frames"):
            return

        results = []
        for frame in session.get("frames", []):
            timestamp_sec = frame.get("timestamp_sec")
            results.append({
                "time_str": format_time(timestamp_sec) if timestamp_sec is not None else "",
                "description": frame.get("description", ""),
                "screenshot_path": os.path.join(SCREENSHOTS_FOLDER, frame.get("filename", "")),
            })

        default_name = build_full_report_filename()
        path = filedialog.asksaveasfilename(
            title=self.loc.t("export_pdf_dialog_title"),
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return
        try:
            export_report_to_pdf(
                path, results,
                video_name=session.get("video_name", ""),
                video_path=session.get("video_path", ""),
                lang=self.loc.language,
            )
            self._set_status("export_pdf_success", path=os.path.basename(path))
        except Exception as error:
            self._set_status("export_pdf_error", error=error)

    def _show_history_preview(self, frame, session=None):
        """Окреме невелике вікно перегляду ОДНОГО кадру з історії -
        фото (адаптивно вписане) + таймкод + відео + опис від Gemini.
        frame - запис кадру всередині сесії; session - сесія, якій цей
        кадр належить (дає video_name/video_path для заголовка й PDF)."""
        screenshot_path = os.path.join(SCREENSHOTS_FOLDER, frame.get("filename", ""))
        if not os.path.exists(screenshot_path):
            return

        win = self._create_modal_dialog(
            self.loc.t("history_preview_title"), 560, 620, minsize=(360, 420)
        )

        timestamp_sec = frame.get("timestamp_sec")
        time_str = format_time(timestamp_sec) if timestamp_sec is not None else ""
        video_name = session.get("video_name", "") if session else ""
        video_path = session.get("video_path", "") if session else ""
        header_parts = [part for part in (time_str, video_name) if part]

        # Заголовок вікна. Закривається стандартною кнопкою вікна ОС
        # (власної кнопки-хрестика тут немає).
        header_row = ctk.CTkFrame(win, fg_color="transparent")
        header_row.pack(fill="x", padx=15, pady=(15, 5))

        ctk.CTkLabel(
            header_row, text="  •  ".join(header_parts), font=ctk.CTkFont(weight="bold"),
        ).pack(side="left", anchor="w")


        image_container = ctk.CTkFrame(win, fg_color="transparent")
        image_container.pack(fill="both", expand=True, padx=15)

        pil_image = Image.open(screenshot_path)
        image_label = ctk.CTkLabel(image_container, text="")
        image_label.pack(fill="both", expand=True)

        def render(event=None):
            fitted = self._fit_image(
                pil_image, image_container.winfo_width(), image_container.winfo_height()
            )
            if fitted is None:
                return
            ctk_img = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=fitted)
            image_label.configure(image=ctk_img, text="")
            image_label.image = ctk_img

        image_container.bind("<Configure>", render)

        ctk.CTkLabel(
            win, text=frame.get("description", ""), wraplength=520, justify="left",
        ).pack(padx=15, pady=(8, 10), anchor="w", fill="x")

        preview_status_label = ctk.CTkLabel(win, text="", text_color=STATUS_COLOR_NEUTRAL, wraplength=520)
        preview_status_label.pack(padx=15, pady=(0, 4), anchor="w", fill="x")

        def set_preview_status(key, is_error=False, **kwargs):
            if not win.winfo_exists():
                return
            preview_status_label.configure(
                text=self.loc.t(key, **kwargs),
                text_color=STATUS_COLOR_ERROR if is_error else STATUS_COLOR_NEUTRAL,
            )

        def do_export_pdf():
            default_name = build_pdf_filename(time_str or "frame")
            path = filedialog.asksaveasfilename(
                title=self.loc.t("export_pdf_dialog_title"),
                defaultextension=".pdf",
                initialfile=default_name,
                filetypes=[("PDF", "*.pdf")],
            )
            if not path:
                return  # користувач натиснув "Скасувати"
            try:
                export_frame_to_pdf(
                    path, screenshot_path, time_str, frame.get("description", ""),
                    video_name=video_name, video_path=video_path,
                    lang=self.loc.language,
                )
                set_preview_status("export_pdf_success", path=os.path.basename(path))
            except Exception as error:
                set_preview_status("export_pdf_error", is_error=True, error=error)

        def do_save_image():
            _, ext = os.path.splitext(frame.get("filename", ""))
            ext = ext or ".jpg"
            path = filedialog.asksaveasfilename(
                title=self.loc.t("save_image_dialog_title"),
                defaultextension=ext,
                initialfile=f"frame{ext}",
                filetypes=[("Image", f"*{ext}"), (self.loc.t("file_dialog_all_files"), "*.*")],
            )
            if not path:
                return
            try:
                shutil.copy(screenshot_path, path)
                set_preview_status("save_image_success", path=os.path.basename(path))
            except OSError as error:
                set_preview_status("save_image_error", is_error=True, error=error)

        actions_row = ctk.CTkFrame(win, fg_color="transparent")
        actions_row.pack(padx=15, pady=(0, 10), fill="x")

        ctk.CTkButton(
            actions_row, text=self.loc.t("export_pdf_button"), command=do_export_pdf,
        ).pack(side="left", expand=True, fill="x", padx=(0, 5))
        ctk.CTkButton(
            actions_row, text=self.loc.t("history_save_image_button"), command=do_save_image,
        ).pack(side="left", expand=True, fill="x", padx=(5, 0))

    # ---------------- Обробники подій ----------------

    def _on_prompt_enter(self, event):
        # Enter в полі промпту запускає аналіз, а не переносить рядок
        self.start_analysis()
        return "break"  # "break" зупиняє стандартну поведінку (перенос рядка)

    def _is_typing_in_prompt(self):
        # Перевіряємо, що фокус зараз саме в полі вводу промпту -
        # тоді стрілки не повинні гортати звіт, а мають просто
        # рухати курсор по тексту, як зазвичай.
        focused = self.focus_get()
        if focused is None:
            return False
        return str(focused).startswith(str(self.prompt_box))

    def _navigate_report(self, direction):
        """direction: -1 = попередній момент, +1 = наступний момент."""
        if self._is_typing_in_prompt():
            return
        if not self.report_buttons:
            return

        current_index = 0
        for i, (btn, result) in enumerate(self.report_buttons):
            if result is self.current_result:
                current_index = i
                break

        new_index = current_index + direction
        if 0 <= new_index < len(self.report_buttons):
            _, new_result = self.report_buttons[new_index]
            self.show_photo(new_result)

    def _select_mode(self, mode):
        self.selected_mode = mode
        for btn, name, _ in self.model_buttons:
            is_selected = name == mode
            btn.configure(
                fg_color=BUTTON_COLOR_SELECTED if is_selected else MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_SELECTED if is_selected else TEXT_COLOR_NEUTRAL,
            )

        # Кнопка "Налаштування" (рівень деталізації) має сенс лише для
        # режиму "Детально" - у "Швидкий" нема слайдера взагалі.
        if mode == MODE_DETAIL:
            self.detail_settings_button.pack(side="left", padx=(8, 0))
        else:
            self.detail_settings_button.pack_forget()

    def choose_file(self):
        path = filedialog.askopenfilename(
            title=self.loc.t("file_dialog_title"),
            filetypes=[
                (self.loc.t("file_dialog_video_files"), "*.mp4 *.avi *.mov *.mkv"),
                (self.loc.t("file_dialog_all_files"), "*.*"),
            ],
        )
        if not path:
            return
        self._accept_video_path(path)

    def _accept_video_path(self, path):
        """
        Спільна "фінальна" точка для БУДЬ-ЯКОГО способу обрати відео -
        через діалог "Обрати відео" (choose_file) чи перетягуванням
        файлу у вікно (_on_dnd_drop): перевіряє тривалість і, якщо все
        гаразд (або користувач підтвердив ризик), встановлює
        self.video_path і оновлює підпис обраного файлу.
        """
        # Перевірка тривалості йде тут, СИНХРОННО в головному потоці -
        # get_video_duration_sec() лише читає заголовок файлу (mvhd
        # бокс/метадані), а не декодує кадри, тож для будь-якого відео
        # це мілісекунди, вікно не встигає "зависнути".
        try:
            duration_sec = get_video_duration_sec(path)
        except ValueError:
            duration_sec = 0  # відео взагалі не відкрилось - трактуємо як "тривалість невідома"

        if duration_sec and duration_sec > MAX_VIDEO_DURATION_SEC:
            self._set_status(
                "status_video_too_long",
                color=STATUS_COLOR_ERROR,
                duration=format_time(duration_sec),
                max=format_time(MAX_VIDEO_DURATION_SEC),
            )
            return

        if not duration_sec:
            # Тривалість не визначилась - можливо, це законний краєвий
            # випадок (незвичний контейнер), а не насправді завелике
            # відео. Попереджаємо і даємо вирішити самому користувачу,
            # а не тихо пропускаємо і не блокуємо безумовно.
            confirmed = self._ask_yes_no(
                self.loc.t("duration_unknown_title"),
                self.loc.t("duration_unknown_message"),
                yes_color=BUTTON_COLOR, yes_hover_color=BUTTON_COLOR_SELECTED[1],
                message_color=WARNING_COLOR,
            )
            if not confirmed:
                return

        self.video_path = path
        self.file_label.configure(text=os.path.basename(path))
        self._set_status(None)

    # ---------------- Drag-and-drop файлу з ОС ----------------

    def _setup_drag_and_drop(self):
        """
        Дозволяє перетягнути відеофайл прямо з провідника/фендера у
        вікно програми замість обов'язкового діалогу "Обрати відео".

        Потребує бібліотеки tkinterdnd2 (див. коментар про
        _APP_BASE_CLASSES вгорі файлу) і нативних tkdnd-бібліотек, які
        вона підвантажує через TkinterDnD._require(). Якщо чогось із
        цього нема - тихо вимикаємо можливість (лишається звичайний
        вибір файлу через кнопку) замість падіння всієї програми.
        """
        self._dnd_enabled = False
        self._dnd_status_reset_job = None
        self._status_before_flash = (None, {}, STATUS_COLOR_NEUTRAL)
        self._dnd_probe_busy = False  # чи йде зараз фонова перевірка скинутого файлу

        if not _DND_IMPORT_OK:
            print("[diag] drag-and-drop вимкнено: бібліотека tkinterdnd2 не встановлена")
            return

        try:
            self.TkdndVersion = TkinterDnD._require(self)
        except Exception as error:  # noqa: BLE001 - відсутність D&D не має валити програму
            print(f"[diag] drag-and-drop вимкнено: {error}")
            return

        # Оверлей "відпустіть файл тут" - темна підкладка на все вікно
        # з підписом по центру, з'являється лише поки курсор із файлом
        # над вікном (DropEnter/DropLeave), в решту часу невидимий
        # (place_forget - віджет не займає місця й не перехоплює кліки).
        self._dnd_overlay = ctk.CTkFrame(self, fg_color=("gray20", "gray10"))
        self._dnd_overlay_label = ctk.CTkLabel(
            self._dnd_overlay, text=self.loc.t("dnd_drop_hint"),
            font=ctk.CTkFont(size=20, weight="bold"), text_color="white",
        )
        self._dnd_overlay_label.place(relx=0.5, rely=0.5, anchor="center")
        self._dnd_overlay.place_forget()

        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<DropEnter>>", self._on_dnd_enter)
        self.dnd_bind("<<DropLeave>>", self._on_dnd_leave)
        self.dnd_bind("<<Drop>>", self._on_dnd_drop)

        self._dnd_enabled = True

    def _on_dnd_enter(self, event):
        # Підпис оновлюємо щоразу - інакше після зміни мови лишався б старий.
        self._dnd_overlay_label.configure(text=self.loc.t("dnd_drop_hint"))
        self._dnd_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        self._dnd_overlay.lift()
        return event.action

    def _on_dnd_leave(self, event):
        self._dnd_overlay.place_forget()
        return event.action

    def _on_dnd_drop(self, event):
        self._dnd_overlay.place_forget()

        if self._analysis_running:
            # Аналіз уже йде - не підміняємо self.video_path у нього
            # з-під ніг. Коротке попередження замість тихого ігнорування.
            self._flash_status("status_dnd_analysis_running", color=STATUS_COLOR_ERROR)
            return event.action

        paths = self._parse_dnd_paths(event.data)
        if not paths:
            return event.action
        # Перетягнули відразу кілька файлів - як і діалог вибору файлу,
        # працюємо лише з одним відео за раз, беремо перший.
        path = paths[0]

        if self._dnd_probe_busy:
            return event.action  # попередній файл ще перевіряється

        # Перевірка файлу (OpenCV, до CV2_PROBE_TIMEOUT_SEC) - у фоновому
        # потоці, щоб вікно не замирало; результат обробляємо в головному.
        self._dnd_probe_busy = True

        def worker():
            try:
                status = dnd_tools.classify_dropped_file(path)
            except Exception:  # noqa: BLE001
                status = dnd_tools.STATUS_UNKNOWN
            self.after(0, lambda: self._on_dnd_classified(path, status))

        threading.Thread(target=worker, daemon=True).start()
        return event.action

    def _on_dnd_classified(self, path, status):
        """Друга половина _on_dnd_drop - в головному потоці, коли фонова
        перевірка файлу завершилась."""
        self._dnd_probe_busy = False

        if self._analysis_running:
            # Аналіз міг стартувати, поки файл перевірявся.
            self._flash_status("status_dnd_analysis_running", color=STATUS_COLOR_ERROR)
            return

        if status == dnd_tools.STATUS_NOT_VIDEO:
            self._flash_status("status_dnd_not_video", color=STATUS_COLOR_ERROR)
            return

        if status == dnd_tools.STATUS_UNKNOWN:
            confirmed = self._ask_yes_no(
                self.loc.t("dnd_unknown_confirm_title"),
                self.loc.t("dnd_unknown_confirm_message"),
                yes_color=BUTTON_COLOR, yes_hover_color=BUTTON_COLOR_SELECTED[1],
                message_color=WARNING_COLOR,
            )
            if not confirmed:
                return

        self._accept_video_path(path)

    def _parse_dnd_paths(self, data):
        """
        tkinterdnd2 віддає шлях(и) одним рядком у форматі Tcl-списку:
        кожен шлях, що містить пробіли, обгорнутий у фігурні дужки
        (наприклад "{C:/My Videos/cat.mp4}"), кілька файлів розділені
        пробілом. self.tk.splitlist() - штатний спосіб Tcl/Tk коректно
        розібрати такий рядок (враховуючи дужки й пробіли всередині
        шляхів), не городячи власний парсер вручну.
        """
        if not data:
            return []
        try:
            return list(self.tk.splitlist(data))
        except Exception:
            return [data]

    def _flash_status(self, key, color=None, duration_ms=2500, **kwargs):
        """
        Короткочасне (2-3 секунди) повідомлення в статус-рядку, яке
        саме гасне - для швидких попереджень про drag-and-drop (\"це не
        відео\", \"зачекай на аналіз\"), а не для помилок аналізу (ті
        лишаються на екрані, поки користувач не почне заново).

        Після гасіння повертає ПОПЕРЕДНІЙ статус (наприклад, прогрес
        аналізу, що йде), а не порожній рядок - але лише якщо за цей
        час статус не змінив хтось інший.
        """
        if self._dnd_status_reset_job is not None:
            self.after_cancel(self._dnd_status_reset_job)
        else:
            self._status_before_flash = (self._status_key, dict(self._status_kwargs), self._status_color)
        self._set_status(key, color=color, **kwargs)

        def restore():
            self._dnd_status_reset_job = None
            if self._status_key != key:
                return  # статус уже оновили (напр. аналіз) - не чіпаємо
            prev_key, prev_kwargs, prev_color = self._status_before_flash
            self._set_status(prev_key, color=prev_color, **prev_kwargs)

        self._dnd_status_reset_job = self.after(duration_ms, restore)

    def _on_prompt_modified(self, event=None):
        """Жорсткий ліміт: усе, що довше за MAX_PROMPT_CHARS символів
        (друк, вставка, перетягування тексту), одразу обрізається."""
        textbox = self.prompt_box._textbox
        if not textbox.edit_modified():
            return
        textbox.edit_modified(False)
        if len(textbox.get("1.0", "end-1c")) > MAX_PROMPT_CHARS:
            textbox.delete(f"1.0+{MAX_PROMPT_CHARS}c", "end-1c")
        self.update_char_count()

    def update_char_count(self, event=None):
        count = len(self.prompt_box.get("1.0", "end-1c"))
        color = STATUS_COLOR_NEUTRAL if count < MAX_PROMPT_CHARS else WARNING_COLOR
        self.word_count_label.configure(
            text=self.loc.t("char_count", count=count, max=MAX_PROMPT_CHARS), text_color=color
        )

    def _set_caption(self, time_str, text):
        """Підпис під фото в єдиному стилі: час (жирний) ліворуч + текст праворуч."""
        if time_str:
            self.caption_time_label.configure(text=time_str)
            self.caption_time_label.grid()
        else:
            self.caption_time_label.configure(text="")
            self.caption_time_label.grid_remove()
        self.description_label.configure(text=text)

    def reset_all(self):
        """Повертає програму в початковий стан: шлях, промпт, звіт, фото."""
        self.close_lightbox()

        if self._analysis_running:
            # "Очистити все" натиснуто просто під час аналізу - скасовуємо
            # його так само, як кнопкою "Зупинити аналіз", а не лишаємо
            # фоновий потік мовчки довисати й перезаписати щойно очищений
            # інтерфейс своїм результатом, коли нарешті завершиться.
            self._cancel_event.set()

        self.video_path = None
        self.file_label.configure(text=self.loc.t("no_file_selected"))

        self.prompt_box.delete("1.0", "end")
        self.update_char_count()

        self._set_status(None)
        self._set_progress(None)
        self._set_analysis_running(False)

        for widget in self.report_list.winfo_children():
            widget.destroy()
        self.report_buttons = []
        self.current_result = None
        self._no_results_label = None
        self._last_prompt_text = ""

        self._set_caption("", "")
        self.export_pdf_button.configure(state="disabled")
        self.export_full_report_button.configure(state="disabled")

        # Перестворюємо лейбл з фото з нуля (див. коментар у _create_photo_label)
        self.photo_label.destroy()
        self._create_photo_label()
        self.current_pil_image = None
        self._photo_state = "placeholder"

        # Скидаємо вибір режиму - користувач обирає заново
        self.selected_mode = None
        for btn, name, _ in self.model_buttons:
            btn.configure(fg_color=MODEL_NEUTRAL_COLOR, text_color=TEXT_COLOR_NEUTRAL)
        self.detail_settings_button.pack_forget()

    def _clear_report_view(self):
        """
        Прибирає ЗВІТ ПОПЕРЕДНЬОГО аналізу з екрану: список моментів
        ліворуч, фото/опис праворуч, кнопки експорту. На відміну від
        reset_all() - НЕ чіпає обране відео, текст запиту й обрану
        модель, бо викликається прямо перед стартом НОВОГО аналізу з
        тими самими параметрами.

        ЧОМУ ЦЕ ПОТРІБНО: раніше старий звіт лишався на екрані аж до
        завершення нового аналізу (show_results() чистить і малює
        звіт лише В КІНЦІ пайплайна). Для довгого відео це могло йти
        кілька хвилин - користувач бачив старі знайдені моменти і не
        міг зрозуміти, це вже результат нового пошуку чи ще старий,
        аналіз узагалі йде чи завис.
        """
        for widget in self.report_list.winfo_children():
            widget.destroy()
        self.report_buttons = []
        self.current_result = None
        self._no_results_label = None

        self._set_caption("", "")
        self.export_pdf_button.configure(state="disabled")
        self.export_full_report_button.configure(state="disabled")

        # Перестворюємо лейбл з фото з нуля (див. коментар у _create_photo_label)
        self.current_pil_image = None
        self.photo_label.destroy()
        self._create_photo_label()
        self._photo_state = "placeholder"

    def _set_analysis_running(self, running):
        """
        Перемикає інтерфейс між станами "нічого не відбувається" і
        "аналіз іде": яка з двох кнопок (submit_button / stop_button)
        показана в submit_row, і чи можна натиснути "Зупинити".

        Викликається і зі старту аналізу (running=True), і з усіх
        точок його завершення - успіх, помилка, скасування,
        "Очистити все" (усюди running=False).
        """
        self._analysis_running = running
        if running:
            self.submit_button.pack_forget()
            self.stop_button.configure(state="normal")
            self.stop_button.pack()
        else:
            self.stop_button.pack_forget()
            self.submit_button.configure(state="normal")
            self.submit_button.pack()

    def stop_analysis(self):
        """
        Натиснута кнопка "Зупинити аналіз". Реально перервати вже
        відправлений HTTP-запит до Gemini неможливо - але run_pipeline()
        (у фоновому потоці) перевіряє self._cancel_event на кожному
        кроці й сам обривається, тільки-но побачить його встановленим
        (див. extract_frames()/find_object_in_frames() і перевірки між
        етапами в run_pipeline). Тому тут просто ставимо прапорець і
        одразу даємо користувачу видимий фідбек, що запит "почуто" -
        саме завершення прийде трохи згодом, через _on_analysis_cancelled().
        """
        if not self._analysis_running:
            return
        self._cancel_event.set()
        self.stop_button.configure(state="disabled")
        self._set_status("status_cancelling")

    def _on_analysis_cancelled(self):
        """
        Викликається (через self.after, тобто в головному потоці) з
        run_pipeline(), коли той сам побачив self._cancel_event і
        обірвав аналіз. Повертає інтерфейс у звичайний стан, як після
        помилки - без часткового/старого звіту на екрані.
        """
        self._set_status("status_cancelled")
        self._set_progress(None)
        self._set_analysis_running(False)

    def start_analysis(self):
        if not self.video_path:
            self._set_status("status_choose_video_first", color=STATUS_COLOR_ERROR)
            return

        if not self.selected_mode:
            self._set_status("status_choose_model", color=STATUS_COLOR_ERROR)
            return

        prompt_text = self.prompt_box.get("1.0", "end").strip()
        if not prompt_text:
            self._set_status("status_write_query", color=STATUS_COLOR_ERROR)
            return

        # Тип API-ключа обов'язковий: без нього не знаємо, під який
        # тариф (free/paid) дросселювати запити до Gemini.
        if not self._has_api_tier():
            self._set_status("status_choose_api_tier", color=STATUS_COLOR_ERROR)
            return

        if not config.load_api_key():
            self._open_api_key_dialog(is_first_setup=True)
            return

        # Страховка: поле вже не пускає понад ліміт, але обрізаємо ще раз
        prompt_text = prompt_text[:MAX_PROMPT_CHARS]

        self._cancel_event.clear()
        self._set_analysis_running(True)
        self._clear_report_view()
        self._set_status("status_cutting_frames")
        # РАНІШЕ тут стояло self._set_progress(5) - миттєвий стрибок на
        # 5%, хоча в цей момент реально не зроблено ще НІЧОГО (навіть
        # відеофайл не відкритий). Це і давало ефект "спочатку швидко,
        # потім повільно": бар одразу "малював" 5% неіснуючого прогресу,
        # а далі чесний прогрес нарізки (яка може йти хвилинами на
        # довгому відео) повз ту саму видиму відстань у рази довше -
        # виглядало як різке гальмування, хоча насправді гальмувало не
        # воно, а сам старт був фальшивим. Тепер стартуємо чесно з 0
        # (докладніше про те, чому саме 0, а не якийсь сентинел - у
        # коментарі над _set_progress()).
        self._set_progress(0, allow_decrease=True)

        # max_frames, thinking_level і media_resolution залежать від
        # self.detail_level_index (для режиму "Детально") так само, як
        # мова - і мусять фіксуватись ТУТ, на момент натискання "Почати
        # аналіз", а не читатись пізніше з фонового потоку: інакше
        # користувач міг би посмикати слайдер деталізації вже під час
        # запущеного аналізу і отримати батчі, надіслані з різними
        # значеннями (частина зі старим рівнем, частина з новим). Усі
        # три значення виводяться з ОДНОГО й того ж detail_level_index
        # синхронно.
        if self.selected_mode == MODE_FAST:
            max_frames = FAST_MODE_FRAMES
            thinking_level = FAST_MODE_THINKING_LEVEL
            media_resolution = FAST_MODE_MEDIA_RESOLUTION
        else:
            _, thinking_level, max_frames, media_resolution = DETAIL_LEVELS[self.detail_level_index]

        # Уся важка робота (відео + Gemini) йде в окремому потоці.
        # Якщо робити це в головному потоці - вікно програми "замре"
        # і не реагуватиме, поки Gemini думає.
        # Мову для Gemini фіксуємо тут, на момент запуску аналізу - якщо
        # користувач перемкне мову інтерфейсу вже під час обробки, це не
        # зіб'є запит, який уже пішов у фоновому потоці.
        thread = threading.Thread(
            target=self._run_pipeline_guarded,
            args=(prompt_text, ANALYSIS_MODEL_NAME, self.loc.language, max_frames, thinking_level, media_resolution),
            daemon=True,
        )
        thread.start()

    def _run_pipeline_guarded(self, *pipeline_args):
        """
        Обгортка над run_pipeline: на час аналізу вимикає сон системи від
        бездіяльності і вмикає сторожа простою (див. keep_awake.py) -
        аналіз довгого відео може йти десятки хвилин, і якщо комп'ютер
        засне, запити до Gemini зависають (на тесті це коштувало
        ~63 хвилини). Виконується у фоновому потоці аналізу: на Windows
        блокування сну прив'язане до потоку, тож вмикати і знімати його
        треба саме звідси.
        """
        keep_awake = KeepAwake()
        watchdog = SuspendWatchdog()
        self._suspend_watchdog = watchdog
        keep_awake.start()
        watchdog.start()
        try:
            self.run_pipeline(*pipeline_args)
        finally:
            watchdog.stop()
            keep_awake.stop()

    def run_pipeline(self, prompt_text, model_name, language, max_frames, thinking_level, media_resolution):
        """Ця функція виконується у фоновому потоці (не в головному!)."""
        # pipeline_start_ts - момент запуску всього аналізу (нарізка +
        # Gemini). Використовується і для консольних замірів по фазах
        # (тимчасово, поки шукаємо, де саме йде час), і для підсумкового
        # "Витрачено часу на аналіз" у status_done.
        pipeline_start_ts = time.time()
        print(f"[log] Старт аналізу: {self.video_path}")
        self._failed_batches = 0  # скільки пачок Gemini пропущено через збої (див. show_results)
        full_res_dir = None  # тека кешу повнорозмірних кадрів (див. нижче); чиститься у finally
        try:
            # --------------------------------------------------------
            # Нарізка і звернення до Gemini йдуть ПАРАЛЕЛЬНО, а не
            # послідовно.
            #
            # РАНІШЕ: спочатку extract_frames() повністю дорізало відео
            # (могло бути кілька хвилин на довгому файлі), і лише ПОТІМ
            # стартував find_object_in_frames() (ще кілька хвилин через
            # ліміти безкоштовного ключа Gemini). Разом - сума двох
            # етапів.
            #
            # ТЕПЕР: video_tools.extract_frames() кладе кожен щойно
            # знайдений кадр у frame_queue одразу, ще ПІД ЧАС нарізки
            # (через on_frame_ready), а gemini_tools.
            # find_object_in_frames_streaming() читає з цієї черги і
            # відправляє пачки в Gemini, щойно набирається BATCH_SIZE
            # кадрів - не чекаючи, поки відріжеться решта відео. Два
            # найдовші етапи пайплайна тепер йдуть в різних потоках
            # одночасно, і загальний час - приблизно ДОВШИЙ з двох
            # етапів, а не їх сума.
            #
            # Технічно це producer/consumer на queue.Queue():
            #   - extraction_thread (окремий потік) - виконує
            #     extract_frames() і одночасно наповнює frame_queue;
            #   - сам цей (фоновий) потік run_pipeline - одразу читає
            #     з frame_queue через find_object_in_frames_streaming(),
            #     яка сама розкидає пачки по MAX_PARALLEL_BATCHES
            #     робочих потоках Gemini (як і раніше).
            #
            # Кадри одночасно збираються у ЗВИЧАЙНИЙ список `frames` -
            # він потрібен пізніше (frames[match.frame_number]) для
            # мапи "номер кадру -> timestamp_sec/full_path" так само,
            # як і в старому, непотоковому варіанті.
            full_res_dir = os.path.join(SCREENSHOTS_FOLDER, f".cache_{uuid.uuid4().hex}")
            os.makedirs(full_res_dir, exist_ok=True)

            frames = []
            frames_lock = threading.Lock()
            frame_queue = queue.Queue()
            extraction_error_box = {}
            # Окремий прапорець ЗУПИНКИ нарізки: користувацьке "Стоп"
            # (self._cancel_event) - не єдина причина зупинити
            # декодування достроково. Якщо Gemini впаде з фатальною
            # помилкою (не мережевою/тимчасовою), продовжувати різати
            # решту годинного відео намарно - зупиняємо нарізку й
            # звідти теж.
            gemini_stop_event = threading.Event()

            def combined_is_cancelled():
                return self._cancel_event.is_set() or gemini_stop_event.is_set()

            def on_frame_ready(frame, _index):
                with frames_lock:
                    frames.append(frame)
                frame_queue.put(frame)

            # ------------------------------------------------------------
            # Прогрес-бар і статус-текст.
            #
            # БУЛО (баг): бар вів ЛИШЕ аналіз Gemini, і то за формулою
            # done_frames / max_frames_hint, де max_frames_hint - це
            # СТАТИЧНА стеля з налаштувань ("Швидкий"/рівень деталізації,
            # напр. 200/800/2000), а не реальна кількість кадрів цього
            # відео. На короткому відео (напр. 22 реальних кадри при
            # стелі 200) бар майже не рухався (22/200 ≈ 11%), а потім
            # ОДРАЗУ стрибав на 90%, щойно приходила відповідь єдиної
            # пачки - тобто по факту не показував нічого весь час нарізки
            # й аналізу. Другий, окремий баг: нарізка взагалі не рухала
            # бар - cut_progress_box лишень зберігав fraction для тексту,
            # у прогрес ніколи не йшов.
            #
            # ТЕПЕР: один бар веде ОБИДВА паралельні процеси одразу -
            # 0-40% нарізка (cut_fraction, від video_tools - точна,
            # рахується від реальної тривалості відео), 40-90% аналіз
            # (analysis_fraction = done_frames / кадрів, які РЕАЛЬНО вже
            # нарізані на цей момент - а не стеля з налаштувань). Обидва
            # доданки монотонно ростуть до 1.0, тож сума теж монотонна -
            # бар ніколи не стрибає назад. Оновлюється з ОБОХ callback'ів
            # (on_cut_progress і on_batch_done), а не лише з одного.
            # ------------------------------------------------------------
            cut_progress_box = {"fraction": 0.0}
            analysis_progress_box = {
                "done_frames": 0, "done_batches": 0, "total_batches": 1,
                # started_frames - кадри пачок, які вже ВІДПРАВЛЕНІ в
                # Gemini, але відповідь ще не прийшла ("у польоті").
                # Потрібне лише для "підкрутки" (див. PROGRESS_TRICKLE_*
                # і _push_combined_progress нижче) - на сам факт/точність
                # done_frames не впливає.
                "started_frames": 0,
                # max_combined_percent - "стеля знизу": найбільший % з тих,
                # що вже були реально показані (0-100). БУЛО (баг): без
                # цього дроти analysis_fraction = done_frames / extracted_count
                # МІГ ЗМЕНШУВАТИСЬ - нарізка (extraction_worker, окремий
                # потік) регулярно віддає нові кадри швидше, ніж Gemini
                # встигає їх опрацювати, тож знаменник (extracted_count)
                # росте, а чисельник (done_frames) на цей момент - ні.
                # done_frames і extracted_count самі по собі монотонні, але
                # їхнє ВІДНОШЕННЯ - ні, тож і сума cut_fraction*40 +
                # analysis_fraction*50 могла впасти, а бар - помітно
                # відкотитись назад. Тепер у _set_progress_async завжди йде
                # max(щойно порахований %, максимум з усіх попередніх) -
                # бар лишень стоїть на місці, поки Gemini не наздожене, але
                # ніколи не їде назад.
                "max_combined_percent": 0.0,
                # parallel_frame_count_hint - "скільки вже нарізано" за
                # даними frame_count_callback з video_tools (див. коментар
                # там же). БУЛО: на паралельному шляху (повільний seek,
                # >1 сегмент) єдиний сигнал про нарізку - відсоток; сам
                # список frames лишався ПОРОЖНІМ аж до самого завершення
                # нарізки (кадри стають відомі бекенду одним шматком,
                # див. extraction_worker нижче) - тож status-рядок "нарізано
                # X" показував застиглий 0 усю нарізку, хоча прогрес-бар
                # (за відсотком) рухався - виглядало як зависання. Тепер
                # це поле оновлюється тим самим колбеком, що рухає й
                # відсоток, тож "нарізано X" теж росте наживо.
                "parallel_frame_count_hint": 0,
            }
            # final_extracted_total - фіксується РІВНО в момент, коли
            # extraction_worker дізнається реальну фінальну кількість
            # нарізаних кадрів (len(result_frames)), ЩЕ ДО того, як він
            # почне заливати їх у frames/frame_queue по одному нижче.
            # БУЛО (баг): доти extracted_count для analysis_fraction
            # рахувався як len(frames) "наживо" - а це саме той список,
            # який extraction_worker заповнює в циклі одразу після
            # завершення нарізки. Поки цей цикл іде, extracted_count
            # росте на очах (0 -> total) ПАРАЛЕЛЬНО з тим, як Gemini вже
            # встигає обробити перші надіслані пачки (done_frames теж
            # росте) - те саме дроблення "чисельник/знаменник ростуть не
            # синхронно", що й баг з відкатом бару вище, тільки в іншому
            # місці (не під час самої нарізки, а в момент масової заливки
            # її результату в чергу). max_combined_percent і це так само
            # ловив (не давав бару їхати назад), але сам % міг надовго
            # "застрягати" замість плавного росту. Коли фінальна
            # кількість уже відома заздалегідь - знаменник більше не
            # росте в цей момент, і analysis_fraction росте чесно й
            # монотонно з самого початку заливки.
            final_extracted_total_box = {"value": None}

            # Лок для max_combined_percent: _push_combined_progress викликають
            # з різних потоків (нарізка й воркери Gemini) - без нього
            # "прочитати max -> порахувати -> записати max" може перетертись.
            progress_lock = threading.Lock()

            def _push_combined_progress():
                # ЧЕСНИЙ прогрес у "одиницях роботи": кожен кадр потребує
                # двох дій - бути нарізаним і бути проаналізованим. Разом
                # 2 * N одиниць, зроблено (нарізано + проаналізовано).
                # Обидва доданки в кадрах, обидва ділимо на ОДНЕ й те саме N -
                # тому 80 нарізано і 40 проаналізовано з 400 = 120/800 = 15%,
                # а не "50% від того, що нарізано на цей момент".
                #
                # N (expected_total) - скільки кадрів буде в підсумку:
                #   - нарізка завершена -> точне число (final_extracted_total);
                #   - інакше екстраполяція: нарізано / частка_нарізки (частка
                #     веде video_tools, рахується від реальної тривалості),
                #     обмежена зверху max_frames з налаштувань;
                #   - поки частка < 5% (оцінка ще шумна) -> просто max_frames.
                # Якщо N уточниться (напр. коротке відео дало 22 кадри замість
                # 200), % може лише СТРИБНУТИ ВПЕРЕД - назад його не пустить
                # max_combined_percent + guard у _set_progress().
                with frames_lock:
                    extracted_count = len(frames)
                extracted_count = max(extracted_count, analysis_progress_box["parallel_frame_count_hint"])
                cut_fraction = cut_progress_box["fraction"]
                done_frames = analysis_progress_box["done_frames"]

                if final_extracted_total_box["value"] is not None:
                    expected_total = max(1, final_extracted_total_box["value"])
                else:
                    expected_total = max_frames
                    if cut_fraction >= 0.05 and extracted_count > 0:
                        expected_total = min(max_frames, max(extracted_count, round(extracted_count / cut_fraction)))
                    expected_total = max(1, expected_total)

                units_done = min(extracted_count, expected_total) + min(done_frames, expected_total)
                # 90% - стеля цього етапу: далі йдуть скріншоти (90-97) і фініш.
                combined_percent = units_done / (2 * expected_total) * 90

                with progress_lock:
                    combined_percent = max(combined_percent, analysis_progress_box["max_combined_percent"])
                    analysis_progress_box["max_combined_percent"] = combined_percent
                self._set_progress_async(combined_percent)

                # "Підкрутку" (_progress_trickle_*) вимкнено навмисно: вона
                # рахувала кадри "у польоті" майже готовими (x0.85) і повзла
                # вгору без реальних подій - саме тому бар показував ~50%,
                # коли реально було проаналізовано 40 з 80 нарізаних.

                if cut_fraction < 1.0:
                    self._set_status_async(
                        "status_cutting_and_analyzing", cut_count=extracted_count, done=done_frames,
                    )
                else:
                    self._set_status_async(
                        "status_analyzing_batches", done=done_frames, total=extracted_count,
                    )

            def on_cut_progress(fraction):
                # Викликається з ПОТОКУ нарізки (video_tools), часто -
                # тепер реально рухає бар і текст, а не лише запам'ятовує
                # значення для колишнього "мовчазного" читання.
                cut_progress_box["fraction"] = fraction
                _push_combined_progress()

            def on_parallel_frame_count(count):
                # Викликається лише на паралельному шляху нарізки (див.
                # коментар над parallel_frame_count_hint вище) - звичайний
                # однопотоковий seek/sequential шлях цим колбеком не
                # користується, бо там кадри й так стають відомі наживо
                # через on_frame_ready/len(frames).
                analysis_progress_box["parallel_frame_count_hint"] = count
                _push_combined_progress()

            def extraction_worker():
                try:
                    print("[log] Нарізаю відео на кадри (паралельно з Gemini)...")
                    result_frames = extract_frames(
                        self.video_path, max_frames=max_frames, progress_callback=on_cut_progress,
                        is_cancelled=combined_is_cancelled, full_res_dir=full_res_dir,
                        on_frame_ready=on_frame_ready, frame_count_callback=on_parallel_frame_count,
                    )
                    # Фіксуємо фінальну кількість ДО циклу заливки нижче -
                    # див. коментар над final_extracted_total_box.
                    final_extracted_total_box["value"] = len(result_frames)
                    _push_combined_progress()
                    with frames_lock:
                        streamed_count = len(frames)
                        # Деякі шляхи нарізки (паралельні слайси на повільному
                        # seek, апаратне декодування) НЕ підтримують
                        # on_frame_ready (див. докстрінг extract_frames) -
                        # для них кадри стають відомі лише зараз, по
                        # завершенню. Доотправляємо їх у чергу заднім числом,
                        # тим самим порядком, що й у result_frames.
                        if streamed_count < len(result_frames):
                            for extra_frame in result_frames[streamed_count:]:
                                frames.append(extra_frame)
                                frame_queue.put(extra_frame)
                except Exception as error:  # noqa: BLE001 - прокидаємо в основний потік нижче
                    extraction_error_box["error"] = error
                    frame_queue.put(error)
                finally:
                    frame_queue.put(STREAM_END)

            cut_start_ts = time.time()
            extraction_thread = threading.Thread(target=extraction_worker, daemon=True)
            extraction_thread.start()

            def on_batch_started(frame_count):
                # Викликається gemini_tools одразу в момент ВІДПРАВКИ
                # пачки (ще до відповіді) - лише щоб "підкрутка"
                # (_push_combined_progress) знала, скільки кадрів зараз
                # "у польоті", і могла обережно посунути стелю вперед,
                # поки чекаємо мережевої відповіді.
                analysis_progress_box["started_frames"] += frame_count
                _push_combined_progress()

            def on_batch_done(done_batches, total_batches, done_frames, total_frames):
                # Викликається gemini_tools ПІСЛЯ кожної завершеної пачки
                # (total_frames тут - лише орієнтир зі статичної стелі
                # налаштувань, для реального прогресу не годиться - див.
                # коментар над _push_combined_progress вище, тому не
                # використовується напряму).
                completed_now = done_frames - analysis_progress_box["done_frames"]
                # started_frames рахує кадри, що ще В ПОЛЬОТІ - щойно
                # пачка підтвердилась, її кадри звідти прибираємо (не
                # менше нуля - про всяк випадок, якщо рахунок колись
                # розійдеться, підкрутці ліпше "не роздувати" стелю,
                # ніж піти в мінус).
                analysis_progress_box["started_frames"] = max(
                    0, analysis_progress_box["started_frames"] - completed_now,
                )
                analysis_progress_box["done_frames"] = done_frames
                analysis_progress_box["done_batches"] = done_batches
                analysis_progress_box["total_batches"] = total_batches
                if not extraction_thread.is_alive():
                    cut_progress_box["fraction"] = 1.0
                _push_combined_progress()

            gemini_start_ts = time.time()
            print(f"[log] Питаю Gemini ({model_name}), запит: «{prompt_text}»...")
            try:
                matches = find_object_in_frames_streaming(
                    frame_queue, prompt_text, model_name=model_name, language=language,
                    progress_callback=on_batch_done,
                    started_callback=on_batch_started,
                    is_cancelled=self._cancel_event.is_set,
                    thinking_level=thinking_level,
                    max_frames_hint=max_frames,
                    media_resolution=media_resolution,
                    # TODO(gemini_tools.py): цей модуль мені поки не показували,
                    # тож параметр лише прокинутий сюди - сама функція його ще
                    # НЕ приймає. Потрібно додати kwarg api_tier у
                    # find_object_in_frames_streaming() і використати його там,
                    # де зараз розраховане дроселювання (паралелізм/затримки між
                    # запитами) під ліміти free-tier - на "paid" воно має
                    # вимикатись або суттєво послаблюватись.
                    api_tier=self._effective_api_tier(),   # вшитий ключ = free, власний - те, що обрав користувач
                )
            except GeminiApiError:
                # Фатальна (не тимчасова) помилка Gemini - нема сенсу
                # дорізати решту відео намарно.
                gemini_stop_event.set()
                raise
            finally:
                extraction_thread.join()
                # Аналіз (успішно чи ні) завершився - реальних подій
                # більше не буде, підкрутку вимикаємо, щоб вона не
                # "доповзала" кудись у порожнечу під час наступних,
                # уже СВОЇХ кроків (збереження скріншотів тощо).
                self._progress_trickle_stop_async()

            gemini_elapsed_sec = time.time() - gemini_start_ts
            cut_elapsed_sec = time.time() - cut_start_ts
            print(f"[timing] нарізка кадрів: {cut_elapsed_sec:.1f}с (паралельно з Gemini), кадрів: {len(frames)}")
            print(f"[timing] аналіз Gemini: {gemini_elapsed_sec:.1f}с, моментів: {len(matches)}")

            if self._cancel_event.is_set():
                print("[log] Аналіз скасовано користувачем")
                self.after(0, self._on_analysis_cancelled)
                return

            self._set_status_async("status_frames_cut", count=len(frames))
            self._set_progress_async(90)

            self._failed_batches = getattr(matches, "failed_batches", 0)
            self._set_status_async("status_moments_found", count=len(matches))
            self._set_progress_async(90)

            print("[log] Зберігаю скріншоти знайдених моментів...")
            screenshots_start_ts = time.time()
            os.makedirs(SCREENSHOTS_FOLDER, exist_ok=True)

            # Спочатку рахуємо, ЯКІ кадри й КУДИ треба зберегти, а потім
            # дістаємо їх усі за ОДИН прохід по відео (grab_screenshots_batch),
            # а не відкриваємо файл і гортаємо його заново для кожного
            # окремого моменту - на довгому звіті це помітно швидше.
            #
            # Ім'я файлу - унікальний uuid (а не frame_<номер>.jpg, як
            # раніше) - інакше кадр №5 сьогоднішнього відео і кадр №5
            # завтрашнього іншого відео мовчки переписували б один
            # одного на диску. Це важливо тепер, коли скріншоти
            # накопичуються в спільній історії (screenshots/meta.json),
            # а не видаляються після кожного аналізу.
            pending = []
            for match in matches:
                if not (0 <= match.frame_number < len(frames)):
                    continue
                timestamp_sec = frames[match.frame_number]["timestamp_sec"]
                filename = history_store.new_screenshot_filename()
                screenshot_path = os.path.join(SCREENSHOTS_FOLDER, filename)
                pending.append((match, timestamp_sec, screenshot_path, filename))

            def on_screenshot_progress(fraction):
                # Аналогічно on_cut_progress вище - цей крок теж може
                # відчутно тривати на звіті з десятками моментів, тож
                # віддаємо йому свій, реальний (а не миттєвий) діапазон:
                # 90% (Gemini вже відповів) .. 97% (усі скріншоти збережені).
                self._set_progress_async(90 + fraction * 7)

            # Швидкий шлях: повнорозмірний кадр уже збережений під час
            # нарізки (full_path) - просто переносимо його на місце
            # скріншота. Той самий кадр, що бачив Gemini, і без другого
            # декодування відео. Лише для тих, кого в кеші не знайшлось
            # (запис не вдався), лишається старе дістання з відео.
            saved_paths = set()
            to_grab = []
            for match, timestamp_sec, path, _ in pending:
                cached = frames[match.frame_number].get("full_path")
                if cached and os.path.exists(cached):
                    try:
                        os.replace(cached, path)
                        saved_paths.add(path)
                        continue
                    except OSError:
                        pass
                to_grab.append((timestamp_sec, path))

            disk_full_warning = None
            if to_grab:
                try:
                    saved_paths |= grab_screenshots_batch(
                        self.video_path, to_grab, progress_callback=on_screenshot_progress,
                    )
                except ScreenshotDiskFullError as error:
                    # П3 плану: диск скінчився ПОСЕРЕД збереження скріншотів.
                    # БУЛО: цей виняток летів у ЗАГАЛЬНИЙ except Exception
                    # ВСЬОГО pipeline (нижче) - і побудова results/
                    # history_store.append_session() (вона йде ДАЛІ по коду)
                    # просто не наступала: усі вже знайдені Gemini моменти
                    # (у реальному випадку - 403 штуки) викидались РАЗОМ із
                    # тими кількома скріншотами, яким не вистачило місця.
                    # ТЕПЕР: беремо часткове збереження з самого винятку
                    # (video_tools.ScreenshotDiskFullError.saved) і йдемо
                    # ДАЛІ як зазвичай - результат буде неповним (частина
                    # моментів лишиться без картинки й відсіється нижче
                    # умовою `if screenshot_path not in saved_paths`), але
                    # НЕ втраченим цілком.
                    saved_paths |= error.saved
                    disk_full_warning = (error.done_targets, error.total_targets)
                    print(f"[log] Місце на диску скінчилось під час збереження скріншотів "
                          f"({error.done_targets}/{error.total_targets} встигло обробитись до збою) - "
                          f"продовжую з тим, що вже збережено ({len(saved_paths)}/{len(pending)})")
            screenshots_elapsed_sec = time.time() - screenshots_start_ts
            print(f"[timing] збереження скріншотів: {screenshots_elapsed_sec:.1f}с, "
                  f"збережено: {len(saved_paths)}/{len(pending)}")

            if self._cancel_event.is_set():
                print("[log] Аналіз скасовано користувачем (під час збереження скріншотів)")
                self.after(0, self._on_analysis_cancelled)
                return

            results = []
            # finished_at - ОДИН спільний момент завершення для всіх
            # кадрів цього запуску аналізу - це і є момент завершення
            # сесії, під ним усі знахідки цього аналізу логічно
            # групуються в історії.
            finished_at = datetime.now().isoformat()
            video_name = os.path.basename(self.video_path) if self.video_path else ""
            video_path = self.video_path or ""
            session_frames = []

            for match, timestamp_sec, screenshot_path, filename in pending:
                if screenshot_path not in saved_paths:
                    continue  # не вдалось дістати саме цей кадр - пропускаємо
                if not os.path.exists(screenshot_path):
                    # Файл "збережено" за звітом, але його нема на диску -
                    # не показуємо момент без картинки і не мовчимо про це.
                    print(f"[log] Скріншот не знайдено на диску, пропускаю: {screenshot_path}")
                    continue
                results.append({
                    "time_str": format_time(timestamp_sec),
                    "timestamp_sec": timestamp_sec,
                    "description": match.description,
                    "screenshot_path": screenshot_path,
                })
                session_frames.append({
                    "id": history_store.new_record_id(),
                    "filename": filename,
                    "timestamp_sec": timestamp_sec,
                    "description": match.description,
                })

            # Кадри сесії - у хронологічному порядку відео (Gemini міг
            # повернути їх у довільному), інакше історія й PDF з неї
            # йшли б не за часом.
            session_frames.sort(key=lambda f: f["timestamp_sec"])

            if session_frames:
                session = {
                    "session_id": history_store.new_session_id(),
                    "finished_at": finished_at,
                    "video_name": video_name,
                    "video_path": video_path,
                    "frames": session_frames,
                }
                history_store.append_session(SCREENSHOTS_FOLDER, session)

            self._set_progress_async(97)

            # Сортуємо за часом - щоб стрілки гортали звіт по порядку
            # (вперед/назад по відео), а не в тому порядку, в якому
            # Gemini їх повернув.
            results.sort(key=lambda r: r["timestamp_sec"])

            wall_elapsed_sec = time.time() - pipeline_start_ts
            idle_sec = self._suspend_watchdog.suspended_sec if self._suspend_watchdog else 0.0
            # Час, коли комп'ютер спав/стояв, не є часом роботи аналізу -
            # віднімаємо його і від підсумку, який бачить користувач.
            total_elapsed_sec = max(0.0, wall_elapsed_sec - idle_sec)
            if idle_sec >= 1.0:
                print(f"[timing] весь аналіз загалом: {total_elapsed_sec:.1f}с "
                      f"(без простою системи {idle_sec:.0f}с; за годинником минуло {wall_elapsed_sec:.1f}с)")
            else:
                print(f"[timing] весь аналіз загалом: {total_elapsed_sec:.1f}с")
            print(f"[log] Готово. Знайдено моментів: {len(results)}")
            if disk_full_warning is not None:
                done_targets, total_targets_saved = disk_full_warning
                # Немає окремого локалізованого статусу "не вистачило місця
                # на диску" (для цього треба чіпати localization.py - не
                # чіпаю наосліп, без файлу під рукою). Використовую наявний
                # ключ "status_moments_found": те, що показано користувачу,
                # правдиве (стільки моментів дійсно знайдено й збережено),
                # а повну картину збою видно в консолі/лозі вище.
                self._set_status_async("status_moments_found", count=len(results))

            # Системне сповіщення - користувач міг згорнути вікно чи
            # перемкнутись на іншу програму, поки аналіз (хвилини)
            # ще тривав. Показ - необов'язкова можливість (див.
            # notify_tools.notify), тож викликаємо прямо тут, у
            # фоновому потоці - сама функція нічого не малює в Tkinter
            # і ніколи не кидає виняток назовні.
            self.after(0, lambda n=len(results): self._notify_analysis_done(n))

            # self.after потрібен, щоб оновлення інтерфейсу сталося
            # в ГОЛОВНОМУ потоці, а не в фоновому - так безпечно для Tkinter
            self.after(0, lambda: self.show_results(results, prompt_text, total_elapsed_sec, idle_sec))

        except MissingApiKeyError:
            # _set_analysis_running(False) - ПЕРШИМ і окремо від
            # логування/статусу нижче (див. велике пояснення в
            # except Exception нижче) - розблокування інтерфейсу не
            # повинно залежати від того, чи вдасться print()/логування.
            self.after(0, lambda: self._set_analysis_running(False))
            try:
                print("[log] Помилка: не знайдено API-ключ Gemini")
                self._set_status_async("status_error_missing_key")
                self._set_progress_async(None)
            except Exception:  # noqa: BLE001 - див. коментар в except Exception нижче
                pass
            self.after(0, lambda: self._open_api_key_dialog(is_first_setup=True))

        except GeminiApiError as error:
            self.after(0, lambda: self._set_analysis_running(False))
            try:
                print(f"[log] Помилка Gemini ({error.kind}): {error}")
                status_key = GEMINI_ERROR_STATUS_KEYS.get(error.kind)
                if status_key:
                    self._set_status_async(status_key)
                else:
                    # "unknown" та інші непередбачені kind - показуємо
                    # загальний текст помилки, щоб хоч щось було видно.
                    self._set_status_async("status_error", error=error)
                self._set_progress_async(None)
            except Exception:  # noqa: BLE001 - див. коментар в except Exception нижче
                pass

        except Exception as error:
            # П3 плану: реальний випадок - диск скінчився (ENOSPC) десь у
            # цьому try (напр. history_store.append_session, а не лише в
            # уже підстрахованому вище кроці скріншотів), і print()/логування
            # ТУТ, У ЦЬОМУ Ж except-блоці, ТЕЖ впиралось у той самий повний
            # диск (лог пишеться у файл) - друга помилка ПОСЕРЕД обробки
            # першої йшла вище по стеку неспійманою, і
            # _set_analysis_running(False) НІКОЛИ не викликався. Зовні це
            # виглядало як "програма зависла" - інтерфейс назавжди лишався
            # в стані "триває аналіз", хоча фоновий потік уже помер.
            #
            # ТЕПЕР: розблокування інтерфейсу - ПЕРШИМ, до будь-якого
            # логування/статусу, і НЕ всередині try/except нижче - навіть
            # якщо print()/self._set_status_async() зараз впадуть ЗНОВУ
            # (диск усе ще повний), інтерфейс однаково розблокується.
            # Саме логування - у СВОЄМУ try/except: best-effort, не
            # критичний для коректності (на відміну від розблокування UI).
            self.after(0, lambda: self._set_analysis_running(False))
            try:
                if isinstance(error, OSError) and error.errno == 28:
                    print(f"[log] Місце на диску скінчилось: {error}")
                else:
                    print(f"[log] Непередбачена помилка: {error}")
                self._set_status_async("status_error", error=error)
                self._set_progress_async(None)
            except Exception:  # noqa: BLE001 - саме логування не повинно
                # каскадом ховати справжню причину падіння вище і, головне,
                # не повинно завадити розблокуванню інтерфейсу (воно вже
                # сталось рядком вище, до цього try).
                pass

        finally:
            # Кеш повнорозмірних кадрів - тимчасовий: потрібні кадри вже
            # перенесені в скріншоти, решту (відкинуті Gemini, скасований
            # чи аварійний аналіз) видаляємо.
            if full_res_dir:
                shutil.rmtree(full_res_dir, ignore_errors=True)

    def _notify_analysis_done(self, count):
        """Системне сповіщення про завершення аналізу. Викликається з
        головного потоку (after): звідти безпечно спитати Tk про фокус.
        Не показуємо, якщо вікно програми зараз активне (користувач і так
        бачить результат). Сам показ - в окремому потоці: на деяких
        бекендах plyer (Windows) notify() блокує до кінця timeout."""
        if not (notify_tools.is_available() or sys.platform == "darwin"):
            return
        try:
            window_active = self.state() != "iconic" and self.focus_displayof() is not None
        except Exception:  # noqa: BLE001
            window_active = False
        if window_active:
            return
        title = self.loc.t("notification_analysis_complete_title")
        message = self.loc.t("notification_analysis_complete_message", count=count)
        threading.Thread(target=notify_tools.notify, args=(title, message), daemon=True).start()

    def _set_status(self, key, color=None, **kwargs):
        """
        Встановлює текст статусу за ключем перекладу (або прибирає його,
        якщо key=None) і запам'ятовує цей ключ, щоб коректно перемалювати
        статус, якщо користувач перемкне мову інтерфейсу.

        color - необов'язковий колір тексту (кортеж світла/темна тема,
        як STATUS_COLOR_ERROR). Якщо не передано - лишається звичайний
        нейтральний колір лейбла (жоден виклик, який раніше нічого про
        колір не знав, поведінки не змінює).
        """
        self._status_key = key
        self._status_kwargs = kwargs
        self._status_color = color or STATUS_COLOR_NEUTRAL
        text = self.loc.t(key, **kwargs) if key else ""
        self.status_label.configure(text=text, text_color=self._status_color)

    def _set_status_async(self, key, color=None, **kwargs):
        """Те саме, що _set_status, але безпечно з фонового потоку."""
        self.after(0, lambda: self._set_status(key, color=color, **kwargs))

    def _set_progress(self, value, allow_decrease=False):
        """
        Показує/оновлює капсулу прогресу аналізу (0-100). Значення НЕ
        стрибає миттєво до value - воно стає новою ЦІЛЛЮ, а відображена
        цифра плавно "доганяє" її через _animate_progress_step() (тому й
        20% -> 90% виглядає як плавний біг, а не різкий стрибок).

        Сигнал "сховати капсулу ОДРАЗУ, без анімації" - це саме
        value=None (скасування/помилка/очищення), а НЕ 0. Раніше тут
        ховало будь-яке value<=0 - і це ламалось на першому ж чесному
        нулі: on_cut_progress() у run_pipeline() передає fraction=0.0
        рівно тоді, коли extract_frames() щойно обробила САМИЙ ПЕРШИЙ
        кандидат кадру (percent==0, це не помилка й не завершення, а
        законний "0% зробленої роботи") - і капсула, щойно показана на
        старті аналізу, миттєво ховалась і за секунду виринала знову
        заново, тільки-но прогрес зрушувався за нуль. Тепер 0 - це
        просто ЗВИЧАЙНЕ значення прогресу (капсула лишається видимою
        і показує чесні "0%"), і окремий сентинел (0.01) на старті
        аналізу вже не потрібен.

        allow_decrease=False (за замовчуванням) - НОВЕ: value, менший за
        поточну ЦІЛЬ (_progress_target), ігнорується (капсула лишається,
        де була). БУЛО (баг, "бар ходить туди-сюди"): _progress_target
        тут перезаписувався БЕЗУМОВНО, а окрема "підкрутка в очікуванні"
        (_progress_trickle_tick, тікає раз на 700мс, поки чекаємо
        відповіді Gemini) рухає ТОЙ САМИЙ _progress_target вперед НЕЗАЛЕЖНО
        і НЕ синхронізуючись із max_combined_percent у run_pipeline().
        Коли потім приходила чергова РЕАЛЬНА подія (done_batches,
        frame_count) і _push_combined_progress() рахував нове значення -
        воно порівнювалось лише з max_combined_percent (яким апдейтить
        сам _push_combined_progress), а не з фактичним _progress_target
        (який "підкрутка" вже могла встигнути зсунути ВИЩЕ) - тож цей
        виклик міг відкотити видиму цифру НАЗАД, нижче того, де вона вже
        встигла побувати. Єдине місце, де відкат справді потрібен - explicit
        скидання на старті НОВОГО аналізу (value=0 одразу після
        _set_analysis_running(True), коли попередній прогін міг лишити
        ціль на 90-100) - там викликач явно передає allow_decrease=True.
        """
        if value is None:
            if self._progress_anim_job is not None:
                self.after_cancel(self._progress_anim_job)
                self._progress_anim_job = None
            if self._progress_hide_job is not None:
                self.after_cancel(self._progress_hide_job)
                self._progress_hide_job = None
            self._progress_trickle_stop()
            self._progress_target = 0
            self._progress_displayed = 0.0
            self.progress_bar.set(0)
            self.progress_percent_label.configure(text="")
            self.progress_frame.pack_forget()
            return

        # Будь-яке НЕ-None значення означає "живий" стан прогресу - тож
        # застарілий відкладений "сховати після 100%" (див. коментар
        # вище і show_results) більше не актуальний і має бути
        # скасований тут теж, а не лише в гілці None вище: інакше він
        # спрацює трохи ПІЗНІШЕ, посеред уже НОВОГО виклику _set_progress(),
        # і сховає щойно показану капсулу нового аналізу.
        if self._progress_hide_job is not None:
            self.after_cancel(self._progress_hide_job)
            self._progress_hide_job = None

        new_target = min(max(value, 0), 100)
        if not allow_decrease and new_target < self._progress_target:
            # Стале/"перегнане підкруткою" значення - див. докстрінг
            # allow_decrease вище. Мовчки ігноруємо: капсула лишається
            # там, де вже була, наступна чесна вища подія її й так
            # посуне далі вперед.
            return
        self._progress_target = new_target

        if not self.progress_frame.winfo_ismapped():
            self.progress_frame.pack(side="left", padx=(10, 0))

        if self._progress_anim_job is None:
            self._animate_progress_step()

    def _animate_progress_step(self):
        """Один крок плавної анімації - зсуває displayed на частку
        різниці до target і планує наступний крок через after(), доки
        не "доїде" достатньо близько (тоді зупиняється, щоб не молотити
        CPU даремно, коли прогрес і так уже на місці)."""
        diff = self._progress_target - self._progress_displayed
        if abs(diff) < 0.3:
            self._progress_displayed = self._progress_target
            self._render_progress()
            self._progress_anim_job = None
            return

        self._progress_displayed += diff * PROGRESS_ANIM_SPEED
        self._render_progress()
        self._progress_anim_job = self.after(PROGRESS_ANIM_INTERVAL_MS, self._animate_progress_step)

    def _render_progress(self):
        self.progress_bar.set(self._progress_displayed / 100)
        self.progress_percent_label.configure(text=f"{round(self._progress_displayed)}%")

    def _set_progress_async(self, value):
        """Те саме, що _set_progress, але безпечно з фонового потоку."""
        self.after(0, lambda: self._set_progress(value))

    def _progress_trickle_set_ceiling(self, ceiling):
        """Вмикає/переставляє "стелю" підкрутки (див. коментар над
        PROGRESS_TRICKLE_* на початку файлу). Викликається щоразу, коли
        відомо, скільки кадрів зараз "у польоті" - тобто стеля лише
        РОСТЕ разом з реальним прогресом, ніколи не падає.

        Не рухає _progress_target напряму - лише задає межу, до якої
        _progress_trickle_tick() обережно повзе, поки чекаємо реальної
        події. Якщо тік уже й так іде - нічого перезапускати не треба,
        він сам підхопить нову (вищу) стелю на наступному кроці."""
        self._progress_trickle_ceiling = ceiling
        if self._progress_trickle_job is None:
            self._progress_trickle_job = self.after(PROGRESS_TRICKLE_INTERVAL_MS, self._progress_trickle_tick)

    def _progress_trickle_set_ceiling_async(self, ceiling):
        self.after(0, lambda: self._progress_trickle_set_ceiling(ceiling))

    def _progress_trickle_stop(self):
        """Вимикає підкрутку - викликається, щойно з'являється чесний
        сигнал, який робить подальше "вгадування" непотрібним (кінець
        очікування Gemini, скасування, помилка, приховання капсули)."""
        if self._progress_trickle_job is not None:
            self.after_cancel(self._progress_trickle_job)
            self._progress_trickle_job = None
        self._progress_trickle_ceiling = None

    def _progress_trickle_stop_async(self):
        self.after(0, self._progress_trickle_stop)

    def _progress_trickle_tick(self):
        """Один крок підкрутки: непомітно зсуває _progress_target
        ближче до _progress_trickle_ceiling (але ніколи впритул - лишає
        запас PROGRESS_TRICKLE_MIN_GAP, щоб реальне підтвердження, коли
        прийде, завжди виглядало як чесний, помітний крок уперед, а не
        "0.1% за раз"). Зупиняється сама, щойно розрив стає малим -
        новий виклик _progress_trickle_set_ceiling() (з вищою стелею)
        перезапустить її знову."""
        self._progress_trickle_job = None
        if self._progress_trickle_ceiling is None:
            return
        gap = self._progress_trickle_ceiling - self._progress_target
        if gap <= PROGRESS_TRICKLE_MIN_GAP:
            return
        self._progress_target = min(
            self._progress_target + gap * PROGRESS_TRICKLE_GAIN,
            self._progress_trickle_ceiling,
        )
        if self._progress_anim_job is None:
            self._animate_progress_step()
        self._progress_trickle_job = self.after(PROGRESS_TRICKLE_INTERVAL_MS, self._progress_trickle_tick)

    def show_results(self, results, prompt_text="", elapsed_sec=None, idle_sec=0.0):
        self._set_analysis_running(False)
        # elapsed_sec приходить із run_pipeline (загальний час нарізки +
        # аналізу Gemini). None лишаємо на випадок, якщо show_results
        # колись викличуть звідкись іще без цього аргументу - тоді просто
        # покажемо 00:00:00 замість падіння.
        elapsed_str = format_time(elapsed_sec) if elapsed_sec is not None else format_time(0)
        if idle_sec >= 60:
            # Комп'ютер простояв (сон) - чесно кажемо, скільки часу не враховано.
            self._set_status(
                "status_done_idle", count=len(results), elapsed=elapsed_str, idle=format_time(idle_sec)
            )
        else:
            self._set_status("status_done", count=len(results), elapsed=elapsed_str)
        failed = getattr(self, "_failed_batches", 0)
        if failed:
            # Частина пачок не відповіла - результат неповний, кажемо про це прямо.
            self._set_status("status_done_partial", color=WARNING_COLOR,
                             count=len(results), elapsed=elapsed_str, failed=failed)
        self._last_prompt_text = prompt_text

        # Коротка пауза на 100%, щоб користувач встиг побачити
        # завершення, а тоді капсула ховається - інакше застосунок
        # "забуває" прибрати прогрес і той висить назавжди на 100%.
        # Job зберігаємо (а не просто плануємо і забуваємо) - якщо
        # користувач встигне натиснути "Почати аналіз" ще РАЗ менш ніж
        # за PROGRESS_HOLD_AT_100_MS (наприклад, короткий кліп
        # аналізується за секунди), start_analysis() через
        # _set_progress(0) відразу побачить цей job і скасує його,
        # інакше він спрацював би вже ПІД ЧАС нового аналізу і сховав
        # би щойно показану капсулу новим стартом.
        self._set_progress(100)
        self._progress_hide_job = self.after(PROGRESS_HOLD_AT_100_MS, lambda: self._set_progress(None))

        # Прибираємо кнопки з минулого звіту
        for widget in self.report_list.winfo_children():
            widget.destroy()
        self.report_buttons = []  # список (кнопка, result) - потрібен для підсвітки
        self.current_result = None
        self._no_results_label = None

        if not results:
            self._no_results_label = ctk.CTkLabel(
                self.report_list, text=self.loc.t("nothing_found_list"), text_color=STATUS_COLOR_NEUTRAL
            )
            self._no_results_label.pack(pady=10)

            self._photo_state = "no_results"
            self._set_caption("", self.loc.t("nothing_found_description", query=prompt_text))

            # Чистимо праву панель з фото (див. коментар у _create_photo_label)
            self.current_pil_image = None
            self.photo_label.destroy()
            self._create_photo_label()
            self.photo_label.configure(text=self.loc.t("nothing_found_photo"))

            self.export_pdf_button.configure(state="disabled")
            self.export_full_report_button.configure(state="disabled")
            return

        self.export_full_report_button.configure(state="normal")

        for result in results:
            row = self._make_report_row(result)
            row.pack(fill="x", pady=3, padx=3)
            self.report_buttons.append((row, result))

        self.show_photo(results[0])

    def _make_report_row(self, result):
        """Рядок списку звіту в єдиному стилі: час (жирний, колонка фіксованої
        ширини) ліворуч, опис праворуч, все вирівняно по лівому/верхньому краю."""
        row = ctk.CTkFrame(self.report_list, fg_color=BUTTON_COLOR, corner_radius=6)
        row.grid_columnconfigure(1, weight=1)

        time_lbl = ctk.CTkLabel(
            row, text=result["time_str"], width=REPORT_TIME_COL_WIDTH,
            font=self.font_report_time, text_color="white", anchor="w", justify="left",
        )
        time_lbl.grid(row=0, column=0, sticky="nw", padx=(10, 6), pady=7)

        description = result["description"]
        short = description if len(description) <= 45 else description[:45].rstrip() + "…"
        text_lbl = ctk.CTkLabel(
            row, text=short, font=self.font_report_text, text_color="white",
            anchor="w", justify="left", wraplength=150,
        )
        text_lbl.grid(row=0, column=1, sticky="nw", padx=(0, 8), pady=7)

        def on_enter(event=None):
            if self.current_result is not result:
                row.configure(fg_color=REPORT_ROW_HOVER_COLOR)

        def on_leave(event=None):
            row.configure(
                fg_color=BUTTON_COLOR_SELECTED if self.current_result is result else BUTTON_COLOR
            )

        for widget in (row, time_lbl, text_lbl):
            widget.bind("<Button-1>", lambda e, r=result: self.show_photo(r))
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)
            try:
                widget.configure(cursor="hand2")
            except Exception:  # noqa: BLE001 - курсор не критичний
                pass
        return row

    def show_photo(self, result):
        self.current_result = result
        self._photo_state = "image"

        # Підсвічуємо темно-синім ту кнопку, чий момент зараз відкрито,
        # у решти повертаємо звичайний колір. Заодно запам'ятовуємо саму
        # кнопку - вона потрібна нижче, щоб прокрутити список до неї.
        selected_btn = None
        for btn, r in self.report_buttons:
            is_selected = r is result
            btn.configure(fg_color=BUTTON_COLOR_SELECTED if is_selected else BUTTON_COLOR)
            if is_selected:
                selected_btn = btn

        if selected_btn is not None:
            self._scroll_report_to_button(selected_btn)

        self._set_caption(result["time_str"], result["description"])
        self.export_pdf_button.configure(state="normal")

        # Відкриваємо оригінал ОДИН раз і зберігаємо - далі просто
        # перераховуємо розмір під поточний розмір вікна (див. _render_photo),
        # не читаючи файл заново на кожен ресайз.
        try:
            with Image.open(result["screenshot_path"]) as opened:
                opened.load()  # одразу читаємо пікселі, щоб не тримати файл відкритим
                self.current_pil_image = opened.copy()
        except Exception as error:  # noqa: BLE001
            print(f"[log] Не вдалось відкрити скріншот {result['screenshot_path']}: {error}")
            self.current_pil_image = None
            # Перестворюємо лейбл (див. коментар у _create_photo_label), щоб
            # зникла попередня картинка, і пишемо причину замість неї.
            self.photo_label.destroy()
            self._create_photo_label()
            self.photo_label.configure(text=self.loc.t("photo_open_error", error=error))
            return
        self.photo_label.configure(cursor="hand2")
        self._render_photo()

        # Якщо зараз відкрито повноекранний перегляд - оновлюємо і його
        # (наприклад, коли гортаємо звіт стрілками прямо під час перегляду)
        if self.lightbox_frame:
            self.zoom_index = 0
            self.pan_x = 0
            self.pan_y = 0
            if self.lightbox_caption_label is not None:
                self.lightbox_time_label.configure(text=result["time_str"])
                self.lightbox_caption_label.configure(text=result["description"])
                fit = getattr(self, "_lightbox_fit_caption", None)
                if fit is not None:
                    fit()  # новий опис може мати іншу кількість рядків
            self._render_lightbox_image()

    @staticmethod
    def _result_caption(result):
        """Підпис моменту звіту - "00:01:07 — опис". Один формат і для
        головного екрану, і для повноекранного перегляду."""
        return f"{result['time_str']} — {result['description']}"

    # ---------------- Експорт у PDF ----------------

    def export_current_pdf(self):
        """Зберігає ПОТОЧНИЙ відкритий момент звіту одним PDF-файлом."""
        if not self.current_result:
            return

        default_name = build_pdf_filename(self.current_result["time_str"])
        path = filedialog.asksaveasfilename(
            title=self.loc.t("export_pdf_dialog_title"),
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return  # користувач натиснув "Скасувати"

        video_name = os.path.basename(self.video_path) if self.video_path else ""
        try:
            export_frame_to_pdf(
                path,
                self.current_result["screenshot_path"],
                self.current_result["time_str"],
                self.current_result["description"],
                video_name=video_name,
                video_path=self.video_path or "",
                lang=self.loc.language,
            )
            self._set_status("export_pdf_success", path=os.path.basename(path))
        except Exception as error:
            self._set_status("export_pdf_error", error=error)

    def export_full_report_pdf(self):
        """Зберігає УВЕСЬ звіт (усі знайдені моменти) одним PDF-файлом,
        по сторінці на кожен момент, у тому ж порядку, що й у списку зліва."""
        if not self.report_buttons:
            return

        results = [result for _, result in self.report_buttons]
        default_name = build_full_report_filename()
        path = filedialog.asksaveasfilename(
            title=self.loc.t("export_pdf_dialog_title"),
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return

        video_name = os.path.basename(self.video_path) if self.video_path else ""
        try:
            export_report_to_pdf(path, results, video_name=video_name,
                                 video_path=self.video_path or "", lang=self.loc.language)
            self._set_status("export_pdf_success", path=os.path.basename(path))
        except Exception as error:
            self._set_status("export_pdf_error", error=error)

    # ---------------- Адаптивний показ фото (без спотворень і без рамок) ----------------

    def _on_photo_container_resize(self, event=None):
        # Ресайз вікна дуже часто смикає <Configure> - перераховуємо
        # розмір картинки не на кожну подію, а з невеликою затримкою
        # (debounce), інакше інтерфейс гальмуватиме при розтягуванні.
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(RESIZE_DEBOUNCE_MS, self._render_photo)

    def _render_photo(self):
        self._resize_job = None
        if self.current_pil_image is None:
            return

        container_w = self.photo_container.winfo_width()
        container_h = self.photo_container.winfo_height()
        fitted = self._fit_image(self.current_pil_image, container_w, container_h)
        if fitted is None:
            return

        new_w, new_h = fitted
        ctk_image = ctk.CTkImage(
            light_image=self.current_pil_image,
            dark_image=self.current_pil_image,
            size=(new_w, new_h),
        )
        self.photo_label.configure(image=ctk_image, text="")
        self.photo_label.image = ctk_image  # тримаємо посилання, щоб картинку не зібрав GC

    @staticmethod
    def _fit_image(pil_image, available_w, available_h, min_size=50):
        """
        Рахує розмір картинки так, щоб вона ЦІЛКОМ вписалась у доступне
        місце, зберігши пропорції (contain-fit) - вертикальне 9:16 відео
        залишиться вузьким і високим, широке кіно залишиться широким і
        низьким, нічого не розтягується і не обрізається.
        """
        if available_w < min_size or available_h < min_size:
            return None  # вікно ще не намальоване / надто маленьке

        img_w, img_h = pil_image.size
        if img_w <= 0 or img_h <= 0:
            return None

        scale = min(available_w / img_w, available_h / img_h)
        new_w = max(1, int(img_w * scale))
        new_h = max(1, int(img_h * scale))
        return new_w, new_h

    def _scroll_report_to_button(self, btn):
        """
        Прокручує список звіту так, щоб вказана кнопка була видна -
        використовується, коли гортаємо звіт стрілками і поточний
        момент "заїхав" за межі видимої області списку.

        CTkScrollableFrame всередині - це звичайний Canvas з прокруткою,
        тому просто рахуємо, на яку частку (0..1) висоти всього
        списку потрібно зсунутись, і рухаємо canvas.yview_moveto() туди.
        """
        canvas = getattr(self.report_list, "_parent_canvas", None)
        if canvas is None:
            return  # на випадок іншої версії customtkinter - просто не скролимо

        self.report_list.update_idletasks()
        bbox = canvas.bbox("all")
        if not bbox:
            return

        total_height = bbox[3] - bbox[1]
        if total_height <= 0:
            return

        btn_top = btn.winfo_y()
        btn_bottom = btn_top + btn.winfo_height()

        canvas_height = canvas.winfo_height()
        visible_top = canvas.yview()[0] * total_height
        visible_bottom = visible_top + canvas_height

        if btn_top < visible_top:
            # кнопка вище видимої частини списку - підскролити вгору до неї
            canvas.yview_moveto(btn_top / total_height)
        elif btn_bottom > visible_bottom:
            # кнопка нижче видимої частини списку - підскролити вниз, щоб потрапила в кадр
            new_top = max(0, btn_bottom - canvas_height)
            canvas.yview_moveto(new_top / total_height)

    # ---------------- Повноекранний перегляд (лайтбокс) ----------------

    def toggle_lightbox(self, event=None):
        if self.current_pil_image is None:
            return
        if self.lightbox_frame:
            self.close_lightbox()
        else:
            self.open_lightbox()

    def open_lightbox(self):
        # Скидаємо зум і зміщення на кожне відкриття - завжди починаємо зі 100%
        self.zoom_index = 0
        self.pan_x = 0
        self.pan_y = 0
        self._drag_start = None
        self.lightbox_image_item = None

        # Підкладка на все вікно - затемнює все інше (див. LIGHTBOX_BG_COLOR)
        self.lightbox_frame = ctk.CTkFrame(self, fg_color=LIGHTBOX_BG_COLOR, corner_radius=0)
        self.lightbox_frame.place(x=0, y=0, relwidth=1, relheight=1)
        # Клік по порожньому фону (не по кнопках і не по фото) - закриває
        self.lightbox_frame.bind("<Button-1>", lambda e: self.close_lightbox())

        # ---- верхня панель: зліва кнопка "Закрити", справа зум, по центру
        # підпис (час + опис) поточного моменту ----
        controls = ctk.CTkFrame(self.lightbox_frame, fg_color="transparent", height=48)
        controls.pack(fill="x", padx=20, pady=15)
        controls.pack_propagate(False)   # висота панелі фіксована - підпис (place) її не розтягує

        ctk.CTkButton(
            controls, text=self.loc.t("lightbox_close"), width=100, command=self.close_lightbox
        ).pack(side="left")

        ctk.CTkButton(
            controls, text="+", width=40, command=self.zoom_in
        ).pack(side="right", padx=(5, 0))

        self.zoom_label = ctk.CTkLabel(controls, text="100%", width=60)
        self.zoom_label.pack(side="right", padx=5)

        ctk.CTkButton(
            controls, text="-", width=40, command=self.zoom_out
        ).pack(side="right", padx=(0, 5))

        # Підпис - рівно по центру панелі (place, а не pack, щоб не залежати
        # від ширини кнопок з боків). Довгий опис переноситься на 2 рядки,
        # але не заходить під кнопки: wraplength підганяється під ширину.
        time_text = self.current_result["time_str"] if self.current_result else ""
        desc_text = self.current_result["description"] if self.current_result else ""
        caption_frame = ctk.CTkFrame(controls, fg_color="transparent")
        caption_frame.place(relx=0.5, rely=0.5, anchor="center")
        # Той самий стиль, що й у звіті: час жирним у фіксованій колонці + текст праворуч.
        self.lightbox_time_label = ctk.CTkLabel(
            caption_frame, text=time_text, width=90, font=self.font_report_time_big,
            text_color="white", anchor="w", justify="left",
        )
        self.lightbox_time_label.grid(row=0, column=0, sticky="nw", padx=(0, 10))
        self.lightbox_caption_label = ctk.CTkLabel(
            caption_frame, text=desc_text, font=self.font_report_text_big,
            text_color="white", anchor="w", justify="left", wraplength=500,
        )
        self.lightbox_caption_label.grid(row=0, column=1, sticky="nw")

        def fit_caption(event=None):
            """Підганяє підпис під панель: ширина переносу - щоб не заходити
            під кнопки (по 200 px з кожного боку), висота панелі - щоб довгий
            опис на 3+ рядки не обрізався (раніше панель була завжди 48 px)."""
            label = self.lightbox_caption_label
            if label is None or not label.winfo_exists():
                return
            width = event.width if event is not None else controls.winfo_width()
            label.configure(wraplength=max(200, width - 2 * 200 - 100))
            caption_frame.update_idletasks()
            needed = max(48, caption_frame.winfo_reqheight() + 8)
            if abs(int(controls.cget("height")) - needed) > 1:
                controls.configure(height=needed)

        self._lightbox_fit_caption = fit_caption
        controls.bind("<Configure>", fit_caption)

        # ---- канвас із самим фото - по ньому тягаємо мишкою (якщо зум > 100%) ----
        self.lightbox_canvas = tk.Canvas(
            self.lightbox_frame, bg=LIGHTBOX_BG_COLOR, highlightthickness=0
        )
        self.lightbox_canvas.pack(
            fill="both", expand=True, padx=LIGHTBOX_PADDING, pady=(0, LIGHTBOX_PADDING)
        )
        self.lightbox_canvas.bind("<Button-1>", self._on_lightbox_press)
        self.lightbox_canvas.bind("<B1-Motion>", self._on_lightbox_drag)
        self.lightbox_canvas.bind("<Configure>", self._on_lightbox_resize)

        self.lightbox_frame.lift()
        self._render_lightbox_image()

    def close_lightbox(self):
        if self.lightbox_frame:
            self.lightbox_frame.destroy()
        self.lightbox_frame = None
        self.lightbox_canvas = None
        self.lightbox_image_item = None
        self.lightbox_caption_label = None
        self.lightbox_time_label = None

    def zoom_in(self):
        if self.zoom_index < len(ZOOM_LEVELS) - 1:
            self.zoom_index += 1
            self.pan_x = 0
            self.pan_y = 0
            self._render_lightbox_image()

    def zoom_out(self):
        if self.zoom_index > 0:
            self.zoom_index -= 1
            self.pan_x = 0
            self.pan_y = 0
            self._render_lightbox_image()

    def _on_lightbox_press(self, event):
        # Запам'ятовуємо, звідки почали тягнути мишкою
        self._drag_start = (event.x, event.y)

    def _on_lightbox_drag(self, event):
        if self._drag_start is None or self.lightbox_image_item is None:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self.pan_x += dx
        self.pan_y += dy
        self._drag_start = (event.x, event.y)
        # Просто рухаємо вже намальовану картинку - без перерахунку
        # розміру, це швидко і не гальмує під час перетягування
        self.lightbox_canvas.move(self.lightbox_image_item, dx, dy)

    def _on_lightbox_resize(self, event=None):
        if self._resize_job:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(RESIZE_DEBOUNCE_MS, self._render_lightbox_image)

    def _render_lightbox_image(self):
        self._resize_job = None
        if not self.lightbox_frame or self.current_pil_image is None:
            return

        canvas_w = self.lightbox_canvas.winfo_width()
        canvas_h = self.lightbox_canvas.winfo_height()
        fitted = self._fit_image(self.current_pil_image, canvas_w, canvas_h)
        if fitted is None:
            return
        base_w, base_h = fitted

        zoom = ZOOM_LEVELS[self.zoom_index]
        new_w = max(1, int(base_w * zoom))
        new_h = max(1, int(base_h * zoom))

        resized_image = self.current_pil_image.resize((new_w, new_h))
        self.lightbox_photo = ImageTk.PhotoImage(resized_image)

        center_x = canvas_w / 2 + self.pan_x
        center_y = canvas_h / 2 + self.pan_y

        self.lightbox_canvas.delete("all")
        self.lightbox_image_item = self.lightbox_canvas.create_image(
            center_x, center_y, anchor="center", image=self.lightbox_photo
        )

        self.zoom_label.configure(text=f"{int(zoom * 100)}%")


if __name__ == "__main__":
    # ОБОВ'ЯЗКОВО для зібраного .exe на Windows: video_tools використовує
    # ProcessPoolExecutor, а без freeze_support() кожен дочірній процес
    # заново запускав би весь застосунок (купа вікон). Має бути ПЕРШИМ
    # рядком тут, до створення App().
    import multiprocessing
    multiprocessing.freeze_support()

    app = App()
    try:
        app.mainloop()   # виходить з циклу подій, коли викликано self.quit()
    except KeyboardInterrupt:
        logger.info("Отримано KeyboardInterrupt (Ctrl+C / Stop) - завершую")
        # Кнопка "Стоп" у PyCharm (і Ctrl+C у терміналі) шле процесу
        # SIGINT НАПРЯМУ, минаючи protocol("WM_DELETE_WINDOW",...) -
        # тобто наш акуратний self.quit() у _on_close_window() просто
        # не встигає спрацювати. mainloop() при SIGINT кидає
        # KeyboardInterrupt і виходить з циклу сам, але вікно на той
        # момент ще НЕ знищене - ловимо виняток тут і завершуємо так
        # само акуратно, як і при звичайному закритті хрестиком.
        app.quit()
    logger.info("Головний цикл завершено, закриваю вікно")
    app.shutdown()   # спершу скасовуємо всі відкладені after(), потім знищуємо
                     # вікно - вже ПІСЛЯ зупинки mainloop, тож відкладеним
                     # колбекам нема за що зачепитися
    print("[log] Вікно закрито, програма завершується нормально")

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
