import pytest
from mathkit.stats import mean, moving_average


def test_mean():
    assert mean([1, 2, 3]) == 2


def test_mean_empty():
    with pytest.raises(ValueError):
        mean([])


def test_moving_average_rejects_bad_window():
    with pytest.raises(ValueError):
        moving_average([1, 2], 0)


def test_moving_average_window_too_large():
    assert moving_average([1, 2], 3) == []


def test_moving_average_includes_last_window():
    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]


def test_moving_average_window_equals_length():
    assert moving_average([2, 4], 2) == [3]
