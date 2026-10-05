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
Регресійний тест на баг у _RateGate.note_success(): AttributeError
'_RateGate' object has no attribute '_tokens_per_frame' на першому ж
успішному батчі (self._tokens_per_frame ніколи не ініціалізувався в
__init__, а сам EMA-розрахунок вже й не читався ніде - див. коментар
у note_success()). Падало прямо в бойовому прогоні (66-хвилинне
відео), щойно приходила перша успішна відповідь від Gemini.

Тест просто викликає note_success() з реалістичними аргументами (як
викликає _analyze_batch() після успіху) і переконується, що це не
кидає AttributeError - тобто регресія такого штибу більше не пройде
непоміченою.
"""
from gemini_tools import _RateGate


class TestNoteSuccessDoesNotCrash:
    def test_note_success_with_tokens_and_frames(self):
        gate = _RateGate("gemini-3.5-flash-lite")
        # Раніше падало саме тут - AttributeError на self._tokens_per_frame.
        gate.note_success(frame_count=40, prompt_tokens=12000)

    def test_note_success_called_multiple_times_in_a_row(self):
        """Кілька батчів поспіль (типовий прогін) - теж не повинно падати,
        і _min_gap не повинен піти в мінус нижче нуля."""
        gate = _RateGate("gemini-3.5-flash-lite")
        gate._min_gap = 0.05
        for _ in range(5):
            gate.note_success(frame_count=40, prompt_tokens=12000)
        assert gate._min_gap >= 0.0

    def test_note_success_with_zero_or_missing_values(self):
        """Крайні випадки (0 кадрів, 0/None токенів) теж не повинні падати."""
        gate = _RateGate("gemini-3.5-flash-lite")
        gate.note_success(frame_count=0, prompt_tokens=0)
        gate.note_success(frame_count=40, prompt_tokens=None)

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
