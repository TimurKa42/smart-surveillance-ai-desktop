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
Тести на gemini_tools._analyze_batch_resilient().

Усюди мокаємо gemini_tools._analyze_batch (не робимо реальних HTTP-
запитів і не потребуємо API-ключа) і перевіряємо саму логіку обгортки:
- звичайний успіх без жодного розбиття;
- розбиття навпіл при "blocked"/"unknown" на ПОВНОМУ батчі;
- що розбиття справді відбувається аргументами batch[:mid]/batch[mid:];
- що при повторному падінні ОДНІЄЇ половини втрачається лише вона,
  а не весь батч;
- що rate_limit/server НЕ призводять до розбиття - помилка
  прокидається нагору як є (рішення "пропустити чи ні" - у викликача).
"""
from unittest.mock import patch

import pytest

import gemini_tools
from gemini_tools import GeminiApiError, Match, _analyze_batch_resilient


COMMON_ARGS = dict(
    api_key="fake-key",
    user_prompt="людина у дворі",
    model_name="gemini-3.5-flash-lite",
    language="ua",
    thinking_level="low",
    media_resolution="MEDIA_RESOLUTION_LOW",
)


def _call(batch, index_offset=0, allow_split=True, media_resolution=None):
    return _analyze_batch_resilient(
        COMMON_ARGS["api_key"], batch, index_offset,
        COMMON_ARGS["user_prompt"], COMMON_ARGS["model_name"], COMMON_ARGS["language"],
        COMMON_ARGS["thinking_level"], allow_split=allow_split, media_resolution=media_resolution,
    )


class TestSuccessFirstTry:
    def test_returns_matches_and_no_loss(self):
        batch = ["frame0", "frame1", "frame2"]
        expected_matches = [Match(frame_number=1, description="людина біля паркану")]

        with patch("gemini_tools._analyze_batch", return_value=expected_matches) as mocked:
            matches, lost, error = _call(batch, index_offset=10, media_resolution=COMMON_ARGS["media_resolution"])

        mocked.assert_called_once_with(
            COMMON_ARGS["api_key"], batch, 10, COMMON_ARGS["user_prompt"],
            COMMON_ARGS["model_name"], COMMON_ARGS["language"], COMMON_ARGS["thinking_level"],
            media_resolution=COMMON_ARGS["media_resolution"],
        )
        assert matches == expected_matches
        assert lost == 0
        assert error is None


class TestSplitOnBlocked:
    def test_full_batch_blocked_splits_in_half_and_recovers(self):
        batch = ["f0", "f1", "f2", "f3"]
        left_matches = [Match(frame_number=0, description="ліва половина")]
        right_matches = [Match(frame_number=2, description="права половина")]

        def side_effect(api_key, sub_batch, index_offset, *args, **kwargs):
            if len(sub_batch) == 4:
                raise GeminiApiError("safety filter", kind="blocked")
            if index_offset == 0:
                return left_matches
            if index_offset == 2:
                return right_matches
            raise AssertionError(f"неочікуваний виклик з index_offset={index_offset}")

        with patch("gemini_tools._analyze_batch", side_effect=side_effect) as mocked:
            matches, lost, error = _call(batch, index_offset=0, media_resolution=COMMON_ARGS["media_resolution"])

        assert lost == 0
        assert error is None
        assert matches == left_matches + right_matches

        # Перевіряємо, що розбиття справді сталось саме на batch[:mid]/batch[mid:]
        assert mocked.call_count == 3
        call_batches_and_offsets = [(call.args[1], call.args[2]) for call in mocked.call_args_list]
        assert call_batches_and_offsets[0] == (batch, 0)               # перша спроба - повний батч
        assert call_batches_and_offsets[1] == (batch[:2], 0)           # ліва половина
        assert call_batches_and_offsets[2] == (batch[2:], 2)           # права половина, offset зсунуто

        # media_resolution має дійти НЕЗМІННИМ до обох половин після розбиття
        for call in mocked.call_args_list:
            assert call.kwargs.get("media_resolution") == COMMON_ARGS["media_resolution"]

    def test_unknown_kind_also_triggers_split(self):
        batch = ["f0", "f1"]

        def side_effect(api_key, sub_batch, index_offset, *args, **kwargs):
            if len(sub_batch) == 2:
                raise GeminiApiError("зіпсована відповідь", kind="unknown")
            return [Match(frame_number=0, description=f"половина offset={index_offset}")]

        with patch("gemini_tools._analyze_batch", side_effect=side_effect):
            matches, lost, error = _call(batch, index_offset=0)

        assert lost == 0
        assert len(matches) == 2

    def test_one_half_fails_again_loses_only_that_half(self):
        """Права половина після розбиття падає знову (allow_split=False для
        половин - подальшого дроблення нема) - втрачаємо лише її кадри,
        ліва половина лишається в результаті."""
        batch = ["f0", "f1", "f2", "f3"]
        left_matches = [Match(frame_number=0, description="знайдено зліва")]

        def side_effect(api_key, sub_batch, index_offset, *args, **kwargs):
            if len(sub_batch) == 4:
                raise GeminiApiError("safety filter", kind="blocked")
            if index_offset == 0:
                return left_matches
            if index_offset == 2:
                raise GeminiApiError("знову заблоковано", kind="blocked")
            raise AssertionError(f"неочікуваний виклик з index_offset={index_offset}")

        with patch("gemini_tools._analyze_batch", side_effect=side_effect) as mocked:
            matches, lost, error = _call(batch, index_offset=0)

        assert matches == left_matches           # лише ліва половина вціліла
        assert lost == 2                          # права половина (2 кадри) втрачена
        assert error is not None
        assert error.kind == "blocked"
        # Половини викликались з allow_split=False - подальшого розбиття не було,
        # тобто рівно 3 виклики _analyze_batch (повний + дві половини), а не більше.
        assert mocked.call_count == 3

    def test_single_frame_batch_cannot_split_even_if_allowed(self):
        """len(batch) <= 1 - фізично нема що ділити навпіл, батч просто
        втрачається цілком, без AssertionError і без нескінченної рекурсії."""
        batch = ["only_frame"]

        with patch(
            "gemini_tools._analyze_batch",
            side_effect=GeminiApiError("safety filter", kind="blocked"),
        ) as mocked:
            matches, lost, error = _call(batch, index_offset=5, allow_split=True)

        assert matches == []
        assert lost == 1
        assert error is not None
        assert error.kind == "blocked"
        mocked.assert_called_once()

    def test_allow_split_false_loses_whole_batch_without_recursing(self):
        batch = ["f0", "f1", "f2", "f3"]

        with patch(
            "gemini_tools._analyze_batch",
            side_effect=GeminiApiError("safety filter", kind="blocked"),
        ) as mocked:
            matches, lost, error = _call(batch, index_offset=0, allow_split=False)

        assert matches == []
        assert lost == len(batch)
        assert error.kind == "blocked"
        mocked.assert_called_once()  # жодної рекурсії - allow_split=False


class TestNoSplitOnTransientErrors:
    @pytest.mark.parametrize("kind", ["rate_limit", "server", "timeout", "network"])
    def test_transient_errors_propagate_without_splitting(self, kind):
        batch = ["f0", "f1", "f2", "f3"]

        with patch(
            "gemini_tools._analyze_batch",
            side_effect=GeminiApiError("тимчасова проблема", kind=kind),
        ) as mocked:
            with pytest.raises(GeminiApiError) as exc_info:
                _call(batch, index_offset=0)

        assert exc_info.value.kind == kind
        mocked.assert_called_once()  # жодного розбиття - помилка одразу нагору

    def test_invalid_key_propagates_without_splitting(self):
        batch = ["f0", "f1"]
        with patch(
            "gemini_tools._analyze_batch",
            side_effect=GeminiApiError("bad key", kind="invalid_key"),
        ) as mocked:
            with pytest.raises(GeminiApiError):
                _call(batch, index_offset=0)
        mocked.assert_called_once()

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
