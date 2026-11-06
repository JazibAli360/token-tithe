import pytest
from textkit.ranges import format_ranges, parse_ranges


def test_parse_simple():
    assert parse_ranges("1-3,5") == [1, 2, 3, 5]


def test_parse_whitespace_duplicates_unsorted():
    assert parse_ranges(" 5, 1-2 ,2") == [1, 2, 5]


def test_parse_single_page_range():
    assert parse_ranges("4-4") == [4]


def test_parse_descending_raises():
    with pytest.raises(ValueError):
        parse_ranges("5-3")


def test_format_collapses_runs():
    assert format_ranges([5, 1, 2, 3, 3]) == "1-3,5"


def test_format_singletons():
    assert format_ranges([1, 3, 5]) == "1,3,5"


def test_roundtrip():
    assert format_ranges(parse_ranges("1-3,7-8,10")) == "1-3,7-8,10"
