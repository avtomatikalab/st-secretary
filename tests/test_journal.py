"""Журнал программы в файл (Правки.md, п. 21): запуск и остановка, ошибки, «закрылась неожиданно», скачать."""

import io
import logging
import threading
import zipfile

import pytest

from st_secretary.journal import LOG_NAME, RUNNING, Journal, folder_for


def text(folder) -> str:
    return (folder / LOG_NAME).read_text(encoding="utf-8")


def test_start_stop_and_marker(tmp_path):
    folder = folder_for(tmp_path / "данные")
    assert folder == tmp_path / "Журнал"
    j = Journal(folder).start(данные=str(tmp_path / "данные"), порт=8765)
    try:
        assert (folder / RUNNING).is_file() and not j.crashed_before
        logging.getLogger("st_secretary.web").warning("Не открылся файл Перевал.xlsx")
        j.stop("кнопка «Выключить»")
        j.stop("второй раз не пишется")
        log = text(folder)
        assert "Запуск: СТ-Секретарь" in log and "порт: 8765" in log and "Не открылся файл" in log
        assert "Остановка: кнопка «Выключить»" in log and "второй раз" not in log
        assert not (folder / RUNNING).exists()
    finally:
        j.close()


def test_unexpected_close_is_noticed_next_time(tmp_path):
    j = Journal(tmp_path / "Журнал").start()
    j.close()  # «вылетела»: остановки не было — метка «работает» осталась
    j2 = Journal(tmp_path / "Журнал").start()
    try:
        assert j2.crashed_before and j2.previous
        assert "закончился неожиданно" in text(tmp_path / "Журнал")
    finally:
        j2.stop("тест")
        j2.close()


def test_uncaught_errors_go_to_file(tmp_path):
    j = Journal(tmp_path / "Журнал").start()
    try:
        t = threading.Thread(target=lambda: 1 / 0, name="табло")
        t.start()
        t.join()
        log = text(tmp_path / "Журнал")
        assert "Ошибка в фоновом потоке «табло»" in log and "ZeroDivisionError" in log
    finally:
        j.close()


def test_download_and_home_notice(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from st_secretary.web.app import create_app

    Journal(tmp_path / "Журнал").start().close()  # прошлый запуск «вылетел»
    j = Journal(tmp_path / "Журнал").start()
    try:
        app = create_app(tmp_path / "данные", opener=lambda p: None, docs_dir=tmp_path / "docs",
                         board_host="127.0.0.1")
        app.state.journal = j
        client = TestClient(app)
        home = client.get("/").text
        assert "В прошлый раз программа закрылась неожиданно" in home and 'href="/journal.zip"' in home
        r = client.get("/journal.zip")
        assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            assert LOG_NAME in z.namelist()
        client.post("/journal/hide")
        assert "закрылась неожиданно" not in client.get("/").text
        assert "Журнал программы" in client.get("/").text  # мелкая ссылка внизу страниц
    finally:
        j.stop("тест")
        j.close()


def test_no_journal_no_link(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from st_secretary.web.app import create_app

    client = TestClient(create_app(tmp_path / "данные", opener=lambda p: None, board_host="127.0.0.1"))
    assert client.get("/journal.zip").status_code == 404 and "Журнал программы" not in client.get("/").text
