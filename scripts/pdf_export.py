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
pdf_export.py (десктопна версія)

Створює PDF-звіт зі скріншотами знайдених моментів: час у відео,
опис від Gemini і номер моменту. Є два режими:

- export_frame_to_pdf()  - PDF з одним моментом.
- export_report_to_pdf() - PDF з усіма моментами, по одній сторінці на кожен.

Це окремий модуль, без роботи з інтерфейсом, тому його легко тестувати.

Бібліотека fpdf2 - чиста Python, без складних збірок. Її взяли з
мобільної версії, бо однаковий код для обох версій простіше підтримувати.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import os
from datetime import datetime

from fpdf import FPDF
from PIL import Image

from localization import Localization

# ---------------------------------------------------------------------
# Кирилиця. Вбудовані шрифти fpdf не мають кирилиці, тому шукаємо
# системний TTF-шрифт з кирилицею. Беремо перший, який знайдеться.
# Якщо нічого немає, лишаємо Helvetica: кирилиця не відобразиться,
# але програма не впаде.
# ---------------------------------------------------------------------
_FONT_CANDIDATES = [
    # Windows
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/segoeuib.ttf"),
    # macOS
    ("/System/Library/Fonts/Supplemental/Arial.ttf",
     "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ("/Library/Fonts/Arial Unicode.ttf", "/Library/Fonts/Arial Unicode.ttf"),
    # Linux
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
     "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
     "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
]


def _find_cyrillic_font():
    """Повертає (звичайний, жирний) шрифт, який знайшли на диску.
    Якщо нічого немає, повертає (None, None)."""
    for regular_path, bold_path in _FONT_CANDIDATES:
        if os.path.exists(regular_path):
            if os.path.exists(bold_path):
                return regular_path, bold_path
            return regular_path, regular_path
    return None, None


# ---------------------------------------------------------------------
# Тексти для PDF лежать у localization.py (ключі pdf_*).
# Тут тільки беремо перекладач потрібною мовою.
# ---------------------------------------------------------------------
def _translator(lang):
    return Localization(lang).t


PAGE_W = 210.0  # ширина A4, мм
PAGE_H = 297.0  # висота A4, мм
MARGIN = 18.0   # відступ від краю, мм


def _wrap_text(text, pdf, max_width):
    """Розбиває текст на рядки, щоб кожен вміщався в max_width."""
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if pdf.get_string_width(candidate) <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _make_pdf():
    """Створює новий PDF з полями і шрифтом.
    Повертає (pdf, назва_шрифту). Назву потім передаємо далі,
    щоб не шукати шрифт на диску для кожної сторінки."""
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=MARGIN)
    pdf.set_margins(MARGIN, MARGIN, MARGIN)

    regular_path, bold_path = _find_cyrillic_font()
    if regular_path:
        pdf.add_font("Report", "", regular_path)
        pdf.add_font("Report", "B", bold_path)
        font_name = "Report"
    else:
        font_name = "Helvetica"

    return pdf, font_name


def _render_frame_page(pdf, font_name, time_str, description, image_path,
                        video_name="", video_path="", frame_index=None,
                        heading=None, lang="ua"):
    """Малює одну сторінку звіту: заголовок, дані, фото і опис.
    Використовується і в export_frame_to_pdf, і в export_report_to_pdf."""
    content_width = PAGE_W - 2 * MARGIN
    t = _translator(lang)
    if heading is None:
        heading = t("pdf_heading_frame")

    # Заголовок
    pdf.set_font(font_name, "B", 16)
    pdf.cell(content_width, 9, heading, new_x="LMARGIN", new_y="NEXT")

    pdf.set_draw_color(190, 190, 190)
    y = pdf.get_y() + 2
    pdf.line(MARGIN, y, PAGE_W - MARGIN, y)
    pdf.set_y(y + 6)

    # Дані про момент: час, номер, відео, дата
    pdf.set_font(font_name, "", 11)
    meta_lines = [t("pdf_time", value=time_str)]
    if frame_index is not None:
        meta_lines.append(t("pdf_moment", value=frame_index))
    if video_name:
        meta_lines.append(t("pdf_video", value=video_name))
    if video_path:
        meta_lines.append(t("pdf_video_path", value=video_path))
    meta_lines.append(t("pdf_created", value=datetime.now().strftime('%Y-%m-%d %H:%M:%S')))

    for line in meta_lines:
        pdf.cell(content_width, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    # Картинка
    if image_path and os.path.exists(image_path):
        try:
            with Image.open(image_path) as img:
                img_w, img_h = img.size
            aspect = img_h / float(img_w)

            max_img_w = content_width
            max_img_h = PAGE_H * 0.55  # картинка не має займати весь аркуш, місце лишаємо під опис
            draw_w = max_img_w
            draw_h = draw_w * aspect
            if draw_h > max_img_h:
                draw_h = max_img_h
                draw_w = draw_h / aspect

            x = MARGIN + (content_width - draw_w) / 2
            pdf.image(image_path, x=x, y=pdf.get_y(), w=draw_w, h=draw_h)
            pdf.set_y(pdf.get_y() + draw_h + 8)
        except Exception:
            pdf.set_font(font_name, "", 10)
            pdf.cell(content_width, 10, t("pdf_image_failed"), new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font(font_name, "", 10)
        pdf.cell(content_width, 10, t("pdf_image_missing"), new_x="LMARGIN", new_y="NEXT")

    # Опис від Gemini
    pdf.set_font(font_name, "B", 12)
    pdf.cell(content_width, 7, t("pdf_description"), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font(font_name, "", 11)
    description = description or "-"
    for paragraph in description.splitlines() or [description]:
        for line in _wrap_text(paragraph, pdf, content_width):
            pdf.cell(content_width, 6, line, new_x="LMARGIN", new_y="NEXT")


def export_frame_to_pdf(output_path, image_path, time_str, description,
                         video_name="", video_path="", frame_index=None,
                         lang="ua"):
    """
    Створює PDF з одним моментом (скріншот + дані).

    output_path - куди зберегти PDF
    image_path  - шлях до скріншота
    time_str    - час у відео, формат ЧЧ:ММ:СС
    description - опис від Gemini
    video_name  - назва відео (необов'язково)
    video_path  - повний шлях до відео (необов'язково)
    frame_index - номер моменту, з 1 (необов'язково)
    lang        - мова тексту в PDF: "ua" або "en"
    """
    pdf, font_name = _make_pdf()
    pdf.add_page()
    _render_frame_page(pdf, font_name, time_str, description, image_path,
                        video_name, video_path, frame_index, lang=lang)
    pdf.output(output_path)
    return output_path


def export_report_to_pdf(output_path, results, video_name="", video_path="",
                         lang="ua"):
    """
    Створює один PDF з усіма знайденими моментами, по одній сторінці
    на кожен. Порядок такий самий, як у списку в програмі.

    results    - список словників: {"time_str", "description", "screenshot_path"}
    video_name - назва відео (необов'язково)
    video_path - повний шлях до відео (необов'язково)
    """
    pdf, font_name = _make_pdf()

    for i, result in enumerate(results, start=1):
        pdf.add_page()
        _render_frame_page(
            pdf, font_name,
            time_str=result["time_str"],
            description=result["description"],
            image_path=result.get("screenshot_path"),
            video_name=video_name,
            video_path=video_path,
            frame_index=i,
            heading=_translator(lang)("pdf_heading_report", i=i, n=len(results)),
            lang=lang,
        )

    pdf.output(output_path)
    return output_path


def build_pdf_filename(time_str, frame_index=None):
    """Ім'я файлу з часом і міткою часу. Так повторний експорт
    не перезаписує старий файл."""
    safe_time = time_str.replace(":", "-")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if frame_index is not None:
        return f"report_{safe_time}_frame{frame_index}_{stamp}.pdf"
    return f"report_{safe_time}_{stamp}.pdf"


def build_full_report_filename():
    """Ім'я файлу для звіту з усіма моментами."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"report_full_{stamp}.pdf"

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
