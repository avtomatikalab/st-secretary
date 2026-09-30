"""Проверка обновлений и установка новой версии (без интернета: GitHub подменяется)."""

import hashlib
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from st_secretary import __version__, training, updates
from st_secretary.updates import DOWNLOADS, Installer, Release, UpdateError


class Answer(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def opener_for(pages: dict):
    """Подмена urlopen: адрес → байты ответа (или исключение); запоминает запросы."""
    seen = []

    def urlopen(req, timeout):
        seen.append(req)
        v = pages[req.full_url]
        if isinstance(v, Exception):
            raise v
        return Answer(v)

    urlopen.seen = seen
    return urlopen


def portable_zip(version: str, top: str = "СТ-Секретарь", extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{top}/program/python/python.exe", b"MZ")
        z.writestr(f"{top}/program/python/Lib/site-packages/st_secretary/__init__.py",
                   f'"""СТ-Секретарь."""\n\n__version__ = "{version}"\n')
        z.writestr(f"{top}/СТ-Секретарь.bat", b"@echo new launcher")
        z.writestr(f"{top}/Прочтите меня.txt", "новая версия".encode())
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def release(version: str, data: bytes = b"", **kw) -> Release:
    url = f"{DOWNLOADS}v{version}/st-secretary-{version}-windows.zip"
    return Release(version, "https://github.com/x", asset_url=url, asset_size=len(data),
                   sha256=hashlib.sha256(data).hexdigest(), **kw)


def api_answer(tag="v9.0.0", digest="sha256:" + "a" * 64, **kw) -> dict:
    return {"tag_name": tag, "html_url": "https://github.com/avtomatikalab/st-secretary/releases/tag/" + tag,
            "body": "Что нового", "published_at": "2026-10-01T10:00:00Z", "draft": False, "prerelease": False,
            "assets": [{"name": "st-secretary-manual.pdf", "browser_download_url": DOWNLOADS + "x.pdf"},
                       {"name": f"st-secretary-{tag[1:]}-windows.zip", "size": 123, "digest": digest,
                        "browser_download_url": f"{DOWNLOADS}{tag}/st-secretary-{tag[1:]}-windows.zip"}], **kw}


# ------------------------------------------------------------------ версии и ответ GitHub


def test_versions_compare_as_numbers():
    assert updates.parse_version("v0.10.2") == (0, 10, 2)
    assert updates.parse_version("0.2.0-rc1") is None
    assert updates.is_newer("0.10.0", "0.9.9")
    assert updates.is_newer("1.0.1", "1.0")
    assert not updates.is_newer("1.0.0", "1.0")
    assert not updates.is_newer("0.1.0", "0.2.0")
    assert not updates.is_newer("мусор", "0.1.0")


def test_release_from_github_answer():
    rel = updates.from_api(api_answer())
    assert rel.version == "9.0.0" and rel.notes == "Что нового" and rel.published.year == 2026
    assert rel.asset_url.endswith("st-secretary-9.0.0-windows.zip") and rel.asset_size == 123
    assert rel.sha256 == "a" * 64 and rel.installable
    assert updates.from_api(api_answer(prerelease=True) | {"prerelease": True}) is None
    assert updates.from_api(api_answer() | {"draft": True}) is None
    assert not updates.from_api(api_answer(digest="")).installable  # без контрольной суммы — только ссылка
    other = api_answer()
    other["assets"][1]["browser_download_url"] = "https://example.com/st-secretary-9.0.0-windows.zip"
    assert not updates.from_api(other).installable  # скачивать можно только из выпусков проекта


def test_check_offers_only_newer_and_is_silent_offline():
    ok = opener_for({updates.LATEST_API: json.dumps(api_answer("v9.0.0")).encode()})
    rel = updates.check("0.1.0", opener=ok)
    assert rel.version == "9.0.0"
    req = ok.seen[0]
    assert req.full_url == updates.LATEST_API and req.get_header("User-agent") == f"st-secretary/{__version__}"
    assert req.data is None  # только запрос — ничего не отправляется
    assert updates.check("9.0.0", opener=ok) is None
    offline = opener_for({updates.LATEST_API: OSError("нет сети")})
    assert updates.check("0.1.0", opener=offline) is None
    broken = opener_for({updates.LATEST_API: b"<html>"})
    assert updates.check("0.1.0", opener=broken) is None


def test_portable_root_only_from_portable_launcher(tmp_path):
    exe = tmp_path / "СТ-Секретарь" / "program" / "python" / "python.exe"
    assert updates.portable_root(str(exe), {"ST_PORTABLE": "1"}) == tmp_path / "СТ-Секретарь"
    assert updates.portable_root(str(exe), {}) is None
    assert updates.portable_root(str(tmp_path / ".venv" / "Scripts" / "python.exe"), {"ST_PORTABLE": "1"}) is None


# ------------------------------------------------------------------ скачивание и распаковка


def test_download_checks_sha256(tmp_path):
    data = portable_zip("9.0.0")
    rel = release("9.0.0", data)
    seen = []
    path = updates.download(rel, tmp_path / "new.zip", lambda d, t: seen.append((d, t)),
                            opener_for({rel.asset_url: data}))
    assert path.read_bytes() == data and seen[-1] == (len(data), len(data))
    bad = release("9.0.0", b"other")
    with pytest.raises(UpdateError, match="контрольная сумма"):
        updates.download(bad, tmp_path / "bad.zip", opener=opener_for({bad.asset_url: data}))
    assert not (tmp_path / "bad.zip").exists() and not (tmp_path / "bad.zip.part").exists()
    with pytest.raises(UpdateError, match="нет архива"):
        updates.download(Release("9.0.0", "x"), tmp_path / "none.zip")


def test_unpack_prepares_program_new_and_keeps_launcher(tmp_path):
    root = tmp_path / "СТ-Секретарь"
    (root / "program" / "python").mkdir(parents=True)
    (root / "СТ-Секретарь.bat").write_bytes(b"@echo old launcher")
    (root / "данные").mkdir()
    archive = tmp_path / "new.zip"
    archive.write_bytes(portable_zip("9.0.0"))
    new = updates.unpack(archive, root, "9.0.0")
    assert new == root / "program.new" and (new / "python" / "python.exe").is_file()
    assert (root / "Прочтите меня.txt").read_text(encoding="utf-8") == "новая версия"
    assert (root / "СТ-Секретарь.bat").read_bytes() == b"@echo old launcher"  # его читает работающая консоль
    assert (root / "program" / "python").is_dir() and (root / "данные").is_dir()
    assert not (root / "program.new.part").exists()


def test_unpack_refuses_wrong_version_and_strange_paths(tmp_path):
    root = tmp_path / "top"
    root.mkdir()
    archive = tmp_path / "a.zip"
    archive.write_bytes(portable_zip("8.0.0"))
    with pytest.raises(UpdateError, match="не та версия"):
        updates.unpack(archive, root, "9.0.0")
    archive.write_bytes(portable_zip("9.0.0", extra={"СТ-Секретарь/program/../../evil.txt": b"x"}))
    with pytest.raises(UpdateError, match="странный путь"):
        updates.unpack(archive, root, "9.0.0")
    archive.write_bytes(portable_zip("9.0.0", extra={"другая/file.txt": b"x"}))
    with pytest.raises(UpdateError, match="не одна папка"):
        updates.unpack(archive, root, "9.0.0")
    assert not (tmp_path / "evil.txt").exists()
    assert sorted(p.name for p in root.iterdir()) == []


def test_installer_runs_backup_first_and_reports_errors(tmp_path):
    data = portable_zip("9.0.0")
    rel = release("9.0.0", data)
    order = []
    inst = Installer(tmp_path)
    assert inst.start(rel, before=lambda: order.append("backup"), opener=opener_for({rel.asset_url: data}), wait=True)
    assert inst.state == "ready" and order == ["backup"] and (tmp_path / "program.new").is_dir()
    assert not inst.start(rel)  # уже готово — второй раз не качает
    assert not (tmp_path / "program.new.zip").exists()

    def no_backup():
        raise UpdateError("резервная копия не сделана (диск полон) — обновление отменено")

    inst = Installer(tmp_path / "другая")
    inst.start(rel, before=no_backup, opener=opener_for({}), wait=True)
    assert inst.status() == {"state": "error", "done": 0, "total": len(data), "version": "9.0.0",
                             "error": "резервная копия не сделана (диск полон) — обновление отменено"}


# ------------------------------------------------------------------ страницы


@pytest.fixture
def web():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from st_secretary.web.app import create_app

    def make(tmp_path, shutdown=None):
        app = create_app(tmp_path / "данные", opener=lambda p: None, shutdown=shutdown, board_host="127.0.0.1")
        return app, TestClient(app)

    return make


def test_home_banner_waits_for_github_answer(tmp_path, web):
    app, client = web(tmp_path)
    assert 'data-update-banner="/update/banner"' in client.get("/").text
    assert client.get("/update/banner").status_code == 204  # проверка выключена
    app.state.update_state = "pending"
    assert client.get("/update/banner").status_code == 202
    app.state.update_state = "done"
    assert client.get("/update/banner").status_code == 204  # новой версии нет
    app.state.update = updates.from_api(api_answer("v9.0.0"))
    r = client.get("/update/banner")
    assert r.status_code == 200 and "Вышла новая версия СТ-Секретаря — 9.0.0" in r.text
    assert "Что нового" in r.text and "01.10.2026" in r.text
    client.post("/update/later")
    assert client.get("/update/banner").status_code == 204  # до следующего запуска


def test_update_page_from_sources_explains_git_pull(tmp_path, web):
    app, client = web(tmp_path)
    assert client.get("/update", follow_redirects=False).status_code in (302, 303)  # обновления нет — на главную
    app.state.update = updates.from_api(api_answer("v9.0.0"))
    r = client.get("/update")
    assert "git pull" in r.text and "Что нового" in r.text and 'action="/update/install"' not in r.text
    assert client.post("/update/install").status_code == 409


def test_update_installs_and_restarts_portable(tmp_path, web):
    stopped = []
    app, client = web(tmp_path, shutdown=lambda: stopped.append(1))
    root = tmp_path / "СТ-Секретарь"
    root.mkdir()
    data = portable_zip("9.0.0")
    rel = release("9.0.0", data)
    inst = Installer(root)
    start = inst.start  # GitHub подменяется, установка — сразу, без фона
    inst.start = lambda rel, before=None: start(rel, before, opener=opener_for({rel.asset_url: data}), wait=True)
    app.state.update, app.state.installer = rel, inst
    training.create(app.state.store, date(2026, 10, 1))
    r = client.get("/update")
    assert 'action="/update/install"' in r.text and "program.old" in r.text
    assert client.post("/update/restart").status_code == 409  # ещё не скачано

    assert client.post("/update/install", follow_redirects=False).status_code in (302, 303)
    assert client.get("/update/status").json()["state"] == "ready"
    assert (root / "program.new" / "python" / "python.exe").is_file()
    assert len(list((tmp_path / "Резервные копии").glob("*.zip"))) == 1  # копия — до скачивания
    assert client.post("/update/restart").json() == {"ok": True}
    assert app.state.restart and stopped == [1]


def test_updated_message_after_restart(tmp_path, web):
    _, client = web(tmp_path)
    assert f"Программа обновлена до версии {__version__}" in client.get("/?done=updated").text


def test_health_reports_version_for_restart_wait(tmp_path, web):
    _, client = web(tmp_path)
    assert client.get("/health").json()["version"] == __version__
    assert Path(updates.__file__).name == "updates.py"


# ------------------------------------------------------------------ три системы: Windows, macOS, Linux


def test_asset_for_each_system():
    assert updates.asset_suffix("win32", "AMD64") == "-windows.zip"
    assert updates.asset_suffix("darwin", "arm64") == "-macos-arm64.tar.gz"
    assert updates.asset_suffix("darwin", "x86_64") == "-macos-x86_64.tar.gz"
    assert updates.asset_suffix("linux", "x86_64") == "-linux-x86_64.tar.gz"
    assert updates.asset_suffix("linux", "aarch64") is None  # сборки нет — только ссылка на выпуск
    data = api_answer("v9.0.0")
    data["assets"].append({"name": "st-secretary-9.0.0-macos-arm64.tar.gz", "size": 7, "digest": "sha256:" + "b" * 64,
                           "browser_download_url": f"{DOWNLOADS}v9.0.0/st-secretary-9.0.0-macos-arm64.tar.gz"})
    mac = updates.from_api(data, "-macos-arm64.tar.gz")
    assert mac.asset_url.endswith("macos-arm64.tar.gz") and mac.sha256 == "b" * 64 and mac.installable
    assert updates.from_api(data, "-windows.zip").asset_url.endswith("windows.zip")
    assert not updates.from_api(data, "-linux-x86_64.tar.gz").installable


def test_portable_root_on_macos_and_linux(tmp_path):
    exe = tmp_path / "СТ-Секретарь" / "program" / "python" / "bin" / "python3"
    assert updates.portable_root(str(exe), {"ST_PORTABLE": "1"}) == tmp_path / "СТ-Секретарь"
    assert updates.portable_root(str(tmp_path / ".venv" / "bin" / "python3"), {"ST_PORTABLE": "1"}) is None


def unix_tar(version: str, extra=None) -> bytes:
    import tarfile

    buf = io.BytesIO()
    top = "СТ-Секретарь"

    def add(name, data=b"", mode=0o644, kind=tarfile.REGTYPE, link=""):
        ti = tarfile.TarInfo(f"{top}/{name}")
        ti.mode, ti.type, ti.linkname, ti.size = mode, kind, link, len(data)
        t.addfile(ti, io.BytesIO(data) if kind == tarfile.REGTYPE else None)

    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        add("program/python/bin/python3.12", b"\x7fELF", 0o755)
        add("program/python/bin/python3", kind=tarfile.SYMTYPE, link="python3.12")
        add("program/python/lib/python3.12/site-packages/st_secretary/__init__.py",
            f'__version__ = "{version}"\n'.encode())
        add("СТ-Секретарь.command", b"#!/bin/bash\necho new\n", 0o755)
        add("Прочтите меня.txt", "новая версия".encode())
        for args in extra or []:
            add(*args)
    return buf.getvalue()


def test_unpack_tar_for_macos_and_linux(tmp_path):
    import os
    import sys

    root = tmp_path / "СТ-Секретарь"
    root.mkdir()
    (root / "СТ-Секретарь.command").write_bytes(b"#!/bin/bash\necho old\n")
    archive = tmp_path / "new.tar.gz"
    archive.write_bytes(unix_tar("9.0.0"))
    new = updates.unpack(archive, root, "9.0.0")
    assert (new / "python" / "bin" / "python3").exists() and updates._version_in(new) == "9.0.0"
    assert (root / "Прочтите меня.txt").read_text(encoding="utf-8") == "новая версия"
    assert (root / "СТ-Секретарь.command").read_bytes() == b"#!/bin/bash\necho old\n"  # файл запуска не трогаем
    assert sorted(p.name for p in root.iterdir()) == ["program.new", "Прочтите меня.txt", "СТ-Секретарь.command"]
    if not sys.platform.startswith("win"):  # права на запуск и ссылка python3 → python3.12 сохранились
        assert os.access(new / "python" / "bin" / "python3.12", os.X_OK)
        assert (new / "python" / "bin" / "python3").is_symlink()


def test_unpack_tar_refuses_links_outside(tmp_path):
    import tarfile

    root = tmp_path / "top"
    root.mkdir()
    archive = tmp_path / "bad.tar.gz"
    archive.write_bytes(unix_tar("9.0.0", [("program/evil", b"", 0o644, tarfile.SYMTYPE, "../../../../etc/passwd")]))
    with pytest.raises(UpdateError, match="странный путь"):
        updates.unpack(archive, root, "9.0.0")
    assert list(root.iterdir()) == []
    archive.write_bytes(b"not an archive")
    with pytest.raises(UpdateError, match="не архив"):
        updates.unpack(archive, root, "9.0.0")


def test_texts_follow_system():
    from st_secretary import system

    assert system.launcher("win32") == "СТ-Секретарь.bat" and system.console("win32") == "чёрное окно"
    assert system.launcher("darwin") == "СТ-Секретарь.command" and system.console("darwin") == "окно Терминала"
    assert system.launcher("linux") == "СТ-Секретарь.sh" and system.console("linux") == "окно терминала"
