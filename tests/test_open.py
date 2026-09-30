"""Кнопки «Открыть»: если на компьютере нечем открыть файл, программа говорит это прямо и даёт скачать файл
(раньше показывала «Открываю…», а ничего не открывалось — Правки.md, п. 1)."""

import subprocess
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from st_secretary.web.app import create_app
from st_secretary.web.common import CannotOpen, cannot_open_text, open_in_os


class Proc:
    def __init__(self, code=0, err=b"", slow=False):
        self.returncode, self.err, self.slow = code, err, slow

    def communicate(self, timeout):
        if self.slow:
            raise subprocess.TimeoutExpired("open", timeout)
        return b"", self.err

    def wait(self):
        return self.returncode


def test_open_on_macos_reports_missing_application(tmp_path):
    f = tmp_path / "Перевал.xlsx"
    f.write_bytes(b"x")
    calls = []

    def popen(cmd, **kw):
        calls.append(cmd)
        return Proc(1, b"No application knows how to open URL file:///Perevalxlsx (kLSApplicationNotFoundErr).")

    with pytest.raises(CannotOpen) as e:
        open_in_os(f, "darwin", popen=popen)
    assert calls == [["open", str(f)]] and "kLSApplicationNotFoundErr" in e.value.reason
    open_in_os(f, "darwin", popen=lambda cmd, **kw: Proc(0))  # открылось
    open_in_os(f, "linux", popen=lambda cmd, **kw: Proc(slow=True))  # программа держит запуск — открылось


def test_open_on_windows_and_linux_without_application(tmp_path):
    f = tmp_path / "Договор.docx"
    f.write_bytes(b"x")

    def no_app(path):
        raise OSError(1155, "Этому файлу не сопоставлена программа")

    with pytest.raises(CannotOpen):
        open_in_os(f, "win32", startfile=no_app)
    opened = []
    open_in_os(f, "win32", startfile=opened.append)
    assert opened == [f]

    def no_xdg(cmd, **kw):
        raise FileNotFoundError(2, "xdg-open не найден")

    with pytest.raises(CannotOpen):
        open_in_os(f, "linux", popen=no_xdg)
    with pytest.raises(CannotOpen):
        open_in_os(f, "linux", popen=lambda cmd, **kw: Proc(3))  # xdg-open: нечем открыть


def test_explanation_names_file_type_and_programs(tmp_path):
    f = tmp_path / "Перевал.xlsx"
    f.write_bytes(b"x")
    mac = cannot_open_text(f, "darwin")
    assert mac["what"] == "файлы Excel (.xlsx)" and "Numbers" in mac["hint"]
    assert "Excel или LibreOffice" in cannot_open_text(f, "win32")["hint"]
    assert cannot_open_text(tmp_path, "darwin")["what"] == "папку"


def test_open_button_shows_page_and_download_when_nothing_opens(tmp_path, psr_card):
    def opener(path):
        raise CannotOpen(path, "kLSApplicationNotFoundErr")

    client = TestClient(create_app(tmp_path / "данные", opener=opener, docs_dir=tmp_path / "документы",
                                   board_host="127.0.0.1"))
    f = client.app.state.store.create(psr_card)
    f.preapp_dir.mkdir(exist_ok=True)
    (f.preapp_dir / "Перевал.xlsx").write_bytes(b"PK-excel")
    b = "/c/" + quote(f.id, safe="")
    r = client.post(b + "/preapps/open", data={"name": "Перевал.xlsx"}, headers={"referer": f"http://t{b}/preapps"})
    assert r.status_code == 200 and "Открыть не получилось" in r.text and "Открываю" not in r.text
    assert "нет программы, которая открывает файлы Excel (.xlsx)" in r.text
    assert f'href="{b}/preapps"' in r.text  # «Вернуться» — туда, где нажали
    link = r.text.split('href="/open-failed/')[1].split('"')[0]
    d = client.get(f"/open-failed/{link}")
    assert d.status_code == 200 and d.content == b"PK-excel" and "attachment" in d.headers["content-disposition"]
    assert client.get("/open-failed/чужой").status_code == 404  # скачать можно только то, что не открылось

    r = client.post("/open-data", headers={"referer": "http://t/"})  # папка: скачивать нечего
    assert "Открыть не получилось" in r.text and "/open-failed/" not in r.text and "Вернуться" in r.text
