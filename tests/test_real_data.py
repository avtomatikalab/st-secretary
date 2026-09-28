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


def test_psr2024_reconciliation():
    sys.path.insert(0, str(Path(__file__).parents[1] / "tools"))
    from reconcile_psr2024 import reconcile

    report, fixture = reconcile(DATA)
    assert "Совпало полностью: 10 из 11" in report
    assert "0840271811Я" in report and "спелео - группа" in report
    assert "по составам 170" in report
    fixed = Path(__file__).parent / "fixtures" / "psr2024.json"
    assert json.loads(fixed.read_text(encoding="utf-8")) == fixture, "обезличенный набор устарел — перегенерируйте"
