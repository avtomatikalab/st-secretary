from fractions import Fraction

import pytest

from st_secretary.qualification import Qual
from st_secretary.reference import (
    Level,
    check_code_matches_name,
    discipline_by_code,
    discipline_by_name,
    norm_edition,
    norm_editions,
)


def test_vrvs_lookup():
    assert discipline_by_name("Дистанция – комбинированная").code == "0840161811Я"
    assert discipline_by_code("0840271811Я").name == "дистанция - спелео - группа"
    assert discipline_by_code(" 0840161811Я ").rank_format == "group"
    assert discipline_by_code("0840021811Я").name.startswith("маршрут - водный")


def test_code_name_mismatch_is_reported():
    # Реальная ошибка протокола ПСР-2024: код спелео-группы в шапке комбинированной дистанции.
    remark = check_code_matches_name("0840271811Я", "дистанция - комбинированная")
    assert "спелео - группа" in remark and "0840161811Я" in remark
    assert check_code_matches_name("0840161811Я", "дистанция – комбинированная") is None


def test_unknown_code():
    assert "не найден" in check_code_matches_name("0840999999Я", "дистанция - комбинированная")


def test_both_editions_present():
    assert set(norm_editions()) >= {"2022-2025", "2026-2029"}


@pytest.mark.parametrize("edition", ["2022-2025", "2026-2029"])
def test_edition_structure(edition):
    n = norm_edition(edition)
    assert len(n.rows) == 36
    assert n.class_rows[3] == (Fraction(20), Fraction(160))
    assert n.class_rows[5] == n.class_rows[6] == (Fraction(200), Fraction(2400))
    assert n.rank_points[Qual.KMS] == 120 and n.rank_points[Qual.Y2] == Fraction(6, 5)
    assert n.min_level[Qual.I] is Level.REGIONAL and n.min_level[Qual.II] is Level.MUNICIPAL


def test_editions_differ_only_where_expected():
    old, new = norm_edition("2022-2025"), norm_edition("2026-2029")
    for a, b in zip(old.rows, new.rows):
        assert a.min_rank == b.min_rank
        assert {q: v for q, v in a.thresholds.items() if q is not Qual.Y3} == \
               {q: v for q, v in b.thresholds.items() if q is not Qual.Y3}
        assert a.y3_condition == (Qual.Y3 in b.thresholds)
    assert old.junior_iii_text and new.junior_iii_text is None
    assert not old.nordic_i_allowed and new.nordic_i_allowed
