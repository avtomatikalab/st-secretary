"""Сверка справочников с первоисточниками и пересчёт реальных протоколов.

Реальные файлы в репозиторий не входят. Тесты запускаются, если задана переменная окружения
ST_REF_DATA — путь к папке с эталонными данными (структура как в рабочей папке организаторов).
"""

import json
import os
import re
import sys
from pathlib import Path

import pytest

xlrd = pytest.importorskip("xlrd")

from st_secretary.reference import disciplines, norm_edition  # noqa: E402

DATA = Path(os.environ["ST_REF_DATA"]) if os.environ.get("ST_REF_DATA") else None
pytestmark = pytest.mark.skipif(DATA is None, reason="не задана ST_REF_DATA — реальные данные недоступны")

SEMINAR = "С семинара/С семинара/Судейский семинар СТ 2026 документы"


def _cell(sh, r, c):
    """Значение ячейки; для объединённых ячеек — значение их левой верхней ячейки."""
    for r1, r2, c1, c2 in getattr(sh, "merged_cells", ()):
        if r1 <= r < r2 and c1 <= c < c2:
            r, c = r1, c1
            break
    v = sh.cell_value(r, c)
    return int(v) if isinstance(v, float) and v.is_integer() else v


def test_vrvs_matches_registry():
    sh = xlrd.open_workbook(str(DATA / SEMINAR / "Reestr_b2e716479e.xls")).sheet_by_name("Общероссийские")
    registry, on = {}, False
    for r in range(sh.nrows):
        kind = str(sh.cell_value(r, 1)).strip()
        if "туризм" in kind.lower():
            on = True
        elif kind and on:
            break
        name = " ".join(str(sh.cell_value(r, 9)).split())
        if on and name:
            k, l, m, n, o, p, q = (_cell(sh, r, c) for c in range(10, 17))
            registry[name] = f"{int(k):03d}{int(l):03d}{int(m)}{int(n)}{int(o)}{int(p)}{q}"
    ours = {d.name: d.code for d in disciplines()}
    assert ours == registry


@pytest.mark.parametrize("edition, file, sheet", [
    ("2022-2025", "rank_appl_2022_2025.xls", "Нормы-Дистанции + СХ"),
    ("2026-2029", "Normy_26-29.xls", "Нормы-дистанции+СХ"),
])
def test_norms_match_source_table(edition, file, sheet):
    # formatting_info — чтобы видеть объединённые ячейки (например, «200 %» на 15 строк).
    sh = xlrd.open_workbook(str(DATA / SEMINAR / file), formatting_info=True).sheet_by_name(sheet)
    cols = {"I": 9, "II": 11, "III": 13, "Y1": 15, "Y2": 17, "Y3": 19}
    source = []
    rank_label = re.compile(r"Менее 1|\d+(?:-\d+(?:,\d+)?)?(?: и более)?")
    for r in range(sh.nrows):
        label = str(_cell(sh, r, 7)).strip()
        if rank_label.fullmatch(label) and (source or label == "Менее 1"):
            row = {"label": label}
            for key, c in cols.items():
                v = _cell(sh, r, c)
                if isinstance(v, int):
                    row[key] = v
            source.append(row)
    ours = norm_edition(edition)
    assert [r["label"] for r in source] == [r.label for r in ours.rows]
    for s, o in zip(source, ours.rows):
        assert {k: v for k, v in s.items() if k != "label"} == {q.name: v for q, v in o.thresholds.items()}, s["label"]


PSR2025 = "Эталонные_данные/ПСР/2025_Чемпионат_Красноярска"
PREAPPS_2025 = "С семинара/С семинара/секретариат/19-21.09.2025 комби/Предзаявки"


def test_psr2025_preapplications():
    """13 реальных предзаявок ЧГ Красноярска ПСР-2025: проверяем количество и виды замечаний (без ФИО)."""
    from st_secretary.importers.card_xlsx import load_card
    from st_secretary.importers.preapp_xlsx import read_preapplication
    from st_secretary.issues import ERROR, WARNING
    from st_secretary.preapp import process

    card = DATA / PSR2025 / "Карточка_соревнования.xlsx"
    if not card.exists():
        pytest.skip("нет карточки ПСР-2025 в эталонных данных")
    comp = load_card(card)
    files = sorted((DATA / PREAPPS_2025).glob("*.xlsx"))
    r = process([read_preapplication(p) for p in files], comp)
    assert (len(files), len(r.teams), len(r.entries)) == (13, 13, 39)
    errors = [i.text for i in r.issues if i.severity == ERROR]
    assert len(errors) == 1 and "не существует" in errors[0]  # 29 февраля невисокосного года
    warnings = [i.text for i in r.issues if i.severity == WARNING]
    assert sum("по решению ГСК" in w for w in warnings) == 8  # моложе 22 лет
    assert sum("мужское" in w for w in warnings) == 1  # пол не совпадает с отчеством
    assert not any("исправлена на «Краснорярск»" in i.text for i in r.issues)
    assert {t.territory for t in r.teams} == {"Красноярск", "Томск"}
    assert all(e.zachet is not None for e in r.entries)


def test_psr2024_reconciliation():
    sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
    from reconcile_psr2024 import reconcile

    report, fixture = reconcile(DATA)
    assert "Совпало полностью: 10 из 11" in report
    assert "0840271811Я" in report and "спелео - группа" in report
    assert "по составам 170" in report
    fixed = Path(__file__).parent / "fixtures" / "psr2024.json"
    assert json.loads(fixed.read_text(encoding="utf-8")) == fixture, "обезличенный набор устарел — перегенерируйте"


def test_psr2024_result_protocol_to_diplomas(tmp_path):
    """Итоговый протокол ПСР-2024 из СЕКРЕТАРЬ_ST → места и дипломы (без вывода ФИО)."""
    from st_secretary import results as res
    from st_secretary.exporters.awards import medal_count, write_diplomas
    from st_secretary.importers.card_xlsx import load_card
    from st_secretary.importers.sekretar_xls import read_result_protocol

    proto_path = DATA / "С семинара" / "С семинара" / "секретариат" / "19-21.09.2025 комби" / "Result_PSR_2024.xls"
    card = DATA / PSR2025 / "Карточка_соревнования.xlsx"
    if not proto_path.exists() or not card.exists():
        pytest.skip("нет протокола ПСР-2024 или карточки 2025 в эталонных данных")
    comp = load_card(card)
    d = res.from_protocol(read_result_protocol(proto_path))
    assert len(d["rows"]) == 11 and d["group_text"].endswith("СМЕШАННЫЕ ГРУППЫ")
    assert [r["place"] for r in d["rows"][:3]] == [1, 2, 3] and all(len(r["members"]) == 3 for r in d["rows"])
    z = res.load({"zachety": {comp.zachety[0].key: d}}, comp, None)
    assert medal_count(z) == 9
    from docx import Document
    texts = [p.text for p in Document(write_diplomas(z, comp, tmp_path / "d.docx")).paragraphs if p.text]
    assert texts.count("Награждаются") == 9 and "за I место" in texts


def test_verify_2025_folder():
    """Сверка папки ЧГК 2025: находятся известные ошибки (без вывода ФИО)."""
    from st_secretary.importers.card_xlsx import load_card
    from st_secretary.verify import Known, read_doc, verify

    folder = DATA / "С семинара" / "С семинара" / "секретариат" / "19-21.09.2025 комби"
    card = DATA / PSR2025 / "Карточка_соревнования.xlsx"
    if not (folder / "Договора").exists() or not card.exists():
        pytest.skip("нет папки ЧГК 2025 или карточки в эталонных данных")
    comp = load_card(card)
    files = [folder / n for n in ("Награждается.docx", "Наклейки на медали.xlsx", "Result_PSR_2024.xls")]
    files += sorted((folder / "Договора").iterdir())
    rep = verify([read_doc(p) for p in files], comp, [Known(o.fio, o.category, "в карточке") for o in comp.officials])
    t = [i.text for i in rep.all_issues]
    assert sum("октября 2024» — не 2025 год" in x for x in t) == 3  # дипломы, наклейки, протокол — даты 2024 г.
    assert any("0840271811Я — это «дистанция - спелео - группа»" in x for x in t)
    assert sum("«комбинированые» — похоже на опечатку" in x for x in t) == 17  # во всех договорах
    assert sum("отмечено дней «р» — 3, а к оплате — 4" in x for x in t) == 2
    assert sum(": срок в договоре «19-21 сентября 2025 года», а в акте" in x for x in t) == 5
    assert sum("в акте 4 дн., а в периоде «19-21 сентября 2025» столько дней нет" in x for x in t) == 7
    assert sum("имя и отчество слитно" in x for x in t) == 2
    assert (rep.contracts, rep.tabel_rows) == (17, 17)
    assert rep.cross == []  # дни, ставки и суммы договоров сходятся с табелем
