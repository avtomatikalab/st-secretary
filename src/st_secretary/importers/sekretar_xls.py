"""Чтение книг Excel (.xls) формата СЕКРЕТАРЬ_ST и итоговых протоколов, сформированных им.

Нужен пакет xlrd (pip install "st-secretary[xls]").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

try:
    import xlrd
except ImportError as e:  # pragma: no cover
    raise ImportError('Для чтения .xls установите дополнительную зависимость: pip install "st-secretary[xls]"') from e


def _num(v) -> Fraction | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return Fraction(str(v))
    if isinstance(v, str) and re.fullmatch(r"\s*-?\d+(?:[.,]\d+)?\s*", v):
        return Fraction(v.strip().replace(",", "."))
    return None


def _text(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return " ".join(str(v).split())


def _find_row(sh, first_cell: str) -> int:
    for r in range(min(sh.nrows, 40)):
        if _text(sh.cell_value(r, 0)) == first_cell:
            return r
    raise ValueError(f"Лист «{sh.name}»: не найдена строка заголовка «{first_cell}»")


# ------------------------------------------------------------------ рабочий лист протокола группы


@dataclass
class StageColumn:
    col: int
    letter: str
    title: str  # пусто — колонка без названия


@dataclass
class TeamRow:
    row: int
    number: str
    team: str
    composition: str
    territory: str
    values: dict[int, Fraction]  # колонка → число


@dataclass
class GroupProtocolSheet:
    path: Path
    sheet: str
    header_lines: list[str]
    stage_columns: list[StageColumn]  # колонки блока «Прохождение дистанции» с названием
    unnamed_columns: list[StageColumn]  # колонки блока без названия, в которых есть числа
    teams: list[TeamRow]
    warnings: list[str] = field(default_factory=list)


def read_group_protocol(path: str | Path, sheet: str = "Протокол_группа") -> GroupProtocolSheet:
    """Лист протокола дистанции-группы книги СЕКРЕТАРЬ_ST (баллы/штрафы по этапам)."""
    path = Path(path)
    sh = xlrd.open_workbook(str(path)).sheet_by_name(sheet)
    head = _find_row(sh, "№ п/п")
    sub = head + 1
    # Блок этапов — от колонки «Прохождение дистанции…» до следующего заголовка («Время старта» и т.п.).
    start = next(c for c in range(sh.ncols) if _text(sh.cell_value(head, c)).startswith("Прохождение дистанции"))
    end = next((c for c in range(start + 1, sh.ncols) if _text(sh.cell_value(head, c))), sh.ncols)

    teams = []
    r = sub + 1
    while r < sh.nrows and _num(sh.cell_value(r, 0)) is not None and _text(sh.cell_value(r, 2)):
        values = {c: v for c in range(start, end) if (v := _num(sh.cell_value(r, c))) is not None}
        teams.append(TeamRow(r + 1, _text(sh.cell_value(r, 1)), _text(sh.cell_value(r, 2)),
                             _text(sh.cell_value(r, 3)), _text(sh.cell_value(r, 4)), values))
        r += 1

    named, unnamed, warnings = [], [], []
    for c in range(start, end):
        title = _text(sh.cell_value(sub, c))
        col = StageColumn(c, xlrd.colname(c), title)
        if title:
            named.append(col)
        elif any(c in t.values for t in teams):
            unnamed.append(col)
            warnings.append(
                f"Колонка {col.letter} в блоке этапов не имеет названия, но содержит числа — "
                "похоже на промежуточный итог; в сумму результата не включается."
            )
    header = [_text(sh.cell_value(i, 0)) for i in range(head) if _text(sh.cell_value(i, 0))]
    return GroupProtocolSheet(path, sheet, header, named, unnamed, teams, warnings)


# ------------------------------------------------------------------ итоговый протокол


@dataclass
class ResultRow:
    number: str
    team: str
    composition: str
    territory: str
    parts: dict[str, Fraction | None]  # тур/этап → баллы
    result: Fraction | None
    place: str
    percent: Fraction | None  # как записано в файле (доля: 1,2473 = 124,73 %)
    norm: str


@dataclass
class ResultProtocol:
    path: Path
    header_lines: list[str]
    discipline: str | None
    distance_class: int | None
    vrvs_code: str | None
    rank_text: str | None
    rows: list[ResultRow]
    footer_lines: list[str]


_TITLE_RE = re.compile(r'в дисциплине:\s*"(?P<name>[^"]+)"\s*(?P<cls>\d)\s*класса,\s*код ВРВС\s*(?P<code>\S+)')


def read_result_protocol(path: str | Path, sheet: int | str = 0) -> ResultProtocol:
    """Протокол результатов, выгруженный СЕКРЕТАРЬ_ST («Считать протокол»)."""
    path = Path(path)
    book = xlrd.open_workbook(str(path))
    sh = book.sheet_by_index(sheet) if isinstance(sheet, int) else book.sheet_by_name(sheet)
    head = _find_row(sh, "№ п/п")
    sub = head + 1
    col_by_title = {}
    for c in range(sh.ncols):
        t = _text(sh.cell_value(head, c)) or _text(sh.cell_value(sub, c))
        if t:
            col_by_title.setdefault(t, c)

    def col(prefix: str) -> int | None:
        return next((c for t, c in col_by_title.items() if t.startswith(prefix)), None)

    c_res, c_place, c_pct, c_norm = col("Результат"), col("Место"), col("% от результата"), col("Выполненный норматив")
    part_cols = [c for c in range(5, c_res or sh.ncols) if _text(sh.cell_value(sub, c))]

    header_lines = []
    for r in range(head):
        header_lines += [_text(sh.cell_value(r, c)) for c in range(sh.ncols) if _text(sh.cell_value(r, c))]
    joined = " ".join(header_lines)
    m = _TITLE_RE.search(joined)
    rank = re.search(r"Квалификационный ранг дистанции:\s*([^\s|]+(?:\s[^\s|]+)?)", joined)

    rows, r = [], sub + 1
    while r < sh.nrows and _num(sh.cell_value(r, 0)) is not None:
        rows.append(ResultRow(
            number=_text(sh.cell_value(r, 1)),
            team=_text(sh.cell_value(r, 2)),
            composition=_text(sh.cell_value(r, 3)),
            territory=_text(sh.cell_value(r, 4)),
            parts={_text(sh.cell_value(sub, c)): _num(sh.cell_value(r, c)) for c in part_cols},
            result=_num(sh.cell_value(r, c_res)) if c_res is not None else None,
            place=_text(sh.cell_value(r, c_place)) if c_place is not None else "",
            percent=_num(sh.cell_value(r, c_pct)) if c_pct is not None else None,
            norm=_text(sh.cell_value(r, c_norm)).strip(" -") if c_norm is not None else "",
        ))
        r += 1
    footer = [_text(sh.cell_value(i, 0)) for i in range(r, sh.nrows) if _text(sh.cell_value(i, 0))]
    return ResultProtocol(
        path=path,
        header_lines=header_lines,
        discipline=m.group("name") if m else None,
        distance_class=int(m.group("cls")) if m else None,
        vrvs_code=m.group("code") if m else None,
        rank_text=rank.group(1).strip() if rank else None,
        rows=rows,
        footer_lines=footer,
    )
