"""Проверка снаряжения ПСР: перечень, штрафные баллы, порог снятия, связь с допуском."""

from st_secretary import commission as cm
from st_secretary import equipment as eq
from st_secretary.importers.preapp_xlsx import read_preapplication
from st_secretary.preapp import process

from conftest import make_application


def result(tmp_path, psr_card):
    a = make_application(tmp_path / "Кедр.xlsx", "Кедр", "Красноярск", "Лебедев Антон Игоревич", "89135550000", 3, [
        ["Кедр", "Красноярск", "Лебедев Антон Игоревич", "Лебедев Антон Игоревич", "02.02.1990", "I", "м", "М/Ж", 3],
        ["Кедр", "Красноярск", "Лебедев Антон Игоревич", "Зуева Мария Олеговна", "05.06.1996", "II", "ж", "М/Ж", 3],
        ["Кедр", "Красноярск", "Лебедев Антон Игоревич", "Носов Глеб Андреевич", "09.09.1993", "б/р", "м", "М/Ж", 3],
    ])
    return process([read_preapplication(a)], psr_card)


def key_of(e):
    return cm.person_key(e.name.full)


def everything(r, s):
    """Всё снаряжение на месте — как после «Начать проверку: всё есть»."""
    items = s["settings"]["items"]
    return {"group": {i["id"]: "all" for i in items if i["kind"] == eq.GROUP},
            "people": {key_of(e): {i["id"]: "all" for i in items if i["kind"] != eq.GROUP} for e in r.teams[0].entries}}


def test_template_and_rules_defaults():
    s = eq.template_settings(eq.PSR_KRSK_2025)
    kinds = [i["kind"] for i in s["items"]]
    assert (kinds.count(eq.PERSONAL), kinds.count(eq.GROUP), kinds.count(eq.SPECIAL)) == (13, 14, 8)
    assert s["penalty"] == {eq.PERSONAL: 1, eq.GROUP: 3, eq.SPECIAL: 1} and s["limit"] == 10
    rules = eq.settings({})
    assert rules["penalty"][eq.GROUP] == 5 and rules["limit"] == 30 and rules["items"] == []  # по Правилам


def test_points_and_verdict(tmp_path, psr_card):
    r = result(tmp_path, psr_card)
    data = {"settings": eq.template_settings(eq.PSR_KRSK_2025)}
    [g] = eq.evaluate(r, ["Кедр.xlsx"], data, key_of)
    assert not g.checked and eq.verdict(g, 10) == "не проверено"

    data["teams"] = {"Кедр.xlsx": everything(r, data)}
    [g] = eq.evaluate(r, ["Кедр.xlsx"], data, key_of)
    assert g.total == 0 and eq.verdict(g, 10) == "допущена"

    ids = {i["name"]: i["id"] for i in data["settings"]["items"]}
    team = data["teams"]["Кедр.xlsx"]
    team["group"][ids["Карабин с муфтой"]] = 4  # из 6
    team["people"][cm.person_key("Зуева Мария Олеговна")][ids["Нож"]] = 0
    team["people"][cm.person_key("Зуева Мария Олеговна")][ids["Защитная каска"]] = 0
    [g] = eq.evaluate(r, ["Кедр.xlsx"], data, key_of)
    assert g.points == {eq.PERSONAL: 1, eq.GROUP: 6, eq.SPECIAL: 1} and g.total == 8  # 2 карабина × 3 балла
    assert eq.verdict(g, 10) == "допущена"
    assert eq.missing_text(g) == "Карабин с муфтой ×2; Зуева Мария Олеговна — нож, защитная каска"
    team["group"][ids["Котелок"]] = 0
    [g] = eq.evaluate(r, ["Кедр.xlsx"], data, key_of)
    assert g.total == 11 and eq.verdict(g, 10).startswith("снятие: 11 баллов")


def test_equipment_blocks_admission(tmp_path, psr_card):
    r = result(tmp_path, psr_card)
    gdata = {"settings": eq.template_settings(eq.PSR_KRSK_2025)}
    adm = {"teams": {"Кедр.xlsx": {"team_docs": {"app": True, "doctor": True},
                                   "people": {key_of(e): {"docs": {d: True for d in ("id", "med", "book", "oms", "ins")}}
                                              for e in r.teams[0].entries}}}}

    def team():
        gear = eq.evaluate(r, ["Кедр.xlsx"], gdata, key_of)
        return cm.evaluate(r, ["Кедр.xlsx"], psr_card, adm, eq.admission_problems(gear, gdata))[0]

    t = team()
    assert t.status == cm.PENDING and "снаряжение не проверено" in t.problems
    gdata["teams"] = {"Кедр.xlsx": everything(r, gdata)}
    assert team().status == cm.ADMITTED
    gdata["teams"]["Кедр.xlsx"]["group"] = {i["id"]: 0 for i in gdata["settings"]["items"] if i["kind"] == eq.GROUP}
    t = team()
    assert t.status == cm.PENDING and any("по Правилам снятие" in p for p in t.problems)
    assert "по Правилам снятие" in cm.protocol_row(t, psr_card)["remarks"]
    assert eq.admission_problems([], {}) == {}  # перечень не задан — проверка не требуется
