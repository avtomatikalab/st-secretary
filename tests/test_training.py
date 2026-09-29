"""Учебное соревнование: выдуманные данные, заданные этапы, специально оставленные ошибки в заявках."""

from datetime import date

import pytest

from st_secretary import training
from st_secretary.issues import ERROR, FIXED
from st_secretary.web.store import Store


@pytest.fixture
def made(tmp_path):
    store = Store(tmp_path / "данные", tmp_path / "документы")
    return store, training.create(store, date(2026, 9, 30))


def test_card_is_valid_and_next_weekend(made):
    _, f = made
    comp = f.load()
    assert comp.title == training.TITLE and comp.check() == []
    assert (comp.date_from, comp.date_to) == (date(2026, 10, 3), date(2026, 10, 4))  # ближайшие сб и вс
    assert [z.key for z in comp.zachety] == ["М/Ж_3", "М/Ж_2"] and comp.norms_edition == "2026-2029"


def test_applications_have_exactly_the_planted_mistakes(made):
    store, f = made
    result, _ = store.review(f, f.load())
    assert len(result.teams) == 14 and sum(len(t.entries) for t in result.teams) == 56
    errors = sorted((i.source, i.text) for i in result.issues if i.severity == ERROR)
    assert {s for s, _ in errors} == {"Бурундуки.xlsx", "Перевал.xlsx", "Пихта.xlsx", "Ёлки-палки.xlsx"}
    assert len(errors) == 4
    assert any("31.02.1998" in t for _, t in errors) and any("женщин в команде 0" in t for _, t in errors)
    fixed = {i.source for i in result.issues if i.severity == FIXED}
    assert {"Сосна.xlsx", "Горный ветер.xlsx"} <= fixed
    names = [e.name.full for t in result.teams for e in t.entries]
    assert len(set(names)) == 55  # один человек специально в двух командах


def test_stages_ready_for_points(made):
    _, f = made
    z = f.run_data()["zachety"]
    assert len(z["М/Ж_3"]["stages"]) == 8 and z["М/Ж_3"]["stages"][-1]["tour"] == "Бонус"
    assert len(z["М/Ж_2"]["stages"]) == 4 and z["М/Ж_2"]["kv"] == "90"


def test_same_every_time():
    comp = training.card(date(2026, 9, 30))
    assert training.applications(comp) == training.applications(comp)
