"""Small statistics helpers."""


def mean(values):
    """Arithmetic mean of a non-empty sequence."""
    if not values:
        raise ValueError("mean() of empty sequence")
    return sum(values) / len(values)


def moving_average(values, window):
    """Simple moving averages of `values` over `window` consecutive items.

    >>> moving_average([1, 2, 3, 4], 2)
    [1.5, 2.5, 3.5]
    """
    if window <= 0:
        raise ValueError("window must be positive")
    if window > len(values):
        return []
    return [mean(values[i : i + window]) for i in range(len(values) - window + 1)]
