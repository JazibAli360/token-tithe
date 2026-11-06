import pytest

from timeutil.durations import format_duration, parse_duration


def test_parse():
    assert parse_duration("1h30m") == 5400


def test_format():
    assert format_duration(5400) == "1h30m"


def test_roundtrip():
    assert parse_duration(format_duration(3725)) == 3725


def test_format_zero():
    assert format_duration(0) == "0s"


def test_negative():
    with pytest.raises(ValueError):
        format_duration(-1)
