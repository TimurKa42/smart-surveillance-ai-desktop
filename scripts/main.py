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

# main.py, тут головний файл з вікном програми (кастомтікінтер)
#
# як користуватись, щоб не забути:
# 1) обираю відео (кнопкою або перетягуванням)
# 2) пишу, кого або що шукати
# 3) обираю тип аналізу: «Швидкий» або «Детально»
# 4) натискаю «Почати аналіз»
# 5) дивлюсь звіт: зліва список моментів, справа фото
#
# важка робота (нарізка відео і запити до джемині) іде в video_tools.py
# і gemini_tools.py. тут тільки інтерфейс

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

# збірка апп з файндера стартує з робочої теки "/", де писати не можна
# переходжу в теку даних користувача, щоб logs/, .env і settings.json працювали
if app_paths.is_frozen():
    os.chdir(app_paths.user_data_dir())

from app_logging import setup_logging

setup_logging()
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

# дрег енд дроп файлів з ос. якщо бібліотеки нема, програма працює без нього
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _DND_IMPORT_OK = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    _DND_IMPORT_OK = False

# беру ДнДврапер, а не ТкінтерДнД.Тк: Тк уже є в базовому класі ЦТК,
# і два Тк дали б конфлікт
_APP_BASE_CLASSES = (ctk.CTk, TkinterDnD.DnDWrapper) if _DND_IMPORT_OK else (ctk.CTk,)

# код помилки з gemini_tools, на ключ тексту для статусу
# "unknown" сюди не входить, для нього показую сам текст помилки
GEMINI_ERROR_STATUS_KEYS = {
    "network": "status_error_network",
    "timeout": "status_error_timeout",
    "rate_limit": "status_error_rate_limit",
    "quota_daily": "status_error_quota_daily",
    "invalid_key": "status_error_invalid_key",
    "server": "status_error_server",
    "blocked": "status_error_blocked",
}

# сторінка, де беру безкоштовний ключ джемині
API_KEY_URL = "https://aistudio.google.com/apikey"

# прогрес-бар плавно доганяє ціль: кожні 30 мс рухається на 18% різниці
PROGRESS_ANIM_INTERVAL_MS = 30
PROGRESS_ANIM_SPEED = 0.18

# скільки секунд після 100% показую капсулу прогресу
PROGRESS_HOLD_AT_100_MS = 3000

# зелений колір прогрес-бару (однаковий у світлій і темній темі)
PROGRESS_BAR_COLOR = "#33C761"

# максимум символів у полі запиту
MAX_PROMPT_CHARS = 200

# тексти, яких нема в localization.py, підмішую через _install_extra_strings, щоб не забути
EXTRA_STRINGS = {
    "ua": {
        "welcome_title": "Вітаємо у LookBack!",
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
        "welcome_title": "Welcome to LookBack!",
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

# ширина колонки часу в списку звіту
REPORT_TIME_COL_WIDTH = 66
REPORT_ROW_HOVER_COLOR = "#144870"

# тека для скріншотів
SCREENSHOTS_FOLDER = os.path.join(app_paths.user_data_dir(), "screenshots")


# ---------- іконка вікон (тільки віндовс) ----------
# кастомтікінтер ставить свою іконку в кожне вікно, підміняю її на нашу
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
        except Exception:  # noqa: BLE001 - іконка не важлива
            pass

    ctk.CTkToplevel._windows_set_titlebar_icon = _set_own_titlebar_icon
    ctk.CTk._windows_set_titlebar_icon = _set_own_titlebar_icon

# максимум довжини відео: 70 хвилин
MAX_VIDEO_DURATION_SEC = 70 * 60

# усі режими беруть одну модель, вона дешева і має багато запитів на добу
ANALYSIS_MODEL_NAME = "gemini-3.5-flash-lite"

# режим «Швидкий»: 200 кадрів (5 запитів по 40), найслабше "мислення", низька роздільність
FAST_MODE_FRAMES = 200
FAST_MODE_THINKING_LEVEL = "minimal"
FAST_MODE_MEDIA_RESOLUTION = "MEDIA_RESOLUTION_LOW"

# режим «Детально»: 4 рівні слайдера
# (ключ назви, thinkingLevel, кількість кадрів, mediaResolution)
# кількості кратні 40, щоб не було маленького останнього запиту
DETAIL_LEVELS = [
    ("detail_level_weak", "minimal", 400, "MEDIA_RESOLUTION_LOW"),
    ("detail_level_medium", "low", 800, "MEDIA_RESOLUTION_LOW"),
    ("detail_level_high", "medium", 1200, "MEDIA_RESOLUTION_MEDIUM"),
    ("detail_level_max", "high", 2000, "MEDIA_RESOLUTION_HIGH"),
]
DEFAULT_DETAIL_LEVEL_INDEX = 1  # «Середній», 800 кадрів

BUTTON_COLOR = "#1F6AA5"

# кольори кнопок: (світла тема, темна тема)
BUTTON_COLOR_SELECTED = ("#2E86DE", "#123A5C")
MODEL_NEUTRAL_COLOR = ("#C4CDD6", "gray30")
TEXT_COLOR_SELECTED = "white"
TEXT_COLOR_NEUTRAL = ("gray10", "gray90")

# кольори статусів, тільки кортежі, інакше колір не зміниться при зміні теми
STATUS_COLOR_NEUTRAL = ("gray45", "gray65")
STATUS_COLOR_ERROR = ("#C0392B", "#FF6B60")
WARNING_COLOR = ("#B8860B", "#F5C242")

# режими аналізу
MODE_FAST = "fast"
MODE_DETAIL = "detail"
MODEL_OPTIONS = [
    ("model_fast_label", MODE_FAST),
    ("model_detail_label", MODE_DETAIL),
]

# теми: ключ у settings.json, на значення для кастомтікінтер
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

LANGUAGE_OPTIONS = [
    ("ua", "UA"),
    ("en", "EN"),
]

# тип ключа джемині: безкоштовний чи платний, обирає користувач у меню
# від цього залежить, скільки потоків можна запускати одночасно
API_TIER_OPTIONS = [
    ("free", "api_tier_free"),
    ("paid", "api_tier_paid"),
]
DEFAULT_API_TIER = None  # поки не обрано
VALID_API_TIERS = {key for key, _ in API_TIER_OPTIONS}

RESIZE_DEBOUNCE_MS = 60
LIGHTBOX_PADDING = 40
LIGHTBOX_BG_COLOR = "#1a1a1a"

# кроки масштабу у перегляді фото: від 100% до 500%
ZOOM_LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]

MENU_BUTTON_COLOR = "#ED8221"
MENU_BUTTON_HOVER_COLOR = "#FF9E4D"

# червоний для небезпечних дій (видалення)
DANGER_COLOR = "#C0392B"
DANGER_HOVER_COLOR = "#A93226"

SETTINGS_BUTTON_COLOR = "#7B2D3B"
SETTINGS_BUTTON_HOVER_COLOR = "#642230"


# ф-ція патча для скролбара текстбокса, не забути її
# інколи кастомтікінтер викликає перевірку скролбара вже після того,
# як віджет знищено, тоді падає ТклЕррор. ловлю її і виходжу тихо
def _patch_ctk_textbox_scrollbar_check():
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
        self._wipe_on_exit = False  # видалити дані після закриття (кнопка «Видалити всі дані»)
        self._closing = False

        # ---- налаштування з settings.json ----
        self.settings = load_settings()
        # старі файли налаштувань можуть не мати поля api_tier
        self.settings.setdefault("api_tier", DEFAULT_API_TIER)
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
            # ставлю іконку ще раз із затримкою, бо кастомтікінтер її перебиває
            self.after(400, lambda: app_paths.apply_crisp_window_icon(self, APP_ICON_PATH))
            self.after(1500, lambda: app_paths.apply_crisp_window_icon(self, APP_ICON_PATH))

        self.video_path = None
        self.report_buttons = []        # пари (кнопка, результат) для звіту
        self.current_pil_image = None   # оригінал відкритого фото
        self.current_result = None      # момент, який зараз показано

        # прапорець «Зупинити», фоновий потік його перевіряє
        self._cancel_event = threading.Event()
        self._analysis_running = False

        self.selected_mode = None
        self.model_buttons = []         # (кнопка, режим, ключ підпису)
        self.theme_buttons = []         # (кнопка, ключ теми, ключ підпису)
        self.language_buttons = []      # (кнопка, код мови)
        self.api_tier_buttons = []      # (кнопка, ключ типу)

        # ---- рівень деталізації ----
        self.remember_detail_level = self.settings.get("remember_detail_level", False)
        if self.remember_detail_level:
            self.detail_level_index = self.settings.get("detail_level_index", DEFAULT_DETAIL_LEVEL_INDEX)
        else:
            self.detail_level_index = DEFAULT_DETAIL_LEVEL_INDEX

        # вікна меню, налаштувань, історії створюються щоразу заново,
        # тому посилання тримаю тут, щоб перемалювати їх при зміні мови
        self._menu_dialog = None
        self._menu_title_label = None
        self._menu_about_btn = None
        self._menu_exit_btn = None
        self._menu_theme_label = None
        self._menu_lang_label = None
        self._menu_tier_label = None
        self._menu_tier_hint = None
        self._menu_history_btn = None
        self._menu_reset_key_btn = None
        self._menu_wipe_btn = None

        self._about_dialog = None
        self._about_content = None

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
        self._detail_caption_labels = []

        self._model_info_dialog = None
        self._model_info_body_label = None

        self._history_window = None
        self._history_sessions = []
        self._history_selection_mode = False
        self._history_selected_ids = set()
        self._history_scroll = None
        self._history_delete_btn = None
        self._history_select_all_var = None
        self._history_select_all_frame = None
        self._history_select_all_chk = None

        self._history_session_window = None
        self._history_current_session = None
        self._history_session_frames = []
        self._history_frame_selection_mode = False
        self._history_frame_selected_ids = set()
        self._history_frame_scroll = None
        self._history_frame_delete_btn = None
        self._history_frame_select_all_var = None
        self._history_frame_select_all_frame = None
        self._history_frame_select_all_chk = None
        self._history_session_export_btn = None

        # статус запам'ятовую як ключ + параметри, щоб перемалювати при зміні мови
        self._status_key = None
        self._status_kwargs = {}
        self._status_color = STATUS_COLOR_NEUTRAL
        self._no_results_label = None
        self._last_prompt_text = ""
        self._photo_state = "placeholder"  # placeholder / image / no_results

        self._resize_job = None

        # ---- капсула прогресу ----
        self.progress_frame = None
        self.progress_bar = None
        self.progress_percent_label = None
        self._progress_target = 0           # куди йду
        self._progress_displayed = 0.0      # що намальовано зараз
        self._progress_anim_job = None
        self._progress_hide_job = None

        # ---- повноекранний перегляд ----
        self.lightbox_frame = None
        self.lightbox_canvas = None
        self.lightbox_photo = None
        self.lightbox_image_item = None
        self.lightbox_caption_label = None
        self.lightbox_time_label = None
        self._suspend_watchdog = None
        self.zoom_index = 0
        self.pan_x = 0
        self.pan_y = 0
        self._drag_start = None

        self._build_interface()
        self._setup_drag_and_drop()

        self.prompt_box.bind("<Return>", self._on_prompt_enter)
        self.prompt_box.bind("<Shift-Return>", lambda e: None)

        # стрілки гортають звіт, якщо фокус не в полі запиту
        self.bind("<Left>", lambda e: self._navigate_report(-1))
        self.bind("<Up>", lambda e: self._navigate_report(-1))
        self.bind("<Right>", lambda e: self._navigate_report(1))
        self.bind("<Down>", lambda e: self._navigate_report(1))

        # спершу зупиняю мейнлуп, потім знищую вікно
        # інакше фонові колбеки кастомтікінтер падають на мертвому вікні
        self.protocol("WM_DELETE_WINDOW", self._on_close_window)

        # перевірка ключа з затримкою, щоб вікно встигло намалюватись
        self.after(150, self._on_start)

    def _on_close_window(self):
        self._closing = True
        self._cancel_event.set()
        self.quit()

    def report_callback_exception(self, exc, val, tb):
        # під час закриття ТклЕррор це нормально, його не друкую
        if getattr(self, "_closing", False) and isinstance(val, tk.TclError):
            return
        super().report_callback_exception(exc, val, tb)

    # ф-ція shutdown, скасовує всі відкладені таймери і знищує вікно, не забути її
    def shutdown(self):
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

    # ф-ція додає тексти з EXTRA_STRINGS до self.loc.t()
    def _install_extra_strings(self):
        original_t = self.loc.t

        def t(key, **kwargs):
            extra = EXTRA_STRINGS.get(self.loc.language, EXTRA_STRINGS["en"]).get(key)
            if extra is not None:
                return extra.format(**kwargs) if kwargs else extra
            return original_t(key, **kwargs)

        self.loc.t = t

    def _on_start(self):
        # вітання показую один раз, потім перевіряю ключ
        if self.settings.get("welcome_shown"):
            self._ensure_api_key()
        else:
            self._open_welcome_dialog()

    def _open_welcome_dialog(self):
        dialog = self._create_modal_dialog(self.loc.t("welcome_title"), 460, 440)

        def on_close():
            self.settings["welcome_shown"] = True
            save_settings(self.settings)
            dialog.destroy()
            self._ensure_api_key()

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        # кнопку пакую першою, щоб вона завжди була видна
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
        # якщо ключа нема, відкриваю вікно для його введення
        if not config.load_api_key():
            self._open_api_key_dialog(is_first_setup=True)

    def _build_interface(self):
        self.font_report_time = ctk.CTkFont(size=13, weight="bold")
        self.font_report_text = ctk.CTkFont(size=13)
        self.font_report_time_big = ctk.CTkFont(size=16, weight="bold")
        self.font_report_text_big = ctk.CTkFont(size=16)

        # ---------- верх, тільки кнопка «Меню» ----------
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

        # ---------- вибір відео ----------
        top_frame = ctk.CTkFrame(self)
        top_frame.pack(fill="x", padx=15, pady=(10, 5))

        self.file_button = ctk.CTkButton(
            top_frame, text=self.loc.t("choose_video"), command=self.choose_file
        )
        self.file_button.pack(side="left")

        self.file_label = ctk.CTkLabel(top_frame, text=self.loc.t("no_file_selected"))
        self.file_label.pack(side="left", padx=10)

        # ---------- поле запиту ----------
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
        # обрізаю текст, який довший за ліміт (вставка, друк)
        self.prompt_box._textbox.bind("<<Modified>>", self._on_prompt_modified, add="+")
        self._bind_clipboard_shortcuts(self.prompt_box)

        # ---------- кнопка старту або зупинки ----------
        # видима тільки одна з двох: «Почати» або «Зупинити»
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
        # не пакую, з'явиться під час аналізу

        # ---------- статус ліворуч, режим праворуч ----------
        status_row = ctk.CTkFrame(self, fg_color="transparent")
        status_row.pack(fill="x", padx=15)

        self.status_label = ctk.CTkLabel(status_row, text="")
        self.status_label.pack(side="left")

        # прогрес, спочатку схований
        self.progress_frame = ctk.CTkFrame(status_row, fg_color="transparent")

        self.progress_bar = ctk.CTkProgressBar(
            self.progress_frame, width=90, height=10, progress_color=PROGRESS_BAR_COLOR,
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

        # кнопка «Налаштування» є тільки для режиму «Детально»
        self.detail_settings_button = ctk.CTkButton(
            model_frame,
            text=self.loc.t("settings_button"),
            width=110,
            fg_color=SETTINGS_BUTTON_COLOR,
            hover_color=SETTINGS_BUTTON_HOVER_COLOR,
            text_color="white",
            command=self._open_detail_settings,
        )

        # ---------- низ, кнопка «Очистити все» ----------
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

        # ---------- звіт, список зліва, фото справа ----------
        results_frame = ctk.CTkFrame(self)
        results_frame.pack(fill="both", expand=True, padx=15, pady=(5, 8))

        self.report_list = ctk.CTkScrollableFrame(
            results_frame, width=280, label_text=self.loc.t("report_label")
        )
        self.report_list.pack(side="left", fill="y", padx=(0, 10), pady=5)

        right_panel = ctk.CTkFrame(results_frame, fg_color="transparent")
        right_panel.pack(side="left", fill="both", expand=True, pady=5)

        # підпис: час + опис
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

        # кнопки експорту в пдф
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

        # контейнер для фото, його розмір потрібен, щоб вписати картинку
        self.photo_container = ctk.CTkFrame(right_panel, fg_color="transparent")
        self.photo_container.pack(fill="both", expand=True)
        self.photo_container.bind("<Configure>", self._on_photo_container_resize)

        self._create_photo_label()

    # ф-ція вставки з буфера в поле (ентрі або текстбокс), не забути її
    # повертає False, якщо в буфері нема тексту
    def _paste_clipboard_into(self, widget):
        is_entry_like = hasattr(widget, "select_present")

        # на маку текст іноді лежить як UTF8_STRING, тому пробую обидва типи
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
            return False

        has_selection = widget.select_present() if is_entry_like else bool(widget.tag_ranges("sel"))
        if has_selection:
            widget.delete("sel.first", "sel.last")
        widget.insert("insert", clipboard_text)
        return True

    # ф-ція додає копі, кат, пейст і селект ол для текстових полів
    # тікінтер на маку не завжди має ці комбінації, тому прив'язую самі
    def _bind_clipboard_shortcuts(self, widget):
        is_entry_like = hasattr(widget, "select_present")

        def has_selection():
            if is_entry_like:
                return widget.select_present()
            return bool(widget.tag_ranges("sel"))

        def get_selection():
            if is_entry_like:
                return widget.selection_get()
            return widget.get("sel.first", "sel.last")

        def do_copy(event=None):
            if has_selection():
                widget.clipboard_clear()
                widget.clipboard_append(get_selection())
            return "break"

        def do_cut(event=None):
            if has_selection():
                widget.clipboard_clear()
                widget.clipboard_append(get_selection())
                widget.delete("sel.first", "sel.last")
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

        # у віндовс з українською розкладкою літери не збігаються з латинськими,
        # тому ловлю комбінації за кодом клавіші
        if sys.platform.startswith("win"):
            win_keycode_actions = {65: do_select_all, 67: do_copy, 86: do_paste, 88: do_cut}

            def on_ctrl_keypress(event):
                action = win_keycode_actions.get(event.keycode)
                if action is not None:
                    return action(event)
                return None

            widget.bind("<Control-KeyPress>", on_ctrl_keypress, add="+")

    # ---------- вікна ----------
    # ф-ція масштабу екрана для вікна (на віндовс зі 125% чи 150%)
    def _window_scaling(self, window):
        try:
            return ctk.ScalingTracker.get_window_scaling(window)
        except Exception:  # noqa: BLE001
            return 1.0

    # ф-ція координат (x, y), щоб вікно стояло по центру головного
    def _center_position(self, width_px, height_px):
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
        y = max(0, min(y, screen_h - height_px - 40))  # 40 px запас під панель задач
        return x, y

    def _centered_geometry(self, dialog, width, height):
        scale = self._window_scaling(dialog)
        x, y = self._center_position(int(width * scale), int(height * scale))
        return f"{width}x{height}+{x}+{y}"

    # ф-ція створює модальне вікно по центру головного, не забути її
    # вікно спочатку ховаю, а показую після наповнення
    def _create_modal_dialog(self, title, width, height, *, minsize=None, resizable=False,
                             grab=True, parent=None, on_shown=None):
        previous_grab = self.grab_current()
        dialog = ctk.CTkToplevel(self)
        dialog.withdraw()
        dialog.title(title)
        dialog.geometry(self._centered_geometry(dialog, width, height))
        dialog.resizable(resizable, resizable)
        if minsize:
            dialog.minsize(*minsize)
        if parent is None:
            parent = previous_grab if previous_grab is not None else self
        dialog.transient(parent)

        # на віндовс кастомтікінтер спочатку ховає і показує вікно, чекаю
        show_delay_ms = 300 if sys.platform.startswith("win") else 15

        def try_grab(attempt=0):
            # захоплюю фокус, коли вікно вже видно
            if not dialog.winfo_exists():
                return
            try:
                if dialog.winfo_viewable():
                    dialog.grab_set()
                    dialog.focus_force()
                    return
            except tk.TclError:
                pass
            if attempt < 20:
                dialog.after(100, lambda: try_grab(attempt + 1))
            else:
                logger.warning(f"Вікно «{title}» так і не з'явилось")

        def show():
            dialog.update_idletasks()
            dialog.deiconify()
            dialog.lift()
            dialog.focus_force()

        def reveal():
            if not dialog.winfo_exists():
                return
            try:
                show()
                if grab:
                    try_grab()
                logger.info(f"Вікно «{title}» відкрито")
            except tk.TclError as error:
                logger.warning(f"Не вдалось показати вікно «{title}»: {error}")
            if on_shown is not None:
                try:
                    on_shown()
                except Exception as error:  # noqa: BLE001
                    logger.warning(f"on_shown впав: {error}")
            dialog.after(1000, check_visible)

        def check_visible():
            # якщо вікно раптом сховалось, показую знову
            if not dialog.winfo_exists():
                return
            try:
                if dialog.state() == "withdrawn" or not dialog.winfo_viewable():
                    logger.warning(f"Вікно «{title}» сховалось - показую знову")
                    show()
                    if grab:
                        try_grab()
            except tk.TclError:
                pass

        dialog.after(show_delay_ms, reveal)
        return dialog

    # ф-ція діалогу «Так / Ні», повертає True, якщо натиснули «Так»
    def _ask_yes_no(self, title, message, yes_color=None, yes_hover_color=None, message_color=None):
        previous_grab = self.grab_current()

        dialog = self._create_modal_dialog(title, 400, 200)
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

        ctk.CTkButton(
            button_row, text=self.loc.t("dialog_no"), width=100,
            fg_color="transparent", border_width=1, text_color=("gray10", "gray90"),
            command=on_no,
        ).pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            button_row, text=self.loc.t("dialog_yes"), width=100,
            fg_color=yes_color or DANGER_COLOR, hover_color=yes_hover_color or DANGER_HOVER_COLOR,
            command=on_yes,
        ).pack(side="left")

        dialog.protocol("WM_DELETE_WINDOW", on_no)

        # підганяю висоту під текст
        dialog.update_idletasks()
        height = int(dialog.winfo_reqheight() / self._window_scaling(dialog))
        dialog.geometry(self._centered_geometry(dialog, 400, height))

        self.wait_window(dialog)

        if previous_grab is not None and previous_grab.winfo_exists():
            previous_grab.grab_set()

        return result["confirmed"]

    # ф-ція створює лейбл для фото з нуля, не забути її
    # старий лейбл не чищу через configure(image=None): кастомтікінтер
    # після цього іноді ламається
    def _create_photo_label(self):
        self.photo_label = ctk.CTkLabel(self.photo_container, text=self.loc.t("photo_placeholder"))
        self.photo_label.pack(fill="both", expand=True)
        self.photo_label.bind("<Button-1>", self.toggle_lightbox)

    # ф-ція прибирає фото і підпис, повертає заглушку
    def _reset_photo_area(self):
        self.current_pil_image = None
        self.photo_label.destroy()
        self._create_photo_label()
        self._photo_state = "placeholder"
        self._set_caption("", "")
        self.export_pdf_button.configure(state="disabled")
        self.export_full_report_button.configure(state="disabled")

    # ---------------- тема і мова ----------------

    def _select_theme(self, theme_key):
        if getattr(self, "_theme_switch_busy", False):
            return
        self.settings["theme"] = theme_key
        save_settings(self.settings)

        for btn, key, _ in self.theme_buttons:
            is_selected = key == theme_key
            btn.configure(
                fg_color=BUTTON_COLOR_SELECTED if is_selected else MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_SELECTED if is_selected else TEXT_COLOR_NEUTRAL,
            )

        if not sys.platform.startswith("win"):
            ctk.set_appearance_mode(THEME_MODES[theme_key])
            return

        # на віндовс зміна теми ховає вікна і може зняти фокус з меню
        # тому знімаю грэб на час зміни і повертаю назад
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
        except Exception:  # noqa: BLE001
            logger.exception("Не вдалося змінити тему")
        self.after(400, self._restore_menu_grab)

    def _restore_menu_grab(self):
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

    # ф-ція перевіряє, чи обрано тип ключа, вшитий ключ безкоштовний, тому вибір не потрібен
    def _has_api_tier(self):
        if not config.is_using_custom_key() and config.has_embedded_key():
            return True
        return self.settings.get("api_tier") in VALID_API_TIERS

    # ф-ція типу ключа, з яким працюю, вшитий ключ = безкоштовний
    def _effective_api_tier(self):
        if not config.is_using_custom_key():
            return "free"
        return self.settings.get("api_tier") or "free"

    def _select_api_tier(self, tier_key):
        if tier_key not in VALID_API_TIERS:
            return
        if tier_key == self.settings.get("api_tier"):
            return
        self.settings["api_tier"] = tier_key
        save_settings(self.settings)
        for btn, key in self.api_tier_buttons:
            is_selected = key == tier_key
            btn.configure(
                fg_color=BUTTON_COLOR_SELECTED if is_selected else MODEL_NEUTRAL_COLOR,
                text_color=TEXT_COLOR_SELECTED if is_selected else TEXT_COLOR_NEUTRAL,
            )
        if self._menu_tier_hint is not None:
            self._menu_tier_hint.configure(text="")
        if self._status_key == "status_choose_api_tier":
            self._set_status(None)

    # ф-ція перемальовує всі написи поточною мовою, не забути її
    def _apply_language(self):
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

        self.model_info_btn.configure(text=self.loc.t("menu_info_button"))
        for btn, _, label_key in self.model_buttons:
            btn.configure(text=self.loc.t(label_key))
        self.detail_settings_button.configure(text=self.loc.t("settings_button"))

        self.export_pdf_button.configure(text=self.loc.t("export_pdf_button"))
        self.export_full_report_button.configure(text=self.loc.t("export_full_report_button"))
        self.export_hint_label.configure(text=self.loc.t("export_hint"))

        self.report_list.configure(label_text=self.loc.t("report_label"))

        # відкрите меню
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

        # відкрита історія сесій
        if self._history_window is not None and self._history_window.winfo_exists():
            self._history_window.title(self.loc.t("history_sessions_title"))
            self._history_delete_btn.configure(text=self.loc.t("history_delete_button"))
            if self._history_select_all_chk is not None:
                self._history_select_all_chk.configure(text=self.loc.t("history_select_all"))
            self._history_rebuild_rows()

        # відкрите вікно кадрів сесії
        if self._history_session_window is not None and self._history_session_window.winfo_exists():
            self._open_history_session_window(self._history_current_session)

        if self._about_dialog is not None and self._about_dialog.winfo_exists():
            self._about_dialog.title(self.loc.t("about_title"))
            self._render_about_content()

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

        if self._detail_info_dialog is not None and self._detail_info_dialog.winfo_exists():
            self._detail_info_dialog.title(self.loc.t("detail_info_title"))
            self._detail_info_body_label.configure(text=self.loc.t("detail_info_body"))

        if self._model_info_dialog is not None and self._model_info_dialog.winfo_exists():
            self._model_info_dialog.title(self.loc.t("model_info_title"))
            self._model_info_body_label.configure(text=self.loc.t("model_info_body"))

        # заглушку або «нічого не знайдено» перемальовую, реальний опис від джемині не чіпаю
        if self._photo_state == "placeholder":
            self.photo_label.configure(text=self.loc.t("photo_placeholder"))
        elif self._photo_state == "no_results":
            self.photo_label.configure(text=self.loc.t("nothing_found_photo"))
            self._set_caption("", self.loc.t("nothing_found_description", query=self._last_prompt_text))

        if self._no_results_label is not None:
            self._no_results_label.configure(text=self.loc.t("nothing_found_list"))

        if self._status_key:
            self.status_label.configure(
                text=self.loc.t(self._status_key, **self._status_kwargs), text_color=self._status_color
            )

    # ---------------- меню ----------------

    # ф-ція меню з темою, мовою, типом ключа, історією та іншим, не забути її
    def _open_menu(self, parent=None, on_shown=None):
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

        # тип ключа показую тільки для власного ключа
        tier_block = ctk.CTkFrame(dialog, fg_color="transparent")
        if config.is_using_custom_key() or not config.has_embedded_key():
            tier_block.pack(fill="x")

        self._menu_tier_label = ctk.CTkLabel(tier_block, text=self.loc.t("api_tier_label"), anchor="w")
        self._menu_tier_label.pack(padx=20, pady=(16, 6), fill="x")

        tier_row = ctk.CTkFrame(tier_block, fg_color="transparent")
        tier_row.pack(padx=20, fill="x")
        self.api_tier_buttons = []
        current_tier = self.settings.get("api_tier")
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

        # червона підказка, поки тип не обрано
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

        # повне видалення даних, мак і віндовс не видаляють теку даних разом із програмою
        self._menu_wipe_btn = ctk.CTkButton(
            dialog, text=self.loc.t("wipe_all_button"),
            fg_color="transparent", border_width=2, border_color=DANGER_COLOR,
            text_color=STATUS_COLOR_ERROR, hover_color=("gray85", "gray20"),
            command=self._confirm_wipe_all_data,
        )
        self._menu_wipe_btn.pack(padx=20, pady=(0, 8), fill="x")

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

    # ф-ція викликається після закриття вікна, видаляє дані користувача
    def _wipe_data_now(self):
        logging.shutdown()
        if app_paths.wipe_user_data():
            return
        # під час розробки видаляю тільки ключ і налаштування
        config.clear_api_key()
        try:
            os.remove(SETTINGS_PATH)
        except OSError:
            pass

    # ф-ція тексту кнопки ключа в меню
    def _key_button_text(self):
        if not config.has_embedded_key():
            return self.loc.t("reset_api_key_button")
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
        # зараз стандартний ключ, даю ввести свій
        if self._menu_dialog is not None and self._menu_dialog.winfo_exists():
            self._menu_dialog.destroy()
        self._menu_dialog = None
        self._open_api_key_dialog(is_first_setup=False)

    # ф-ція видаляє власний ключ, далі працює вшитий
    def _confirm_default_key(self):
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

        # скидаю поточну сесію, історія лишається
        self.reset_all()
        self._open_api_key_dialog(is_first_setup=True)

    # ф-ція вікна «Про застосунок», короткі кроки і поради
    def _open_about_dialog(self):
        if self._about_dialog is not None and self._about_dialog.winfo_exists():
            self._about_dialog.lift()
            return

        dialog = self._create_modal_dialog(self.loc.t("about_title"), 480, 600)
        self._about_dialog = dialog

        def on_close():
            dialog.destroy()
            self._about_dialog = None

        dialog.protocol("WM_DELETE_WINDOW", on_close)

        close_btn = ctk.CTkButton(dialog, text=self.loc.t("dialog_understood"), command=on_close)
        close_btn.pack(side="bottom", pady=(0, 20))
        self._about_content = ctk.CTkFrame(dialog, fg_color="transparent")
        self._about_content.pack(fill="both", expand=True, padx=26, pady=(22, 10))
        self._render_about_content()

        dialog.update_idletasks()
        needed = self._about_content.winfo_reqheight() + close_btn.winfo_reqheight() + 22 + 10 + 20 + 10
        height = min(needed, max(self.winfo_screenheight() - 120, 400))
        dialog.geometry(self._centered_geometry(dialog, 480, height))

    # ф-ція малює вміст вікна «Про застосунок» заново (треба при зміні мови)
    def _render_about_content(self):
        frame = self._about_content
        for widget in frame.winfo_children():
            widget.destroy()

        t = self.loc.t
        bold = ctk.CTkFont(weight="bold")

        def divider():
            ctk.CTkFrame(frame, fg_color=("gray80", "gray25"), height=1).pack(fill="x", pady=14)

        ctk.CTkLabel(
            frame, text=t("window_title"), anchor="w",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).pack(fill="x")
        ctk.CTkLabel(
            frame, text=t("about_subtitle"), anchor="w", justify="left",
            wraplength=420, text_color=STATUS_COLOR_NEUTRAL,
        ).pack(fill="x", pady=(4, 0))
        divider()

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

        ctk.CTkLabel(
            frame, text=t("about_tips_title"), anchor="w",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(fill="x", pady=(0, 8))
        for number in range(1, 4):
            ctk.CTkLabel(
                frame, text="\u2022  " + t(f"about_tip_{number}"), anchor="w",
                justify="left", wraplength=420,
            ).pack(fill="x", pady=(0, 4))

        ctk.CTkLabel(
            frame, text=t("about_credit"), anchor="w", justify="left",
            wraplength=420, text_color=STATUS_COLOR_NEUTRAL,
            font=ctk.CTkFont(size=11),
        ).pack(fill="x", side="bottom")

    # ---------------- налаштування деталізації ----------------

    # ф-ція вікна зі слайдером рівня деталізації (для режиму «Детально»)
    def _open_detail_settings(self):
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
            # зберігаю рівень, тільки якщо стоїть галочка «Запам'ятати»
            if self.remember_detail_level:
                self.settings["detail_level_index"] = index
                save_settings(self.settings)

        self._detail_slider = ctk.CTkSlider(
            dialog, from_=0, to=len(DETAIL_LEVELS) - 1,
            number_of_steps=len(DETAIL_LEVELS) - 1,
            command=on_slider_move,
            fg_color=MODEL_NEUTRAL_COLOR,
            progress_color=BUTTON_COLOR_SELECTED,
            button_color=BUTTON_COLOR_SELECTED,
            button_hover_color=BUTTON_COLOR_SELECTED,
        )
        self._detail_slider.set(self.detail_level_index)
        self._detail_slider.pack(padx=34, pady=(0, 4), fill="x")

        # підписи під слайдером, ставлю їх у ті самі точки, де стоять кружки
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
            if self.remember_detail_level:
                self.settings["detail_level_index"] = self.detail_level_index
            save_settings(self.settings)

        self._detail_remember_checkbox = ctk.CTkCheckBox(
            dialog, text=self.loc.t("remember_detail_level"),
            variable=self._detail_remember_var, command=on_remember_toggle,
        )
        self._detail_remember_checkbox.pack(padx=20, pady=(16, 20), anchor="w")

    # ф-ція вікна з поясненням режиму «Детально»
    def _open_detail_info_dialog(self):
        if self._detail_info_dialog is not None and self._detail_info_dialog.winfo_exists():
            self._detail_info_dialog.lift()
            return

        # вікно відкривається поверх «Налаштувань», тому повертаю фокус назад
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

    # ф-ція вікна з поясненням «Швидкий» і «Детально»
    def _open_model_info_dialog(self):
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

    # ---------------- ЕйПіАй-ключ ----------------

    # ф-ція вікна введення ключа джемині, не забути її
    # при першому запуску ключ потрібен, але закрити вікно можна:
    # тоді при старті аналізу воно відкриється знову
    def _open_api_key_dialog(self, is_first_setup=False):
        dialog = self._create_modal_dialog(self.loc.t("setup_title"), 480, 320)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        description_key = "setup_description_first" if is_first_setup else "setup_description_change"
        ctk.CTkLabel(
            dialog, text=self.loc.t(description_key), wraplength=440, justify="left"
        ).pack(padx=20, pady=(20, 4), fill="x")

        # посилання відкриває браузер, бо текст мишкою не скопіюється
        ctk.CTkButton(
            dialog,
            text=self.loc.t("setup_open_link_button"),
            fg_color="transparent",
            hover_color=("gray85", "gray25"),
            text_color=("#1F6AA5", "#4DA3E8"),
            anchor="w",
            command=lambda: webbrowser.open(API_KEY_URL),
        ).pack(padx=16, pady=(0, 10), fill="x")

        entry_row = ctk.CTkFrame(dialog, fg_color="transparent")
        entry_row.pack(padx=20, pady=5)

        entry = ctk.CTkEntry(entry_row, width=330, placeholder_text="AQ.Ab8...")
        existing_key = config.load_custom_key()  # вшитий ключ не показую
        if existing_key:
            entry.insert(0, existing_key)
        entry.pack(side="left")
        entry.focus_set()
        self._bind_clipboard_shortcuts(entry)

        def do_paste_button():
            if not self._paste_clipboard_into(entry):
                status_label.configure(text=self.loc.t("setup_clipboard_empty"), text_color=STATUS_COLOR_ERROR)
            entry.focus_set()

        # кнопка «Вставити» на випадок, якщо Cmd+V не працює
        ctk.CTkButton(
            entry_row, text=self.loc.t("setup_paste_button"), width=80,
            command=do_paste_button,
        ).pack(side="left", padx=(6, 0))

        status_label = ctk.CTkLabel(dialog, text="", text_color=STATUS_COLOR_NEUTRAL, wraplength=440)
        status_label.pack(padx=20, pady=(8, 0), fill="x")

        def do_save():
            key = entry.get().strip()
            if not key:
                status_label.configure(text=self.loc.t("setup_empty"), text_color=STATUS_COLOR_ERROR)
                return

            save_btn.configure(state="disabled")
            status_label.configure(text=self.loc.t("setup_checking"), text_color=STATUS_COLOR_NEUTRAL)

            # перевірка йде у фоні, щоб вікно не зависало
            def worker():
                result = check_api_key(key)

                def finish():
                    if not dialog.winfo_exists():
                        return
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

    # ---------------- історія ----------------

    # ф-ція списку всіх аналізів (сесій), відкривається з меню
    def _open_history_window(self):
        old_menu = self._menu_dialog if (
            self._menu_dialog is not None and self._menu_dialog.winfo_exists()
        ) else None
        self._menu_dialog = None

        if self._history_window is not None and self._history_window.winfo_exists():
            if old_menu is not None:
                old_menu.destroy()
            self._history_window.lift()
            return

        def close_old_menu():
            if old_menu is not None and old_menu.winfo_exists():
                old_menu.destroy()

        win = self._create_modal_dialog(
            self.loc.t("history_sessions_title"), 420, 580, minsize=(360, 400), resizable=True,
            parent=self, on_shown=close_old_menu,
        )
        self._history_window = win

        def on_close():
            # «Назад» повертає в меню, а не закриває програму
            self._history_window = None

            def close_history():
                if win.winfo_exists():
                    win.destroy()

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

        # чекбокс «Всі» з'являється тільки в режимі вибору
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

    # ф-ція заголовка сесії: дата і час завершення, старі записи це «Без категорії»
    def _format_session_title(self, session):
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
        if self._history_select_all_var.get():
            self._history_selected_ids = {s["session_id"] for s in self._history_sessions}
        else:
            self._history_selected_ids = set()
        self._history_rebuild_rows()
        self._history_update_delete_btn_style()

    # ф-ція галочки «Всі», стоїть тільки якщо вибрано всі сесії
    def _history_sync_select_all_var(self):
        if self._history_select_all_var is None:
            return
        all_selected = bool(self._history_sessions) and (
            len(self._history_selected_ids) == len(self._history_sessions)
        )
        self._history_select_all_var.set(all_selected)

    # ф-ція стилю кнопки «Видалити», не забути її:
    # - звичайна (сіра), коли не вибираю
    # - жирна сіра, коли вибираю, але нічого не обрано
    # - червона, коли щось обрано
    def _history_update_delete_btn_style(self):
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
        self._history_selection_mode = enabled
        if enabled:
            self._history_select_all_var.set(False)
            self._history_select_all_frame.pack(side="left", padx=(10, 0))
        else:
            self._history_select_all_frame.pack_forget()
            self._history_selected_ids = set()

    def _history_on_delete_pressed(self):
        # одна кнопка і входить у режим вибору, і підтверджує видалення
        if not self._history_selection_mode:
            self._history_set_selection_mode(True)
            self._history_rebuild_rows()
            self._history_update_delete_btn_style()
            return

        if not self._history_selected_ids:
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

    # ф-ція видаляє обрані сесії разом з їхніми кадрами (файли і записи)
    def _history_do_delete(self):
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

    # ---------------- кадри однієї сесії ----------------

    # ф-ція вікна з кадрами однієї сесії, тут же кнопка експорту всієї сесії в пдф
    def _open_history_session_window(self, session):
        if self._history_session_window is not None and self._history_session_window.winfo_exists():
            self._history_session_window.destroy()

        self._history_current_session = session
        self._history_session_frames = list(session.get("frames", []))

        win = self._create_modal_dialog(
            self.loc.t("history_title"), 420, 620, minsize=(360, 420), resizable=True
        )
        self._history_session_window = win

        def on_close():
            win.destroy()
            self._history_session_window = None
            self._history_current_session = None
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

    # ф-ція видаляє обрані кадри, якщо кадрів не лишилось, сесію теж прибираю
    def _history_frame_do_delete(self):
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
            if self._history_session_window is not None and self._history_session_window.winfo_exists():
                self._history_session_window.destroy()
            self._history_session_window = None
            self._history_current_session = None
            self._history_rebuild_rows()
            return

        self._history_frame_rebuild_rows()
        self._history_frame_update_delete_btn_style()
        self._history_rebuild_rows()

    # ф-ція експорту всіх кадрів сесії в один пдф
    def _history_session_export_pdf(self):
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

    # ф-ція окремого вікна з одним кадром з історії: фото, час, опис, кнопки
    def _show_history_preview(self, frame, session=None):
        screenshot_path = os.path.join(SCREENSHOTS_FOLDER, frame.get("filename", ""))
        if not os.path.exists(screenshot_path):
            return

        win = self._create_modal_dialog(
            self.loc.t("history_preview_title"), 560, 620, minsize=(360, 420), resizable=True
        )

        timestamp_sec = frame.get("timestamp_sec")
        time_str = format_time(timestamp_sec) if timestamp_sec is not None else ""
        video_name = session.get("video_name", "") if session else ""
        video_path = session.get("video_path", "") if session else ""
        header_parts = [part for part in (time_str, video_name) if part]

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
                return
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

    # ---------------- обробники подій ----------------

    def _on_prompt_enter(self, event):
        self.start_analysis()
        return "break"  # ентер запускає аналіз, а не переносить рядок

    # ф-ція перевіряє, чи фокус зараз у полі запиту (тоді стрілки рухають курсор)
    def _is_typing_in_prompt(self):
        focused = self.focus_get()
        if focused is None:
            return False
        return str(focused).startswith(str(self.prompt_box))

    # ф-ція гортання звіту, direction: -1 попередній момент, +1 наступний
    def _navigate_report(self, direction):
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

    # ф-ція спільної точки для вибору відео: діалогом і перетягуванням, не забути її
    def _accept_video_path(self, path):
        # тривалість читаю з заголовка файлу, це швидко
        try:
            duration_sec = get_video_duration_sec(path)
        except ValueError:
            duration_sec = 0

        if duration_sec and duration_sec > MAX_VIDEO_DURATION_SEC:
            self._set_status(
                "status_video_too_long",
                color=STATUS_COLOR_ERROR,
                duration=format_time(duration_sec),
                max=format_time(MAX_VIDEO_DURATION_SEC),
            )
            return

        if not duration_sec:
            # тривалість невідома, питаю користувача
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

    # ---------------- перетягування файлу ----------------

    # ф-ція дозволяє перетягнути відео у вікно, без бібліотеки просто вимикаю
    def _setup_drag_and_drop(self):
        self._dnd_enabled = False
        self._dnd_status_reset_job = None
        self._status_before_flash = (None, {}, STATUS_COLOR_NEUTRAL)
        self._dnd_probe_busy = False

        if not _DND_IMPORT_OK:
            print("[diag] drag-and-drop вимкнено: бібліотека tkinterdnd2 не встановлена")
            return

        try:
            self.TkdndVersion = TkinterDnD._require(self)
        except Exception as error:  # noqa: BLE001
            print(f"[diag] drag-and-drop вимкнено: {error}")
            return

        # темний напис «відпустіть файл», показую коли файл над вікном
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
            self._flash_status("status_dnd_analysis_running", color=STATUS_COLOR_ERROR)
            return event.action

        paths = self._parse_dnd_paths(event.data)
        if not paths:
            return event.action
        path = paths[0]  # беру перший файл

        if self._dnd_probe_busy:
            return event.action

        # перевірка файлу може йти довго, тому в окремому потоці
        self._dnd_probe_busy = True

        def worker():
            try:
                status = dnd_tools.classify_dropped_file(path)
            except Exception:  # noqa: BLE001
                status = dnd_tools.STATUS_UNKNOWN
            self.after(0, lambda: self._on_dnd_classified(path, status))

        threading.Thread(target=worker, daemon=True).start()
        return event.action

    # ф-ція продовження перетягування, вже в головному потоці
    def _on_dnd_classified(self, path, status):
        self._dnd_probe_busy = False

        if self._analysis_running:
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

    # ф-ція розбирає рядок зі шляхами, який віддає tkinterdnd2
    def _parse_dnd_paths(self, data):
        if not data:
            return []
        try:
            return list(self.tk.splitlist(data))
        except Exception:
            return [data]

    # ф-ція коротке повідомлення в статусі, яке саме зникає і повертає попередній текст
    def _flash_status(self, key, color=None, duration_ms=2500, **kwargs):
        if self._dnd_status_reset_job is not None:
            self.after_cancel(self._dnd_status_reset_job)
        else:
            self._status_before_flash = (self._status_key, dict(self._status_kwargs), self._status_color)
        self._set_status(key, color=color, **kwargs)

        def restore():
            self._dnd_status_reset_job = None
            if self._status_key != key:
                return  # статус уже змінили, не чіпаю
            prev_key, prev_kwargs, prev_color = self._status_before_flash
            self._set_status(prev_key, color=prev_color, **prev_kwargs)

        self._dnd_status_reset_job = self.after(duration_ms, restore)

    # ф-ція обрізає текст, який довший за MAX_PROMPT_CHARS
    def _on_prompt_modified(self, event=None):
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

    # ф-ція підпису під фото: час жирним зліва, опис праворуч
    def _set_caption(self, time_str, text):
        if time_str:
            self.caption_time_label.configure(text=time_str)
            self.caption_time_label.grid()
        else:
            self.caption_time_label.configure(text="")
            self.caption_time_label.grid_remove()
        self.description_label.configure(text=text)

    # ф-ція повертає програму до початкового стану, не забути її
    def reset_all(self):
        self.close_lightbox()

        if self._analysis_running:
            # якщо аналіз іще йде, зупиняю його
            self._cancel_event.set()

        self.video_path = None
        self.file_label.configure(text=self.loc.t("no_file_selected"))

        self.prompt_box.delete("1.0", "end")
        self.update_char_count()

        self._set_status(None)
        self._set_progress(None)
        self._set_analysis_running(False)

        self._clear_report_list()
        self._reset_photo_area()

        # режим треба вибрати заново
        self.selected_mode = None
        for btn, name, _ in self.model_buttons:
            btn.configure(fg_color=MODEL_NEUTRAL_COLOR, text_color=TEXT_COLOR_NEUTRAL)
        self.detail_settings_button.pack_forget()

    # ф-ція прибирає список знайдених моментів
    def _clear_report_list(self):
        for widget in self.report_list.winfo_children():
            widget.destroy()
        self.report_buttons = []
        self.current_result = None
        self._no_results_label = None
        self._last_prompt_text = ""

    # ф-ція прибирає старий звіт перед новим аналізом, запит, відео і режим лишаються
    def _clear_report_view(self):
        self._clear_report_list()
        self._reset_photo_area()

    # ф-ція показує «Почати» або «Зупинити»
    def _set_analysis_running(self, running):
        self._analysis_running = running
        if running:
            self.submit_button.pack_forget()
            self.stop_button.configure(state="normal")
            self.stop_button.pack()
        else:
            self.stop_button.pack_forget()
            self.submit_button.configure(state="normal")
            self.submit_button.pack()

    # ф-ція для кнопки «Зупинити», фоновий потік сам побачить прапорець і вийде
    def stop_analysis(self):
        if not self._analysis_running:
            return
        self._cancel_event.set()
        self.stop_button.configure(state="disabled")
        self._set_status("status_cancelling")

    def _on_analysis_cancelled(self):
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

        if not self._has_api_tier():
            self._set_status("status_choose_api_tier", color=STATUS_COLOR_ERROR)
            return

        if not config.load_api_key():
            self._open_api_key_dialog(is_first_setup=True)
            return

        prompt_text = prompt_text[:MAX_PROMPT_CHARS]

        self._cancel_event.clear()
        self._set_analysis_running(True)
        self._clear_report_view()
        self._set_status("status_cutting_frames")
        self._set_progress(0, allow_decrease=True)

        # параметри беру тут, а не у фоновому потоці: користувач міг би
        # посмикати слайдер уже під час аналізу
        if self.selected_mode == MODE_FAST:
            max_frames = FAST_MODE_FRAMES
            thinking_level = FAST_MODE_THINKING_LEVEL
            media_resolution = FAST_MODE_MEDIA_RESOLUTION
        else:
            _, thinking_level, max_frames, media_resolution = DETAIL_LEVELS[self.detail_level_index]

        # аналіз у фоновому потоці, інакше вікно зависне
        thread = threading.Thread(
            target=self._run_pipeline_guarded,
            args=(prompt_text, ANALYSIS_MODEL_NAME, self.loc.language, max_frames, thinking_level, media_resolution),
            daemon=True,
        )
        thread.start()

    # ф-ція вмикає «не засинати» на час аналізу і запускає пайплайн
    def _run_pipeline_guarded(self, *pipeline_args):
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

    # ф-ція головної роботи: нарізка відео, запити до джемині, скріншоти, збереження, йде у фоні, не забути її
    def run_pipeline(self, prompt_text, model_name, language, max_frames, thinking_level, media_resolution):
        pipeline_start_ts = time.time()
        print(f"[log] Старт аналізу: {self.video_path}")
        self._failed_batches = 0
        full_res_dir = None  # тимчасова тека для повних кадрів, видаляю в кінці
        try:
            # нарізка і запити до джемині йдуть одночасно:
            # нарізка кладе кадри в чергу, а джемині забирає їх пачками
            full_res_dir = os.path.join(SCREENSHOTS_FOLDER, f".cache_{uuid.uuid4().hex}")
            os.makedirs(full_res_dir, exist_ok=True)

            frames = []
            frames_lock = threading.Lock()
            frame_queue = queue.Queue()
            extraction_error_box = {}
            # нарізку зупиняю або по кнопці, або якщо джемині впав
            gemini_stop_event = threading.Event()

            def combined_is_cancelled():
                return self._cancel_event.is_set() or gemini_stop_event.is_set()

            # ---- прогрес: нарізка 0-40%, аналіз 40-90%, скріншоти 90-97% ----
            # рахую в "одиницях роботи": кожен кадр = нарізати + проаналізувати
            cut_progress_box = {"fraction": 0.0}
            analysis_progress_box = {
                "done_frames": 0,
                "done_batches": 0,
                "total_batches": 1,
                # найбільший відсоток, який уже показав, бар не повертається назад
                "max_combined_percent": 0.0,
                # скільки кадрів уже нарізано (для паралельної нарізки)
                "parallel_frame_count_hint": 0,
            }
            # точна кількість кадрів, коли нарізка завершилась
            final_extracted_total_box = {"value": None}

            progress_lock = threading.Lock()

            def _push_combined_progress():
                with frames_lock:
                    extracted_count = len(frames)
                extracted_count = max(extracted_count, analysis_progress_box["parallel_frame_count_hint"])
                cut_fraction = cut_progress_box["fraction"]
                done_frames = analysis_progress_box["done_frames"]

                # скільки кадрів буде всього
                if final_extracted_total_box["value"] is not None:
                    expected_total = max(1, final_extracted_total_box["value"])
                else:
                    expected_total = max_frames
                    if cut_fraction >= 0.05 and extracted_count > 0:
                        expected_total = min(max_frames, max(extracted_count, round(extracted_count / cut_fraction)))
                    expected_total = max(1, expected_total)

                units_done = min(extracted_count, expected_total) + min(done_frames, expected_total)
                combined_percent = units_done / (2 * expected_total) * 90

                with progress_lock:
                    combined_percent = max(combined_percent, analysis_progress_box["max_combined_percent"])
                    analysis_progress_box["max_combined_percent"] = combined_percent
                self._set_progress_async(combined_percent)

                if cut_fraction < 1.0:
                    self._set_status_async(
                        "status_cutting_and_analyzing", cut_count=extracted_count, done=done_frames,
                    )
                else:
                    self._set_status_async(
                        "status_analyzing_batches", done=done_frames, total=extracted_count,
                    )

            def on_cut_progress(fraction):
                cut_progress_box["fraction"] = fraction
                _push_combined_progress()

            def on_parallel_frame_count(count):
                analysis_progress_box["parallel_frame_count_hint"] = count
                _push_combined_progress()

            def on_frame_ready(frame, _index):
                with frames_lock:
                    frames.append(frame)
                frame_queue.put(frame)
                _push_combined_progress()

            def extraction_worker():
                try:
                    print("[log] Нарізаю відео на кадри (паралельно з Gemini)...")
                    result_frames = extract_frames(
                        self.video_path, max_frames=max_frames, progress_callback=on_cut_progress,
                        is_cancelled=combined_is_cancelled, full_res_dir=full_res_dir,
                        on_frame_ready=on_frame_ready, frame_count_callback=on_parallel_frame_count,
                    )
                    final_extracted_total_box["value"] = len(result_frames)
                    _push_combined_progress()
                    # якщо якісь кадри не пройшли через on_frame_ready, докидаю їх
                    with frames_lock:
                        streamed_count = len(frames)
                        if streamed_count < len(result_frames):
                            for extra_frame in result_frames[streamed_count:]:
                                frames.append(extra_frame)
                                frame_queue.put(extra_frame)
                except Exception as error:  # noqa: BLE001
                    extraction_error_box["error"] = error
                    frame_queue.put(error)
                finally:
                    frame_queue.put(STREAM_END)

            cut_start_ts = time.time()
            extraction_thread = threading.Thread(target=extraction_worker, daemon=True)
            extraction_thread.start()

            def on_batch_done(done_batches, total_batches, done_frames, total_frames):
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
                    is_cancelled=self._cancel_event.is_set,
                    thinking_level=thinking_level,
                    max_frames_hint=max_frames,
                    media_resolution=media_resolution,
                    api_tier=self._effective_api_tier(),
                )
            except GeminiApiError:
                # фатальна помилка: зупиняю нарізку, далі вона не потрібна
                gemini_stop_event.set()
                raise
            finally:
                extraction_thread.join()

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

            # для кожного знайденого кадру готую ім'я файлу
            # юайді, щоб файли з різних аналізів не перезаписували один одного
            pending = []
            for match in matches:
                if not (0 <= match.frame_number < len(frames)):
                    continue
                timestamp_sec = frames[match.frame_number]["timestamp_sec"]
                filename = history_store.new_screenshot_filename()
                screenshot_path = os.path.join(SCREENSHOTS_FOLDER, filename)
                pending.append((match, timestamp_sec, screenshot_path, filename))

            def on_screenshot_progress(fraction):
                self._set_progress_async(90 + fraction * 7)

            # спочатку беру повні кадри з кешу (вони вже є після нарізки)
            # відео відкриваю тільки для тих, яких у кеші нема
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

            if to_grab:
                try:
                    saved_paths |= grab_screenshots_batch(
                        self.video_path, to_grab, progress_callback=on_screenshot_progress,
                    )
                except ScreenshotDiskFullError as error:
                    # диск скінчився, зберігаю те, що вже встиг записати,
                    # а не викидаю весь звіт
                    saved_paths |= error.saved
                    print(f"[log] Місце на диску скінчилось ({error.done_targets}/{error.total_targets}) - "
                          f"продовжую з тим, що збережено ({len(saved_paths)}/{len(pending)})")
            screenshots_elapsed_sec = time.time() - screenshots_start_ts
            print(f"[timing] збереження скріншотів: {screenshots_elapsed_sec:.1f}с, "
                  f"збережено: {len(saved_paths)}/{len(pending)}")

            if self._cancel_event.is_set():
                print("[log] Аналіз скасовано користувачем (під час збереження скріншотів)")
                self.after(0, self._on_analysis_cancelled)
                return

            results = []
            finished_at = datetime.now().isoformat()
            video_name = os.path.basename(self.video_path) if self.video_path else ""
            video_path = self.video_path or ""
            session_frames = []

            for match, timestamp_sec, screenshot_path, filename in pending:
                if screenshot_path not in saved_paths:
                    continue
                if not os.path.exists(screenshot_path):
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

            # кадри в історії за часом відео
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

            results.sort(key=lambda r: r["timestamp_sec"])

            wall_elapsed_sec = time.time() - pipeline_start_ts
            idle_sec = self._suspend_watchdog.suspended_sec if self._suspend_watchdog else 0.0
            # час, поки комп'ютер спав, не рахую
            total_elapsed_sec = max(0.0, wall_elapsed_sec - idle_sec)
            if idle_sec >= 1.0:
                print(f"[timing] весь аналіз загалом: {total_elapsed_sec:.1f}с "
                      f"(без простою системи {idle_sec:.0f}с; за годинником минуло {wall_elapsed_sec:.1f}с)")
            else:
                print(f"[timing] весь аналіз загалом: {total_elapsed_sec:.1f}с")
            print(f"[log] Готово. Знайдено моментів: {len(results)}")

            # сповіщення, якщо користувач міг згорнути вікно
            self.after(0, lambda n=len(results): self._notify_analysis_done(n))

            self.after(0, lambda: self.show_results(results, prompt_text, total_elapsed_sec, idle_sec))

        except MissingApiKeyError:
            self.after(0, lambda: self._set_analysis_running(False))
            try:
                print("[log] Помилка: не знайдено API-ключ Gemini")
                self._set_status_async("status_error_missing_key")
                self._set_progress_async(None)
            except Exception:  # noqa: BLE001
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
                    self._set_status_async("status_error", error=error)
                self._set_progress_async(None)
            except Exception:  # noqa: BLE001
                pass

        except Exception as error:
            # спершу розблоковую інтерфейс, щоб він не завис, навіть якщо
            # далі логування теж впаде (наприклад, диск повний)
            self.after(0, lambda: self._set_analysis_running(False))
            try:
                if isinstance(error, OSError) and error.errno == 28:
                    print(f"[log] Місце на диску скінчилось: {error}")
                else:
                    print(f"[log] Непередбачена помилка: {error}")
                self._set_status_async("status_error", error=error)
                self._set_progress_async(None)
            except Exception:  # noqa: BLE001
                pass

        finally:
            # тимчасові повні кадри видаляю
            if full_res_dir:
                shutil.rmtree(full_res_dir, ignore_errors=True)

    # ф-ція системного сповіщення, якщо вікно не активне
    def _notify_analysis_done(self, count):
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

    # ф-ція ставить текст статусу за ключем перекладу (або прибирає його, якщо key=None)
    # ключ запам'ятовую, щоб перемалювати статус при зміні мови
    def _set_status(self, key, color=None, **kwargs):
        self._status_key = key
        self._status_kwargs = kwargs
        self._status_color = color or STATUS_COLOR_NEUTRAL
        text = self.loc.t(key, **kwargs) if key else ""
        self.status_label.configure(text=text, text_color=self._status_color)

    def _set_status_async(self, key, color=None, **kwargs):
        self.after(0, lambda: self._set_status(key, color=color, **kwargs))

    # ф-ція показує прогрес (0-100), значення плавно доганяється, не забути її
    # value=None, ховаю капсулу одразу
    # нижче цілі не опускаю, крім випадку allow_decrease=True
    # (тільки при старті нового аналізу)
    def _set_progress(self, value, allow_decrease=False):
        if value is None:
            if self._progress_anim_job is not None:
                self.after_cancel(self._progress_anim_job)
                self._progress_anim_job = None
            if self._progress_hide_job is not None:
                self.after_cancel(self._progress_hide_job)
                self._progress_hide_job = None
            self._progress_target = 0
            self._progress_displayed = 0.0
            self.progress_bar.set(0)
            self.progress_percent_label.configure(text="")
            self.progress_frame.pack_forget()
            return

        # нове значення скасовує старий таймер «сховати після 100%»
        if self._progress_hide_job is not None:
            self.after_cancel(self._progress_hide_job)
            self._progress_hide_job = None

        new_target = min(max(value, 0), 100)
        if not allow_decrease and new_target < self._progress_target:
            return
        self._progress_target = new_target

        if not self.progress_frame.winfo_ismapped():
            self.progress_frame.pack(side="left", padx=(10, 0))

        if self._progress_anim_job is None:
            self._animate_progress_step()

    # ф-ція одного кроку анімації прогресу, зупиняється, коли вже біля цілі
    def _animate_progress_step(self):
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
        self.after(0, lambda: self._set_progress(value))

    def show_results(self, results, prompt_text="", elapsed_sec=None, idle_sec=0.0):
        self._set_analysis_running(False)
        elapsed_str = format_time(elapsed_sec) if elapsed_sec is not None else format_time(0)
        if idle_sec >= 60:
            self._set_status(
                "status_done_idle", count=len(results), elapsed=elapsed_str, idle=format_time(idle_sec)
            )
        else:
            self._set_status("status_done", count=len(results), elapsed=elapsed_str)
        failed = getattr(self, "_failed_batches", 0)
        if failed:
            self._set_status("status_done_partial", color=WARNING_COLOR,
                             count=len(results), elapsed=elapsed_str, failed=failed)
        self._last_prompt_text = prompt_text

        # 100% показую кілька секунд, потім ховаю
        # таймер зберігаю, щоб новий старт міг його скасувати
        self._set_progress(100)
        self._progress_hide_job = self.after(PROGRESS_HOLD_AT_100_MS, lambda: self._set_progress(None))

        self._clear_report_list()

        if not results:
            self._no_results_label = ctk.CTkLabel(
                self.report_list, text=self.loc.t("nothing_found_list"), text_color=STATUS_COLOR_NEUTRAL
            )
            self._no_results_label.pack(pady=10)

            self._photo_state = "no_results"
            self._set_caption("", self.loc.t("nothing_found_description", query=prompt_text))

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

    # ф-ція рядка звіту: час зліва, опис справа
    def _make_report_row(self, result):
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
            except Exception:  # noqa: BLE001
                pass
        return row

    def show_photo(self, result):
        self.current_result = result
        self._photo_state = "image"

        # підсвічую обраний рядок звіту
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

        # відкриваю картинку один раз і зберігаю в пам'яті
        try:
            with Image.open(result["screenshot_path"]) as opened:
                opened.load()
                self.current_pil_image = opened.copy()
        except Exception as error:  # noqa: BLE001
            print(f"[log] Не вдалось відкрити скріншот {result['screenshot_path']}: {error}")
            self.current_pil_image = None
            self.photo_label.destroy()
            self._create_photo_label()
            self.photo_label.configure(text=self.loc.t("photo_open_error", error=error))
            return
        self.photo_label.configure(cursor="hand2")
        self._render_photo()

        # якщо відкрито перегляд на весь екран, оновлюю і його
        if self.lightbox_frame:
            self.zoom_index = 0
            self.pan_x = 0
            self.pan_y = 0
            if self.lightbox_caption_label is not None:
                self.lightbox_time_label.configure(text=result["time_str"])
                self.lightbox_caption_label.configure(text=result["description"])
                fit = getattr(self, "_lightbox_fit_caption", None)
                if fit is not None:
                    fit()
            self._render_lightbox_image()

    @staticmethod
    def _result_caption(result):
        return f"{result['time_str']} — {result['description']}"

    # ---------------- експорт у пдф ----------------

    # ф-ція зберігає поточний момент в пдф
    def export_current_pdf(self):
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
            return

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

    # ф-ція зберігає весь звіт в один пдф, по сторінці на момент
    def export_full_report_pdf(self):
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

    # ---------------- показ фото ----------------

    def _on_photo_container_resize(self, event=None):
        # перерахунок розміру з затримкою, щоб не гальмувало під час розтягування вікна
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
        self.photo_label.image = ctk_image  # без цього картинка зникне

    # ф-ція розміру картинки, щоб вона цілком влізла і не спотворилась
    @staticmethod
    def _fit_image(pil_image, available_w, available_h, min_size=50):
        if available_w < min_size or available_h < min_size:
            return None

        img_w, img_h = pil_image.size
        if img_w <= 0 or img_h <= 0:
            return None

        scale = min(available_w / img_w, available_h / img_h)
        new_w = max(1, int(img_w * scale))
        new_h = max(1, int(img_h * scale))
        return new_w, new_h

    # ф-ція прокручує список звіту, щоб вибраний рядок було видно
    def _scroll_report_to_button(self, btn):
        canvas = getattr(self.report_list, "_parent_canvas", None)
        if canvas is None:
            return

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
            canvas.yview_moveto(btn_top / total_height)
        elif btn_bottom > visible_bottom:
            new_top = max(0, btn_bottom - canvas_height)
            canvas.yview_moveto(new_top / total_height)

    # ---------------- перегляд на весь екран ----------------

    def toggle_lightbox(self, event=None):
        if self.current_pil_image is None:
            return
        if self.lightbox_frame:
            self.close_lightbox()
        else:
            self.open_lightbox()

    def open_lightbox(self):
        self.zoom_index = 0
        self.pan_x = 0
        self.pan_y = 0
        self._drag_start = None
        self.lightbox_image_item = None

        # темна підкладка на все вікно
        self.lightbox_frame = ctk.CTkFrame(self, fg_color=LIGHTBOX_BG_COLOR, corner_radius=0)
        self.lightbox_frame.place(x=0, y=0, relwidth=1, relheight=1)
        self.lightbox_frame.bind("<Button-1>", lambda e: self.close_lightbox())

        # верхня панель: «Закрити» зліва, масштаб справа, підпис по центру
        controls = ctk.CTkFrame(self.lightbox_frame, fg_color="transparent", height=48)
        controls.pack(fill="x", padx=20, pady=15)
        controls.pack_propagate(False)

        ctk.CTkButton(
            controls, text=self.loc.t("lightbox_close"), width=100, command=self.close_lightbox
        ).pack(side="left")

        ctk.CTkButton(
            controls, text="+", width=40, command=self.zoom_in
        ).pack(side="right", padx=(5, 0))

        self.zoom_label = ctk.CTkLabel(
            controls, text="100%", width=60,
            text_color="white", font=self.font_report_text_big,
        )
        self.zoom_label.pack(side="right", padx=5)

        ctk.CTkButton(
            controls, text="-", width=40, command=self.zoom_out
        ).pack(side="right", padx=(0, 5))

        time_text = self.current_result["time_str"] if self.current_result else ""
        desc_text = self.current_result["description"] if self.current_result else ""
        caption_frame = ctk.CTkFrame(controls, fg_color="transparent")
        caption_frame.place(relx=0.5, rely=0.5, anchor="center")
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

        # ф-ція підганяє ширину і висоту підпису під панель
        def fit_caption(event=None):
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
        self._drag_start = (event.x, event.y)

    def _on_lightbox_drag(self, event):
        if self._drag_start is None or self.lightbox_image_item is None:
            return
        dx = event.x - self._drag_start[0]
        dy = event.y - self._drag_start[1]
        self.pan_x += dx
        self.pan_y += dy
        self._drag_start = (event.x, event.y)
        # рухаю картинку без повного перемалювання
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
    # потрібно для зібраного ехе на віндовс, інакше дочірні процеси відкривають вікна
    import multiprocessing
    multiprocessing.freeze_support()

    app = App()
    try:
        app.mainloop()
    except KeyboardInterrupt:
        # Ctrl+C або «Стоп» у пайчарм минає WM_DELETE_WINDOW, тому закриваю тут
        logger.info("Отримано KeyboardInterrupt (Ctrl+C / Stop) - завершую")
        app.quit()
    logger.info("Головний цикл завершено, закриваю вікно")
    app.shutdown()
    print("[log] Вікно закрито, програма завершується нормально")

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
