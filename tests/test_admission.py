from datetime import date

from st_secretary.admission import age_groups, age_in_year, check_athlete, check_leader, full_years
from st_secretary.qualification import Qual


def test_age_counts_by_calendar_year():
    assert age_in_year(date(2008, 12, 31), 2025) == 17
    assert full_years(date(2007, 9, 21), date(2025, 9, 20)) == 17
    assert full_years(date(2007, 9, 20), date(2025, 9, 20)) == 18


def test_psr_age_groups_by_class():
    assert age_groups("psr", 19, 3) == ["юниоры, юниорки", "юниоры, юниорки (студенты)"]
    assert age_groups("psr", 22, 6) == ["мужчины, женщины"]
    assert age_groups("psr", 19, 6) == []  # юниоры — до 5 класса
    assert check_athlete("psr", 6, date(2006, 6, 20), Qual.BR, 2025)  # есть замечание


def test_psr_adult_ok():
    assert check_athlete("psr", 3, date(1995, 1, 1), Qual.BR, 2025) == []


def test_leader_age():
    start = date(2025, 9, 20)
    assert check_leader("psr", 3, date(2007, 9, 20), start) == []
    assert check_leader("psr", 3, date(2007, 9, 21), start)
    assert check_leader("psr", 5, date(2006, 1, 1), start)  # 5–6 класс — с 20 лет


def test_speleo_class_requirements():
    assert check_athlete("speleo", 4, date(2010, 5, 5), Qual.III, 2025) == []
    issues = check_athlete("speleo", 4, date(2010, 5, 5), Qual.Y1, 2025)
    assert issues and "не ниже III" in issues[0].text
    assert check_athlete("speleo", 5, date(2012, 1, 1), Qual.II, 2025)  # 13 лет — рано


def test_pedestrian_class5_two_options():
    assert check_athlete("pedestrian", 5, date(2009, 1, 1), Qual.KMS, 2025) == []  # 16 лет + КМС
    assert check_athlete("pedestrian", 5, date(2008, 1, 1), Qual.I, 2025) == []    # 17 лет + I
    assert check_athlete("pedestrian", 5, date(2009, 1, 1), Qual.I, 2025)          # 16 лет + I — нет


def test_pedestrian_class3_junior_alternative():
    assert check_athlete("pedestrian", 3, date(2012, 1, 1), Qual.Y1, 2025) == []
    assert check_athlete("pedestrian", 3, date(2012, 1, 1), Qual.Y2, 2025)
