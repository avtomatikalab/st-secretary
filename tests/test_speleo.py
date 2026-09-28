from fractions import Fraction as F

import pytest

from st_secretary.disciplines import Status
from st_secretary.disciplines.speleo import SpeleoRun, point_seconds, standings
from st_secretary.timeutil import parse_time


def run(entry, order, start, finish, **kw):
    return SpeleoRun(entry, order, parse_time(start), parse_time(finish), **kw)


def test_point_seconds():
    assert point_seconds(30) == 15 and point_seconds(31) == 30 and point_seconds(10, override=20) == 20


def test_result_time_minus_cutoff_plus_points():
    r = run("a", 1, "10:00:00", "10:20:00", cutoffs=F(60), penalty_points=F(3, 10))  # 0,3 балла
    assert r.result(15) == 19 * 60 + F(9, 2)  # 19 мин + 4,5 с


def test_removals_go_after_full_finishers():
    a = run("fast_but_removed", 1, "10:00:00", "10:10:00", removals=1)
    b = run("slow_clean", 2, "10:00:00", "10:30:00")
    c = run("dnf", 3, "10:00:00", "10:05:00", status=Status.DNF)
    assert [(p.item.entry, p.place) for p in standings([a, b, c], 30)] == \
        [("slow_clean", 1), ("fast_but_removed", 2), ("dnf", None)]


def test_removed_by_count_option():
    one = run("one", 1, "10:00:00", "10:30:00", removals=1)
    two = run("two", 2, "10:00:00", "10:10:00", removals=2)
    assert [p.item.entry for p in standings([one, two], 30)] == ["two", "one"]
    assert [p.item.entry for p in standings([one, two], 30, removed_by_count=True)] == ["one", "two"]


def test_bad_times_are_errors():
    with pytest.raises(ValueError):
        run("x", 1, "10:00:00", "09:59:00").result(15)
    with pytest.raises(ValueError):
        SpeleoRun("y", 1, None, parse_time("10:00:00")).result(15)
