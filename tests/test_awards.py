"""Итоги и документы награждения: протокол СЕКРЕТАРЬ_ST → места; дипломы, наклейки, список награждаемых."""

from datetime import date
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from openpyxl import load_workbook

from st_secretary import results as res
from st_secretary.exporters import awards as aw
from st_secretary.importers.preapp_xlsx import read_preapplication
from st_secretary.importers.sekretar_xls import ResultRow
from st_secretary.preapp import process

from conftest import make_application


def protocol():
    """Итоговый протокол, как его читает importers.sekretar_xls (люди выдуманы)."""
    rows = [
        ResultRow("3", "Кедр", "Лебедев Антон Игоревич(III), Зуева Мария Олеговна(III), Носов Глеб Андреевич(б/р)",
                  "Красноярск", {}, Fraction(-283), "1", None, "II"),
        ResultRow("9", "Сосна", "Орлов Павел Ильич(КМС), Белова Ирина Петровна(б/р), Пестов Юрий Андреевич(КМС)",
                  "Томск", {}, Fraction(-213), "2", None, "II"),
        ResultRow("6", "Ель", "Ким Олег Борисович(КМС), Лис Ева Павловна(I), Рыбин Иван Ильич(КМС)",
                  "Красноярск", {}, Fraction(-60), "3", None, ""),
        ResultRow("2", "Пихта", "Сом Яков Ильич(б/р), Ёж Инна Олеговна(б/р), Кот Лев Ильич(б/р)",
                  "Красноярск", {}, None, "", None, ""),
    ]
    head = ["ПРОВОДЯЩИЕ", "ЧЕМПИОНАТ Г. N ПО СПОРТИВНОМУ ТУРИЗМУ", "20-21 сентября 2025 г.", "окрестности г. N",
            'Протокол соревнований в дисциплине: "дистанция - комбинированная" 3 класса, код ВРВС 0840161811Я '
            "МУЖЧИНЫ/ЖЕНЩИНЫ. СМЕШАННЫЕ ГРУППЫ", "Квалификационный ранг дистанции: 71,7"]
    return SimpleNamespace(path=Path("Result_ПСР.xls"), header_lines=head, rows=rows, rank_text="71,7")


def zres(psr_card, preapps=None):
    return res.load({"zachety": {"М/Ж_3": res.from_protocol(protocol())}}, psr_card, preapps)


def test_protocol_import(tmp_path, psr_card):
    d = res.from_protocol(protocol())
    assert d["group_text"] == "МУЖЧИНЫ/ЖЕНЩИНЫ. СМЕШАННЫЕ ГРУППЫ" and d["rank"] == "71,7"
    assert d["rows"][0]["members"][0] == {"fio": "Лебедев Антон Игоревич", "qual": "III"}
    assert d["rows"][3]["place"] is None and d["rows"][1]["result"] == "-213"
    # дата рождения и пол — из заявки: в протоколе их нет
    a = make_application(tmp_path / "Кедр.xlsx", "Кедр", "Красноярск", "Лебедев Антон Игоревич", "89135550000", 3, [
        ["Кедр", "Красноярск", "Лебедев Антон Игоревич", "Лебедев Антон Игоревич", "02.02.1990", "III", "м", "М/Ж", 3]])
    [z] = zres(psr_card, process([read_preapplication(a)], psr_card))
    first = z.rows[0].members[0]
    assert first.birth == date(1990, 2, 2) and first.sex == "м" and first.first_last == "Антон Лебедев"
    assert [p.team for p in z.medalists] == ["Кедр", "Сосна", "Ель"] and z.rows[-1].place is None


def test_texts():
    assert res.rotations(["А", "Б", "В"]) == [["А", "Б", "В"], ["Б", "В", "А"], ["В", "А", "Б"]]
    comp = SimpleNamespace(title="Чемпионат г. Красноярска по спортивному туризму")
    assert res.title_in(comp) == "Чемпионате г. Красноярска по спортивному туризму"
    assert res.title_in(SimpleNamespace(title="Открытое первенство города")) == "Открытом первенстве города"
    assert res.date_text(date(2025, 9, 21)) == "21 сентября 2025 г."


def text_of(path):
    return [p.text for p in Document(path).paragraphs if p.text]


def test_diplomas_each_member_first(tmp_path, psr_card):
    out = aw.write_diplomas(zres(psr_card), psr_card, tmp_path / "d.docx")
    t = text_of(out)
    assert t.count("Награждаются") == 9  # 3 призёра × 3 участника — по диплому каждому
    firsts = [t[i + 1] for i, s in enumerate(t) if s == "Награждаются"][:3]
    assert firsts == ["Антон Лебедев, Мария Зуева, Глеб Носов", "Мария Зуева, Глеб Носов, Антон Лебедев",
                      "Глеб Носов, Антон Лебедев, Мария Зуева"]
    assert "за I место" in t and "за III место" in t and "за IV место" not in " ".join(t)
    assert "на Чемпионате города N по спортивному туризму" in t
    assert "в спортивной дисциплине «дистанция - комбинированная» 3 класса, смешанные группы" in t  # название — как в ВРВС
    assert "окрестности г. N, 21 сентября 2025 г." in t  # дата — из карточки, а не прошлогодняя


def test_stickers_and_awardees(tmp_path, psr_card):
    z = zres(psr_card)
    ws = load_workbook(aw.write_stickers(z, psr_card, tmp_path / "s.xlsx")).active
    stickers = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert len(stickers) == 9 + 2 and stickers[0].startswith("ЧЕМПИОНАТ ГОРОДА N")  # 9 медалей + 2 запасные
    t = text_of(aw.write_awardees(z, psr_card, tmp_path / "l.docx"))
    places = [s.split(" место")[0] for s in t if " место — " in s]
    assert places == ["III", "II", "I"]  # в порядке вызова
    assert any("I место — команда «Кедр», Красноярск: Антон Лебедев, Мария Зуева, Глеб Носов" == s for s in t)
