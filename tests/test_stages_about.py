"""Этапы в карточке, описание этапа и зачёта, схема дистанции (Правки, п. 65). Данные — выдуманные."""

import json
import re
from dataclasses import replace
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from test_web import FormFields, base, kedr

from st_secretary.importers.card_xlsx import load_card, write_card
from st_secretary.web.app import create_app

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "данные", opener=lambda p: None, docs_dir=tmp_path / "документы",
                                 board_host="127.0.0.1"))


def test_zachet_description_in_card_form_view_and_excel(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    edit = client.get(base(f) + "/card/edit").text
    assert 'name="z-0-description"' in edit
    data = FormFields(edit, "cardform").fields | {"z-0-description": "Командная дистанция в лесу.\nСтарт у реки."}
    r = client.post(base(f) + "/card/edit", data=data, follow_redirects=False)
    assert r.status_code == 303
    assert f.load().zachety[0].description == "Командная дистанция в лесу.\nСтарт у реки."
    view = client.get(base(f) + "/card").text
    assert "Командная дистанция в лесу." in view and 'class="muted z-desc"' in view
    back = load_card(write_card(tmp_path / "Карточка.xlsx", f.load()))
    assert back.zachety[0].description == "Командная дистанция в лесу.\nСтарт у реки."


def test_stages_line_in_card_and_stage_description_for_judges(client, tmp_path, psr_card):
    f = client.app.state.store.create(psr_card)
    f.add_preapp("Кедр.xlsx", kedr(tmp_path))
    zk = psr_card.zachety[0].key
    q = "?z=" + quote(zk)
    view = client.get(base(f) + "/card").text
    assert "Этапы не заданы — <a" in view and f'href="{base(f)}/results?z={quote(zk)}#stages"' in view
    client.post(base(f) + "/results/stages" + q, data={"st-0-tour": "Тур 1", "st-0-name": "Узлы",
                                                        "st-0-desc": "Три узла на время; судей двое.",
                                                        "st-1-tour": "Тур 1", "st-1-name": "Бивак"})
    zdata = f.run_data()["zachety"][zk]
    assert zdata["stages"][0]["desc"] == "Три узла на время; судей двое." and "desc" not in zdata["stages"][1]
    assert "Этапы: 2 — <a" in client.get(base(f) + "/card").text
    assert "Три узла на время; судей двое." in client.get(base(f) + "/results" + q).text  # в таблице этапов

    client.post(base(f) + "/judges/link" + q, data={"stage": "s1"})
    token = next(iter(f.run_data()["judge_links"]))
    phone = TestClient(client.app.state.board.app)
    page = phone.get(f"/j/{token}").text
    assert "Об этапе" in page and "Три узла на время; судей двое." in page  # судье этого этапа
    printed = client.get(base(f) + "/judges/print" + q).text
    assert "Три узла на время; судей двое." in printed and "Этапы дистанции — зачёт" in printed
    assert "Три узла" not in client.get(base(f) + "/start" + q).text  # в стартовом протоколе — нет


def test_distance_scheme_upload_view_phone_print_remove(client, tmp_path, psr_card):
    f = client.app.state.store.create(replace(psr_card))
    f.add_preapp("Кедр.xlsx", kedr(tmp_path))
    zk = psr_card.zachety[0].key
    q = "?z=" + quote(zk)
    client.post(base(f) + "/results/stages" + q, data={"st-0-tour": "Тур 1", "st-0-name": "Узлы"})
    r = client.post(base(f) + "/results/scheme" + q, files=[("files", ("Схема 1.png", PNG, "image/png")),
                                                            ("files", ("Лист 2.pdf", b"%PDF-1.4 x", "application/pdf"))],
                    follow_redirects=False)
    assert "done=scheme_added" in r.headers["location"]
    assert [p.name for p in f.schemes(zk)] == ["Лист 2.pdf", "Схема 1.png"]
    r = client.post(base(f) + "/results/scheme" + q, files=[("files", ("вирус.exe", b"MZ", "application/x"))],
                    follow_redirects=False)
    assert "done=scheme_bad" in r.headers["location"] and len(f.schemes(zk)) == 2
    img = client.get(base(f) + "/scheme/" + quote("Схема 1.png") + q)
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content == PNG
    assert client.get(base(f) + "/scheme/" + quote("../Карточка_соревнования.xlsx") + q).status_code == 404
    f.add_scheme(zk, "рисунок.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    svg = client.get(base(f) + "/scheme/" + quote("рисунок.svg") + q)
    assert svg.headers["content-security-policy"] == "sandbox"  # SVG — без скриптов

    view = client.get(base(f) + "/card").text
    assert "<img src=" in view and "Схема дистанции: Схема 1.png" in view and "Лист 2.pdf" in view
    client.post(base(f) + "/judges/link" + q, data={"stage": "s1"})
    token = next(iter(f.run_data()["judge_links"]))
    phone = TestClient(client.app.state.board.app)
    page = phone.get(f"/j/{token}").text
    assert f"/j/{token}/scheme/" in page and "Схема открывается, когда телефон в Wi-Fi ноутбука" in page
    assert phone.get(f"/j/{token}/scheme/" + quote("Схема 1.png")).content == PNG  # с ноутбука по Wi-Fi
    assert phone.get(f"/j/{token}/scheme/" + quote("нет.png")).status_code == 404
    local = client.get(base(f) + f"/judges/phone/{token}").text
    assert f"{base(f)}/scheme/" in local  # на ноутбуке — с самой программы
    printed = client.get(base(f) + "/judges/print" + q).text
    assert 'class="scheme"' in printed and "PDF: <a" in printed

    client.post(base(f) + "/results/scheme" + q, data={"do": "remove", "name": "Схема 1.png"})
    assert [p.name for p in f.schemes(zk)] == ["Лист 2.pdf", "рисунок.svg"]
    data = json.loads(re.search(r'id="data">(.*?)</script>', phone.get(f"/j/{token}").text, re.DOTALL).group(1))
    assert "sync_url" in data  # страница судьи работает как прежде
