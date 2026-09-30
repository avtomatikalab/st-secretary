"""Таблицы штрафов: встроенные из Правил (пешеходные, спелео), своя из Excel (Правки.md, п. 15)."""

import io

import pytest
from openpyxl import Workbook

from st_secretary import penalties as pen
from st_secretary.competition import Zachet


def test_builtin_tables_from_rules():
    ped = pen.builtin("pedestrian")
    rows = {r.code: r for r in ped.rows}
    assert ped.systems and len(ped.rows) == 26 and "таблица 11" in ped.source
    one = rows["1"]
    assert (one.title, one.nopenalty, one.value) == ("Не заблокирована защёлка карабина", "См. п. 6.2.2", 1)
    assert rows["12.3"].points == "Снятие с этапа (блока этапов)" and rows["12.3"].value is None
    assert rows["17"].points == "10 минут" and rows["17"].value is None and rows["2"].group
    assert rows["2.2"].points == "Предупреждение" and "1 минута" in rows["2.2"].note

    sp = pen.builtin("speleo")
    r = {x.code: x for x in sp.rows}
    assert len([x for x in sp.rows if x.group]) == 10 and "приложение 2" in sp.source
    assert r["7"].group and r["7"].points == "0,3" and r["7.2.9"].value == 0.3 and r["7.2.9"].section == "Спуск"
    assert r["6.1.1"].title == "Карабин на страховочной или тяговой верёвке не замуфтован" and r["6.1.1"].value == 1
    assert r["1.1"].points.startswith("Снятие") and r["10.1"].value == 0.1 and "7.2.30" not in r


def test_default_table_by_discipline_and_choice():
    ped_z, speleo_z, psr = Zachet("М/Ж", 2, "0840091811Я"), Zachet("М/Ж", 2, "0840271811Я"), Zachet("М/Ж", 3, "0840161811Я")
    assert (pen.default_key(ped_z), pen.default_key(speleo_z), pen.default_key(psr)) == ("pedestrian", "speleo", "")
    assert pen.table_for(psr, {}) is None  # ПСР: таблица штрафов — в Условиях
    assert pen.table_for(psr, {"penalty_table": "pedestrian"}).key == "pedestrian"  # за основу — пешеходная
    assert pen.table_for(ped_z, {"penalty_table": "none"}) is None
    assert pen.table_for(psr, {"penalty_table": "custom"}) is None  # своя не загружена
    assert pen.points_value("10 баллов (см. п. 6.3.2)") == 10 and pen.points_value("0,3") == 0.3
    assert pen.points_value("10 минут") is None and pen.points_value("Снятие") is None


def test_own_table_from_excel(tmp_path):
    back = pen.read_excel(pen.write_excel(pen.builtin("pedestrian"), tmp_path / "t.xlsx").read_bytes())
    assert back[0].code == "1" and back[0].points == "1 балл (см. п. 6.3.2)" and back[0].value == 1
    wb = Workbook()
    ws = wb.active
    ws.append(["Таблица штрафов этапов (Условия)"])
    ws.append(["Пункт", "Нарушение", "Баллы", "Примечание"])
    ws.append([1, "Нет каски на этапе", 5, "каска — на всех этапах"])
    ws.append([None, None, None, None])
    ws.append(["2.1", "Потеря снаряжения", "0,5", ""])
    buf = io.BytesIO()
    wb.save(buf)
    rows = pen.read_excel(buf.getvalue())
    assert [(x.code, x.title, x.value, x.note) for x in rows] == [
        ("1", "Нет каски на этапе", 5, "каска — на всех этапах"), ("2.1", "Потеря снаряжения", 0.5, "")]
    with pytest.raises(ValueError):
        pen.read_excel(b"not excel")
