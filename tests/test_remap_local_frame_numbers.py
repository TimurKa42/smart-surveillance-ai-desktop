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
Тести на gemini_tools._remap_local_frame_numbers().

Перевіряємо два випадки:
1) валідний локальний номер кадру (0..batch_size-1) коректно
   зсувається на index_offset;
2) номер ПОЗА межами пачки відкидається (а не "летить" на випадковий,
   помилковий кадр десь-інде у відео) - див. коментар у самій функції.
"""
from gemini_tools import Match, _remap_local_frame_numbers


def test_valid_local_numbers_are_shifted_by_offset():
    matches = [
        Match(frame_number=0, description="перший кадр пачки"),
        Match(frame_number=5, description="кадр посередині пачки"),
        Match(frame_number=9, description="останній кадр пачки"),
    ]

    result = _remap_local_frame_numbers(matches, index_offset=100, batch_size=10)

    assert [m.frame_number for m in result] == [100, 105, 109]
    assert len(result) == 3


def test_out_of_range_match_is_dropped_not_misplaced():
    matches = [
        Match(frame_number=3, description="валідний"),
        Match(frame_number=10, description="рівно на межі - вже поза пачкою"),
        Match(frame_number=-1, description="від'ємний - теж поза пачкою"),
        Match(frame_number=999, description="явно за межами"),
    ]

    result = _remap_local_frame_numbers(matches, index_offset=50, batch_size=10)

    # лишається лише один валідний матч, коректно зсунутий
    assert len(result) == 1
    assert result[0].frame_number == 53
    assert result[0].description == "валідний"


def test_all_out_of_range_returns_empty_list():
    matches = [Match(frame_number=40, description="поза межами")]
    result = _remap_local_frame_numbers(matches, index_offset=0, batch_size=40)
    assert result == []


def test_empty_matches_list_returns_empty_list():
    assert _remap_local_frame_numbers([], index_offset=0, batch_size=40) == []


def test_boundary_values_are_inclusive_exclusive_correctly():
    """batch_size=5 -> валідний діапазон 0..4 включно, 5 вже поза межами."""
    matches = [
        Match(frame_number=0, description="нижня межа - валідно"),
        Match(frame_number=4, description="верхня межа - валідно"),
        Match(frame_number=5, description="одразу за межею - невалідно"),
    ]
    result = _remap_local_frame_numbers(matches, index_offset=20, batch_size=5)
    assert [m.frame_number for m in result] == [20, 24]

# © 2026 Тимур Каленик (Timur Kalenik Corporation) — timurkalenik950@gmail.com
