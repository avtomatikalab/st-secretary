"""«Сообщить» (Правки, п. 43; решение 044): сообщения пользователей — файлами для разработчика. Всё выдуманное."""

import base64
import io
import json
import re
import zipfile
from datetime import datetime
from urllib.parse import unquote

import pytest

from st_secretary import feedback as fb

PNG = base64.b64decode(  # картинка 1×1
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
META = {"who": "Секретарёва А.", "competition": "2026-10-03 Учебный чемпионат", "page": "/c/x/start — start.html",
        "place": "поле «Первый старт» (name=first)", "version": "0.4.0 бета, переносная, Windows",
        "page_title": "Жеребьёвка и стартовые протоколы", "path": ["15:40 открыта «Жеребьёвка»", "15:41 нажата «Провести»"],
        "errors": "Журнал программы, последние строки:\nINFO запуск"}


def test_message_saved_parsed_bundled_and_imported_without_repeats(tmp_path):
    folder = tmp_path / "Правки и ошибки"
    m = fb.save(folder, "Ошибка", "Время старта пустое.", META, PNG, now=datetime(2026, 10, 1, 15, 42, 7), host="PC-1")
    assert m.path.name == "2026-10-01 15-42-07 Ошибка.md" and m.png and m.png.read_bytes() == PNG
    back = fb.parse(m.path)
    assert back.head["Статус"] == "новое" and back.head["Кто"] == "Секретарёва А." and back.head["Компьютер"] == "PC-1"
    assert back.head["Снимок"] == "2026-10-01 15-42-07 Ошибка.png" and back.text == "Время старта пустое."
    assert back.sections["Перед этим"].splitlines()[1] == "15:41 нажата «Провести»"
    assert back.title == "Ошибка · Жеребьёвка и стартовые протоколы · 01.10.2026 15:42" and back.when == "01.10.2026 15:42"
    other = fb.save(folder, "Предложение", "Кнопку бы побольше.", {}, None, now=datetime(2026, 10, 1, 16, 0), host="PC-1")

    path, sent = fb.bundle(folder, "журнал", "0.4.0 бета", now=datetime(2026, 10, 1, 17, 5), host="PC-1")
    assert path.name == "СТ-Секретарь — сообщения PC-1 2026-10-01 17-05.zip" and len(sent) == 2
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read("manifest.json"))
    assert {m.path.name, m.png.name, other.path.name, "manifest.json", "Журнал — последние записи.txt"} <= names
    assert manifest["subject"] == "СТ-Секретарь: сообщения — 2 шт., версия 0.4.0 бета" and len(manifest["messages"]) == 2
    assert all(x.status == "отправлено 01.10.2026" for x in fb.messages(folder))
    assert fb.bundle(folder, "", "0.4.0") is None  # новых нет — в следующий файл не попадают
    again, _ = fb.bundle(folder, "", "0.4.0", numbers=fb.last_bundle(folder)["numbers"])  # «Собрать заново»
    assert again.is_file()

    # владелец загружает этот файл к себе: всё добавилось, с пометкой компьютера; второй раз — без повторов
    owner = tmp_path / "владелец" / "Правки и ошибки"
    assert fb.import_bundle(owner, path.read_bytes()) == (2, 0)
    assert fb.import_bundle(owner, path.read_bytes()) == (0, 2)
    got = fb.messages(owner)
    assert {x.head["Получено с"] for x in got} == {"PC-1"} and any(x.png for x in got)

    link = fb.mailto(2, "0.4.0 бета", path.name)
    assert link.startswith(f"mailto:{fb.SUPPORT_EMAIL}?subject=") and fb.SUPPORT_EMAIL == "support.st.secretary@gmail.com"
    assert unquote(link.split("subject=")[1].split("&")[0]) == "СТ-Секретарь: сообщения — 2 шт., версия 0.4.0 бета"
    assert fb.remove(folder, m.path.name) and not m.path.exists() and (folder / "Убранные" / m.path.name).is_file()


def test_feedback_from_page_list_send_and_error_page(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from st_secretary.web.app import create_app

    opened = []
    client = TestClient(create_app(tmp_path / "данные", opener=opened.append, docs_dir=tmp_path / "документы",
                                   board_host="127.0.0.1"), raise_server_exceptions=False)
    home = client.get("/").text
    assert "data-feedback" in home and 'data-template="home.html"' in home and "Мои сообщения разработчику" in home
    assert 'data-module="st_secretary.web.pages.home"' in home
    body = {"kind": "Неудобно", "text": "Не видно, где время старта.", "who": "Секретарёва А.",
            "place": "поле «Первый старт» (name=first)", "image": "data:image/png;base64," + base64.b64encode(PNG).decode(),
            "path": ["15:40 открыта «Жеребьёвка»"], "js_errors": ["15:41 TypeError: x is null (app.js:10)"],
            "page": {"url": "/c/2026-10-03%20Учебный/start?z=М/Ж_3#times", "title": "Жеребьёвка", "template": "start.html",
                     "module": "st_secretary.web.pages.start", "section": "#times", "window": "1366×768"}}
    j = client.post("/feedback", json=body).json()
    assert j["ok"] and j["unsent"] == 1 and j["image"] and j["folder"].endswith("Правки и ошибки")
    m = fb.messages(tmp_path / "Правки и ошибки")[0]
    assert m.head["Соревнование"] == "2026-10-03 Учебный"
    assert "start.html (web/pages/start.py), раздел #times" in m.head["Страница"]
    assert "TypeError: x is null" in m.sections["Ошибки и журнал"] and m.png
    assert client.post("/feedback", json={**body, "text": " "}).status_code == 422
    assert "Есть неотправленные сообщения: 1" in client.get("/").text

    page = client.get("/feedback").text
    assert "Не видно, где время старта." in page and "Отправить разработчику" in page
    r = client.post("/feedback/send", follow_redirects=False)
    assert "fb_sent" in r.headers["location"] and opened[-1].name == "Отправка"
    page = client.get(r.headers["location"]).text
    assert "data-auto-open" in page and "mailto:support.st.secretary@gmail.com?subject=" in page
    assert "Есть неотправленные" not in client.get("/").text
    assert client.get("/feedback/all.zip").content[:2] == b"PK"

    # страница «Что-то пошло не так»: большая кнопка и текст ошибки для сообщения — сами
    @client.app.get("/boom")
    def boom():
        raise RuntimeError("проверка")

    err = client.get("/boom").text
    assert "Сообщить об ошибке" in err and 'data-feedback-kind="Ошибка"' in err
    trace = json.loads(re.search(r'<script type="application/json" id="fb-error">(.*?)</script>', err, re.DOTALL).group(1))
    assert trace["error"].startswith("/boom") and "RuntimeError: проверка" in trace["error"]


def test_import_rejects_not_a_zip(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from st_secretary.web.app import create_app

    client = TestClient(create_app(tmp_path / "данные", opener=lambda p: None, docs_dir=tmp_path / "документы",
                                   board_host="127.0.0.1"))
    r = client.post("/feedback/import", files={"file": ("x.zip", b"not a zip")}, follow_redirects=False)
    assert "fb_bad" in r.headers["location"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../вне.md", "# x\nНомер: 1\n")
    r = client.post("/feedback/import", files={"file": ("x.zip", buf.getvalue())}, follow_redirects=False)
    assert "added=0" in r.headers["location"] and not (tmp_path / "вне.md").exists()
