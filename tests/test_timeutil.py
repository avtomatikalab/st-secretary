from fractions import Fraction

import pytest

from st_secretary.timeutil import format_time, from_excel, parse_time


@pytest.mark.parametrize("text, seconds", [
    ("1:02:03", 3723), ("02:03", 123), ("1:02:03,4", Fraction(37234, 10)), ("0:00:59.9", Fraction(599, 10)),
])
def test_parse(text, seconds):
    assert parse_time(text) == seconds


@pytest.mark.parametrize("bad", ["1:60:00", "abc", "1:02:3a"])
def test_parse_bad(bad):
    with pytest.raises(ValueError):
        parse_time(bad)


def test_from_excel_day_fraction():
    # 0.0875 суток = 2:06:00 (время старта из рабочей книги ПСР-2024)
    assert from_excel(0.0875) == 2 * 3600 + 6 * 60


def test_format():
    assert format_time(Fraction(3723)) == "1:02:03"
    assert format_time(Fraction(37234, 10), tenths=True) == "1:02:03,4"
    with pytest.raises(ValueError):
        format_time(Fraction(-1))
