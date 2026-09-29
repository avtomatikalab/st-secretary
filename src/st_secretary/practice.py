"""Учёт судейской практики: кто, где и в какой должности судил — по всем соревнованиям в программе.

Для присвоения и подтверждения квалификационных категорий (Квалификационные требования к спортивным судьям,
приказ Минспорта № 1101) учитываются соревнования, должность, вхождение в ГСК, статус соревнований и оценка
судейства. Программа собирает это из карточек соревнований (лист «ГСК»), оценок на странице итогов и
судей, добавленных на странице договоров. Баллы по таблицам приказа программа пока не считает — сводка и
выгрузка для квалификационной комиссии.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from st_secretary.competition import GSK_ROLES, LEVEL_LABELS, Competition
from st_secretary.results import person_key

# Должности судей (а не комендантской бригады) среди добавленных на странице договоров.
_JUDGING = re.compile(r"суд|секретар|инспектор|начальник дистанции|хронометр|стартер|стартёр", re.IGNORECASE)
_GSK = {r.lower() for r in GSK_ROLES}


@dataclass
class Record:
    fio: str
    title: str
    date_from: date
    date_to: date
    kind: str
    level: str
    role: str
    category: str
    in_gsk: bool
    grade: str = ""
    competition: str = ""  # папка соревнования


@dataclass
class Judge:
    key: str
    fio: str
    records: list[Record] = field(default_factory=list)

    @property
    def category(self) -> str:
        """Категория из последнего по дате соревнования."""
        dated = [r for r in self.records if r.category]
        return max(dated, key=lambda r: r.date_from).category if dated else ""

    @property
    def gsk(self) -> int:
        return sum(r.in_gsk for r in self.records)

    @property
    def grades(self) -> Counter:
        return Counter(r.grade for r in self.records if r.grade)

    @property
    def last(self) -> date | None:
        return max((r.date_to for r in self.records), default=None)


def records_of(comp: Competition, folder: str, grades: dict[str, str], brigade: list[dict]) -> list[Record]:
    """Судьи одного соревнования: из карточки (лист «ГСК») и добавленные на странице договоров."""
    out, seen = [], set()
    status = f"{comp.kind}, {LEVEL_LABELS.get(comp.level, '').lower()}"

    def add(fio, role, category):
        k = person_key(fio)
        if not fio.strip() or (k, role) in seen:
            return
        seen.add((k, role))
        out.append(Record(" ".join(fio.split()), comp.title, comp.date_from, comp.date_to, comp.kind, status,
                          role, category if category != "б/к" else "", role.strip().lower() in _GSK,
                          grades.get(k, ""), folder))

    for o in comp.officials:
        add(o.fio, o.role, o.category)
    for x in brigade:
        if _JUDGING.search(str(x.get("role", ""))):
            add(str(x.get("fio", "")), str(x.get("role", "")), str(x.get("category", "")))
    return out


def judges(records: list[Record]) -> list[Judge]:
    by: dict[str, Judge] = {}
    for r in records:
        k = person_key(r.fio)
        by.setdefault(k, Judge(k, r.fio)).records.append(r)
    for j in by.values():
        j.records.sort(key=lambda r: r.date_from, reverse=True)
    return sorted(by.values(), key=lambda j: j.fio.lower().replace("ё", "е"))


THIN = Side(style="thin", color="7F7F7F")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD = PatternFill("solid", fgColor="DCE6F1")


def write_practice(people: list[Judge], path: str | Path) -> Path:
    """Excel: «Сводка» — строка на судью; «Практика» — строка на судью и соревнование."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Сводка"
    heads = ["ФИО", "Категория (последняя)", "Соревнований", "В составе ГСК", "Последнее", "Отлично", "Хорошо",
             "Удовлетворительно", "Неудовлетворительно"]
    for c, h in enumerate(heads, start=1):
        cell = ws.cell(1, c, h)
        cell.font, cell.fill, cell.border = Font(bold=True), HEAD, BOX
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for r, j in enumerate(people, start=2):
        g = j.grades
        vals = [j.fio, j.category, len(j.records), j.gsk, f"{j.last:%d.%m.%Y}" if j.last else "", g["отлично"],
                g["хорошо"], g["удовлетворительно"], g["неудовлетворительно"]]
        for c, v in enumerate(vals, start=1):
            ws.cell(r, c, v).border = BOX
    for c, w in enumerate([34, 14, 13, 12, 12, 9, 9, 12, 14], start=1):
        ws.column_dimensions[get_column_letter(c)].width = w

    ws2 = wb.create_sheet("Практика")
    heads2 = ["ФИО", "Соревнования", "Даты", "Статус", "Должность", "В составе ГСК", "Категория", "Оценка"]
    for c, h in enumerate(heads2, start=1):
        cell = ws2.cell(1, c, h)
        cell.font, cell.fill, cell.border = Font(bold=True), HEAD, BOX
    r = 2
    for j in people:
        for x in j.records:
            dates = f"{x.date_from:%d.%m.%Y}" + (f"–{x.date_to:%d.%m.%Y}" if x.date_to != x.date_from else "")
            vals = [j.fio, x.title, dates, x.level, x.role, "да" if x.in_gsk else "", x.category, x.grade]
            for c, v in enumerate(vals, start=1):
                cell = ws2.cell(r, c, v)
                cell.border, cell.alignment = BOX, Alignment(wrap_text=True, vertical="top")
            r += 1
    for c, w in enumerate([30, 44, 22, 34, 28, 10, 11, 14], start=1):
        ws2.column_dimensions[get_column_letter(c)].width = w
    ws2.freeze_panes = "B2"
    path = Path(path)
    wb.save(path)
    return path
