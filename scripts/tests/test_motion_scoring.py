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
Тести на скоринг руху відносно медіани фону (video_tools.py).

Чиста математика на СИНТЕТИЧНИХ numpy-кадрах, без реального відео і
без cv2.VideoCapture:
- _motion_percent() - базова метрика % змінених пікселів;
- _has_motion() - бінарний поріг поверх неї;
- відтворення формули відбору кандидата з extract_frames()
  (score = motion_percent - median(baseline), перемагає найбільший
  score) - на послідовності "статичне море шуму" + один кадр з явним
  об'єктом, переконуємось, що перемагає саме кадр з об'єктом, а не
  випадковий шумовий кадр.
"""
import statistics

import numpy as np

from video_tools import MOTION_THRESHOLD_PERCENT, _has_motion, _motion_percent

FRAME_H, FRAME_W = 120, 160


def _noisy_static_frame(seed, amplitude=4, base=100):
    """Кадр "статичного моря" - слабкий випадковий шум навколо base,
    без жодного справжнього об'єкта в кадрі."""
    rng = np.random.default_rng(seed)
    noise = rng.integers(-amplitude, amplitude + 1, size=(FRAME_H, FRAME_W))
    frame = np.clip(base + noise, 0, 255).astype(np.uint8)
    return frame


def _frame_with_object(seed, amplitude=4, base=100):
    """Той самий шумовий фон + явний прямокутний об'єкт (яскравий блок)
    в частині кадру - помітна, непропорційна решті кадру зміна."""
    frame = _noisy_static_frame(seed, amplitude=amplitude, base=base).copy()
    frame[30:90, 50:110] = 240  # яскравий прямокутник - "об'єкт"
    return frame


class TestMotionPercent:
    def test_identical_frames_have_zero_motion(self):
        frame = _noisy_static_frame(seed=1)
        assert _motion_percent(frame, frame) == 0.0

    def test_completely_different_frames_have_high_motion(self):
        black = np.zeros((FRAME_H, FRAME_W), dtype=np.uint8)
        white = np.full((FRAME_H, FRAME_W), 255, dtype=np.uint8)
        assert _motion_percent(black, white) == 100.0

    def test_result_is_within_0_100_range(self):
        a = _noisy_static_frame(seed=1)
        b = _noisy_static_frame(seed=2)
        result = _motion_percent(a, b)
        assert 0.0 <= result <= 100.0

    def test_object_frame_has_more_motion_than_static_noise_pair(self):
        background_a = _noisy_static_frame(seed=1)
        background_b = _noisy_static_frame(seed=2)
        object_frame = _frame_with_object(seed=3)

        static_motion = _motion_percent(background_a, background_b)
        object_motion = _motion_percent(background_a, object_frame)

        assert object_motion > static_motion


class TestHasMotion:
    def test_below_threshold_is_no_motion(self):
        frame = _noisy_static_frame(seed=1)
        assert _has_motion(frame, frame, threshold_percent=MOTION_THRESHOLD_PERCENT) is False

    def test_object_appearing_crosses_default_threshold(self):
        background = _noisy_static_frame(seed=1)
        object_frame = _frame_with_object(seed=1)  # той самий фон + об'єкт
        assert _has_motion(background, object_frame) is True


class TestMedianBaselineCandidateSelection:
    """Відтворює формулу відбору кращого кадру в сегменті з
    extract_frames() (video_tools.py): score = motion_percent -
    median(baseline руху попередніх кандидатів), обирається кадр з
    найбільшим score. Тут перевіряємо саму формулу, а не всю функцію
    нарізки відео."""

    @staticmethod
    def _pick_best_candidate(frames):
        """frames - список сірих numpy-кадрів по порядку. Повертає індекс
        кадру з найвищим score - той самий алгоритм, що й у
        extract_frames() (порівняння з ПОПЕРЕДНІМ кандидатом + медіана
        баунду руху)."""
        prev_gray = None
        motion_baseline = []
        best_index = None
        best_score = None

        for i, gray in enumerate(frames):
            motion_percent = 0.0 if prev_gray is None else _motion_percent(prev_gray, gray)
            prev_gray = gray

            baseline = statistics.median(motion_baseline) if motion_baseline else 0.0
            score = motion_percent - baseline
            motion_baseline.append(motion_percent)

            if best_score is None or score > best_score:
                best_score = score
                best_index = i

        return best_index

    def test_object_frame_wins_over_purely_static_sequence(self):
        # "Статичне море" з випадковим слабким шумом - 6 кадрів,
        # об'єкт з'являється рівно на одному, явно виділяючись.
        frames = [_noisy_static_frame(seed=s) for s in range(6)]
        object_index = 3
        frames[object_index] = _frame_with_object(seed=object_index)

        winner = self._pick_best_candidate(frames)

        assert winner == object_index

    def test_all_static_frames_still_pick_something_without_crashing(self):
        # Без жодного об'єкта - формула не повинна падати (перший кадр
        # score=0 через prev_gray=None, це коректний базовий випадок).
        frames = [_noisy_static_frame(seed=s) for s in range(4)]
        winner = self._pick_best_candidate(frames)
        assert winner is not None
        assert 0 <= winner < len(frames)

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
