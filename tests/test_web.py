"""Интерфейс в браузере: страницы открываются, карточка сохраняется без потерь, заявки проверяются."""

import io
import os
import re
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from st_secretary.importers.card_xlsx import load_card, write_card  # noqa: E402
from st_secretary.web.app import create_app  # noqa: E402
from st_secretary.web.review import issue_key  # noqa: E402

from conftest import make_application  # noqa: E402

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

MAIN = {"title": "Чемпионат города N по спортивному туризму", "kind": "Чемпионат", "level": "MUNICIPAL",
        "date_from": "2025-09-20", "date_to": "2025-09-21", "place": "окрестности г. N",
        "host_territory": "Красноярск", "organizers": "Федерация спортивного туризма", "calendar_number": "",
        "norms_edition": "2022-2025", "percent_method": "POINTS_RELATIVE_TO_WINNER", "preapp_deadline": "2025-09-17"}


class FormFields(HTMLParser):
    """Поля формы так, как их отправит браузер (содержимое <template> — заготовки строк — не отправляется)."""

    def __init__(self, html: str, form_id: str):
        super().__init__()
        self.form_id, self.inside, self.tpl = form_id, False, 0
        self.fields: dict[str, str] = {}
        self._select = self._textarea = None
        self._first = None
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == self.form_id:
            self.inside = True
        if tag == "template":
            self.tpl += 1
        if not self.inside or self.tpl:
            return
        if tag == "input" and a.get("name") and a.get("type") not in ("file", "submit"):
            self.fields[a["name"]] = a.get("value") or ""
        elif tag == "select":
            self._select, self._first = a.get("name"), None
        elif tag == "option" and self._select:
            if self._first is None:
                self._first = a.get("value", "")
                self.fields[self._select] = self._first
            if "selected" in a:
                self.fields[self._select] = a.get("value", "")
        elif tag == "textarea":
            self._textarea = a.get("name")
            self.fields[self._textarea] = ""

    def handle_endtag(self, tag):
        if tag == "template":
            self.tpl -= 1
        elif tag == "form":
            self.inside = False
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._textarea and self.inside and not self.tpl:
            self.fields[self._textarea] += data


@pytest.fixture
def opened():
    return []


@pytest.fixture
def client(tmp_path, opened):
    return TestClient(create_app(tmp_path / "данные", opener=opened.append))


def base(folder) -> str:
    return "/c/" + quote(folder.id, safe="")


def test_home_and_health(client):
    r = client.get("/")
    assert r.status_code == 200 and "Пока нет ни одного соревнования" in r.text
    assert client.get("/health").json()["app"] == "st-secretary"
    assert client.get("/static/style.css").status_code == 200


def test_new_competition_then_fill_card(client, tmp_path, psr_card):
    r = client.post("/new", data=MAIN, follow_redirects=False)
    assert r.status_code == 303
    card_url = r.headers["location"].split("?")[0]
    page = client.get(r.headers["location"])
    assert "Соревнование создано" in page.text and "Главный секретарь" in page.text
    folder = tmp_path / "данные" / "2025-09-20 Чемпионат города N по спортивному туризму"
    assert (folder / "Карточка_соревнования.xlsx").is_file() and (folder / "Предзаявки").is_dir()

    data = FormFields(page.text, "cardform").fields
    roles = {v: k.split("-")[1] for k, v in data.items() if k.endswith("-role")}
    js, gs = roles["Главный судья"], roles["Главный секретарь"]
    data |= {f"g-{js}-fio": "Судьин Иван Петрович", f"g-{js}-category": "СС1К", f"g-{js}-territory": "г. Красноярск",
             f"g-{gs}-fio": "Секретарёва Анна Ивановна", f"g-{gs}-category": "СС2К", f"g-{gs}-territory": "г. Красноярск",
             "z-0-group": "М/Ж", "z-0-distance_class": "3", "z-0-discipline_code": "0840161811Я", "z-0-age_from": "22",
             "z-0-age_from_by_gsk": "16", "z-0-team_size": "3", "z-0-min_men": "1", "z-0-min_women": "1",
             "z-0-fee": "3000"}
    assert card_url.endswith("/card/edit")  # новое соревнование — сразу в форму карточки
    r = client.post(card_url, data=data, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].split("?")[0].endswith("/card")
    assert load_card(folder / "Карточка_соревнования.xlsx") == psr_card
    view = client.get(r.headers["location"]).text  # после сохранения — карточка для просмотра
    assert "Карточка заполнена, замечаний нет" in view and "Карточка сохранена" in view
    assert 'id="cardform"' not in view and "Редактировать карточку" in view
    assert "И.П. Судьин, СС1К, г. Красноярск" in view and "М/Ж_3" in view and "с 16 лет — по решению ГСК" in view


def test_card_page_roundtrip_loses_nothing(client, psr_card):
    """Открыли карточку и нажали «Сохранить», ничего не меняя, — файл тот же по содержанию."""
    psr_card.officials.append(replace(psr_card.officials[0], role="Судья-информатор", fio="Инфо Олег Олегович",
                                      category="судья без категории"))  # категория не из списка — не теряется
    f = client.app.state.store.create(psr_card)
    url = base(f) + "/card/edit"
    page = client.get(url).text
    assert "(не из списка)" in page
    r = client.post(url, data=FormFields(page, "cardform").fields, follow_redirects=False)
    assert r.status_code == 303
    assert load_card(f.card_path) == psr_card


def test_errors_shown_next_to_fields_and_input_kept(client):
    r = client.post("/new", data=MAIN | {"title": "", "date_to": "2025-09-19"})
    assert r.status_code == 422
    assert "обязательное поле" in r.text and "дата окончания раньше даты начала" in r.text
    assert 'value="окрестности г. N"' in r.text  # введённое не пропало
    assert client.app.state.store.all() == []


def test_card_changed_in_excel_is_not_overwritten(client, psr_card):
    f = client.app.state.store.create(psr_card)
    url = base(f) + "/card/edit"
    data = FormFields(client.get(url).text, "cardform").fields | {"place": "моё место"}
    write_card(f.card_path, replace(psr_card, place="место из Excel"))  # кто-то поправил в Excel
    os.utime(f.card_path, ns=(10**18, 10**18))
    r = client.post(url, data=data)
    assert r.status_code == 409 and "изменили в Excel" in r.text
    assert load_card(f.card_path).place == "место из Excel"
    r = client.post(url, data=data | {"force": "1"}, follow_redirects=False)
    assert r.status_code == 303 and load_card(f.card_path).place == "моё место"


def test_zachet_field_errors(client, psr_card):
    f = client.app.state.store.create(psr_card)
    url = base(f) + "/card/edit"
    data = FormFields(client.get(url).text, "cardform").fields | {"z-0-distance_class": "", "z-0-fee": "три тысячи"}
    r = client.post(url, data=data)
    assert r.status_code == 422 and "нужно целое число" in r.text
    assert load_card(f.card_path) == psr_card


def lesoviki(tmp_path):
    return make_application(tmp_path / "Лесовики.xlsx", "Лесовики", "Красноярск", "Иванов Пётр Сергеевич",
                            "89131234567, les@mail.ru", 3, [
                                ["Лесовики", "Красноярск", "Иванов Пётр Сергеевич", "Иванов Пётр Сергеевич",
                                 "17.10.1989", "II", "м", "М/Ж", 3],
                                ["Лесовики", "Красноярск", "Иванов Пётр Сергеевич", "Смирнова Анна Олеговна",
                                 "12.03.2001", "КМС", "ж", "М/Ж", 3],
                                ["Лесовики", "Красноярск", "Иванов Пётр Сергеевич", "Кузьмин Олег Игоревич",
                                 "29.02.1995", "б/р", "м", "М/Ж", 3],
                            ]).read_bytes()


def test_preapps_upload_check_summary_remove(client, tmp_path, psr_card, opened):
    f = client.app.state.store.create(psr_card)
    b = base(f)
    data = lesoviki(tmp_path)
    r = client.post(b + "/preapps/upload", files=[("files", ("Лесовики.xlsx", data, XLSX)),
                                                  ("files", ("заметки.txt", b"x", "text/plain"))])
    assert "добавлено заявок: 1" in r.text and "пропущено файлов не Excel: 1" in r.text
    assert "такой даты не существует" in r.text  # 29.02.1995 — ошибка видна
    assert "+7 913 123-45-67" in r.text  # телефон представителя — рядом с замечаниями
    assert re.search(r'<b>1</b><span>ошибок', r.text)

    r = client.post(b + "/preapps/summary", follow_redirects=False)
    assert r.status_code == 303 and opened == [f.summary_path] and f.summary_path.is_file()
    r = client.get(b + "/preapps/summary.xlsx")
    assert r.status_code == 200 and r.content[:2] == b"PK"

    r = client.post(b + "/preapps/upload", files=[("files", ("Лесовики.xlsx", data, XLSX))])
    assert "заменено исправленными: 1" in r.text
    assert len(list((f.preapp_dir / "Прежние версии").iterdir())) == 1  # прежний вариант не затёрт

    r = client.post(b + "/preapps/remove", data={"name": "Лесовики.xlsx"})
    assert "убрана из обработки" in r.text
    assert not (f.preapp_dir / "Лесовики.xlsx").exists()
    assert (f.preapp_dir / "Убранные" / "Лесовики.xlsx").is_file()  # не удалена, а перенесена


def test_issue_explains_why_and_links_to_form(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    r = client.get(base(f) + "/preapps")
    assert "Почему:" in r.text and "1995 год не високосный" in r.text and "Что сделать:" in r.text
    assert "строка 12 в файле" in r.text
    assert "/preapps/edit?file=%D0%9B%D0%B5%D1%81%D0%BE%D0%B2%D0%B8%D0%BA%D0%B8.xlsx#p-12" in r.text
    assert 'class="team-link" href="' + base(f) + "/preapps/team?file=" in r.text  # название команды — ссылка


def preapp_url(f, name="Лесовики.xlsx", page="edit"):
    return base(f) + f"/preapps/{page}?file=" + quote(name)


def test_edit_application_in_form(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    page = client.get(preapp_url(f))
    assert page.status_code == 200 and "Редактирование заявки «Лесовики»" in page.text
    data = FormFields(page.text, "preappform").fields
    assert data["h-team"] == "Лесовики" and data["p-2-birth"] == "29.02.1995" and data["p-2-zachet"] == "М/Ж_3"
    assert data["p-0-birth"] == "17.10.1989" and data["p-0-qual"] == "II" and data["p-1-sex"] == "ж"
    assert 'id="p-12"' in page.text and "mk-error" in page.text  # строка с ошибкой отмечена

    r = client.post(base(f) + "/preapps/save", data=data | {"p-2-birth": "28.02.1995"}, follow_redirects=False)
    assert r.status_code == 303 and "/preapps/team?" in r.headers["location"] and "done=psaved" in r.headers["location"]
    page = client.get(r.headers["location"])  # после сохранения — карточка команды
    assert "Карточка команды" in page.text and "Прежние версии" in page.text and "Ошибок нет" in page.text
    [old] = (f.preapp_dir / "Прежние версии").iterdir()  # файл команды не пропал
    assert old.name.startswith("Лесовики (") and [p.name for p in f.preapp_files()] == ["Лесовики.xlsx"]
    result = client.app.state.store.preapps(f, psr_card)
    assert result.count("error") == 0 and len(result.entries) == 3
    assert [e.name.full for e in result.entries] == ["Иванов Пётр Сергеевич", "Смирнова Анна Олеговна",
                                                    "Кузьмин Олег Игоревич"]

    # удалить участника, добавить нового
    data = FormFields(client.get(preapp_url(f)).text, "preappform").fields
    data = {k: v for k, v in data.items() if not k.startswith("p-1-")}
    data |= {"p-7-fio": "Орлова Мария Ивановна", "p-7-birth": "01.02.2000", "p-7-qual": "III", "p-7-sex": "ж",
             "p-7-zachet": "М/Ж_3", "p-7-team_dist": "1"}
    client.post(base(f) + "/preapps/save", data=data)
    names = [e.name.full for e in client.app.state.store.preapps(f, psr_card).entries]
    assert names == ["Иванов Пётр Сергеевич", "Кузьмин Олег Игоревич", "Орлова Мария Ивановна"]


def test_unchanged_form_save_keeps_application(client, tmp_path, psr_card):
    """Открыли заявку и сохранили, ничего не меняя, — участники, даты, разряды, зачёты те же."""
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    store = client.app.state.store

    def snapshot():
        r = store.preapps(f, psr_card)
        return [(e.name.full, e.birth, e.qual, e.sex, e.zachet, e.team_dist) for e in r.entries], \
            [(t.team, t.territory, t.representative, t.phone, t.email) for t in r.teams], r.count("error")

    before = snapshot()
    data = FormFields(client.get(preapp_url(f)).text, "preappform").fields
    assert client.post(base(f) + "/preapps/save", data=data, follow_redirects=False).status_code == 303
    assert snapshot() == before


def test_new_application_and_form_errors(client, psr_card):
    f = client.app.state.store.create(psr_card)
    page = client.get(base(f) + "/preapps/new")
    assert page.status_code == 200 and "Новая заявка" in page.text
    data = FormFields(page.text, "preappform").fields
    r = client.post(base(f) + "/preapps/save", data=data)
    assert r.status_code == 422 and "Впишите название команды" in r.text
    data |= {"h-team": "Ураган", "h-territory": "Красноярск", "p-0-birth": "01.01.1990"}
    r = client.post(base(f) + "/preapps/save", data=data)
    assert r.status_code == 422 and "Впишите ФИО или удалите строку" in r.text and 'value="Ураган"' in r.text
    data |= {"p-0-fio": "Петров Иван Ильич", "p-0-sex": "м", "p-0-zachet": "М/Ж_3"}
    r = client.post(base(f) + "/preapps/save", data=data, follow_redirects=False)
    assert r.status_code == 303 and "done=pcreated" in r.headers["location"]
    assert [p.name for p in f.preapp_files()] == ["Ураган.xlsx"]
    page = client.get(r.headers["location"])
    assert "Заявка сохранена в файл «Ураган.xlsx»" in page.text and "Карточка команды" in page.text


def test_application_changed_meanwhile_is_not_overwritten(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    data = FormFields(client.get(preapp_url(f)).text, "preappform").fields | {"h-team": "Лесовики-2"}
    os.utime(f.preapp_dir / "Лесовики.xlsx", ns=(10**18, 10**18))  # команда прислала новую версию
    r = client.post(base(f) + "/preapps/save", data=data)
    assert r.status_code == 409 and "Файл заявки изменился" in r.text
    r = client.post(base(f) + "/preapps/save", data=data | {"force": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.app.state.store.preapps(f, psr_card).teams[0].team == "Лесовики-2"


def sosna(tmp_path):
    """Заявка без ошибок, но с «проверить»: участнику 16 лет — допуск только по решению ГСК."""
    return make_application(tmp_path / "Сосна.xlsx", "Сосна", "Красноярск", "Орлов Павел Ильич",
                            "89137654321", 3, [
                                ["Сосна", "Красноярск", "Орлов Павел Ильич", "Орлов Павел Ильич",
                                 "11.05.1985", "II", "м", "М/Ж", 3],
                                ["Сосна", "Красноярск", "Орлов Павел Ильич", "Белова Ирина Петровна",
                                 "03.07.1999", "КМС", "ж", "М/Ж", 3],
                                ["Сосна", "Красноярск", "Орлов Павел Ильич", "Пестов Юрий Андреевич",
                                 "01.01.2009", "б/р", "м", "М/Ж", 3],
                            ]).read_bytes()


def test_team_card_is_read_only_with_edit_button(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    page = client.get(preapp_url(f, page="team"))
    assert page.status_code == 200 and "Карточка команды" in page.text and "Лесовики" in page.text
    assert 'name="p-0-fio"' not in page.text and "Кузьмин Олег Игоревич" in page.text  # только просмотр
    assert "Редактировать заявку" in page.text and preapp_url(f).replace(" ", "%20") in page.text
    assert "status-opt-fix\" aria-pressed=\"true\"" in page.text  # есть ошибки → «Исправить» сам
    assert "1995 год не високосный" in page.text
    people = client.get(base(f) + "/preapps").text.split('id="panel-people"')[1].split("</section>")[0]
    assert "Кузьмин Олег Игоревич" in people and "<a " not in people  # ФИО в списке участников — без ссылок


def test_check_marks_and_statuses(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Сосна.xlsx", sosna(tmp_path))
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    store = client.app.state.store
    card = preapp_url(f, "Сосна.xlsx", "team")

    result, reviews = store.review(f, psr_card)
    [warn] = [i for i in result.issues if i.source == "Сосна.xlsx" and i.severity == "warning"]
    assert reviews["Сосна.xlsx"].status == "check" and reviews["Лесовики.xlsx"].status == "fix"
    lst = client.get(base(f) + "/preapps").text
    assert re.search(r'data-team-filter="fix"[^>]*>Ошибки <b>1</b>', lst)
    assert re.search(r'data-team-filter="check"[^>]*>Проверить <b>1</b>', lst)

    # «Проверено» у замечания: оно больше не в «Проверить»
    r = client.post(base(f) + "/preapps/check", data={"file": "Сосна.xlsx", "key": issue_key(warn), "on": "1",
                                                     "back": base(f) + "/preapps#t-2"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("done=checked#t-2")
    result, _ = store.review(f, psr_card)
    assert result.count("warning") == 0 and result.count("checked") == 1
    assert "Проверено замечаний: 1" in client.get(base(f) + "/preapps").text
    client.post(base(f) + "/preapps/check", data={"file": "Сосна.xlsx", "key": issue_key(warn), "on": "0"})
    assert store.review(f, psr_card)[0].count("warning") == 1

    # статус «Проверено» отмечает и оставшиеся «проверить»; с ошибками — нельзя
    r = client.post(base(f) + "/preapps/status", data={"file": "Сосна.xlsx", "status": "done"}, follow_redirects=False)
    assert r.status_code == 303 and "/preapps/team?" in r.headers["location"]
    result, reviews = store.review(f, psr_card)
    assert reviews["Сосна.xlsx"].status == "done" and reviews["Сосна.xlsx"].manual and result.count("warning") == 0
    assert "Выставлен вручную" in client.get(card).text
    assert client.post(base(f) + "/preapps/status", data={"file": "Лесовики.xlsx", "status": "done"}).status_code == 409
    client.post(base(f) + "/preapps/status", data={"file": "Лесовики.xlsx", "status": "check"})
    assert store.review(f, psr_card)[1]["Лесовики.xlsx"].status == "check"  # вручную можно и «Проверить»

    # заявку изменили — «Проверено» снимается, а отметка у того же замечания остаётся
    data = FormFields(client.get(preapp_url(f, "Сосна.xlsx")).text, "preappform").fields
    client.post(base(f) + "/preapps/save", data=data | {"h-representative": "Петров Иван Ильич"})
    result, reviews = store.review(f, psr_card)
    assert reviews["Сосна.xlsx"].status == "check" and not reviews["Сосна.xlsx"].manual
    assert "снят: заявку с тех пор изменили" in reviews["Сосна.xlsx"].reset
    assert result.count("checked") == 1  # то же замечание — отметка «проверено» сохранилась

    # статус — в сводке Excel
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(client.get(base(f) + "/preapps/summary.xlsx").content))
    teams = {row[1]: row[12] for row in wb["Команды"].iter_rows(min_row=2, values_only=True) if row[1]}
    assert teams["Лесовики"] == "Проверить" and teams["Сосна"] == "Проверить"


def test_preapps_wait_for_card_without_errors(client, tmp_path, psr_card):
    psr_card.zachety = []
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    r = client.get(base(f) + "/preapps")
    assert "Сначала исправьте карточку" in r.text and "Лесовики.xlsx" in r.text
    assert client.post(base(f) + "/preapps/summary").status_code == 409
    assert client.get(preapp_url(f)).status_code == 409


def test_overview_steps_and_errors(client, psr_card, opened):
    f = client.app.state.store.create(psr_card)
    r = client.get(base(f))
    assert "Дальше по порядку" in r.text and "Комиссия по допуску" in r.text and "И.П. Судьин, СС1К" in r.text
    assert "Протокол комиссии по допуску" in client.get(base(f) + "/step/admission").text
    assert client.get(base(f) + "/step/card").status_code == 404  # готовый шаг открывается не здесь
    assert client.get("/c/нет такого").status_code == 404
    assert "Страница не найдена" in client.get("/c/..%2F..%2Fsecret").text
    client.post(base(f) + "/open/folder")
    assert opened == [f.path]


def test_import_card_from_excel(client, tmp_path, psr_card):
    card = write_card(tmp_path / "card.xlsx", psr_card)
    r = client.post("/import", files={"card": ("Карточка.xlsx", card.read_bytes(), XLSX)})
    assert "Карточка загружена из Excel" in r.text and psr_card.title in r.text
    [f] = client.app.state.store.all()
    assert load_card(f.card_path) == psr_card
    r = client.post("/import", files={"card": ("заявка.xlsx", b"not an excel file", XLSX)})
    assert r.status_code == 422 and "не похож на карточку" in r.text
