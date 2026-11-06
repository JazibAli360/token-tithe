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
