"""Дисциплины «по времени» (спелео, пешеходные): время, отсечки, баллы × 15/30 с, снятия, КВ — на выдуманных командах."""

from dataclasses import replace
from fractions import Fraction

import pytest

from st_secretary import time_run as tr
from st_secretary.competition import Zachet
from st_secretary.disciplines import Status
from st_secretary.norms import achieved_norm
from st_secretary.psr_run import Member, TeamInput
from st_secretary.qualification import Qual
from st_secretary.reference import norm_edition


@pytest.fixture
def speleo(psr_card):
    return replace(psr_card, zachety=[Zachet("М/Ж", 2, "0840271811Я", team_size=4)])


def team(name, n, qual=Qual.II, chip=""):
    return TeamInput(f"{name}.xlsx", name, "г. N", str(n), [Member(f"{name} {i}", qual, qual.label, chip) for i in range(4)])


STAGES = [{"id": "s1", "tour": "", "name": "Колодец"}, {"id": "s2", "tour": "", "name": "Шкуродёр"}]


def test_times_penalties_removals_and_places(speleo):
    z = speleo.zachety[0]
    teams = [team("Кедр", 1), team("Сосна", 2), team("Ель", 3), team("Пихта", 4), team("Лиственница", 5),
             team("Берёза", 6), team("Осина", 7)]
    zdata = {"stages": STAGES, "expected": "25", "kv": "60", "teams": {
        "Кедр.xlsx": {"start": "10:00:00", "finish": "10:20:30", "cutoffs": "2:00", "points": {"s1": "0,3", "s2": "1"}},
        "Сосна.xlsx": {"start": "10:05:00", "finish": "10:24:00", "points": {"s1": "2"}},   # 19:00 + 2 × 15 = 19:30
        "Ель.xlsx": {"start": "10:10:00", "finish": "10:25:00", "points": {"s1": "с"}},     # быстрее всех, но со снятием
        "Пихта.xlsx": {"start": "10:15:00", "finish": "11:30:00"},                          # 75 мин > КВ 60 мин
        "Лиственница.xlsx": {"start": "23:50:00", "finish": "00:12:10"},                    # финиш после полуночи
        "Берёза.xlsx": {"start": "10:30", "finish": "10:52:00", "cutoffs": "abc"},
        "Осина.xlsx": {"start": "10:35:00"},                                                # финиша ещё нет
    }}
    run = tr.compute(speleo, z, zdata, teams)
    assert run.kind == "time" and run.seconds_per_point == 15  # расчётное время 25 мин → 15 с за балл
    rows = {r.inp.team: r for r in run.rows}
    k = rows["Кедр"]
    assert (k.distance_time, k.total) == (Fraction(18 * 60 + 30), Fraction(18 * 60 + 30) + Fraction(13, 10) * 15)
    assert tr.clock_text(k.total) == "18:49,5"
    assert rows["Лиственница"].distance_time == 22 * 60 + 10
    assert [(r.inp.team, r.place) for r in run.rows[:4]] == [("Кедр", 1), ("Сосна", 2), ("Лиственница", 3), ("Ель", 4)]
    assert rows["Пихта"].status is Status.OVER_TIME and rows["Пихта"].auto_status and rows["Пихта"].place is None
    assert rows["Осина"].place is None and rows["Берёза"].place is None
    texts = [i.text for i in run.issues]
    assert "«Берёза»: отсечки «abc» — не время (нужно мм:сс)" in texts
    assert any("«Пихта»: время на дистанции 1:15:00 больше КВ (60 мин)" in t for t in texts)
    assert any(t.startswith("нет времени старта или финиша — место не присуждается: ") and "Осина" in t for t in texts)
    assert rows["Кедр"].percent == 100 and rows["Сосна"].percent == Fraction(19 * 60 + 30) / k.total * 100
    assert rows["Ель"].removals == 1 and rows["Ель"].norm == ""  # со снятием норматив не присваивается
    norms = norm_edition(speleo.norms_edition)
    assert run.rank.value is not None
    want = achieved_norm(norms, 2, run.rank.value, Fraction(100), speleo.level).qual
    assert k.norm == (want.label if want else "")


def test_seconds_per_point_and_removal_order(speleo):
    z = speleo.zachety[0]
    teams = [team("А", 1), team("Б", 2), team("В", 3)]
    zdata = {"stages": STAGES, "spp": "30", "removed_order": "count", "teams": {
        "А.xlsx": {"start": "10:00:00", "finish": "10:30:00", "points": {"s1": "с", "s2": "с"}},
        "Б.xlsx": {"start": "10:00:00", "finish": "10:40:00", "points": {"s1": "с", "s2": "1"}},
        "В.xlsx": {"start": "10:00:00", "finish": "10:50:00", "points": {"s2": "2"}}}}
    run = tr.compute(speleo, z, zdata, teams)
    assert run.seconds_per_point == 30
    # без снятий — первые; среди снятых выше тот, у кого снятий меньше (так в Условиях)
    assert [(r.inp.team, r.place) for r in run.rows] == [("В", 1), ("Б", 2), ("А", 3)]
    assert run.rows[0].total == 50 * 60 + 60


@pytest.mark.parametrize("s, sec", [("10:05:23", 36323), ("10:05", 36300), ("9:05:23,4", Fraction(327234, 10)), ("", None)])
def test_parse_clock(s, sec):
    assert tr.parse_clock(s) == sec


@pytest.mark.parametrize("s, sec", [("5:30", 330), ("1:05:30", 3930), ("5", 300), ("", 0), ("2,5", 150)])
def test_parse_duration(s, sec):
    assert tr.parse_duration(s) == sec


def test_bad_values_and_texts():
    for bad in ("25:61:00", "10:5", "десять"):
        with pytest.raises(ValueError):
            tr.parse_clock(bad)
    with pytest.raises(ValueError):
        tr.parse_duration("5:75")
    assert tr.clock_text(Fraction(3930)) == "1:05:30" and tr.clock_text(Fraction(59)) == "0:59"
    assert tr.is_time_discipline(Zachet("М/Ж", 2, "0840131811Я")) and not tr.is_time_discipline(Zachet("М/Ж", 3, "0840161811Я"))
