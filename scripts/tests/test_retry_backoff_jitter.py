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
Тест на джиттер у retry backoff _analyze_batch() (gemini_tools.py).

sleep_sec = RETRY_BACKOFF_BASE_SEC * (2 ** transient_failures) * random.uniform(1.0, 1.3)

Мокаємо _send_batch_request (кидає GeminiApiError(kind="server") N
разів, потім повертає успіх), _get_gate (щоб не чіпати реальний
rate-ліміт шлюз) і time.sleep (щоб тест не чекав реальні секунди).
random.uniform мокаємо на фіксоване значення - без цього тест був би
недетермінованим (сам факт джиттера і перевіряємо).
"""
from unittest.mock import call, patch

from gemini_tools import GeminiApiError, Match, _analyze_batch


def _fake_frame():
    return {"jpg_bytes": b"fake-jpeg-bytes"}


class TestRetryBackoffJitter:
    def test_sleep_uses_uniform_jitter_multiplier(self):
        call_count = {"n": 0}

        def fake_send_batch_request(url, api_key, payload):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                raise GeminiApiError("тимчасова проблема", kind="server")
            return [Match(frame_number=0, description="ок")], 1000

        with patch("gemini_tools._send_batch_request", side_effect=fake_send_batch_request), \
             patch("gemini_tools._get_gate") as mocked_get_gate, \
             patch("gemini_tools.time.sleep") as mocked_sleep, \
             patch("gemini_tools.random.uniform", return_value=1.2) as mocked_uniform:
            mocked_gate = mocked_get_gate.return_value
            mocked_gate.estimate_tokens.return_value = 1000
            mocked_gate.acquire.return_value = None

            _analyze_batch(
                "fake-key", [_fake_frame()], 0, "людина у дворі",
                "gemini-3.5-flash-lite", "ua", thinking_level="low",
            )

        # RETRY_BACKOFF_BASE_SEC=1.5, jitter=1.2 (мокнутий) -> 1.5*1*1.2, 1.5*2*1.2
        expected_sleeps = [1.5 * (2 ** 0) * 1.2, 1.5 * (2 ** 1) * 1.2]
        actual_sleeps = [call.args[0] for call in mocked_sleep.call_args_list]

        assert actual_sleeps == expected_sleeps
        assert mocked_uniform.call_args_list == [call(1.0, 1.3), call(1.0, 1.3)]
        assert call_count["n"] == 3  # 2 падіння + 1 успіх

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
