from datetime import date, datetime

import pytest

from st_secretary.textclean import (
    clean_spaces,
    edit_distance,
    from_years,
    normalize_name,
    normalize_phone,
    normalize_team,
    normalize_territory,
    parse_birth_date,
    sex_from_patronymic,
    split_contacts,
    years,
)

TODAY = date(2025, 9, 20)


def test_glued_name_is_split():
    n = normalize_name("Петров ИванСергеевич")
    assert n.full == "Петров Иван Сергеевич" and n.patronymic == "Сергеевич"
    assert any("слитные" in f for f in n.fixes)


def test_date_inside_name_is_extracted():
    n = normalize_name("Смирнова Анна Олеговна12.03.2001")
    assert n.full == "Смирнова Анна Олеговна" and n.extracted_birth == "12.03.2001"


def test_case_and_spaces():
    n = normalize_name("  иванова-петрова   МАРИЯ  ивановна ")
    assert n.full == "Иванова-Петрова Мария Ивановна"


@pytest.mark.parametrize("fio, doubt", [
    ("Козлова Ольга Александров", "не похоже на отчество"),  # обрезанное отчество
    ("Лебедев Макс Андрей", "не похоже на отчество"),
    ("Орлов Пётр", "нет отчества"),
])
def test_name_doubts(fio, doubt):
    assert any(doubt in text and why for text, why in normalize_name(fio).doubts)


@pytest.mark.parametrize("patr, sex", [("Сергеевич", "м"), ("Ильич", "м"), ("Олеговна", "ж"),
                                       ("Ильинична", "ж"), ("Мамед оглы", "м"), ("Александров", None)])
def test_sex_from_patronymic(patr, sex):
    assert sex_from_patronymic(patr) == sex


@pytest.mark.parametrize("raw, expected", [
    ("17.10.1989", date(1989, 10, 17)),
    ("07/12/2000", date(2000, 12, 7)),
    ("1995-03-05", date(1995, 3, 5)),
    (datetime(2004, 4, 14), date(2004, 4, 14)),
    (35296, date(1996, 8, 19)),  # серийный номер даты Excel
])
def test_birth_dates(raw, expected):
    assert parse_birth_date(raw, today=TODAY).value == expected


def test_nonexistent_date_is_an_error():
    p = parse_birth_date("29.02.1995", today=TODAY)
    assert p.value is None and "не существует" in p.problem
    assert "1995 год не високосный" in p.why


@pytest.mark.parametrize("raw, why", [("31.04.2001", "В апреле 30 дней"),
                                      ("12.25.2000", "переставлены местами — тогда это 25.12.2000"),
                                      ("00.05.2001", "Дня с номером 0")])
def test_why_date_does_not_exist(raw, why):
    assert why in parse_birth_date(raw, today=TODAY).why


@pytest.mark.parametrize("n, text, frm", [(1, "1 год", "1 года"), (21, "21 год", "21 года"), (22, "22 года", "22 лет"),
                                          (11, "11 лет", "11 лет"), (25, "25 лет", "25 лет")])
def test_years_words(n, text, frm):
    assert years(n) == text and from_years(n) == frm


@pytest.mark.parametrize("raw, fragment", [("", "не указана"), ("12.03.25", "двумя цифрами"),
                                           ("01.01.2030", "в будущем"), ("вчера", "не удалось")])
def test_bad_dates(raw, fragment):
    assert fragment in parse_birth_date(raw, today=TODAY).problem


def test_year_only():
    p = parse_birth_date("1995", today=TODAY)
    assert p.value is None and p.year_only == 1995 and "только год" in p.note


def test_phone_and_contacts():
    assert normalize_phone("89131234567") == "+7 913 123-45-67"
    assert normalize_phone("7 (913) 123-45-67") == "+7 913 123-45-67"
    assert split_contacts("89131234567, test@mail.ru") == ("+7 913 123-45-67", "test@mail.ru")
    assert split_contacts(79131234567) == ("+7 913 123-45-67", "")


def test_territory_and_team():
    assert normalize_territory("г.Томск") == "Томск"
    assert normalize_territory("г. Красноярск ") == "Красноярск"
    assert normalize_team('"Имени кота') == "Имени кота"
    assert normalize_team(" Черепахи -ниндзя") == "Черепахи-ниндзя"
    assert normalize_team("Ураган - 2") == "Ураган - 2"
    assert clean_spaces("a\xa0 b") == "a b"


def test_edit_distance():
    assert edit_distance("Краснорярск", "Красноярск") == 1
    assert edit_distance("Томск", "Омск") == 1
