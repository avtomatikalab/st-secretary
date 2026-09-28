"""Чтение предварительных заявок, заполненных по бланку БЛАНК_ПРЕД_ЗАЯВКИ_ST (xlsx или xls).

Бланк: лист «Заявка»; строка с заголовками «Команда | Территория | Представитель | Контакты |
Кол-во участников» и строка значений под ней; таблица участников с заголовком «№ п/п» — колонки
A–O те же, что на листе «Заявка» книги СЕКРЕТАРЬ_ST. Строка «ОБРАЗЕЦ» и пример под ней пропускаются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from st_secretary.textclean import clean_spaces

# Колонки таблицы участников (как в бланке и в СЕКРЕТАРЬ_ST): ключ → (номер колонки, ожидаемое начало заголовка)
COLUMNS = {
    "num": (0, "№"),
    "team": (1, "Команда"),
    "territory": (2, "Территория"),
    "representative": (3, "Представитель"),
    "fio": (4, "Фамилия"),
    "birth": (5, "Дата"),
    "qual": (6, "Разряд"),
    "sex": (7, "Пол"),
    "group": (8, "Группа"),
    "cls": (9, "Класс"),
    "chip": (10, "Номер чипа"),
    "personal": (11, "Участие в личной"),
    "pair": (12, "Участие в дистанции связок"),
    "pair_num": (13, "Номер связки"),
    "team_dist": (14, ""),  # «Участие в дистанции-группа» или просто «Группа»
}


@dataclass
class RawRow:
    row: int  # номер строки в файле (как видит человек)
    values: dict[str, object]


@dataclass
class RawApplication:
    path: Path
    team: str = ""
    territory: str = ""
    representative: str = ""
    contacts: str = ""
    declared_count: object = None
    rows: list[RawRow] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # файл не удалось прочитать как бланк


def _grid(path: Path) -> list[list[object]]:
    """Лист «Заявка» (или первый) как список строк значений."""
    if path.suffix.lower() == ".xls":
        import xlrd  # необязательная зависимость

        book = xlrd.open_workbook(str(path))
        names = book.sheet_names()
        sh = book.sheet_by_name("Заявка") if "Заявка" in names else book.sheet_by_index(0)
        grid = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                v = cell.value
                if cell.ctype == xlrd.XL_CELL_DATE:
                    v = xlrd.xldate.xldate_as_datetime(v, book.datemode)
                row.append(v)
            grid.append(row)
        return grid
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True, read_only=True)
    ws = wb["Заявка"] if "Заявка" in wb.sheetnames else wb.worksheets[0]
    grid = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return grid


def read_preapplication(path: str | Path) -> RawApplication:
    path = Path(path)
    app = RawApplication(path)
    try:
        grid = _grid(path)
    except Exception as e:  # noqa: BLE001 — любой нечитаемый файл должен попасть в отчёт, а не уронить обработку
        app.problems.append(f"файл не открывается: {e}")
        return app

    def cell(r, c):
        return grid[r][c] if r < len(grid) and c < len(grid[r]) else None

    head_team = next((r for r in range(min(len(grid), 15)) if clean_spaces(cell(r, 1)) == "Команда"
                      and clean_spaces(cell(r, 2)).startswith("Территория")), None)
    head = next((r for r in range(min(len(grid), 25)) if clean_spaces(cell(r, 0)).startswith("№")
                 and clean_spaces(cell(r, 4)).startswith("Фамилия")), None)
    if head is None:
        app.problems.append("не найдена таблица участников (строка заголовков «№ п/п … Фамилия, имя»)")
        return app
    for key, (c, expected) in COLUMNS.items():
        if expected and not clean_spaces(cell(head, c)).startswith(expected):
            app.problems.append(f"колонка {chr(65 + c)}: ожидался заголовок «{expected}…», "
                                f"а в файле «{clean_spaces(cell(head, c))}» — бланк изменён")
    if app.problems:
        return app
    if head_team is not None and head_team + 1 < head:
        r = head_team + 1
        app.team, app.territory, app.representative = (clean_spaces(cell(r, c)) for c in (1, 2, 3))
        app.contacts = clean_spaces(cell(r, 4))
        app.declared_count = cell(r, 5)
    for r in range(head + 1, len(grid)):
        first = clean_spaces(cell(r, 0))
        if first.upper() == "ОБРАЗЕЦ" or first == "0":
            continue  # строка «ОБРАЗЕЦ» и пример под ней
        values = {key: cell(r, c) for key, (c, _) in COLUMNS.items()}
        if not clean_spaces(values["fio"]):
            continue  # пустые строки бланка (№ проставлен заранее)
        app.rows.append(RawRow(r + 1, values))
    return app
