from mathkit.stats import moving_average


def test_docstring_example():
    assert moving_average([1, 2, 3, 4], 2) == [1.5, 2.5, 3.5]


def test_full_window():
    assert moving_average([1, 2, 3], 3) == [2]


def test_window_one_is_identity():
    assert moving_average([5, 6, 7], 1) == [5, 6, 7]
