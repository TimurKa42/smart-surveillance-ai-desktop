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

# localization.py, тут переклади інтерфейсу. є два словники: українська
# (за замовчуванням) і англійська. російської немає, не забути
#
# як користуватись:
#     loc = Localization("ua")
#     label.configure(text=loc.t("start_analysis"))
#
# якщо в тексті є {count}, передаю значення іменованим аргументом:
#     loc.t("status_done", count=5)

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

LANG_UA = "ua"
LANG_EN = "en"

DEFAULT_LANGUAGE = LANG_UA

TEXTS = {
    LANG_UA: {
        "window_title": "LookBack",

        "choose_video": "Обрати відео",
        "no_file_selected": "Файл не обрано",
        "clear_all": "Очистити все",

        "search_prompt_label": "Кого/що шукати на відео:",
        "char_count": "Символів: {count}/{max}",

        "start_analysis": "Почати аналіз",
        "stop_analysis": "Зупинити аналіз",
        "status_cancelling": "Зупиняю аналіз...",
        "status_cancelled": "Аналіз зупинено користувачем",

        "choose_model_label": "Тип аналізу:",
        "model_fast_label": "Швидкий",
        "model_detail_label": "Детально",
        "settings_button": "Налаштування",
        "settings_title": "Налаштування",
        "settings_info_button": "ⓘ",

        "detail_level_weak": "Слабкий",
        "detail_level_medium": "Середній",
        "detail_level_high": "Високий",
        "detail_level_max": "Максимальний",
        "detail_level_frames_hint": "До {count} кадрів",

        "remember_detail_level": "Запам'ятати налаштування",

        "detail_info_title": "Про режим «Детально»",
        "detail_info_body": (
            "Режим «Детально» аналізує відео частіше - від 400 до 2000 "
            "кадрів залежно від обраного рівня, і чим вищий рівень, тим "
            "уважніше модель розглядає кожну пачку кадрів. Це корисно "
            "для складних сцен (погана освітленість, рух на воді, дрібні "
            "об'єкти) та довгих відео, але аналіз займає більше часу. "
            "За замовчуванням - рівень «Середній» (800 кадрів)."
        ),

        "model_info_title": "Про типи аналізу",
        "model_info_body": (
            "Швидкий - найшвидший і найекономніший режим, до 200 "
            "кадрів. Зручний для відео до 20-30 хвилин, де шуканий "
            "об'єкт чи людину добре видно.\n\n"
            "Детально - дозволяє самому обрати кількість кадрів (від "
            "400 до 2000, кнопка «Налаштування») і змушує модель "
            "уважніше аналізувати кожну пачку. Краще підходить для "
            "довших відео та складних сцен: погана видимість, темрява, "
            "дрібні деталі, об'єкти на великій відстані. Сам аналіз "
            "при цьому займає більше часу.\n\n"
            "Якщо не впевнений - почни зі «Швидкого»: якщо результат "
            "не влаштує або відео довге, повтори пошук у «Детальному»."
        ),

        "status_video_too_long": "Занадто довге відео ({duration}). Максимум - {max}",

        "duration_unknown_title": "Не вдалось перевірити тривалість",
        "duration_unknown_message": (
            "Не вдалось визначити тривалість відео. Продовжити аналіз "
            "на власний ризик (по витраті токенів)?"
        ),

        "report_label": "Звіт",
        "photo_placeholder": "Тут з'явиться скріншот",
        "nothing_found_photo": "Нічого не знайдено 🙁",

        "status_choose_video_first": "Спочатку обери відео",
        "status_choose_model": "Оберіть тип аналізу праворуч",
        "status_choose_api_tier": "Не обрано тип API-ключа. Будь ласка, оберіть його в меню.",
        "api_tier_required_hint": "Обов'язково оберіть тип ключа",
        "status_write_query": "Напиши, кого або що шукати",
        "status_cutting_frames": "Нарізаю відео на кадри...",
        "status_frames_cut": "Кадрів нарізано: {count}. Питаю Gemini...",
        "status_moments_found": "Моментів знайдено: {count}. Зберігаю кадри...",
        "status_done": "Готово! Знайдено моментів: {count}. Витрачено часу на аналіз: {elapsed}",
        "status_done_idle": "Готово! Знайдено моментів: {count}. Витрачено часу на аналіз: {elapsed} (без простою комп'ютера: {idle})",
        "status_done_partial": "Готово, але НЕПОВНИЙ результат: {failed} пач. кадрів не вдалось проаналізувати (збій зв'язку з Gemini). Знайдено моментів: {count}. Час: {elapsed}. Спробуйте запустити ще раз.",
        "status_error": "Помилка: {error}",

        "nothing_found_list": "Нічого не знайдено",
        "nothing_found_description": "За запитом «{query}» на відео нічого не знайдено.",

        "lightbox_close": "✕ Закрити",

        "file_dialog_title": "Обери відео",
        "file_dialog_video_files": "Відеофайли",
        "file_dialog_all_files": "Усі файли",

        "dnd_drop_hint": "Відпустіть, щоб завантажити відео",
        "status_dnd_not_video": "Це не відео, завантажити не вдалося",
        "status_dnd_analysis_running": "Дочекайтесь завершення поточного аналізу",
        "dnd_unknown_confirm_title": "Не вдалося перевірити файл",
        "dnd_unknown_confirm_message": "Не вдалося перевірити файл. Продовжити на свій ризик?",

        "notification_analysis_complete_title": "LookBack",
        "notification_analysis_complete_message": (
            "Аналіз завершено — знайдено моментів: {count}"
        ),

        "theme_label": "Тема:",
        "theme_auto": "Авто",
        "theme_light": "Світла",
        "theme_dark": "Темна",

        "language_label": "Мова:",

        "api_tier_label": "Тип API-ключа:",
        "api_tier_free": "Безкоштовний",
        "api_tier_paid": "Платний",

        "dialog_yes": "Так",
        "dialog_understood": "Зрозуміло",
        "back_button": "← Назад",
        "menu_about_button": "Про застосунок",
        "menu_exit_button": "Вихід",
        "exit_title": "Вихід",
        "exit_confirm": "Закрити програму?",
        "exit_confirm_running": "Аналіз ще триває. Закрити програму? Незавершений аналіз буде скасовано.",
        "clear_all_title": "Очистити все?",
        "clear_all_confirm": "Буде скинуто обране відео, запит і поточний звіт. Історія кадрів не видаляється.",
        "dialog_no": "Ні",

        "change_api_key_button": "Змінити ключ",
        "menu_button": "Меню",
        "menu_title": "Меню",
        "view_history_button": "Переглянути історію",
        "reset_api_key_button": "Скинути API ключ",
        "reset_api_key_title": "Скинути API ключ?",
        "reset_api_key_confirm": (
            "Точно скинути API ключ? Поточний аналіз буде очищено, і "
            "доведеться ввести ключ заново. Історія кадрів збережеться."
        ),

        "wipe_all_button": "Видалити всі дані",
        "wipe_all_title": "Видалити всі дані?",
        "wipe_all_confirm": (
            "Буде остаточно видалено API ключ, налаштування, історію кадрів "
            "і всі збережені скріншоти з цього комп'ютера, після чого "
            "програму буде закрито. Це не можна скасувати."
        ),

        "history_sessions_title": "Історія аналізів",
        "history_sessions_empty": "Історія аналізів поки порожня",
        "history_session_row_title": "Аналіз {date}",
        "history_uncategorized": "Без категорії",
        "history_frames_count": "{count} кадр(ів)",
        "history_session_delete_confirm_title": "Видалити аналіз?",
        "history_session_delete_confirm_message": (
            "Видалити {count} аналіз(ів) разом з усіма кадрами? Цю дію не можна скасувати."
        ),

        "history_title": "Кадри",
        "history_delete_button": "Видалити",
        "history_select_all": "Всі",
        "history_empty": "Історія поки порожня",
        "history_delete_confirm_title": "Видалити кадри?",
        "history_delete_confirm_message": "Видалити {count} кадр(ів)? Цю дію не можна скасувати.",
        "history_preview_title": "Перегляд кадру",

        "setup_title": "API-ключ Gemini",
        "setup_description_first": "Щоб аналізувати відео, потрібен безкоштовний API-ключ Gemini.",
        "setup_description_change": "Встав інший API-ключ Gemini і натисни «Зберегти і перевірити».",
        "setup_open_link_button": "🔗 Відкрити aistudio.google.com/apikey",
        "setup_save_button": "Зберегти і перевірити",
        "setup_paste_button": "Вставити",
        "setup_clipboard_empty": "У буфері обміну нема тексту",
        "setup_checking": "Перевіряю ключ...",
        "setup_empty": "Спочатку встав ключ у поле вище",
        "setup_invalid": "Ключ недійсний. Перевір, чи скопійований правильно",
        "setup_network_error": "Не вдалось перевірити ключ - перевір інтернет-з'єднання",

        "status_analyzing_batches": "Аналізую кадри: {done}/{total}...",
        "status_cutting_and_analyzing": "Ріжу кадри (нарізано {cut_count}) і аналізую (готово {done})...",
        "status_error_missing_key": "Спочатку введи API-ключ Gemini",
        "status_error_network": "Немає з'єднання з інтернетом",
        "status_error_timeout": "Gemini не відповів вчасно. Спробуй ще раз",
        "status_error_rate_limit": "Перевищено ліміт запитів до Gemini. Зачекай трохи і спробуй знову",
        "status_error_quota_daily": "Вичерпано добовий ліміт безкоштовного ключа Gemini. Спробуй завтра або встав інший ключ",
        "status_error_invalid_key": "API-ключ недійсний. Онови його кнопкою «Змінити ключ»",
        "status_error_server": "Тимчасова проблема на боці Gemini. Спробуй пізніше",
        "status_error_blocked": "Gemini відмовився аналізувати ці кадри (спрацював фільтр безпеки)",

        "export_pdf_button": "Один кадр (PDF)",
        "export_full_report_button": "Повний звіт (PDF)",
        "export_hint": "«Один кадр» зберігає лише відкритий знімок. «Повний звіт» — усі знайдені моменти в одному файлі.",
        "export_pdf_dialog_title": "Зберегти PDF",

        # текст самого пдф звіту, його бере пдф експорт, щоб не забути
        "pdf_heading_frame": "LookBack - звіт по кадру",
        "pdf_heading_report": "LookBack - звіт ({i}/{n})",
        "pdf_time": "Час у відео: {value}",
        "pdf_moment": "Момент №: {value}",
        "pdf_video": "Відео: {value}",
        "pdf_video_path": "Шлях до відео: {value}",
        "pdf_created": "Звіт створено: {value}",
        "pdf_image_failed": "[не вдалось завантажити зображення]",
        "pdf_image_missing": "[зображення відсутнє]",
        "pdf_description": "Опис (Gemini):",

        "photo_open_error": "Не вдалось відкрити скріншот:\n{error}",
        "export_pdf_success": "PDF збережено: {path}",
        "export_pdf_error": "Не вдалось зберегти PDF: {error}",

        "history_save_image_button": "Зберегти зображення",
        "save_image_dialog_title": "Зберегти зображення",
        "save_image_success": "Зображення збережено: {path}",
        "save_image_error": "Не вдалось зберегти зображення: {error}",

        "menu_info_button": "ⓘ",
        "about_title": "Про застосунок",
        "about_subtitle": (
            "Шукає людей і об'єкти на відео за допомогою штучного "
            "інтелекту Gemini та показує знайдені моменти з фото."
        ),
        "about_steps_title": "Як користуватись",
        "about_step_1_title": "Оберіть відео",
        "about_step_1_text": "Натисніть «Обрати відео» або перетягніть файл у вікно програми.",
        "about_step_2_title": "Опишіть, що шукати",
        "about_step_2_text": "Напишіть, кого або що потрібно знайти, наприклад: «людина в червоній куртці».",
        "about_step_3_title": "Виберіть тип аналізу",
        "about_step_3_text": (
            "«Швидкий» - до 200 кадрів, для коротких і чітких відео. "
            "«Детально» - до 2000 кадрів, рівень змінює кнопка «Налаштування»."
        ),
        "about_step_4_title": "Запустіть аналіз",
        "about_step_4_text": "Натисніть «Почати аналіз» і дочекайтесь результату. Зупинити його можна в будь-який момент.",
        "about_step_5_title": "Перегляньте результат",
        "about_step_5_text": (
            "Ліворуч - список знайдених моментів, праворуч - фото. Кадр можна "
            "відкрити на весь екран, зберегти або експортувати в PDF."
        ),
        "about_tips_title": "Корисно знати",
        "about_tip_1": "Перед першим аналізом у «Меню» оберіть тип API-ключа: безкоштовний або платний.",
        "about_tip_2": "Усі знайдені кадри зберігаються: «Меню → Переглянути історію».",
        "about_tip_3": "Максимальна тривалість відео - 70 хвилин.",
        "about_credit": "Корпорація Тимур Каленик, 2026",
    },
    LANG_EN: {
        "window_title": "LookBack",

        "choose_video": "Choose video",
        "no_file_selected": "No file selected",
        "clear_all": "Clear all",

        "search_prompt_label": "Who/what to look for in the video:",
        "char_count": "Characters: {count}/{max}",

        "start_analysis": "Start analysis",
        "stop_analysis": "Stop analysis",
        "status_cancelling": "Stopping analysis...",
        "status_cancelled": "Analysis stopped by user",

        "choose_model_label": "Analysis type:",
        "model_fast_label": "Fast",
        "model_detail_label": "Detailed",
        "settings_button": "Settings",
        "settings_title": "Settings",
        "settings_info_button": "ⓘ",

        "detail_level_weak": "Light",
        "detail_level_medium": "Medium",
        "detail_level_high": "High",
        "detail_level_max": "Maximum",
        "detail_level_frames_hint": "Up to {count} frames",

        "remember_detail_level": "Remember settings",

        "detail_info_title": "About Detailed mode",
        "detail_info_body": (
            "\u201cDetailed\u201d mode analyzes the video more thoroughly - from 400 "
            "to 2000 frames depending on the chosen level, and the higher "
            "the level, the more carefully the model looks at each batch "
            "of frames. This helps with hard scenes (poor lighting, motion "
            "on water, small objects) and long videos, but the analysis "
            "takes longer. The default is \u201cMedium\u201d (800 frames)."
        ),

        "model_info_title": "About analysis types",
        "model_info_body": (
            "Fast - the quickest and cheapest mode, up to 200 frames. "
            "Handy for videos up to 20-30 minutes where the object or "
            "person you're looking for is clearly visible.\n\n"
            "Detailed - lets you pick how many frames to use (400 to "
            "2000, via the \u201cSettings\u201d button) and makes the model "
            "look at each batch more carefully. Better for longer videos "
            "and hard scenes: poor visibility, darkness, small details, "
            "objects far from the camera. The analysis itself takes "
            "longer.\n\n"
            "Not sure which to pick? Start with \u201cFast\u201d - if the result "
            "isn't good enough or the video is long, repeat the search "
            "with \u201cDetailed\u201d."
        ),

        "status_video_too_long": "Video is too long ({duration}). Maximum is {max}",

        "duration_unknown_title": "Couldn't check duration",
        "duration_unknown_message": (
            "Couldn't determine the video's duration. Continue at your own "
            "risk (in terms of token usage)?"
        ),

        "report_label": "Report",
        "photo_placeholder": "The screenshot will appear here",
        "nothing_found_photo": "Nothing found 🙁",

        "status_choose_video_first": "Please choose a video first",
        "status_choose_model": "Choose the analysis type on the right",
        "status_choose_api_tier": "API key type is not selected. Please choose it in the menu.",
        "api_tier_required_hint": "Please choose your key type",
        "status_write_query": "Write who or what to look for",
        "status_cutting_frames": "Cutting the video into frames...",
        "status_frames_cut": "Frames cut: {count}. Asking Gemini...",
        "status_moments_found": "Moments found: {count}. Saving frames...",
        "status_done": "Done! Moments found: {count}. Analysis time: {elapsed}",
        "status_done_idle": "Done! Moments found: {count}. Analysis time: {elapsed} (excluding {idle} of computer idle time)",
        "status_done_partial": "Done, but the result is INCOMPLETE: {failed} frame batch(es) couldn't be analyzed (Gemini connection problem). Moments found: {count}. Time: {elapsed}. Try running again.",
        "status_error": "Error: {error}",

        "nothing_found_list": "Nothing found",
        "nothing_found_description": "Nothing was found in the video for the query \u201c{query}\u201d.",

        "lightbox_close": "✕ Close",

        "file_dialog_title": "Choose a video",
        "file_dialog_video_files": "Video files",
        "file_dialog_all_files": "All files",

        "dnd_drop_hint": "Drop to load the video",
        "status_dnd_not_video": "This isn't a video, couldn't load it",
        "status_dnd_analysis_running": "Wait for the current analysis to finish",
        "dnd_unknown_confirm_title": "Couldn't verify the file",
        "dnd_unknown_confirm_message": "Couldn't verify the file. Continue at your own risk?",

        "notification_analysis_complete_title": "LookBack",
        "notification_analysis_complete_message": (
            "Analysis complete — moments found: {count}"
        ),

        "theme_label": "Theme:",
        "theme_auto": "Auto",
        "theme_light": "Light",
        "theme_dark": "Dark",

        "language_label": "Language:",

        "api_tier_label": "API key type:",
        "api_tier_free": "Free",
        "api_tier_paid": "Paid",

        "dialog_yes": "Yes",
        "dialog_understood": "Understood",
        "back_button": "← Back",
        "menu_about_button": "About the app",
        "menu_exit_button": "Exit",
        "exit_title": "Exit",
        "exit_confirm": "Close the app?",
        "exit_confirm_running": "Analysis is still running. Close the app? The unfinished analysis will be cancelled.",
        "clear_all_title": "Clear everything?",
        "clear_all_confirm": "The selected video, query and current report will be reset. Frame history is not deleted.",
        "dialog_no": "No",

        "change_api_key_button": "Change key",
        "menu_button": "Menu",
        "menu_title": "Menu",
        "view_history_button": "View history",
        "reset_api_key_button": "Reset API key",
        "reset_api_key_title": "Reset API key?",
        "reset_api_key_confirm": (
            "Reset the API key? The current analysis will be cleared and "
            "you'll need to enter the key again. Frame history is kept."
        ),

        "wipe_all_button": "Delete all data",
        "wipe_all_title": "Delete all data?",
        "wipe_all_confirm": (
            "This permanently deletes the API key, settings, frame history "
            "and all saved screenshots from this computer, and then closes "
            "the app. This cannot be undone."
        ),

        "history_sessions_title": "Analysis history",
        "history_sessions_empty": "No analyses yet",
        "history_session_row_title": "Analysis {date}",
        "history_uncategorized": "Uncategorized",
        "history_frames_count": "{count} frame(s)",
        "history_session_delete_confirm_title": "Delete analysis?",
        "history_session_delete_confirm_message": (
            "Delete {count} analysis(es) along with all their frames? This can't be undone."
        ),

        "history_title": "Frames",
        "history_delete_button": "Delete",
        "history_select_all": "All",
        "history_empty": "No frames yet",
        "history_delete_confirm_title": "Delete frames?",
        "history_delete_confirm_message": "Delete {count} frame(s)? This can't be undone.",
        "history_preview_title": "Frame preview",

        "setup_title": "Gemini API key",
        "setup_description_first": "To analyze videos you need a free Gemini API key.",
        "setup_description_change": "Paste a different Gemini API key and press \u201cSave & verify\u201d.",
        "setup_open_link_button": "🔗 Open aistudio.google.com/apikey",
        "setup_save_button": "Save & verify",
        "setup_paste_button": "Paste",
        "setup_clipboard_empty": "Clipboard has no text",
        "setup_checking": "Checking the key...",
        "setup_empty": "Paste a key into the field above first",
        "setup_invalid": "The key is invalid. Check that it was copied correctly",
        "setup_network_error": "Couldn't verify the key - check your internet connection",

        "status_analyzing_batches": "Analyzing frames: {done}/{total}...",
        "status_cutting_and_analyzing": "Cutting frames (cut {cut_count}) and analyzing (done {done})...",
        "status_error_missing_key": "Enter a Gemini API key first",
        "status_error_network": "No internet connection",
        "status_error_timeout": "Gemini didn't respond in time. Try again",
        "status_error_rate_limit": "Gemini rate limit exceeded. Wait a bit and try again",
        "status_error_quota_daily": "The daily limit of the free Gemini key is used up. Try again tomorrow or use another key",
        "status_error_invalid_key": "API key is invalid. Update it with the \u201cChange key\u201d button",
        "status_error_server": "Temporary issue on Gemini's side. Try again later",
        "status_error_blocked": "Gemini refused to analyze these frames (safety filter triggered)",

        "export_pdf_button": "Single frame (PDF)",
        "export_full_report_button": "Full report (PDF)",
        "export_hint": "“Single frame” saves only the open screenshot. “Full report” saves all found moments in one file.",
        "export_pdf_dialog_title": "Save PDF",

        # текст всередині самого пдф звіту, його бере пдф експорт, щоб не забути
        "pdf_heading_frame": "LookBack - frame report",
        "pdf_heading_report": "LookBack - report ({i}/{n})",
        "pdf_time": "Video time: {value}",
        "pdf_moment": "Moment #: {value}",
        "pdf_video": "Video: {value}",
        "pdf_video_path": "Video path: {value}",
        "pdf_created": "Report created: {value}",
        "pdf_image_failed": "[failed to load image]",
        "pdf_image_missing": "[image not available]",
        "pdf_description": "Description (Gemini):",

        "photo_open_error": "Couldn't open the screenshot:\n{error}",
        "export_pdf_success": "PDF saved: {path}",
        "export_pdf_error": "Couldn't save PDF: {error}",

        "history_save_image_button": "Save image",
        "save_image_dialog_title": "Save image",
        "save_image_success": "Image saved: {path}",
        "save_image_error": "Couldn't save image: {error}",

        "menu_info_button": "ⓘ",
        "about_title": "About",
        "about_subtitle": (
            "Finds people and objects in a video using Gemini AI and "
            "shows the moments it found with photos."
        ),
        "about_steps_title": "How to use it",
        "about_step_1_title": "Choose a video",
        "about_step_1_text": "Press \u201cChoose video\u201d or drag the file into the app window.",
        "about_step_2_title": "Describe what to look for",
        "about_step_2_text": "Write who or what to find, for example: \u201ca person in a red jacket\u201d.",
        "about_step_3_title": "Pick the analysis type",
        "about_step_3_text": (
            "\u201cFast\u201d - up to 200 frames, for short and clear videos. "
            "\u201cDetailed\u201d - up to 2000 frames, the level is set with the \u201cSettings\u201d button."
        ),
        "about_step_4_title": "Start the analysis",
        "about_step_4_text": "Press \u201cStart analysis\u201d and wait for the result. You can stop it at any time.",
        "about_step_5_title": "Check the results",
        "about_step_5_text": (
            "Found moments are listed on the left, the photo is on the right. "
            "A frame can be opened full-screen, saved, or exported to PDF."
        ),
        "about_tips_title": "Good to know",
        "about_tip_1": "Before the first analysis, choose your API key type in the Menu: free or paid.",
        "about_tip_2": "All found frames are kept under \u201cMenu \u2192 View history\u201d.",
        "about_tip_3": "The maximum video length is 70 minutes.",
        "about_credit": "Timur Kalenik Corporation, 2026",
    },
}


# клас локалізації, тримає поточну мову і віддає переклад за ключем, не забути його
class Localization:

    def __init__(self, language=DEFAULT_LANGUAGE):
        self.language = language if language in TEXTS else DEFAULT_LANGUAGE

    def set_language(self, language):
        if language in TEXTS:
            self.language = language

    # ф-ція перекладу, беру текст для поточної мови, не забути її
    # якщо передано kwargs, підставляю їх в текст через формат
    # якщо ключа нема, повертаю сам ключ, щоб програма не впала
    def t(self, key, **kwargs):
        text = TEXTS[self.language].get(key, key)
        if kwargs:
            return text.format(**kwargs)
        return text

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
