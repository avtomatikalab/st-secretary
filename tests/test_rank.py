from fractions import Fraction

import pytest

from st_secretary.qualification import Qual, parse_qual
from st_secretary.rank import RankEntry, evsk_participation_ok, qualification_rank, rank_divisor
from st_secretary.reference import Level, norm_edition

N = norm_edition("2022-2025")


def entries(rows):
    return [RankEntry(place, tuple(parse_qual(q) for q in quals)) for place, quals in rows]


# Всероссийские соревнования, дистанция – комбинированная, 2025 (протокол опубликован на tssr.ru).
# Составы — только разряды, без ФИО. В протоколе: «Квалификационный ранг дистанции 71,7».
VS_PSR_2025 = [
    (1, ["КМС", "I", "I", "I", "II", "КМС"]),
    (2, ["б/р", "б/р", "б/р", "III", "III", "III"]),
    (3, ["III", "б/р", "III", "III", "б/р", "III"]),
    (4, ["б/р"] * 6),
    (5, ["б/р", "2ю", "III", "II", "III", "2ю"]),
    (6, ["б/р", "б/р", "б/р", "III", "III", "б/р"]),
    (7, ["б/р"] * 6),
    (8, ["б/р"] * 6),
    (9, ["б/р"] * 6),
]


def test_vs_psr_2025_rank_is_71_7():
    r = qualification_rank(entries(VS_PSR_2025), "group", N)
    assert r.value == Fraction(1076, 15)  # 71,733…
    assert r.formatted() == "71,7"


def test_group_of_three_divides_by_four():
    # ПСР-2024, группы по 3 человека: делитель 4 (строка «4» таблицы баллов).
    rows = [(1, ["III", "III", "б/р"]), (2, ["КМС", "б/р", "КМС"]), (3, ["КМС", "КМС", "I"]),
            (4, ["КМС", "б/р", "б/р"]), (5, ["б/р", "III", "II"]), (6, ["б/р", "III", "II"]),
            (7, ["б/р", "III", "б/р"]), (8, ["III", "II", "б/р"])]
    assert qualification_rank(entries(rows), "group", N).value == 170


def test_less_than_six_participants():
    r = qualification_rank(entries([(i, ["КМС"]) for i in range(1, 6)]), "individual", N)
    assert r.value is None and "не менее 6" in r.reason
    assert r.formatted() == "не определялся"


def test_ties_at_sixth_place_count_all():
    rows = [(1, ["I"]), (2, ["I"]), (3, ["I"]), (4, ["I"]), (5, ["I"]), (6, ["I"]), (6, ["I"]), (8, ["КМС"])]
    assert qualification_rank(entries(rows), "individual", N).value == 7 * 40


def test_unplaced_not_counted():
    rows = [(1, ["III"]), (2, ["III"]), (None, ["МС"]), (3, ["III"]), (4, ["III"]), (5, ["III"])]
    assert qualification_rank(entries(rows), "individual", N).value == 5 * 4


def test_pair_divides_by_two():
    rows = [(i, ["КМС", "КМС"]) for i in range(1, 7)]
    assert qualification_rank(entries(rows), "pair", N).value == 6 * 120


@pytest.mark.parametrize("fmt, size, div", [("individual", 1, 1), ("pair", 2, 2), ("group", 3, 4),
                                            ("group", 4, 4), ("group", 6, 6), ("crew", 2, 2), ("crew", 5, 5)])
def test_divisor(fmt, size, div):
    assert rank_divisor(fmt, size) == div


def test_crew_of_three_is_undefined():
    with pytest.raises(ValueError):
        rank_divisor("crew", 3)


def test_evsk_participation():
    assert evsk_participation_ok(Level.ALL_RUSSIAN, 7, 6, False) == (True, None)
    ok, why = evsk_participation_ok(Level.ALL_RUSSIAN, 9, 5, False)
    assert not ok and "25.2.1" in why
    assert evsk_participation_ok(Level.MUNICIPAL, 3, None, False)[0]
    assert not evsk_participation_ok(Level.MUNICIPAL, 5, None, True)[0]  # баллы судей — нужно 6
    assert not evsk_participation_ok(Level.INTERREGIONAL, 6, None, False)[0]


def test_qual_points_order_matches_table():
    pts = N.rank_points
    assert [pts[q] for q in (Qual.MS, Qual.KMS, Qual.I, Qual.II, Qual.III, Qual.Y1, Qual.Y2, Qual.Y3, Qual.BR)] == \
        [400, 120, 40, 12, 4, 4, Fraction(6, 5), Fraction(2, 5), 0]
