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
Тести на нову логіку media_resolution (п.2 з плану):
- _RateGate.estimate_tokens() правильно рахує токени/кадр за кожен
  рівень roздільності (і дефолтне значення для None/невідомого рядка);
- _analyze_batch() кладе mediaResolution у payload, ЛИШЕ якщо
  media_resolution передано (None - поле взагалі не додається, як і
  раніше - "поведінка як була до цієї опції").

Другий пункт мокає _send_batch_request і gate.acquire (не робимо
реальних HTTP-запитів), щоб перевірити САМЕ payload, який пішов би
до Gemini.
"""
from unittest.mock import patch

import pytest

from gemini_tools import Match, _RateGate, _analyze_batch


class TestEstimateTokens:
    @pytest.mark.parametrize("media_resolution,expected_tokens_per_frame", [
        (None, 1120),
        ("MEDIA_RESOLUTION_HIGH", 1120),
        ("MEDIA_RESOLUTION_MEDIUM", 560),
        ("MEDIA_RESOLUTION_LOW", 280),
    ])
    def test_known_resolutions_use_documented_token_cost(self, media_resolution, expected_tokens_per_frame):
        gate = _RateGate("gemini-3.5-flash-lite")
        frame_count = 40
        expected = frame_count * expected_tokens_per_frame + 600  # + PROMPT_OVERHEAD_TOKENS
        assert gate.estimate_tokens(frame_count, media_resolution) == expected

    def test_unknown_resolution_string_falls_back_to_default(self):
        gate = _RateGate("gemini-3.5-flash-lite")
        # Про всяк випадок: неочікуваний рядок не повинен падати -
        # має тихо відкотитись на DEFAULT_TOKENS_PER_FRAME (1120), як і None.
        assert gate.estimate_tokens(10, "SOME_UNEXPECTED_VALUE") == gate.estimate_tokens(10, None)

    def test_lower_resolution_is_cheaper_than_higher(self):
        gate = _RateGate("gemini-3.5-flash-lite")
        low = gate.estimate_tokens(100, "MEDIA_RESOLUTION_LOW")
        medium = gate.estimate_tokens(100, "MEDIA_RESOLUTION_MEDIUM")
        high = gate.estimate_tokens(100, "MEDIA_RESOLUTION_HIGH")
        assert low < medium < high

    def test_gate_is_not_locked_to_one_resolution_across_calls(self):
        """Раніше роздільність фіксувалась ОДИН раз у __init__ шлюзу
        (спільного на весь процес по моделі) - тепер це параметр
        КОЖНОГО виклику, тож той самий шлюз має правильно порахувати
        різні виклики з різною роздільністю (наприклад, "Швидкий" і
        "Детально" аналізи одна за одною на тій самій моделі)."""
        gate = _RateGate("gemini-3.5-flash-lite")
        first = gate.estimate_tokens(50, "MEDIA_RESOLUTION_LOW")
        second = gate.estimate_tokens(50, "MEDIA_RESOLUTION_HIGH")
        assert first != second
        assert first == 50 * 280 + 600
        assert second == 50 * 1120 + 600


class TestPayloadMediaResolution:
    @staticmethod
    def _fake_frame():
        return {"jpg_bytes": b"fake-jpeg-bytes"}

    def _call_analyze_batch(self, media_resolution):
        """Викликає _analyze_batch з мокнутими мережею/шлюзом і повертає
        payload, з яким МІГ БИ піти реальний запит (аргумент
        _send_batch_request)."""
        captured_payload = {}

        def fake_send_batch_request(url, api_key, payload):
            captured_payload.update(payload)
            return [Match(frame_number=0, description="ок")], 1000

        with patch("gemini_tools._send_batch_request", side_effect=fake_send_batch_request), \
             patch("gemini_tools._get_gate") as mocked_get_gate:
            mocked_gate = mocked_get_gate.return_value
            mocked_gate.estimate_tokens.return_value = 1000
            mocked_gate.acquire.return_value = None

            _analyze_batch(
                "fake-key", [self._fake_frame()], 0, "людина у дворі",
                "gemini-3.5-flash-lite", "ua", thinking_level="low",
                media_resolution=media_resolution,
            )

        return captured_payload

    def test_media_resolution_is_included_when_set(self):
        payload = self._call_analyze_batch(media_resolution="MEDIA_RESOLUTION_LOW")
        assert payload["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_LOW"

    def test_media_resolution_field_is_absent_when_none(self):
        payload = self._call_analyze_batch(media_resolution=None)
        assert "mediaResolution" not in payload["generationConfig"]

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
