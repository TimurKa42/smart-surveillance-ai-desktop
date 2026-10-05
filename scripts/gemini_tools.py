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
gemini_tools.py (десктопна версія)

Перенесено з мобільної версії: замість офіційного SDK (google-genai)
тут прямі HTTP-запити до Gemini REST API через requests. На десктопі
немає проблеми з архітектурою (як була на Android з pydantic_core під
ARM64), але REST-варіант лишили свідомо - він дає дві речі, яких не
було у старому SDK-варіанті:

1) Паралельні батчі (ThreadPoolExecutor) замість послідовного циклу -
   для довгого відео з кількома пачками кадрів це помітно швидше.
2) Ретраї з exponential backoff при тимчасових помилках (мережа,
   таймаут, 429, 5xx) - одне "гикання" інтернету більше не валить
   весь аналіз.

Ключ читається ліниво (при кожному виклику) через config.load_api_key(),
щоб main.py міг дати користувачу змінити ключ прямо в інтерфейсі без
перезапуску програми.
"""

__author__ = "Тимур Каленик (Timur Kalenik)"
__email__ = "timurkalenik950@gmail.com"
__copyright__ = "© 2026 Тимур Каленик (Timur Kalenik Corporation)"
__license__ = "Усі права захищено; див. файл LICENSE"

import base64
import json
import logging
import math
import queue as queue_module
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed, wait

import requests

import config

logger = logging.getLogger(__name__)

BATCH_SIZE = 40

# Прогрес-бар (main.py) отримує "живий" сигнал лише в момент, коли ЦІЛА
# пачка кадрів повертається з Gemini - для короткого відео (типовий
# кейс: клипи відеоспостереження на кілька секунд-хвилин) кадрів
# менше за BATCH_SIZE, тож УСЕ відео йде в Gemini ОДНІЄЮ пачкою -
# і progress_callback викликається рівно ОДИН раз, на самому кінці
# аналізу. Бар при цьому "застигає" на весь час мережевого запиту
# (секунди, а на повільних відповідях Google - і десятки секунд), а
# потім миттю стрибає з проміжного значення на майже кінцеве.
#
# Рішення: ЛИШЕ "хвостову" (неповну) пачку - ту, що набирається з
# решти кадрів по завершенню нарізки - дробимо на кілька менших
# підпачок. Для довгого відео це майже нічого не коштує (додаткові
# запити йдуть лише за рахунок останнього неповного залишку, а не
# пропорційно довжині відео); для короткого відео (весь потік - один
# "хвіст") це і є єдиний спосіб дати прогрес-бару кілька точок
# оновлення замість однієї. Дробити суть менше MIN_TRICKLE_SUBBATCH_SIZE
# кадрів немає сенсу (запит все одно швидкий, зайвий HTTP-виклик
# лише витрачає добову квоту запитів дарма).
MIN_TRICKLE_SUBBATCH_SIZE = 6
MAX_TRICKLE_SUBBATCHES = 4

# Скільки пачок кадрів відправляємо в Gemini ОДНОЧАСНО.
#
# БУЛО 2 (до появи _RateGate нижче): при 4 одночасних потоках без
# жодної координації між ними кожен сам собі ловив 429 і сам собі йшов
# у backoff - у підсумку кілька потоків одночасно натикались на ту
# саму стіну й повторювали запит в один момент знову. 2 було грубим,
# "ручним" способом уникнути цього - просто рідше стукати.
#
# ТЕПЕР темп запитів регулює _RateGate (спільний на всі потоки, вчить
# реальний RPM/TPM ліміт з відповіді 429 і сам гальмує acquire(), поки
# не звільниться вікно) - тобто СКІЛЬКИ РЕАЛЬНО запитів піде за хвилину,
# більше НЕ залежить від MAX_PARALLEL_BATCHES: зайві потоки просто
# чекають всередині gate.acquire(), а не летять "в стіну". Тому старий
# страх (4 потоки = гарантовані 429) для поточного коду вже не
# актуальний - GeminiApiError/429 і так неможливий частіше, ніж дозволяє
# вивчений ліміт, скільки потоків не постав.
#
# ЧОМУ ВСЕ ОДНО ПІДНІМАЄМО, а не лишаємо 2 "про всяк випадок": на
# стрес-тесті видно батчі по 30-46с (важке "мислення" рівня "Максимальний").
# Якщо ОБИДВА воркери одночасно застрягли на такому повільному запиті -
# третій батч, для якого ліміт (RPM) вже давно звільнив місце, фізично
# не може стартувати, поки один з двох не звільниться, - це вже не
# захист від рейт-ліміту, а звичайне голодування пропускної здатності
# через брак вільних потоків. 6 воркерів дають запас, щоб пара повільних
# батчів не блокувала решту черги, а сам темп стартів усе одно тримає
# gate.acquire() - вище цього піднімати сенсу мало, бо HTTPAdapter і
# кількість реальних одночасних з'єднань все одно впираються в те саме
# вивчене вікно.
MAX_PARALLEL_BATCHES = 6

# Та сама стеля, але для ПЛАТНОГО ключа (api_tier="paid" - обов'язкове
# поле в меню main.py, поруч із мовою/темою).
#
# ВАЖЛИВО, чого цей параметр НЕ робить: _RateGate нижче нічого не
# "знає" про тарифи заздалегідь і нічого не бере на віру - він вчить
# РЕАЛЬНИЙ ліміт (quotaValue) лише з РЕАЛЬНОЇ відповіді 429 від Gemini,
# однаково чесно для будь-якого ключа. Тобто "вгадати" стелю запитів/
# токенів наперед і десь її хардкодити тут НЕ ТРЕБА і НЕ ЗРОБЛЕНО -
# платний ключ сам "розкаже" про свій вищий ліміт, щойно (якщо) уперше
# в нього впреться.
#
# Але є одне місце, де тариф усе ж важливий - кількість ПОТОКІВ, що
# можуть одночасно чекати "в польоті". На стрес-тесті батчі рівня
# "Детально" (важке thinking) займали 30-46с. При лише 6 потоках -
# навіть якщо платний ліміт дозволяє йти вдесятеро швидше - фізично
# одночасно "в польоті" можуть бути лише 6 запитів: решта бюджету
# просто лишається невикористаною, бо нема вільного потоку, який міг
# би його зайняти (той самий "голод пропускної здатності", через який
# свого часу підняли з 2 до 6 - див. коментар над MAX_PARALLEL_BATCHES).
# На платному ключі стелю піднімаємо вище - тоді швидкість реально
# впирається у вивчений ліміт (чи в саму швидкість відповіді Gemini),
# а не в брак вільних воркерів.
PAID_TIER_MAX_PARALLEL_BATCHES = 16


def _worker_count_for_tier(api_tier, batch_count=None):
    """Стеля одночасних батчів залежно від api_tier ("free"/"paid",
    те саме значення, що main.py бере з self.settings["api_tier"]).
    Невідоме/несподіване значення (None, битий settings.json тощо)
    тихо трактується як "free" - безпечний бік помилки: гірше зайве
    дроселювання, ніж 429 на free-ключі, який користувач не очікував.

    batch_count, якщо переданий, додатково обмежує стелю кількістю
    РЕАЛЬНИХ пачок (немає сенсу тримати більше воркерів, ніж є
    завдань) - як і раніше робив find_object_in_frames().
    """
    ceiling = PAID_TIER_MAX_PARALLEL_BATCHES if api_tier == "paid" else MAX_PARALLEL_BATCHES
    if batch_count is not None:
        ceiling = min(ceiling, batch_count)
    return max(1, ceiling)


# Одна спільна Session замість requests.get/post "з нуля" на кожен виклик:
# requests за замовчуванням і так тримає пул TCP/TLS-з'єднань ВСЕРЕДИНІ
# одної Session (keep-alive) - без спільної Session кожен запит наново
# піднімає TLS-хендшейк до generativelanguage.googleapis.com, а на
# батчах, що йдуть один за одним (чи паралельно, до MAX_PARALLEL_BATCHES
# одночасно), це відчутна частка часу, яку можна віддати даром.
# HTTPAdapter з pool_maxsize=MAX_PARALLEL_BATCHES - щоб з'єднань у пулі
# вистачало на всі паралельні батчі одразу, а не тільки на один (типовий
# дефолт adapter'а - 10, тут з запасом на випадок майбутнього збільшення
# MAX_PARALLEL_BATCHES).
#
# ВАЖЛИВО про потокобезпечність: requests НЕ гарантує повну
# потокобезпечність Session офіційно, але сам пул з'єднань (urllib3)
# - так, а функції нижче (check_api_key/_send_batch_request) не
# змінюють стан самої Session (заголовки/cookies) між викликами -
# лише читають з неї з'єднання з пулу. Це стандартна й поширена
# практика для саме такого використання.
_http_session = requests.Session()
_http_session.mount(
    # pool_maxsize рахуємо від НАЙБІЛЬШОЇ можливої стелі (платної), а не
    # лише free MAX_PARALLEL_BATCHES: Session створюється один раз при
    # імпорті модуля, ЩЕ ДО того, як стане відомий api_tier конкретного
    # запуску - якщо порахувати пул під free (6), а користувач виявиться
    # на "paid" (до 16 потоків), зайві з'єднання не з пулу підуть новими
    # TLS-хендшейками щоразу, а не з keep-alive.
    "https://", requests.adapters.HTTPAdapter(pool_maxsize=max(PAID_TIER_MAX_PARALLEL_BATCHES, 4))
)

# Модель для аналізу задана в main.py (ANALYSIS_MODEL_NAME) і
# передається сюди явно при виклику find_object_in_frames - тому
# значення тут лише "запасне", на випадок прямого виклику без
# аргументу model_name.
DEFAULT_MODEL_NAME = "gemini-3.5-flash-lite"
DEFAULT_PROMPT_LANGUAGE = "ua"

API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
REQUEST_TIMEOUT_SEC = 120  # верхня межа; реальний таймаут адаптивний - див. _request_timeout()

# Адаптивний таймаут запиту: типовий батч відповідає за 5-10 с, а
# жорсткі 120 с × 4 спроби означали до ~8 хвилин "зависання" на одному
# мовчазному запиті. Тепер таймаут = кратне медіани УСПІШНИХ запитів
# цього запуску (нижня межа - щоб не різати повільніші рівні
# "Детально"), а до першої успішної відповіді - обережні 60 с.
_TIMEOUT_MEDIAN_MULTIPLIER = 6
_TIMEOUT_MIN_SEC = 30
_TIMEOUT_INITIAL_SEC = 60
_CONNECT_TIMEOUT_SEC = 10
_success_durations = []
_success_durations_lock = threading.Lock()


def _request_timeout():
    with _success_durations_lock:
        durations = sorted(_success_durations)
    if not durations:
        return (_CONNECT_TIMEOUT_SEC, _TIMEOUT_INITIAL_SEC)
    median = durations[len(durations) // 2]
    read_timeout = min(REQUEST_TIMEOUT_SEC, max(_TIMEOUT_MIN_SEC, median * _TIMEOUT_MEDIAN_MULTIPLIER))
    return (_CONNECT_TIMEOUT_SEC, read_timeout)


def _note_request_duration(seconds):
    with _success_durations_lock:
        _success_durations.append(seconds)

# Скільки РАЗІВ ДОДАТКОВО пробуємо один і той самий батч, якщо він
# впав з ТИМЧАСОВОЮ помилкою (див. RETRYABLE_ERROR_KINDS нижче), перш
# ніж здатись і показати помилку користувачу. 3 додаткових спроби
# (тобто до 4 разів сумарно) - розумний баланс: досить, щоб пережити
# короткий збій мережі чи миттєвий сплеск 429 від Gemini, і не
# настільки багато, щоб користувач сидів і чекав хвилинами на явно
# зламаний запит.
MAX_RETRIES = 3

# Базова пауза перед повтором (секунди). Кожна наступна спроба чекає
# вдвічі довше за попередню (класичний exponential backoff:
# 1.5s -> 3s -> 6s). Підходить для timeout/network/server - тимчасові
# збої, які часто минають за секунди.
RETRY_BACKOFF_BASE_SEC = 1.5

# Окрема, набагато довша база backoff саме для "rate_limit" (429).
# ЧОМУ ОКРЕМА: ліміт запитів у безкоштовного ключа рахується вікном
# на ХВИЛИНУ, а не на секунду - тому короткого 1.5s тут явно замало:
# усі 3 спроби провалювались би підряд за ~10 секунд, поки лімітне
# вікно ще не встигло звільнитись, і користувач бачив помилку замість
# того, щоб просто трохи почекати. 20s -> 40s -> 80s - з запасом
# перекриває типове хвилинне вікно вже з другої спроби.
RATE_LIMIT_BACKOFF_BASE_SEC = 20.0

# ---- Спільний "шлюз" темпу запитів (див. клас _RateGate нижче) ----
#
# ПРОБЛЕМА, яку він вирішує (виміряно на стрес-тесті 66-хвилинного
# відео, 2000 кадрів = 50 запитів): 12 з 50 пачок впіралися в 429, і
# на них пішла приблизно ПОЛОВИНА всього часу аналізу Gemini. Кожен
# потік чекав СВОЮ паузу окремо (20 -> 40 с), обидва потоки
# повторювали запит в один і той самий момент і знову стикались, а
# наступні пачки далі летіли "в стіну" - 429 приходили парами кожні
# ~7 пачок. Тепер пауза після 429 ОДНА НА ВСІХ, її тривалість Gemini
# підказує сам (RetryInfo.retryDelay), а після першого 429 темп
# запитів підлаштовується під вивчений ліміт, щоб не битись у нього знову.

# Довжина "вікна" лімітів Gemini (RPM/TPM - за хвилину).
RATE_WINDOW_SEC = 60.0

# Яку частку вивченого ліміту дозволяємо використати (запас, щоб не
# впиратись рівно в межу).
RATE_LIMIT_SAFETY = 0.9

# Скільки разів пробуємо пачку ПІСЛЯ 429, перш ніж здатись. Більше, ніж
# MAX_RETRIES (той для мережі/5xx): 429 - не поломка, а "почекай", і
# пауза тепер спільна, тож кілька повторів не б'ють по ліміту знову.
RATE_LIMIT_MAX_RETRIES = 5

# Стеля спільної паузи після одного 429, секунди.
RATE_LIMIT_MAX_COOLDOWN_SEC = 120.0

# Запас до retryDelay від Gemini, секунди.
RATE_LIMIT_COOLDOWN_MARGIN_SEC = 1.0

# Випадковий розкид пробудження потоків після спільної паузи, секунди -
# щоб обидва не стартували в одну й ту саму мить.
RATE_LIMIT_JITTER_SEC = 1.5

# Якщо у відповіді 429 нема чисел ліміту - тримаємо мінімальний
# проміжок між стартами запитів: спочатку UNKNOWN_LIMIT_GAP_SEC, після
# кожного наступного 429 збільшуємо в 1.5 раза (до UNKNOWN_LIMIT_GAP_MAX_SEC),
# після кожного успіху зменшуємо на GAP_RECOVERY_SEC.
UNKNOWN_LIMIT_GAP_SEC = 5.0
UNKNOWN_LIMIT_GAP_MAX_SEC = 12.0
GAP_RECOVERY_SEC = 0.3

# Оцінка токенів на кадр для лімітів за токенами. Gemini 3 бере 1120
# токенів на зображення за замовчуванням (підтверджено на тесті:
# 2.22M вхідних токенів / 2000 кадрів). Далі уточнюється за
# usageMetadata.promptTokenCount з реальних відповідей.
DEFAULT_TOKENS_PER_FRAME = 1120
PROMPT_OVERHEAD_TOKENS = 600

# ---- Роздільність кадрів для Gemini (generationConfig.mediaResolution) ----
#
# ГОЛОВНИЙ ВАЖІЛЬ ШВИДКОСТІ на безкоштовному ключі. Виміряно на стрес-тесті
# і на дашборді AI Studio: ліміт Flash-Lite - 250K вхідних токенів на
# ХВИЛИНУ (RPM 15 і RPD 500 не заважали: пік RPM був 8 із 15), а кожен
# кадр за замовчуванням коштує 1120 токенів. 2000 кадрів = 2.2M токенів =
# ~8 хвилин суто чекання на ліміт, як не розкладай запити в часі.
# Менша роздільність = менше токенів на кадр = пропорційно швидше:
#   None / "MEDIA_RESOLUTION_HIGH"   - 1120 токенів/кадр (дефолт Gemini 3)
#   "MEDIA_RESOLUTION_MEDIUM"        -  560 токенів/кадр (удвічі швидше)
#   "MEDIA_RESOLUTION_LOW"           -  280 токенів/кадр (вчетверо швидше)
# ЦІНА: дрібні об'єкти (човен вдалині) можуть гірше розпізнаватись, тож
# змінюй лише після порівняння на тому самому відео (кількість і якість
# знахідок). Кадри й так стискаються до GEMINI_FRAME_WIDTH пікселів у
# video_tools.
#
# НЕ глобальна константа - на відміну від попередньої версії, тепер це
# явний параметр media_resolution, що йде вниз по всьому ланцюжку
# викликів (find_object_in_frames/find_object_in_frames_streaming ->
# _analyze_batch_resilient -> _analyze_batch -> payload), той самий
# принцип, що вже застосований до thinking_level. Це дозволяє main.py
# прив'язати роздільність до режиму "Швидкий"/"Детально" (і до рівня
# слайдера в "Детально") - див. коментар над FAST_MODE_MEDIA_RESOLUTION
# і DETAIL_LEVELS у main.py. DEFAULT_MEDIA_RESOLUTION нижче - лише
# запасне значення, якщо викликач явно не передав своє (None = поведінка
# як була до цієї опції, HIGH/1120 токенів).
DEFAULT_MEDIA_RESOLUTION = None
_TOKENS_PER_FRAME_BY_RESOLUTION = {
    None: 1120,
    "MEDIA_RESOLUTION_HIGH": 1120,
    "MEDIA_RESOLUTION_MEDIUM": 560,
    "MEDIA_RESOLUTION_LOW": 280,
}

# Які "види" помилок ІМЕЄ СЕНС повторювати. "rate_limit" (429) і
# "server" (5xx) - явно тимчасові. "timeout" і "network" теж часто
# минущі. А от "invalid_key" (ключ точно поганий) чи "unknown" (щось
# структурно не так у відповіді) повторювати немає сенсу.
RETRYABLE_ERROR_KINDS = {"rate_limit", "server", "timeout", "network"}


@dataclass
class Match:
    """Один знайдений момент - номер кадру і короткий опис від Gemini."""
    frame_number: int
    description: str


SYSTEM_PROMPTS = {
    "ua": (
        "Ти - система відеоспостереження. Знайди на кадрах нижче наступний об'єкт:\n"
        "{user_prompt}\n\n"
        "Правила відповіді:\n"
        "- Якщо об'єкта на кадрі нема - просто не включай цей кадр у відповідь.\n"
        "- Опис - 3-6 слів, лише суть (що це і де), українською мовою, без вступних "
        "фраз на кшталт \"на цьому кадрі видно\" і без домислів."
    ),
    "en": (
        "You are a video surveillance system. Find the following object in the frames below:\n"
        "{user_prompt}\n\n"
        "Response rules:\n"
        "- If the object is not present in a frame, simply do not include that frame in the response.\n"
        "- Description - 3-6 words, just the essence (what it is and where), in English, without "
        "introductory phrases like \"this frame shows\" and without speculation."
    ),
}

# JSON Schema простим словником - Gemini REST API розуміє його так само,
# як раніше через SDK, без потреби у Pydantic-класах (тому pydantic
# більше не в requirements.txt).
RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "matches": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "frame_number": {"type": "INTEGER"},
                    "description": {"type": "STRING"},
                },
                "required": ["frame_number", "description"],
            },
        }
    },
    "required": ["matches"],
}


# Gemini 3.x більше НЕ підтримує temperature/top_p/top_k у
# generationConfig - їх треба повністю прибрати з payload, інакше API
# повертає помилку. Замість цього з'явився thinkingConfig.thinkingLevel
# - ВАЖЛИВО: це саме ВКЛАДЕНИЙ об'єкт thinkingConfig з полем
# thinkingLevel у camelCase, а НЕ плоске поле "thinking_level" напряму
# в generationConfig (це спричиняло 400 Bad Request).
#
# THINKING_LEVEL_BY_MODEL нижче - це лише ЗАПАСНЕ (fallback) значення
# за замовчуванням для моделі, якщо викликач не передав свій
# thinking_level явно (див. параметр thinking_level у
# find_object_in_frames() нижче). main.py завжди передає рівень явно:
# "minimal" для режиму "Швидкий" і low/low/medium/high для чотирьох
# рівнів "Детально" (всі на gemini-3.5-flash-lite).
#
# УВАГА, якщо колись додаватимеш інші моделі: у gemini-3.8-flash значення
# "minimal" НЕ підтримується (API поверне помилку валідації) - там
# допустимі лише low/medium/high. Flash-Lite "minimal" підтримує.
THINKING_LEVEL_BY_MODEL = {
    "gemini-3.5-flash-lite": "minimal",
}
DEFAULT_THINKING_LEVEL = "low"


class MissingApiKeyError(Exception):
    """Кидається, якщо спробувати аналізувати відео без збереженого ключа."""


class GeminiApiError(Exception):
    """
    Кидається, якщо Gemini API повернув помилку (неправильний ключ, ліміти
    тощо), АБО якщо запит взагалі не дійшов до сервера (нема інтернету,
    таймаут).

    kind - короткий машинний код причини, яким UI (main.py) вибирає
    зрозумілий людині текст замість сирого технічного повідомлення:
    - "network"      - нема з'єднання з інтернетом.
    - "timeout"      - сервер не відповів вчасно.
    - "rate_limit"   - 429, перевищено ліміт запитів.
    - "quota_daily"  - вичерпано ДОБОВИЙ ліміт (повторювати марно -
                       до завтра квота не оновиться).
    - "invalid_key"  - 401/403, ключ не підходить.
    - "server"       - 5xx, тимчасова проблема на боці Gemini.
    - "blocked"      - Gemini відповів 200 OK, але без жодного
                       результату (найчастіше - спрацював safety-
                       фільтр і запит/кадри заблоковано).
    - "unknown"      - усе інше (напр. неочікувана структура відповіді).

    retry_after - скільки секунд чекати перед повтором, якщо Gemini
    сам це підказав через заголовок відповіді "Retry-After" (буває
    при 429). None, якщо заголовка не було або помилка не з 429. Для
    429 сюди також потрапляє RetryInfo.retryDelay з тіла відповіді.

    quota - словник з розбору тіла 429 (або None): scope ("minute"/"day"/
    None), metric ("requests"/"tokens"/None), quota_id, limit (число або
    None). Дає змогу ПОБАЧИТИ, який саме ліміт спрацював.
    """

    def __init__(self, message, kind="unknown", retry_after=None, quota=None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.quota = quota


def check_api_key(api_key):
    """
    Перевіряє ключ РЕАЛЬНИМ запитом до Gemini API (список моделей -
    найлегший можливий запит, нічого не генерує і не витрачає квоту).

    Навмисно НЕ перевіряємо ключ за виглядом/префіксом рядка - формат
    ключів Google може відрізнятись і змінюватись з часом, тому єдиний
    надійний спосіб дізнатись, робочий ключ чи ні - справді запитати
    ним щось у Gemini.

    Повертає один з трьох станів:
    - "valid"          - ключ підходить, 200 OK.
    - "invalid"        - Gemini явно відхилив ключ (400/401/403).
    - "network_error"  - не вдалося достукатись до сервера взагалі
                          (нема інтернету, таймаут тощо). Це НЕ означає,
                          що ключ поганий - UI повинен показати інше
                          повідомлення, а не "ключ недійсний".
    """
    try:
        response = _http_session.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
            timeout=15,
        )
    except requests.exceptions.RequestException:
        return "network_error"

    if response.status_code == 200:
        return "valid"
    if response.status_code in (400, 401, 403):
        return "invalid"
    return "network_error"


def _get_api_key():
    api_key = config.load_api_key()
    if not api_key:
        raise MissingApiKeyError(
            "GEMINI_API_KEY не знайдено. Спочатку введи ключ у вікні налаштувань."
        )
    return api_key


class _RateGate:
    """
    Спільний для ВСІХ потоків "шлюз" перед кожним запитом до Gemini.

    Що він робить:
    1) Спільна пауза. Після 429 ВСІ потоки стоять до кінця паузи (а не
       кожен чекає сам по собі). Тривалість - з відповіді Gemini
       (retryDelay), якщо її нема - 20 -> 40 -> 80 с.
    2) Вивчений темп. Якщо у відповіді 429 є число ліміту (quotaValue),
       шлюз обмежує кількість запитів (або токенів) за ковзну хвилину
       до RATE_LIMIT_SAFETY від нього - і наступних 429 просто не буде.
       Якщо чисел нема - тримає мінімальний проміжок між запитами і
       підбирає його (AIMD: після 429 збільшує, після успіху зменшує).
    3) Fail-fast на добову квоту: якщо вичерпано ліміт "на добу",
       усі решта пачок падають одразу, без марних запитів і пауз.
    Стан спільний на процес (по назві моделі): ліміти діють на весь
    ключ, тож другий аналіз одразу після першого враховує, що хвилинне
    вікно ще не звільнилось.
    """

    def __init__(self, model_name):
        self.model_name = model_name
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self._starts = deque()          # (момент старту, оцінка токенів) за останнє вікно
        self._max_requests = None       # вивчений ліміт запитів за хвилину
        self._max_tokens = None         # вивчений ліміт токенів за хвилину
        self._safety = RATE_LIMIT_SAFETY        # запас для ліміту ЗАПИТІВ
        self._token_safety = 1.0                # запас для ліміту ТОКЕНІВ (див. _window_wait)
        self._min_gap = 0.0             # мінімальний проміжок між стартами (коли ліміт невідомий)
        self._next_slot = 0.0
        self._fatal = None
        self._detail_logged = False

    def reset_for_run(self):
        """Викликається на початку кожного аналізу: добова помилка з
        попереднього запуску не повинна блокувати новий (квота могла
        оновитись, або користувач змінив ключ)."""
        with self._lock:
            self._fatal = None
            self._detail_logged = False

    def estimate_tokens(self, frame_count, media_resolution=None):
        """media_resolution - те саме явне значення, що йде в payload цього
        батчу (див. _analyze_batch) - визначає, скільки токенів/кадр
        рахувати (_TOKENS_PER_FRAME_BY_RESOLUTION). Параметр, а не
        значення з __init__: шлюз спільний на весь процес по МОДЕЛІ
        (_get_gate кешує по model_name), а роздільність тепер може
        відрізнятись від виклику до виклику (режим "Швидкий"/"Детально")."""
        tokens_per_frame = _TOKENS_PER_FRAME_BY_RESOLUTION.get(media_resolution, DEFAULT_TOKENS_PER_FRAME)
        return int(frame_count * tokens_per_frame) + PROMPT_OVERHEAD_TOKENS

    def set_fatal(self, error):
        with self._lock:
            self._fatal = error

    def _purge(self, now):
        while self._starts and now - self._starts[0][0] >= RATE_WINDOW_SEC:
            self._starts.popleft()

    def _window_wait(self, now, est_tokens):
        """Скільки ще чекати, щоб новий запит не перевищив вивчені
        ліміти за ковзну хвилину. 0 - можна відправляти."""
        wait = 0.0
        starts = self._starts
        if self._max_requests is not None:
            limit = max(1, int(self._max_requests * self._safety))
            if len(starts) >= limit:
                # Треба, щоб із вікна вийшли (len - limit + 1) найстаріших.
                wait = max(wait, starts[len(starts) - limit][0] + RATE_WINDOW_SEC - now)
        if self._max_tokens is not None and starts:
            # Ліміт токенів Gemini працює як "пропускаю запит, ПОКИ використання
            # за хвилину ще не досягло ліміту" (а не "використання + цей запит
            # <= ліміт"): на дашборді при ліміті 250K пік був 314K = 7 пачок по
            # 44.8K, і всі вони прийняті. Тому й ми пускаємо новий запит, поки
            # сума за вікно < бюджету, а НЕ вимагаємо, щоб він у нього вмістився -
            # інакше шлюз душив би темп сильніше за сам Gemini (~225K замість
            # ~280K токенів/хв, тобто аналіз довший на чверть). Якщо 429 усе ж
            # приходить - _token_safety автоматично зменшується
            # (див. report_rate_limit).
            budget = int(self._max_tokens * self._token_safety)
            running = sum(tokens for _, tokens in starts)
            if running >= budget:
                release_at = starts[-1][0] + RATE_WINDOW_SEC
                for started_at, tokens in starts:
                    running -= tokens
                    if running < budget:
                        release_at = started_at + RATE_WINDOW_SEC
                        break
                wait = max(wait, release_at - now)
        return wait + 0.05 if wait > 0 else 0.0

    def acquire(self, est_tokens):
        """Блокує потік, доки можна відправляти запит; реєструє старт.
        Кидає GeminiApiError(kind="quota_daily"), якщо добову квоту
        вже вичерпано в цьому аналізі."""
        while True:
            with self._lock:
                if self._fatal is not None:
                    raise GeminiApiError(str(self._fatal), kind=self._fatal.kind)
                now = time.monotonic()
                self._purge(now)
                cooling = now < self._cooldown_until
                if cooling:
                    wait = self._cooldown_until - now
                else:
                    wait = self._window_wait(now, est_tokens)
                    if self._min_gap > 0.0:
                        wait = max(wait, self._next_slot - now)
                if wait <= 0.0:
                    self._starts.append((now, est_tokens))
                    self._next_slot = now + self._min_gap
                    return
            extra = random.uniform(0.0, RATE_LIMIT_JITTER_SEC if cooling else 0.05)
            time.sleep(wait + extra)

    def note_success(self, frame_count, prompt_tokens):
        """
        frame_count/prompt_tokens наразі НЕ використовуються всередині -
        раніше тут накопичувалась адаптивна оцінка "токенів на кадр"
        (self._tokens_per_frame, EMA), але це був баг: воно ніколи
        не ініціалізувалось у __init__() і падало на першому ж
        успішному батчі (AttributeError). А сама оцінка вже й не
        читалась ніде - estimate_tokens() рахує вартість через СТАТИЧНУ
        таблицю _TOKENS_PER_FRAME_BY_RESOLUTION(media_resolution) ще
        відколи media_resolution став параметром виклику (п.2 плану),
        а не полем шлюза. Мертвий код прибрано, сигнатуру лишаємо як є
        (викликач передає ці аргументи) - на випадок, якщо адаптивну
        оцінку захочеться повернути пізніше.
        """
        with self._lock:
            if self._min_gap > 0.0:
                self._min_gap = max(0.0, self._min_gap - GAP_RECOVERY_SEC)

    def report_rate_limit(self, error, failure_index, batch_id):
        """Викликається потоком, який щойно отримав 429 (не daily).
        Ставить спільну паузу для всіх і, якщо це "свіжа" подія (а не
        другий потік, що отримав ту саму відмову), підлаштовує темп."""
        with self._lock:
            now = time.monotonic()
            fresh = now >= self._cooldown_until
            if error.retry_after:
                wait = error.retry_after + RATE_LIMIT_COOLDOWN_MARGIN_SEC
            else:
                wait = RATE_LIMIT_BACKOFF_BASE_SEC * (2 ** failure_index)
            wait = min(wait, RATE_LIMIT_MAX_COOLDOWN_SEC)
            self._cooldown_until = max(self._cooldown_until, now + wait)
            remaining = self._cooldown_until - now

            note = ""
            if fresh:
                quota = error.quota or {}
                limit = quota.get("limit")
                already_paced = self._max_requests is not None or self._max_tokens is not None
                if already_paced:
                    # Ліміт уже знали, а 429 усе одно прийшов (напр. ключем
                    # користуються ще й деінде) - стискаємо запас.
                    self._safety = max(0.5, self._safety * 0.85)
                    self._token_safety = max(0.5, self._token_safety * 0.85)
                if quota.get("scope") == "minute" and limit:
                    if quota.get("metric") == "tokens":
                        self._max_tokens = limit
                        unit = "токенів"
                        allowed = int(limit * self._token_safety)
                    else:
                        self._max_requests = limit
                        unit = "запитів"
                        allowed = max(1, int(limit * self._safety))
                    note = (f"; ліміт Gemini: {quota.get('quota_id') or '?'} = {limit}, "
                            f"тримаю до {allowed} {unit} за хвилину")
                else:
                    self._min_gap = min(
                        UNKNOWN_LIMIT_GAP_MAX_SEC,
                        max(self._min_gap * 1.5, UNKNOWN_LIMIT_GAP_SEC),
                    )
                    note = (f"; числа ліміту у відповіді нема, тримаю проміжок "
                            f"{self._min_gap:.1f}с між запитами")
            log_detail = not self._detail_logged
            self._detail_logged = True

        logger.warning(f"[timing] батч #{batch_id}: rate_limit, спільна пауза для всіх потоків "
                       f"{remaining:.0f}с{note}")
        if log_detail:
            logger.debug(f"[limiter] перша відповідь 429 повністю: {str(error)[:500]}")


_gates = {}
_gates_lock = threading.Lock()


def _get_gate(model_name):
    with _gates_lock:
        gate = _gates.get(model_name)
        if gate is None:
            gate = _RateGate(model_name)
            _gates[model_name] = gate
        return gate


def _parse_rate_limit_details(response):
    """
    Розбирає тіло 429 від Gemini (формат Google API: error.details[]).
    Повертає (retry_delay_sec або None, quota) - див. GeminiApiError.
    Усе в try/except: якщо формат раптом інший, просто нічого не
    дізнаємось (і шлюз піде запасним шляхом), але нічого не впаде.
    """
    retry_delay = None
    quota = {"scope": None, "metric": None, "quota_id": None, "limit": None}
    message = ""
    try:
        body = response.json()
    except Exception:
        body = None
    error_obj = body.get("error") if isinstance(body, dict) else None
    if isinstance(error_obj, dict):
        message = str(error_obj.get("message", ""))
        for detail in error_obj.get("details") or []:
            if not isinstance(detail, dict):
                continue
            detail_type = str(detail.get("@type", ""))
            if detail_type.endswith("RetryInfo"):
                try:
                    retry_delay = float(str(detail.get("retryDelay", "")).strip().rstrip("s"))
                except ValueError:
                    pass
            elif detail_type.endswith("QuotaFailure"):
                for violation in detail.get("violations") or []:
                    if not isinstance(violation, dict):
                        continue
                    quota_id = str(violation.get("quotaId", ""))
                    text = f"{quota_id} {violation.get('quotaMetric', '')}".lower()
                    if "perday" in text:
                        scope = "day"
                    elif "perminute" in text:
                        scope = "minute"
                    else:
                        scope = None
                    # Добовий ліміт важливіший за хвилинний: якщо порушено
                    # обидва, повторювати марно.
                    if scope == "day" or quota["scope"] is None:
                        quota["scope"] = scope
                        quota["quota_id"] = quota_id or None
                        quota["metric"] = "tokens" if "token" in text else ("requests" if "request" in text else None)
                        try:
                            quota["limit"] = int(float(violation.get("quotaValue")))
                        except (TypeError, ValueError):
                            quota["limit"] = None
    if quota["scope"] is None:
        lowered = message.lower()
        if "per day" in lowered or "daily" in lowered:
            quota["scope"] = "day"
    return retry_delay, quota


class GeminiMatches(list):
    """Список знайдених моментів + скільки пачок/кадрів НЕ вдалось
    проаналізувати. Поводиться як звичайний list, тож старий код нічого
    не помічає; main.py читає failed_batches, щоб чесно попередити про
    неповний результат.

    failed_batches - скільки ВИХІДНИХ пачок (до можливого розбиття
    навпіл, див. _analyze_batch_resilient) мали хоч один остаточно
    втрачений кадр - для сумісності зі старим UI-повідомленням "X пачок
    не вдалось".
    failed_frames/total_frames - точніший облік у КАДРАХ: потрібен,
    щоб відрізнити "втрачено кілька кадрів десь усередині одного
    батчу" (партія в цілому успішна) від "втрачено взагалі все" -
    останнє єдине лишається фатальною помилкою (див. виклик нижче)."""
    failed_batches = 0
    total_batches = 0
    failed_frames = 0
    total_frames = 0


# Збої, після яких пачку можна просто пропустити, зберігши результати
# решти пачок; усе інше (ключ, добова квота) - фатальне.
_SKIPPABLE_ERROR_KINDS = {"timeout", "network", "server", "rate_limit"}

# Види помилок, при яких винен, найімовірніше, ВМІСТ конкретних
# кадрів (safety-фільтр Gemini на кадрі з людьми з камери
# спостереження - "blocked"; чи зіпсована/неочікувана відповідь -
# "unknown"), а не тимчасова проблема мережі/ліміту. Для них замість
# простого пропуску всього батчу пробуємо розбити його навпіл - див.
# _analyze_batch_resilient() нижче.
_SPLIT_ON_ERROR_KINDS = {"blocked", "unknown"}


def _analyze_batch_resilient(
    api_key, batch, index_offset, user_prompt, model_name, language, thinking_level=None, allow_split=True,
    media_resolution=None,
):
    """
    Обгортка над _analyze_batch(): не дає ОДНІЙ проблемній пачці
    (safety-блок Gemini чи зіпсована відповідь - _SPLIT_ON_ERROR_KINDS)
    забрати з собою результати по РЕШТІ кадрів того самого батчу і не
    валить увесь аналіз через один такий батч. Решта видів помилок
    (rate_limit/server/timeout/network/invalid_key/quota_daily)
    прокидаються як є - рішення "чи можна пропустити" і так ухвалює
    викликач (find_object_in_frames/find_object_in_frames_streaming),
    як і раніше.

    Якщо батч (>1 кадру) впав з kind із _SPLIT_ON_ERROR_KINDS - пробуємо
    ОДИН РАЗ розбити його навпіл (allow_split=False для обох половин -
    без подальшого дроблення, інакше на дійсно "зіпсованому" відео це
    перетворилось би на окремий запит на кожен кадр, що і довше, і
    дорожче). Якщо винен був лише один-два кадри в межах батчу - друга
    половина відповість нормально, і результати по ній не пропадуть.

    Повертає (matches, lost_frame_count, last_error):
      matches          - список Match, які вдалось отримати (може бути
                          неповним, якщо одна з половин таки не вийшла).
      lost_frame_count - скільки кадрів цього батчу остаточно НЕ
                          вдалось проаналізувати (0, якщо все гаразд).
      last_error       - GeminiApiError, що спричинила втрату (None,
                          якщо lost_frame_count == 0). Потрібен
                          викликачу лише для діагностики/повідомлення,
                          якщо зрештою виявиться втраченим УВЕСЬ відео.
    """
    try:
        matches = _analyze_batch(
            api_key, batch, index_offset, user_prompt, model_name, language, thinking_level,
            media_resolution=media_resolution,
        )
        return matches, 0, None
    except GeminiApiError as error:
        if error.kind not in _SPLIT_ON_ERROR_KINDS:
            raise
        if not allow_split or len(batch) <= 1:
            logger.warning(f"[timing] батч #{index_offset} ({len(batch)} кадр(и/ів)) остаточно "
                           f"втрачено ({error.kind}): {str(error)[:120]}")
            return [], len(batch), error

        mid = len(batch) // 2
        logger.warning(f"[timing] батч #{index_offset} ({len(batch)} кадрів) впав ({error.kind}) - "
                       f"пробую розбити навпіл і повторити")
        left_matches, left_lost, left_error = _analyze_batch_resilient(
            api_key, batch[:mid], index_offset, user_prompt, model_name, language, thinking_level,
            allow_split=False, media_resolution=media_resolution,
        )
        right_matches, right_lost, right_error = _analyze_batch_resilient(
            api_key, batch[mid:], index_offset + mid, user_prompt, model_name, language, thinking_level,
            allow_split=False, media_resolution=media_resolution,
        )
        return left_matches + right_matches, left_lost + right_lost, left_error or right_error


def find_object_in_frames(
    frames, user_prompt, model_name=DEFAULT_MODEL_NAME, language=DEFAULT_PROMPT_LANGUAGE,
    progress_callback=None, is_cancelled=None, thinking_level=None, media_resolution=None,
    api_tier="free",
):
    """
    Розбиває кадри на пачки по BATCH_SIZE і аналізує їх ПАРАЛЕЛЬНО
    (до MAX_PARALLEL_BATCHES одночасно) замість послідовного циклу.
    Порядок результатів у підсумковому списку не важливий - show_results()
    у main.py все одно сортує results за timestamp_sec.

    thinking_level - необов'язкове явне значення thinkingLevel
    ("minimal"/"low"/"medium"/"high"), яке піде в КОЖЕН батч цього
    аналізу замість запасного THINKING_LEVEL_BY_MODEL.get(model_name).
    Навіщо це тут: для режиму "Детально" main.py дає користувачу 4
    рівні деталізації, і кожному рівню відповідає свій thinkingLevel -
    цей модуль свідомо НІЧОГО не знає про рівні/слайдер, лише приймає
    вже готове рядкове значення від викликача. None (за замовчуванням)
    - поведінка як і раніше, лишається запасне значення за моделлю.
    ВАЖЛИВО: значення фіксується один раз тут, на вході всього аналізу
    - усі батчі (навіть якщо їх кілька і вони йдуть паралельно) підуть
    з ОДНИМ і тим самим thinking_level, який не зміниться, навіть якщо
    користувач посмикає слайдер в інтерфейсі вже під час виконання
    запущеного аналізу.

    media_resolution - те саме, але для generationConfig.mediaResolution
    (None/"MEDIA_RESOLUTION_LOW"/"MEDIA_RESOLUTION_MEDIUM"/
    "MEDIA_RESOLUTION_HIGH" - див. коментар над DEFAULT_MEDIA_RESOLUTION
    вище). Так само фіксується один раз на весь аналіз і так само нічого
    не знає про режими/слайдер - лише приймає готове значення.

    api_tier - "free" (за замовчуванням) або "paid", те саме обов'язкове
    поле з меню main.py. Впливає ЛИШЕ на стелю одночасних потоків
    (_worker_count_for_tier) - сам темп запитів все одно регулює
    _RateGate, вивчаючи РЕАЛЬНИЙ ліміт із відповідей Gemini, а не з
    цього параметра (див. коментар над PAID_TIER_MAX_PARALLEL_BATCHES).

    progress_callback(done_batches, total_batches, done_frames, total_frames),
    якщо переданий, викликається ПІСЛЯ кожної завершеної пачки. Окремо
    йдуть лічильник ПАЧОК (для тексту статусу - "Аналізую пачку X/Y")
    і лічильник КАДРІВ (для самого прогрес-бару) - вони навмисно РІЗНІ:
    пачки майже завжди нерівного розміру (останній батч добирає
    залишок, який часто набагато менший за BATCH_SIZE), тож рахувати
    прогрес як "готових пачок / усіх пачок" бреше - маленький останній
    батч, який реально виконується вдвічі швидше за повний, все одно
    "коштував" би стільки ж прогрес-бару, скільки й повний. Рахунок за
    кадрами точно відображає, яка частка РЕАЛЬНОЇ роботи вже готова.
    Викликається з фонового потоку, тож сам callback має бути
    потокобезпечним (у main.py це робиться через self.after(...)).

    is_cancelled, якщо переданий, - функція БЕЗ аргументів, що повертає
    True, коли аналіз потрібно перервати (наприклад, користувач
    натиснув "Очистити все" або запустив новий аналіз, поки цей ще
    йшов). ВАЖЛИВО: реально зупинити вже запущений HTTP-запит
    неможливо - але можна скасувати ті батчі, які ще НЕ встигли
    стартувати і просто стоять у черзі ThreadPoolExecutor (бо
    одночасно виконується не більше MAX_PARALLEL_BATCHES).
    """
    api_key = _get_api_key()
    _get_gate(model_name).reset_for_run()
    with _success_durations_lock:
        _success_durations.clear()  # адаптивний таймаут рахуємо заново для кожного запуску

    batches = [
        (batch_start, frames[batch_start:batch_start + BATCH_SIZE])
        for batch_start in range(0, len(frames), BATCH_SIZE)
    ]

    if not batches:
        return GeminiMatches()

    all_matches = GeminiMatches()
    first_skipped_error = None
    worker_count = _worker_count_for_tier(api_tier, len(batches))
    total_batches = len(batches)
    total_frames = len(frames)
    done_batches = 0
    done_frames = 0

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        # Значення словника - розмір ПАЧКИ (у кадрах), а не index_offset
        # (він все одно нікуди далі не використовується) - потрібен,
        # щоб після завершення future додати саме стільки до done_frames.
        futures = {
            executor.submit(
                _analyze_batch_resilient, api_key, batch, offset, user_prompt, model_name, language,
                thinking_level, media_resolution=media_resolution,
            ): len(batch)
            for offset, batch in batches
        }
        for future in as_completed(futures):
            if is_cancelled is not None and is_cancelled():
                for pending_future in futures:
                    pending_future.cancel()
                break

            batch_frame_count = futures[future]
            try:
                matches, lost_frame_count, batch_error = future.result()
            except CancelledError:
                continue
            except GeminiApiError as error:
                if error.kind not in _SKIPPABLE_ERROR_KINDS:
                    for pending_future in futures:
                        pending_future.cancel()
                    raise
                # Одна пачка не відповіла навіть після всіх повторів -
                # не губимо результати решти (і кількох хвилин нарізки).
                all_matches.failed_batches += 1
                all_matches.failed_frames += batch_frame_count
                first_skipped_error = first_skipped_error or error
                logger.warning(f"[timing] пачку пропущено після всіх повторів ({error.kind}): {str(error)[:120]}")
            else:
                all_matches.extend(matches)
                if lost_frame_count:
                    # Батч частково (чи, у гіршому випадку, повністю)
                    # втрачено через blocked/unknown - див.
                    # _analyze_batch_resilient. Рахуємо як "невдалу
                    # пачку" для сумісності зі старим UI-повідомленням,
                    # і додатково точно за кадрами - для рішення нижче.
                    all_matches.failed_batches += 1
                    all_matches.failed_frames += lost_frame_count
                    first_skipped_error = first_skipped_error or batch_error
            done_batches += 1
            done_frames += batch_frame_count
            if progress_callback is not None:
                progress_callback(done_batches, total_batches, done_frames, total_frames)

    all_matches.total_batches = total_batches
    all_matches.total_frames = total_frames
    if first_skipped_error is not None and all_matches.failed_frames >= total_frames:
        raise first_skipped_error  # не вдалось жодного кадру - це повна помилка, а не часткова
    return all_matches


# Сентинел "кадрів більше не буде" для find_object_in_frames_streaming().
# Окремий унікальний об'єкт (а не None чи рядок) - щоб його неможливо
# було випадково сплутати зі справжнім кадром чи помилкою.
STREAM_END = object()


def _split_trailing_batch(frames):
    """Ділить неповну "хвостову" пачку кадрів на кілька менших -
    ЛИШЕ щоб дати progress_callback кілька точок оновлення замість
    однієї (див. коментар над MIN_TRICKLE_SUBBATCH_SIZE вище). Ніяк не
    впливає на результат аналізу - лише на те, скількома HTTP-запитами
    він прийде і як часто прогрес-бар отримає сигнал.

    Якщо кадрів замало (<= MIN_TRICKLE_SUBBATCH_SIZE) - дроблення не
    дає користі (запит і так швидкий), повертаємо як є, одним шматком."""
    n = len(frames)
    if n <= MIN_TRICKLE_SUBBATCH_SIZE:
        return [frames]
    chunk_count = min(MAX_TRICKLE_SUBBATCHES, n // MIN_TRICKLE_SUBBATCH_SIZE)
    if chunk_count <= 1:
        return [frames]
    base, remainder = divmod(n, chunk_count)
    chunks = []
    start = 0
    for i in range(chunk_count):
        size = base + (1 if i < remainder else 0)
        chunks.append(frames[start:start + size])
        start += size
    return chunks


def find_object_in_frames_streaming(
    frame_queue, user_prompt, model_name=DEFAULT_MODEL_NAME, language=DEFAULT_PROMPT_LANGUAGE,
    progress_callback=None, is_cancelled=None, thinking_level=None, max_frames_hint=None,
    media_resolution=None, started_callback=None, api_tier="free",
):
    """
    Те саме, що find_object_in_frames(), але кадри приймаються НЕ
    готовим списком, а через queue.Queue() - і пачки йдуть у Gemini
    ОДРАЗУ, як тільки назбирується BATCH_SIZE кадрів, навіть якщо
    відео ще продовжує нарізатись у паралельному потоці.

    ЧОМУ ЦЕ ПОТРІБНО: раніше нарізка (кілька хвилин на довгому відео)
    і звернення до Gemini (ще кілька хвилин через ліміти безкоштовного
    ключа) йшли строго одне за одним. Тепер вони йдуть ПАРАЛЕЛЬНО:
    поки останні сегменти відео ще декодуються, перші пачки вже
    летять у Gemini. Економія часу - приблизно тривалість КОРОТШОГО
    з двох етапів (той, що йде "у фоні" за довшим).

    frame_queue - queue.Queue(), куди постачальник (video_tools через
    on_frame_ready, за посередництвом main.py) кладе, СТРОГО в такому
    порядку:
      1) словники кадрів (той самий формат, що елементи frames у
         find_object_in_frames) - один за одним, у порядку появи;
      2) РІВНО ОДИН раз - або сентинел STREAM_END (нарізка успішно
         завершилась), або екземпляр Exception (нарізка впала з
         помилкою - ця функція перерве прийом нових кадрів, дочекається
         вже запущених пачок і прокине ту саму помилку далі, так само,
         як зробив би виклик extract_frames(), якби впав ДО аналізу).

    max_frames_hint - очікувана верхня межа кількості кадрів (те саме
    max_frames, що передається в extract_frames). Реальна кількість
    стає відома лише по завершенню нарізки, тож це лише орієнтир для
    прогрес-бару (total_frames/total_batches) - як і в звичайному
    режимі, рахуємо прогрес за КАДРАМИ, а не за пачками.

    Решта поведінки (ретраї, спільний rate-ліміт через _RateGate,
    пропуск "тимчасово" зламаних пачок, progress_callback, is_cancelled,
    thinking_level, media_resolution) - точно така сама, як у
    find_object_in_frames(); ця функція лише інакше ДІСТАЄ кадри для
    пачок.

    started_callback(frame_count) - опційно, викликається одразу в
    момент ВІДПРАВКИ кожної пачки в Gemini (ще до відповіді), з
    кількістю кадрів у ній. На відміну від progress_callback (який
    підтверджує лише ЗАВЕРШЕНІ пачки), це дозволяє викликаючому коду
    знати, скільки кадрів зараз "у польоті" - і, наприклад, обережно
    підкручувати прогрес-бар вперед, поки чекаємо реальної відповіді
    (див. _progress_trickle_* у main.py).

    api_tier - "free"/"paid", те саме, що в find_object_in_frames() -
    впливає лише на стелю одночасних потоків, див. коментар там і над
    PAID_TIER_MAX_PARALLEL_BATCHES.
    """
    api_key = _get_api_key()
    _get_gate(model_name).reset_for_run()
    with _success_durations_lock:
        _success_durations.clear()

    total_frames_hint = max(1, max_frames_hint or 1)
    total_batches_hint = max(1, math.ceil(total_frames_hint / BATCH_SIZE))

    all_matches = GeminiMatches()
    first_skipped_error = None
    extraction_error = None
    done_batches = 0
    done_frames = 0
    next_index = 0
    pending_batch = []
    stream_ended = False

    def report_progress():
        if progress_callback is not None:
            progress_callback(
                done_batches, max(done_batches, total_batches_hint),
                done_frames, max(done_frames, total_frames_hint),
            )

    with ThreadPoolExecutor(max_workers=_worker_count_for_tier(api_tier)) as executor:
        pending_futures = {}  # future -> кількість кадрів у ЦІЙ пачці (для точного done_frames)

        while not stream_ended or pending_batch or pending_futures:
            if is_cancelled is not None and is_cancelled():
                for future in pending_futures:
                    future.cancel()
                break

            # 1) Дочитуємо з черги все, що вже накопичилось, поки не
            #    набереться повна пачка, черга не спорожніє чи не
            #    прийде сентинел/помилка. Короткий timeout - щоб цикл
            #    міг час від часу перевіряти is_cancelled() і забирати
            #    готові futures нижче, навіть якщо нових кадрів довго нема.
            if not stream_ended:
                try:
                    while len(pending_batch) < BATCH_SIZE:
                        item = frame_queue.get(timeout=0.1)
                        if item is STREAM_END:
                            stream_ended = True
                            break
                        if isinstance(item, Exception):
                            extraction_error = item
                            stream_ended = True
                            break
                        pending_batch.append(item)
                except queue_module.Empty:
                    pass

            if extraction_error is not None:
                for future in pending_futures:
                    future.cancel()
                break

            if pending_batch and (len(pending_batch) >= BATCH_SIZE or stream_ended):
                # Повна пачка (набралась до BATCH_SIZE ПІД ЧАС нарізки) -
                # відправляємо як є. "Хвостовий" залишок (нарізка вже
                # закінчилась, а до BATCH_SIZE не дотягнули - типово для
                # коротких відео, де це ВЕСЬ потік кадрів) - дробимо на
                # кілька менших підпачок (див. _split_trailing_batch),
                # щоб прогрес-бар отримав кілька точок оновлення замість
                # однієї на весь аналіз.
                if len(pending_batch) >= BATCH_SIZE:
                    batches_to_submit = [pending_batch]
                else:
                    batches_to_submit = _split_trailing_batch(pending_batch)
                pending_batch = []
                for batch in batches_to_submit:
                    offset = next_index
                    next_index += len(batch)
                    future = executor.submit(
                        _analyze_batch_resilient, api_key, batch, offset, user_prompt, model_name, language,
                        thinking_level, media_resolution=media_resolution,
                    )
                    pending_futures[future] = len(batch)
                    if started_callback is not None:
                        started_callback(len(batch))

            # 2) Забираємо вже завершені пачки, не чекаючи довго - щоб
            #    не пропустити момент, коли варто підкласти в чергу ще
            #    кадрів (крок 1 вище) чи перевірити скасування.
            if pending_futures:
                finished, still_pending = wait(pending_futures.keys(), timeout=0.1)
                for future in finished:
                    batch_frame_count = pending_futures.pop(future)
                    try:
                        matches, lost_frame_count, batch_error = future.result()
                    except CancelledError:
                        continue
                    except GeminiApiError as error:
                        if error.kind not in _SKIPPABLE_ERROR_KINDS:
                            for pending_future in pending_futures:
                                pending_future.cancel()
                            raise
                        all_matches.failed_batches += 1
                        all_matches.failed_frames += batch_frame_count
                        first_skipped_error = first_skipped_error or error
                        logger.warning(f"[timing] пачку пропущено після всіх повторів ({error.kind}): {str(error)[:120]}")
                    else:
                        all_matches.extend(matches)
                        if lost_frame_count:
                            all_matches.failed_batches += 1
                            all_matches.failed_frames += lost_frame_count
                            first_skipped_error = first_skipped_error or batch_error
                    done_batches += 1
                    done_frames += batch_frame_count
                    report_progress()
                pending_futures = {f: pending_futures[f] for f in still_pending}

    if extraction_error is not None:
        # Помилка сталась у нарізці (video_tools/main.py), а не в
        # Gemini - прокидаємо як є, викликаючий код (run_pipeline)
        # ловить її так само, як ловив би помилку extract_frames()
        # до цього рефакторингу.
        raise extraction_error

    all_matches.total_batches = done_batches
    all_matches.total_frames = done_frames
    if first_skipped_error is not None and all_matches.failed_frames >= done_frames and done_frames > 0:
        raise first_skipped_error
    return all_matches


def _analyze_batch(api_key, batch, index_offset, user_prompt, model_name, language, thinking_level=None,
                    media_resolution=None):
    """
    Аналізує одну пачку кадрів через прямий REST-виклик до Gemini.

    index_offset - зсув цієї пачки в загальному списку кадрів. Сам
    запит до Gemini нумерує кадри ЛОКАЛЬНО (0..len(batch)-1) - назад у
    глобальний номер результати перераховуються тут же, перед
    поверненням (_remap_local_frame_numbers).

    thinking_level - те саме явне значення thinkingLevel, що прийшло
    "згори" від find_object_in_frames() (див. докстрінг там). Якщо
    None - підставляємо запасне значення за моделлю, як і раніше.

    media_resolution - те саме явне значення mediaResolution, що
    прийшло "згори" (див. докстрінг find_object_in_frames). None -
    поле generationConfig.mediaResolution взагалі не додається в
    payload (поведінка Gemini за замовчуванням, HIGH/1120 токенів/
    кадр) - так само впливає і на оцінку токенів для rate-ліміту
    нижче (gate.estimate_tokens).

    Якщо запит впаде з ТИМЧАСОВОЮ помилкою (rate_limit/server/timeout/
    network - див. RETRYABLE_ERROR_KINDS) - повторює його ще до
    MAX_RETRIES разів із зростаючою паузою (exponential backoff),
    перш ніж остаточно здатись і перекинути помилку нагору.
    """
    system_prompt = SYSTEM_PROMPTS.get(language, SYSTEM_PROMPTS[DEFAULT_PROMPT_LANGUAGE])

    parts = [{"text": system_prompt.format(user_prompt=user_prompt)}]

    for i, frame in enumerate(batch):
        # Нумеруємо ЛОКАЛЬНО (0..len(batch)-1), а не глобальним
        # frame_number (наприклад, 1961) - модель точно не переплутає
        # цифру, коли їх лише 40 і рахунок завжди з нуля, ніж коли
        # треба безпомилково повторити довільне число з підказки. Назад
        # у глобальний номер перераховуємо на боці коду нижче (_remap_local_frame_numbers) -
        # це надійніше, ніж покладатись, що модель не помилиться цифрою.
        parts.append({"text": f"Кадр номер {i}:"})
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(frame["jpg_bytes"]).decode("ascii"),
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "response_schema": RESPONSE_SCHEMA,
            "thinkingConfig": {
                "thinkingLevel": thinking_level or THINKING_LEVEL_BY_MODEL.get(model_name, DEFAULT_THINKING_LEVEL),
            },
        },
    }
    if media_resolution:
        payload["generationConfig"]["mediaResolution"] = media_resolution

    url = API_URL_TEMPLATE.format(model=model_name)

    gate = _get_gate(model_name)
    est_tokens = gate.estimate_tokens(len(batch), media_resolution)

    batch_start_ts = time.time()
    transient_failures = 0     # timeout / network / server - до MAX_RETRIES
    rate_limit_failures = 0    # 429 - до RATE_LIMIT_MAX_RETRIES (пауза спільна)
    while True:
        # Чекає, якщо триває спільна пауза або вичерпано вивчений темп.
        gate.acquire(est_tokens)
        try:
            result, prompt_tokens = _send_batch_request(url, api_key, payload)
        except GeminiApiError as error:
            if error.kind == "rate_limit":
                if (error.quota or {}).get("scope") == "day":
                    daily_error = GeminiApiError(
                        f"Вичерпано добовий ліміт Gemini: {str(error)[:300]}", kind="quota_daily"
                    )
                    gate.set_fatal(daily_error)
                    logger.warning(f"[timing] батч #{index_offset}: добовий ліміт вичерпано - "
                                   f"решту пачок не відправляю")
                    raise daily_error from error
                rate_limit_failures += 1
                if rate_limit_failures > RATE_LIMIT_MAX_RETRIES:
                    raise
                gate.report_rate_limit(error, rate_limit_failures - 1, index_offset)
                continue
            if error.kind not in RETRYABLE_ERROR_KINDS or transient_failures >= MAX_RETRIES:
                raise
            # Звичайний короткий backoff для timeout/network/server:
            # 1.5s -> 3s -> 6s (базово), помножено на джиттер
            # random.uniform(1.0, 1.3) - щоб два паралельні потоки
            # (MAX_PARALLEL_BATCHES), які впали на server/timeout майже
            # одночасно, не прокинулись і не повторили запит теж
            # майже одночасно (це саме по собі може спровокувати 429
            # на небагатому free-tier RPM). Викликається з робочого
            # потоку ThreadPoolExecutor - time.sleep() тут безпечний.
            sleep_sec = RETRY_BACKOFF_BASE_SEC * (2 ** transient_failures) * random.uniform(1.0, 1.3)
            transient_failures += 1
            logger.warning(f"[timing] батч #{index_offset}: помилка {error.kind}, "
                           f"чекаю {sleep_sec:.1f}с перед повтором")
            time.sleep(sleep_sec)
            continue

        gate.note_success(len(batch), prompt_tokens)
        attempts_used = transient_failures + rate_limit_failures + 1
        logger.info(f"[timing] батч #{index_offset}: {time.time() - batch_start_ts:.1f}с "
                    f"(спроба {attempts_used})")
        return _remap_local_frame_numbers(result, index_offset, len(batch))


def _remap_local_frame_numbers(matches, index_offset, batch_size):
    """
    Перераховує ЛОКАЛЬНІ (0..batch_size-1) frame_number, які повернула
    _send_batch_request(), назад у справжні номери кадрів загального
    списку (додаючи index_offset) - див. коментар над "Кадр номер {i}:"
    у _analyze_batch() вище.

    Захист про всяк випадок: якщо модель усе ж повернула номер поза
    межами пачки (0..batch_size-1) - такий збіг відкидаємо з
    діагностикою в консоль, а не зсуваємо на випадковий, помилковий
    кадр десь-інде у відео (раніше, з глобальною нумерацією, така
    помилка моделі була б взагалі непомітною).
    """
    remapped = []
    for match in matches:
        if 0 <= match.frame_number < batch_size:
            match.frame_number += index_offset
            remapped.append(match)
        else:
            logger.debug(f"[diag] батч #{index_offset}: Gemini повернув номер кадру "
                         f"{match.frame_number} поза межами пачки (0..{batch_size - 1}) - результат відкинуто")
    return remapped


def _send_batch_request(url, api_key, payload):
    """
    Один "сирий" HTTP-виклик до Gemini + розбір відповіді. Винесено з
    _analyze_batch окремою функцією, щоб retry-цикл там міг просто
    викликати це знову при тимчасовій помилці.
    """
    request_started_ts = time.time()
    try:
        response = _http_session.post(
            url,
            params={"key": api_key},
            json=payload,
            timeout=_request_timeout(),
        )
    except requests.exceptions.Timeout as error:
        raise GeminiApiError(f"Timeout: {error}", kind="timeout") from error
    except requests.exceptions.RequestException as error:
        raise GeminiApiError(f"Network error: {error}", kind="network") from error

    if response.status_code != 200:
        detail = f"Gemini API повернув помилку {response.status_code}: {response.text[:300]}"
        retry_after = None
        quota = None
        if response.status_code == 429:
            kind = "rate_limit"
            # Google іноді підказує напряму, скільки секунд чекати -
            # якщо є, довіряємо ЙОМУ, а не тільки нашому фіксованому
            # backoff (сирий заголовок - завжди ціле число секунд за
            # специфікацією HTTP, тому обійшлись без розбору HTTP-дати).
            raw_retry_after = response.headers.get("Retry-After")
            if raw_retry_after is not None:
                try:
                    retry_after = float(raw_retry_after)
                except ValueError:
                    retry_after = None
            # Тіло 429 у Gemini містить точну паузу (RetryInfo) і назву/
            # значення порушеного ліміту (QuotaFailure) - дивись
            # _parse_rate_limit_details().
            body_retry_delay, quota = _parse_rate_limit_details(response)
            if body_retry_delay is not None:
                retry_after = max(retry_after or 0.0, body_retry_delay)
        elif response.status_code in (401, 403):
            kind = "invalid_key"
        elif response.status_code >= 500:
            kind = "server"
        else:
            kind = "unknown"
        raise GeminiApiError(detail, kind=kind, retry_after=retry_after, quota=quota)

    _note_request_duration(time.time() - request_started_ts)
    result = response.json()

    # Gemini іноді відповідає 200 OK, але БЕЗ жодного результату - типово
    # це safety-фільтр, який заблокував або сам запит (promptFeedback.
    # blockReason на верхньому рівні відповіді), або конкретну відповідь
    # (порожній список candidates).
    block_reason = result.get("promptFeedback", {}).get("blockReason")
    if block_reason:
        raise GeminiApiError(
            f"Gemini заблокував запит: {block_reason}", kind="blocked"
        )

    candidates = result.get("candidates") or []
    if not candidates:
        raise GeminiApiError(
            f"Gemini не повернув жодної відповіді: {json.dumps(result)[:300]}", kind="blocked"
        )

    try:
        text_answer = candidates[0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as error:
        raise GeminiApiError(
            f"Неочікувана структура відповіді Gemini: {json.dumps(result)[:300]}", kind="unknown"
        ) from error

    try:
        data = json.loads(text_answer)
    except json.JSONDecodeError as error:
        raise GeminiApiError(f"Gemini повернув невалідний JSON: {text_answer[:300]}", kind="unknown") from error

    matches = [
        Match(
            frame_number=int(item["frame_number"]),
            description=str(item["description"]),
        )
        for item in data.get("matches", [])
    ]
    prompt_tokens = (result.get("usageMetadata") or {}).get("promptTokenCount")
    return matches, prompt_tokens

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
