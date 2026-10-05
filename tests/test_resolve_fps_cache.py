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
Тести на кеп розміру _resolve_fps_cache (video_tools.py).

Мокаємо _resolve_fps_cache_key() (щоб не залежати від реальних файлів
на диску) і _resolve_fps_uncached() (щоб не відкривати відео) -
перевіряємо лише саму логіку кешу: наповнення, влучання в кеш без
повторного виклику "важкої" функції, і очищення після досягнення
ліміту.

Перед і після кожного тесту кеш явно очищується, щоб тести не впливали
один на одного (модульний рівень - спільний стан на весь тестовий
прогін).
"""
from unittest.mock import patch

import pytest

import video_tools


@pytest.fixture(autouse=True)
def _clean_cache():
    video_tools._resolve_fps_cache.clear()
    yield
    video_tools._resolve_fps_cache.clear()


def _fake_key(video_path):
    """Замінник _resolve_fps_cache_key(): у тестах немає реальних
    файлів на диску, тож просто використовуємо сам шлях як ключ."""
    return video_path


class TestCacheHitAvoidsRecompute:
    def test_second_call_with_same_path_uses_cache(self):
        with patch("video_tools._resolve_fps_cache_key", side_effect=_fake_key), \
             patch("video_tools._resolve_fps_uncached", return_value=(30.0, 12.0, 360)) as mocked:
            first = video_tools._resolve_fps(video=None, video_path="video_a.mp4")
            second = video_tools._resolve_fps(video=None, video_path="video_a.mp4")

        assert first == second == (30.0, 12.0, 360)
        mocked.assert_called_once()  # другий виклик узяв результат з кешу


class TestCacheSizeCap:
    def test_cache_is_cleared_once_limit_is_reached(self):
        """Заповнюємо кеш РІВНО до ліміту фейковими записами напряму
        (без виклику _resolve_fps), тоді один НОВИЙ виклик має
        спричинити очищення кешу перед додаванням нового запису -
        тобто після нього в кеші лишається РІВНО один запис (щойно
        доданий), а не ліміт+1."""
        limit = video_tools._RESOLVE_FPS_CACHE_MAX_SIZE
        for i in range(limit):
            video_tools._resolve_fps_cache[f"video_{i}.mp4"] = (25.0, 10.0, 250)
        assert len(video_tools._resolve_fps_cache) == limit

        with patch("video_tools._resolve_fps_cache_key", side_effect=_fake_key), \
             patch("video_tools._resolve_fps_uncached", return_value=(60.0, 5.0, 300)):
            video_tools._resolve_fps(video=None, video_path="new_video.mp4")

        assert len(video_tools._resolve_fps_cache) == 1
        assert video_tools._resolve_fps_cache["new_video.mp4"] == (60.0, 5.0, 300)

    def test_cache_never_grows_past_limit_across_many_calls(self):
        limit = video_tools._RESOLVE_FPS_CACHE_MAX_SIZE

        with patch("video_tools._resolve_fps_cache_key", side_effect=_fake_key), \
             patch("video_tools._resolve_fps_uncached", return_value=(24.0, 8.0, 192)):
            for i in range(limit * 3):
                video_tools._resolve_fps(video=None, video_path=f"video_{i}.mp4")
                assert len(video_tools._resolve_fps_cache) <= limit

    def test_cache_miss_key_none_is_never_stored(self):
        """Якщо файл зник/недоступний, _resolve_fps_cache_key()
        повертає None (див. video_tools.py) - такий виклик не повинен
        нічого класти в кеш і не повинен падати."""
        with patch("video_tools._resolve_fps_cache_key", return_value=None), \
             patch("video_tools._resolve_fps_uncached", return_value=(30.0, 1.0, 30)) as mocked:
            video_tools._resolve_fps(video=None, video_path="missing.mp4")
            video_tools._resolve_fps(video=None, video_path="missing.mp4")

        assert video_tools._resolve_fps_cache == {}
        assert mocked.call_count == 2  # кожен раз рахує заново, кешувати нема що

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
