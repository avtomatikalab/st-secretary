"""Судья искал пункт штрафа на телефоне и не нашёл (Правки, п. 62): запрос уходит на ноутбук с записями, секретарь
добавляет слова судей, список прикладывается к «Сообщить». Данные — выдуманные."""

import json
import re

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from test_web import base, kedr

from st_secretary import feedback as fb
from st_secretary import judge_sync as js
from st_secretary.web.app import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "данные", opener=lambda p: None, docs_dir=tmp_path / "документы",
                                 board_host="127.0.0.1"))


def test_add_misses_cleans_and_counts():
    zdata = {}
    got = js.add_misses(zdata, "s1", [{"q": "  Не  Замуфтован ", "n": 2}, {"q": "6.2", "n": 1}, {"q": "x"},
                                      "мусор", {"q": "ёлка", "n": "много"}], "2026-10-10T10:00:00")
    assert got == 2 and set(zdata["pen_misses"]) == {"не замуфтован", "елка"}
    js.add_misses(zdata, "s2", [{"q": "не замуфтован", "n": 1}], "2026-10-10T11:00:00")
    m = js.misses(zdata)[0]
    assert m == {"q": "не замуфтован", "n": 3, "stages": ["s1", "s2"], "last": "2026-10-10T11:00:00"}


def test_phone_misses_reach_secretary_jargon_and_feedback(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Кедр.xlsx", kedr(tmp_path))
    q = "?z=М/Ж_3"
    client.post(base(f) + "/results/stages" + q, data={"st-0-tour": "Тур 1", "st-0-name": "Узлы"})
    client.post(base(f) + "/judges/penalties" + q, data={"table": "pedestrian"})
    client.post(base(f) + "/judges/link" + q, data={"stage": "s1"})
    token = next(iter(f.run_data()["judge_links"]))
    phone = TestClient(client.app.state.board.app)
    page = phone.get(f"/j/{token}").text
    assert "noteMiss(" in page and "misses: miss" in page  # телефон копит и отправляет с записями

    j = phone.post(f"/j/{token}/sync", json={"device": "т-1", "records": [],
                                              "misses": [{"q": "не замуфтован", "n": 2}]}).json()
    assert j["ok"] and j["misses"] == 1
    jp = client.get(base(f) + "/judges" + q).text
    assert "Судьи искали и не нашли" in jp and "не замуфтован" in jp and "Тур 1 · Узлы" in jp

    # «Сообщить» с этого соревнования — список в сообщении (для словаря программы)
    r = client.post("/feedback", json={"kind": "Предложение", "text": "Добавьте слова",
                                       "page": {"url": base(f) + "/judges" + q, "title": "Телефоны судей этапов"}})
    assert r.json()["ok"]
    msg = fb.messages(fb.folder_for(client.app.state.store.root))[0].path.read_text(encoding="utf-8")
    assert "## Судьи искали и не нашли" in msg and "«не замуфтован» — 2 раз (зачёт М/Ж_3)" in msg

    r = client.post(base(f) + "/judges/misses" + q, data={"q": "не замуфтован", "do": "add", "to": "защёлка карабина"},
                    follow_redirects=False)
    assert "done=miss_added" in r.headers["location"]
    zdata = f.run_data()["zachety"]["М/Ж_3"]
    assert "не замуфтован = защёлка карабина" in zdata["pen_jargon"] and zdata["pen_misses"] == {}
    data = json.loads(re.search(r'id="data">(.*?)</script>', phone.get(f"/j/{token}").text, re.DOTALL).group(1))
    assert ["не замуфтован", "защелка карабина"] in data["penalties"]["jargon"]  # телефон получит слово для поиска
    assert "Судьи искали и не нашли" not in client.get(base(f) + "/judges" + q).text

    phone.post(f"/j/{token}/sync", json={"device": "т-1", "records": [], "misses": [{"q": "каска", "n": 1}]})
    client.post(base(f) + "/judges/misses" + q, data={"q": "каска", "do": "forget"})
    assert f.run_data()["zachety"]["М/Ж_3"]["pen_misses"] == {}


def test_penalty_choices_explained_under_list(client, psr_card):
    """Правки, п. 63: под списком «Таблица штрафов» — чем отличаются варианты; числа пунктов — из справочника."""
    from st_secretary import penalties as pen

    f = client.app.state.store.create(psr_card)
    page = client.get(base(f) + "/judges?z=М/Ж_3").text
    assert 'class="hint pen-help"' in page and "программа выберет сама" in page
    for key in pen.BUILTIN:
        assert f"{len(pen.builtin(key).rows)} пунктов" in page
    assert "две системы оценки" in page and "кнопки «Таблица штрафов» на телефоне судьи не будет" in page
