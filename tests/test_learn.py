"""Учебный режим (Правки, п. 53): «Заполнить примером» у форм и заглушки документов. Все данные — выдуманные."""

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from test_web import FormFields, base, lesoviki

from st_secretary import staff
from st_secretary.issues import ERROR
from st_secretary.web.app import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "данные", opener=lambda p: None, docs_dir=tmp_path / "документы",
                                 board_host="127.0.0.1"))


def test_toggle_and_example_buttons_on_forms(client, tmp_path, psr_card):
    """Галочка «Учебный режим» — в шапке; кнопки примера — у форм, скрыты, пока галочка не стоит (класс learn-only)."""
    assert "data-learn-toggle" in client.get("/").text and "Учебный режим" in client.get("/").text
    assert 'data-example="/example/card"' in client.get("/new").text
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    b = base(f)
    pages = {"/card/edit": 'data-example="/example/card" data-example-confirm',
             "/preapps/new": f'data-example="{b}/example/preapp">',
             "/preapps/edit?file=%D0%9B%D0%B5%D1%81%D0%BE%D0%B2%D0%B8%D0%BA%D0%B8.xlsx": "data-example-confirm",
             "/preapps/team?file=%D0%9B%D0%B5%D1%81%D0%BE%D0%B2%D0%B8%D0%BA%D0%B8.xlsx": f'action="{b}/docs/stubs"'}
    for path, mark in pages.items():
        page = client.get(b + path).text
        assert mark in page, path
        assert 'class="learn-only"' in page, path


def test_card_example_fills_a_card_without_errors(client, psr_card):
    """Пример карточки (учебная) — в форме «Нового соревнования» и в «Редактировании карточки»: сохраняется без ошибок."""
    ex = client.get("/example/card").json()
    assert ex["rows"]["z-rows"] == 2 and ex["clear"] == ["g", "z"] and "z-0-zid" not in ex["fields"]
    main = {k: v for k, v in ex["fields"].items() if not k.startswith(("g-", "z-"))}
    r = client.post("/new", data=main, follow_redirects=False)
    assert r.status_code == 303
    edit = client.get(r.headers["location"])
    data = FormFields(edit.text, "cardform").fields | ex["fields"]  # как fillExample: строки — из примера
    r = client.post(r.headers["location"].split("?")[0], data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].split("?")[0].endswith("/card")
    f = client.app.state.store.all()[0]
    comp = f.load()
    assert [z.key for z in comp.zachety] == ["М/Ж_3", "М/Ж_2"] and comp.official("Главный судья").category
    assert not [i for i in comp.check() if i.severity == ERROR]


def test_preapp_example_saves_without_errors(client, tmp_path, psr_card):
    """Пример заявки — команда, которой ещё нет, под зачёт этой карточки: сохраняется без ошибок."""
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    ex = client.get(base(f) + "/example/preapp").json()
    assert ex["fields"]["h-team"] != "Лесовики" and ex["rows"]["people-rows"] >= 3
    page = client.get(base(f) + "/preapps/new")
    data = {k: v for k, v in FormFields(page.text, "preappform").fields.items() if not k.startswith("p-")}
    r = client.post(base(f) + "/preapps/save", data=data | ex["fields"], follow_redirects=False)
    assert r.status_code == 303 and "done=pcreated" in r.headers["location"]
    result, _ = client.app.state.store.review(f, psr_card)
    name = ex["fields"]["h-team"] + ".xlsx"
    assert [i.text for i in result.issues if i.source == name and i.severity == ERROR] == []


def test_person_example_passes_checks(client, psr_card):
    """Пример личных данных: всё выдуманное, контрольные цифры ИНН, СНИЛС и счёта верные."""
    f = client.app.state.store.create(psr_card)
    p = client.get(base(f) + "/example/person").json()["fields"]
    assert staff.inn_ok(p["inn"]) and staff.snils_ok(p["snils"]) and staff.account_ok(p["account"], p["bik"])
    assert "пример" in p["bank"] and "пример" in p["issued_by"]


def test_stub_documents_for_team(client, tmp_path, psr_card):
    """Заглушки документов команды: SVG с «ОБРАЗЕЦ — НЕ ДОКУМЕНТ» в папке команды (на этом компьютере); открываются
    без скриптов; второй раз — ничего не дублирует."""
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    r = client.post(base(f) + "/docs/stubs", data={"file": "Лесовики.xlsx", "back": ""}, follow_redirects=False)
    assert r.status_code == 303 and "done=docs_stubs" in r.headers["location"] and "made=" in r.headers["location"]
    docs = client.app.state.store.team_docs(f, "Лесовики.xlsx")
    names = [p.name for p in docs]
    assert "Заявка с допуском врача.svg" in names and any(n.startswith("Паспорт — ") for n in names)
    assert all(p.suffix == ".svg" and "ОБРАЗЕЦ — НЕ ДОКУМЕНТ" in p.read_text(encoding="utf-8") for p in docs)
    assert str(tmp_path / "документы") in str(docs[0])  # не в папке соревнования и не в облаке
    view = client.get(base(f) + "/docs/view", params={"file": "Лесовики.xlsx", "name": names[0]})
    assert view.status_code == 200 and view.headers["content-security-policy"] == "sandbox"
    assert view.headers["content-type"].startswith("image/svg+xml")
    page = client.get(r.headers["location"]).text
    assert "Добавлено заглушек документов: " + str(len(docs)) in page
    r = client.post(base(f) + "/docs/stubs", data={"file": "Лесовики.xlsx", "back": ""}, follow_redirects=False)
    assert "made=0" in r.headers["location"]
