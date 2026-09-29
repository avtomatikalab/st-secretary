"""ПСР в программе: баллы этапов → результат, места, ранг, процент, разряды. Эталон — обезличенный ПСР-2024."""

import json
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from st_secretary import psr_run as pr
from st_secretary.disciplines import Status
from st_secretary.qualification import Qual

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "psr2024.json").read_text(encoding="utf-8"))


def fixture_input():
    stages = []
    for s in FIXTURE["stages"]:
        tour, name = pr.split_title(s["title"])
        stages.append({"id": s["key"], "tour": tour, "name": name})
    teams, stored = [], {}
    for t in FIXTURE["teams"]:
        file = t["label"] + ".xlsx"
        members = [pr.Member(f"Участник {i}", Qual[q], q) for i, q in enumerate(t["protocol"]["members"])]
        teams.append(pr.TeamInput(file, t["label"], "г. N", str(t["start_order"]), members))
        stored[file] = {"points": t["points"]}
    return {"stages": stages, "teams": stored}, teams


def test_psr2024_recalculation(psr_card):
    """Пересчёт совпадает с итоговым протоколом у 10 команд из 11 (у одной в рабочей книге другой бонус)."""
    zdata, teams = fixture_input()
    run = pr.compute(psr_card, psr_card.zachety[0], zdata, teams)
    assert len(run.stages) == 35 and run.tours[:2] == ["Тур 1", "Тур 2"] and run.tours[-1] == "Бонус"
    assert run.complete is False  # не у всех команд заполнены все этапы (пустые — 0 баллов)
    proto = {t["label"]: t["protocol"] for t in FIXTURE["teams"]}
    same = [r for r in run.rows if r.total == Fraction(proto[r.inp.team]["result"])]
    assert len(same) == 10
    assert all(str(r.place) == proto[r.inp.team]["place"] for r in same)
    assert run.rows[0].place == 1 and run.rows[0].total == run.winner
    assert run.rank.formatted() == "170"  # по составам (в протоколе — 411, ошибка протокола)
    for r in same:
        p = proto[r.inp.team]
        assert r.tours["Тур 1"] == Fraction(p["parts"]["Тур 1"] or 0)
        assert round(float(r.percent), 2) == round(float(Fraction(p["percent"])) * 100, 2)
        assert r.norm == p["norm"]


def test_statuses_ties_and_bad_input(psr_card):
    z = psr_card.zachety[0]
    stages = [{"id": "a", "tour": "Тур 1", "name": "Узлы", "max": "100"}, {"id": "b", "tour": "Тур 1", "name": "Бивак"}]
    teams = [pr.TeamInput(f"{n}.xlsx", n, "г. N", str(i), [pr.Member("", Qual.II, "II")] * 3)
             for i, n in enumerate(["Кедр", "Сосна", "Ель", "Пихта"], start=1)]
    zdata = {"stages": stages, "teams": {
        "Кедр.xlsx": {"points": {"a": "20", "b": "-5"}},
        "Сосна.xlsx": {"points": {"a": "10,5", "b": "4,5"}},  # 15 — как у Кедра
        "Ель.xlsx": {"points": {"a": "двадцать", "b": "250"}},
        "Пихта.xlsx": {"points": {"a": "0"}, "status": "removed"},
    }}
    run = pr.compute(psr_card, z, zdata, teams)
    assert [(r.inp.team, r.place, pr.points_text(r.total)) for r in run.rows] == [
        ("Кедр", 1, "15"), ("Сосна", 1, "15"), ("Ель", 3, "250"), ("Пихта", None, "0")]
    texts = [i.text for i in run.issues]
    assert "«Ель», Тур 1 · Узлы: «двадцать» — не число" in texts
    assert any("ранг не определяется: в виде программы участвовало 4" in t for t in texts)
    assert any("нормативы: участников 4" in t for t in texts)
    zdata["tie"] = "start"  # Положение: при равенстве выше стартовавший раньше
    assert [r.place for r in pr.compute(psr_card, z, zdata, teams).rows][:2] == [1, 2]
    assert pr.STATUS_LABEL[Status.REMOVED] == "снята"


def test_actual_class(psr_card):
    """Фактический класс: без этапов, где получен МШ (п. 6.1.3)."""
    z = psr_card.zachety[0]
    stages = [{"id": f"s{i}", "tour": "Тур 1", "name": f"Этап {i}", "max": "300"} for i in range(6)]
    teams = [pr.TeamInput("A.xlsx", "A", "", "1", []), pr.TeamInput("B.xlsx", "B", "", "2", [])]
    zdata = {"stages": stages, "distance": {"km": "20", "modes": "1", "kv_hours": "30"},
             "teams": {"A.xlsx": {"points": {"s0": "10"}}, "B.xlsx": {"points": {"s0": "300", "s1": "300"}}}}
    run = pr.compute(psr_card, z, zdata, teams)
    # A: 6 × 300 + 20 × 20 + 200 = 2400 ≥ 2000 → 3 класс; B: 4 × 300 + 400 + 200 = 1800 → 2 класс
    assert [(r.inp.team, r.actual_class) for r in run.rows] == [("A", 3), ("B", 2)]
    assert any("«B»: фактически пройден 2 класс (заявлен 3)" in i.text for i in run.issues)


@pytest.mark.parametrize("title, tour, name", [
    ("Тур 2 Мера (Вышка 51 м, Река 17,5 м)", "Тур 2", "Мера (Вышка 51 м, Река 17,5 м)"),
                                               ("Бонус Ориентирование", "Бонус", "Ориентирование"),
                                               ("Тур3. Переправа", "Тур 3", "Переправа")])
def test_split_title(title, tour, name):
    assert pr.split_title(title) == (tour, name)


def test_parse_points():
    assert pr.parse_points("−12,5") == Fraction(-25, 2) and pr.parse_points(" ") is None
    with pytest.raises(ValueError):
        pr.parse_points("12 б")
    assert pr.points_text(Fraction(-25, 2)) == "-12,5" and pr.points_text(Fraction(7)) == "7"


def test_import_group_protocol(psr_card):
    from types import SimpleNamespace

    sheet = SimpleNamespace(
        stage_columns=[SimpleNamespace(col=5, title="Тур 1 Узлы"), SimpleNamespace(col=6, title="Бонус Ориентирование")],
        teams=[SimpleNamespace(team="КЕДР", values={5: Fraction(20), 6: Fraction(-100), 9: Fraction(3)}),
               SimpleNamespace(team="Дуб", values={5: Fraction(1)})],
        warnings=["Колонка AM без названия"])
    teams = [pr.TeamInput("Кедр.xlsx", "Кедр", "", "1", [])]
    data, notes = pr.import_group_protocol(sheet, teams)
    assert data["stages"] == [{"id": "s1", "tour": "Тур 1", "name": "Узлы"},
                              {"id": "s2", "tour": "Бонус", "name": "Ориентирование"}]
    assert data["teams"] == {"Кедр.xlsx": {"points": {"s1": "20", "s2": "-100"}}}
    assert notes == ["Колонка AM без названия",
                     "Команда «Дуб» из рабочей книги не найдена среди заявок зачёта — её баллы не перенесены"]
    assert replace(teams[0], number="7").number == "7"
