"""Страницы карточки соревнования: подписи, примеры, подсказки, ссылки (Правки, п. 56–65). Данные — выдуманные."""

from dataclasses import replace

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from test_web import base

from st_secretary.issues import ERROR
from st_secretary.web.app import create_app
from st_secretary.web.forms import card_to_form, form_from_data, form_to_card


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "данные", opener=lambda p: None, docs_dir=tmp_path / "документы",
                                 board_host="127.0.0.1"))


def test_one_class_label_is_plain(client, psr_card):
    """П. 56: галочка «один класс» подписана понятно — и в редактировании, и в просмотре карточки."""
    f = client.app.state.store.create(replace(psr_card, one_class=True))
    edit = client.get(base(f) + "/card/edit").text
    assert "Один человек — только в одном классе" in edit and "нельзя сразу во 2 и 3 кл." in edit
    assert "так бывает в Положении или ИБ" in edit
    view = client.get(base(f) + "/card").text
    assert "Один человек — только в одном классе" in view and "проверка заявок покажет ошибку" in view


def test_new_competition_from_example_is_full(client):
    """П. 57: «Новое соревнование» по примеру (учебный режим) — сразу с ГСК и зачётами учебного соревнования."""
    ex = client.get("/example/card").json()["fields"]
    assert ex["learn_full"] == "1" and 'name="learn_full"' in client.get("/new").text
    main = {k: v for k, v in ex.items() if not k.startswith(("g-", "z-"))}
    r = client.post("/new", data=main, follow_redirects=False)
    assert r.status_code == 303
    comp = client.app.state.store.all()[0].load()
    assert [z.key for z in comp.zachety] == ["М/Ж_3", "М/Ж_2"] and comp.official("Главный судья").fio
    assert not [i for i in comp.check() if i.severity == ERROR]
    r = client.post("/new", data=main | {"learn_full": ""}, follow_redirects=False)  # без галочки — как раньше
    assert r.status_code == 303 and len(client.app.state.store.all()) == 2


def test_section_examples_gsk_and_zachet(client, psr_card):
    """П. 57: в разделах «ГСК» и «Зачёты» — свои «Заполнить примером»; ГСК меняет только g-поля (спросит, если судьи
    уже вписаны), зачёт — добавляется к существующим: первый учебный, которого ещё нет (группа_класс)."""
    f = client.app.state.store.create(psr_card)
    edit = client.get(base(f) + "/card/edit").text
    assert 'data-example="/example/card?part=gsk" data-example-confirm="g-fio"' in edit
    assert 'data-example="/example/card?part=zachet"' in edit
    gsk = client.get("/example/card?part=gsk").json()
    assert gsk["clear"] == ["g"] and gsk["fields"] and all(k.startswith("g-") for k in gsk["fields"])
    z = client.get("/example/card?part=zachet").json()
    assert z["fields"] == {} and z["append"]["box"] == "z-rows" and z["append"]["key"] == ["group", "distance_class"]
    keys = [(r["group"], r["distance_class"]) for r in z["append"]["rows"]]
    assert len(set(keys)) == len(keys) >= 6 and all("zid" not in r for r in z["append"]["rows"])
    # любой учебный зачёт ложится в карточку без ошибок (как его вставит app.js — в новую строку формы)
    rows = card_to_form(psr_card)["zachety"]
    for r in z["append"]["rows"]:
        if (r["group"], r["distance_class"]) in {(x["group"], x["distance_class"]) for x in rows}:
            continue
        data = {}
        for i, row in enumerate(rows + [r | {"zid": ""}]):
            data |= {f"z-{i}-{k}": v for k, v in row.items()}
        form = card_to_form(psr_card)
        for i, o in enumerate(form["officials"]):
            data |= {f"g-{i}-{k}": v for k, v in o.items() if k != "own"}
        comp, errors = form_to_card(form_from_data(data | form["main"]))
        assert not errors, (r, errors)
        assert len(comp.zachety) == len(rows) + 1 and not [i for i in comp.check() if i.severity == ERROR], r
