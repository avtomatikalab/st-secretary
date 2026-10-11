"""Приём «Сообщить» на сервере (tools/feedback_server, решение 050) и отправка из программы. Всё выдуманное, сервер —
на этом компьютере, на свободном порту."""

import importlib.util
import io
import json
import threading
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from st_secretary import feedback as fb

spec = importlib.util.spec_from_file_location("receiver", Path(__file__).parents[1] / "tools" / "feedback_server" / "receiver.py")
rcv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rcv)

TOKEN = "test-pickup-key-0123456789abcdef"


@pytest.fixture
def server(tmp_path):
    srv = rcv.serve(tmp_path / "приём", TOKEN, port=0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", tmp_path / "приём"
    srv.shutdown()
    srv.server_close()


def bundle(tmp_path) -> Path:
    folder = tmp_path / "Правки и ошибки"
    fb.save(folder, "Ошибка", "Время старта пустое.", {"who": "Секретарёва А."}, None,
            now=datetime(2026, 10, 11, 15, 42, 7), host="PC-1")
    path, _ = fb.bundle(folder, "журнал", "0.5.1 бета", now=datetime(2026, 10, 11, 17, 5), host="PC-1")
    return path


def call(url, method="GET", data=None, token=None):
    req = urllib.request.Request(url, data=data, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def zip_of(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_program_uploads_and_owner_picks_up(tmp_path, server):
    base, folder = server
    path = bundle(tmp_path)
    ident = fb.upload(path, url=f"{base}/api/feedback")
    assert ident and rcv.ID_RE.match(ident) and (folder / f"{ident}.zip").read_bytes() == path.read_bytes()

    assert call(f"{base}/api/pickup")[0] == 403 and call(f"{base}/api/pickup", token="someone-else")[0] == 403
    code, body = call(f"{base}/api/pickup", token=TOKEN)
    assert code == 200 and [x["id"] for x in json.loads(body)] == [ident]
    code, body = call(f"{base}/api/pickup/{ident}", token=TOKEN)
    assert code == 200 and body == path.read_bytes()
    assert call(f"{base}/api/pickup/{ident}", "DELETE", token=TOKEN)[0] == 200
    assert json.loads(call(f"{base}/api/pickup", token=TOKEN)[1]) == []
    assert call(f"{base}/api/pickup/../../etc", token=TOKEN)[0] == 404


def test_server_refuses_what_is_not_a_bundle(tmp_path, server):
    base, folder = server
    manifest = json.dumps({"app": "st-secretary"}).encode()
    bad = {
        "не zip": b"hello",
        "чужой zip": zip_of({"manifest.json": b'{"app": "other"}'}),
        "путь в архиве": zip_of({"manifest.json": manifest, "../x.md": b"x"}),
        "программа": zip_of({"manifest.json": manifest, "run.exe": b"MZ"}),
    }
    for what, data in bad.items():
        code, body = call(f"{base}/api/feedback", "POST", data)
        assert code == 422 and not json.loads(body)["ok"], what
    assert call(f"{base}/api/feedback", "POST", zip_of({"manifest.json": manifest, "a.md": b"x"}))[0] == 200
    assert call(f"{base}/api/other", "POST", b"x")[0] == 404
    assert len(list(folder.glob("*.zip"))) == 1


def test_zip_bomb_and_full_disk(tmp_path, monkeypatch):
    store = rcv.Store(tmp_path / "приём", TOKEN)
    manifest = json.dumps({"app": "st-secretary"}).encode()
    monkeypatch.setattr(rcv, "MAX_UNPACKED", 1000)
    assert "распакованном" in store.check(zip_of({"manifest.json": manifest, "a.txt": b"0" * 5000}))
    monkeypatch.setattr(rcv, "MAX_UNPACKED", 80 * 1024 * 1024)
    ok = zip_of({"manifest.json": manifest, "a.txt": b"0" * 5000})
    monkeypatch.setattr(rcv, "MAX_STORED", len(ok) + 10)
    assert store.save(ok)[0] and "переполнен" in store.save(ok)[1]


def test_upload_gives_none_without_server(tmp_path):
    """Нет интернета / сервер не отвечает — None: программа откроет письмо, как раньше."""
    assert fb.upload(bundle(tmp_path), url="http://127.0.0.1:9/api/feedback", timeout=2) is None


def test_send_button_uploads_and_page_says_so(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from st_secretary.web.app import create_app

    opened, sent = [], []
    app = create_app(tmp_path / "данные", opener=opened.append, docs_dir=tmp_path / "документы", board_host="127.0.0.1")
    client = TestClient(app)
    client.post("/feedback", json={"kind": "Ошибка", "text": "Не видно времени старта.", "page": {}})
    app.state.fb_upload = lambda path: sent.append(path) or "20261011-170500-abcdef"
    r = client.post("/feedback/send", follow_redirects=False)
    assert "fb_uploaded" in r.headers["location"] and sent and not opened  # папку и письмо не открывали
    page = client.get(r.headers["location"]).text
    assert "отправлены разработчику через интернет" in page and "отправлен через интернет" in page
    assert "mailto:" not in page and "Есть неотправленные" not in client.get("/").text

    client.post("/feedback", json={"kind": "Неудобно", "text": "Ещё одно.", "page": {}})
    app.state.fb_upload = lambda path: None  # нет связи — письмо, как раньше
    r = client.post("/feedback/send", follow_redirects=False)
    assert "fb_sent" in r.headers["location"] and opened[-1].name == "Отправка"
    assert "mailto:support.st.secretary@gmail.com?subject=" in client.get(r.headers["location"]).text
