from fractions import Fraction

import pytest

from st_secretary.norms import PercentMethod, achieved_norm, norm_row, percent_of_winner
from st_secretary.qualification import Qual
from st_secretary.reference import Level, norm_edition

N = norm_edition("2022-2025")
N26 = norm_edition("2026-2029")


@pytest.mark.parametrize(
    "cls, rank, label",
    [
        (1, Fraction(0), "Менее 1"),
        (1, Fraction(12), "10 и более"),      # у 1 класса строки кончаются на «10 и более»
        (2, Fraction(45), "40 и более"),      # у 2 класса — на «40 и более»
        (3, Fraction(45), "40 и более"),
        (3, Fraction(170), "160 и более"),    # 3 класс идёт до «160 и более»
        (3, Fraction(411), "160 и более"),
        (4, Fraction(71), "63-79"),
        (5, Fraction(2500), "2400"),
        (6, Fraction(250), "250-299"),
    ],
)
def test_row_lookup(cls, rank, label):
    assert norm_row(N, cls, rank).label == label


@pytest.mark.parametrize("cls, rank", [(3, Fraction(15)), (4, Fraction(60)), (5, Fraction(199))])
def test_rank_below_class_range(cls, rank):
    assert norm_row(N, cls, rank) is None


def test_no_rank_no_norms():
    assert achieved_norm(N, 3, None, Fraction(100), Level.REGIONAL).qual is None


def test_psr_2024_case():
    # 3 класс, ранг 170, муниципальные соревнования: I недоступен по статусу, II ≤ 132 %.
    assert achieved_norm(N, 3, Fraction(170), Fraction(100), Level.MUNICIPAL).qual is Qual.II
    assert achieved_norm(N, 3, Fraction(170), Fraction(12473, 100), Level.MUNICIPAL).qual is Qual.II
    assert achieved_norm(N, 3, Fraction(170), Fraction(17880, 100), Level.MUNICIPAL).qual is None
    # Те же проценты на краевых соревнованиях: победителю — I.
    assert achieved_norm(N, 3, Fraction(170), Fraction(100), Level.REGIONAL).qual is Qual.I


def test_threshold_is_inclusive_and_exact():
    assert achieved_norm(N, 3, Fraction(170), Fraction(132), Level.MUNICIPAL).qual is Qual.II
    assert achieved_norm(N, 3, Fraction(170), Fraction(132) + Fraction(1, 10**9), Level.MUNICIPAL).qual is Qual.III


def test_age_limits():
    # I разряд выполняется с 13 лет.
    d = achieved_norm(N, 3, Fraction(170), Fraction(100), Level.REGIONAL, age=12)
    assert d.qual is Qual.II and any("13 лет" in n for n in d.notes)
    # Юношеские — только до 18 лет.
    assert achieved_norm(N, 3, Fraction(170), Fraction(160), Level.REGIONAL, age=15).qual is Qual.III
    assert achieved_norm(N, 2, Fraction(5), Fraction(120), Level.REGIONAL, age=15).qual is Qual.Y2
    assert achieved_norm(N, 2, Fraction(5), Fraction(120), Level.REGIONAL, age=25).qual is None


def test_junior_iii_differs_by_edition():
    # 2022–2025: III юношеский — текстовое условие; 2026–2029: ≤ 200 %.
    assert achieved_norm(N, 1, Fraction(0), Fraction(150), Level.MUNICIPAL, age=12).qual is None
    assert achieved_norm(N26, 1, Fraction(0), Fraction(150), Level.MUNICIPAL, age=12).qual is Qual.Y3


def test_nordic_walking():
    assert achieved_norm(N, 4, Fraction(100), Fraction(100), Level.REGIONAL, age=30, nordic_walking=True).qual is Qual.II
    assert achieved_norm(N26, 4, Fraction(100), Fraction(100), Level.REGIONAL, age=30, nordic_walking=True).qual is Qual.I
    # С 18 лет ограничены только I–III разряды; юношеский 17-летнему доступен.
    assert achieved_norm(N26, 4, Fraction(100), Fraction(100), Level.REGIONAL, age=17, nordic_walking=True).qual is Qual.Y1


def test_percent_time():
    assert percent_of_winner(Fraction(3600), Fraction(3000), PercentMethod.TIME) == 120


def test_percent_points_relative():
    # ПСР-2024: победитель −283, второе место −213 → 124,73 %
    p = percent_of_winner(Fraction(-213), Fraction(-283), PercentMethod.POINTS_RELATIVE_TO_WINNER)
    assert p == Fraction(35300, 283) and round(float(p), 2) == 124.73
    with pytest.raises(ValueError):
        percent_of_winner(Fraction(5), Fraction(0), PercentMethod.POINTS_RELATIVE_TO_WINNER)
