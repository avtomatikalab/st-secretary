"""Интерфейс в браузере: страницы открываются, карточка сохраняется без потерь, заявки проверяются."""

import io
import os
import re
from dataclasses import replace
from html.parser import HTMLParser
from urllib.parse import quote, urlencode

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
        if tag == "input" and a.get("type") == "checkbox":
            if a.get("name") and "checked" in a:  # браузер отправляет только отмеченные галочки
                self.fields[a["name"]] = a.get("value") or "on"
        elif tag == "input" and a.get("name") and a.get("type") not in ("file", "submit"):
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
    return TestClient(create_app(tmp_path / "данные", opener=opened.append, docs_dir=tmp_path / "документы"))


def base(folder) -> str:
    return "/c/" + quote(folder.id, safe="")


def test_home_and_health(client):
    r = client.get("/")
    assert r.status_code == 200 and "Пока нет ни одного соревнования" in r.text
    assert client.get("/health").json()["app"] == "st-secretary"
    assert client.get("/static/style.css").status_code == 200
    assert 'action="/shutdown"' not in r.text  # запущено не из окна программы — кнопки «Выключить» нет
    assert client.post("/shutdown").status_code == 409


def test_shutdown_button_stops_server_after_answering(tmp_path):
    stopped = []
    client = TestClient(create_app(tmp_path / "данные", opener=lambda p: None, shutdown=lambda: stopped.append(1)))
    assert 'action="/shutdown"' in client.get("/").text
    r = client.post("/shutdown")
    assert r.status_code == 200 and "СТ-Секретарь выключен" in r.text and 'action="/shutdown"' not in r.text
    assert stopped == [1]  # сервер останавливается после того, как страница отдана


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
    people = r.text.split('class="team-people"')[1].split("</div>")[0]  # в списке команд — все участники
    assert all(n in people for n in ("Иванов Пётр Сергеевич", "Смирнова Анна Олеговна", "Кузьмин Олег Игоревич"))
    assert 'sev-error"' in people  # у кого ошибка — выделен
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
    assert "Карточка команды" in page.text and "Прежние версии" in page.text
    assert "Программа замечаний не нашла" in page.text and "проверять нечего" not in page.text
    # замечаний не осталось — «Проверено» ставится сразу, и видно, что поставлен при сохранении
    assert "сразу отмечена «Проверено»" in page.text and "Поставлен сам" in page.text
    review = client.app.state.store.review(f, psr_card)[1]["Лесовики.xlsx"]
    assert review.status == "done" and review.by == "save"
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


def sosna(tmp_path, contacts="89137654321"):
    """Заявка без ошибок, но с «проверить»: участнику 16 лет — допуск только по решению ГСК."""
    return make_application(tmp_path / "Сосна.xlsx", "Сосна", "Красноярск", "Орлов Павел Ильич",
                            contacts, 3, [
                                ["Сосна", "Красноярск", "Орлов Павел Ильич", "Орлов Павел Ильич",
                                 "11.05.1985", "II", "м", "М/Ж", 3],
                                ["Сосна", "Красноярск", "Орлов Павел Ильич", "Белова Ирина Петровна",
                                 "03.07.1999", "КМС", "ж", "М/Ж", 3],
                                ["Сосна", "Красноярск", "Орлов Павел Ильич", "Пестов Юрий Андреевич",
                                 "01.01.2009", "б/р", "м", "М/Ж", 3],
                            ]).read_bytes()


def test_back_to_list_lands_on_the_team_and_next_team_link(client, tmp_path, psr_card):
    from st_secretary.web.app import team_anchor

    f = client.app.state.store.create(psr_card)
    f.add_preapp("Лесовики.xlsx", lesoviki(tmp_path))
    f.add_preapp("Сосна.xlsx", sosna(tmp_path))
    anchor = team_anchor("Лесовики.xlsx")
    assert f'id="{anchor}"' in client.get(base(f) + "/preapps").text  # у команды в списке — постоянная метка
    card = client.get(preapp_url(f, page="team")).text
    assert f'href="{base(f)}/preapps#{anchor}"' in card  # «← Все заявки» ведёт прямо к этой команде
    assert "команда 1 из 2" in card and "Следующая команда: Сосна" in card
    assert preapp_url(f, "Сосна.xlsx", "team").replace(" ", "%20") in card
    last = client.get(preapp_url(f, "Сосна.xlsx", "team")).text
    assert "Это последняя команда в списке" in last and "Предыдущая:" in last
    # «Проверено» в списке возвращает к той же команде
    result, _ = client.app.state.store.review(f, psr_card)
    [warn] = [i for i in result.issues if i.source == "Сосна.xlsx" and i.severity == "warning"]
    r = client.post(base(f) + "/preapps/check", follow_redirects=False,
                    data={"file": "Сосна.xlsx", "key": issue_key(warn), "back": f"{base(f)}/preapps#{team_anchor('Сосна.xlsx')}"})
    assert r.headers["location"].endswith("#" + team_anchor("Сосна.xlsx"))


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

    # команда прислала новый файл — «Проверено» снимается, а отметка у того же замечания остаётся
    f.add_preapp("Сосна.xlsx", sosna(tmp_path, contacts="89130000000, sosna@mail.ru"))
    result, reviews = store.review(f, psr_card)
    assert reviews["Сосна.xlsx"].status == "check" and not reviews["Сосна.xlsx"].manual
    assert "снят: заявку с тех пор изменили" in reviews["Сосна.xlsx"].reset
    assert result.count("checked") == 1  # то же замечание — отметка «проверено» сохранилась

    # сохранили в форме: неотмеченных «проверить» нет (отмеченное не мешает) — «Проверено» сразу
    data = FormFields(client.get(preapp_url(f, "Сосна.xlsx")).text, "preappform").fields
    r = client.post(base(f) + "/preapps/save", data=data, follow_redirects=False)
    assert "done=psaved-done" in r.headers["location"]
    review = store.review(f, psr_card)[1]["Сосна.xlsx"]
    assert review.status == "done" and review.by == "save"
    client.post(base(f) + "/preapps/check", data={"file": "Сосна.xlsx", "key": issue_key(warn), "on": "0"})
    review = store.review(f, psr_card)[1]["Сосна.xlsx"]  # появилось неотмеченное «проверить» — статус снят
    assert review.status == "check" and "появились замечания «Проверить»" in review.reset
    client.post(base(f) + "/preapps/save", data=data)  # сохранили с неотмеченным «проверить» — сам не ставится
    assert store.review(f, psr_card)[1]["Сосна.xlsx"].status == "check"

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
    assert "Жеребьёвка или порядок старта" in client.get(base(f) + "/step/start").text
    assert client.get(base(f) + "/step/admission").status_code == 404  # готовый шаг — своя страница
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


# ------------------------------------------------------------------ комиссия по допуску


def kedr(tmp_path):
    """Заявка без замечаний: три взрослых участника."""
    return make_application(tmp_path / "Кедр.xlsx", "Кедр", "Красноярск", "Лебедев Антон Игоревич",
                            "89135550000", 3, [
                                ["Кедр", "Красноярск", "Лебедев Антон Игоревич", "Лебедев Антон Игоревич",
                                 "02.02.1990", "I", "м", "М/Ж", 3],
                                ["Кедр", "Красноярск", "Лебедев Антон Игоревич", "Зуева Мария Олеговна",
                                 "05.06.1996", "II", "ж", "М/Ж", 3],
                                ["Кедр", "Красноярск", "Лебедев Антон Игоревич", "Носов Глеб Андреевич",
                                 "09.09.1993", "б/р", "м", "М/Ж", 3],
                            ]).read_bytes()


def team_form(page_html, file):
    """Поля формы команды на странице комиссии (как их отправит браузер)."""
    from st_secretary.web.app import team_anchor

    start = page_html.index(f'id="{team_anchor(file)}"')
    block = page_html[start:page_html.index("</article>", start)]
    return FormFields('<form id="t">' + block.split(">", 1)[1], "t").fields


def test_admission_documents_decisions_fees_numbers(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Кедр.xlsx", kedr(tmp_path))
    f.add_preapp("Сосна.xlsx", sosna(tmp_path))
    url = base(f) + "/admission"
    page = client.get(url).text
    assert "Комиссия по допуску" in page and "Зуева Мария Олеговна" in page and "Паспорт" in page
    assert "0 из 2" in page and "Ожидают <b>2</b>" in page  # пока ничего не отмечено

    # «Отметить все документы» — со страницы приходит без перезагрузки (X-Autosave), ответ — блок команды и итоги
    data = team_form(page, "Кедр.xlsx") | {"do": "all_docs", "fee_paid": "3000", "fee_method": "наличные"}
    r = client.post(url + "/team", data=data, headers={"X-Autosave": "1"})
    j = r.json()
    assert j["status"] == "admitted" and "Допущена" in j["team"] and "оплачено" in j["team"]
    assert "1 из 2" in j["tiles"] and "3 000 ₽" in j["tiles"]

    # Сосна: Пестову 16 лет — ждёт решения комиссии; допустить решением комиссии с основанием
    data = team_form(client.get(url).text, "Сосна.xlsx") | {"do": "all_docs"}
    j = client.post(url + "/team", data=data, headers={"X-Autosave": "1"}).json()
    assert j["status"] == "pending" and "нужно решение комиссии" in j["team"]
    data = team_form(client.get(url).text, "Сосна.xlsx")
    pestov = next(k.split("-")[1] for k, v in data.items() if k.endswith("-key") and v == "пестов юрий андреевич")
    data |= {f"p-{pestov}-decision": "admitted", f"p-{pestov}-reason": "решение ГСК"}
    r = client.post(url + "/team", data=data, follow_redirects=False)  # без скриптов — обычная форма
    assert r.status_code == 303 and "#t-" in r.headers["location"]
    page = client.get(url).text
    assert "2 из 2" in page and "допущен решением комиссии: решение ГСК" in page

    # номера командам по порядку списка
    client.post(url + "/numbers", data={"mode": "missing"})
    adm = f.admission()["teams"]
    assert sorted(t["number"] for t in adm.values()) == [1, 2]
    data = team_form(client.get(url).text, "Сосна.xlsx") | {"number": str(adm["Кедр.xlsx"]["number"])}
    j = client.post(url + "/team", data=data, headers={"X-Autosave": "1"}).json()
    assert "уже у команды «Кедр»" in j["team"]  # одинаковый номер — предупреждение

    # протокол и ведомость
    r = client.get(url + "/report.xlsx")
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Протокол комиссии", "Ведомость взносов", "Документы участников"]
    text = [str(c.value) for row in wb["Протокол комиссии"].iter_rows() for c in row if c.value is not None]
    assert text.count("допущена") == 2  # обе команды — в графе «Решения по замечаниям»
    r = client.post(url + "/report", follow_redirects=False)
    assert r.status_code == 303 and f.commission_report_path.is_file()


def test_admission_settings_and_reentry(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Кедр.xlsx", kedr(tmp_path))
    url = base(f) + "/admission"
    client.post(url + "/settings", data={"doc-id": "1", "doc-med": "1", "doc-pd": "1", "tdoc-app": "1",
                                         "start_at": "2000-01-01T10:00"})  # старт давно прошёл — перезаявка поздняя
    page = client.get(url).text
    assert "Согласие" in page and "Книжка" not in page.split("<thead>")[1].split("</thead>")[0]

    edit = client.get(base(f) + "/preapps/edit?file=" + quote("Кедр.xlsx") + "&reentry=1").text
    assert "Перезаявка «Кедр»" in edit and "меньше часа" in edit
    data = FormFields(edit, "preappform").fields
    assert data["reentry"] == "1"
    fio_key = next(k for k, v in data.items() if v == "Носов Глеб Андреевич")
    data[fio_key] = "Носова Галина Андреевна"
    data[fio_key.replace("fio", "sex")] = "ж"
    r = client.post(base(f) + "/preapps/save", data=data, follow_redirects=False)
    assert r.status_code == 303 and "/admission?done=reentry_late" in r.headers["location"]
    [rec] = f.admission()["teams"]["Кедр.xlsx"]["reentries"]
    assert "выбыл(а): Носов Глеб Андреевич" in rec["text"] and "включён(а): Носова Галина Андреевна" in rec["text"]
    assert rec["late"] and "позже, чем за час до старта" in client.get(url).text
    edit = client.get(base(f) + "/preapps/edit?file=" + quote("Кедр.xlsx") + "&reentry=1").text
    assert "повторные не принимаются" in edit


# ------------------------------------------------------------------ проверка снаряжения и документы команд

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
       b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82")


def test_equipment_page_check_and_report(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Кедр.xlsx", kedr(tmp_path))
    url = base(f) + "/equipment"
    assert "Перечень снаряжения не задан" in client.get(url).text
    client.post(url + "/settings", data={"do": "template"})
    page = client.get(url).text
    assert "Не проверено" in page and "Начать проверку: всё есть" in page
    assert "снаряжение не проверено" in client.get(base(f) + "/admission").text  # без проверки не допустить

    j = client.post(url + "/team", data={"file": "Кедр.xlsx", "do": "all"}, headers={"X-Autosave": "1"}).json()
    assert "0 б. — допущена" in j["team"] and j["by"]["ok"] == 1
    data = team_form(client.get(url).text, "Кедр.xlsx")
    lost = next(k for k, v in data.items() if k.startswith("p-0-") and v == "on")  # первая галочка первого участника
    data = {k: v for k, v in data.items() if k != lost}  # у участника одного предмета нет
    j = client.post(url + "/team", data=data, headers={"X-Autosave": "1"}).json()
    assert "1 б. — допущена" in j["team"] and "Нет:" in j["team"]

    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(client.get(base(f) + "/admission/report.xlsx").content))
    assert "Проверка снаряжения" in wb.sheetnames
    text = [str(c.value) for row in wb["Проверка снаряжения"].iter_rows() for c in row if c.value is not None]
    assert "Акт проверки снаряжения" in text and "допущена" in text


def test_team_documents_stored_locally_and_checked_in_one_window(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Кедр.xlsx", kedr(tmp_path))
    card = base(f) + "/preapps/team?file=" + quote("Кедр.xlsx")
    assert "Документы команды (0)" in client.get(card).text
    r = client.post(base(f) + "/docs/upload", data={"file": "Кедр.xlsx", "back": card},
                    files=[("files", ("паспорт Лебедев.png", PNG, "image/png")),
                           ("files", ("полис.pdf", b"%PDF-1.4 test", "application/pdf")),
                           ("files", ("вирус.exe", b"MZ", "application/octet-stream"))], follow_redirects=False)
    assert r.status_code == 303 and "done=docs_skipped" in r.headers["location"]
    docs_dir = tmp_path / "документы" / f.id / "Кедр"
    assert sorted(p.name for p in docs_dir.iterdir()) == ["паспорт Лебедев.png", "полис.pdf"]
    assert not any((f.path).rglob("*.png"))  # в папке соревнования (её копируют, держат в облаке) сканов нет

    page = client.get(card).text
    assert "Документы команды (2)" in page and "Проверить в одном окне" in page
    view = client.get(base(f) + "/docs/view?" + urlencode({"file": "Кедр.xlsx", "name": "паспорт Лебедев.png"}))
    assert view.content == PNG and view.headers["cache-control"] == "no-store"
    assert "inline" in view.headers["content-disposition"]
    assert client.get(base(f) + "/docs/view?" + urlencode({"file": "Кедр.xlsx", "name": "../../x.png"})).status_code == 404

    check = client.get(base(f) + "/admission/check?file=" + quote("Кедр.xlsx")).text
    assert 'data-doc-kind="image"' in check and 'data-doc-kind="pdf"' in check
    assert "Отметить все документы" in check and "Лебедев Антон Игоревич" in check
    assert "проверить в одном окне" not in check  # уже в нём
    data = team_form(check, "Кедр.xlsx") | {"do": "all_docs"}
    j = client.post(base(f) + "/admission/team", data=data, headers={"X-Autosave": "1"}).json()
    assert j["status"] == "admitted" and "проверить в одном окне" not in j["team"]

    client.post(base(f) + "/docs/remove", data={"file": "Кедр.xlsx", "name": "полис.pdf"})
    assert (docs_dir / "Убранные" / "полис.pdf").is_file() and not (docs_dir / "полис.pdf").exists()


# ------------------------------------------------------------------ награждение и документы по итогам


def test_awards_manual_results_and_documents(client, tmp_path, psr_card, opened):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Кедр.xlsx", kedr(tmp_path))
    f.add_preapp("Сосна.xlsx", sosna(tmp_path))
    url = base(f) + "/awards"
    page = client.get(url).text
    assert "Результатов пока нет" in page and "Ввести или поправить места вручную" in page
    r = client.post(url + "/import", data={"zachet": "М/Ж_3"},
                    files={"protocol": ("протокол.xls", b"not an excel file", "application/vnd.ms-excel")},
                    follow_redirects=False)
    assert "done=res_bad" in r.headers["location"]  # не протокол — объяснение, а не ошибка

    data = {"zachet": "М/Ж_3", "r-0-team": "Кедр", "r-0-place": "1", "r-0-result": "-283", "r-0-norm": "II",
            "r-1-team": "Сосна", "r-1-place": "2", "r-1-result": "-213", "r-1-norm": ""}
    r = client.post(url + "/manual", data=data, follow_redirects=False)
    assert "done=res_saved" in r.headers["location"]
    page = client.get(url).text
    assert "Места введены вручную" in page and "Лебедев Антон Игоревич (I)" in page

    from docx import Document
    d = client.get(url + "/file/diplomas")
    assert d.status_code == 200 and d.content[:2] == b"PK"
    texts = [p.text for p in Document(io.BytesIO(d.content)).paragraphs if p.text]
    assert texts.count("Награждаются") == 6 and "за II место" in texts
    r = client.post(url + "/doc/stickers", follow_redirects=False)
    assert r.status_code == 303 and opened[-1] == f.out_dir / "Наклейки на медали.xlsx" and opened[-1].is_file()
    assert client.get(url + "/file/nothing").status_code == 404


def test_judge_grades_go_to_certificates(client, psr_card):
    f = client.app.state.store.create(psr_card)
    url = base(f) + "/awards"
    page = client.get(url).text
    assert "Судьин Иван Петрович" in page and "Сохранить оценки" in page
    r = client.post(url + "/grades", data={"g-0": "отлично", "g-1": "хорошо"}, follow_redirects=False)
    assert "done=grades_saved" in r.headers["location"]
    from docx import Document
    texts = [p.text for p in Document(io.BytesIO(client.get(url + "/file/judging").content)).paragraphs]
    assert "Оценка судейства: «отлично»." in texts and "Оценка судейства: «хорошо»." in texts
    sk = client.get(url + "/file/sk")
    assert sk.status_code == 200 and sk.content[:2] == b"PK"


def test_report_texts_and_extracts_download(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Кедр.xlsx", kedr(tmp_path))
    url = base(f) + "/awards"
    client.post(url + "/manual", data={"zachet": "М/Ж_3", "r-0-team": "Кедр", "r-0-place": "1", "r-0-norm": "III"})
    r = client.post(url + "/report", data={"protests": "подан один протест, отклонён", "base": "Всё хорошо."},
                    follow_redirects=False)
    assert "done=report_saved" in r.headers["location"]
    assert "подан один протест, отклонён" in client.get(url).text
    from docx import Document
    texts = [p.text for p in Document(io.BytesIO(client.get(url + "/file/report").content)).paragraphs]
    assert "Подан один протест, отклонён." in texts and "Всё хорошо." in texts
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(client.get(url + "/file/extracts").content))
    rows = [[c.value for c in r] for r in wb.active.iter_rows() if r[5].value == "III"]
    assert len(rows) == 3 and rows[0][2] == "02.02.1990"  # дата рождения — из заявки


def test_contracts_tabel_personal_data_and_documents(client, tmp_path, psr_card, opened):
    from docx import Document
    from openpyxl import load_workbook

    from test_contracts import CUSTOMER, PERSON

    f = client.app.state.store.create(psr_card)
    url = base(f) + "/contracts"
    page = client.get(url).text
    assert "Судьин Иван Петрович" in page and "Секретарёва Анна Ивановна" in page and "ставка не задана" not in page
    assert page.count('type="checkbox" name="p-0-d-') == 4  # 19–22 сентября: день до и день после
    r = client.post(url + "/add", data={"fio": "Работяга  Семён Ильич", "role": "Рабочий комендантской бригады",
                                        "category": "б/к"}, follow_redirects=False)
    assert "done=ct_added" in r.headers["location"]
    assert "done=ct_exists" in client.post(url + "/add", data={"fio": "Судьин Иван Петрович", "role": "Судья"},
                                           follow_redirects=False).headers["location"]

    r = client.post(url + "/settings", data={"do": "sample"}, follow_redirects=False)
    assert "done=ct_settings" in r.headers["location"]
    data = f.contracts()
    assert data["rates"] == {"главный судья|1": 850, "рабочий комендантской бригады|б/к": 460}  # чего нет в образце — пусто
    client.post(url + "/settings", data={"from": "2025-09-19", "to": "2025-09-22", "accrual": "30",
                                         "rate-главный судья|1": "850", "rate-главный секретарь|2": "700",
                                         "rate-рабочий комендантской бригады|б/к": "460"})
    days = {"p-0-key": "судьин иван петрович", "p-1-key": "секретарева анна ивановна", "p-2-key": "работяга семен ильич",
            "p-0-d-20250919": "on", "p-0-d-20250920": "on", "p-0-d-20250921": "on", "p-0-d-20250922": "on",
            "p-1-d-20250920": "on", "p-1-d-20250921": "on", "p-2-d-20250921": "on", "p-2-unpaid": "on"}
    r = client.post(url + "/days", data=days, headers={"X-Autosave": "1"})
    j = r.json()
    assert "<b>3 400</b>" in j["team"] and "4 800,00" in j["team"]  # 4×850 + 2×700; рабочий без оплаты
    assert "6 240,00" in j["team"] and "6 240,00 ₽" in j["tiles"]

    person = url + "/person?" + urlencode({"key": "судьин иван петрович"})
    p = client.get(person)
    assert p.headers["cache-control"] == "no-store" and "в качестве главного судьи 1 категории" in p.text
    r = client.post(url + "/person", data={"key": "судьин иван петрович", **dict(PERSON, inn="123456789012")},
                    follow_redirects=False)
    assert "done=ct_person" in r.headers["location"]
    assert "ИНН не сходится" in client.get(person).text
    client.post(url + "/person", data={"key": "судьин иван петрович", **PERSON})
    assert client.app.state.store.personal()["судьин иван петрович"]["inn"] == PERSON["inn"]
    assert client.app.state.store.personal_path.parent == tmp_path / "документы"  # не в папке соревнования
    client.post(url + "/customer", data=CUSTOMER)

    r = client.post(url + "/doc/person", data={"key": "судьин иван петрович"}, follow_redirects=False)
    assert "done=ct_doc" in r.headers["location"]
    doc_path = opened[-1]
    assert doc_path.parent == tmp_path / "документы" / f.id / "Договоры и табель"
    assert doc_path.name == "Судьин И.П. — главный судья.docx"
    text = "\n".join(p.text for p in Document(str(doc_path)).paragraphs)
    assert "в лице директора Начальникова Петра Сергеевича" in text and "Срок оказания услуг: 19–22 сентября" in text
    assert "3400 (три тысячи четыреста) рублей 00 копеек" in text

    ws = load_workbook(io.BytesIO(client.get(url + "/file/tabel").content)).active
    names = [ws.cell(r, 2).value for r in range(8, 11)]
    assert names == ["Судьин Иван Петрович", "Секретарёва Анна Ивановна", "Итого начислено"]
    all_docs = client.get(url + "/file/all")
    assert all_docs.status_code == 200 and all_docs.headers["cache-control"] == "no-store"
    assert "\n".join(p.text for p in Document(io.BytesIO(all_docs.content)).paragraphs).count("ДОГОВОР № ______") == 2

    r = client.post(url + "/template", follow_redirects=False)
    assert "done=ct_template" in r.headers["location"] and f.contract_template.is_file()
    assert "Используется свой шаблон" in client.get(url).text
    client.post(url + "/remove", data={"key": "работяга семен ильич"})
    assert "Работяга" not in client.get(url).text
