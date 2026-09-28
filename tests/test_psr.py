"""ПСР: пересчёт реального соревнования (обезличенный набор) и правила части 4."""

import json
from fractions import Fraction
from pathlib import Path

import pytest

from st_secretary.disciplines import Status
from st_secretary.disciplines.psr import (
    PsrStage,
    PsrTeamCard,
    class_points,
    distance_class,
    stage_tech_penalty,
    standings,
    tour_totals,
    unknown_stage_keys,
)
from st_secretary.norms import PercentMethod, achieved_norm, percent_of_winner
from st_secretary.qualification import Qual
from st_secretary.rank import RankEntry, qualification_rank
from st_secretary.reference import Level, check_code_matches_name, norm_edition

FIX = json.loads((Path(__file__).parent / "fixtures" / "psr2024.json").read_text(encoding="utf-8"))
STAGES = [PsrStage(s["key"], s["title"], s["tour"]) for s in FIX["stages"]]
KNOWN = {s.key for s in STAGES}


def cards():
    return [
        PsrTeamCard(t["label"], t["start_order"],
                    {k: Fraction(v) for k, v in t["points"].items() if k in KNOWN})
        for t in FIX["teams"]
    ]


def by_label():
    return {t["label"]: t for t in FIX["teams"]}


def test_fixture_has_35_stages_and_one_subtotal_column():
    assert len(STAGES) == 35
    assert FIX["unnamed_columns"] == ["AM"]


def test_workbook_total_double_counts_subtotal():
    # Сумма в рабочей книге = сумма по этапам + колонка-подытог тура 7.
    for t in FIX["teams"]:
        stages_sum = sum(Fraction(v) for k, v in t["points"].items() if k in KNOWN)
        subtotal = Fraction(t["points"].get("unnamed:AM", "0"))
        assert Fraction(t["workbook_total"]) == stages_sum + subtotal
        assert subtotal == sum(Fraction(v) for k, v in t["points"].items()
                               if k in KNOWN and next(s for s in STAGES if s.key == k).tour == "Тур 7")


def test_recalculation_matches_final_protocol_except_known_bonus():
    fx = by_label()
    mismatched = []
    for p in standings(cards()):
        prot = fx[p.item.team]["protocol"]
        if p.item.total != Fraction(prot["result"]):
            mismatched.append((p.item.team, p.item.total, Fraction(prot["result"])))
        assert str(p.place) == prot["place"]
        # суммы по турам
        tours = tour_totals(p.item, STAGES)
        for title, val in prot["parts"].items():
            key = title if title.startswith("Тур") else title.split()[0]
            if key == "Бонус":
                continue
            assert tours.get(key, 0) == Fraction(val or "0"), (p.item.team, title)
    # Единственное расхождение: бонус за ориентирование одной команды (−190 в книге, −250 в протоколе).
    assert len(mismatched) == 1
    _, ours, theirs = mismatched[0]
    assert ours - theirs == 60


def test_protocol_header_code_error_is_detected():
    assert check_code_matches_name(FIX["vrvs_code_in_protocol"], "дистанция - комбинированная") is not None


def test_rank_and_norms_of_final_protocol():
    norms = norm_edition("2022-2025")
    teams = sorted(FIX["teams"], key=lambda t: int(t["protocol"]["place"]))
    entries = [RankEntry(int(t["protocol"]["place"]), tuple(Qual[m] for m in t["protocol"]["members"])) for t in teams]
    rank = qualification_rank(entries, "group", norms)
    assert rank.value == 170 and FIX["rank_in_protocol"] == "411"
    winner = Fraction(teams[0]["protocol"]["result"])
    for t in teams:
        p = percent_of_winner(Fraction(t["protocol"]["result"]), winner, PercentMethod.POINTS_RELATIVE_TO_WINNER)
        assert abs(p / 100 - Fraction(t["protocol"]["percent"])) < Fraction(1, 10**9)  # протокол хранит долю
        got = achieved_norm(norms, FIX["distance_class"], rank.value, p, Level.MUNICIPAL).qual
        assert (got.label if got else "") == t["protocol"]["norm"]


def test_unknown_stage_keys():
    c = PsrTeamCard("x", 1, {"F": Fraction(1), "ZZ": Fraction(2)})
    assert unknown_stage_keys([c], STAGES) == {"ZZ"}


def test_removed_team_gets_no_place():
    a = PsrTeamCard("a", 1, {"F": Fraction(10)})
    b = PsrTeamCard("b", 2, {"F": Fraction(-5)}, status=Status.REMOVED)
    assert [(p.item.team, p.place) for p in standings([a, b])] == [("a", 1), ("b", None)]


# ---------------------------------------------------------------- класс дистанции и ТШ (п. 4.1.3, 4.3)

def test_class_points_formula():
    assert class_points([80, 70, 50], km=Fraction(25), travel_modes=2) == 200 + 500 + 400


@pytest.mark.parametrize("points, hours, cls", [
    (2500, 31, 3), (2500, 29, 2), (3100, 40, 3), (6000, 100, 6), (399, 100, None), (900, 7, 1),
])
def test_distance_class(points, hours, cls):
    assert distance_class(Fraction(points), Fraction(hours)) == cls


@pytest.mark.parametrize("scores, k, expected", [
    ([10, 5, 10, 10, 0, 0, 5, 5], Fraction(1), 50),     # 45 → 50
    ([10, 5, 10, 10, 0, 0, 5, 5], Fraction(1, 2), 30),  # 22,5 → 30
    ([0] * 8, Fraction(1), 10),
    ([10] * 8, Fraction(1), 80),
])
def test_stage_tech_penalty(scores, k, expected):
    assert stage_tech_penalty(scores, k) == expected


def test_stage_tech_penalty_validation():
    with pytest.raises(ValueError):
        stage_tech_penalty([10] * 7, Fraction(1))
    with pytest.raises(ValueError):
        stage_tech_penalty([10] * 8, Fraction(2))
