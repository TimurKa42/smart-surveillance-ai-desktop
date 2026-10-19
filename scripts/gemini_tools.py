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

Надсилає кадри в Gemini через REST API (requests), а не через SDK.
Чому так:
1) Кілька пачок кадрів йдуть паралельно (ThreadPoolExecutor) - так швидше.
2) При тимчасових помилках (мережа, таймаут, 429, 5xx) запит повторюється
   з паузою, тому один збій інтернету не валить весь аналіз.

Ключ читається при кожному виклику через config.load_api_key(), тож
ключ можна змінити в програмі без перезапуску.
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

# Скільки кадрів у одній пачці (одному запиті до Gemini).
BATCH_SIZE = 40

# Щоб прогрес-бар рухався плавніше, останню (неповну) пачку ділимо на
# кілька менших. Так у короткому відео бар оновлюється не лише раз
# наприкінці. Менше 6 кадрів ділити немає сенсу.
MIN_TRICKLE_SUBBATCH_SIZE = 6
MAX_TRICKLE_SUBBATCHES = 4

# Скільки пачок летить у Gemini одночасно (для безкоштовного ключа).
# Темп запитів все одно тримає _RateGate, тож зайві потоки просто чекають.
# 6 - запас, щоб пара повільних запитів не блокувала чергу.
MAX_PARALLEL_BATCHES = 6

# Для платного ключа ліміти вищі, тож дозволяємо більше потоків.
PAID_TIER_MAX_PARALLEL_BATCHES = 16


def _worker_count_for_tier(api_tier, batch_count=None):
    """Скільки потоків можна запустити залежно від типу ключа.
    Якщо тип невідомий (None або сміття) - беремо free, так безпечніше.
    batch_count - не запускаємо більше потоків, ніж є пачок."""
    ceiling = PAID_TIER_MAX_PARALLEL_BATCHES if api_tier == "paid" else MAX_PARALLEL_BATCHES
    if batch_count is not None:
        ceiling = min(ceiling, batch_count)
    return max(1, ceiling)


# Одна спільна сесія для всіх запитів. Так з'єднання з сервером
# повторно використовуються, і не треба щоразу робити TLS-рукостискання.
# pool_maxsize рахуємо від платної стелі, бо сесія створюється один раз,
# ще до того, як відомо тип ключа.
_http_session = requests.Session()
_http_session.mount(
    "https://", requests.adapters.HTTPAdapter(pool_maxsize=max(PAID_TIER_MAX_PARALLEL_BATCHES, 4))
)

# Модель за замовчуванням. Насправді main.py передає модель явно.
DEFAULT_MODEL_NAME = "gemini-3.5-flash-lite"
DEFAULT_PROMPT_LANGUAGE = "ua"

API_URL_TEMPLATE = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
REQUEST_TIMEOUT_SEC = 120  # максимальний таймаут

# Таймаут запиту: беремо медіану успішних запитів і множимо на 6.
# Поки успішних запитів ще немає, ставимо 60 с.
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

# Скільки разів повторити пачку при тимчасовій помилці, перш ніж здатися.
MAX_RETRIES = 3

# Пауза перед повтором: 1.5 с, потім 3 с, потім 6 с.
RETRY_BACKOFF_BASE_SEC = 1.5

# Пауза при помилці 429 (забагато запитів). Ліміт рахується за хвилину,
# тому пауза довша: 20 с, 40 с, 80 с.
RATE_LIMIT_BACKOFF_BASE_SEC = 20.0

# ---- Спільний "гальмівник" запитів (клас _RateGate нижче) ----
#
# Проблема: кожен потік сам чекав після 429 і повторював запит у той самий
# момент, тому 429 приходили знову і знову. Тепер пауза одна на всіх, а
# її довжину підказує сам Gemini (retryDelay). Також після 429 темп
# підлаштовується під реальний ліміт.

# Вікно лімітів Gemini - одна хвилина.
RATE_WINDOW_SEC = 60.0

# Використовуємо 90% від ліміту, щоб не впиратися рівно в межу.
RATE_LIMIT_SAFETY = 0.9

# Скільки разів повторювати пачку після 429.
RATE_LIMIT_MAX_RETRIES = 5

# Максимальна спільна пауза після одного 429, секунди.
RATE_LIMIT_MAX_COOLDOWN_SEC = 120.0

# Додаємо 1 с до паузи, яку підказав Gemini.
RATE_LIMIT_COOLDOWN_MARGIN_SEC = 1.0

# Випадковий розкид часу пробудження потоків, щоб вони не стартували разом.
RATE_LIMIT_JITTER_SEC = 1.5

# Якщо Gemini не сказав, який у нас ліміт, тримаємо проміжок між запитами:
# спочатку 5 с, після кожного 429 більше (до 12 с), після успіху трохи менше.
UNKNOWN_LIMIT_GAP_SEC = 5.0
UNKNOWN_LIMIT_GAP_MAX_SEC = 12.0
GAP_RECOVERY_SEC = 0.3

# Скільки токенів іде на один кадр. За замовчуванням 1120 (перевірено на тесті).
# Далі число уточнюється з реальних відповідей Gemini.
DEFAULT_TOKENS_PER_FRAME = 1120
PROMPT_OVERHEAD_TOKENS = 600

# ---- Роздільність кадрів для Gemini (generationConfig.mediaResolution) ----
#
# Це головне, що впливає на швидкість на безкоштовному ключі. Ліміт
# Flash-Lite - 250K токенів за хвилину, а кожен кадр за замовчуванням
# коштує 1120 токенів. Тобто 2000 кадрів - це хвилин 8 чекання.
# Менша роздільність = менше токенів на кадр:
#   HIGH / None - 1120 токенів (за замовчуванням)
#   MEDIUM      - 560 токенів
#   LOW         - 280 токенів
# Мінус: дрібні об'єкти можуть гірше розпізнаватись. Міняти треба після
# порівняння на тому самому відео.
#
# Це не глобальна константа, а параметр, який іде від main.py по всьому
# ланцюжку викликів. Так можна мати різні значення для "Швидкий" і
# "Детально". DEFAULT_MEDIA_RESOLUTION - запасне значення, якщо його не передали.
DEFAULT_MEDIA_RESOLUTION = None
_TOKENS_PER_FRAME_BY_RESOLUTION = {
    None: 1120,
    "MEDIA_RESOLUTION_HIGH": 1120,
    "MEDIA_RESOLUTION_MEDIUM": 560,
    "MEDIA_RESOLUTION_LOW": 280,
}

# Які помилки варто повторити. Мережа, таймаут, 429 і 5xx зазвичай
# проходять самі. А от неправильний ключ або дивна відповідь - ні.
RETRYABLE_ERROR_KINDS = {"rate_limit", "server", "timeout", "network"}


@dataclass
class Match:
    """Один знайдений момент: номер кадру і короткий опис від Gemini."""
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

# Схема відповіді (JSON). Gemini за нею повертає список знайдених кадрів.
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


# Gemini 3 не приймає temperature/top_p/top_k, тому їх тут немає.
# Замість них є thinkingConfig.thinkingLevel. Важливо: це вкладений
# об'єкт thinkingConfig, а не плоске поле в generationConfig - інакше
# буде помилка 400.
#
# THINKING_LEVEL_BY_MODEL - запасне значення, якщо main.py не передав
# свій рівень. Для Flash-Lite підтримується "minimal". Для інших моделей
# (наприклад gemini-3.8-flash) "minimal" не підходить, там тільки
# low/medium/high.
THINKING_LEVEL_BY_MODEL = {
    "gemini-3.5-flash-lite": "minimal",
}
DEFAULT_THINKING_LEVEL = "low"


class MissingApiKeyError(Exception):
    """Ключа немає, а аналіз запустили."""


class GeminiApiError(Exception):
    """
    Помилка від Gemini або запит взагалі не дійшов до сервера.

    kind - короткий код причини. За ним main.py показує зрозумілий текст:
    - "network"      - нема інтернету.
    - "timeout"      - сервер не відповів вчасно.
    - "rate_limit"   - 429, забагато запитів.
    - "quota_daily"  - вичерпано добовий ліміт (до завтра не допоможе).
    - "invalid_key"  - 401/403, ключ не підходить.
    - "server"       - 5xx, проблема на боці Gemini.
    - "blocked"      - відповідь 200, але без результату (часто спрацював
                       фільтр безпеки).
    - "unknown"      - інше (наприклад, дивна структура відповіді).

    retry_after - скільки секунд чекати перед повтором (з заголовка
    Retry-After або з тіла відповіді). None, якщо не сказали.

    quota - що саме спрацювало: scope ("minute"/"day"), metric
    ("requests"/"tokens"), quota_id, limit. Або None.
    """

    def __init__(self, message, kind="unknown", retry_after=None, quota=None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.quota = quota


def check_api_key(api_key):
    """
    Перевіряє ключ простим запитом до Gemini (список моделей). Цей запит
    нічого не генерує і майже не витрачає квоту.

    Формат ключа не перевіряємо за виглядом, бо він може змінюватись.
    Єдиний надійний спосіб - спробувати ним щось зробити.

    Повертає:
    - "valid"          - ключ підходить (200).
    - "invalid"        - сервер відхилив ключ (400/401/403).
    - "network_error"  - не вдалось достукатись до сервера. Це не означає,
                          що ключ поганий.
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
    Спільний для всіх потоків "гальмівник" перед кожним запитом.

    1) Спільна пауза. Після 429 всі потоки чекають однаково. Довжину
       паузи беремо з відповіді Gemini, а якщо її нема - 20, 40, 80 с.
    2) Вивчений темп. Якщо Gemini сказав свій ліміт, не пускаємо більше
       80-90% від нього за хвилину. Тоді наступних 429 вже не буде.
       Якщо ліміту не сказали - тримаємо проміжок між запитами і
       змінюємо його: після 429 більше, після успіху менше.
    3) Добова квота. Якщо вона вичерпана, решта пачок одразу падають,
       без зайвих запитів.
    Стан спільний для всього процесу (по назві моделі), бо ліміти діють
    на весь ключ.
    """

    def __init__(self, model_name):
        self.model_name = model_name
        self._lock = threading.Lock()
        self._cooldown_until = 0.0
        self._starts = deque()          # (час старту, оцінка токенів) за останню хвилину
        self._max_requests = None       # вивчений ліміт запитів за хвилину
        self._max_tokens = None         # вивчений ліміт токенів за хвилину
        self._safety = RATE_LIMIT_SAFETY        # запас для запитів
        self._token_safety = 1.0                # запас для токенів
        self._min_gap = 0.0             # проміжок між стартами, якщо ліміт невідомий
        self._next_slot = 0.0
        self._fatal = None
        self._detail_logged = False

    def reset_for_run(self):
        """На початку кожного аналізу скидаємо фатальну помилку з
        попереднього запуску: квота могла оновитись або ключ змінили."""
        with self._lock:
            self._fatal = None
            self._detail_logged = False

    def estimate_tokens(self, frame_count, media_resolution=None):
        """Оцінює, скільки токенів піде в запиті. Залежить від роздільності,
        яку передали саме для цієї пачки."""
        tokens_per_frame = _TOKENS_PER_FRAME_BY_RESOLUTION.get(media_resolution, DEFAULT_TOKENS_PER_FRAME)
        return int(frame_count * tokens_per_frame) + PROMPT_OVERHEAD_TOKENS

    def set_fatal(self, error):
        with self._lock:
            self._fatal = error

    def _purge(self, now):
        # Викидаємо старші за хвилину записи.
        while self._starts and now - self._starts[0][0] >= RATE_WINDOW_SEC:
            self._starts.popleft()

    def _window_wait(self, now, est_tokens):
        """Скільки ще чекати, щоб не перевищити ліміт за останню хвилину.
        0 - можна відправляти."""
        wait = 0.0
        starts = self._starts
        if self._max_requests is not None:
            limit = max(1, int(self._max_requests * self._safety))
            if len(starts) >= limit:
                # Чекаємо, поки з вікна вийдуть найстаріші записи.
                wait = max(wait, starts[len(starts) - limit][0] + RATE_WINDOW_SEC - now)
        if self._max_tokens is not None and starts:
            # Gemini пропускає запит, поки сума за хвилину ще менша за ліміт.
            # Тому не вимагаємо, щоб новий запит повністю вмістився. Якщо
            # 429 все ж прийде, _token_safety сам зменшиться.
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
        """Блокує потік, поки можна відправляти запит, і записує старт.
        Кидає quota_daily, якщо добова квота вже вичерпана."""
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
        """Після успішного запиту трохи зменшуємо проміжок між запитами.
        Аргументи поки не використовуються, залишені на майбутнє."""
        with self._lock:
            if self._min_gap > 0.0:
                self._min_gap = max(0.0, self._min_gap - GAP_RECOVERY_SEC)

    def report_rate_limit(self, error, failure_index, batch_id):
        """Викликається, коли прийшло 429 (не добовий ліміт). Ставимо спільну
        паузу і, якщо це перша така відмова, підлаштовуємо темп."""
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
                    # Ліміт вже знали, а 429 все одно прийшов - зменшуємо запас.
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
    # Один гальмівник на кожну модель.
    with _gates_lock:
        gate = _gates.get(model_name)
        if gate is None:
            gate = _RateGate(model_name)
            _gates[model_name] = gate
        return gate


def _parse_rate_limit_details(response):
    """
    Читає тіло відповіді 429 від Gemini. Повертає (скільки чекати або None,
    словник quota). Якщо формат інший, нічого не падає - просто нічого
    не дізнаємось.
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
                    # Добовий ліміт важливіший за хвилинний.
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
    """Список знайдених моментів + лічильники втрат. Поводиться як звичайний
    list, тож старий код нічого не помічає. main.py читає failed_batches,
    щоб попередити про неповний результат."""
    failed_batches = 0
    total_batches = 0
    failed_frames = 0
    total_frames = 0


# Помилки, після яких пачку можна пропустити, а решту результатів зберегти.
_SKIPPABLE_ERROR_KINDS = {"timeout", "network", "server", "rate_limit"}

# Помилки, які зазвичай через сам вміст кадрів (фільтр безпеки або дивна
# відповідь). Для них пачку ділимо навпіл і пробуємо ще раз.
_SPLIT_ON_ERROR_KINDS = {"blocked", "unknown"}


def _analyze_batch_resilient(
    api_key, batch, index_offset, user_prompt, model_name, language, thinking_level=None, allow_split=True,
    media_resolution=None,
):
    """
    Обгортка над _analyze_batch(). Одна погана пачка не забирає з собою
    результати решти кадрів.

    Якщо пачка впала з blocked/unknown, пробуємо розбити її навпіл (один
    раз, далі не ділимо). Інші помилки пропускаємо далі без змін.

    Повертає (matches, lost_frame_count, last_error):
      matches          - знайдені моменти (може бути неповним).
      lost_frame_count - скільки кадрів не вдалось проаналізувати.
      last_error       - помилка, яка спричинила втрату (або None).
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
    Ділить кадри на пачки по BATCH_SIZE і шле їх у Gemini паралельно.
    Порядок результатів не важливий, бо main.py сортує їх за часом.

    thinking_level - рівень "мислення" для всіх пачок цього аналізу.
    None - беремо запасне значення для моделі.

    media_resolution - роздільність кадрів (див. коментар вище). Теж
    фіксується один раз на весь аналіз.

    api_tier - "free" або "paid". Впливає лише на кількість потоків.

    progress_callback(done_batches, total_batches, done_frames, total_frames)
    викликається після кожної готової пачки. Прогрес-бар рахуємо за
    кадрами, а не за пачками, бо пачки різного розміру.

    is_cancelled - функція без аргументів. Якщо вона повертає True, пачки,
    які ще не почались, не запускаються. Вже відправлений запит зупинити
    не можна.
    """
    api_key = _get_api_key()
    _get_gate(model_name).reset_for_run()
    with _success_durations_lock:
        _success_durations.clear()  # таймаут рахуємо заново для кожного запуску

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
        # Значення словника - розмір пачки в кадрах. Потрібно, щоб після
        # готовності додати правильну кількість до done_frames.
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
                # Пачка не відповіла навіть після повторів. Решту не губимо.
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
            if progress_callback is not None:
                progress_callback(done_batches, total_batches, done_frames, total_frames)

    all_matches.total_batches = total_batches
    all_matches.total_frames = total_frames
    # Якщо не вдалось жодного кадру - це повна помилка, а не часткова.
    if first_skipped_error is not None and all_matches.failed_frames >= total_frames:
        raise first_skipped_error
    return all_matches


# Сигнал "кадрів більше не буде". Окремий унікальний об'єкт, щоб його
# не можна було сплутати з кадром або помилкою.
STREAM_END = object()


def _split_trailing_batch(frames):
    """Ділить неповну останню пачку на кілька менших, щоб прогрес-бар
    оновлювався частіше. На результат аналізу це не впливає."""
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
    Те саме, що find_object_in_frames(), але кадри приходять через чергу.
    Пачки летять у Gemini одразу, як набирається BATCH_SIZE кадрів, ще
    поки відео нарізається. Так нарізка і запити йдуть паралельно.

    frame_queue - черга (queue.Queue). Спочатку в ній кадри (словники),
    потім РІВНО один фінальний елемент: або STREAM_END (нарізку завершено),
    або виняток (нарізка впала).

    max_frames_hint - приблизна кількість кадрів, тільки для прогрес-бару.

    started_callback(frame_count) викликається, коли пачка відправлена
    (ще до відповіді). Потрібен, щоб прогрес-бар міг трохи рухатись далі,
    поки ми чекаємо.
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
        pending_futures = {}  # future -> кількість кадрів у цій пачці

        while not stream_ended or pending_batch or pending_futures:
            if is_cancelled is not None and is_cancelled():
                for future in pending_futures:
                    future.cancel()
                break

            # 1) Читаємо з черги, поки не набереться повна пачка.
            #    Короткий timeout, щоб цикл міг перевіряти скасування.
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
                # Повна пачка - відправляємо як є. Неповну останню (коли
                # нарізка вже закінчилась) ділимо на менші.
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

            # 2) Забираємо готові пачки, не чекаючи довго.
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
        # Помилка сталась у нарізці, а не в Gemini. Прокидаємо далі як є.
        raise extraction_error

    all_matches.total_batches = done_batches
    all_matches.total_frames = done_frames
    if first_skipped_error is not None and all_matches.failed_frames >= done_frames and done_frames > 0:
        raise first_skipped_error
    return all_matches


def _analyze_batch(api_key, batch, index_offset, user_prompt, model_name, language, thinking_level=None,
                    media_resolution=None):
    """
    Аналізує одну пачку кадрів: один REST-запит до Gemini.

    Gemini нумерує кадри з нуля всередині пачки. Тому тут же переводимо
    номери назад у загальні (додаємо index_offset). Так модель не
    плутається з великими числами.

    Якщо запит впав з тимчасовою помилкою, повторюємо його до MAX_RETRIES
    разів з паузою, яка росте.
    """
    system_prompt = SYSTEM_PROMPTS.get(language, SYSTEM_PROMPTS[DEFAULT_PROMPT_LANGUAGE])

    parts = [{"text": system_prompt.format(user_prompt=user_prompt)}]

    for i, frame in enumerate(batch):
        # Номер кадру в пачці (з нуля), а не загальний номер.
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
    transient_failures = 0     # таймаут / мережа / 5xx
    rate_limit_failures = 0    # 429
    while True:
        # Чекаємо, якщо триває спільна пауза або ліміт уже вичерпано.
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
            # Пауза: 1.5 с, 3 с, 6 с, з невеликим випадковим розкидом,
            # щоб два потоки не повторили запит в один момент.
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
    Переводить номери кадрів із пачки (0..batch_size-1) у загальні номери.

    Якщо модель повернула номер поза межами пачки, такий результат
    відкидаємо. Краще втратити один збіг, ніж записати його на чужий кадр.
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
    Один HTTP-запит до Gemini і розбір відповіді. Окремою функцією, щоб
    цикл повторів у _analyze_batch міг викликати її знову.
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
            # Якщо сервер сам сказав, скільки чекати (Retry-After), довіряємо
            # йому. Це ціле число секунд.
            raw_retry_after = response.headers.get("Retry-After")
            if raw_retry_after is not None:
                try:
                    retry_after = float(raw_retry_after)
                except ValueError:
                    retry_after = None
            # У тілі 429 є точна пауза і назва ліміту.
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

    # Буває відповідь 200, але без результату. Найчастіше це фільтр безпеки.
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
