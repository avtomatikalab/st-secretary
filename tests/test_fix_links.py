"""«Исправить» у замечаний ведёт прямо к полю (Правки.md, п. 6), и JS страниц программы разбирается без ошибок."""

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from st_secretary.issues import ERROR, Issue
from st_secretary.web.common import fix_url, team_anchor

WEB = Path(__file__).parents[1] / "src" / "st_secretary" / "web"
B = "/c/Соревнование"


def focus(url: str) -> str:
    return parse_qs(urlsplit(url).query).get("focus", [""])[0]


def test_fix_url_for_each_kind():
    assert fix_url(B, Issue(ERROR, "x")) == ""  # вести некуда — кнопки нет
    u = fix_url(B, Issue(ERROR, "x", target="rate:главный судья|1"))
    assert u.startswith(f"{B}/contracts?") and u.endswith("#settings") and focus(u) == "rate-главный судья|1"
    u = fix_url(B, Issue(ERROR, "x", target="person:орлов виктор:inn"))
    assert urlsplit(u).path == f"{B}/contracts/person" and focus(u) == "f-inn"
    assert parse_qs(urlsplit(u).query)["key"] == ["орлов виктор"]
    assert focus(fix_url(B, Issue(ERROR, "x", target="tabel:орлов виктор"))) == f"tabel-{team_anchor('орлов виктор')}"
    assert fix_url(B, Issue(ERROR, "x", target="customer")).endswith("#customer")
    assert fix_url(B, Issue(ERROR, "x", target="card:gsk")) == f"{B}/card/edit#gsk"
    u = fix_url(B, Issue(ERROR, "x", source="М/Ж_3", target="cell:Кедр.xlsx:s2"))
    assert urlsplit(u).path == f"{B}/results" and parse_qs(urlsplit(u).query)["z"] == ["М/Ж_3"]
    assert focus(u) == f"c-{team_anchor('Кедр.xlsx')}-s2" and u.endswith("#points")
    u = fix_url(B, {"target": "adm:Кедр.xlsx/td-app"})  # причины «не допущена» — не Issue, а строки
    assert focus(u) == f"{team_anchor('Кедр.xlsx')}/td-app" and u.endswith("#" + team_anchor("Кедр.xlsx"))
    assert "reentry=1" in fix_url(B, {"target": "reentry:Кедр.xlsx"})
    assert focus(fix_url(B, Issue(ERROR, "x", source="М/Ж_3", target="start:first"))) == "first"


def test_contracts_and_results_lists_have_fix_links(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from st_secretary.web.app import create_app

    client = TestClient(create_app(tmp_path / "данные", opener=lambda p: None, docs_dir=tmp_path / "документы",
                                   board_host="127.0.0.1"))
    client.post("/training")
    f = client.app.state.store.all()[0]
    b = "/c/" + quote(f.id, safe="")
    html = client.get(b + "/contracts").text
    assert "не задана ставка" in html and "Исправить" in html
    assert re.search(r'href="[^"]*/contracts\?focus=rate-[^"]*#settings"', html)
    assert 'id="tabel-' in html  # строка табеля — цель «нет дней»

    z = "М/Ж_3"
    run = client.app.state.store.get(f.id).run_data()
    file = next(iter(run["zachety"][z].get("teams", {})), None) or "Бурундуки.xlsx"
    client.post(b + "/results/points?z=" + quote(z), data={"p-0-file": file, "p-0-s1": "abc", "p-0-status": "finished"})
    html = client.get(b + "/results?z=" + quote(z)).text
    anchor = team_anchor(file)
    assert f'id="c-{anchor}-s1"' in html and "не число" in html
    assert re.search(rf'href="[^"]*/results\?z=[^"]*focus=c-{re.escape(anchor)}-s1#points"', html)


@pytest.mark.skipif(shutil.which("node") is None, reason="нет Node.js — синтаксис JS не проверить")
def test_page_scripts_parse(tmp_path):
    """app.js и скрипт страницы судьи: одна синтаксическая ошибка выключает всё поведение страниц."""
    subprocess.run(["node", "--check", str(WEB / "static" / "app.js")], check=True)
    judge = (WEB / "templates" / "judge.html").read_text(encoding="utf-8")
    script = judge.split("<script>")[-1].split("</script>")[0]
    js = tmp_path / "judge.js"
    js.write_text(script, encoding="utf-8")
    subprocess.run(["node", "--check", str(js)], check=True)
