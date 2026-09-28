from dataclasses import dataclass

from st_secretary.ranking import assign_places


@dataclass(frozen=True)
class E:
    name: str
    start: int
    result: int | None  # None — место не присуждается


def run(items, **kw):
    placed = assign_places(items, eligible=lambda e: e.result is not None, sort_key=lambda e: e.result,
                           start_order=lambda e: e.start, **kw)
    return [(p.item.name, p.place) for p in placed]


def test_ties_share_place_and_skip_next():
    items = [E("a", 1, 10), E("b", 2, 5), E("c", 3, 5), E("d", 4, 7), E("e", 5, None)]
    assert run(items) == [("b", 1), ("c", 1), ("d", 3), ("a", 4), ("e", None)]


def test_ties_listed_in_start_order():
    items = [E("late", 9, 5), E("early", 2, 5)]
    assert run(items) == [("early", 1), ("late", 1)]


def test_tie_by_start_order_for_mountain():
    items = [E("late", 9, 5), E("early", 2, 5), E("x", 1, 6)]
    assert run(items, tie_by_start_order=True) == [("early", 1), ("late", 2), ("x", 3)]


def test_unplaced_in_start_order_at_the_end():
    items = [E("z", 3, None), E("y", 1, None), E("w", 2, 1)]
    assert run(items) == [("w", 1), ("y", None), ("z", None)]
